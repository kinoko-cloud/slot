#!/usr/bin/env python3
"""
デイリーデータ収集スクリプト

毎日実行して履歴データを蓄積
- 前日までのデータを取得
- JSONで日付別に保存
- 長期的な傾向分析用
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
import sys

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from scrapers.papimo import get_unit_history, PAPIMO_CONFIG
from scrapers.daidata_detail_history import get_all_history


def collect_daily_data():
    """全店舗のデータを収集"""
    today = datetime.now().strftime('%Y%m%d')
    data_dir = Path('data/daily')
    data_dir.mkdir(parents=True, exist_ok=True)

    results = {
        'collected_at': datetime.now().isoformat(),
        'stores': {}
    }

    print('=' * 60)
    print(f'デイリーデータ収集 - {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('=' * 60)

    # 1. 渋谷エスパス（daidata）
    print('\n【渋谷エスパス新館】')
    try:
        from playwright.sync_api import sync_playwright

        shibuya_units = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # 規約同意を一度だけ
            page.goto('https://daidata.goraggio.com/100860/all_list?ps=S', wait_until='load', timeout=60000)
            page.wait_for_timeout(2000)
            page.evaluate('() => { const form = document.querySelector("form"); if (form) form.submit(); }')
            page.wait_for_timeout(3000)

            for unit_id in ['3011', '3012', '3013']:
                print(f'  台{unit_id}...')
                result = get_all_history(hall_id='100860', unit_id=unit_id, hall_name='渋谷エスパス新館')
                if result:
                    shibuya_units.append(result)

            browser.close()

        results['stores']['shibuya_espass'] = {
            'hall_name': '渋谷エスパス新館',
            'units': shibuya_units,
        }
        print(f'  ✓ {len(shibuya_units)}台取得')

    except Exception as e:
        print(f'  ✗ エラー: {e}')

    # 2. アイランド秋葉原（papimo）
    print('\n【アイランド秋葉原店】')
    try:
        from playwright.sync_api import sync_playwright

        config = PAPIMO_CONFIG['island_akihabara']
        akiba_units = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            for unit_id in config['sbj_units']:
                print(f'  台{unit_id}...')
                result = get_unit_history(page, config['hall_id'], unit_id, days_back=1)
                result['hall_name'] = config['hall_name']
                result['machine_name'] = 'Lスーパーブラックジャック'
                akiba_units.append(result)

            browser.close()

        results['stores']['island_akihabara'] = {
            'hall_name': 'アイランド秋葉原店',
            'units': akiba_units,
        }
        print(f'  ✓ {len(akiba_units)}台取得')

    except Exception as e:
        print(f'  ✗ エラー: {e}')

    # 保存
    save_path = data_dir / f'sbj_daily_{today}.json'
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f'\n✓ 保存: {save_path}')

    return results


def merge_historical_data():
    """過去のデイリーデータを統合して長期分析用データを作成"""
    data_dir = Path('data/daily')
    merged_dir = Path('data/merged')
    merged_dir.mkdir(parents=True, exist_ok=True)

    # 全デイリーファイルを読み込み
    daily_files = sorted(data_dir.glob('sbj_daily_*.json'))

    if not daily_files:
        print('デイリーデータがありません')
        return

    print(f'統合対象: {len(daily_files)}日分')

    # 台ごとにデータを統合
    merged = {}

    for file_path in daily_files:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        for store_key, store_data in data.get('stores', {}).items():
            hall_name = store_data.get('hall_name', '')

            for unit in store_data.get('units', []):
                unit_id = unit.get('unit_id')
                key = f"{store_key}_{unit_id}"

                if key not in merged:
                    merged[key] = {
                        'unit_id': unit_id,
                        'hall_name': hall_name,
                        'machine_name': unit.get('machine_name', 'SBJ'),
                        'days': [],
                    }

                # 日別データを追加
                for day in unit.get('days', []):
                    # 重複チェック
                    existing_dates = [d.get('date') for d in merged[key]['days']]
                    if day.get('date') not in existing_dates:
                        merged[key]['days'].append(day)

    # 日付順にソート
    for key in merged:
        merged[key]['days'].sort(key=lambda d: d.get('date', ''), reverse=True)

    # 保存
    save_path = merged_dir / f'sbj_merged_{datetime.now().strftime("%Y%m%d")}.json'
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(list(merged.values()), f, ensure_ascii=False, indent=2)

    print(f'✓ 統合データ保存: {save_path}')
    print(f'  台数: {len(merged)}')

    # 統計表示
    for key, data in merged.items():
        days = len(data['days'])
        print(f'  {data["hall_name"]} 台{data["unit_id"]}: {days}日分')


def main():
    import argparse
    parser = argparse.ArgumentParser(description='SBJ デイリーデータ収集')
    parser.add_argument('--merge', action='store_true', help='過去データを統合')
    args = parser.parse_args()

    if args.merge:
        merge_historical_data()
    else:
        collect_daily_data()


if __name__ == '__main__':
    main()
