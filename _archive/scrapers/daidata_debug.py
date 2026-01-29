#!/usr/bin/env python3
"""
台データオンライン - UI構造デバッグ
リスト表示ボタンとスライドの動作を詳しく調査
"""

from playwright.sync_api import sync_playwright
import re
from pathlib import Path

REMOVE_ADS_SCRIPT = """
() => {
    document.querySelectorAll('#gn_interstitial_outer_area, .yads_ad_item, [id*="google_ads"]').forEach(el => el.remove());
}
"""


def debug_ui():
    """UIの詳細調査"""
    print("=" * 70)
    print("台データオンライン UI調査")
    print("=" * 70)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1280, 'height': 900})

        try:
            # 規約同意
            hall_id = "100860"
            unit_id = "3011"

            page.goto(f"https://daidata.goraggio.com/{hall_id}/all_list?ps=S", wait_until='load', timeout=60000)
            page.wait_for_timeout(2000)
            page.evaluate(REMOVE_ADS_SCRIPT)
            page.evaluate('() => { const form = document.querySelector("form"); if (form) form.submit(); }')
            page.wait_for_timeout(3000)

            # 台詳細ページ
            url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}"
            print(f"\nアクセス: {url}")
            page.goto(url, wait_until='load', timeout=60000)
            page.wait_for_timeout(3000)
            page.evaluate(REMOVE_ADS_SCRIPT)

            # 1. 全体のHTML構造を確認
            print("\n【1. 初期状態のボタン/インタラクティブ要素】")
            elements = page.evaluate('''() => {
                const results = [];
                document.querySelectorAll('button, a, input, [onclick], [class*="btn"], [class*="tab"], [class*="list"], [class*="slide"]').forEach(el => {
                    if (el.offsetParent !== null) {  // 表示されている要素のみ
                        results.push({
                            tag: el.tagName,
                            text: (el.innerText || el.value || '').trim().substring(0, 40),
                            class: el.className,
                            id: el.id,
                            onclick: el.onclick ? 'yes' : 'no'
                        });
                    }
                });
                return results;
            }''')

            for el in elements[:30]:
                if el['text'] or el['class']:
                    print(f"  {el['tag']}: '{el['text']}' class={el['class'][:50]}")

            # 2. リスト表示関連を探す
            print("\n【2. リスト表示関連要素】")
            list_elements = page.evaluate('''() => {
                const results = [];
                document.querySelectorAll('*').forEach(el => {
                    const text = (el.innerText || '').toLowerCase();
                    const cls = (el.className || '').toLowerCase();
                    if (text.includes('リスト') || cls.includes('list') || text.includes('履歴')) {
                        if (el.offsetParent !== null && el.innerText.length < 100) {
                            results.push({
                                tag: el.tagName,
                                text: el.innerText.trim().substring(0, 50),
                                class: el.className,
                                clickable: (el.tagName === 'A' || el.tagName === 'BUTTON' || el.onclick !== null)
                            });
                        }
                    }
                });
                return results;
            }''')

            for el in list_elements[:15]:
                print(f"  {el['tag']}: '{el['text']}' clickable={el['clickable']}")

            # 3. リスト表示をクリック
            print("\n【3. リスト表示クリック】")
            clicked = page.evaluate('''() => {
                const elements = document.querySelectorAll('*');
                for (const el of elements) {
                    if (el.innerText && el.innerText.includes('リスト表示') && el.innerText.length < 20) {
                        el.click();
                        return 'clicked: ' + el.tagName + ' - ' + el.className;
                    }
                }
                return 'not found';
            }''')
            print(f"  結果: {clicked}")
            page.wait_for_timeout(2000)

            # 4. クリック後の状態
            print("\n【4. クリック後のテーブル/履歴データ】")
            tables = page.evaluate('''() => {
                const results = [];
                document.querySelectorAll('table, [class*="table"], [class*="history"], [class*="data"]').forEach(el => {
                    if (el.offsetParent !== null) {
                        results.push({
                            tag: el.tagName,
                            class: el.className,
                            rows: el.querySelectorAll('tr').length,
                            text: el.innerText.substring(0, 200)
                        });
                    }
                });
                return results;
            }''')

            for t in tables[:5]:
                print(f"  {t['tag']} class={t['class'][:40]} rows={t['rows']}")
                print(f"    内容: {t['text'][:100]}...")

            # 5. 大当たり履歴の詳細セクションを探す
            print("\n【5. 大当たり履歴セクション】")
            text = page.inner_text('body')

            # 「本日の大当たり履歴詳細」セクションを探す
            if '本日の大当たり履歴' in text:
                idx = text.find('本日の大当たり履歴')
                section = text[idx:idx+1000]
                print(f"  発見！内容:\n{section}")
            else:
                print("  「本日の大当たり履歴」セクションなし")

            # 6. スライダー/日付切り替えを探す
            print("\n【6. 日付切り替え要素】")
            date_nav = page.evaluate('''() => {
                const results = [];
                document.querySelectorAll('*').forEach(el => {
                    const text = el.innerText || '';
                    // 日付パターン: 1/25, 1月25日, 前日, 翌日
                    if (/\\d{1,2}[月\\/]\\d{1,2}|前日|翌日|過去/.test(text) && text.length < 30) {
                        if (el.offsetParent !== null) {
                            results.push({
                                tag: el.tagName,
                                text: text.trim(),
                                class: el.className,
                            });
                        }
                    }
                });
                return results;
            }''')

            for el in date_nav[:15]:
                print(f"  {el['tag']}: '{el['text']}' class={el['class'][:30]}")

            # 7. スクリーンショット
            page.screenshot(path='data/raw/daidata_debug.png', full_page=True)
            print("\n✓ フルページスクリーンショット: data/raw/daidata_debug.png")

            # 8. HTMLを保存（デバッグ用）
            html = page.content()
            Path('data/raw/daidata_debug.html').write_text(html, encoding='utf-8')
            print("✓ HTML保存: data/raw/daidata_debug.html")

        except Exception as e:
            print(f"エラー: {e}")
            import traceback
            traceback.print_exc()

        finally:
            browser.close()


if __name__ == "__main__":
    debug_ui()
