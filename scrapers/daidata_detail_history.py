#!/usr/bin/env python3
"""
台データオンライン - 詳細履歴取得
「詳細を見る」リンクをクリックして全当たり履歴を取得
"""

from playwright.sync_api import sync_playwright
import re
import json
from datetime import datetime
from pathlib import Path

REMOVE_ADS_SCRIPT = """
() => {
    document.querySelectorAll('#gn_interstitial_outer_area, .yads_ad_item, [id*="google_ads"]').forEach(el => el.remove());
}
"""


def get_all_history(hall_id: str = "100860", unit_id: str = "3011", hall_name: str = "渋谷エスパス新館",
                    expected_machine: str = None):
    """全日の詳細履歴を取得"""
    print(f"=" * 70)
    print(f"台{unit_id}（{hall_name}）全履歴取得")
    print("=" * 70)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1280, 'height': 900})

        try:
            # 規約同意
            page.goto(f"https://daidata.goraggio.com/{hall_id}/accept", wait_until='load', timeout=60000)
            page.wait_for_timeout(2000)
            page.evaluate(REMOVE_ADS_SCRIPT)
            # 「利用規約に同意する」ボタンをクリック
            try:
                agree_btn = page.locator('button:has-text("利用規約に同意する")')
                if agree_btn.count() > 0:
                    agree_btn.first.click()
                    page.wait_for_timeout(3000)
                else:
                    # フォールバック: formをsubmit
                    page.evaluate('() => { const form = document.querySelector("form"); if (form) form.submit(); }')
                    page.wait_for_timeout(3000)
            except Exception as e:
                print(f"  規約同意の処理でエラー（続行）: {e}")
                page.wait_for_timeout(1000)

            # 台詳細ページ
            url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}"
            print(f"\nアクセス: {url}")
            page.goto(url, wait_until='load', timeout=60000)
            page.wait_for_timeout(3000)
            page.evaluate(REMOVE_ADS_SCRIPT)

            result = {
                'unit_id': unit_id,
                'hall_id': hall_id,
                'hall_name': hall_name,
                'fetched_at': datetime.now().isoformat(),
                'days': []
            }

            # 機種名取得＋バリデーション
            text = page.inner_text('body')
            machine_match = re.search(r'(L[ｱ-ﾝァ-ヶー\w]+)\s*\(', text)
            if machine_match:
                result['machine_name'] = machine_match.group(1)
                print(f"機種: {result['machine_name']}")
                # 期待する機種名と照合
                if expected_machine and expected_machine not in result['machine_name']:
                    print(f"  ⚠️ 機種不一致! 台{unit_id}: 期待={expected_machine}, 実際={result['machine_name']}")
                    print(f"  → 台番号が別機種に変わった可能性。スキップします。")
                    result['machine_mismatch'] = True
                    browser.close()
                    return result

            # 0. 概要ページから日別サマリー（最大持ち玉・累計スタート）をパース
            overview_by_date = _parse_overview_summary(text)
            if overview_by_date:
                print(f"概要データ: {len(overview_by_date)}日分")

            # 1. 「詳細を見る」リンクを全て取得
            print("\n【詳細を見るリンクを探す】")
            detail_links = page.evaluate('''() => {
                const links = [];
                document.querySelectorAll('a').forEach(a => {
                    if (a.innerText.includes('詳細を見る')) {
                        links.push({
                            text: a.innerText.trim(),
                            href: a.href,
                        });
                    }
                });
                return links;
            }''')

            print(f"詳細リンク数: {len(detail_links)}")
            for link in detail_links:
                print(f"  - {link['href']}")

            # 2. 各詳細ページにアクセスして履歴を取得
            for i, link in enumerate(detail_links):
                print(f"\n【{i+1}/{len(detail_links)}】{link['href']}")

                page.goto(link['href'], wait_until='load', timeout=60000)
                page.wait_for_timeout(2000)
                page.evaluate(REMOVE_ADS_SCRIPT)

                # 日付と履歴を取得
                text = page.inner_text('body')
                day_data = extract_day_history(text, unit_id)

                if day_data:
                    # 概要ページの最大持ち玉・累計スタートをマージ
                    date_key = day_data.get('date', '')
                    if date_key in overview_by_date:
                        ov = overview_by_date[date_key]
                        if ov.get('max_medals_day') and not day_data.get('max_medals_day'):
                            day_data['max_medals_day'] = ov['max_medals_day']
                        if ov.get('total_start') and not day_data.get('total_start'):
                            day_data['total_start'] = ov['total_start']
                    result['days'].append(day_data)
                    print(f"  日付: {day_data.get('date')} 最大持玉={day_data.get('max_medals_day', '-')}")
                    print(f"  サマリー: BB={day_data.get('bb', 0)}, RB={day_data.get('rb', 0)}, ART={day_data.get('art', 0)}")
                    print(f"  履歴件数: {len(day_data.get('history', []))}")

                    # 履歴の最初の数件を表示
                    for h in day_data.get('history', [])[:3]:
                        print(f"    {h}")

                # スクリーンショット（最初の日だけ）
                if i == 0:
                    page.screenshot(path=f'data/raw/daidata_{unit_id}_detail_{i}.png')

            # 保存
            save_path = Path('data/raw') / f'sbj_{unit_id}_history_{datetime.now().strftime("%Y%m%d_%H%M")}.json'
            save_path.parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"\n✓ 保存: {save_path}")

            return result

        except Exception as e:
            print(f"エラー: {e}")
            import traceback
            traceback.print_exc()
            return None

        finally:
            browser.close()


