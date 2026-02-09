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
        
        # ワークフロー別に最新5件を取得
        by_workflow = {}
        for run in runs:
            name = run.get('name', '')
            if name not in by_workflow:
                by_workflow[name] = []
            if len(by_workflow[name]) < 5:
                by_workflow[name].append(run)
        
        issues = []
        for name, workflow_runs in by_workflow.items():
            if 'PythonAnywhere' in name:
                continue  # デプロイは別
            
            # 最新の状態
            latest = workflow_runs[0] if workflow_runs else None
            if not latest:
                continue
                
            conclusion = latest.get('conclusion', '')
            
            # failure または cancelled を検出
            if conclusion == 'failure':
                issues.append({
                    'workflow': name,
                    'conclusion': conclusion,
                    'url': latest.get('html_url', '')
                })
            
            # 連続cancelled（3回以上）を検出
            cancelled_count = sum(1 for r in workflow_runs if r.get('conclusion') == 'cancelled')
            if cancelled_count >= 3:
                issues.append({
                    'workflow': name,
                    'conclusion': f'連続cancelled({cancelled_count}回)',
                    'url': latest.get('html_url', '')
                })
        
        if issues:
            return {
                'status': 'error',
                'message': f'{len(issues)}件のワークフローに問題',
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


def check_git_status():
    """Gitリポジトリの状態チェック"""
    import subprocess
    
    issues = []
    
    try:
        # rebase/merge中かチェック
        git_dir = PROJECT_ROOT / '.git'
        if (git_dir / 'rebase-merge').exists() or (git_dir / 'rebase-apply').exists():
            issues.append('rebase中')
        if (git_dir / 'MERGE_HEAD').exists():
            issues.append('merge中')
        
        # index.lockが残っていないか
        if (git_dir / 'index.lock').exists():
            issues.append('index.lockが残っている')
        
        # ローカルとリモートの差分
        result = subprocess.run(
            ['git', 'status', '--porcelain'],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=10
        )
        uncommitted = len([l for l in result.stdout.strip().split('\n') if l.strip()])
        
        # リモートとの差分
        subprocess.run(['git', 'fetch', 'origin', '--quiet'], cwd=str(PROJECT_ROOT), timeout=30)
        result = subprocess.run(
            ['git', 'rev-list', '--count', 'HEAD..origin/main'],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=10
        )
        behind = int(result.stdout.strip() or '0')
        
        result = subprocess.run(
            ['git', 'rev-list', '--count', 'origin/main..HEAD'],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=10
        )
        ahead = int(result.stdout.strip() or '0')
        
        if behind > 5:
            issues.append(f'リモートより{behind}コミット遅れ')
        if ahead > 10:
            issues.append(f'未プッシュ{ahead}コミット')
            
    except Exception as e:
        return {'status': 'warning', 'message': f'Git状態確認失敗: {e}'}
    
    if issues:
        return {
            'status': 'error',
            'message': f'Git異常: {", ".join(issues)}',
            'issues': issues
        }
    else:
        return {
            'status': 'ok',
            'message': 'Git状態正常'
        }


def check_data_consistency():
    """データ整合性チェック（ART回数 vs 履歴件数）"""
    avail_file = DATA_DIR / 'availability.json'
    if not avail_file.exists():
        return {'status': 'warning', 'message': 'availability.jsonなし'}
    
    try:
        with open(avail_file) as f:
            data = json.load(f)
        
        issues = []
        
        for store_key, store in data.get('stores', {}).items():
            for unit in store.get('units', []):
                unit_id = unit.get('unit_id', '?')
                art = unit.get('art', 0)
                history = unit.get('today_history', [])
                history_count = len(history)
                
                # ART回数が履歴件数の2倍以上 → 履歴が取れていない
                if art > 0 and history_count > 0 and art > history_count * 2:
                    issues.append({
                        'store': store_key,
                        'unit': unit_id,
                        'art': art,
                        'history': history_count,
                        'ratio': round(art / history_count, 1)
                    })
                
                # ART > 0 なのに履歴0件
                if art > 10 and history_count == 0:
                    issues.append({
                        'store': store_key,
                        'unit': unit_id,
                        'art': art,
                        'history': 0,
                        'ratio': 'inf'
                    })
        
        if issues:
            # 上位5件のみ報告
            return {
                'status': 'error',
                'message': f'{len(issues)}台でART/履歴矛盾',
                'issues': issues[:5],
                'total_issues': len(issues)
            }
        else:
            return {
                'status': 'ok',
                'message': 'データ整合性OK'
            }
    except Exception as e:
        return {'status': 'warning', 'message': f'整合性チェック失敗: {e}'}


def check_history_freshness_realtime():
    """営業時間中の履歴更新チェック（最新履歴が古すぎないか）"""
    now = now_jst()
    hour = now.hour
    
    # 営業時間外（23時〜10時）はスキップ
    if hour >= 23 or hour < 10:
        return {'status': 'ok', 'message': '営業時間外のためスキップ'}
    
    avail_file = DATA_DIR / 'availability.json'
    if not avail_file.exists():
        return {'status': 'warning', 'message': 'availability.jsonなし'}
    
    try:
        with open(avail_file) as f:
            data = json.load(f)
        
        stale_units = []
        
        for store_key, store in data.get('stores', {}).items():
            for unit in store.get('units', []):
                unit_id = unit.get('unit_id', '?')
                art = unit.get('art', 0)
                history = unit.get('today_history', [])
                
                if art < 5 or not history:
                    continue  # 稼働していない台はスキップ
                
                # 最新履歴の時刻
                latest_time = history[0].get('time', '')
                if not latest_time:
                    continue
                
                try:
                    latest_hour, latest_min = map(int, latest_time.split(':'))
                    latest_dt = now.replace(hour=latest_hour, minute=latest_min, second=0, microsecond=0)
                    
                    # 最新履歴が2時間以上前
                    age_hours = (now - latest_dt).total_seconds() / 3600
                    if age_hours > 2:
                        stale_units.append({
                            'store': store_key,
                            'unit': unit_id,
                            'art': art,
                            'latest_hit': latest_time,
                            'age_hours': round(age_hours, 1)
                        })
                except:
                    continue
        
        if len(stale_units) > 10:  # 10台以上で警告（全体的に履歴が取れていない）
            return {
                'status': 'error',
                'message': f'{len(stale_units)}台で履歴が2時間以上古い',
                'issues': stale_units[:5],
                'total_issues': len(stale_units)
            }
        elif stale_units:
            return {
                'status': 'warning',
                'message': f'{len(stale_units)}台で履歴が古い',
                'issues': stale_units[:3]
            }
        else:
            return {
                'status': 'ok',
                'message': '履歴更新正常'
            }
    except Exception as e:
        return {'status': 'warning', 'message': f'履歴鮮度チェック失敗: {e}'}

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
    results['checks']['git_status'] = check_git_status()
    results['checks']['data_consistency'] = check_data_consistency()
    results['checks']['history_realtime'] = check_history_freshness_realtime()
    
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
                if 'workflow' in issue:
                    lines.append(f"   - {issue['workflow']}: {issue.get('conclusion', '')}")
                elif 'unit' in issue:
                    # data_consistency: ART/履歴矛盾
                    lines.append(f"   - {issue['store']} #{issue['unit']}: ART{issue['art']}/履歴{issue['history']}")
                elif 'latest_hit' in issue:
                    # history_realtime: 履歴が古い
                    lines.append(f"   - {issue['store']} #{issue['unit']}: 最新{issue['latest_hit']}")
                elif 'latest' in issue:
                    # history: 店舗データが古い
                    lines.append(f"   - {issue['store']}: {issue['latest']}")
                elif 'store' in issue:
                    lines.append(f"   - {issue['store']}")
    
    return '\n'.join(lines)

def auto_repair(results):
    """自己修復を試みる"""
    import subprocess
    repairs = []
    
    # 1. ロックファイルが残っていたら削除
    for lock_pattern in ['/tmp/slot_fetch.lock', '/tmp/slot_sbj_update.lock', '/tmp/slot_hokuto_update.lock']:
        lock_file = Path(lock_pattern)
        if lock_file.exists():
            try:
                lock_file.unlink()
                repairs.append(f'🔧 {lock_file.name}削除')
            except:
                pass
    
    # 1.5. Git index.lock削除
    git_lock = PROJECT_ROOT / '.git' / 'index.lock'
    if git_lock.exists():
        try:
            git_lock.unlink()
            repairs.append('🔧 git index.lock削除')
        except:
            pass
    
    # 2. Git rebase/merge中なら abort
    git_check = results['checks'].get('git_status', {})
    if git_check.get('status') == 'error':
        issues = git_check.get('issues', [])
        if 'rebase中' in issues:
            try:
                subprocess.run(['git', 'rebase', '--abort'], cwd=str(PROJECT_ROOT), timeout=30)
                repairs.append('🔧 git rebase --abort')
            except:
                pass
        if 'merge中' in issues:
            try:
                subprocess.run(['git', 'merge', '--abort'], cwd=str(PROJECT_ROOT), timeout=30)
                repairs.append('🔧 git merge --abort')
            except:
                pass
        
        # リモートに同期
        if any(x in issues for x in ['rebase中', 'merge中', 'リモートより']):
            try:
                subprocess.run(['git', 'fetch', 'origin'], cwd=str(PROJECT_ROOT), timeout=30)
                subprocess.run(['git', 'reset', '--hard', 'origin/main'], cwd=str(PROJECT_ROOT), timeout=30)
                repairs.append('🔧 git reset --hard origin/main')
            except:
                pass
    
    # 3. availabilityが古い → fetch実行
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
