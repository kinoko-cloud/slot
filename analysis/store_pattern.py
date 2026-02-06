#!/usr/bin/env python3
"""
店舗設定投入パターン分析モジュール

各店舗の「設定投入パターン」を過去データから自動学習し、
前日予想スコアにボーナスを付与する。

既存の weekday_bonus / slump_bonus は「一般的な傾向」。
本モジュールの pattern_bonus は「店舗固有の癖」を補正する。
"""

import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from functools import lru_cache

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.rankings import MACHINES, STORES, get_machine_threshold

# =============================================================================
# 定数
# =============================================================================

HISTORY_DIR = Path(__file__).parent.parent / 'data' / 'history'

# 店舗キー → 履歴ディレクトリ名マッピング
_STORE_KEY_TO_HISTORY_DIR = {
    'shibuya_espass_hokuto': 'shibuya_espass_hokuto2',
    'shinjuku_espass_hokuto': 'shinjuku_espass_hokuto2',
    'akiba_espass_hokuto': 'akiba_espass_hokuto2',
    'island_akihabara_hokuto': 'island_akihabara_hokuto2',
}

# 曜日名
WEEKDAY_NAMES = ['月', '火', '水', '木', '金', '土', '日']

# 特定日グループ定義
SPECIAL_DAY_GROUPS = {
    3: [3, 13, 23, 30, 31],       # 3のつく日
    6: [6, 16, 26],               # 6のつく日
    7: [7, 17, 27],               # 7のつく日
    '5mult': [5, 10, 15, 20, 25, 30],  # 5の倍数
    'zorome': [11, 22],           # ゾロ目
}

# ボーナス重み係数
WEIGHT_CARRYOVER = 3.0       # 据え置きボーナス重み
WEIGHT_SLUMP_CHANGE = 4.0    # 不調→設定変更期待ボーナス重み
WEIGHT_SPECIAL_DAY = 2.5     # 特定日ボーナス重み
WEIGHT_WEEKDAY = 1.5         # 曜日ボーナス重み（既存weekday_bonusと差別化: こちらは店舗固有率ベース）
WEIGHT_ISLAND_WAVE = 2.0     # 島の波ボーナス重み
WEIGHT_DIGIT = 1.0           # 台番末尾ボーナス重み
WEIGHT_GOOD_STREAK = 2.0     # 好調連続からの下降期待
WEIGHT_SETTING6_CARRY = 3.5  # 設定6据え置き期待
WEIGHT_SPREAD_PROMO = 2.5    # spread型の不調→投入期待加算
WEIGHT_TIGHT_PENALTY = -2.0  # tight店の全体ペナルティ

# confidence の最小サンプル数
MIN_SAMPLES_FOR_FULL_CONFIDENCE = 14  # 14日分あれば信頼度1.0

# =============================================================================
# 設定段階推定
# =============================================================================

# 機種別の設定域閾値（ART確率 = games / art）
# prob が閾値以下なら該当段階
SETTING_TIERS = {
    'sbj': {
        6: 90,    # 設定6域: 1/90以下
        5: 110,   # 設定5域: 1/90〜1/110
        4: 140,   # 設定4域: 1/110〜1/140
        # 3以下: 1/140以上
    },
    'hokuto2': {
        6: 60,    # 設定6域: 1/60以下
        5: 80,    # 設定5域: 1/60〜1/80
        4: 110,   # 設定4域: 1/80〜1/110
        # 3以下: 1/110以上
    },
}

# デフォルト閾値（新機種用）
SETTING_TIERS_DEFAULT = {6: 100, 5: 130, 4: 170}


def _estimate_setting_tier(day: dict, machine_key: str) -> int:
    """日のデータから設定段階を推定する

    Returns:
        6, 5, 4, 3 のいずれか（3 = 設定3以下）
        0 = データ不足で判定不能
    """
    art = day.get('art', 0)
    games = day.get('games', 0) or day.get('total_start', 0)

    # 最低試行回数チェック
    min_art = 15 if machine_key == 'sbj' else 8
    if art < min_art or games < 300:
        return 0  # データ不足

    prob = games / art
    tiers = SETTING_TIERS.get(machine_key, SETTING_TIERS_DEFAULT)

    if prob <= tiers[6]:
        return 6
    elif prob <= tiers[5]:
        return 5
    elif prob <= tiers[4]:
        return 4
    else:
        return 3


# =============================================================================
# ユーティリティ
# =============================================================================

def _resolve_history_dir(store_key: str) -> str:
    """store_key から履歴ディレクトリ名を解決"""
    return _STORE_KEY_TO_HISTORY_DIR.get(store_key, store_key)


def _is_good_day(day: dict, machine_key: str) -> bool:
    """正しい閾値で好調判定する（historyの is_good は閾値バグの可能性があるため再計算）"""
    art = day.get('art', 0)
    games = day.get('games', 0) or day.get('total_start', 0)
    if art <= 0 or games <= 0:
        return False

    prob = games / art
    good_prob = get_machine_threshold(machine_key, 'good_prob')
    # 最低試行回数: SBJ=20, 北斗=10
    min_art = 20 if machine_key == 'sbj' else 10
    return prob <= good_prob and art >= min_art


def _is_bad_day(day: dict, machine_key: str) -> bool:
    """不調判定"""
    art = day.get('art', 0)
    games = day.get('games', 0) or day.get('total_start', 0)
    if art <= 0 or games <= 0:
        return True  # データなし = 稼働なし ≈ 不調

    prob = games / art
    bad_prob = get_machine_threshold(machine_key, 'bad_prob')
    return prob > bad_prob or art < (10 if machine_key == 'sbj' else 5)


def _is_active_day(day: dict, machine_key: str) -> bool:
    """有効データのある日かどうか（稼働あり）"""
    art = day.get('art', 0)
    games = day.get('games', 0) or day.get('total_start', 0)
    return art > 0 and games > 300


def _confidence(n: int, min_samples: int = MIN_SAMPLES_FOR_FULL_CONFIDENCE) -> float:
    """サンプル数から信頼度（0〜1）を計算"""
    if n <= 0:
        return 0.0
    if n >= min_samples:
        return 1.0
    return n / min_samples


