#!/usr/bin/env python3
"""
デイリーデータ収集スクリプト

毎日実行して履歴データを蓄積
- 前日までのデータを取得
- JSONで日付別に保存
- 長期的な傾向分析用
- 排他ロック付き — 複数プロセスの同時実行を防止
"""
# 排他ロック(最初に取得)
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from scripts.fetch_lock import acquire_lock
_lock_fp = acquire_lock()

対応機種:
- SBJ (スーパーブラックジャック)
- 北斗転生2 (北斗の拳 転生の章2)
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
import sys

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.stores import DAIDATA_STORES, PAPIMO_STORES, MACHINES
from scrapers.daidata_detail_history import get_all_history
from scripts.verify_units import verify_units_from_daily, save_alerts, print_report


def collect_daily_data(machine_keys: list = None, max_units_per_store: int = None):
    """全店舗のデータを収集

    Args:
        machine_keys: 取得する機種リスト(None=全機種)
        max_units_per_store: 店舗あたりの最大台数(テスト用)
    """
    today = datetime.now().strftime('%Y%m%d')
    data_dir = Path('data/daily')
    data_dir.mkdir(parents=True, exist_ok=True)

    if machine_keys is None:
        machine_keys = ['sbj', 'hokuto_tensei2']

    results = {
        'collected_at': datetime.now().isoformat(),
        'machines': machine_keys,
        'stores': {}
    }

    print('=' * 60)
    print(f'デイリーデータ収集 - {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print(f'対象機種: {", ".join(machine_keys)}')
    print('=' * 60)

    # 1. 台データオンライン店舗(当日分も含め全日データを取得)
    for store_key, store_config in DAIDATA_STORES.items():
        hall_id = store_config['hall_id']
        hall_name = store_config['name']

        for machine_key in machine_keys:
            units = store_config.get('machines', {}).get(machine_key, [])
            if not units:
                continue

            if max_units_per_store:
                units = units[:max_units_per_store]

            machine_name = MACHINES.get(machine_key, {}).get('name', machine_key)
            print(f'\n【{hall_name} - {machine_name}】')

            try:
                collected = []
                _expected = MACHINES.get(machine_key, {}).get('verify_keywords')
                for unit_id in units:
                    print(f'  台{unit_id}...')
                    result = get_all_history(hall_id=hall_id, unit_id=unit_id, hall_name=hall_name,
                                             expected_machine=_expected)
                    if result and result.get('machine_mismatch'):
                        print(f"    ⚠️ 台{unit_id}は別機種に変更された可能性。収集スキップ。")
                        continue
                    if result:
                        # 空データチェック: daysが空 or 全日art=0&games=0ならスキップ
                        valid_days = [d for d in result.get('days', [])
                                      if d.get('art', 0) > 0 or (d.get('total_start', 0) or d.get('games', 0) or 0) > 0]
                        if not valid_days:
                            print(f"    ⚠️ 台{unit_id}: 有効データなし(スクレイピング失敗の可能性)")
                            continue
                        result['machine_key'] = machine_key
                        result['machine_name'] = machine_name
                        collected.append(result)

                result_key = f'{store_key}_{machine_key}'
                results['stores'][result_key] = {
                    'hall_name': hall_name,
                    'machine_key': machine_key,
                    'machine_name': machine_name,
                    'units': collected,
                }
                print(f'  ✓ {len(collected)}台取得')

            except Exception as e:
                print(f'  ✗ エラー: {e}')

    # 2. PAPIMO店舗
    from playwright.sync_api import sync_playwright
    from scrapers.papimo import get_unit_history

    for store_key, store_config in PAPIMO_STORES.items():
        hall_id = store_config['hall_id']
        hall_name = store_config['name']

        for machine_key in machine_keys:
            units = store_config.get('machines', {}).get(machine_key, [])
            if not units:
                continue

            if max_units_per_store:
                units = units[:max_units_per_store]

            machine_name = MACHINES.get(machine_key, {}).get('name', machine_key)
            print(f'\n【{hall_name} - {machine_name}】')

            try:
                collected = []
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    page = browser.new_page()

                    for unit_id in units:
                        print(f'  台{unit_id}...')
                        # 当日分(23:00実行時点のリアルタイムデータ)+ 前日分を取得
                        # 差分更新: historyに既存データがあれば2日分(当日+前日)
                        # 新規台はフルバックフィル(14日分)
                        from pathlib import Path as _P
                        _hist_dir = _P(f'data/history/{store_key}_{machine_key}')
                        _hist_file = _hist_dir / f'{unit_id}.json'
                        _days_back = 2 if _hist_file.exists() else 14
                        _expected = MACHINES.get(machine_key, {}).get('verify_keywords')
                        result = get_unit_history(page, hall_id, unit_id, days_back=_days_back,
                                                  expected_machine=_expected)
                        if result.get('machine_mismatch'):
                            print(f"    ⚠️ 台{unit_id}は別機種に変更された可能性。収集スキップ。")
                            continue
                        # 空データチェック
                        valid_days = [d for d in result.get('days', [])
                                      if d.get('art', 0) > 0 or (d.get('total_start', 0) or d.get('games', 0) or 0) > 0]
                        if not valid_days:
                            print(f"    ⚠️ 台{unit_id}: 有効データなし(スクレイピング失敗の可能性)")
                            continue
                        result['hall_name'] = hall_name
                        result['machine_key'] = machine_key
                        result['machine_name'] = machine_name
                        collected.append(result)

                    browser.close()

                result_key = f'{store_key}_{machine_key}'
                results['stores'][result_key] = {
                    'hall_name': hall_name,
                    'machine_key': machine_key,
                    'machine_name': machine_name,
                    'units': collected,
                }
                print(f'  ✓ {len(collected)}台取得')

            except Exception as e:
                print(f'  ✗ エラー: {e}')

    # 保存
    machine_suffix = '_'.join(machine_keys) if len(machine_keys) <= 2 else 'all'
    save_path = data_dir / f'daily_{machine_suffix}_{today}.json'
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f'\n✓ 保存: {save_path}')

    return results


