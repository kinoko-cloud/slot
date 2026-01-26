#!/usr/bin/env python3
"""
台データオンライン - 直接台番号指定でデータ取得
過去データと詳細履歴も取得
"""

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import re
import json
from datetime import datetime, timedelta
from pathlib import Path

REMOVE_ADS_SCRIPT = """
() => {
    const adSelectors = ['#gn_interstitial_outer_area', '.yads_ad_item', '[id*="google_ads"]', '[id*="yads"]'];
    adSelectors.forEach(s => document.querySelectorAll(s).forEach(el => el.remove()));
    return 'done';
}
"""

# SBJ台番号設定
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


def get_unit_detail_full(page, hall_id: str, unit_id: str):
    """台の詳細データを完全に取得"""
    url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}"
    print(f"  アクセス: {url}")

    page.goto(url, wait_until='load', timeout=30000)
    page.wait_for_timeout(2000)
    page.evaluate(REMOVE_ADS_SCRIPT)

    text = page.inner_text('body')
    html = page.content()

    print(f"  テキスト長: {len(text)}")
    print(f"  テキストサンプル:\n{text[:1500]}")

    result = {
        'unit_id': unit_id,
        'date': datetime.now().strftime('%Y-%m-%d'),
        'fetched_at': datetime.now().isoformat(),
        'raw_text': text[:3000],
    }

    # 機種名
    machine_match = re.search(r'([ァ-ヶー\w\s]+)\s*\(\d+\.?\d*円', text)
    if machine_match:
        result['machine_name'] = machine_match.group(1).strip()

    # BB, RB, ART
    stats_match = re.search(r'BB\s+RB\s+ART\s+スタート回数\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)', text)
    if stats_match:
        result['bb'] = int(stats_match.group(1))
        result['rb'] = int(stats_match.group(2))
        result['art'] = int(stats_match.group(3))
        result['start'] = int(stats_match.group(4))

    # 最大持ち玉、累計スタート
    max_match = re.search(r'最大持ち玉\s*(\d+)', text)
    total_match = re.search(r'累計スタート\s*(\d+)', text)
    prev_match = re.search(r'前日最終スタート\s*(\d+)', text)
    prob_match = re.search(r'合成確率\s*([\d.]+)', text)

    if max_match:
        result['max_medals'] = int(max_match.group(1))
    if total_match:
        result['total_start'] = int(total_match.group(1))
    if prev_match:
        result['prev_final_start'] = int(prev_match.group(1))
    if prob_match:
        result['combined_prob'] = float(prob_match.group(1))

    # 大当たり履歴（詳細）
    print("\n  【大当たり履歴解析】")
    history = []

    # パターン1: テーブル形式
    # 大当たり	スタート	出玉	種別	時間
    history_section = re.search(r'本日の大当たり履歴詳細[^0-9]*(.+?)(?:過去|スランプ|$)', text, re.DOTALL)
    if history_section:
        section_text = history_section.group(1)
        print(f"    履歴セクション: {section_text[:500]}")

        # 各行を解析
        lines = section_text.split('\n')
        for line in lines:
            # パターン: 数字 数字 数字 (BB|RB|ART) 時間
            match = re.search(r'(\d+)\s+(\d+)\s+(\d+)\s+(BB|RB|ART|AT)\s+(\d+:\d+)', line)
            if match:
                history.append({
                    'hit_num': int(match.group(1)),
                    'start': int(match.group(2)),
                    'medals': int(match.group(3)),
                    'type': match.group(4),
                    'time': match.group(5)
                })

    if history:
        result['history'] = history
        print(f"    履歴件数: {len(history)}")
        for h in history[:5]:
            print(f"      {h}")

    return result


def get_past_data(page, hall_id: str, unit_id: str):
    """過去データを取得（7日分）"""
    print(f"\n  【過去データ取得】台{unit_id}")

    # 過去データページを探す
    base_url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}"
    page.goto(base_url, wait_until='load', timeout=30000)
    page.wait_for_timeout(2000)
    page.evaluate(REMOVE_ADS_SCRIPT)

    # 過去データへのリンクを探す
    html = page.content()
    text = page.inner_text('body')

    print(f"    ページ内容から過去データリンクを探す...")

    # リンクを取得
    links = page.evaluate('''() => {
        return Array.from(document.querySelectorAll('a')).map(a => ({
            text: a.innerText.trim(),
            href: a.href
        })).filter(l => l.text.includes('過去') || l.text.includes('履歴') || l.href.includes('history'));
    }''')

    print(f"    過去データ関連リンク: {links}")

    # 日付タブを探す
    date_links = page.evaluate('''() => {
        return Array.from(document.querySelectorAll('a')).map(a => ({
            text: a.innerText.trim(),
            href: a.href
        })).filter(l => /\\d{1,2}月\\d{1,2}日|\\d{1,2}\\/\\d{1,2}/.test(l.text));
    }''')

    print(f"    日付リンク: {date_links}")

    # スクリーンショット
    page.screenshot(path=f'data/raw/daidata_unit_{unit_id}.png')

    past_data = []

    # 過去データリンクがあればアクセス
    for link in links:
        if 'history' in link['href'].lower() or '過去' in link['text']:
            print(f"    過去データページにアクセス: {link['href']}")
            page.goto(link['href'], wait_until='load', timeout=30000)
            page.wait_for_timeout(2000)
            page.evaluate(REMOVE_ADS_SCRIPT)

            past_text = page.inner_text('body')
            print(f"    過去データテキスト長: {len(past_text)}")
            print(f"    内容サンプル: {past_text[:1000]}")

            page.screenshot(path=f'data/raw/daidata_unit_{unit_id}_history.png')
            break

    return past_data


def main():
    """メイン処理"""
    print("=" * 70)
    print("台データオンライン - SBJ直接取得")
    print("=" * 70)

    shop = SBJ_UNITS['espass_shibuya']
    hall_id = shop['hall_id']
    hall_name = shop['hall_name']

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = context.new_page()

        try:
            # 規約同意
            print(f"\n【{hall_name}】規約同意")
            accept_terms(page, hall_id)

            results = []

            # 各SBJ台のデータを取得
            for unit_id in shop['units']:
                print(f"\n{'='*50}")
                print(f"台{unit_id}のデータ取得")
                print('='*50)

                detail = get_unit_detail_full(page, hall_id, unit_id)
                detail['hall_id'] = hall_id
                detail['hall_name'] = hall_name

                # 過去データも取得
                get_past_data(page, hall_id, unit_id)

                results.append(detail)

            # 保存
            save_path = Path('data/raw') / f'sbj_espass_shibuya_{datetime.now().strftime("%Y%m%d_%H%M")}.json'
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


if __name__ == "__main__":
    main()
