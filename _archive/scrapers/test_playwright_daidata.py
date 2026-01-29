#!/usr/bin/env python3
"""
Playwrightで台データオンラインをスクレイピング
"""

from playwright.sync_api import sync_playwright
import re
import json


def test_daidata():
    """台データオンラインをPlaywrightで取得"""
    print("=" * 70)
    print("台データオンライン（Playwright）")
    print("=" * 70)

    with sync_playwright() as p:
        # ヘッドレスブラウザを起動
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = context.new_page()

        try:
            # 渋谷エスパス新館にアクセス
            url = "https://daidata.goraggio.com/100860"
            print(f"アクセス中: {url}")
            page.goto(url, wait_until='networkidle', timeout=30000)

            # ページが読み込まれるまで待機
            page.wait_for_timeout(3000)

            # ページタイトル
            title = page.title()
            print(f"タイトル: {title}")

            # ページのテキスト内容
            content = page.content()
            print(f"HTMLサイズ: {len(content)} bytes")

            # ブラックジャック関連を検索
            text = page.inner_text('body')
            if 'ブラックジャック' in text:
                print("✓ 'ブラックジャック' を発見！")

                # 前後のテキストを取得
                idx = text.find('ブラックジャック')
                context_text = text[max(0, idx-100):idx+200]
                print(f"コンテキスト: {context_text[:300]}...")
            else:
                print("✗ 'ブラックジャック' は見つからず")

            # 機種検索を試す
            print("\n【機種検索を試行】")

            # 検索ボックスを探す
            search_input = page.query_selector('input[type="search"], input[placeholder*="検索"], input[name*="search"]')
            if search_input:
                print("✓ 検索ボックスを発見")
                search_input.fill("ブラックジャック")
                page.wait_for_timeout(2000)

            # スロットタブがあれば切り替え
            slot_tab = page.query_selector('text=スロット')
            if slot_tab:
                print("✓ スロットタブを発見、クリック")
                slot_tab.click()
                page.wait_for_timeout(2000)

            # 機種一覧のリンクを探す
            links = page.query_selector_all('a')
            sbj_links = []
            for link in links:
                text = link.inner_text()
                if 'ブラックジャック' in text:
                    href = link.get_attribute('href')
                    sbj_links.append((text, href))
                    print(f"  ★SBJリンク発見: {text[:50]} → {href}")

            # 機種別で探すセクションを探す
            machine_section = page.query_selector('text=機種別で探す')
            if machine_section:
                print("\n✓ '機種別で探す' セクションを発見")
                machine_section.click()
                page.wait_for_timeout(2000)

                # 更新後のコンテンツ
                text2 = page.inner_text('body')
                if 'ブラックジャック' in text2:
                    print("✓ 機種一覧にSBJ発見")

            # ネットワークリクエストを確認（APIを探す）
            print("\n【API調査】")
            # ページをリロードしてネットワークを監視
            api_urls = []

            def handle_response(response):
                url = response.url
                if 'api' in url.lower() or 'json' in response.headers.get('content-type', ''):
                    api_urls.append(url)

            page.on('response', handle_response)
            page.reload(wait_until='networkidle')
            page.wait_for_timeout(3000)

            if api_urls:
                print(f"発見したAPI: {len(api_urls)}個")
                for api_url in api_urls[:5]:
                    print(f"  - {api_url[:100]}")

            # スクリーンショットを保存
            page.screenshot(path='data/raw/daidata_screenshot.png')
            print("\n✓ スクリーンショットを保存: data/raw/daidata_screenshot.png")

        except Exception as e:
            print(f"エラー: {e}")
            import traceback
            traceback.print_exc()

        finally:
            browser.close()


def test_daidata_machine_page():
    """特定機種ページを試す"""
    print("\n" + "=" * 70)
    print("機種個別ページ探索")
    print("=" * 70)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            # 店舗ページにアクセスして機種リンクを探す
            url = "https://daidata.goraggio.com/100860"
            page.goto(url, wait_until='networkidle', timeout=30000)
            page.wait_for_timeout(3000)

            # 全リンクを取得
            all_links = page.evaluate('''() => {
                return Array.from(document.querySelectorAll('a')).map(a => ({
                    text: a.innerText.trim(),
                    href: a.href
                }));
            }''')

            print(f"総リンク数: {len(all_links)}")

            # SBJ関連のリンク
            for link in all_links:
                if 'ブラックジャック' in link['text']:
                    print(f"  ★ {link['text']}: {link['href']}")

            # 機種IDパターンを探す
            machine_patterns = [l['href'] for l in all_links if '/m/' in l['href'] or '/machine/' in l['href']]
            if machine_patterns:
                print(f"\n機種ページパターン: {machine_patterns[:3]}")

        except Exception as e:
            print(f"エラー: {e}")

        finally:
            browser.close()


if __name__ == "__main__":
    test_daidata()
    test_daidata_machine_page()