def _load_all_unit_histories(store_key: str) -> List[dict]:
    """店舗の全台の履歴データを読み込む"""
    dir_name = _resolve_history_dir(store_key)
    store_dir = HISTORY_DIR / dir_name
    if not store_dir.exists():
        return []

    histories = []
    for file_path in sorted(store_dir.glob('*.json')):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                histories.append(data)
        except (json.JSONDecodeError, IOError):
            continue
    return histories


def _load_unit_history(store_key: str, unit_id) -> Optional[dict]:
    """特定台の履歴データを読み込む"""
    dir_name = _resolve_history_dir(store_key)
    store_dir = HISTORY_DIR / dir_name
    file_path = store_dir / f'{unit_id}.json'
    if not file_path.exists():
        return None
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


# =============================================================================
# パターン分析: 設定移動パターン
# =============================================================================

def _analyze_setting_movement(all_histories: List[dict], machine_key: str) -> dict:
    """設定移動パターンを分析する

    各台の日ごとの好調/不調を時系列で見て、
    据え置き率・投入率・不調放置上限等を算出。
    """
    # 全台の日別好調/不調データを構築
    # {unit_id: [(date_str, is_good, is_bad, is_active), ...]}
    unit_timelines = {}

    for hist in all_histories:
        uid = str(hist.get('unit_id', ''))
        days = hist.get('days', [])
        if not uid or not days:
            continue

        sorted_days = sorted(days, key=lambda d: d.get('date', ''))
        timeline = []
        for day in sorted_days:
            date_str = day.get('date', '')
            if not date_str:
                continue
            good = _is_good_day(day, machine_key)
            bad = _is_bad_day(day, machine_key)
            active = _is_active_day(day, machine_key)
            timeline.append((date_str, good, bad, active))
        if timeline:
            unit_timelines[uid] = timeline

    # --- 据え置き率 (carry_over_rate) ---
    # 前日好調 → 翌日も好調
    carry_good_good = 0
    carry_good_total = 0

    # --- 投入率 (promotion_rate) ---
    # 前日不調 → 翌日好調
    promo_bad_good = 0
    promo_bad_total = 0

    # --- 不調連続ストリーク ---
    bad_streaks = []       # 不調が何日続いたか
    # --- 好調連続ストリーク ---
    good_streaks = []

    for uid, timeline in unit_timelines.items():
        current_bad_streak = 0
        current_good_streak = 0

        for i in range(len(timeline)):
            date_str, good, bad, active = timeline[i]

            # 不調ストリーク計算
            if bad:
                current_bad_streak += 1
            else:
                if current_bad_streak > 0:
                    bad_streaks.append(current_bad_streak)
                current_bad_streak = 0

            # 好調ストリーク計算
            if good:
                current_good_streak += 1
            else:
                if current_good_streak > 0:
                    good_streaks.append(current_good_streak)
                current_good_streak = 0

            # 翌日との比較
            if i < len(timeline) - 1:
                next_date, next_good, next_bad, next_active = timeline[i + 1]

                # 連続日かチェック（1日以上空いたらスキップ）
                try:
                    d1 = datetime.strptime(date_str, '%Y-%m-%d')
                    d2 = datetime.strptime(next_date, '%Y-%m-%d')
                    if (d2 - d1).days != 1:
                        continue
                except ValueError:
                    continue

                if good:
                    carry_good_total += 1
                    if next_good:
                        carry_good_good += 1

                if bad:
                    promo_bad_total += 1
                    if next_good:
                        promo_bad_good += 1

        # 末尾のストリークも記録
        if current_bad_streak > 0:
            bad_streaks.append(current_bad_streak)
        if current_good_streak > 0:
            good_streaks.append(current_good_streak)

    carry_over_rate = carry_good_good / carry_good_total if carry_good_total > 0 else 0.0
    promotion_rate = promo_bad_good / promo_bad_total if promo_bad_total > 0 else 0.0

    max_bad_streak = max(bad_streaks) if bad_streaks else 0
    avg_bad_before_promotion = sum(bad_streaks) / len(bad_streaks) if bad_streaks else 0.0

    max_good_streak = max(good_streaks) if good_streaks else 0
    avg_good_before_demotion = sum(good_streaks) / len(good_streaks) if good_streaks else 0.0

    return {
        'carry_over_rate': round(carry_over_rate, 3),
        'carry_over_samples': carry_good_total,
        'promotion_rate': round(promotion_rate, 3),
        'promotion_samples': promo_bad_total,
        'max_bad_streak': max_bad_streak,
        'avg_bad_before_promotion': round(avg_bad_before_promotion, 2),
        'bad_streak_count': len(bad_streaks),
        'max_good_streak': max_good_streak,
        'avg_good_before_demotion': round(avg_good_before_demotion, 2),
        'good_streak_count': len(good_streaks),
    }


