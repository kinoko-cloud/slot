#!/usr/bin/env python3
"""
単一店舗のデータを取得（並列実行用）
"""
import sys
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import sync_playwright

JST = timezone(timedelta(hours=9))

# 店舗設定をインポート
from scripts.fetch_daidata_availability import (
    DAIDATA_STORES,
    fetch_daidata_store,
    fetch_daidata_unit_detail
)

def main():
    if len(sys.argv) < 2:
        print("Usage: python fetch_single_store.py <store_key>")
        sys.exit(1)

    store_key = sys.argv[1]

    if store_key not in DAIDATA_STORES:
        print(f"Unknown store: {store_key}")
        sys.exit(1)

    config = DAIDATA_STORES[store_key]
    print(f"Fetching data for {config['name']}...")

    # データ取得
    store_data = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={'width': 1280, 'height': 800},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            page = context.new_page()

            # 店舗データ取得
            avail_data = fetch_daidata_store(page, config['hall_id'], config.get('model_encoded'))

            # 各台の詳細取得
            units = []
            for unit_id in config['units']:
                try:
                    unit_data = fetch_daidata_unit_detail(
                        page,
                        config['hall_id'],
                        unit_id,
                        last_hit_time=None
                    )
                    units.append(unit_data)
                    print(f"  {unit_id}: OK")
                except Exception as e:
                    print(f"  {unit_id}: Error - {e}")
                    units.append({
                        'unit_id': unit_id,
                        'error': str(e)
                    })

            browser.close()

            store_data = {
                'store_key': store_key,
                'store_name': config['name'],
                'hall_id': config['hall_id'],
                'fetched_at': datetime.now(JST).isoformat(),
                'availability': avail_data,
                'units': units
            }

    except Exception as e:
        print(f"Error fetching {store_key}: {e}")
        store_data = {
            'store_key': store_key,
            'error': str(e),
            'fetched_at': datetime.now(JST).isoformat()
        }

    # 保存
    output_dir = PROJECT_ROOT / 'data' / 'partial'
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f'{store_key}.json'

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(store_data, f, ensure_ascii=False, indent=2)

    print(f"✅ Saved to {output_file}")

if __name__ == '__main__':
    main()
