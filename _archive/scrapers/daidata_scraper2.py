#!/usr/bin/env python3
"""
台データオンライン スクレイパー（規約同意対応版）
"""

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import re
import json
from datetime import datetime
from pathlib import Path

# 広告を削除するスクリプト
REMOVE_ADS_SCRIPT = """
() => {
    const adSelectors = [
        '#gn_interstitial_outer_area',
        '.yads_ad_item',
        '[id*="google_ads"]',
        '[id*="yads"]',
        '[class*="ad-"]',
        'iframe[src*="doubleclick"]',
    ];
    adSelectors.forEach(selector => {
        document.querySelectorAll(selector).forEach(el => el.remove());
    });
    document.querySelectorAll('[style*="position: fixed"]').forEach(el => {
        if (el.style.zIndex > 100) el.remove();
    });
    return 'Ads removed';
}
"""


def accept_terms_and_get_data(hall_id: str = "100860"):
    """利用規約に同意してデータを取得"""
    print("=" * 60)
    print("台データオンライン（規約同意対応）")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = context.new_page()

        try:
            # スロット台番号一覧にアクセス
            url = f"https://daidata.goraggio.com/{hall_id}/all_list?ps=S"
            print(f"アクセス: {url}")

            page.goto(url, wait_until='load', timeout=60000)
            page.wait_for_timeout(3000)

            # 広告を削除
            page.evaluate(REMOVE_ADS_SCRIPT)

            # 利用規約同意ボタンを探してクリック
            print("\n【利用規約同意】")
            agree_btn = page.query_selector('text=利用規約に同意する')
            if not agree_btn:
                agree_btn = page.query_selector('button:has-text("同意")')
            if not agree_btn:
                agree_btn = page.query_selector('input[type="submit"]')
            if not agree_btn:
                # すべてのボタンを探す
                buttons = page.query_selector_all('button, input[type="button"], input[type="submit"], a.btn')
                for btn in buttons:
                    text = btn.inner_text() if btn.inner_text() else ''
                    if '同意' in text:
                        agree_btn = btn
                        break

            if agree_btn:
                print("✓ 同意ボタン発見、クリック")
                agree_btn.click()
                page.wait_for_timeout(3000)
            else:
                print("✗ 同意ボタンが見つからない")
                # ページ内のボタンを確認
                all_buttons = page.evaluate('''() => {
                    return Array.from(document.querySelectorAll('button, input[type="button"], input[type="submit"], a')).map(b => ({
                        tag: b.tagName,
                        text: b.innerText?.trim().substring(0, 50) || b.value || '',
                        class: b.className
                    })).filter(b => b.text.length > 0);
                }''')
                print(f"ボタン一覧: {all_buttons[:10]}")

            # 広告を再度削除
            page.evaluate(REMOVE_ADS_SCRIPT)
            page.wait_for_timeout(1000)

            # ページ内容を確認
            text = page.inner_text('body')
            print(f"\nテキスト長: {len(text)}")

            if 'ブラックジャック' in text:
                print("✓ 'ブラックジャック' 発見！")
                idx = text.find('ブラックジャック')
                print(f"コンテキスト: ...{text[max(0,idx-30):idx+100]}...")
            else:
                print("現在のページ内容（先頭500文字）:")
                print(text[:500])

            # スクリーンショット
            page.screenshot(path='data/raw/daidata_after_agree.png')
            print("\n✓ スクリーンショット: data/raw/daidata_after_agree.png")

            # 台リンクを探す
            links = page.evaluate('''() => {
                return Array.from(document.querySelectorAll('a')).map(a => ({
                    text: a.innerText.trim().substring(0, 50),
                    href: a.href
                })).filter(l => l.text.length > 0);
            }''')
            print(f"\n総リンク数: {len(links)}")

            # detailリンク
            detail_links = [l for l in links if '/detail' in l.get('href', '')]
            print(f"台詳細リンク数: {len(detail_links)}")
            for l in detail_links[:10]:
                print(f"  - {l['text']}: {l['href']}")

            return text, links

        except Exception as e:
            print(f"エラー: {e}")
            import traceback
            traceback.print_exc()
            return None, None

        finally:
            browser.close()


def test_with_cookies():
    """Cookieを使って同意済み状態でアクセス"""
    print("\n" + "=" * 60)
    print("Cookie設定でアクセス")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )

        # 同意済みCookieを設定（一般的なパターン）
        context.add_cookies([
            {
                'name': 'terms_agreed',
                'value': '1',
                'domain': 'daidata.goraggio.com',
                'path': '/'
            },
            {
                'name': 'agree',
                'value': 'true',
                'domain': 'daidata.goraggio.com',
                'path': '/'
            }
        ])

        page = context.new_page()

        try:
            url = "https://daidata.goraggio.com/100860/all_list?ps=S"
            print(f"アクセス: {url}")

            page.goto(url, wait_until='load', timeout=60000)
            page.wait_for_timeout(3000)
            page.evaluate(REMOVE_ADS_SCRIPT)

            text = page.inner_text('body')
            print(f"テキスト長: {len(text)}")

            if '規約' in text:
                print("→ まだ規約ページ")
            else:
                print("→ データページの可能性")

            print(f"内容: {text[:300]}")

        except Exception as e:
            print(f"エラー: {e}")

        finally:
            browser.close()


def explore_page_structure():
    """ページ構造を詳しく調査"""
    print("\n" + "=" * 60)
    print("ページ構造調査")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            url = "https://daidata.goraggio.com/100860/all_list?ps=S"
            page.goto(url, wait_until='load', timeout=60000)
            page.wait_for_timeout(2000)

            # HTML全体を取得
            html = page.content()

            # フォームを探す
            soup = BeautifulSoup(html, 'lxml')
            forms = soup.find_all('form')
            print(f"フォーム数: {len(forms)}")

            for i, form in enumerate(forms):
                action = form.get('action', '')
                method = form.get('method', '')
                print(f"  フォーム{i+1}: action={action}, method={method}")

                # ボタンを探す
                buttons = form.find_all(['button', 'input'])
                for btn in buttons:
                    btn_type = btn.get('type', '')
                    btn_value = btn.get('value', btn.get_text(strip=True))
                    print(f"    - {btn.name} type={btn_type} value={btn_value[:30]}")

            # JavaScriptで同意処理を探す
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string and ('agree' in script.string.lower() or '同意' in script.string):
                    print(f"\n同意関連スクリプト発見:")
                    print(script.string[:500])

        except Exception as e:
            print(f"エラー: {e}")

        finally:
            browser.close()


if __name__ == "__main__":
    explore_page_structure()
    accept_terms_and_get_data()
    test_with_cookies()
