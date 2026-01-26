#!/usr/bin/env python3
"""
Playwrightで台データオンラインをスクレイピング（改良版）
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
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = context.new_page()

        try:
            url = "https://daidata.goraggio.com/100860"
            print(f"アクセス中: {url}")

            # networkidleではなくloadで待機（高速化）
            page.goto(url, wait_until='load', timeout=60000)

            # 追加で少し待機
            page.wait_for_timeout(5000)

            print(f"タイトル: {page.title()}")

            # ページのテキスト
            text = page.inner_text('body')
            print(f"テキスト長: {len(text)} chars")

            # ブラックジャック検索
            if 'ブラックジャック' in text:
                print("✓ 'ブラックジャック' 発見！")
                idx = text.find('ブラックジャック')
                print(f"  コンテキスト: ...{text[max(0,idx-30):idx+80]}...")
            else:
                print("✗ 'ブラックジャック' 未発見")

            # 全リンクを取得
            links = page.evaluate('''() => {
                return Array.from(document.querySelectorAll('a')).map(a => ({
                    text: a.innerText.trim().substring(0, 50),
                    href: a.href
                })).filter(l => l.text.length > 0);
            }''')
            print(f"\n総リンク数: {len(links)}")

            # SBJ関連リンク
            sbj_links = [l for l in links if 'ブラックジャック' in l['text']]
            if sbj_links:
                print("★ SBJリンク:")
                for l in sbj_links:
                    print(f"  - {l['text']}: {l['href']}")

            # 機種っぽいリンク（最初の10個）
            print("\n機種リンク例:")
            for l in links[:15]:
                print(f"  - {l['text'][:30]}: {l['href'][:60]}")

            # クリック可能な要素を探す
            print("\n【スロットタブを探す】")
            slot_elements = page.query_selector_all('text=スロット')
            print(f"'スロット'要素: {len(slot_elements)}個")

            if slot_elements:
                # スロットをクリック
                slot_elements[0].click()
                page.wait_for_timeout(3000)

                text2 = page.inner_text('body')
                if 'ブラックジャック' in text2:
                    print("✓ スロット切替後、'ブラックジャック' 発見！")
                    idx = text2.find('ブラックジャック')
                    print(f"  コンテキスト: ...{text2[max(0,idx-30):idx+100]}...")

            # 機種別で探すをクリック
            print("\n【機種別で探す】")
            machine_btn = page.query_selector('text=機種別で探す')
            if machine_btn:
                machine_btn.click()
                page.wait_for_timeout(3000)

                text3 = page.inner_text('body')
                if 'ブラックジャック' in text3:
                    print("✓ '機種別で探す'後、'ブラックジャック' 発見！")

            # スクリーンショット
            page.screenshot(path='data/raw/daidata_screenshot.png')
            print("\n✓ スクリーンショット保存: data/raw/daidata_screenshot.png")

            return True

        except Exception as e:
            print(f"エラー: {e}")
            import traceback
            traceback.print_exc()
            return False

        finally:
            browser.close()


def explore_sbj_page():
    """SBJの詳細ページを探す"""
    print("\n" + "=" * 70)
    print("SBJ詳細ページ探索")
    print("=" * 70)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            url = "https://daidata.goraggio.com/100860"
            page.goto(url, wait_until='load', timeout=60000)
            page.wait_for_timeout(3000)

            # スロットタブをクリック
            slot_tab = page.query_selector('text=スロット')
            if slot_tab:
                slot_tab.click()
                page.wait_for_timeout(2000)

            # 機種別で探すをクリック
            machine_btn = page.query_selector('text=機種別で探す')
            if machine_btn:
                machine_btn.click()
                page.wait_for_timeout(2000)

            # ページ内容を取得
            html = page.content()

            # ブラックジャックへのリンクを探す
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'lxml')

            links = soup.find_all('a', href=True)
            for link in links:
                text = link.get_text(strip=True)
                if 'ブラックジャック' in text:
                    href = link.get('href')
                    print(f"★ {text}: {href}")

                    # そのリンクにアクセス
                    if href and not href.startswith('javascript'):
                        full_url = href if href.startswith('http') else f"https://daidata.goraggio.com{href}"
                        print(f"  アクセス: {full_url}")

                        page.goto(full_url, wait_until='load', timeout=30000)
                        page.wait_for_timeout(3000)

                        # 台データを取得
                        detail_text = page.inner_text('body')
                        print(f"  テキスト長: {len(detail_text)}")

                        # BB, RB, ART などを探す
                        if 'BB' in detail_text or 'ART' in detail_text:
                            print("  ✓ BB/ART データあり")
                            # 数字データを探す
                            numbers = re.findall(r'(\d+)回', detail_text)
                            if numbers:
                                print(f"  回数データ: {numbers[:10]}")

                        # スクリーンショット
                        page.screenshot(path='data/raw/daidata_sbj_detail.png')
                        print("  ✓ スクリーンショット: data/raw/daidata_sbj_detail.png")
                        break

        except Exception as e:
            print(f"エラー: {e}")
            import traceback
            traceback.print_exc()

        finally:
            browser.close()


if __name__ == "__main__":
    test_daidata()
    explore_sbj_page()
