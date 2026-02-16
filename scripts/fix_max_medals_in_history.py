#!/usr/bin/env python3
"""蓄積DB（data/history/）のmax_medalsを正しい値に修正

問題: 蓄積DBに保存されているmax_medalsが「1回の最大枚数」になっている
正解: 「最大連チャン区間の合計枚数」であるべき

このスクリプトは全ユニットのhistoryから正しいmax_medalsを再計算して更新します。
"""
import json
import sys
from pathlib import Path

# プロジェクトルート
BASE = Path(__file__).resolve().parent.parent
HISTORY_DIR = BASE / 'data' / 'history'

sys.path.insert(0, str(BASE))
from analysis.analyzer import calculate_max_chain_medals


def fix_unit_history(unit_file: Path, machine_key: str) -> dict:
    """1ユニットのmax_medalsを修正"""
    try:
        data = json.loads(unit_file.read_text())
    except Exception as e:
        return {'error': str(e)}

    fixed_count = 0
    skipped_count = 0

    for day in data.get('days', []):
        history = day.get('history', [])
        if not history:
            skipped_count += 1
            continue

        # 現在の値
        old_max_medals = day.get('max_medals', 0)

        # historyから正しい値を計算
        correct_max_medals = calculate_max_chain_medals(history, machine_key=machine_key)

        # 更新（値が変わった場合のみ）
        if old_max_medals != correct_max_medals:
            day['max_medals'] = correct_max_medals
            fixed_count += 1

    # ファイルに書き戻し
    if fixed_count > 0:
        unit_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    return {
        'fixed': fixed_count,
        'skipped': skipped_count,
        'total_days': len(data.get('days', []))
    }


def main():
    """全ユニットのmax_medalsを修正"""
    print("=== max_medals修正スクリプト開始 ===\n")

    total_units = 0
    total_fixed = 0
    total_days = 0

    # 各店舗・機種ディレクトリを走査
    for store_dir in HISTORY_DIR.iterdir():
        if not store_dir.is_dir():
            continue

        store_name = store_dir.name

        # 機種を判定（ディレクトリ名から）
        if 'sbj' in store_name:
            machine_key = 'sbj'
        elif 'hokuto' in store_name:
            machine_key = 'hokuto2'
        else:
            print(f"⚠️  {store_name}: 機種不明、スキップ")
            continue

        print(f"📂 {store_name} ({machine_key})")

        store_fixed = 0
        store_units = 0

        for unit_file in store_dir.glob('*.json'):
            unit_id = unit_file.stem
            result = fix_unit_history(unit_file, machine_key)

            if 'error' in result:
                print(f"  ❌ {unit_id}: {result['error']}")
                continue

            fixed = result['fixed']
            total = result['total_days']

            if fixed > 0:
                print(f"  ✅ {unit_id}: {fixed}/{total}日分を修正")
                store_fixed += fixed

            store_units += 1
            total_units += 1
            total_fixed += fixed
            total_days += total

        print(f"  → {store_units}台、{store_fixed}日分を修正\n")

    print("=" * 50)
    print(f"完了: {total_units}台、{total_fixed}/{total_days}日分を修正")
    print("=" * 50)


if __name__ == '__main__':
    main()
