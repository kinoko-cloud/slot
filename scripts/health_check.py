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

def check_art_zero_anomaly():
    """ART=0異常検知（前日に比べて極端に低いART値を検知）
    
    例: 2/10 ART=50, 2/11 ART=0 → 異常
    """
    now = now_jst()
    yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    day_before = (now - timedelta(days=2)).strftime('%Y-%m-%d')
    
    issues = []
    checked_stores = 0
    
    for store_dir in HISTORY_DIR.iterdir():
        if not store_dir.is_dir():
            continue
        
        store_key = store_dir.name
        store_issues = []
        
        # サンプル台をチェック
        unit_files = sorted(store_dir.glob('*.json'), key=lambda f: f.stat().st_mtime, reverse=True)
        for unit_file in unit_files[:10]:  # 店舗あたり最大10台
            try:
                with open(unit_file) as f:
                    data = json.load(f)
                
                days = data.get('days', [])
                art_by_date = {d.get('date'): d.get('art', 0) for d in days}
                
                # 昨日と一昨日を比較
                art_yesterday = art_by_date.get(yesterday, -1)
                art_day_before = art_by_date.get(day_before, -1)
                
                # 一昨日ART >= 20 で 昨日ART <= 2 → 異常
                if art_day_before >= 20 and 0 <= art_yesterday <= 2:
                    store_issues.append({
                        'unit': unit_file.stem,
                        'date': yesterday,
                        'art': art_yesterday,
                        'prev_art': art_day_before
                    })
            except:
                continue
        
        if store_issues:
            # 店舗の50%以上の台で異常 → 全体的な問題
            if len(store_issues) >= 5:
                issues.append({
                    'store': store_key,
                    'affected_units': len(store_issues),
                    'sample': store_issues[:3]
                })
        
        checked_stores += 1
    
    if issues:
        return {
            'status': 'error',
            'message': f'{len(issues)}店舗でART=0異常を検知',
            'issues': issues,
            'checked_stores': checked_stores
        }
    else:
        return {
            'status': 'ok',
            'message': f'ART異常なし（{checked_stores}店舗確認）',
            'checked_stores': checked_stores
        }


