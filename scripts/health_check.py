#!/usr/bin/env python3
"""
スロットサイト ヘルスチェック

チェック項目:
1. availability.json の鮮度（24時間以内か）
2. 各店舗の蓄積データ鮮度（前日データがあるか）
3. GitHub Actions の実行状態
4. 表示データとソースの整合性

異常検知時はexit code 1 + JSONで詳細を出力
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / 'data'
HISTORY_DIR = DATA_DIR / 'history'

JST = timezone(timedelta(hours=9))

def now_jst():
    return datetime.now(JST)

def check_availability_freshness():
    """availability.jsonの鮮度チェック"""
    avail_file = DATA_DIR / 'availability.json'
    if not avail_file.exists():
        return {'status': 'error', 'message': 'availability.json が存在しません'}
    
    try:
        with open(avail_file) as f:
            data = json.load(f)
        fetched_at = data.get('fetched_at', '')
        if not fetched_at:
            return {'status': 'error', 'message': 'fetched_at がありません'}
        
        fetched_dt = datetime.fromisoformat(fetched_at)
        age_hours = (now_jst() - fetched_dt).total_seconds() / 3600
        
        if age_hours > 24:
            return {
                'status': 'error',
                'message': f'リアルタイムデータが{int(age_hours)}時間前で停止',
                'fetched_at': fetched_at,
                'age_hours': int(age_hours)
            }
        elif age_hours > 2:
            return {
                'status': 'warning',
                'message': f'リアルタイムデータが{int(age_hours)}時間前',
                'fetched_at': fetched_at,
                'age_hours': int(age_hours)
            }
        else:
            return {
                'status': 'ok',
                'message': f'リアルタイムデータ正常（{int(age_hours*60)}分前）',
                'fetched_at': fetched_at,
                'age_hours': round(age_hours, 1)
            }
    except Exception as e:
        return {'status': 'error', 'message': f'availability.json 読み込みエラー: {e}'}

def check_history_freshness():
    """各店舗の蓄積データ鮮度チェック"""
    yesterday = (now_jst() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    results = {}
    issues = []
    
    for store_dir in HISTORY_DIR.iterdir():
        if not store_dir.is_dir():
            continue
        
        store_key = store_dir.name
        latest_date = None
        
        # サンプル5台で最新日付を確認（ファイル更新日時が新しい順）
        unit_files = sorted(store_dir.glob('*.json'), key=lambda f: f.stat().st_mtime, reverse=True)
        for unit_file in unit_files[:5]:
            try:
                with open(unit_file) as f:
                    data = json.load(f)
                for day in data.get('days', []):
                    date = day.get('date', '')
                    if date and (latest_date is None or date > latest_date):
                        latest_date = date
            except:
                continue
        
        if latest_date:
            results[store_key] = latest_date
            if latest_date < yesterday:
                issues.append({
                    'store': store_key,
                    'latest': latest_date,
                    'expected': yesterday
                })
    
    if issues:
        return {
            'status': 'error',
            'message': f'{len(issues)}店舗でデータが古い',
            'issues': issues,
            'all_stores': results
        }
    else:
        return {
            'status': 'ok',
            'message': '全店舗のデータが最新',
            'all_stores': results
        }

def check_unit_changes():
    """台変動チェック（増台/減台/台移動/撤去）"""
    try:
        import subprocess
        result = subprocess.run(
            ['python3', str(PROJECT_ROOT / 'scripts' / 'verify_units.py')],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=60
        )
        output = result.stdout + result.stderr
        
        if '異常なし' in output:
            return {'status': 'ok', 'message': '台番号の異常なし'}
        elif '増台' in output or '減台' in output or '撤去' in output or '台移動' in output:
            return {
                'status': 'error',
                'message': '台変動を検知！',
                'details': output[:500]
            }
        else:
            return {'status': 'warning', 'message': f'不明な出力: {output[:200]}'}
    except Exception as e:
        return {'status': 'warning', 'message': f'台変動チェック失敗: {e}'}

def check_github_actions():
    """GitHub Actionsの最新実行状態チェック"""
    import urllib.request
    
    try:
        url = 'https://api.github.com/repos/kinoko-cloud/slot/actions/runs?per_page=20'
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        
        runs = data.get('workflow_runs', [])
        
        # ワークフロー別に最新を取得
        by_workflow = {}
        for run in runs:
            name = run.get('name', '')
            if name not in by_workflow:
                by_workflow[name] = run
        
        issues = []
        for name, run in by_workflow.items():
            if 'PythonAnywhere' in name:
                continue  # デプロイは別
            
            conclusion = run.get('conclusion', '')
            if conclusion == 'failure':
                issues.append({
                    'workflow': name,
                    'conclusion': conclusion,
                    'url': run.get('html_url', '')
                })
        
        if issues:
            return {
                'status': 'error',
                'message': f'{len(issues)}件のワークフローが失敗',
                'issues': issues
            }
        else:
            return {
                'status': 'ok',
                'message': 'GitHub Actions正常'
            }
    except Exception as e:
        return {
            'status': 'warning',
            'message': f'GitHub API確認失敗: {e}'
        }

def run_all_checks():
    """全チェック実行"""
    results = {
        'timestamp': now_jst().isoformat(),
        'checks': {}
    }
    
    # 各チェック実行
    results['checks']['availability'] = check_availability_freshness()
    results['checks']['history'] = check_history_freshness()
    results['checks']['unit_changes'] = check_unit_changes()
    results['checks']['github_actions'] = check_github_actions()
    
    # 全体ステータス判定
    has_error = any(c.get('status') == 'error' for c in results['checks'].values())
    has_warning = any(c.get('status') == 'warning' for c in results['checks'].values())
    
    if has_error:
        results['overall'] = 'error'
    elif has_warning:
        results['overall'] = 'warning'
    else:
        results['overall'] = 'ok'
    
    return results

def format_alert_message(results):
    """WhatsApp通知用のメッセージ生成"""
    status_emoji = {'ok': '✅', 'warning': '⚠️', 'error': '🚨'}
    
    lines = [f"{status_emoji.get(results['overall'], '❓')} スロットサイト ヘルスチェック"]
    lines.append(f"時刻: {results['timestamp'][:16]}")
    lines.append("")
    
    for name, check in results['checks'].items():
        status = check.get('status', 'unknown')
        emoji = status_emoji.get(status, '❓')
        msg = check.get('message', '')
        lines.append(f"{emoji} {name}: {msg}")
        
        # エラー詳細
        if status == 'error' and 'issues' in check:
            for issue in check['issues'][:3]:  # 最大3件
                if 'store' in issue:
                    lines.append(f"   - {issue['store']}: {issue['latest']}")
                elif 'workflow' in issue:
                    lines.append(f"   - {issue['workflow']}")
    
    return '\n'.join(lines)

def auto_repair(results):
    """自己修復を試みる"""
    repairs = []
    
    # 1. ロックファイルが残っていたら削除
    lock_file = Path('/tmp/slot_fetch.lock')
    if lock_file.exists():
        try:
            lock_file.unlink()
            repairs.append('🔧 ロックファイル削除')
        except:
            pass
    
    # 2. availabilityが古い → fetch実行
    avail_check = results['checks'].get('availability', {})
    if avail_check.get('status') == 'error' and avail_check.get('age_hours', 0) > 2:
        try:
            import subprocess
            # 非同期で実行（タイムアウト60秒）
            result = subprocess.run(
                ['python3', str(PROJECT_ROOT / 'scripts' / 'fetch_daidata_availability.py')],
                cwd=str(PROJECT_ROOT),
                timeout=300,  # 5分
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                repairs.append('🔧 リアルタイムデータ再取得成功')
            else:
                repairs.append(f'⚠️ リアルタイムデータ再取得失敗: {result.stderr[:100]}')
        except subprocess.TimeoutExpired:
            repairs.append('⚠️ リアルタイムデータ再取得タイムアウト')
        except Exception as e:
            repairs.append(f'⚠️ リアルタイムデータ再取得エラー: {e}')
    
    # 3. historyが古い → fetch_all_missing実行（バックグラウンド）
    hist_check = results['checks'].get('history', {})
    if hist_check.get('status') == 'error':
        issues = hist_check.get('issues', [])
        if len(issues) >= 1:  # 1店舗以上古い場合に自動復旧
            try:
                import subprocess
                # バックグラウンドで実行
                subprocess.Popen(
                    ['python3', str(PROJECT_ROOT / 'scripts' / 'fetch_all_missing.py')],
                    cwd=str(PROJECT_ROOT),
                    stdout=open('/tmp/fetch_all.log', 'w'),
                    stderr=subprocess.STDOUT
                )
                repairs.append(f'🔧 {len(issues)}店舗のデータ取得を開始（バックグラウンド）')
            except Exception as e:
                repairs.append(f'⚠️ データ取得開始失敗: {e}')
    
    # 4. 修復後にサイト再ビルド
    if any('再取得成功' in r for r in repairs):
        try:
            import subprocess
            result = subprocess.run(
                ['python3', str(PROJECT_ROOT / 'scripts' / 'generate_static.py')],
                cwd=str(PROJECT_ROOT),
                timeout=180,
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                repairs.append('🔧 サイト再ビルド成功')
            else:
                repairs.append('⚠️ サイト再ビルド失敗')
        except:
            pass
    
    return repairs

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--repair', action='store_true', help='自己修復を試みる')
    parser.add_argument('--quiet', action='store_true', help='正常時は出力しない')
    args = parser.parse_args()
    
    results = run_all_checks()
    
    # 自己修復
    repairs = []
    if args.repair and results['overall'] == 'error':
        repairs = auto_repair(results)
        results['repairs'] = repairs
        
        # 修復後に再チェック
        if repairs:
            import time
            time.sleep(2)
            results['after_repair'] = run_all_checks()
    
    # 出力
    if args.quiet and results['overall'] == 'ok':
        sys.exit(0)
    
    # JSON出力
    print(json.dumps(results, ensure_ascii=False, indent=2))
    
    # 異常時はexit code 1
    if results['overall'] == 'error':
        # アラートメッセージも出力
        msg = format_alert_message(results)
        if repairs:
            msg += '\n\n--- 自己修復 ---\n' + '\n'.join(repairs)
        print("\n--- ALERT MESSAGE ---", file=sys.stderr)
        print(msg, file=sys.stderr)
        sys.exit(1)
    elif results['overall'] == 'warning':
        sys.exit(0)
    else:
        sys.exit(0)

if __name__ == '__main__':
    main()
