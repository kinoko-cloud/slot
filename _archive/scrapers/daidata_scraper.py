#!/usr/bin/env python3
"""
台データオンライン スクレイパー
- 広告を削除してデータを取得
- 機種別・台番号別データを取得
"""

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import re
import json
from datetime import datetime
from pathlib import Path

# 店舗設定
SHOPS = {
    'espass_shibuya_shinkan': {
        'name': '渋谷エスパス新館',
        'hall_id': '100860',
    },
}

# 広告を削除するスクリプト
REMOVE_ADS_SCRIPT = """
() => {
    // 広告関連の要素を削除
    const adSelectors = [
        '#gn_interstitial_outer_area',
        '.yads_ad_item',
        '[id*="google_ads"]',
        '[id*="yads"]',
        '[class*="ad-"]',
        '[class*="ads-"]',
        'iframe[src*="doubleclick"]',
        'iframe[src*="googlesyndication"]',
    ];
    adSelectors.forEach(selector => {
        document.querySelectorAll(selector).forEach(el => el.remove());
    });

    // オーバーレイを削除
    document.querySelectorAll('[style*="position: fixed"]').forEach(el => {
        if (el.style.zIndex > 100) el.remove();
    });

    return 'Ads removed';
}
"""


def get_machine_list(page, hall_id: str) -> list[dict]:
    """機種一覧を取得"""
    url = f"https://daidata.goraggio.com/{hall_id}/list?mode=psModelNameSearch&ps=S"
    print(f"  機種一覧: {url}")

    page.goto(url, wait_until='load', timeout=60000)
    page.wait_for_timeout(3000)
    page.evaluate(REMOVE_ADS_SCRIPT)
    page.wait_for_timeout(1000)

    # 機種リンクを取得
    machines = page.evaluate('''() => {
        const links = document.querySelectorAll('a');
        const machines = [];
        links.forEach(link => {
            const text = link.innerText.trim();
            const href = link.href;
            // 機種詳細ページへのリンクを探す
            if (href.includes('/list?') && href.includes('psModelName=')) {
                machines.push({text, href});
            }
        });
        return machines;
    }''')

    return machines


def get_machine_detail(page, hall_id: str, machine_name: str) -> list[dict]:
    """特定機種の台一覧を取得"""
    import urllib.parse
    encoded_name = urllib.parse.quote(machine_name)
    url = f"https://daidata.goraggio.com/{hall_id}/list?mode=psModelNameSearch&ps=S&psModelName={encoded_name}"

    print(f"  機種詳細: {machine_name}")
    page.goto(url, wait_until='load', timeout=60000)
    page.wait_for_timeout(3000)
    page.evaluate(REMOVE_ADS_SCRIPT)
    page.wait_for_timeout(1000)

    html = page.content()
    text = page.inner_text('body')

    print(f"    テキスト長: {len(text)}")

    # 台番号データを探す
    results = []
    soup = BeautifulSoup(html, 'lxml')

    # 台へのリンクを探す
    links = soup.find_all('a', href=True)
    unit_links = []
    for link in links:
        href = link.get('href', '')
        if '/detail?unit=' in href:
            unit_id = re.search(r'unit=(\d+)', href)
            if unit_id:
                unit_links.append({
                    'unit_id': unit_id.group(1),
                    'text': link.get_text(strip=True),
                    'href': href
                })

    print(f"    台リンク数: {len(unit_links)}")

    return unit_links


