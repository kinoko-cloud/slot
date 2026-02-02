#!/usr/bin/env python3
"""渋谷エスパスの欠落データを取得して蓄積DBに追加"""
import sys
import json
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.stores import DAIDATA_STORES
from scrapers.daidata_detail_history import get_all_history
from analysis.history_accumulator import _accumulate_unit, load_unit_history

HALL_ID = '100860'
HALL_NAME = '渋谷エスパス新館'
STORE_KEYS = {
    'hokuto_tensei2': 'shibuya_espass_hokuto_tensei2',
    'sbj': 'shibuya_espass_sbj',
}

def main():
    shibuya = DAIDATA_STORES.get('shibuya_espass', {})
    machines = shibuya.get('machines', {})
    
    total_updated = 0
    
    for machine_key, units in machines.items():
        store_key = STORE_KEYS.get(machine_key)
        if not store_key:
            print(f"⚠️ {machine_key}のstore_keyが不明")
            continue
            
        print(f"\n{'='*60}")
        print(f"機種: {machine_key} ({len(units)}台)")
        print(f"{'='*60}")
        
        for i, unit_id in enumerate(units):
            print(f"\n[{i+1}/{len(units)}] 台{unit_id}")
            
            # 現在の蓄積データを確認
            hist = load_unit_history(store_key, unit_id)
            existing_dates = set(d.get('date', '') for d in hist.get('days', []))
            print(f"  既存データ: {len(existing_dates)}日分")
            
            try:
                # 新しいデータを取得
                result = get_all_history(
                    hall_id=HALL_ID,
                    unit_id=str(unit_id),
                    hall_name=HALL_NAME,
                    expected_machine=machine_key
                )
                
                new_days = result.get('days', [])
                print(f"  取得データ: {len(new_days)}日分")
                
                # 新しい日付のみ追加
                added = 0
                days_to_add = []
                for day in new_days:
                    date = day.get('date', '')
                    if date and date not in existing_dates:
                        days_to_add.append(day)
                        added += 1
                        print(f"    + {date}: ART={day.get('art', 0)}")
                
                if days_to_add:
                    _accumulate_unit(store_key, unit_id, days_to_add, machine_key)
                
                if added > 0:
                    total_updated += 1
                    print(f"  ✅ {added}日分追加")
                else:
                    print(f"  ⏭️ 追加なし（既に最新）")
                    
            except Exception as e:
                print(f"  ❌ エラー: {e}")
            
            # レート制限対策
            time.sleep(2)
    
    print(f"\n{'='*60}")
    print(f"完了: {total_updated}台のデータを更新")
    print(f"{'='*60}")

if __name__ == '__main__':
    main()
