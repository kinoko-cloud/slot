#!/usr/bin/env python3
"""
GitHub Actions用: daidataから空き状況を取得してJSONに保存
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

        # ページ読み込み待機（短縮）
        page.wait_for_timeout(2000)

        # HTMLを取得
        html = page.content()

        # 遊技中の台を検出
        playing = []
        empty = []

        for unit_id in expected_units:
            # 台番号を含む行を探す
            # パターン: <tr>...<td>(<em class="slot icon-user">)?</td><td><a>台番号</a></td>...</tr>
            pattern = rf'<tr[^>]*>.*?<td[^>]*>(.*?)</td>\s*<td[^>]*>\s*<a[^>]*>\s*{unit_id}\s*</a>'
            match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)

            if match:
                first_td_content = match.group(1)
                # icon-user があれば遊技中
                if 'icon-user' in first_td_content:
                    playing.append(unit_id)
                    print(f"    {unit_id}: 遊技中")
                else:
                    empty.append(unit_id)
                    print(f"    {unit_id}: 空き")
            else:
                # 見つからない場合は空きとする
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


def main():
    result = {
        'stores': {},
        'fetched_at': datetime.now(JST).isoformat(),
    }

    with sync_playwright() as p:
        # リソース使用を最小化
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-gpu',
                '--disable-dev-shm-usage',  # メモリ使用量削減
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

        # 不要なリソースをブロック（メモリ節約）
        page = context.new_page()
        page.route("**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf}", lambda route: route.abort())
        page.route("**/google*", lambda route: route.abort())
        page.route("**/geniee*", lambda route: route.abort())
        page.route("**/doubleclick*", lambda route: route.abort())

        for store_key, config in DAIDATA_STORES.items():
            print(f"Fetching {config['name']}...")

            data = fetch_store_availability(
                page,
                config['hall_id'],
                config['model_encoded'],
                config['units']
            )
            data['name'] = config['name']
            result['stores'][store_key] = data

            print(f"  Result - Playing: {data.get('playing', [])}, Empty: {data.get('empty', [])}")

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
