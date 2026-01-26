#!/usr/bin/env python3
"""
台データオンライン スクレイパー（フォーム送信対応版）
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
    return 'Ads removed';
}
"""


def scrape_daidata_with_form_submit(hall_id: str = "100860"):
    """フォーム送信で規約に同意してデータを取得"""
    print("=" * 60)
    print("台データオンライン（フォーム送信対応）")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = context.new_page()

        try:
            # まず規約ページにアクセス
            url = f"https://daidata.goraggio.com/{hall_id}/all_list?ps=S"
            print(f"1. 規約ページにアクセス: {url}")

            page.goto(url, wait_until='load', timeout=60000)
            page.wait_for_timeout(2000)
            page.evaluate(REMOVE_ADS_SCRIPT)

            # フォームをJavaScriptで送信
            print("\n2. フォームを送信")
            result = page.evaluate('''() => {
                const form = document.querySelector('form');
                if (form) {
                    form.submit();
                    return 'Form submitted';
                }
                return 'No form found';
            }''')
            print(f"   結果: {result}")

            # ページ遷移を待つ
            page.wait_for_timeout(5000)

            # 現在のURL確認
            current_url = page.url
            print(f"\n3. 現在のURL: {current_url}")

            # 広告削除
            page.evaluate(REMOVE_ADS_SCRIPT)

            # ページ内容確認
            text = page.inner_text('body')
            print(f"   テキスト長: {len(text)}")

            if '規約' in text and len(text) < 2000:
                print("   → まだ規約ページの可能性")
                # 再度all_listにアクセス
                print("\n4. 再度データページにアクセス")
                page.goto(f"https://daidata.goraggio.com/{hall_id}/all_list?ps=S", wait_until='load', timeout=60000)
                page.wait_for_timeout(3000)
                page.evaluate(REMOVE_ADS_SCRIPT)

                text = page.inner_text('body')
                print(f"   テキスト長: {len(text)}")

            # ブラックジャック検索
            if 'ブラックジャック' in text:
                print("\n✓ 'ブラックジャック' 発見！")
                idx = text.find('ブラックジャック')
                print(f"   コンテキスト: ...{text[max(0,idx-30):idx+100]}...")
            else:
                # 機種名があるか確認
                machines = re.findall(r'[ァ-ヶー]{3,}|[a-zA-Z]{3,}', text)
                print(f"\n機種名らしきもの: {machines[:20]}")

            # スクリーンショット
            page.screenshot(path='data/raw/daidata_form_submit.png')
            print("\n✓ スクリーンショット: data/raw/daidata_form_submit.png")

            # リンク一覧
            links = page.evaluate('''() => {
                return Array.from(document.querySelectorAll('a')).map(a => ({
                    text: a.innerText.trim().substring(0, 50),
                    href: a.href
                })).filter(l => l.text.length > 0 && l.href.length > 0);
            }''')
            print(f"\n総リンク数: {len(links)}")

            detail_links = [l for l in links if '/detail' in l.get('href', '')]
            print(f"台詳細リンク数: {len(detail_links)}")

            if detail_links:
                for l in detail_links[:10]:
                    print(f"  - {l['text']}: {l['href']}")

                # 最初の台の詳細を取得
                print("\n5. 台詳細ページにアクセス")
                first_detail = detail_links[0]
                page.goto(first_detail['href'], wait_until='load', timeout=60000)
                page.wait_for_timeout(3000)
                page.evaluate(REMOVE_ADS_SCRIPT)

                detail_text = page.inner_text('body')
                print(f"   テキスト長: {len(detail_text)}")
                print(f"   内容サンプル: {detail_text[:500]}")

                # BB, RB, ART を探す
                if 'BB' in detail_text or 'ART' in detail_text:
                    print("\n✓ BB/ART データあり")

                page.screenshot(path='data/raw/daidata_detail.png')
                print("✓ スクリーンショット: data/raw/daidata_detail.png")

            return text, links

        except Exception as e:
            print(f"エラー: {e}")
            import traceback
            traceback.print_exc()
            return None, None

        finally:
            browser.close()


def try_accept_endpoint():
    """acceptエンドポイントを直接試す"""
    print("\n" + "=" * 60)
    print("acceptエンドポイント直接アクセス")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        try:
            # まず通常ページでトークンを取得
            page.goto("https://daidata.goraggio.com/100860/all_list?ps=S", wait_until='load', timeout=60000)
            page.wait_for_timeout(2000)

            # トークンを取得
            token = page.evaluate('''() => {
                const input = document.querySelector('input[type="hidden"]');
                return input ? input.value : null;
            }''')
            print(f"トークン: {token}")

            if token:
                # POSTリクエストを送信
                response = page.evaluate(f'''async () => {{
                    const formData = new FormData();
                    formData.append('_token', '{token}');

                    const response = await fetch('https://daidata.goraggio.com/100860/accept', {{
                        method: 'POST',
                        body: formData,
                        credentials: 'include'
                    }});

                    return {{
                        status: response.status,
                        url: response.url
                    }};
                }}''')
                print(f"POSTレスポンス: {response}")

                # データページにアクセス
                page.goto("https://daidata.goraggio.com/100860/all_list?ps=S", wait_until='load', timeout=60000)
                page.wait_for_timeout(3000)

                text = page.inner_text('body')
                print(f"テキスト長: {len(text)}")

                if 'ブラックジャック' in text:
                    print("✓ データ取得成功！")

        except Exception as e:
            print(f"エラー: {e}")

        finally:
            browser.close()


if __name__ == "__main__":
    scrape_daidata_with_form_submit()
    try_accept_endpoint()
