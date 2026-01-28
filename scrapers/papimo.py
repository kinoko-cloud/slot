#!/usr/bin/env python3
"""
papimo.jp - 秋葉原アイランド SBJデータ取得
"""

from playwright.sync_api import sync_playwright
import re
import json
from datetime import datetime, timedelta
from pathlib import Path

# 店舗設定
PAPIMO_CONFIG = {
    'island_akihabara': {
        'hall_id': '00031715',
        'hall_name': 'アイランド秋葉原店',
        'sbj_machine_id': '225010000',
        'sbj_units': ['1015', '1016', '1017', '1018', '1020', '1021', '1022', '1023',
                      '1025', '1026', '1027', '1028', '1030', '1031'],
        'hokuto_units': ['0731', '0732', '0733', '0735', '0736', '0737', '0738',
                         '0750', '0751', '0752', '0753', '0755', '0756', '0757'],
    }
}


def get_unit_history(page, hall_id: str, unit_id: str, days_back: int = 14,
                     expected_machine: str = None) -> dict:
    """1台分の履歴を取得（最大14日分）
    
    Args:
        expected_machine: 期待する機種名のキーワード（例: 'ブラックジャック', '北斗'）。
                         ページ上の機種名と照合し、不一致なら警告を出してスキップ。
    """
    result = {
        'unit_id': unit_id,
        'days': []
    }

    # まず台詳細ページにアクセス
    url = f"https://papimo.jp/h/{hall_id}/hit/view/{unit_id}"
    page.goto(url, wait_until='load', timeout=30000)
    page.wait_for_timeout(2000)

    # 機種名バリデーション: ページ上の機種名を取得して照合
    if expected_machine:
        try:
            page_text = page.inner_text('body')
            # papimoのページ上部に機種名が含まれる行を探す
            page_machine = ''
            for line in page_text.split('\n')[:30]:  # ページ上部30行以内
                line = line.strip()
                if len(line) > 3 and ('L' in line or 'ス' in line or '北' in line):
                    page_machine = line
                    break
            if page_machine:
                keywords = expected_machine if isinstance(expected_machine, list) else [expected_machine]
                missing = [kw for kw in keywords if kw not in page_machine]
                if missing:
                    print(f"    ⚠️ 機種不一致! 台{unit_id}: 期待キーワード={keywords}, 実際={page_machine}, 不足={missing}")
                    print(f"    → 台番号が別機種に変わった可能性。スキップします。")
                    result['machine_mismatch'] = True
                    result['actual_machine'] = page_machine
                    return result
        except Exception as e:
            print(f"    機種名確認エラー: {e}")

    # 利用可能な日付を取得
    available_dates = page.evaluate('''() => {
        const select = document.querySelector('#display-date');
        if (!select) return [];
        return Array.from(select.options).map(o => o.value);
    }''')

    if not available_dates:
        print(f"    日付セレクターが見つかりません")
        return result

    # 指定日数分のデータを取得
    for i, date_value in enumerate(available_dates[:days_back]):
        # 日付をYYYY-MM-DD形式に変換
        date_str = f"{date_value[:4]}-{date_value[4:6]}-{date_value[6:8]}"

        print(f"  {date_str}: 取得中...")

        try:
            # 日付を選択
            page.select_option('#display-date', date_value)
            page.wait_for_timeout(1500)

            # 「もっと見る」ボタンをクリックして全履歴を表示
            while True:
                more_btn = page.query_selector('text=もっと見る')
                if more_btn and more_btn.is_visible():
                    more_btn.click()
                    page.wait_for_timeout(500)
                else:
                    break

            # ページテキストから抽出
            text = page.inner_text('body')
            day_data = extract_papimo_day(text, unit_id, date_str)

            if day_data and day_data.get('total_start', 0) > 0:
                result['days'].append(day_data)
                print(f"    ART={day_data.get('art', 0)}, 総スタート={day_data.get('total_start', 0):,}")
            else:
                print(f"    データなし")

        except Exception as e:
            print(f"    エラー: {e}")

    return result