def _parse_overview_summary(text: str) -> dict:
    """概要ページのテキストから日別サマリーをパース
    
    テキスト例:
      1月27日 （詳細を見る） 最終更新時間 2026.01.27 23:43
      BB	RB	ART	スタート回数
      0	23	129	0
      最大持ち玉	15785	累計スタート	4612
    
    Returns:
        {date_str: {'max_medals_day': int, 'total_start': int, 'bb': int, 'rb': int, 'art': int}}
    """
    result = {}
    year = datetime.now().year
    
    # 日付セクションごとに分割
    # パターン: X月Y日 （詳細を見る）
    sections = re.split(r'(\d{1,2})月(\d{1,2})日\s*(?:（詳細を見る）)?', text)
    
    for i in range(1, len(sections) - 2, 3):
        month = int(sections[i])
        day = int(sections[i + 1])
        section_text = sections[i + 2]
        
        # 前年判定
        current_month = datetime.now().month
        y = year if month <= current_month else year - 1
        date_str = f"{y}-{month:02d}-{day:02d}"
        
        entry = {}
        
        # 最大持ち玉
        max_match = re.search(r'最大持ち玉\s*(\d+)', section_text)
        if max_match:
            entry['max_medals_day'] = int(max_match.group(1))
        
        # 累計スタート
        total_match = re.search(r'累計スタート\s*(\d+)', section_text)
        if total_match:
            entry['total_start'] = int(total_match.group(1))
        
        # BB/RB/ART/スタート回数
        summary_match = re.search(r'BB\s+RB\s+ART\s+スタート回数\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)', section_text)
        if summary_match:
            entry['bb'] = int(summary_match.group(1))
            entry['rb'] = int(summary_match.group(2))
            entry['art'] = int(summary_match.group(3))
        
        if entry:
            result[date_str] = entry
    
    return result


def extract_day_history(text: str, unit_id: str) -> dict:
    """ページテキストから日付と履歴を抽出"""
    data = {
        'unit_id': unit_id,
    }

    # 日付を探す（複数パターン）
    # パターン1: 1月25日
    date_match = re.search(r'(\d{1,2})月(\d{1,2})日', text)
    if date_match:
        month, day = date_match.groups()
        year = datetime.now().year
        # 1月なのに12月のデータの場合は前年
        current_month = datetime.now().month
        if int(month) > current_month:
            year -= 1
        data['date'] = f"{year}-{int(month):02d}-{int(day):02d}"

    # サマリーデータ
    # BB	RB	ART	スタート回数
    # 0	2	9	153
    summary_pattern = re.search(r'BB\s+RB\s+ART\s+スタート回数\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)', text)
    if summary_pattern:
        data['bb'] = int(summary_pattern.group(1))
        data['rb'] = int(summary_pattern.group(2))
        data['art'] = int(summary_pattern.group(3))
        data['final_start'] = int(summary_pattern.group(4))

    # 最大持ち玉、累計スタート
    max_match = re.search(r'最大持ち玉\s*(\d+)', text)
    total_match = re.search(r'累計スタート\s*(\d+)', text)

    if max_match:
        data['max_medals'] = int(max_match.group(1))
    if total_match:
        data['total_start'] = int(total_match.group(1))

    # 当たり履歴
    # パターン: 大当たり スタート 出玉 種別 時間
    # 例: 9 970 105 ART 23:47
    history = []

    # 履歴セクションを探す
    history_section_match = re.search(r'本日の大当たり履歴詳細.*?大当たり\s+スタート\s+出玉\s+種別\s+時間(.+?)(?:過去|ページ|$)', text, re.DOTALL)
    if history_section_match:
        section = history_section_match.group(1)

        # 各行を解析
        # 数字 数字 数字 (ART|BB|RB) 時間
        matches = re.findall(r'(\d+)\s+(\d+)\s+(\d+)\s+(ART|BB|RB|AT)\s+(\d{1,2}:\d{2})', section)
        for match in matches:
            history.append({
                'hit_num': int(match[0]),
                'start': int(match[1]),      # この当たりまでの回転数（重要！）
                'medals': int(match[2]),     # 獲得枚数
                'type': match[3],            # ART/BB/RB
                'time': match[4],            # 時間
            })

    if history:
        data['history'] = history

        # 統計を計算
        art_starts = [h['start'] for h in history if h['type'] == 'ART']
        if art_starts:
            data['avg_art_start'] = sum(art_starts) / len(art_starts)
            data['max_art_start'] = max(art_starts)

    # --- prob/is_good を必ず計算 ---
    art = data.get('art', 0)
    total_start = data.get('total_start', 0)
    if art > 0 and total_start > 0:
        data['prob'] = round(total_start / art, 1)
        data['is_good_sbj'] = data['prob'] <= 130
        data['is_good_hokuto'] = data['prob'] <= 330
    else:
        data['prob'] = 0
        data['is_good_sbj'] = False
        data['is_good_hokuto'] = False

    return data


def main():
    """メイン：3台分の履歴を取得"""
    units = ['3011', '3012', '3013']
    all_results = []

    for unit_id in units:
        result = get_all_history(unit_id=unit_id)
        if result:
            all_results.append(result)

    # 全台分を保存
    save_path = Path('data/raw') / f'sbj_all_history_{datetime.now().strftime("%Y%m%d_%H%M")}.json'
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n✓ 全台保存: {save_path}")


if __name__ == "__main__":
    main()
