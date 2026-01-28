#!/usr/bin/env python3
"""パピモ過去データ一括取得 - プルダウンで取れる全日分を取得"""
import sys, json
sys.path.insert(0, '.')
from pathlib import Path
from scrapers.papimo import get_unit_history, PAPIMO_CONFIG
from playwright.sync_api import sync_playwright

config = PAPIMO_CONFIG['island_akihabara']
hall_id = config['hall_id']

targets = [
    ('sbj', config['sbj_units']),
    ('hokuto_tensei2', config.get('hokuto_units', [])),
]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    
    for machine_key, units in targets:
        if not units:
            continue
        history_dir = Path(f'data/history/island_akihabara_{machine_key}')
        history_dir.mkdir(parents=True, exist_ok=True)
        
        print(f'\n=== island_akihabara {machine_key} ({len(units)}台) ===', flush=True)
        
        for unit_id in units:
            existing_path = history_dir / f'{unit_id}.json'
            existing_dates = set()
            existing_data = {'unit_id': unit_id, 'days': []}
            
            if existing_path.exists():
                existing_data = json.loads(existing_path.read_text())
                existing_dates = {d.get('date','') for d in existing_data.get('days',[])}
            
            print(f'\n台{unit_id} (既存{len(existing_dates)}日):', flush=True)
            
            result = get_unit_history(page, hall_id, unit_id, days_back=14)
            new_days = [d for d in result.get('days',[]) if d.get('date','') and d.get('date','') not in existing_dates]
            
            if new_days:
                all_days = existing_data.get('days', []) + new_days
                seen = set()
                unique = []
                for day in sorted(all_days, key=lambda x: x.get('date',''), reverse=True):
                    dt = day.get('date','')
                    if dt and dt not in seen:
                        seen.add(dt)
                        unique.append(day)
                
                existing_data['days'] = unique
                existing_path.write_text(json.dumps(existing_data, ensure_ascii=False, indent=2))
                print(f'  +{len(new_days)}日追加 → 合計{len(unique)}日分', flush=True)
            else:
                print(f'  新規データなし', flush=True)
    
    browser.close()

print('\n=== 完了 ===', flush=True)
