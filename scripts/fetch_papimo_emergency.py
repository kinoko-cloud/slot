#!/usr/bin/env python3
"""アイランド秋葉原（PAPIMO）緊急取得"""
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
JST = timezone(timedelta(hours=9))

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import sync_playwright
from scrapers.papimo import get_unit_history
from analysis.history_accumulator import _accumulate_unit, load_unit_history

TARGET = (datetime.now(JST) - timedelta(days=1)).strftime('%Y-%m-%d')

STORES = {
    'island_akihabara_sbj': {
        'hall_id': '00031715',
        'machine_key': 'sbj',
        'units': ['1015', '1016', '1017', '1018', '1020', '1021', '1022', '1023',
                  '1025', '1026', '1027', '1028', '1030', '1031'],
    },
    'island_akihabara_hokuto2': {
        'hall_id': '00031715',
        'machine_key': 'hokuto2',
        'units': ['0731', '0732', '0733', '0735', '0736', '0737', '0738',
                  '0750', '0751', '0752', '0753', '0755', '0756', '0757'],
    },
}

def main():
    print("🚨 PAPIMO緊急取得（アイランド秋葉原）")
    print(f"目標: {TARGET}")
    
    updated = 0
    errors = 0
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        for store_key, cfg in STORES.items():
            hall_id = cfg['hall_id']
            machine_key = cfg['machine_key']
            units = cfg['units']
            
            print(f"\n{store_key}: {len(units)}台")
            
            for i, unit_id in enumerate(units, 1):
                # 既存チェック
                hist = load_unit_history(store_key, unit_id)
                existing = set(d.get('date', '') for d in hist.get('days', []))
                
                if TARGET in existing:
                    print(f"  [{i}/{len(units)}] {unit_id}: スキップ")
                    continue
                
                try:
                    result = get_unit_history(page, hall_id, unit_id, days_back=7)
                    new_days = [d for d in result.get('days', []) 
                               if d.get('date') and d.get('date') not in existing]
                    
                    if new_days:
                        _accumulate_unit(store_key, unit_id, new_days, machine_key)
                        print(f"  [{i}/{len(units)}] {unit_id}: +{len(new_days)}日 ✅")
                        updated += 1
                    else:
                        print(f"  [{i}/{len(units)}] {unit_id}: 新規なし")
                except Exception as e:
                    print(f"  [{i}/{len(units)}] {unit_id}: エラー ❌ {str(e)[:50]}")
                    errors += 1
        
        browser.close()
    
    print(f"\n完了: 更新{updated}台, エラー{errors}台")

if __name__ == '__main__':
    main()