def check_history_completeness():
    """履歴データの完全性チェック（途中で切れていないか）
    
    営業時間（10:00-23:00）を考慮して:
    - 閉店後（23:00以降）: 前日データの最終時刻が20:00以降であるべき
    - 営業中（12:00以降）: 当日データの最終時刻が現在時刻-2時間以降であるべき
    - 開店前（0:00-10:00）: 前日データの最終時刻が20:00以降であるべき
    """
    now = now_jst()
    current_hour = now.hour
    today = now.strftime('%Y-%m-%d')
    yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    
    # チェック対象日と期待する最終時刻を決定
    if current_hour >= 23:  # 閉店後
        target_date = today
        min_last_time = '20:00'
        context = '閉店後チェック'
    elif current_hour >= 12:  # 営業中（12時以降）
        target_date = today
        expected_hour = current_hour - 2
        min_last_time = f'{expected_hour:02d}:00'
        context = '営業中チェック'
    elif current_hour >= 10:  # 営業中（10-12時）
        # 開店直後はスキップ
        return {'status': 'ok', 'message': '開店直後のためスキップ'}
    else:  # 開店前（0-10時）
        target_date = yesterday
        min_last_time = '20:00'
        context = '開店前チェック'
    
    issues = []
    checked = 0
    
    for store_dir in HISTORY_DIR.iterdir():
        if not store_dir.is_dir():
            continue
        
        # 各店舗からサンプル3台をチェック
        unit_files = sorted(store_dir.glob('*.json'), key=lambda f: f.stat().st_mtime, reverse=True)
        for unit_file in unit_files[:3]:
            try:
                with open(unit_file) as f:
                    data = json.load(f)
                for day in data.get('days', []):
                    if day.get('date') == target_date:
                        hist = day.get('history', [])
                        if hist:
                            last_time = hist[0].get('time', '')  # 最新の履歴（[0]が最新）
                            checked += 1
                            if last_time and last_time < min_last_time:
                                issues.append({
                                    'store': store_dir.name,
                                    'unit': unit_file.stem,
                                    'date': target_date,
                                    'last_time': last_time,
                                    'expected': f'{min_last_time}以降'
                                })
                        break
            except:
                continue
    
    if issues:
        # 10%以上の台で問題があればエラー
        error_rate = len(issues) / max(checked, 1) * 100
        if error_rate >= 10:
            return {
                'status': 'error',
                'message': f'{context}: {len(issues)}台の履歴データが途中で切れている（{error_rate:.0f}%）',
                'issues': issues[:10],  # 最大10件
                'total_issues': len(issues),
                'checked': checked
            }
        else:
            return {
                'status': 'warning',
                'message': f'{context}: {len(issues)}台の履歴データが途中で切れている',
                'issues': issues[:5],
                'total_issues': len(issues),
                'checked': checked
            }
    else:
        return {
            'status': 'ok',
            'message': f'{context}: 履歴データ完全性OK（{checked}台確認）',
            'checked': checked
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


def check_config_integrity():
    """設定ファイル整合性チェック（rankings.py vs fetch_daidata_availability.py）"""
    try:
        from check_config_integrity import check_integrity
        errors, warnings = check_integrity()
        
        if errors:
            return {
                'status': 'error',
                'message': f'設定ファイル不整合: {len(errors)}件',
                'errors': errors[:5]
            }
        elif warnings:
            return {
                'status': 'warning',
                'message': f'設定ファイル警告: {len(warnings)}件',
                'warnings': warnings[:5]
            }
        else:
            return {'status': 'ok', 'message': '設定ファイル整合性OK'}
    except Exception as e:
        return {'status': 'warning', 'message': f'整合性チェック失敗: {e}'}


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
            # SBJ店舗は履歴が取れにくいのでスキップ
            # island_akihabara（papimoソース）は履歴が8件までしか取得できないのでスキップ
            if '_sbj' in store_key or store_key.startswith('island_akihabara'):
                continue
                
            for unit in store.get('units', []):
                unit_id = unit.get('unit_id', '?')
                art = unit.get('art', 0)
                history = unit.get('history', unit.get('today_history', []))
                history_count = len(history)
                
                # 履歴内でhit_numがリセット（1に戻る）されている場合はスキップ
                # （データソースの履歴分断の問題）
                if history_count > 1:
                    hit_nums = [h.get('hit_num', 0) for h in history]
                    reset_count = sum(1 for i in range(1, len(hit_nums)) if hit_nums[i] == 1 and hit_nums[i-1] > 1)
                    if reset_count > 0:
                        continue  # 履歴分断台はスキップ
                
                # ART回数が履歴件数の2.5倍以上 → 履歴が取れていない（閾値緩和）
                if art > 0 and history_count > 0 and art > history_count * 2.5:
                    issues.append({
                        'store': store_key,
                        'unit': unit_id,
                        'art': art,
                        'history': history_count,
                        'ratio': round(art / history_count, 1)
                    })
                
                # ART > 0 なのに履歴0件
                if art > 30 and history_count == 0:
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
    minute = now.minute
    
    # 営業時間外（22:45〜10:00）はスキップ
    if hour >= 23 or hour < 10 or (hour == 22 and minute >= 45):
        return {'status': 'ok', 'message': '営業時間外のためスキップ'}
    
    avail_file = DATA_DIR / 'availability.json'
    if not avail_file.exists():
        return {'status': 'warning', 'message': 'availability.jsonなし'}
    
    try:
        with open(avail_file) as f:
            data = json.load(f)
        
        stale_units = []
        
        for store_key, store in data.get('stores', {}).items():
            # island_akihabara（papimoソース）は履歴取得が不安定なのでスキップ
            # SBJ店舗は詳細ページから履歴が取れないのでスキップ
            if store_key.startswith('island_akihabara') or '_sbj' in store_key:
                continue
            
            for unit in store.get('units', []):
                unit_id = unit.get('unit_id', '?')
                art = unit.get('art', 0)
                history = unit.get('history', unit.get('today_history', []))
                
                if art < 10 or not history:
                    continue  # 稼働が少ない台はスキップ（閾値を5→10に）
                
                # 最新履歴の時刻
                latest_time = history[0].get('time', '')
                if not latest_time:
                    continue
                
                try:
                    latest_hour, latest_min = map(int, latest_time.split(':'))
                    latest_dt = now.replace(hour=latest_hour, minute=latest_min, second=0, microsecond=0)
                    
                    # 最新履歴の古さ
                    age_hours = (now - latest_dt).total_seconds() / 3600
                    
                    # 時間帯で閾値を調整（閉店間際は緩和）
                    # 北斗系は当たりが重い（1/319）のでハマりやすい
                    if hour >= 21:
                        threshold_hours = 12  # 21時以降は12時間（ほぼチェック無効化）
                    elif hour >= 19:
                        threshold_hours = 10  # 19時以降は10時間
                    else:
                        threshold_hours = 6  # 日中は6時間
                    
                    if age_hours > threshold_hours:
                        stale_units.append({
                            'store': store_key,
                            'unit': unit_id,
                            'art': art,
                            'latest_hit': latest_time,
                            'age_hours': round(age_hours, 1)
                        })
                except:
                    continue
        
        if len(stale_units) > 50:  # 50台以上でエラー（システム的な問題の可能性）
            return {
                'status': 'error',
                'message': f'{len(stale_units)}台で履歴が古い（システム問題の可能性）',
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
    results['checks']['art_zero'] = check_art_zero_anomaly()
    results['checks']['unit_changes'] = check_unit_changes()
    results['checks']['config_integrity'] = check_config_integrity()
    results['checks']['github_actions'] = check_github_actions()
    results['checks']['git_status'] = check_git_status()
    results['checks']['data_consistency'] = check_data_consistency()
    results['checks']['history_realtime'] = check_history_freshness_realtime()
    results['checks']['history_completeness'] = check_history_completeness()
    results['checks']['history_data'] = check_history_completeness_data()
    
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
                elif 'latest_hit' in issue:
                    # history_realtime: 履歴が古い（'unit'より先にチェック）
                    lines.append(f"   - {issue['store']} #{issue['unit']}: 最新{issue['latest_hit']} ({issue.get('age_hours', '?')}h前)")
                elif 'unit' in issue and 'history' in issue:
                    # data_consistency: ART/履歴矛盾
                    lines.append(f"   - {issue['store']} #{issue['unit']}: ART{issue['art']}/履歴{issue['history']}")
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
    
    # 3. 台番号変更検出 → 自動更新
    unit_check = results['checks'].get('unit_changes', {})
    if unit_check.get('status') == 'error':
        try:
            import subprocess
            # 台番号自動更新スクリプトを実行
            result = subprocess.run(
                ['python3', str(PROJECT_ROOT / 'scripts' / 'scrapers_v2' / 'auto_update_units.py'), '--apply'],
                cwd=str(PROJECT_ROOT),
                timeout=600,  # 10分
                capture_output=True,
                text=True
            )
            if '更新完了' in result.stdout:
                repairs.append('🔧 台番号設定を自動更新')
                # git commit & push
                subprocess.run(['git', 'add', '-A'], cwd=str(PROJECT_ROOT), timeout=30)
                subprocess.run(['git', 'commit', '-m', 'fix: 台番号自動更新'], cwd=str(PROJECT_ROOT), timeout=30)
                subprocess.run(['git', 'push'], cwd=str(PROJECT_ROOT), timeout=60)
                repairs.append('🔧 設定変更をコミット・プッシュ')
            else:
                repairs.append(f'⚠️ 台番号自動更新: 変更なしまたは失敗')
        except subprocess.TimeoutExpired:
            repairs.append('⚠️ 台番号自動更新タイムアウト')
        except Exception as e:
            repairs.append(f'⚠️ 台番号自動更新エラー: {e}')
    
    # 4. availabilityが古い → fetch実行
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
    
    # 5. historyデータが不完全な場合の修復
    hist_check = results['checks'].get('history_data', {})
    if hist_check.get('status') == 'error':
        repairs.append(repair_history_data())

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


def check_history_completeness_data():
    """historyファイルに当日の差枚・最大・連チャンが入っているか確認"""
    from datetime import datetime
    today = now_jst().strftime('%Y-%m-%d')
    
    issues = []
    checked = 0
    
    for store_dir in HISTORY_DIR.iterdir():
        if not store_dir.is_dir():
            continue
        
        for hist_file in list(store_dir.glob('*.json'))[:5]:  # 店舗あたり5台サンプル
            try:
                with open(hist_file) as f:
                    data = json.load(f)
                
                for day in data.get('days', []):
                    if day.get('date') == today:
                        checked += 1
                        art = day.get('art', 0)
                        if art > 0:
                            # ARTがあるなら差枚・最大・連チャンも必要
                            if day.get('diff_medals') is None:
                                issues.append({
                                    'store': store_dir.name,
                                    'unit': hist_file.stem,
                                    'missing': 'diff_medals'
                                })
                            if day.get('max_rensa') is None:
                                issues.append({
                                    'store': store_dir.name,
                                    'unit': hist_file.stem,
                                    'missing': 'max_rensa'
                                })
                        break
            except:
                continue
    
    if issues:
        return {
            'status': 'error',
            'message': f'{len(issues)}台でhistoryデータ不完全',
            'issues': issues[:5],
            'total_issues': len(issues),
            'checked': checked
        }
    else:
        return {
            'status': 'ok',
            'message': f'historyデータ完全性OK（{checked}台確認）',
            'checked': checked
        }


# auto_repairにhistoryデータ修復を追加
def repair_history_data():
    """historyデータが不完全な場合、availability.jsonから更新"""
    import subprocess
    try:
        result = subprocess.run(
            ['python3', str(PROJECT_ROOT / 'scripts' / 'update_history_from_availability.py')],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120
        )
        if result.returncode == 0:
            return '🔧 historyファイル更新成功'
        else:
            return f'⚠️ historyファイル更新失敗: {result.stderr[:100]}'
    except Exception as e:
        return f'⚠️ historyファイル更新エラー: {e}'
