#!/usr/bin/env python3
"""
バックテストスクリプト
N日までのデータでN+1日を予測し、実績と比較する

使い方:
    # 26日までのデータで27日を予測
    python scripts/backtest.py --predict-date 2026-01-27

    # 複数日のバックテスト
    python scripts/backtest.py --predict-date 2026-01-25 2026-01-26 2026-01-27
"""
import argparse
import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.rankings import STORES, MACHINES, get_stores_by_machine
from analysis.recommender import STORE_KEY_MAPPING

JST = timezone(timedelta(hours=9))

# STORE_KEY_MAPPING の逆引き（data_key → stores_key）
_REVERSE_KEY_MAP = {}
for _sk, _dk in STORE_KEY_MAPPING.items():
    if _dk not in _REVERSE_KEY_MAP:
        _REVERSE_KEY_MAP[_dk] = _sk


def load_all_daily_data():
    """全ての日別データファイルを読み込んで統合する"""
    data_dir = PROJECT_ROOT / 'data' / 'daily'
    all_data = {}  # {store_key: {unit_id: {date: day_data}}}

    for json_file in sorted(data_dir.glob('*.json')):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue

        for store_key, store_data in data.get('stores', {}).items():
            if store_key not in all_data:
                all_data[store_key] = {}
            for unit in store_data.get('units', []):
                uid = str(unit.get('unit_id', ''))
                if uid not in all_data[store_key]:
                    all_data[store_key][uid] = {}
                for day in unit.get('days', []):
                    date = day.get('date', '')
                    if date:
                        all_data[store_key][uid][date] = day

    return all_data


def load_availability_data():
    """availability.jsonから実績データを取得"""
    path = PROJECT_ROOT / 'data' / 'availability.json'
    if not path.exists():
        return {}, ''
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    fetched_at = data.get('fetched_at', '')
    # フェッチ日を抽出
    try:
        fetch_date = datetime.fromisoformat(fetched_at).strftime('%Y-%m-%d')
    except Exception:
        fetch_date = ''

    # store_key -> {unit_id: {art, total_start, ...}}
    actuals = {}
    for store_key, store_data in data.get('stores', {}).items():
        units = {}
        for u in store_data.get('units', []):
            uid = str(u.get('unit_id', ''))
            units[uid] = u
        actuals[store_key] = units

    return actuals, fetch_date


def build_filtered_daily_data(all_data, cutoff_date, original_data_template):
    """cutoff_date以前のデータのみを含むdaily_dataを構築する

    Args:
        all_data: load_all_daily_data()の結果
        cutoff_date: この日付以前のデータのみ使用（YYYY-MM-DD形式）
        original_data_template: 元のデータファイルの構造をテンプレートとして使用
    """
    filtered = {
        'collected_at': f'{cutoff_date}T23:59:59',
        'machines': ['sbj', 'hokuto_tensei2'],
        'stores': {},
    }

    for store_key in STORES:
        if store_key in ('island_akihabara', 'shibuya_espass', 'shinjuku_espass'):
            continue
        store = STORES[store_key]
        store_units = store.get('units', [])
        machine_key = store.get('machine', 'sbj')

        # 日別データのキー（STORE_KEY_MAPPINGで変換）
        data_key = STORE_KEY_MAPPING.get(store_key, store_key)

        units_list = []
        for uid in store_units:
            # STORESキーとデータキーの両方で検索
            unit_days = all_data.get(store_key, {}).get(uid, {})
            if not unit_days:
                unit_days = all_data.get(data_key, {}).get(uid, {})
            # cutoff_date以前のデータのみフィルタ
            filtered_days = []
            for date, day_data in sorted(unit_days.items(), reverse=True):
                if date <= cutoff_date:
                    filtered_days.append(day_data)

            if filtered_days:
                units_list.append({
                    'unit_id': uid,
                    'hall_id': store.get('hall_id', ''),
                    'hall_name': store.get('name', ''),
                    'fetched_at': f'{cutoff_date}T23:59:59',
                    'days': filtered_days,
                })

        if units_list:
            store_entry = {
                'hall_name': store.get('name', ''),
                'machine_key': machine_key,
                'machine_name': MACHINES.get(machine_key, {}).get('name', ''),
                'units': units_list,
            }
            filtered['stores'][store_key] = store_entry
            # データキーでも格納（recommender内部のSTORE_KEY_MAPPING用）
            if data_key != store_key:
                filtered['stores'][data_key] = store_entry

    return filtered