def get_unit_detail(page, hall_id: str, unit_id: str) -> dict:
    """特定台の詳細データを取得"""
    url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}"

    page.goto(url, wait_until='load', timeout=60000)
    page.wait_for_timeout(3000)
    page.evaluate(REMOVE_ADS_SCRIPT)
    page.wait_for_timeout(1000)

    text = page.inner_text('body')
    html = page.content()

    result = {
        'unit_id': unit_id,
        'url': url,
        'raw_text': text[:2000],  # 最初の2000文字
    }

    # BB, RB, ART などを探す
    bb_match = re.search(r'BB[:\s]*(\d+)', text)
    rb_match = re.search(r'RB[:\s]*(\d+)', text)
    art_match = re.search(r'ART[:\s]*(\d+)', text)
    at_match = re.search(r'AT[:\s]*(\d+)', text)
    games_match = re.search(r'総ゲーム数[:\s]*(\d[\d,]*)', text)
    start_match = re.search(r'スタート[:\s]*(\d[\d,]*)', text)

    if bb_match:
        result['bb'] = int(bb_match.group(1))
    if rb_match:
        result['rb'] = int(rb_match.group(1))
    if art_match:
        result['art'] = int(art_match.group(1))
    if at_match:
        result['at'] = int(at_match.group(1))
    if games_match:
        result['total_games'] = int(games_match.group(1).replace(',', ''))
    if start_match:
        result['start'] = int(start_match.group(1).replace(',', ''))

    # 当たり履歴を探す
    history_matches = re.findall(r'(\d+)G\s*(BB|RB|ART|AT)', text)
    if history_matches:
        result['history'] = [{'games': int(g), 'type': t} for g, t in history_matches[:20]]

    return result


def scrape_daidata(shop_id: str, target_machine: str = None):
    """台データオンラインをスクレイピング"""
    shop = SHOPS.get(shop_id)
    if not shop:
        raise ValueError(f"Unknown shop: {shop_id}")

    hall_id = shop['hall_id']
    print(f"=" * 60)
    print(f"台データオンライン: {shop['name']}")
    print(f"=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = context.new_page()

        try:
            # 機種一覧を取得
            machines = get_machine_list(page, hall_id)
            print(f"\n機種数: {len(machines)}")

            # SBJを探す
            sbj_machines = [m for m in machines if 'ブラックジャック' in m.get('text', '')]
            if sbj_machines:
                print(f"SBJ発見: {sbj_machines}")

            # 特定機種の詳細を取得
            if target_machine:
                units = get_machine_detail(page, hall_id, target_machine)

                # 各台の詳細
                all_details = []
                for unit in units[:5]:  # 最初の5台
                    print(f"  台{unit['unit_id']}のデータ取得中...")
                    detail = get_unit_detail(page, hall_id, unit['unit_id'])
                    all_details.append(detail)
                    print(f"    → {detail}")

                return all_details

            return machines

        except Exception as e:
            print(f"エラー: {e}")
            import traceback
            traceback.print_exc()
            return None

        finally:
            browser.close()


def test_direct_urls():
    """直接URLアクセスをテスト"""
    print("=" * 60)
    print("直接URLアクセステスト")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            # スロット台番号一覧に直接アクセス
            url = "https://daidata.goraggio.com/100860/all_list?ps=S"
            print(f"アクセス: {url}")

            page.goto(url, wait_until='load', timeout=60000)
            page.wait_for_timeout(3000)

            # 広告を削除
            page.evaluate(REMOVE_ADS_SCRIPT)
            page.wait_for_timeout(1000)

            text = page.inner_text('body')
            print(f"テキスト長: {len(text)}")

            # ブラックジャック検索
            if 'ブラックジャック' in text:
                print("✓ 'ブラックジャック' 発見！")
                idx = text.find('ブラックジャック')
                print(f"コンテキスト: {text[max(0,idx-50):idx+150]}")
            else:
                print("✗ 'ブラックジャック' 未発見")
                # 何があるか確認
                print(f"テキストサンプル: {text[:500]}")

            # 全台リンクを取得
            links = page.evaluate('''() => {
                return Array.from(document.querySelectorAll('a')).map(a => ({
                    text: a.innerText.trim().substring(0, 50),
                    href: a.href
                })).filter(l => l.href.includes('/detail'));
            }''')
            print(f"\n台リンク数: {len(links)}")
            for l in links[:10]:
                print(f"  - {l['text']}: {l['href']}")

            # スクリーンショット
            page.screenshot(path='data/raw/daidata_all_list.png')
            print("\n✓ スクリーンショット: data/raw/daidata_all_list.png")

        except Exception as e:
            print(f"エラー: {e}")
            import traceback
            traceback.print_exc()

        finally:
            browser.close()


if __name__ == "__main__":
    test_direct_urls()
