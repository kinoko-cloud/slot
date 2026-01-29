"""
全recのデータ補完を一箇所で行う共通モジュール。
generate_static.pyから呼ばれる。

補完対象:
- yesterday_diff_medals / yesterday_max_rensa / yesterday_max_medals
- day_before_diff_medals / day_before_max_rensa / day_before_max_medals
- three_days_ago_diff_medals / three_days_ago_max_rensa / three_days_ago_max_medals
- recent_days[].diff_medals / max_rensa / max_medals
"""
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def enrich_recs(recs):
    """
    全recの蓄積DB補完を一括で行う。
    generate_static.pyで全recを生成した後に1回だけ呼ぶ。
    """
    from analysis.history_accumulator import load_unit_history

    # キャッシュ: (store_key, unit_id) -> days_by_date
    _cache = {}

    for rec in recs:
        store_key = rec.get('store_key', '')
        unit_id = str(rec.get('unit_id', ''))
        if not store_key or not unit_id:
            continue

        cache_key = (store_key, unit_id)
        if cache_key not in _cache:
            try:
                acc = load_unit_history(store_key, unit_id)
                if acc and acc.get('days'):
                    _cache[cache_key] = {d['date']: d for d in acc['days'] if d.get('date')}
                else:
                    _cache[cache_key] = {}
            except Exception:
                _cache[cache_key] = {}

        days_by_date = _cache[cache_key]
        if not days_by_date:
            continue

        # 1. 前日/前々日/3日前の補完
        _enrich_day_prefix(rec, days_by_date, 'yesterday_', 'yesterday_date')
        _enrich_day_prefix(rec, days_by_date, 'day_before_', 'day_before_date')
        _enrich_day_prefix(rec, days_by_date, 'three_days_ago_', 'three_days_ago_date')

        # 2. recent_daysの補完
        for rd in rec.get('recent_days', []):
            rd_date = rd.get('date', '')
            if not rd_date:
                continue
            day_data = days_by_date.get(rd_date)
            if not day_data:
                continue
            _enrich_day_dict(rd, day_data)


def _enrich_day_prefix(rec, days_by_date, prefix, date_key):
    """rec[prefix + 'diff_medals'] 等を蓄積DBから補完"""
    target_date = rec.get(date_key, '')
    if not target_date:
        return
    day_data = days_by_date.get(target_date)
    if not day_data:
        return

    if not rec.get(f'{prefix}diff_medals'):
        db_diff = day_data.get('diff_medals')
        if db_diff is not None and db_diff != 0:
            rec[f'{prefix}diff_medals'] = int(db_diff)

    if not rec.get(f'{prefix}max_rensa'):
        db_rensa = day_data.get('max_rensa')
        if db_rensa:
            rec[f'{prefix}max_rensa'] = db_rensa

    if not rec.get(f'{prefix}max_medals'):
        db_max = day_data.get('max_medals')
        if db_max:
            rec[f'{prefix}max_medals'] = db_max


def _enrich_day_dict(day_dict, day_data):
    """recent_daysの各日データを蓄積DBから補完"""
    if not day_dict.get('diff_medals'):
        db_diff = day_data.get('diff_medals')
        if db_diff is not None and db_diff != 0:
            day_dict['diff_medals'] = int(db_diff)

    if not day_dict.get('max_rensa'):
        db_rensa = day_data.get('max_rensa')
        if db_rensa:
            day_dict['max_rensa'] = db_rensa

    if not day_dict.get('max_medals'):
        db_max = day_data.get('max_medals')
        if db_max:
            day_dict['max_medals'] = db_max