def get_actual_for_date(all_data, avail_actuals, avail_date, predict_date):
    """predict_dateの実績データを取得する

    Returns:
        {store_key: {unit_id: {art, total_start, actual_prob}}}
    """
    actuals = {}

    # availability.jsonの日付がpredict_dateと一致すればそれを使う
    if avail_date == predict_date:
        for store_key, units in avail_actuals.items():
            actuals[store_key] = {}
            for uid, u in units.items():
                art = u.get('art', 0)
                total = u.get('total_start', 0)
                actuals[store_key][uid] = {
                    'art': art,
                    'total_start': total,
                    'actual_prob': total / art if art > 0 else 0,
                }
        return actuals

    # それ以外はdaily_dataから対象日のデータを取得
    for store_key in STORES:
        if store_key in ('island_akihabara', 'shibuya_espass', 'shinjuku_espass'):
            continue
        actuals[store_key] = {}
        data_key = STORE_KEY_MAPPING.get(store_key, store_key)
        store_data = all_data.get(store_key, {})
        if not store_data:
            store_data = all_data.get(data_key, {})
        for uid, dates in store_data.items():
            if predict_date in dates:
                day = dates[predict_date]
                art = day.get('art', 0)
                total = day.get('total_start', 0)
                actuals[store_key][uid] = {
                    'art': art,
                    'total_start': total,
                    'actual_prob': total / art if art > 0 else 0,
                }

    return actuals


def run_backtest(predict_date, machine_filter='all'):
    """指定日のバックテストを実行

    Args:
        predict_date: 予測対象日（YYYY-MM-DD形式）
        machine_filter: 対象機種（'sbj', 'hokuto_tensei2', 'all'）

    Returns:
        結果サマリー
    """
    import analysis.recommender as recommender

    # 前日を計算
    predict_dt = datetime.strptime(predict_date, '%Y-%m-%d')
    cutoff_date = (predict_dt - timedelta(days=1)).strftime('%Y-%m-%d')

    print(f"\n{'='*60}")
    print(f"バックテスト: {cutoff_date}までのデータで{predict_date}を予測")
    print(f"{'='*60}")

    # 全データを読み込み
    all_data = load_all_daily_data()
    avail_actuals, avail_date = load_availability_data()

    # cutoff_date以前のデータでフィルタ
    filtered_data = build_filtered_daily_data(all_data, cutoff_date, None)

    # 実績データを取得
    actuals = get_actual_for_date(all_data, avail_actuals, avail_date, predict_date)

    # load_daily_dataをmonkeypatch
    original_load = recommender.load_daily_data
    def mock_load_daily_data(date_str=None, machine_key=None):
        return filtered_data
    recommender.load_daily_data = mock_load_daily_data

    # datetimeもmonkeypatch（今日の日付をpredict_dateにする）
    original_now = datetime.now
    class MockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return predict_dt.replace(hour=9, minute=0)
    # recommenderのdatetimeをモック
    import analysis.recommender
    orig_datetime = analysis.recommender.datetime
    analysis.recommender.datetime = MockDatetime

    try:
        # 各店舗の予測を実行
        results = []
        target_stores = [sk for sk in STORES if sk not in ('island_akihabara', 'shibuya_espass', 'shinjuku_espass')]
        if machine_filter != 'all':
            target_stores = [sk for sk in target_stores if STORES.get(sk, {}).get('machine') == machine_filter]

        for store_key in target_stores:
            store = STORES.get(store_key)
            if not store:
                continue
            machine_key = store.get('machine', 'sbj')
            store_name = store.get('short_name', store.get('name', store_key))

            predictions = recommender.recommend_units(store_key)
            store_actuals = actuals.get(store_key, {})

            for pred in predictions:
                uid = str(pred.get('unit_id', ''))
                actual = store_actuals.get(uid, {})
                art = actual.get('art', 0)
                total = actual.get('total_start', 0)
                actual_prob = actual.get('actual_prob', 0)

                results.append({
                    'store_key': store_key,
                    'store_name': store_name,
                    'machine_key': machine_key,
                    'unit_id': uid,
                    'pred_rank': pred.get('final_rank', 'C'),
                    'pred_score': pred.get('final_score', 50),
                    'art': art,
                    'total_start': total,
                    'actual_prob': actual_prob,
                })
    finally:
        # monkeypatchを元に戻す
        recommender.load_daily_data = original_load
        analysis.recommender.datetime = orig_datetime

    # 結果集計
    return summarize_results(results, predict_date)


