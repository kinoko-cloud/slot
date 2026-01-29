#!/usr/bin/env python3
"""
台データオンライン - 完全版スクレイパー
- リスト表示ボタンで全当たり履歴取得
- スライドで過去日付も取得
"""

from playwright.sync_api import sync_playwright
import re
import json
from datetime import datetime
from pathlib import Path

REMOVE_ADS_SCRIPT = """
() => {
    const adSelectors = ['#gn_interstitial_outer_area', '.yads_ad_item', '[id*="google_ads"]', '[id*="yads"]'];
    adSelectors.forEach(s => document.querySelectorAll(s).forEach(el => el.remove()));
    return 'done';
}
"""

SBJ_UNITS = {
    'espass_shibuya': {
        'hall_id': '100860',
        'hall_name': '渋谷エスパス新館',
        'units': ['3011', '3012', '3013']
    }
}


def accept_terms(page, hall_id):
    """規約同意"""
    page.goto(f"https://daidata.goraggio.com/{hall_id}/all_list?ps=S", wait_until='load', timeout=60000)
    page.wait_for_timeout(2000)
    page.evaluate(REMOVE_ADS_SCRIPT)
    page.evaluate('() => { const form = document.querySelector("form"); if (form) form.submit(); }')
    page.wait_for_timeout(3000)


def get_full_history(page, hall_id: str, unit_id: str):
    """台の全履歴データを取得（リスト表示 + スライド）"""
    url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}"
    print(f"\n  【台{unit_id}】{url}")

    page.goto(url, wait_until='load', timeout=60000)
    page.wait_for_timeout(3000)
    page.evaluate(REMOVE_ADS_SCRIPT)

    result = {
        'unit_id': unit_id,
        'hall_id': hall_id,
        'fetched_at': datetime.now().isoformat(),
        'days': []
    }

    # 機種名を取得
    text = page.inner_text('body')
    machine_match = re.search(r'(L[ｱ-ﾝァ-ヶー\w]+)\s*\(', text)
    if machine_match:
        result['machine_name'] = machine_match.group(1)
        print(f"    機種: {result['machine_name']}")

    # 1. リスト表示ボタンを探してクリック
    print("    リスト表示ボタンを探す...")

    # ボタンを探す複数のパターン
    list_btn = page.query_selector('text=リスト表示')
    if not list_btn:
        list_btn = page.query_selector('button:has-text("リスト")')
    if not list_btn:
        list_btn = page.query_selector('[class*="list"]')
    if not list_btn:
        # すべてのボタンを確認
        buttons = page.evaluate('''() => {
            return Array.from(document.querySelectorAll('button, a, div[onclick], span[onclick]')).map(b => ({
                text: b.innerText?.trim().substring(0, 30) || '',
                class: b.className || '',
                tag: b.tagName
            })).filter(b => b.text.length > 0);
        }''')
        print(f"    ボタン一覧: {buttons[:15]}")

    if list_btn:
        print("    ✓ リスト表示ボタン発見、クリック")
        list_btn.click()
        page.wait_for_timeout(2000)
        page.evaluate(REMOVE_ADS_SCRIPT)

    # 2. 当日の履歴を取得
    print("    当日の履歴を取得...")
    today_data = extract_day_data(page, unit_id)
    if today_data:
        result['days'].append(today_data)
        print(f"    → 当日: {len(today_data.get('history', []))}件の履歴")

    # 3. 過去日付へのスライド/ボタンを探す
    print("    過去日付を探す...")

    # 日付ナビゲーションを探す
    date_elements = page.evaluate('''() => {
        // 日付っぽいテキストを持つクリック可能な要素を探す
        const elements = [];
        document.querySelectorAll('a, button, div, span').forEach(el => {
            const text = el.innerText?.trim() || '';
            // 1/25, 1月25日, などのパターン
            if (/\\d{1,2}[月\\/]\\d{1,2}/.test(text) && text.length < 20) {
                elements.push({
                    text: text,
                    tag: el.tagName,
                    class: el.className,
                    clickable: el.onclick !== null || el.tagName === 'A' || el.tagName === 'BUTTON'
                });
            }
        });
        return elements;
    }''')
    print(f"    日付要素: {date_elements[:10]}")

    # 左矢印/前日ボタンを探す
    prev_buttons = page.query_selector_all('text=前日, text=◀, text=<, [class*="prev"], [class*="left"]')
    print(f"    前日ボタン候補: {len(prev_buttons)}個")

    # スライダーを探す
    sliders = page.query_selector_all('input[type="range"], [class*="slider"], [class*="swipe"]')
    print(f"    スライダー候補: {len(sliders)}個")

    # 4. 過去7日分のデータを取得
    for i in range(7):
        # 前日ボタンをクリック
        prev_btn = page.query_selector('text=前日')
        if not prev_btn:
            prev_btn = page.query_selector('[class*="prev"]')
        if not prev_btn:
            # 左矢印アイコン
            prev_btn = page.query_selector('text=◀')

        if prev_btn:
            print(f"    前日ボタンクリック ({i+1}日前)")
            try:
                prev_btn.click()
                page.wait_for_timeout(1500)
                page.evaluate(REMOVE_ADS_SCRIPT)

                day_data = extract_day_data(page, unit_id)
                if day_data and day_data.get('date') not in [d.get('date') for d in result['days']]:
                    result['days'].append(day_data)
                    print(f"    → {day_data.get('date')}: {len(day_data.get('history', []))}件")
            except Exception as e:
                print(f"    エラー: {e}")
                break
        else:
            print("    前日ボタンが見つからない")
            break

    # スクリーンショット
    page.screenshot(path=f'data/raw/daidata_{unit_id}_full.png')

    return result


