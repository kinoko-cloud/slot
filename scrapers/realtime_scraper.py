#!/usr/bin/env python3
"""
リアルタイムスクレイパー
当日データのみを高速で取得
"""

from playwright.sync_api import sync_playwright
import json
from datetime import datetime
from pathlib import Path

# 店舗設定（キーはconfig/rankings.pyと統一）
STORES = {
    # SBJ
    'island_akihabara_sbj': {
        'name': 'アイランド秋葉原',
        'source': 'papimo',
        'hall_id': '00031715',
        'sbj_machine_id': '225010000',
        'units': ['1015', '1016', '1017', '1018', '1020', '1021', '1022', '1023',
                  '1025', '1026', '1027', '1028', '1030', '1031'],
    },
    'shibuya_espass_sbj': {
        'name': 'エスパス日拓渋谷新館',
        'source': 'daidata',
        'hall_id': '100860',
        'units': ['3011', '3012', '3013'],
    },
    'shinjuku_espass_sbj': {
        'name': 'エスパス日拓新宿歌舞伎町店',
        'source': 'daidata',
        'hall_id': '100949',
        'units': ['682', '683', '684', '685'],
    },
    'akihabara_espass_sbj': {
        'name': 'エスパス日拓秋葉原駅前店',
        'source': 'daidata',
        'hall_id': '100928',
        'units': ['2158', '2159', '2160', '2161'],
    },
    'seibu_shinjuku_espass_sbj': {
        'name': 'エスパス日拓西武新宿駅前店',
        'source': 'daidata',
        'hall_id': '100950',
        'units': ['3185', '3186', '3187', '4109', '4118', '4125', '4168'],
    },
}

# 旧キーとの互換性
STORES['island_akihabara'] = STORES['island_akihabara_sbj']
STORES['shibuya_espass'] = STORES['shibuya_espass_sbj']


def scrape_papimo_current(page, hall_id: str, units: list) -> list:
    """papimo.jpから当日データを取得"""
    results = []

    for unit_id in units:
        url = f"https://papimo.jp/h/{hall_id}/hit/view/{unit_id}"

        try:
            page.goto(url, wait_until='load', timeout=20000)
            page.wait_for_timeout(1000)

            text = page.inner_text('body')

            # データ抽出
            import re
            data = {'unit_id': unit_id}

            # BB/RB/ART
            bb = re.search(r'BB回数\s*(\d+)', text)
            rb = re.search(r'RB回数\s*(\d+)', text)
            art = re.search(r'ART回数\s*(\d+)', text)
            total = re.search(r'総スタート\s*([\d,]+)', text)
            final = re.search(r'最終スタート\s*([\d,]+)', text)

            if bb: data['bb'] = int(bb.group(1))
            if rb: data['rb'] = int(rb.group(1))
            if art: data['art'] = int(art.group(1))
            if total: data['total_start'] = int(total.group(1).replace(',', ''))
            if final: data['final_start'] = int(final.group(1).replace(',', ''))

            # 履歴（もっと見るをクリック）
            while True:
                more_btn = page.query_selector('text=もっと見る')
                if more_btn and more_btn.is_visible():
                    more_btn.click()
                    page.wait_for_timeout(300)
                else:
                    break

            text = page.inner_text('body')
            history = []
            hist_matches = re.findall(
                r'(\d{1,2}:\d{2})\s+([\d,]+)\s+([\d,]+)\s*\n?\s*(ART|BB|RB|AT|REG)',
                text, re.MULTILINE
            )
            for i, m in enumerate(hist_matches):
                history.append({
                    'hit_num': i + 1,
                    'time': m[0],
                    'start': int(m[1].replace(',', '')),
                    'medals': int(m[2].replace(',', '')),
                    'type': m[3],
                })
            data['history'] = history

            results.append(data)
            print(f"  台{unit_id}: ART={data.get('art', 0)}, G数={data.get('total_start', 0)}")

        except Exception as e:
            print(f"  台{unit_id}: エラー - {e}")
            results.append({'unit_id': unit_id, 'error': str(e)})

    return results


