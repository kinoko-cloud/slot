#!/usr/bin/env python3
"""
台データオンライン - SBJ専用スクレイパー
"""

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import re
import json
from datetime import datetime
from pathlib import Path

REMOVE_ADS_SCRIPT = """
() => {
    const adSelectors = [
        '#gn_interstitial_outer_area',
        '.yads_ad_item',
        '[id*="google_ads"]',
        '[id*="yads"]',
    ];
    adSelectors.forEach(selector => {
        document.querySelectorAll(selector).forEach(el => el.remove());
    });
    return 'done';
}
"""


def get_sbj_data(hall_id: str = "100860", hall_name: str = "渋谷エスパス新館"):
    """SBJの台データを取得"""
    print("=" * 60)
    print(f"SBJデータ取得: {hall_name}")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = context.new_page()

        try:
            # 1. 規約同意
            print("\n1. 規約ページにアクセス")
            page.goto(f"https://daidata.goraggio.com/{hall_id}/all_list?ps=S", wait_until='load', timeout=60000)
            page.wait_for_timeout(2000)
            page.evaluate(REMOVE_ADS_SCRIPT)

            # フォーム送信
            page.evaluate('() => { const form = document.querySelector("form"); if (form) form.submit(); }')
            page.wait_for_timeout(3000)

            # 2. 機種検索ページにアクセス
            print("\n2. 機種検索ページにアクセス")
            search_url = f"https://daidata.goraggio.com/{hall_id}/list?mode=psModelNameSearch&ps=S"
            page.goto(search_url, wait_until='load', timeout=60000)
            page.wait_for_timeout(2000)
            page.evaluate(REMOVE_ADS_SCRIPT)

            text = page.inner_text('body')

            # ブラックジャックを検索
            if 'ブラックジャック' in text:
                print("✓ 'ブラックジャック' 発見！")
            else:
                print("✗ 機種一覧にブラックジャックなし")
                print("  機種名で検索を試行...")

            # 3. 検索フォームで検索
            print("\n3. 機種名で検索")
            search_input = page.query_selector('input[type="text"], input[type="search"], input[name*="search"], input[placeholder*="検索"]')
            if search_input:
                search_input.fill("ブラックジャック")
                page.wait_for_timeout(1000)

                # 検索ボタンを探す
                search_btn = page.query_selector('button[type="submit"], input[type="submit"], button:has-text("検索")')
                if search_btn:
                    search_btn.click()
                    page.wait_for_timeout(3000)
            else:
                print("  検索フォームが見つからない")

            # 4. ページ内のSBJリンクを探す
            print("\n4. SBJリンクを探す")
            html = page.content()
            soup = BeautifulSoup(html, 'lxml')

            sbj_links = []
            for link in soup.find_all('a', href=True):
                text = link.get_text(strip=True)
                href = link.get('href')
                if 'ブラックジャック' in text or 'SBJ' in text.upper():
                    sbj_links.append({'text': text, 'href': href})
                    print(f"  ★ {text}: {href}")

            # 5. 全台一覧からSBJを探す
            print("\n5. 全台一覧からSBJ台を探す")
            page.goto(f"https://daidata.goraggio.com/{hall_id}/all_list?ps=S", wait_until='load', timeout=60000)
            page.wait_for_timeout(3000)
            page.evaluate(REMOVE_ADS_SCRIPT)

            # ページのHTMLを解析
            html = page.content()
            soup = BeautifulSoup(html, 'lxml')

            # テーブルや台データを探す
            all_text = soup.get_text()

            # ブラックジャック周辺のテキストを取得
            if 'ブラックジャック' in all_text:
                idx = all_text.find('ブラックジャック')
                context = all_text[max(0, idx-200):idx+300]
                print(f"  コンテキスト: {context}")

                # 台番号を探す
                unit_numbers = re.findall(r'(\d{4})番台', context)
                if unit_numbers:
                    print(f"  SBJ台番号: {unit_numbers}")

            # 6. 各台の詳細を取得（テスト用に最初の数台）
            print("\n6. 台詳細データを取得")

            # すべての台リンクを取得
            all_links = page.evaluate('''() => {
                return Array.from(document.querySelectorAll('a')).map(a => ({
                    text: a.innerText.trim(),
                    href: a.href
                })).filter(l => l.href.includes('/detail?unit='));
            }''')

            print(f"  総台数: {len(all_links)}")

            # SBJ台を特定するため、いくつかの台をサンプリング
            sbj_units = []

            # まず機種名が見えるリンクを探す
            for link in all_links:
                if 'ブラックジャック' in link['text']:
                    unit_match = re.search(r'unit=(\d+)', link['href'])
                    if unit_match:
                        sbj_units.append(unit_match.group(1))

            if not sbj_units:
                # 全台から探す（効率化のため、台番号の範囲を推測）
                print("  機種名から特定できず、詳細ページを確認...")

                # 最初の20台をチェック
                for i, link in enumerate(all_links[:50]):
                    unit_match = re.search(r'unit=(\d+)', link['href'])
                    if unit_match:
                        unit_id = unit_match.group(1)
                        page.goto(link['href'], wait_until='load', timeout=30000)
                        page.wait_for_timeout(1500)
                        page.evaluate(REMOVE_ADS_SCRIPT)

                        detail_text = page.inner_text('body')
                        if 'ブラックジャック' in detail_text:
                            print(f"    ✓ 台{unit_id}: SBJ発見！")
                            sbj_units.append(unit_id)
                        elif i < 5 or i % 10 == 0:
                            # 進捗表示
                            machine_match = re.search(r'([\w\s]+)\s*\(', detail_text[:200])
                            machine_name = machine_match.group(1) if machine_match else "不明"
                            print(f"    台{unit_id}: {machine_name[:20]}...")

            print(f"\n  SBJ台: {sbj_units}")

            # 7. SBJ台の詳細データを取得
            results = []
            for unit_id in sbj_units:
                print(f"\n  台{unit_id}の詳細を取得...")
                detail = get_unit_detail(page, hall_id, unit_id)
                detail['hall_id'] = hall_id
                detail['hall_name'] = hall_name
                results.append(detail)
                print(f"    → {detail}")

            # 保存
            if results:
                save_path = Path('data/raw') / f'sbj_daidata_{hall_id}_{datetime.now().strftime("%Y%m%d")}.json'
                save_path.parent.mkdir(parents=True, exist_ok=True)
                with open(save_path, 'w', encoding='utf-8') as f:
                    json.dump(results, f, ensure_ascii=False, indent=2)
                print(f"\n✓ 保存: {save_path}")

            return results

        except Exception as e:
            print(f"エラー: {e}")
            import traceback
            traceback.print_exc()
            return None

        finally:
            browser.close()


