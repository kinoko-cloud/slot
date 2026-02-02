#!/usr/bin/env python3
"""
スロットサイト ステータスダッシュボード

全システムの状態を一覧表示する。
"""
import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / 'data'
HISTORY_DIR = DATA_DIR / 'history'

JST = timezone(timedelta(hours=9))

def now_jst():
    return datetime.now(JST)

def check_availability():
    """リアルタイムデータ状態"""
    avail_file = DATA_DIR / 'availability.json'
    if not avail_file.exists():
        return '❌ ファイルなし', None
    
    try:
        with open(avail_file) as f:
            data = json.load(f)
        fetched_at = data.get('fetched_at', '')
        if fetched_at:
            dt = datetime.fromisoformat(fetched_at)
            age_hours = (now_jst() - dt).total_seconds() / 3600
            if age_hours > 24:
                return f'🚨 {int(age_hours)}時間前', fetched_at
            elif age_hours > 2:
                return f'⚠️ {int(age_hours)}時間前', fetched_at
            else:
                return f'✅ {int(age_hours*60)}分前', fetched_at
    except:
        pass
    return '❌ 読み込みエラー', None

def check_history():
    """蓄積データ状態"""
    yesterday = (now_jst() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    total = 0
    ok = 0
    stores = {}
    
    for store_dir in HISTORY_DIR.iterdir():
        if not store_dir.is_dir():
            continue
        
        store_key = store_dir.name
        store_total = 0
        store_ok = 0
        store_latest = None
        
        for uf in store_dir.glob('*.json'):
            store_total += 1
            total += 1
            try:
                with open(uf) as f:
                    data = json.load(f)
                dates = [d.get('date', '') for d in data.get('days', [])]
                if dates:
                    latest = max(dates)
                    if store_latest is None or latest > store_latest:
                        store_latest = latest
                    if latest >= yesterday:
                        store_ok += 1
                        ok += 1
            except:
                continue
        
        stores[store_key] = {
            'total': store_total,
            'ok': store_ok,
            'latest': store_latest,
            'pct': int(store_ok / store_total * 100) if store_total > 0 else 0
        }
    
    pct = int(ok / total * 100) if total > 0 else 0
    if pct >= 95:
        status = f'✅ {pct}%'
    elif pct >= 50:
        status = f'⚠️ {pct}%'
    else:
        status = f'🚨 {pct}%'
    
    return status, {'total': total, 'ok': ok, 'pct': pct, 'stores': stores}

def check_github_actions():
    """GitHub Actions状態"""
    try:
        url = 'https://api.github.com/repos/kinoko-cloud/slot/actions/runs?per_page=20'
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        
        runs = data.get('workflow_runs', [])
        
        by_workflow = {}
        for run in runs:
            name = run.get('name', '')
            if name not in by_workflow:
                by_workflow[name] = run
        
        results = []
        has_failure = False
        for name, run in by_workflow.items():
            conclusion = run.get('conclusion', run.get('status', 'unknown'))
            created = run.get('created_at', '')[:16]
            
            if conclusion == 'failure':
                has_failure = True
                emoji = '🚨'
            elif conclusion == 'success':
                emoji = '✅'
            elif conclusion is None:
                emoji = '🔄'
                conclusion = '実行中'
            else:
                emoji = '❓'
            
            results.append({
                'name': name,
                'conclusion': conclusion,
                'created': created,
                'emoji': emoji
            })
        
        if has_failure:
            status = '🚨 失敗あり'
        else:
            status = '✅ 正常'
        
        return status, results
    except Exception as e:
        return f'❌ 確認失敗: {e}', []

def main():
    now = now_jst()
    print("=" * 60)
    print(f"📊 スロットサイト ステータスダッシュボード")
    print(f"   {now.strftime('%Y-%m-%d %H:%M:%S')} JST")
    print("=" * 60)
    
    # リアルタイムデータ
    avail_status, avail_time = check_availability()
    print(f"\n🔄 リアルタイムデータ: {avail_status}")
    if avail_time:
        print(f"   最終更新: {avail_time[:19]}")
    
    # 蓄積データ
    hist_status, hist_data = check_history()
    print(f"\n📁 蓄積データ: {hist_status} ({hist_data['ok']}/{hist_data['total']}台)")
    print(f"   店舗別:")
    for store, info in sorted(hist_data['stores'].items()):
        emoji = '✅' if info['pct'] >= 95 else '⚠️' if info['pct'] > 0 else '❌'
        print(f"   {emoji} {store}: {info['pct']}% ({info['latest']})")
    
    # GitHub Actions
    gh_status, gh_results = check_github_actions()
    print(f"\n🔧 GitHub Actions: {gh_status}")
    for r in gh_results:
        if 'PythonAnywhere' not in r['name']:
            print(f"   {r['emoji']} {r['name'][:30]}: {r['conclusion']} ({r['created']})")
    
    print("\n" + "=" * 60)
    
    # 全体判定
    has_error = '🚨' in avail_status or '🚨' in hist_status or '🚨' in gh_status
    if has_error:
        print("⚠️ 要対応の問題があります")
        return 1
    else:
        print("✅ 全システム正常")
        return 0

if __name__ == '__main__':
    exit(main())