def extract_day_data(page, unit_id: str) -> dict:
    """現在表示されている日のデータを抽出"""
    text = page.inner_text('body')

    data = {
        'unit_id': unit_id,
        'extracted_at': datetime.now().isoformat(),
    }

    # 日付を探す
    date_match = re.search(r'(\d{1,2})月(\d{1,2})日', text)
    if date_match:
        month, day = date_match.groups()
        year = datetime.now().year
        data['date'] = f"{year}-{int(month):02d}-{int(day):02d}"

    # サマリーデータ
    # BB RB ART スタート回数
    summary_match = re.search(r'BB\s+RB\s+ART\s+スタート回数\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)', text)
    if summary_match:
        data['bb'] = int(summary_match.group(1))
        data['rb'] = int(summary_match.group(2))
        data['art'] = int(summary_match.group(3))
        data['start'] = int(summary_match.group(4))

    # 最大持ち玉、累計スタート
    max_match = re.search(r'最大持ち玉\s*(\d+)', text)
    total_match = re.search(r'累計スタート\s*(\d+)', text)

    if max_match:
        data['max_medals'] = int(max_match.group(1))
    if total_match:
        data['total_start'] = int(total_match.group(1))

    # 当たり履歴を抽出
    history = []

    # パターン1: 大当たり スタート 出玉 種別 時間
    # 例: 1 0 137 BB 10:26
    history_matches = re.findall(r'(\d+)\s+(\d+)\s+(\d+)\s+(BB|RB|ART|AT)\s+(\d{1,2}:\d{2})', text)
    for match in history_matches:
        history.append({
            'hit_num': int(match[0]),
            'start': int(match[1]),  # この当たりまでの回転数
            'medals': int(match[2]),
            'type': match[3],
            'time': match[4]
        })

    # パターン2: もし上記で取れなかった場合、別のパターンを試す
    if not history:
        # より緩いパターン
        lines = text.split('\n')
        for line in lines:
            match = re.search(r'(\d+)\s+(\d+)\s+(\d+)\s+(BB|RB|ART)', line)
            if match:
                history.append({
                    'hit_num': int(match.group(1)),
                    'start': int(match.group(2)),
                    'medals': int(match.group(3)),
                    'type': match.group(4),
                })

    if history:
        data['history'] = history

    return data


def main():
    """メイン処理"""
    print("=" * 70)
    print("台データオンライン - 完全版（リスト表示 + 過去日付）")
    print("=" * 70)

    shop = SBJ_UNITS['espass_shibuya']
    hall_id = shop['hall_id']
    hall_name = shop['hall_name']

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1280, 'height': 900},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = context.new_page()

        try:
            # 規約同意
            print(f"\n【{hall_name}】規約同意")
            accept_terms(page, hall_id)

            all_results = []

            # 各SBJ台のデータを取得
            for unit_id in shop['units']:
                print(f"\n{'='*60}")
                print(f"台{unit_id} フルデータ取得")
                print('='*60)

                result = get_full_history(page, hall_id, unit_id)
                result['hall_name'] = hall_name
                all_results.append(result)

                # 概要を表示
                print(f"\n    【結果サマリー】")
                print(f"    機種: {result.get('machine_name', '不明')}")
                print(f"    取得日数: {len(result.get('days', []))}")
                for day in result.get('days', []):
                    hist_count = len(day.get('history', []))
                    print(f"      {day.get('date')}: ART={day.get('art', 0)}, 履歴{hist_count}件")

            # 保存
            save_path = Path('data/raw') / f'sbj_full_{datetime.now().strftime("%Y%m%d_%H%M")}.json'
            save_path.parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, 'w', encoding='utf-8') as f:
                json.dump(all_results, f, ensure_ascii=False, indent=2)
            print(f"\n✓ 保存: {save_path}")

            return all_results

        except Exception as e:
            print(f"エラー: {e}")
            import traceback
            traceback.print_exc()
            return None

        finally:
            browser.close()


if __name__ == "__main__":
    main()
