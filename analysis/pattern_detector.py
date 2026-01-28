"""
パターン検出器

あらゆる角度からパターンを検出する:
- 日付パターン（3のつく日、6のつく日、7のつく日、ゾロ目、月末等）
- 台番号パターン（末尾、連番、ゾロ目等）
- 曜日パターン
- 月の時期パターン
- 店舗固有の傾向

データを蓄積し続けて、統計的に有意なパターンを検出する。
"""

import json
from datetime import datetime
from pathlib import Path
from collections import defaultdict

PATTERN_DIR = Path('data/patterns')
PATTERN_DIR.mkdir(parents=True, exist_ok=True)


def get_date_features(date_str: str) -> dict:
    """日付からあらゆる特徴を抽出"""
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    day = dt.day
    month = dt.month
    weekday = dt.weekday()  # 0=月
    weekday_names = ['月', '火', '水', '木', '金', '土', '日']

    features = {
        'date': date_str,
        'day': day,
        'month': month,
        'weekday': weekday_names[weekday],
        'weekday_num': weekday,

        # 日付パターン
        'has_3': '3' in str(day),           # 3のつく日（3,13,23,30,31）
        'has_6': '6' in str(day),           # 6のつく日（6,16,26）
        'has_7': '7' in str(day),           # 7のつく日（7,17,27）
        'has_8': '8' in str(day),           # 8のつく日
        'has_0': str(day).endswith('0'),    # 10,20,30日
        'is_zorome': len(str(day)) == 2 and str(day)[0] == str(day)[1],  # ゾロ目（11,22）
        'day_mod_3': day % 3 == 0,          # 3の倍数
        'day_mod_5': day % 5 == 0,          # 5の倍数
        'day_mod_7': day % 7 == 0,          # 7の倍数

        # 月の時期
        'month_period': 'early' if day <= 10 else 'mid' if day <= 20 else 'late',
        'is_month_start': day <= 3,         # 月初
        'is_month_end': day >= 28,          # 月末
        'is_payday': day == 25,             # 給料日

        # 曜日パターン
        'is_weekend': weekday >= 5,
        'is_friday': weekday == 4,
        'is_monday': weekday == 0,

        # 特殊日
        'is_first_day': day == 1,
    }

    return features


def get_unit_features(unit_id: str) -> dict:
    """台番号からあらゆる特徴を抽出"""
    uid = str(unit_id)
    num = int(uid) if uid.isdigit() else 0

    features = {
        'unit_id': uid,
        'last_digit': int(uid[-1]) if uid else 0,           # 末尾
        'last_2digits': int(uid[-2:]) if len(uid) >= 2 else 0,
        'is_zorome': len(uid) >= 2 and len(set(uid)) == 1,  # ゾロ目（111, 222等）
        'has_7': '7' in uid,
        'has_3': '3' in uid,
        'has_8': '8' in uid,
        'ends_with_1': uid.endswith('1'),    # 端台
        'is_round': num % 10 == 0,           # キリ番
        'is_round_100': num % 100 == 0,      # 100の倍数
        'digit_sum': sum(int(d) for d in uid if d.isdigit()),  # 各桁の合計
    }

    return features


def record_daily_results(store_key: str, machine_key: str, date_str: str, units_data: list):
    """日次の全台結果を記録する（パターン分析用）

    Args:
        units_data: [{unit_id, art, prob, games, is_good, rank_predicted, ...}]
    """
    date_features = get_date_features(date_str)

    records = []
    for u in units_data:
        unit_id = str(u.get('unit_id', ''))
        unit_features = get_unit_features(unit_id)

        record = {
            **date_features,
            **unit_features,
            'store_key': store_key,
            'machine_key': machine_key,
            'art': u.get('art', 0),
            'games': u.get('games', 0),
            'prob': u.get('prob', 0),
            'is_good': u.get('is_good', False),
            'predicted_rank': u.get('predicted_rank', ''),
            'predicted_score': u.get('predicted_score', 0),
        }
        records.append(record)

    # 保存
    out_file = PATTERN_DIR / f'{store_key}_{machine_key}_{date_str}.json'
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    return len(records)


