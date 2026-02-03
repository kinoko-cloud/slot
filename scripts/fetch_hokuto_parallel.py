#!/usr/bin/env python3
"""
北斗転生2の並列データ取得

エスパス北斗店舗（5店舗）を並列で取得してavailability.jsonにマージする。
"""
import sys
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import sync_playwright
import re

JST = timezone(timedelta(hours=9))
PROJECT_ROOT = Path(__file__).parent.parent

# 北斗転生2の店舗設定（fetch_daidata_availability.pyから抽出）
HOKUTO_STORES = {
    'shibuya_espass_hokuto': {
        'hall_id': '100860',
        'name': 'エスパス渋谷新館(北斗)',
        'units': [str(i) for i in range(2046, 2068)] + [str(i) for i in range(2233, 2241)],
    },
    'shibuya_honkan_espass_hokuto': {
        'hall_id': '100930',
        'name': 'エスパス渋谷本館(北斗)',
        'units': [str(i) for i in range(2013, 2020)] + [str(i) for i in range(2030, 2038)],
    },
    'shinjuku_espass_hokuto': {
        'hall_id': '100949',
        'name': 'エスパス歌舞伎町(北斗)',
        'units': [str(i) for i in range(1, 38)] + [str(i) for i in range(125, 129)],
    },
    'akiba_espass_hokuto': {
        'hall_id': '100928',
        'name': 'エスパス秋葉原(北斗)',
        'units': [str(i) for i in range(2011, 2020)] + [str(i) for i in range(2056, 2069)],
    },
    'seibu_shinjuku_espass_hokuto': {
        'hall_id': '100950',
        'name': 'エスパス西武新宿(北斗)',
        'units': [str(i) for i in range(3138, 3152)] + ['3165', '3166', '3185', '3186', '3187'],
    },
}


def fetch_unit_data(hall_id, unit_id, hall_name):
    """1台のデータを取得（Playwright使用）"""
    url = f'https://daidata.goraggio.com/101033/{hall_id}/unitDetail/{unit_id}'

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            # ページ読み込み
            page.goto(url, timeout=20000, wait_until='domcontentloaded')
            page.wait_for_timeout(2000)

            # 規約同意ボタンがあればクリック
            try:
                accept_btn = page.locator('text="利用規約に同意する"')
                if accept_btn.count() > 0:
                    accept_btn.click()
                    page.wait_for_timeout(2000)
                    page.goto(url, timeout=20000, wait_until='domcontentloaded')
                    page.wait_for_timeout(2000)
            except:
                pass

            # データ抽出
            text = page.inner_text('body', timeout=30000)
            data = {'unit_id': unit_id, 'bb': 0, 'rb': 0, 'art': 0, 'total_start': 0, 'final_start': 0}

            # BB/RB/ART/スタート
            match = re.search(r'BB\s+RB\s+ART\s+スタート回数\s*\n?\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)', text)
            if match:
                data['bb'] = int(match.group(1))
                data['rb'] = int(match.group(2))
                data['art'] = int(match.group(3))
                data['final_start'] = int(match.group(4))

            # 累計スタート
            total_match = re.search(r'累計スタート\s*\n?\s*(\d+)', text)
            if total_match:
                data['total_start'] = int(total_match.group(1))

            # 差枚
            diff_match = re.search(r'差枚\s*\n?\s*([+-]?\d+)', text)
            if diff_match:
                data['diff_medals'] = int(diff_match.group(1))

            # 最大メダル
            max_match = re.search(r'(?:最大メダル|最大持ちコイン|最大枚数|最大持ち玉)\s*\n?\s*([\d,]+)', text)
            if max_match:
                data['max_medals'] = int(max_match.group(1).replace(',', ''))

            # 当日履歴
            try:
                history = []
                hits = re.findall(
                    r'0\s+(\d+)\s+(\d+)\s+(ART|BB|RB|AT|REG)\s+(\d{1,2}:\d{2})',
                    text
                )

                for i, match in enumerate(hits):
                    history.append({
                        'hit_num': i + 1,
                        'time': match[3],
                        'start': int(match[0]),
                        'medals': int(match[1]),
                        'type': match[2],
                    })

                if history:
                    data['today_history'] = history
                    # 最大連チャン計算
                    sorted_hist = sorted(history, key=lambda h: h['time'])
                    max_rensa = 1
                    current_rensa = 1
                    for j in range(1, len(sorted_hist)):
                        if sorted_hist[j]['start'] <= 70:
                            current_rensa += 1
                            max_rensa = max(max_rensa, current_rensa)
                        else:
                            current_rensa = 1
                    data['today_max_rensa'] = max_rensa
            except:
                pass

            return data

        except Exception as e:
            print(f"    ❌ {unit_id}: {e}")
            return None
        finally:
            browser.close()


def fetch_store(store_key, store_config):
    """1店舗のデータを並列取得"""
    hall_id = store_config['hall_id']
    hall_name = store_config['name']
    units = store_config['units']

    print(f"\n🔄 {hall_name} ({len(units)}台) 取得開始...")

    results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch_unit_data, hall_id, uid, hall_name): uid for uid in units}

        for i, future in enumerate(as_completed(futures), 1):
            try:
                data = future.result(timeout=60)
                if data:
                    results.append(data)
                    if i % 5 == 0 or i == len(units):
                        print(f"  進捗: {i}/{len(units)}台")
            except Exception as e:
                print(f"  ❌ エラー: {e}")

    print(f"✅ {hall_name} 完了: {len(results)}/{len(units)}台")
    return {
        'store_key': store_key,
        'units': results,
        'fetched_at': datetime.now(JST).isoformat(),
    }


def main():
    print("=" * 60)
    print("🚀 北斗転生2 並列データ取得")
    print(f"対象: {len(HOKUTO_STORES)}店舗")
    print("=" * 60)

    # availability.jsonを読み込み
    avail_path = PROJECT_ROOT / 'data' / 'availability.json'
    if avail_path.exists():
        with open(avail_path, 'r', encoding='utf-8') as f:
            avail_data = json.load(f)
    else:
        avail_data = {'stores': {}, 'fetched_at': datetime.now(JST).isoformat()}

    # 各店舗を取得
    for store_key, store_config in HOKUTO_STORES.items():
        result = fetch_store(store_key, store_config)
        avail_data['stores'][store_key] = {
            'units': result['units'],
            'empty': [],
            'playing': [],
        }

    # availability.jsonに保存
    avail_data['fetched_at'] = datetime.now(JST).isoformat()
    with open(avail_path, 'w', encoding='utf-8') as f:
        json.dump(avail_data, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("✅ 完了！availability.jsonに保存しました")
    print("=" * 60)


if __name__ == '__main__':
    main()