def get_unit_detail(page, hall_id: str, unit_id: str) -> dict:
    """台の詳細データを取得"""
    url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}"
    page.goto(url, wait_until='load', timeout=30000)
    page.wait_for_timeout(2000)
    page.evaluate(REMOVE_ADS_SCRIPT)

    text = page.inner_text('body')

    result = {
        'unit_id': unit_id,
        'date': datetime.now().strftime('%Y-%m-%d'),
        'fetched_at': datetime.now().isoformat(),
    }

    # 機種名
    machine_match = re.search(r'([\w\sｱ-ﾝァ-ヶー]+)\s*\([^)]+\)', text)
    if machine_match:
        result['machine_name'] = machine_match.group(1).strip()

    # BB, RB, ART
    bb_match = re.search(r'BB\s*(\d+)', text)
    rb_match = re.search(r'RB\s*(\d+)', text)
    art_match = re.search(r'ART\s*(\d+)', text)

    if bb_match:
        result['bb'] = int(bb_match.group(1))
    if rb_match:
        result['rb'] = int(rb_match.group(1))
    if art_match:
        result['art'] = int(art_match.group(1))

    # スタート回数
    start_match = re.search(r'スタート回数\s*(\d+)', text)
    if start_match:
        result['start'] = int(start_match.group(1))

    # 累計スタート
    total_match = re.search(r'累計スタート\s*(\d+)', text)
    if total_match:
        result['total_start'] = int(total_match.group(1))

    # 最大持ち玉
    max_match = re.search(r'最大持ち玉\s*(\d+)', text)
    if max_match:
        result['max_medals'] = int(max_match.group(1))

    # 前日最終スタート
    prev_match = re.search(r'前日最終スタート\s*(\d+)', text)
    if prev_match:
        result['prev_final_start'] = int(prev_match.group(1))

    # 合成確率
    prob_match = re.search(r'合成確率\s*([\d.]+)', text)
    if prob_match:
        result['combined_prob'] = float(prob_match.group(1))

    # 大当たり履歴
    history = []
    history_matches = re.findall(r'(\d+)\s+(\d+)\s+(\d+)\s+(BB|RB|ART|AT)\s+(\d+:\d+)', text)
    for match in history_matches:
        history.append({
            'hit_num': int(match[0]),
            'start': int(match[1]),
            'medals': int(match[2]),
            'type': match[3],
            'time': match[4]
        })

    if history:
        result['history'] = history

    return result


if __name__ == "__main__":
    get_sbj_data()