def analyze_patterns(store_key: str, machine_key: str = None) -> dict:
    """蓄積されたパターンデータを分析する

    Returns:
        {
            'date_patterns': {パターン名: {好調率, サンプル数}},
            'unit_patterns': {パターン名: {好調率, サンプル数}},
            'significant': [統計的に有意なパターン],
        }
    """
    # データ読み込み
    all_records = []
    pattern = f'{store_key}_*' if not machine_key else f'{store_key}_{machine_key}_*'
    for fp in PATTERN_DIR.glob(f'{pattern}.json'):
        try:
            with open(fp) as f:
                records = json.load(f)
            all_records.extend(records)
        except (json.JSONDecodeError, IOError):
            continue

    if not all_records:
        return {'date_patterns': {}, 'unit_patterns': {}, 'significant': []}

    # 日付パターン分析
    date_pattern_keys = [
        'has_3', 'has_6', 'has_7', 'has_8', 'has_0',
        'is_zorome', 'day_mod_3', 'day_mod_5', 'day_mod_7',
        'is_month_start', 'is_month_end', 'is_payday',
        'is_weekend', 'is_friday', 'is_monday',
    ]
    date_pattern_names = {
        'has_3': '3のつく日', 'has_6': '6のつく日', 'has_7': '7のつく日',
        'has_8': '8のつく日', 'has_0': '0のつく日',
        'is_zorome': 'ゾロ目の日(11,22)', 'day_mod_3': '3の倍数日',
        'day_mod_5': '5の倍数日', 'day_mod_7': '7の倍数日',
        'is_month_start': '月初(1-3日)', 'is_month_end': '月末(28日~)',
        'is_payday': '給料日(25日)',
        'is_weekend': '土日', 'is_friday': '金曜', 'is_monday': '月曜',
    }

    date_patterns = {}
    for key in date_pattern_keys:
        matching = [r for r in all_records if r.get(key)]
        not_matching = [r for r in all_records if not r.get(key)]
        if len(matching) >= 3:  # 最低3件
            good_rate = sum(1 for r in matching if r.get('is_good')) / len(matching) * 100
            base_rate = sum(1 for r in not_matching if r.get('is_good')) / max(1, len(not_matching)) * 100
            date_patterns[date_pattern_names.get(key, key)] = {
                'good_rate': round(good_rate, 1),
                'base_rate': round(base_rate, 1),
                'diff': round(good_rate - base_rate, 1),
                'sample': len(matching),
            }

    # 台番号パターン分析
    unit_pattern_keys = [
        'has_7', 'has_3', 'has_8', 'ends_with_1', 'is_round',
    ]
    # 末尾別
    unit_patterns = {}
    for digit in range(10):
        matching = [r for r in all_records if r.get('last_digit') == digit]
        if len(matching) >= 3:
            good_rate = sum(1 for r in matching if r.get('is_good')) / len(matching) * 100
            unit_patterns[f'末尾{digit}'] = {
                'good_rate': round(good_rate, 1),
                'sample': len(matching),
            }

    # 統計的に有意なパターン（好調率が全体平均から10%以上乖離）
    overall_good_rate = sum(1 for r in all_records if r.get('is_good')) / max(1, len(all_records)) * 100
    significant = []

    for name, data in {**date_patterns, **unit_patterns}.items():
        diff = data['good_rate'] - overall_good_rate
        if abs(diff) >= 10 and data.get('sample', 0) >= 5:
            significant.append({
                'pattern': name,
                'good_rate': data['good_rate'],
                'overall_rate': round(overall_good_rate, 1),
                'diff': round(diff, 1),
                'sample': data['sample'],
                'direction': '高い' if diff > 0 else '低い',
            })

    significant.sort(key=lambda x: -abs(x['diff']))

    return {
        'date_patterns': date_patterns,
        'unit_patterns': unit_patterns,
        'significant': significant,
        'overall_good_rate': round(overall_good_rate, 1),
        'total_records': len(all_records),
    }


def record_from_history(store_key: str, machine_key: str):
    """蓄積済みhistoryデータからパターンデータを一括記録する"""
    history_dir = Path('data/history') / store_key
    if not history_dir.exists():
        return 0

    # 日付ごとにまとめる
    by_date = defaultdict(list)
    good_prob = 130 if machine_key == 'sbj' else 330

    for fp in history_dir.glob('*.json'):
        try:
            with open(fp) as f:
                data = json.load(f)
        except Exception:
            continue

        unit_id = data.get('unit_id', fp.stem)
        for day in data.get('days', []):
            date = day.get('date', '')
            art = day.get('art', 0)
            games = day.get('games', 0)
            prob = day.get('prob', 0)
            min_art = 20 if machine_key == 'sbj' else 10
            is_good = prob > 0 and prob <= good_prob and art >= min_art

            by_date[date].append({
                'unit_id': unit_id,
                'art': art,
                'games': games,
                'prob': prob,
                'is_good': is_good,
            })

    total = 0
    for date_str, units in by_date.items():
        if date_str:
            n = record_daily_results(store_key, machine_key, date_str, units)
            total += n

    return total