def _analyze_island_wave(all_histories: List[dict], machine_key: str) -> dict:
    """島全体の日別好調台数の波パターンを分析

    前日の好調台数が少ない → 翌日増やす傾向があるか？
    """
    # 日別好調台数を集計
    date_good_counts = {}   # {date: 好調台数}
    date_total_counts = {}  # {date: 稼働台数}

    for hist in all_histories:
        days = hist.get('days', [])
        for day in days:
            date_str = day.get('date', '')
            if not date_str:
                continue
            active = _is_active_day(day, machine_key)
            good = _is_good_day(day, machine_key)

            if active:
                date_total_counts[date_str] = date_total_counts.get(date_str, 0) + 1
                if good:
                    date_good_counts[date_str] = date_good_counts.get(date_str, 0) + 1

    # 日付順にソート
    sorted_dates = sorted(date_total_counts.keys())
    if len(sorted_dates) < 3:
        return {
            'inverse_correlation': 0.0,
            'samples': 0,
            'daily_good_counts': {},
        }

    # 前日の好調率が低い → 翌日の好調率が高い（逆相関）のチェック
    prev_rates = []
    next_rates = []
    daily_good_counts = {}

    for i, date_str in enumerate(sorted_dates):
        total = date_total_counts.get(date_str, 0)
        good = date_good_counts.get(date_str, 0)
        rate = good / total if total > 0 else 0
        daily_good_counts[date_str] = {'good': good, 'total': total, 'rate': round(rate, 3)}

        if i > 0:
            prev_date = sorted_dates[i - 1]
            # 連続日かチェック
            try:
                d1 = datetime.strptime(prev_date, '%Y-%m-%d')
                d2 = datetime.strptime(date_str, '%Y-%m-%d')
                if (d2 - d1).days == 1:
                    prev_total = date_total_counts.get(prev_date, 0)
                    prev_good = date_good_counts.get(prev_date, 0)
                    prev_rate = prev_good / prev_total if prev_total > 0 else 0
                    prev_rates.append(prev_rate)
                    next_rates.append(rate)
            except ValueError:
                continue

    # 逆相関の程度を計算（ピアソン相関の符号反転）
    inverse_corr = 0.0
    if len(prev_rates) >= 3:
        n = len(prev_rates)
        mean_p = sum(prev_rates) / n
        mean_n = sum(next_rates) / n
        cov = sum((p - mean_p) * (q - mean_n) for p, q in zip(prev_rates, next_rates)) / n
        std_p = math.sqrt(sum((p - mean_p) ** 2 for p in prev_rates) / n) if n > 0 else 0
        std_n = math.sqrt(sum((q - mean_n) ** 2 for q in next_rates) / n) if n > 0 else 0
        if std_p > 0 and std_n > 0:
            inverse_corr = -cov / (std_p * std_n)  # 正の値 = 前日絞り→翌日増やす傾向

    return {
        'inverse_correlation': round(inverse_corr, 3),
        'samples': len(prev_rates),
        'daily_good_counts': daily_good_counts,
    }


# =============================================================================
# パターン分析: 設定段階分析
# =============================================================================

def _analyze_setting_grade(all_histories: List[dict], machine_key: str) -> dict:
    """設定段階の分布・スタイルを分析する

    好調/不調の二値ではなく設定6/5/4/3以下の段階で分析し、
    店舗の設定投入スタイルを判定する。

    Returns:
        {
            'setting_distribution': {date: {6: n, 5: n, 4: n, 3: n, 'total': n}},
            'uses_setting_6': bool,
            'setting_6_frequency': float,        # 設定6が出る日の割合
            'setting_6_per_day': float,           # 設定6が出る日の平均台数
            'typical_high_count': {               # 設定4以上の台数統計
                'avg': float, 'min': int, 'max': int, 'samples': int
            },
            'setting_style': str,                 # "heavy_hitter"|"spread"|"mixed"|"tight"
            'style_confidence': float,
        }
    """
    # 日別の設定段階分布を集計
    # {date: {6: count, 5: count, 4: count, 3: count, 'total': count}}
    date_tier_counts: Dict[str, Dict] = {}

    for hist in all_histories:
        days = hist.get('days', [])
        for day in days:
            date_str = day.get('date', '')
            if not date_str:
                continue
            if not _is_active_day(day, machine_key):
                continue

            tier = _estimate_setting_tier(day, machine_key)
            if tier == 0:
                continue  # データ不足はスキップ

            if date_str not in date_tier_counts:
                date_tier_counts[date_str] = {6: 0, 5: 0, 4: 0, 3: 0, 'total': 0}

            date_tier_counts[date_str][tier] += 1
            date_tier_counts[date_str]['total'] += 1

    if not date_tier_counts:
        return {
            'setting_distribution': {},
            'uses_setting_6': False,
            'setting_6_frequency': 0.0,
            'setting_6_per_day': 0.0,
            'typical_high_count': {'avg': 0, 'min': 0, 'max': 0, 'samples': 0},
            'setting_style': 'tight',
            'style_confidence': 0.0,
        }

    n_days = len(date_tier_counts)

    # --- uses_setting_6: 設定6域が定期的に出るか ---
    days_with_6 = sum(1 for d in date_tier_counts.values() if d[6] > 0)
    setting_6_frequency = days_with_6 / n_days if n_days > 0 else 0.0
    # 「定期的」= 30%以上の日で6域が出る
    uses_setting_6 = setting_6_frequency >= 0.3

    # 6域が出る日の平均台数
    count_6_on_6days = [d[6] for d in date_tier_counts.values() if d[6] > 0]
    setting_6_per_day = sum(count_6_on_6days) / len(count_6_on_6days) if count_6_on_6days else 0.0

    # --- typical_high_count: 日別の設定4以上台数 ---
    daily_high_counts = []
    for d in date_tier_counts.values():
        high = d[6] + d[5] + d[4]
        daily_high_counts.append(high)

    avg_high = sum(daily_high_counts) / len(daily_high_counts) if daily_high_counts else 0
    min_high = min(daily_high_counts) if daily_high_counts else 0
    max_high = max(daily_high_counts) if daily_high_counts else 0

    # --- setting_style 判定 ---
    #
    # heavy_hitter: 6を1-2台入れて目立たせる
    #   → 6域が頻繁に出る AND 6域の台数が少ない(1-2) AND 5域が少ない
    #
    # spread: 4-5を複数台に分散
    #   → 6域が少ないor無い AND 設定4以上が台数の30%以上に分散
    #
    # mixed: 両方やる
    #   → 6域も出つつ、4-5も複数台に入る
    #
    # tight: 4以上が少ない（渋い店）
    #   → 設定4以上が少ない

    # 島の総台数（最頻の total を使用）
    total_counts = [d['total'] for d in date_tier_counts.values()]
    typical_total = max(set(total_counts), key=total_counts.count) if total_counts else 1

    # 各日の設定分布パターンを統計
    has_heavy_days = 0    # 6が1-2台入り、5以下は控えめな日
    has_spread_days = 0   # 4以上が30%以上に分散している日
    tight_days = 0        # 4以上が少ない日

    for d in date_tier_counts.values():
        total = d['total']
        if total == 0:
            continue

        high_count = d[6] + d[5] + d[4]
        high_ratio = high_count / total

        if d[6] >= 1 and d[6] <= 2 and d[5] <= 2:
            has_heavy_days += 1
        if high_ratio >= 0.3 and high_count >= 3 and d[6] <= 1:
            has_spread_days += 1
        if high_ratio < 0.15:
            tight_days += 1

    heavy_ratio = has_heavy_days / n_days if n_days > 0 else 0
    spread_ratio = has_spread_days / n_days if n_days > 0 else 0
    tight_ratio = tight_days / n_days if n_days > 0 else 0

    style_confidence = _confidence(n_days, 7)

    if tight_ratio >= 0.5:
        setting_style = 'tight'
    elif heavy_ratio >= 0.3 and spread_ratio >= 0.3:
        setting_style = 'mixed'
    elif heavy_ratio >= 0.3:
        setting_style = 'heavy_hitter'
    elif spread_ratio >= 0.3:
        setting_style = 'spread'
    elif avg_high / typical_total >= 0.25 if typical_total > 0 else False:
        setting_style = 'spread'
    else:
        setting_style = 'tight'

    return {
        'setting_distribution': {
            date: {str(k): v for k, v in counts.items()}
            for date, counts in sorted(date_tier_counts.items())
        },
        'uses_setting_6': uses_setting_6,
        'setting_6_frequency': round(setting_6_frequency, 3),
        'setting_6_per_day': round(setting_6_per_day, 2),
        'typical_high_count': {
            'avg': round(avg_high, 1),
            'min': min_high,
            'max': max_high,
            'samples': n_days,
        },
        'setting_style': setting_style,
        'style_confidence': round(style_confidence, 2),
    }


