#!/usr/bin/env python3
"""
アイランド秋葉原 - SBJ全14台 + 北斗全16台の過去履歴データ取得
papimoスクレイパーを使って最大14日分の過去データを取得し、
既存のdailyファイルを更新する
"""

import sys
import json
from datetime import datetime
from pathlib import Path

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.papimo import get_unit_history
from playwright.sync_api import sync_playwright

# 設定
HALL_ID = '00031715'
HALL_NAME = 'アイランド秋葉原店'
DAYS_BACK = 14

# SBJ台番号
SBJ_UNITS = ['1015', '1016', '1017', '1018', '1020', '1021', '1022', '1023',
             '1025', '1026', '1027', '1028', '1030', '1031']

# 北斗台番号
HOKUTO_UNITS = [f'{i:04d}' for i in range(731, 739)] + [f'{i:04d}' for i in range(750, 758)]

DAILY_DIR = PROJECT_ROOT / 'data' / 'daily'


def fetch_all_units(page, unit_ids: list, machine_name: str, machine_key: str) -> list:
    """指定台番号リストの全台データを取得"""
    results = []
    total = len(unit_ids)

    for idx, unit_id in enumerate(unit_ids, 1):
        print(f"\n{'='*60}")
        print(f"[{idx}/{total}] 台{unit_id} ({machine_name})")
        print(f"{'='*60}")

        try:
            result = get_unit_history(page, HALL_ID, unit_id, days_back=DAYS_BACK)
            days_count = len(result.get('days', []))
            print(f"  → {days_count}日分のデータ取得完了")
            results.append(result)
        except Exception as e:
            print(f"  → エラー: {e}")
            results.append({
                'unit_id': unit_id,
                'days': [],
                'error': str(e)
            })

    return results


def update_daily_file(filepath: str, store_key: str, units_data: list, machine_key: str, machine_name: str):
    """既存のdailyファイルのisland_akihabara部分を更新"""
    filepath = Path(filepath)
    if not filepath.exists():
        print(f"  ファイルが見つかりません: {filepath}")
        return

    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # store_keyが存在すれば更新、なければ追加
    data['stores'][store_key] = {
        'hall_name': HALL_NAME,
        'machine_key': machine_key,
        'machine_name': machine_name,
        'units': units_data,
    }

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"  ✓ 更新: {filepath} ({store_key}: {len(units_data)}台)")


def main():
    print("=" * 70)
    print(f"アイランド秋葉原 全台過去データ取得")
    print(f"SBJ: {len(SBJ_UNITS)}台, 北斗: {len(HOKUTO_UNITS)}台")
    print(f"取得日数: {DAYS_BACK}日分")
    print(f"開始時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    all_sbj = []
    all_hokuto = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox']
        )
        page = browser.new_page(viewport={'width': 1280, 'height': 900})

        try:
            # === SBJ全14台 ===
            print("\n" + "★" * 35)
            print("★ SBJ (Lスーパーブラックジャック) 全14台")
            print("★" * 35)
            all_sbj = fetch_all_units(page, SBJ_UNITS, 'Lスーパーブラックジャック', 'sbj')

            # === 北斗全16台 ===
            print("\n" + "★" * 35)
            print("★ 北斗の拳 転生2 全16台")
            print("★" * 35)
            all_hokuto = fetch_all_units(page, HOKUTO_UNITS, 'L北斗の拳 転生の章2', 'hokuto_tensei2')

        except Exception as e:
            print(f"\n致命的エラー: {e}")
            import traceback
            traceback.print_exc()
        finally:
            browser.close()

    # === 生データ保存 ===
    timestamp = datetime.now().strftime('%Y%m%d_%H%M')
    raw_dir = PROJECT_ROOT / 'data' / 'raw'
    raw_dir.mkdir(parents=True, exist_ok=True)

    raw_path = raw_dir / f'island_akiba_all_{timestamp}.json'
    with open(raw_path, 'w', encoding='utf-8') as f:
        json.dump({
            'hall_id': HALL_ID,
            'hall_name': HALL_NAME,
            'fetched_at': datetime.now().isoformat(),
            'sbj': all_sbj,
            'hokuto': all_hokuto,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n✓ 生データ保存: {raw_path}")

    # === 既存dailyファイル更新 ===
    print("\n" + "=" * 70)
    print("既存dailyファイル更新")
    print("=" * 70)

    # daily_sbj_20260126.json - island_akihabara_sbj を更新
    sbj_daily = DAILY_DIR / 'daily_sbj_20260126.json'
    update_daily_file(sbj_daily, 'island_akihabara_sbj', all_sbj, 'sbj', 'Lスーパーブラックジャック')

    # daily_sbj_hokuto_tensei2_20260126.json - island_akihabara_sbj と island_akihabara_hokuto_tensei2 を更新
    hokuto_daily = DAILY_DIR / 'daily_sbj_hokuto_tensei2_20260126.json'
    update_daily_file(hokuto_daily, 'island_akihabara_sbj', all_sbj, 'sbj', 'Lスーパーブラックジャック')
    update_daily_file(hokuto_daily, 'island_akihabara_hokuto_tensei2', all_hokuto, 'hokuto_tensei2', 'L北斗の拳 転生の章2')

    # === サマリー ===
    print("\n" + "=" * 70)
    print("取得結果サマリー")
    print("=" * 70)

    print("\n【SBJ】")
    for unit in all_sbj:
        uid = unit.get('unit_id', '?')
        days = unit.get('days', [])
        total_art = sum(d.get('art', 0) for d in days)
        total_games = sum(d.get('total_start', 0) for d in days)
        print(f"  台{uid}: {len(days)}日分, ART合計={total_art}回, 総G数={total_games:,}G")

    print("\n【北斗】")
    for unit in all_hokuto:
        uid = unit.get('unit_id', '?')
        days = unit.get('days', [])
        total_art = sum(d.get('art', 0) for d in days)
        total_games = sum(d.get('total_start', 0) for d in days)
        print(f"  台{uid}: {len(days)}日分, ART合計={total_art}回, 総G数={total_games:,}G")

    print(f"\n完了: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == '__main__':
    main()