def summarize_results(results, predict_date):
    """バックテスト結果を集計して表示"""

    total_sa = 0
    sa_hits = 0
    total_cd = 0
    cd_hits = 0
    total_good = 0
    sa_good = 0

    store_results = {}

    for r in results:
        mk = r['machine_key']
        high_th = 145 if mk == 'sbj' else 100
        mid_th = 200 if mk == 'sbj' else 150

        prob = r['actual_prob']
        rank = r['pred_rank']

        if prob <= 0:
            continue  # データなし

        is_good = prob <= high_th
        is_bad = prob >= mid_th

        if is_good:
            total_good += 1

        sk = r['store_key']
        if sk not in store_results:
            store_results[sk] = {'name': r['store_name'], 'sa': 0, 'sa_hit': 0, 'good': 0, 'sa_good': 0}

        if rank in ('S', 'A'):
            total_sa += 1
            store_results[sk]['sa'] += 1
            if is_good:
                sa_hits += 1
                sa_good += 1
                store_results[sk]['sa_hit'] += 1
                store_results[sk]['sa_good'] += 1
        elif rank in ('C', 'D'):
            total_cd += 1
            if is_bad:
                cd_hits += 1

        if is_good:
            store_results[sk]['good'] += 1
            if rank in ('S', 'A'):
                pass  # already counted

    # 結果表示
    precision = sa_hits / total_sa * 100 if total_sa > 0 else 0
    coverage = sa_good / total_good * 100 if total_good > 0 else 0
    f1 = 2 * precision * coverage / (precision + coverage) if (precision + coverage) > 0 else 0

    print(f"\n📊 {predict_date} 予測結果")
    print(f"  S/A予測的中率（精度）: {sa_hits}/{total_sa} ({precision:.1f}%)")
    print(f"  カバー率: {sa_good}/{total_good} ({coverage:.1f}%)")
    print(f"  F1スコア: {f1:.1f}")
    print()

    # 店舗別
    print("📍 店舗別:")
    for sk, sr in sorted(store_results.items()):
        if sr['sa'] > 0:
            rate = sr['sa_hit'] / sr['sa'] * 100
            print(f"  {sr['name']}: S/A的中 {sr['sa_hit']}/{sr['sa']} ({rate:.0f}%) / 高設定台{sr['good']}台中{sr['sa_good']}台カバー")

    # 外れ分析
    print()
    print("🔍 詳細:")
    missed_sa = [r for r in results if r['pred_rank'] in ('S', 'A') and r['actual_prob'] > 0 and r['actual_prob'] > (145 if r['machine_key'] == 'sbj' else 100)]
    surprise = [r for r in results if r['pred_rank'] not in ('S', 'A') and r['actual_prob'] > 0 and r['actual_prob'] <= (145 if r['machine_key'] == 'sbj' else 100)]

    if missed_sa:
        print("  S/A外れ:")
        for r in missed_sa:
            print(f"    {r['store_name']} {r['unit_id']}番 [{r['pred_rank']}] → 1/{r['actual_prob']:.0f} (スコア{r['pred_score']:.0f})")

    if surprise:
        print("  見逃し（B/C/D予測だが高設定）:")
        for r in surprise:
            print(f"    {r['store_name']} {r['unit_id']}番 [{r['pred_rank']}] → 1/{r['actual_prob']:.0f} (スコア{r['pred_score']:.0f})")

    return {
        'date': predict_date,
        'precision': round(precision, 1),
        'coverage': round(coverage, 1),
        'f1_score': round(f1, 1),
        'sa_total': total_sa,
        'sa_hits': sa_hits,
        'total_good': total_good,
        'captured_good': sa_good,
        'missed_sa': len(missed_sa),
        'surprises': len(surprise),
    }


def main():
    parser = argparse.ArgumentParser(description='予測精度のバックテスト')
    parser.add_argument('--predict-date', nargs='+', required=True,
                       help='予測対象日（YYYY-MM-DD形式、複数指定可）')
    parser.add_argument('--machine', choices=['sbj', 'hokuto_tensei2', 'all'], default='all',
                       help='対象機種（デフォルト: all）')
    args = parser.parse_args()

    all_summaries = []
    for date in args.predict_date:
        summary = run_backtest(date, machine_filter=args.machine)
        all_summaries.append(summary)

    if len(all_summaries) > 1:
        print(f"\n{'='*60}")
        print("📊 総合サマリー")
        print(f"{'='*60}")
        total_sa = sum(s['sa_total'] for s in all_summaries)
        total_sa_hits = sum(s['sa_hits'] for s in all_summaries)
        total_good = sum(s['total_good'] for s in all_summaries)
        total_captured = sum(s['captured_good'] for s in all_summaries)

        overall_precision = total_sa_hits / total_sa * 100 if total_sa > 0 else 0
        overall_coverage = total_captured / total_good * 100 if total_good > 0 else 0
        overall_f1 = 2 * overall_precision * overall_coverage / (overall_precision + overall_coverage) if (overall_precision + overall_coverage) > 0 else 0

        for s in all_summaries:
            print(f"  {s['date']}: 精度{s['precision']:.0f}% / カバー{s['coverage']:.0f}% / F1={s['f1_score']:.1f}")

        print(f"\n  総合: 精度{overall_precision:.1f}% / カバー{overall_coverage:.1f}% / F1={overall_f1:.1f}")
        print(f"  S/A的中: {total_sa_hits}/{total_sa} / 高設定カバー: {total_captured}/{total_good}")


if __name__ == '__main__':
    main()