# =============================================================================
# パターン分析: 日程パターン
# =============================================================================

def _analyze_date_patterns(all_histories: List[dict], machine_key: str) -> dict:
    """曜日・特定日・月内位置の好調率を分析"""
    # 曜日別集計
    weekday_good = {i: 0 for i in range(7)}
    weekday_total = {i: 0 for i in range(7)}

    # 特定日集計
    special_good = {k: 0 for k in SPECIAL_DAY_GROUPS}
    special_total = {k: 0 for k in SPECIAL_DAY_GROUPS}

    # 月内位置別集計
    position_good = {'start': 0, 'mid': 0, 'end': 0}
    position_total = {'start': 0, 'mid': 0, 'end': 0}

    for hist in all_histories:
        days = hist.get('days', [])
        for day in days:
            date_str = day.get('date', '')
            if not date_str:
                continue
            active = _is_active_day(day, machine_key)
            if not active:
                continue

            good = _is_good_day(day, machine_key)

            try:
                dt = datetime.strptime(date_str, '%Y-%m-%d')
            except ValueError:
                continue

            # 曜日
            wd = dt.weekday()
            weekday_total[wd] += 1
            if good:
                weekday_good[wd] += 1

            # 特定日
            dom = dt.day  # day of month
            for group_key, group_days in SPECIAL_DAY_GROUPS.items():
                if dom in group_days:
                    special_total[group_key] += 1
                    if good:
                        special_good[group_key] += 1

            # 月内位置
            if dom <= 10:
                pos = 'start'
            elif dom <= 20:
                pos = 'mid'
            else:
                pos = 'end'
            position_total[pos] += 1
            if good:
                position_good[pos] += 1

    # 全体好調率（ベースライン）を算出
    total_all = sum(weekday_total.values())
    good_all = sum(weekday_good.values())
    baseline_rate = good_all / total_all if total_all > 0 else 0.0

    # 曜日別好調率
    weekday_rates = {}
    for i in range(7):
        n = weekday_total[i]
        rate = weekday_good[i] / n if n > 0 else 0.0
        weekday_rates[WEEKDAY_NAMES[i]] = {
            'rate': round(rate, 3),
            'samples': n,
            'confidence': round(_confidence(n, 7), 2),
            'vs_baseline': round(rate - baseline_rate, 3) if n > 0 else 0.0,
        }

    # 特定日好調率
    special_day_rates = {}
    for group_key in SPECIAL_DAY_GROUPS:
        n = special_total[group_key]
        rate = special_good[group_key] / n if n > 0 else 0.0
        special_day_rates[group_key] = {
            'rate': round(rate, 3),
            'samples': n,
            'confidence': round(_confidence(n, 5), 2),
            'vs_baseline': round(rate - baseline_rate, 3) if n > 0 else 0.0,
        }

    # 月内位置別
    month_position_rates = {}
    for pos in ['start', 'mid', 'end']:
        n = position_total[pos]
        rate = position_good[pos] / n if n > 0 else 0.0
        month_position_rates[pos] = {
            'rate': round(rate, 3),
            'samples': n,
            'confidence': round(_confidence(n, 7), 2),
            'vs_baseline': round(rate - baseline_rate, 3) if n > 0 else 0.0,
        }

    return {
        'weekday_rates': weekday_rates,
        'special_day_rates': special_day_rates,
        'month_position_rates': month_position_rates,
        'baseline_rate': round(baseline_rate, 3),
        'total_samples': total_all,
    }


# =============================================================================
# パターン分析: 台番パターン
# =============================================================================

