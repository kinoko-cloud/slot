#!/usr/bin/env python3
"""
GitHub Actions用: daidataから空き状況とリアルタイムデータを取得してJSONに保存
"""

import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

JST = timezone(timedelta(hours=9))

# 店舗設定 (modelは半角カナでURLエンコード済み)
DAIDATA_STORES = {
    'shibuya_espass_sbj': {
        'hall_id': '100860',
        'name': '渋谷エスパス新館',
        'model_encoded': 'L%EF%BD%BD%EF%BD%B0%EF%BE%8A%EF%BE%9F%EF%BD%B0%EF%BE%8C%EF%BE%9E%EF%BE%97%EF%BD%AF%EF%BD%B8%EF%BD%BC%EF%BE%9E%EF%BD%AC%EF%BD%AF%EF%BD%B8',
        'units': ['3011', '3012', '3013'],
    },
    'shinjuku_espass_sbj': {
        'hall_id': '100949',
        'name': '新宿エスパス歌舞伎町',
        'model_encoded': 'L%EF%BD%BD%EF%BD%B0%EF%BE%8A%EF%BE%9F%EF%BD%B0%EF%BE%8C%EF%BE%9E%EF%BE%97%EF%BD%AF%EF%BD%B8%EF%BD%BC%EF%BE%9E%EF%BD%AC%EF%BD%AF%EF%BD%B8',
        'units': ['682', '683', '684', '685'],
    },
    'akihabara_espass_sbj': {
        'hall_id': '100928',
        'name': '秋葉原エスパス駅前',
        'model_encoded': 'L%EF%BD%BD%EF%BD%B0%EF%BE%8A%EF%BE%9F%EF%BD%B0%EF%BE%8C%EF%BE%9E%EF%BE%97%EF%BD%AF%EF%BD%B8%EF%BD%BC%EF%BE%9E%EF%BD%AC%EF%BD%AF%EF%BD%B8',
        'units': ['2158', '2159', '2160', '2161'],
    },
    'seibu_shinjuku_espass_sbj': {
        'hall_id': '100950',
        'name': '西武新宿駅前エスパス',
        'model_encoded': 'L%EF%BD%BD%EF%BD%B0%EF%BE%8A%EF%BE%9F%EF%BD%B0%EF%BE%8C%EF%BE%9E%EF%BE%97%EF%BD%AF%EF%BD%B8%EF%BD%BC%EF%BE%9E%EF%BD%AC%EF%BD%AF%EF%BD%B8',
        'units': ['3185', '3186', '3187', '4109', '4118', '4125', '4168'],
    },
}


def fetch_store_availability(page, hall_id: str, model_encoded: str, expected_units: list) -> dict:
    """店舗の台一覧ページから空き状況を取得"""

    url = f"https://daidata.goraggio.com/{hall_id}/unit_list?model={model_encoded}&ballPrice=21.70&ps=S"
    print(f"  URL: {url}")

    try:
        page.goto(url, timeout=20000, wait_until='domcontentloaded')

        # 規約同意ボタンをクリック
        try:
            accept_btn = page.locator('text="利用規約に同意する"')
            if accept_btn.count() > 0:
                accept_btn.click()
                page.wait_for_timeout(1500)
                print("  Accepted terms")
        except:
            pass

        # ポップアップを閉じる
        try:
            close_btn = page.locator('text="Close"')
            if close_btn.count() > 0:
                close_btn.first.click()
                page.wait_for_timeout(300)
        except:
            pass

        # ページ読み込み待機
        page.wait_for_timeout(2000)

        # HTMLを取得
        html = page.content()

        # 遊技中の台を検出
        playing = []
        empty = []

        for unit_id in expected_units:
            pattern = rf'<tr[^>]*>.*?<td[^>]*>(.*?)</td>\s*<td[^>]*>\s*<a[^>]*>\s*{unit_id}\s*</a>'
            match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)

            if match:
                first_td_content = match.group(1)
                if 'icon-user' in first_td_content:
                    playing.append(unit_id)
                    print(f"    {unit_id}: 遊技中")
                else:
                    empty.append(unit_id)
                    print(f"    {unit_id}: 空き")
            else:
                empty.append(unit_id)
                print(f"    {unit_id}: (not found, assuming empty)")

        return {
            'playing': sorted(playing),
            'empty': sorted(empty),
            'total': len(expected_units),
        }

    except Exception as e:
        print(f"  Error: {e}")
        return {
            'playing': [],
            'empty': expected_units,
            'total': len(expected_units),
            'error': str(e)
        }