def extract_papimo_day(text: str, unit_id: str, date_str: str) -> dict:
    """papimo.jpの1日分データを抽出"""
    data = {
        'unit_id': unit_id,
        'date': date_str,
    }

    def parse_number(s):
        """カンマ区切りの数値を解析"""
        return int(s.replace(',', ''))

    # BB/RB/ARTの回数（BB回数、RB回数、ART回数の形式）
    bb_match = re.search(r'BB回数\s*(\d+)', text)
    rb_match = re.search(r'RB回数\s*(\d+)', text)
    art_match = re.search(r'ART回数\s*(\d+)', text)

    if bb_match:
        data['bb'] = int(bb_match.group(1))
    if rb_match:
        data['rb'] = int(rb_match.group(1))
    if art_match:
        data['art'] = int(art_match.group(1))

    # 総スタート（カンマ区切り対応）
    total_match = re.search(r'総スタート\s*([\d,]+)', text)
    if total_match:
        data['total_start'] = parse_number(total_match.group(1))

    # 最終スタート
    final_match = re.search(r'最終スタート\s*([\d,]+)', text)
    if final_match:
        data['final_start'] = parse_number(final_match.group(1))

    # ARTゲーム数
    art_games_match = re.search(r'ARTゲーム数\s*([\d,]+)', text)
    if art_games_match:
        data['art_games'] = parse_number(art_games_match.group(1))

    # 最大出メダル
    max_match = re.search(r'最大出メダル\s*([\d,]+)', text)
    if max_match:
        data['max_medals'] = parse_number(max_match.group(1))

    # 合成確率
    prob_match = re.search(r'合成確率\s*1/([\d,]+)', text)
    if prob_match:
        data['combined_prob'] = parse_number(prob_match.group(1))

    # 当たり履歴
    # 形式: 時間 スタート 出メダル ステータス（ART/REG等）
    history = []

    # 大当り履歴セクションを探す
    # パターン: 時間\tスタート\t出メダル\nステータス（改行やタブを含む）
    history_pattern = re.findall(
        r'(\d{1,2}:\d{2})\s+([\d,]+)\s+([\d,]+)\s*\n?\s*(ART|BB|RB|AT|REG)',
        text,
        re.MULTILINE
    )

    for i, match in enumerate(history_pattern):
        history.append({
            'hit_num': i + 1,
            'time': match[0],
            'start': parse_number(match[1]),
            'medals': parse_number(match[2]),
            'type': match[3],
        })

    if history:
        data['history'] = history
        # 履歴から追加統計を計算
        art_starts = [h['start'] for h in history if h['type'] == 'ART']
        if art_starts:
            data['avg_art_start'] = sum(art_starts) / len(art_starts)
            data['max_art_start'] = max(art_starts)

    # --- prob/is_good を必ず計算 ---
    art = data.get('art', 0)
    total_start = data.get('total_start', 0)
    if art > 0 and total_start > 0:
        data['prob'] = round(total_start / art, 1)
        # SBJ: ≤130, 北斗: ≤330（呼び出し元で機種を判断できないので両方のキーを用意）
        data['is_good_sbj'] = data['prob'] <= 130
        data['is_good_hokuto'] = data['prob'] <= 330
    else:
        data['prob'] = 0
        data['is_good_sbj'] = False
        data['is_good_hokuto'] = False

    return data


def scrape_sbj_island(days_back: int = 14) -> list:
    """秋葉原アイランドのSBJ全台データを取得"""
    config = PAPIMO_CONFIG['island_akihabara']
    hall_id = config['hall_id']
    hall_name = config['hall_name']
    units = config['sbj_units']

    print("=" * 70)
    print(f"papimo.jp - {hall_name} SBJデータ取得")
    print(f"対象台: {len(units)}台")
    print(f"取得日数: {days_back}日分")
    print("=" * 70)

    all_results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1280, 'height': 900})

        try:
            for unit_id in units:
                print(f"\n【台{unit_id}】")

                result = get_unit_history(page, hall_id, unit_id, days_back)
                result['hall_id'] = hall_id
                result['hall_name'] = hall_name
                result['machine_name'] = 'Lスーパーブラックジャック'
                result['fetched_at'] = datetime.now().isoformat()

                all_results.append(result)

                # スクリーンショット（最初の台だけ）
                if unit_id == units[0]:
                    screenshot_path = Path('data/raw') / f'papimo_{unit_id}_screenshot.png'
                    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(screenshot_path))
                    print(f"  スクリーンショット: {screenshot_path}")

        except Exception as e:
            print(f"エラー: {e}")
            import traceback
            traceback.print_exc()

        finally:
            browser.close()

    # 保存
    save_path = Path('data/raw') / f'papimo_island_sbj_{datetime.now().strftime("%Y%m%d_%H%M")}.json'
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n✓ 保存: {save_path}")

    return all_results


def main():
    """メイン処理"""
    results = scrape_sbj_island(days_back=7)

    # サマリー表示
    print("\n" + "=" * 70)
    print("取得結果サマリー")
    print("=" * 70)

    for unit in results:
        unit_id = unit.get('unit_id')
        days = unit.get('days', [])

        total_art = sum(d.get('art', 0) for d in days)
        total_games = sum(d.get('total_start', 0) for d in days)

        print(f"台{unit_id}: {len(days)}日分, ART合計={total_art}回, 総G数={total_games:,}G")


if __name__ == "__main__":
    main()


def scrape_island_machine(machine_key: str = 'hokuto', days_back: int = 7) -> list:
    """アイランド秋葉原の指定機種の全台データを取得"""
    config = PAPIMO_CONFIG['island_akihabara']
    hall_id = config['hall_id']
    hall_name = config['hall_name']

    if machine_key in ('hokuto', 'hokuto_tensei2'):
        units = config['hokuto_units']
        machine_name = 'L北斗の拳 転生の章2'
    else:
        units = config['sbj_units']
        machine_name = 'Lスーパーブラックジャック'

    print("=" * 70)
    print(f"papimo.jp - {hall_name} {machine_name} データ取得")
    print(f"対象台: {len(units)}台")
    print(f"取得日数: {days_back}日分")
    print("=" * 70)

    all_results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1280, 'height': 900})

        try:
            for unit_id in units:
                print(f"\n【台{unit_id}】")
                result = get_unit_history(page, hall_id, unit_id, days_back)
                result['hall_id'] = hall_id
                result['hall_name'] = hall_name
                result['machine_name'] = machine_name
                result['fetched_at'] = datetime.now().isoformat()
                all_results.append(result)
        except Exception as e:
            print(f"エラー: {e}")
            import traceback
            traceback.print_exc()
        finally:
            browser.close()

    # 保存
    tag = 'hokuto' if 'hokuto' in machine_key else 'sbj'
    save_path = Path('data/raw') / f'papimo_island_{tag}_{datetime.now().strftime("%Y%m%d_%H%M")}.json'
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n✓ 保存: {save_path}")
    return all_results


if __name__ == '__main__':
    import sys
    machine = sys.argv[1] if len(sys.argv) > 1 else 'sbj'
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    if machine in ('hokuto', 'hokuto_tensei2'):
        scrape_island_machine('hokuto', days)
    else:
        scrape_sbj_island(days)