def _analyze_unit_number_patterns(all_histories: List[dict], machine_key: str) -> dict:
    """台番末尾別・グループ別の好調率を分析"""
    # 末尾別集計
    digit_good = {i: 0 for i in range(10)}
    digit_total = {i: 0 for i in range(10)}

    # 台番を数値順ソートしてグループ分け
    unit_ids_numeric = []
    unit_day_data = {}  # {unit_id: [(date, is_good), ...]}

    for hist in all_histories:
        uid = str(hist.get('unit_id', ''))
        days = hist.get('days', [])
        if not uid or not days:
            continue

        try:
            uid_num = int(uid)
        except ValueError:
            uid_num = 0
        unit_ids_numeric.append((uid, uid_num))

        for day in days:
            if not _is_active_day(day, machine_key):
                continue
            good = _is_good_day(day, machine_key)

            # 末尾
            last_digit = uid_num % 10
            digit_total[last_digit] += 1
            if good:
                digit_good[last_digit] += 1

            if uid not in unit_day_data:
                unit_day_data[uid] = []
            unit_day_data[uid].append(good)

    # 全体ベースライン
    total_all = sum(digit_total.values())
    good_all = sum(digit_good.values())
    baseline = good_all / total_all if total_all > 0 else 0.0

    # 末尾別好調率
    digit_rates = {}
    for i in range(10):
        n = digit_total[i]
        rate = digit_good[i] / n if n > 0 else 0.0
        digit_rates[i] = {
            'rate': round(rate, 3),
            'samples': n,
            'confidence': round(_confidence(n, 7), 2),
            'vs_baseline': round(rate - baseline, 3) if n > 0 else 0.0,
        }

    # 位置別: 島の先頭台・末尾台・中間台
    # 台番を数値順ソートして、先頭/末尾/中間に分類
    unit_ids_numeric.sort(key=lambda x: x[1])
    n_units = len(unit_ids_numeric)

    position_good = {'first': 0, 'last': 0, 'middle': 0}
    position_total = {'first': 0, 'last': 0, 'middle': 0}

    if n_units > 0:
        first_uid = unit_ids_numeric[0][0]
        last_uid = unit_ids_numeric[-1][0]

        for uid, uid_num in unit_ids_numeric:
            data = unit_day_data.get(uid, [])
            if not data:
                continue

            if uid == first_uid:
                pos = 'first'
            elif uid == last_uid:
                pos = 'last'
            else:
                pos = 'middle'

            for is_good in data:
                position_total[pos] += 1
                if is_good:
                    position_good[pos] += 1

    position_rates = {}
    for pos in ['first', 'last', 'middle']:
        n = position_total[pos]
        rate = position_good[pos] / n if n > 0 else 0.0
        position_rates[pos] = {
            'rate': round(rate, 3),
            'samples': n,
            'confidence': round(_confidence(n, 5), 2),
            'vs_baseline': round(rate - baseline, 3) if n > 0 else 0.0,
        }

    return {
        'digit_rates': digit_rates,
        'position_rates': position_rates,
    }


# =============================================================================
# メイン分析関数
# =============================================================================

# パターン分析結果キャッシュ（同一セッション内で再利用）
_pattern_cache: Dict[str, dict] = {}


def analyze_store_patterns(store_key: str, machine_key: str) -> dict:
    """店舗の設定投入パターンを過去データから分析

    Args:
        store_key: 店舗キー（例: 'shibuya_espass_hokuto'）
        machine_key: 機種キー（例: 'hokuto2'）

    Returns:
        {
            'setting_movement': { carry_over_rate, promotion_rate, ... },
            'island_wave': { inverse_correlation, ... },
            'date_patterns': { weekday_rates, special_day_rates, month_position_rates, ... },
            'unit_number_patterns': { digit_rates, position_rates },
            'setting_grade': { setting_distribution, uses_setting_6, setting_style, ... },
            'meta': { store_key, machine_key, total_units, total_days }
        }
    """
    cache_key = f'{store_key}:{machine_key}'
    if cache_key in _pattern_cache:
        return _pattern_cache[cache_key]

    all_histories = _load_all_unit_histories(store_key)
    if not all_histories:
        result = _empty_patterns(store_key, machine_key)
        _pattern_cache[cache_key] = result
        return result

    setting_movement = _analyze_setting_movement(all_histories, machine_key)
    island_wave = _analyze_island_wave(all_histories, machine_key)
    date_patterns = _analyze_date_patterns(all_histories, machine_key)
    unit_number_patterns = _analyze_unit_number_patterns(all_histories, machine_key)
    setting_grade = _analyze_setting_grade(all_histories, machine_key)

    # メタデータ
    total_units = len(all_histories)
    total_days = sum(len(h.get('days', [])) for h in all_histories)

    result = {
        'setting_movement': setting_movement,
        'island_wave': island_wave,
        'date_patterns': date_patterns,
        'unit_number_patterns': unit_number_patterns,
        'setting_grade': setting_grade,
        'meta': {
            'store_key': store_key,
            'machine_key': machine_key,
            'total_units': total_units,
            'total_days': total_days,
        },
    }

    _pattern_cache[cache_key] = result
    return result


def _empty_patterns(store_key: str, machine_key: str) -> dict:
    """データなし時の空パターン"""
    return {
        'setting_movement': {
            'carry_over_rate': 0, 'carry_over_samples': 0,
            'promotion_rate': 0, 'promotion_samples': 0,
            'max_bad_streak': 0, 'avg_bad_before_promotion': 0, 'bad_streak_count': 0,
            'max_good_streak': 0, 'avg_good_before_demotion': 0, 'good_streak_count': 0,
        },
        'island_wave': {'inverse_correlation': 0, 'samples': 0, 'daily_good_counts': {}},
        'date_patterns': {
            'weekday_rates': {}, 'special_day_rates': {},
            'month_position_rates': {}, 'baseline_rate': 0, 'total_samples': 0,
        },
        'unit_number_patterns': {'digit_rates': {}, 'position_rates': {}},
        'setting_grade': {
            'setting_distribution': {}, 'uses_setting_6': False,
            'setting_6_frequency': 0, 'setting_6_per_day': 0,
            'typical_high_count': {'avg': 0, 'min': 0, 'max': 0, 'samples': 0},
            'setting_style': 'tight', 'style_confidence': 0,
        },
        'meta': {'store_key': store_key, 'machine_key': machine_key, 'total_units': 0, 'total_days': 0},
    }


# =============================================================================
# ボーナス計算
# =============================================================================

