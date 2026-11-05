#!/usr/bin/env python3
"""
並列取得した店舗データをマージ
"""
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
PROJECT_ROOT = Path(__file__).parent.parent

def main():
    partial_dir = PROJECT_ROOT / 'data' / 'partial'

    if not partial_dir.exists():
        print("❌ No partial data directory found")
        return

    # 全店舗データを読み込み
    stores = {}
    errors = []

    for store_file in partial_dir.glob('store-*/*.json'):
        try:
            with open(store_file, 'r', encoding='utf-8') as f:
                store_data = json.load(f)

            store_key = store_data.get('store_key')
            if not store_key:
                continue

            if 'error' in store_data:
                errors.append(f"{store_key}: {store_data['error']}")
                continue

            # availability形式に変換
            store_info = {
                'store_name': store_data.get('store_name', ''),
                'hall_id': store_data.get('hall_id', ''),
                'fetched_at': store_data.get('fetched_at', ''),
                'playing': store_data.get('availability', {}).get('playing', []),
                'empty': store_data.get('availability', {}).get('empty', []),
                'units': store_data.get('units', [])
            }

            stores[store_key] = store_info
            print(f"✅ Merged {store_key}: {len(store_info['units'])} units")

        except Exception as e:
            print(f"⚠️ Error reading {store_file}: {e}")
            errors.append(f"{store_file.stem}: {e}")

    # 既存のavailability.jsonを読み込んで、取得できなかった店舗のデータを保持
    output_file = PROJECT_ROOT / 'data' / 'availability.json'
    existing_stores = {}

    if output_file.exists():
        try:
            with open(output_file, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
                existing_stores = existing_data.get('stores', {})
        except:
            pass

    # マージ（新データ優先、なければ既存データ保持）
    for store_key in existing_stores:
        if store_key not in stores:
            stores[store_key] = existing_stores[store_key]
            print(f"📋 Kept existing data for {store_key}")

    # 結果を保存
    result = {
        'fetched_at': datetime.now(JST).isoformat(),
        'fetch_method': 'parallel',
        'stores': stores,
        'errors': errors if errors else None
    }

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Merged {len(stores)} stores to {output_file}")
    if errors:
        print(f"⚠️ {len(errors)} errors occurred:")
        for err in errors:
            print(f"  - {err}")

if __name__ == '__main__':
    main()