def fetch_unit_detail(page, hall_id: str, unit_id: str) -> dict:
    """台詳細ページからリアルタイムデータを取得"""
    url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}"

    try:
        page.goto(url, timeout=20000, wait_until='domcontentloaded')

        # 規約同意ボタンをクリック
        try:
            accept_btn = page.locator('text="利用規約に同意する"')
            if accept_btn.count() > 0:
                accept_btn.click()
                page.wait_for_timeout(1500)
        except:
            pass

        page.wait_for_timeout(1500)

        # テキストからデータを抽出
        text = page.inner_text('body')

        data = {'unit_id': unit_id}

        # BB/RB/ART/スタート回数を取得
        # パターン: BB RB ART スタート回数\n数値 数値 数値 数値
        match = re.search(r'BB\s+RB\s+ART\s+スタート回数\s*\n?\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)', text)
        if match:
            data['bb'] = int(match.group(1))
            data['rb'] = int(match.group(2))
            data['art'] = int(match.group(3))
            data['final_start'] = int(match.group(4))
        else:
            # 別のパターンを試す
            bb_match = re.search(r'BB[^\d]*(\d+)', text)
            rb_match = re.search(r'RB[^\d]*(\d+)', text)
            art_match = re.search(r'ART[^\d]*(\d+)', text)

            if bb_match:
                data['bb'] = int(bb_match.group(1))
            if rb_match:
                data['rb'] = int(rb_match.group(1))
            if art_match:
                data['art'] = int(art_match.group(1))

        # 累計スタート
        total_match = re.search(r'累計スタート\s*\n?\s*(\d+)', text)
        if total_match:
            data['total_start'] = int(total_match.group(1))

        # 差枚
        diff_match = re.search(r'差枚\s*\n?\s*([+-]?\d+)', text)
        if diff_match:
            data['diff_medals'] = int(diff_match.group(1))

        print(f"    {unit_id}: ART={data.get('art', '?')}, G数={data.get('total_start', '?')}")
        return data

    except Exception as e:
        print(f"    {unit_id}: Error - {e}")
        return {'unit_id': unit_id, 'error': str(e)}


def main():
    result = {
        'stores': {},
        'fetched_at': datetime.now(JST).isoformat(),
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-gpu',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-extensions',
                '--disable-background-networking',
                '--disable-sync',
            ]
        )
        context = browser.new_context(
            user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15',
            java_script_enabled=True,
        )

        # 不要なリソースをブロック
        page = context.new_page()
        page.route("**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf}", lambda route: route.abort())
        page.route("**/google*", lambda route: route.abort())
        page.route("**/geniee*", lambda route: route.abort())
        page.route("**/doubleclick*", lambda route: route.abort())

        for store_key, config in DAIDATA_STORES.items():
            print(f"\nFetching {config['name']}...")

            # 空き状況を取得
            avail_data = fetch_store_availability(
                page,
                config['hall_id'],
                config['model_encoded'],
                config['units']
            )

            # 各台の詳細データを取得
            units_data = []
            print(f"  Fetching unit details...")
            for unit_id in config['units']:
                unit_data = fetch_unit_detail(page, config['hall_id'], unit_id)
                # 空き状況を追加
                if unit_id in avail_data.get('playing', []):
                    unit_data['availability'] = '遊技中'
                else:
                    unit_data['availability'] = '空き'
                units_data.append(unit_data)

            result['stores'][store_key] = {
                'name': config['name'],
                'hall_id': config['hall_id'],
                'playing': avail_data.get('playing', []),
                'empty': avail_data.get('empty', []),
                'total': avail_data.get('total', len(config['units'])),
                'units': units_data,
            }

            print(f"  Done - Playing: {avail_data.get('playing', [])}, Empty: {avail_data.get('empty', [])}")

        try:
            browser.close()
        except Exception as e:
            print(f"Warning: browser close error: {e}")

    # JSONに保存
    output_path = Path(__file__).parent.parent / 'data' / 'availability.json'
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\nSaved to {output_path}")


if __name__ == '__main__':
    main()