def calculate_pattern_bonus(store_key: str, machine_key: str,
                            unit_id, target_date: str) -> float:
    """店舗パターンから対象台・対象日のスコアボーナスを計算

    Args:
        store_key: 店舗キー
        machine_key: 機種キー
        unit_id: 台番号（str or int）
        target_date: 対象日（'YYYY-MM-DD' 形式）

    Returns:
        ボーナス値（-15 〜 +15）
    """
    patterns = analyze_store_patterns(store_key, machine_key)
    meta = patterns.get('meta', {})

    # データが全くない場合は0
    if meta.get('total_days', 0) == 0:
        return 0.0

    unit_id_str = str(unit_id)
    try:
        target_dt = datetime.strptime(target_date, '%Y-%m-%d')
    except ValueError:
        return 0.0

    bonus = 0.0

    # --- 1. 据え置き / 不調→設定変更期待ボーナス ---
    bonus += _calc_movement_bonus(patterns, store_key, machine_key, unit_id_str, target_dt)

    # --- 2. 島の波ボーナス ---
    bonus += _calc_island_wave_bonus(patterns, target_dt)

    # --- 3. 特定日ボーナス ---
    bonus += _calc_special_day_bonus(patterns, target_dt)

    # --- 4. 曜日ボーナス（店舗固有の差分のみ） ---
    bonus += _calc_weekday_bonus(patterns, target_dt)

    # --- 5. 台番パターンボーナス ---
    bonus += _calc_unit_number_bonus(patterns, unit_id_str)

    # --- 6. 月内位置ボーナス ---
    bonus += _calc_month_position_bonus(patterns, target_dt)

    # --- 7. 設定段階ボーナス ---
    bonus += _calc_setting_grade_bonus(patterns, store_key, machine_key, unit_id_str, target_dt)

    # クリップ -15 〜 +15
    return round(max(-15.0, min(15.0, bonus)), 1)


def _calc_movement_bonus(patterns: dict, store_key: str, machine_key: str,
                         unit_id_str: str, target_dt: datetime) -> float:
    """据え置き・不調→設定変更期待ボーナス"""
    sm = patterns.get('setting_movement', {})
    bonus = 0.0

    # 台の直近履歴を取得
    unit_hist = _load_unit_history(store_key, unit_id_str)
    if not unit_hist:
        return 0.0

    days = sorted(unit_hist.get('days', []), key=lambda d: d.get('date', ''))
    if not days:
        return 0.0

    # target_date の前日までのデータを使う
    prev_date_str = (target_dt - timedelta(days=1)).strftime('%Y-%m-%d')
    recent_days = [d for d in days if d.get('date', '') <= prev_date_str]
    if not recent_days:
        return 0.0

    latest_day = recent_days[-1]
    latest_date = latest_day.get('date', '')

    # 連続日チェック（前日のデータがあるか）
    try:
        latest_dt = datetime.strptime(latest_date, '%Y-%m-%d')
        days_gap = (target_dt - latest_dt).days
    except ValueError:
        return 0.0

    if days_gap > 3:
        # データが3日以上前 → 古すぎて意味がない
        return 0.0

    prev_good = _is_good_day(latest_day, machine_key)
    prev_bad = _is_bad_day(latest_day, machine_key)

    # --- 据え置きボーナス ---
    # 前日好調 × 店の据え置き率が高い → +(前日の分は既存スコアに含まれるが、
    # 「この店は据え置きしやすい」という店固有の癖を補正)
    carry_rate = sm.get('carry_over_rate', 0)
    carry_conf = _confidence(sm.get('carry_over_samples', 0))

    if prev_good and carry_rate > 0.3:
        # 据え置き率 30%超えたらボーナス、最大 WEIGHT 分
        # 据え置き率50%なら最大ボーナス、30%でゼロ
        rate_factor = min((carry_rate - 0.3) / 0.3, 1.0)  # 0〜1
        bonus += rate_factor * WEIGHT_CARRYOVER * carry_conf

    # --- 不調→設定変更期待ボーナス ---
    if prev_bad:
        # 不調が何日続いているかカウント
        bad_streak = 0
        for d in reversed(recent_days):
            if _is_bad_day(d, machine_key):
                bad_streak += 1
            else:
                break

        max_bad = sm.get('max_bad_streak', 7)
        avg_bad = sm.get('avg_bad_before_promotion', 3)
        promo_rate = sm.get('promotion_rate', 0)
        promo_conf = _confidence(sm.get('promotion_samples', 0))

        if max_bad > 0 and bad_streak > 0:
            # 不調放置上限に近づくほどボーナス増
            # bad_streak / max_bad が 0.5 以上で効き始め、1.0 で最大
            streak_ratio = bad_streak / max_bad
            if streak_ratio >= 0.5:
                streak_factor = min((streak_ratio - 0.5) / 0.5, 1.0)
                # 投入率も考慮
                promo_factor = min(promo_rate / 0.3, 1.0) if promo_rate > 0 else 0.5
                bonus += streak_factor * promo_factor * WEIGHT_SLUMP_CHANGE * promo_conf

        # 平均不調日数に近い場合の補正
        if avg_bad > 0 and bad_streak >= avg_bad:
            avg_conf = _confidence(sm.get('bad_streak_count', 0), 5)
            bonus += 1.5 * avg_conf  # 平均到達で小ボーナス

    # --- 好調連続からの下降期待 ---
    # 好調が長く続きすぎている → そろそろ下げる
    if prev_good:
        good_streak = 0
        for d in reversed(recent_days):
            if _is_good_day(d, machine_key):
                good_streak += 1
            else:
                break

        max_good = sm.get('max_good_streak', 7)
        avg_good = sm.get('avg_good_before_demotion', 3)

        if max_good > 0 and good_streak > 0:
            streak_ratio = good_streak / max_good
            if streak_ratio >= 0.7:
                # 好調が上限に近い → 下げリスク → マイナスボーナス
                down_factor = min((streak_ratio - 0.7) / 0.3, 1.0)
                good_conf = _confidence(sm.get('good_streak_count', 0), 5)
                bonus -= down_factor * WEIGHT_GOOD_STREAK * good_conf

    return bonus


def _calc_island_wave_bonus(patterns: dict, target_dt: datetime) -> float:
    """島の波ボーナス: 前日の島全体が絞られていたら翌日期待"""
    wave = patterns.get('island_wave', {})
    inv_corr = wave.get('inverse_correlation', 0)
    wave_conf = _confidence(wave.get('samples', 0), 5)

    if inv_corr <= 0 or wave_conf == 0:
        return 0.0

    # 前日の好調率を確認
    prev_date_str = (target_dt - timedelta(days=1)).strftime('%Y-%m-%d')
    daily_counts = wave.get('daily_good_counts', {})
    prev_data = daily_counts.get(prev_date_str, {})

    if not prev_data:
        return 0.0

    prev_rate = prev_data.get('rate', 0)
    baseline = patterns.get('date_patterns', {}).get('baseline_rate', 0.3)

    if prev_rate < baseline * 0.7:
        # 前日が平均の70%以下 → 翌日増やす期待
        deficit_factor = min((baseline - prev_rate) / baseline, 1.0) if baseline > 0 else 0
        return deficit_factor * inv_corr * WEIGHT_ISLAND_WAVE * wave_conf

    return 0.0


