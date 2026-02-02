#!/usr/bin/env python3
"""全店舗の欠落データを取得して蓄積DBに追加"""
import sys
import json
import time
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.stores import DAIDATA_STORES
from config.rankings import STORES
from scrapers.daidata_detail_history import get_all_history
from analysis.history_accumulator import _accumulate_unit, load_unit_history

# 目標日付（今日の前日まで）
TARGET_DATE = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

def main():
    total_updated = 0
    total_added_days = 0
    
    # DAIDATA_STORESから全店舗を処理
    for store_name, store_cfg in DAIDATA_STORES.items():
        hall_id = store_cfg.get('hall_id')
        hall_name = store_cfg.get('name', store_name)
        machines = store_cfg.get('machines', {})
        
        if not hall_id or not machines:
            continue
            
        for machine_key, units in machines.items():
            if not units:
                continue
                
            # store_key を構築
            store_key = f"{store_name}_{machine_key}"
            
            print(f"\n{'='*60}")
            print(f"店舗: {hall_name} / 機種: {machine_key} ({len(units)}台)")
            print(f"store_key: {store_key}")
            print(f"{'='*60}")
            
            for i, unit_id in enumerate(units):
                print(f"\n[{i+1}/{len(units)}] 台{unit_id}")
                
                # 現在の蓄積データを確認
                hist = load_unit_history(store_key, unit_id)
                existing_dates = set(d.get('date', '') for d in hist.get('days', []))
                latest = max(existing_dates) if existing_dates else 'なし'
                print(f"  既存データ: {len(existing_dates)}日分 (最新: {latest})")
                
                # 既に最新なら スキップ
                if TARGET_DATE in existing_dates:
                    print(f"  ⏭️ スキップ（{TARGET_DATE}まであり）")
                    continue
                
                try:
                    # 新しいデータを取得
                    result = get_all_history(
                        hall_id=hall_id,
                        unit_id=str(unit_id),
                        hall_name=hall_name,
                        expected_machine=machine_key
                    )
                    
                    new_days = result.get('days', [])
                    print(f"  取得データ: {len(new_days)}日分")
                    
                    # 新しい日付のみ追加
                    days_to_add = []
                    for day in new_days:
                        date = day.get('date', '')
                        if date and date not in existing_dates:
                            days_to_add.append(day)
                            print(f"    + {date}: ART={day.get('art', 0)}")
                    
                    if days_to_add:
                        _accumulate_unit(store_key, unit_id, days_to_add, machine_key)
                        total_updated += 1
                        total_added_days += len(days_to_add)
                        print(f"  ✅ {len(days_to_add)}日分追加")
                    else:
                        print(f"  ⏭️ 追加なし（既に最新）")
                        
                except Exception as e:
                    print(f"  ❌ エラー: {e}")
                
                # レート制限対策
                time.sleep(2)
    
    print(f"\n{'='*60}")
    print(f"完了: {total_updated}台のデータを更新 ({total_added_days}日分追加)")
    print(f"{'='*60}")

if __name__ == '__main__':
    main()
