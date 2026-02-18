#!/usr/bin/env python3
"""
欠損履歴を補完するスクリプト
daidataのfetch_historyを使って、ARTがあるのにhistoryが空の日を補完する
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).parent / 'scrapers_v2'))
from daidata.scraper import DaidataScraper

ROOT = Path(__file__).parent.parent
HISTORY_DIR = ROOT / 'data' / 'history'

# hall_idマッピング
HALL_IDS = {
    'akasaka_espass_sbj': '100196',
    'akiba_espass_hokuto2': '100860',
    'akiba_espass_sbj': '100860',
    'island_akihabara_hokuto2': '100928',
    'island_akihabara_sbj': '100928',
    'seibu_shinjuku_espass_hokuto2': '100950',
    'seibu_shinjuku_espass_sbj': '100950',
    'shibuya_espass_hokuto2': '100947',
    'shibuya_espass_sbj': '100947',
    'shibuya_honkan_espass_hokuto2': '100949',
    'shibuya_honkan_espass_sbj': '100949',
    'shinjuku_espass_hokuto2': '100949',
    'shinjuku_espass_sbj': '100949',
    'shinkoiwa_espass_sbj': '100260',
    'shinokubo_espass_sbj': '100951',
    'takadanobaba_espass_sbj': '100948',
    'ueno_espass_sbj': '100946',
    'ueno_honkan_espass_sbj': '100861',
}

def find_missing():
    """欠損を検出"""
    missing = []
    dates_to_check = ['2026-02-14', '2026-02-15', '2026-02-16', '2026-02-17']
    
    for store_dir in sorted(HISTORY_DIR.iterdir()):
        if not store_dir.is_dir():
            continue
        store_key = store_dir.name
        hall_id = HALL_IDS.get(store_key)
        if not hall_id:
            continue
        
        for f in store_dir.glob('*.json'):
            unit_id = f.stem
            try:
                data = json.load(open(f))
                for day in data.get('days', []):
                    date = day.get('date')
                    if date in dates_to_check:
                        art = day.get('art', 0)
                        hist = day.get('history', [])
                        if art > 0 and len(hist) == 0:
                            missing.append({
                                'store_key': store_key,
                                'hall_id': hall_id,
                                'unit_id': unit_id,
                                'date': date,
                                'art': art,
                                'file': f
                            })
            except:
                pass
    
    return missing

def backfill(missing_list, max_items=None):
    """欠損を補完"""
    if max_items:
        missing_list = missing_list[:max_items]
    
    # hall_id + unit_id でグループ化（同じ台の複数日を1回のfetch_historyで取得）
    grouped = {}
    for m in missing_list:
        key = (m['hall_id'], m['unit_id'], m['store_key'])
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(m)
    
    print(f"欠損: {len(missing_list)}件, ユニーク台: {len(grouped)}台")
    
    scraper = DaidataScraper(headless=True)
    updated = 0
    
    with scraper.browser_session():
        for i, ((hall_id, unit_id, store_key), items) in enumerate(grouped.items()):
            print(f"[{i+1}/{len(grouped)}] {store_key}/{unit_id}...", end=' ', flush=True)
            
            try:
                history = scraper.fetch_history(hall_id, unit_id, days=7)
                
                # 各欠損日を補完
                for item in items:
                    target_date = item['date']
                    for day in history:
                        if day.get('date') == target_date and day.get('history'):
                            # historyファイルを更新
                            data = json.load(open(item['file']))
                            for d in data.get('days', []):
                                if d.get('date') == target_date:
                                    d['history'] = day.get('history', [])
                                    updated += 1
                                    break
                            with open(item['file'], 'w') as f:
                                json.dump(data, f, ensure_ascii=False, indent=2)
                            print(f"✓{target_date[-5:]}", end=' ', flush=True)
                            break
                
                print()
            except Exception as e:
                print(f"✗ {e}")
    
    return updated

if __name__ == '__main__':
    print("=== 欠損履歴補完 ===")
    missing = find_missing()
    print(f"検出: {len(missing)}件")
    
    if missing:
        updated = backfill(missing)
        print(f"\n完了: {updated}件更新")