def _calc_special_day_bonus(patterns: dict, target_dt: datetime) -> float:
    """特定日ボーナス"""
    dp = patterns.get('date_patterns', {})
    special = dp.get('special_day_rates', {})
    baseline = dp.get('baseline_rate', 0)

    if not special:
        return 0.0

    dom = target_dt.day
    bonus = 0.0

    for group_key, group_days in SPECIAL_DAY_GROUPS.items():
        if dom in group_days:
            info = special.get(group_key, {})
            if not info:
                # キーが文字列・整数両方の可能性
                info = special.get(str(group_key), {})
            if not info:
                continue

            vs_baseline = info.get('vs_baseline', 0)
            conf = info.get('confidence', 0)

            if vs_baseline != 0:
                # ベースラインとの差分 × 重み × 信頼度
                # vs_baseline が +0.2 (20%ポイント高い) なら最大ボーナス
                factor = min(abs(vs_baseline) / 0.15, 1.0)
                sign = 1 if vs_baseline > 0 else -1
                bonus += sign * factor * WEIGHT_SPECIAL_DAY * conf

    return bonus


def _calc_weekday_bonus(patterns: dict, target_dt: datetime) -> float:
    """曜日ボーナス（店舗固有の差分のみ、既存weekday_bonusと差別化）

    既存: 全店共通の曜日レーティング (1-5) → ±6
    本モジュール: 店舗固有のデータ実績 vs ベースラインの差分
    → 既存と二重にならないよう、vs_baseline の符号が既存と同じ方向なら
      既に反映済みとしてスキップし、反対方向の場合のみ補正。
      ただし、データが十分にある場合はデータを優先。
    """
    dp = patterns.get('date_patterns', {})
    weekday_rates = dp.get('weekday_rates', {})

    if not weekday_rates:
        return 0.0

    wd = target_dt.weekday()
    wd_name = WEEKDAY_NAMES[wd]
    info = weekday_rates.get(wd_name, {})
    if not info:
        return 0.0

    vs_baseline = info.get('vs_baseline', 0)
    conf = info.get('confidence', 0)

    if vs_baseline == 0 or conf == 0:
        return 0.0

    # 差分の大きさに応じてボーナス（最大 WEIGHT_WEEKDAY）
    # vs_baseline +0.15 で最大
    factor = min(abs(vs_baseline) / 0.15, 1.0)
    sign = 1 if vs_baseline > 0 else -1
    return sign * factor * WEIGHT_WEEKDAY * conf


def _calc_unit_number_bonus(patterns: dict, unit_id_str: str) -> float:
    """台番末尾別ボーナス"""
    unp = patterns.get('unit_number_patterns', {})
    digit_rates = unp.get('digit_rates', {})

    if not digit_rates:
        return 0.0

    try:
        uid_num = int(unit_id_str)
    except ValueError:
        return 0.0

    last_digit = uid_num % 10
    info = digit_rates.get(last_digit, {})
    if not info:
        info = digit_rates.get(str(last_digit), {})
    if not info:
        return 0.0

    vs_baseline = info.get('vs_baseline', 0)
    conf = info.get('confidence', 0)

    if vs_baseline == 0 or conf == 0:
        return 0.0

    factor = min(abs(vs_baseline) / 0.1, 1.0)
    sign = 1 if vs_baseline > 0 else -1
    return sign * factor * WEIGHT_DIGIT * conf


def _calc_month_position_bonus(patterns: dict, target_dt: datetime) -> float:
    """月内位置ボーナス"""
    dp = patterns.get('date_patterns', {})
    mp_rates = dp.get('month_position_rates', {})

    if not mp_rates:
        return 0.0

    dom = target_dt.day
    if dom <= 10:
        pos = 'start'
    elif dom <= 20:
        pos = 'mid'
    else:
        pos = 'end'

    info = mp_rates.get(pos, {})
    if not info:
        return 0.0

    vs_baseline = info.get('vs_baseline', 0)
    conf = info.get('confidence', 0)

    if vs_baseline == 0 or conf == 0:
        return 0.0

    factor = min(abs(vs_baseline) / 0.1, 1.0)
    sign = 1 if vs_baseline > 0 else -1
    return sign * factor * 1.0 * conf  # 重み 1.0