def merge_historical_data():
    """過去のデイリーデータを統合して長期分析用データを作成"""
    data_dir = Path('data/daily')
    merged_dir = Path('data/merged')
    merged_dir.mkdir(parents=True, exist_ok=True)

    # 全デイリーファイルを読み込み
    daily_files = sorted(data_dir.glob('sbj_daily_*.json'))

    if not daily_files:
        print('デイリーデータがありません')
        return

    print(f'統合対象: {len(daily_files)}日分')

    # 台ごとにデータを統合
    merged = {}

    for file_path in daily_files:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        for store_key, store_data in data.get('stores', {}).items():
            hall_name = store_data.get('hall_name', '')

            for unit in store_data.get('units', []):
                unit_id = unit.get('unit_id')
                key = f"{store_key}_{unit_id}"

                if key not in merged:
                    merged[key] = {
                        'unit_id': unit_id,
                        'hall_name': hall_name,
                        'machine_name': unit.get('machine_name', 'SBJ'),
                        'days': [],
                    }

                # 日別データを追加
                for day in unit.get('days', []):
                    # 重複チェック
                    existing_dates = [d.get('date') for d in merged[key]['days']]
                    if day.get('date') not in existing_dates:
                        merged[key]['days'].append(day)

    # 日付順にソート
    for key in merged:
        merged[key]['days'].sort(key=lambda d: d.get('date', ''), reverse=True)

    # 保存
    save_path = merged_dir / f'sbj_merged_{datetime.now().strftime("%Y%m%d")}.json'
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(list(merged.values()), f, ensure_ascii=False, indent=2)

    print(f'✓ 統合データ保存: {save_path}')
    print(f'  台数: {len(merged)}')

    # 統計表示
    for key, data in merged.items():
        days = len(data['days'])
        print(f'  {data["hall_name"]} 台{data["unit_id"]}: {days}日分')


def main():
    import argparse
    parser = argparse.ArgumentParser(description='パチスロ デイリーデータ収集')
    parser.add_argument('--merge', action='store_true', help='過去データを統合')
    parser.add_argument('--machine', '-m', nargs='+', choices=['sbj', 'hokuto_tensei2', 'all'],
                        default=['all'], help='取得する機種 (default: all)')
    parser.add_argument('--max-units', type=int, default=None,
                        help='店舗あたりの最大台数(テスト用)')
    args = parser.parse_args()

    if args.merge:
        merge_historical_data()
    else:
        # 機種指定
        if 'all' in args.machine:
            machine_keys = ['sbj', 'hokuto_tensei2']
        else:
            machine_keys = args.machine

        results = collect_daily_data(machine_keys=machine_keys, max_units_per_store=args.max_units)

        # 台番号検証(従来)
        print('\n' + '=' * 60)
        print('台番号検証')
        print('=' * 60)
        alerts = verify_units_from_daily(results)
        print_report(alerts)
        if alerts:
            save_path = save_alerts(alerts, source='daily')
            print(f'アラート保存: {save_path}')

        # 包括データ整合性チェック(新)
        try:
            from scripts.data_integrity_check import run_all_checks, save_check_result, format_notification
            integrity_alerts = run_all_checks(results)
            if integrity_alerts:
                save_check_result(integrity_alerts)
            # 重大アラートがあれば通知テキスト出力(GitHub Actions等で拾える)
            notification = format_notification(integrity_alerts)
            if notification:
                # 通知テキストをファイルに保存(外部から読める形で)
                notif_path = Path('data/alerts/latest_notification.txt')
                notif_path.parent.mkdir(parents=True, exist_ok=True)
                notif_path.write_text(notification, encoding='utf-8')
                print(f'\n📱 重大アラート通知テキスト保存: {notif_path}')
        except Exception as e:
            print(f'⚠ 整合性チェックエラー: {e}')

        # ランキングデータ取得(差玉TOP10)
        print('\n' + '=' * 60)
        print('ランキングデータ取得(差玉TOP10)')
        print('=' * 60)
        try:
            from scrapers.daidata_ranking import collect_all_rankings
            ranking_results = collect_all_rankings()
            print(f'ランキング取得完了: {len(ranking_results)}店舗')
        except Exception as e:
            print(f'⚠ ランキング取得エラー: {e}')

        # サイトセブン(全台の最高出玉データ)
        print('\n' + '=' * 60)
        print('サイトセブン データ取得(全台BB/RB/ART/最高出玉)')
        print('=' * 60)
        try:
            from scrapers.site777 import collect_and_save as s777_collect
            s777_results = s777_collect(days_back=1)
            total = sum(len(units) for store in s777_results.values() for units in store.values())
            print(f'サイトセブン完了: {len(s777_results)}店舗 計{total}台分')
        except Exception as e:
            print(f'⚠ サイトセブン取得エラー: {e}')

        # アナスロ(店舗日別 総差枚・勝率・旧イベント日)
        print('\n' + '=' * 60)
        print('アナスロ データ取得(店舗日別 差枚・勝率)')
        print('=' * 60)
        try:
            from scrapers.anaslo import collect_and_save as anaslo_collect
            anaslo_results = anaslo_collect()
            print(f'アナスロ完了: {len(anaslo_results)}店舗')
        except Exception as e:
            print(f'⚠ アナスロ取得エラー: {e}')


if __name__ == '__main__':
    main()