def scrape_daidata_current(page, hall_id: str, units: list) -> list:
    """台データオンラインから当日データを取得"""
    results = []

    # 規約同意
    page.goto(f"https://daidata.goraggio.com/{hall_id}/all_list?ps=S", wait_until='load', timeout=30000)
    page.wait_for_timeout(2000)
    page.evaluate('() => { const form = document.querySelector("form"); if (form) form.submit(); }')
    page.wait_for_timeout(2000)

    for unit_id in units:
        url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}"

        try:
            page.goto(url, wait_until='load', timeout=20000)
            page.wait_for_timeout(1500)

            # 広告除去
            page.evaluate('''() => {
                document.querySelectorAll('#gn_interstitial_outer_area, .yads_ad_item, [id*="google_ads"]').forEach(el => el.remove());
            }''')

            text = page.inner_text('body')

            import re
            data = {'unit_id': unit_id}

            # サマリー
            summary = re.search(r'BB\s+RB\s+ART\s+スタート回数\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)', text)
            if summary:
                data['bb'] = int(summary.group(1))
                data['rb'] = int(summary.group(2))
                data['art'] = int(summary.group(3))
                data['final_start'] = int(summary.group(4))

            total = re.search(r'累計スタート\s*(\d+)', text)
            if total:
                data['total_start'] = int(total.group(1))

            # 履歴
            history = []
            hist_section = re.search(r'本日の大当たり履歴詳細.*?大当たり\s+スタート\s+出玉\s+種別\s+時間(.+?)(?:過去|ページ|$)', text, re.DOTALL)
            if hist_section:
                matches = re.findall(r'(\d+)\s+(\d+)\s+(\d+)\s+(ART|BB|RB|AT)\s+(\d{1,2}:\d{2})', hist_section.group(1))
                for m in matches:
                    history.append({
                        'hit_num': int(m[0]),
                        'start': int(m[1]),
                        'medals': int(m[2]),
                        'type': m[3],
                        'time': m[4],
                    })
            data['history'] = history

            results.append(data)
            print(f"  台{unit_id}: ART={data.get('art', 0)}, G数={data.get('total_start', 0)}")

        except Exception as e:
            print(f"  台{unit_id}: エラー - {e}")
            results.append({'unit_id': unit_id, 'error': str(e)})

    return results


def scrape_realtime(store_key: str = None) -> dict:
    """リアルタイムデータを取得"""
    results = {}
    now = datetime.now()

    stores_to_scrape = [store_key] if store_key else STORES.keys()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for key in stores_to_scrape:
            if key not in STORES:
                continue

            store = STORES[key]
            print(f"\n【{store['name']}】")

            if store['source'] == 'papimo':
                data = scrape_papimo_current(page, store['hall_id'], store['units'])
            elif store['source'] == 'daidata':
                data = scrape_daidata_current(page, store['hall_id'], store['units'])
            else:
                continue

            results[key] = {
                'store_name': store['name'],
                'fetched_at': now.isoformat(),
                'units': data,
            }

        browser.close()

    return results


def main():
    """メイン処理"""
    print("=" * 60)
    print("SBJ リアルタイムデータ取得")
    print(f"取得時刻: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # 全店舗のデータを取得
    results = scrape_realtime()

    # 保存
    save_path = Path('data/raw') / f'realtime_{datetime.now().strftime("%Y%m%d_%H%M")}.json'
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n✓ 保存: {save_path}")

    # 予測を実行
    from analysis.realtime_predictor import generate_realtime_report

    for key, data in results.items():
        store = STORES[key]
        current_day_data = data['units']
        report = generate_realtime_report(store['name'], current_day_data)
        print(report)

    return results


if __name__ == "__main__":
    main()