def _calc_setting_grade_bonus(patterns: dict, store_key: str, machine_key: str,
                              unit_id_str: str, target_dt: datetime) -> float:
    """設定段階に基づくボーナス

    - 「6を使う店」で前日6域だった台 → 据え置き期待（6は目玉だから連日使う）
    - 「spread型の店」→ 前日不調台への投入期待が高い（分散で毎日違う台に入れる）
    - 「tight店」→ 全体的にスコアを控えめに
    """
    sg = patterns.get('setting_grade', {})
    style = sg.get('setting_style', 'tight')
    style_conf = sg.get('style_confidence', 0)

    if style_conf == 0:
        return 0.0

    bonus = 0.0

    # 台の直近履歴を取得
    unit_hist = _load_unit_history(store_key, unit_id_str)
    prev_date_str = (target_dt - timedelta(days=1)).strftime('%Y-%m-%d')

    prev_tier = 0
    prev_bad = False
    if unit_hist:
        days = sorted(unit_hist.get('days', []), key=lambda d: d.get('date', ''))
        recent_days = [d for d in days if d.get('date', '') <= prev_date_str]
        if recent_days:
            latest_day = recent_days[-1]
            latest_date = latest_day.get('date', '')
            try:
                gap = (target_dt - datetime.strptime(latest_date, '%Y-%m-%d')).days
            except ValueError:
                gap = 99
            if gap <= 3:
                prev_tier = _estimate_setting_tier(latest_day, machine_key)
                prev_bad = _is_bad_day(latest_day, machine_key)

    uses_6 = sg.get('uses_setting_6', False)

    # --- スタイル別ボーナス ---
    # 各スタイルが設定6据え置き・投入期待を自前で処理する（二重計算回避）

    if style == 'heavy_hitter':
        # heavy_hitter: 6を目玉にする → 6の据え置き期待が最も強い
        if prev_tier == 6:
            bonus += 1.2 * WEIGHT_SETTING6_CARRY * style_conf
        elif prev_tier == 5:
            bonus += 0.4 * WEIGHT_SETTING6_CARRY * style_conf

    elif style == 'spread':
        # spread: 4-5を分散投入、6もたまに
        # 前日6域は据え置き期待あり（ただし heavy_hitter ほど強くない）
        if uses_6 and prev_tier == 6:
            freq_factor = min(sg.get('setting_6_frequency', 0) / 0.5, 1.0)
            bonus += 0.7 * freq_factor * WEIGHT_SETTING6_CARRY * style_conf
        elif uses_6 and prev_tier == 5:
            bonus += 0.3 * WEIGHT_SETTING6_CARRY * style_conf

        # 分散型の店は前日不調台への投入期待が高い
        if prev_bad:
            avg_high = sg.get('typical_high_count', {}).get('avg', 0)
            total_units = patterns.get('meta', {}).get('total_units', 1)
            high_ratio = avg_high / total_units if total_units > 0 else 0
            spread_factor = min(high_ratio / 0.4, 1.0)  # 40%で最大
            bonus += spread_factor * WEIGHT_SPREAD_PROMO * style_conf

    elif style == 'mixed':
        # mixed: 6の据え置き + spread の両方を少し加味
        if uses_6 and prev_tier == 6:
            bonus += 0.8 * WEIGHT_SETTING6_CARRY * style_conf
        elif uses_6 and prev_tier == 5:
            bonus += 0.3 * WEIGHT_SETTING6_CARRY * style_conf

        if prev_bad:
            avg_high = sg.get('typical_high_count', {}).get('avg', 0)
            total_units = patterns.get('meta', {}).get('total_units', 1)
            high_ratio = avg_high / total_units if total_units > 0 else 0
            spread_factor = min(high_ratio / 0.4, 1.0)
            bonus += 0.5 * spread_factor * WEIGHT_SPREAD_PROMO * style_conf

    elif style == 'tight':
        # tight: 渋い店 → 全体的にスコアを控えめに
        bonus += WEIGHT_TIGHT_PENALTY * style_conf
        # それでも6域が出ていた場合は少しだけ期待
        if uses_6 and prev_tier == 6:
            bonus += 0.4 * WEIGHT_SETTING6_CARRY * style_conf

    return bonus


def clear_cache():
    """パターンキャッシュをクリア（テスト用）"""
    _pattern_cache.clear()


# =============================================================================
# テスト用エントリポイント
# =============================================================================

if __name__ == '__main__':
    import pprint

    print('=' * 60)
    print('店舗設定投入パターン分析テスト')
    print('=' * 60)

    # 全店舗のパターンをテスト
    test_cases = [
        ('shibuya_espass_hokuto', 'hokuto2'),
        ('shibuya_espass_sbj', 'sbj'),
        ('shinjuku_espass_hokuto', 'hokuto2'),
        ('island_akihabara_sbj', 'sbj'),
    ]

    for store_key, machine_key in test_cases:
        print(f'\n--- {store_key} ({machine_key}) ---')
        patterns = analyze_store_patterns(store_key, machine_key)
        meta = patterns['meta']
        sm = patterns['setting_movement']
        dp = patterns['date_patterns']
        sg = patterns['setting_grade']
        print(f'  台数: {meta["total_units"]}, データ日数: {meta["total_days"]}')
        print(f'  据え置き率: {sm["carry_over_rate"]} (n={sm["carry_over_samples"]})')
        print(f'  不調→投入率: {sm["promotion_rate"]} (n={sm["promotion_samples"]})')
        print(f'  不調放置上限: {sm["max_bad_streak"]}日')
        print(f'  好調連続上限: {sm["max_good_streak"]}日')
        print(f'  島波逆相関: {patterns["island_wave"]["inverse_correlation"]}')
        print(f'  ベースライン好調率: {dp["baseline_rate"]}')
        print(f'  曜日別好調率:')
        for wd_name, info in dp.get('weekday_rates', {}).items():
            print(f'    {wd_name}: {info["rate"]:.3f} (n={info["samples"]}, vs={info["vs_baseline"]:+.3f})')
        # 設定段階分析
        print(f'  --- 設定段階分析 ---')
        print(f'  設定6使用: {sg["uses_setting_6"]} (頻度: {sg["setting_6_frequency"]:.1%}, 出現日平均: {sg["setting_6_per_day"]:.1f}台)')
        thc = sg['typical_high_count']
        print(f'  設定4以上台数: 平均{thc["avg"]:.1f}台 (最小{thc["min"]}, 最大{thc["max"]}, {thc["samples"]}日)')
        print(f'  設定スタイル: {sg["setting_style"]} (信頼度: {sg["style_confidence"]})')
        # 日別分布サマリ
        dist = sg.get('setting_distribution', {})
        if dist:
            print(f'  日別設定分布 (直近):')
            for date_str in sorted(dist.keys())[-5:]:
                d = dist[date_str]
                print(f'    {date_str}: 6域={d.get("6",0)} 5域={d.get("5",0)} 4域={d.get("4",0)} 3以下={d.get("3",0)} (計{d.get("total",0)})')

    # ボーナス計算テスト
    print('\n' + '=' * 60)
    print('ボーナス計算テスト')
    print('=' * 60)

    bonus_tests = [
        ('shibuya_espass_hokuto', 'hokuto2', 2233, '2026-01-28'),
        ('shibuya_espass_hokuto', 'hokuto2', 2046, '2026-01-28'),
        ('shibuya_espass_sbj', 'sbj', 3011, '2026-01-28'),
        ('island_akihabara_sbj', 'sbj', 1023, '2026-01-28'),
    ]

    for store_key, machine_key, uid, date in bonus_tests:
        bonus = calculate_pattern_bonus(store_key, machine_key, uid, date)
        print(f'  {store_key} / {uid} / {date} → bonus: {bonus:+.1f}')
