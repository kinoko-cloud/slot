#!/usr/bin/env python3
"""
リアルタイム台推奨システム

過去データ（静的ランキング）+ 当日データを組み合わせて
今打つべき台を推奨する

根拠データ：
- 過去7日間のトレンド（連続プラス/マイナス）
- 前日凹み→翌日狙い目パターン
- 当日の他台との稼働比較
- ART確率の推移
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.rankings import STORES, RANKINGS, get_rank, get_unit_ranking, MACHINES, get_machine_threshold, rank_up, rank_down
from analysis.analyzer import calculate_at_intervals, calculate_current_at_games, calculate_max_rensa

# 機種別の設定情報
# SBJ: 設定1=1/241.7(97.8%), 設定6=1/181.3(112.7%)
# 北斗転生2: 設定1=1/366.0(97.6%), 設定6=1/273.1(114.9%)
MACHINE_SPECS = {
    'sbj': {
        'setting6_at_prob': 181.3,
        'setting1_at_prob': 241.7,
        'setting6_payout': 112.7,
        'setting1_payout': 97.8,
        # 閾値（表示用）
        'excellent_prob': 80,   # 設定6超え
        'high_prob': 100,       # 高設定域
        'mid_prob': 130,        # 中間設定域
        'low_prob': 180,        # 低設定域境界
        'very_low_prob': 250,   # 低設定域
    },
    'hokuto_tensei2': {
        'setting6_at_prob': 273.1,
        'setting1_at_prob': 366.0,
        'setting6_payout': 114.9,
        'setting1_payout': 97.6,
        'excellent_prob': 250,
        'high_prob': 290,
        'mid_prob': 330,
        'low_prob': 366,
        'very_low_prob': 450,
    },
}

# 後方互換性のため
MACHINE_THRESHOLDS = {
    'sbj': {
        'setting6_at_prob': 80,
        'high_at_prob': 100,
        'mid_at_prob': 130,
        'low_at_prob': 180,
        'very_low_at_prob': 250,
    },
    'hokuto_tensei2': {
        'setting6_at_prob': 273,
        'high_at_prob': 300,
        'mid_at_prob': 340,
        'low_at_prob': 366,
        'very_low_at_prob': 450,
    },
}


def estimate_setting_from_prob(art_prob: float, machine_key: str = 'sbj') -> dict:
    """ART確率から設定を推定し、期待差枚を計算

    Returns:
        {
            'estimated_setting': str,  # '高設定濃厚', '高設定域', '中間', '低設定域'
            'payout_estimate': float,  # 推定機械割
            'hourly_expected': int,    # 1時間あたり期待差枚
            'confidence': str,         # 'high', 'medium', 'low'
        }
    """
    specs = MACHINE_SPECS.get(machine_key, MACHINE_SPECS['sbj'])

    if art_prob <= 0:
        return {
            'estimated_setting': '不明',
            'setting_num': 0,
            'payout_estimate': 100.0,
            'hourly_expected': 0,
            'confidence': 'none',
        }

    # 設定6と設定1のART確率から機械割を線形補間
    s6_prob = specs['setting6_at_prob']
    s1_prob = specs['setting1_at_prob']
    s6_payout = specs['setting6_payout']
    s1_payout = specs['setting1_payout']

    # ART確率が設定6より良い場合
    if art_prob <= s6_prob:
        payout = s6_payout + (s6_prob - art_prob) * 0.1  # さらに上乗せ
        setting = '設定6'
        setting_num = 6
        confidence = 'high'
    # 設定6〜設定1の間
    elif art_prob <= s1_prob:
        ratio = (s1_prob - art_prob) / (s1_prob - s6_prob)
        payout = s1_payout + (s6_payout - s1_payout) * ratio
        # ratioを設定番号に変換（1.0→6, 0.0→1）
        setting_num = round(1 + ratio * 5)
        setting_num = max(1, min(6, setting_num))  # 1-6にクランプ
        setting = f'設定{setting_num}'
        if ratio >= 0.8:
            confidence = 'high'
        elif ratio >= 0.5:
            confidence = 'medium'
        else:
            confidence = 'low'
    # 設定1より悪い場合
    else:
        payout = s1_payout - (art_prob - s1_prob) * 0.05
        setting = '設定1'
        setting_num = 1
        confidence = 'low'

    # 1時間あたりの期待差枚（700G/時間 × 3枚/G × (機械割-100%)/100）
    hourly_games = 700
    hourly_expected = int(hourly_games * 3 * (payout - 100) / 100)

    return {
        'estimated_setting': setting,
        'setting_num': setting_num,
        'payout_estimate': round(payout, 1),
        'hourly_expected': hourly_expected,
        'confidence': confidence,
    }


def calculate_expected_profit(total_games: int, art_count: int, machine_key: str = 'sbj') -> dict:
    """現在のデータから期待差枚を計算

    Returns:
        {
            'current_estimate': int,      # 現在の推定差枚
            'closing_estimate': int,      # 閉店時の推定差枚
            'remaining_hours': float,     # 残り時間
            'profit_category': str,       # '5000枚+', '3000枚+', '2000枚+', '1000枚+', 'プラス', 'マイナス'
        }
    """
    now = datetime.now()
    closing_hour = 23  # 閉店時刻

    # 残り時間
    if now.hour >= closing_hour:
        remaining_hours = 0
    else:
        remaining_hours = closing_hour - now.hour - (now.minute / 60)

    # ART確率から設定推定
    art_prob = total_games / art_count if art_count > 0 else 0
    setting_info = estimate_setting_from_prob(art_prob, machine_key)

    # 現在の推定差枚（投入枚数 × (機械割-100%)/100）
    invested = total_games * 3  # 3枚/G
    current_estimate = int(invested * (setting_info['payout_estimate'] - 100) / 100)

    # 閉店までの追加差枚
    additional = int(remaining_hours * setting_info['hourly_expected'])
    closing_estimate = current_estimate + additional

    # カテゴリ分類
    if closing_estimate >= 5000:
        category = '5000枚+'
    elif closing_estimate >= 3000:
        category = '3000枚+'
    elif closing_estimate >= 2000:
        category = '2000枚+'
    elif closing_estimate >= 1000:
        category = '1000枚+'
    elif closing_estimate > 0:
        category = 'プラス'
    elif closing_estimate > -1000:
        category = '微マイナス'
    else:
        category = 'マイナス'

    return {
        'current_estimate': current_estimate,
        'closing_estimate': closing_estimate,
        'remaining_hours': round(remaining_hours, 1),
        'profit_category': category,
        'setting_info': setting_info,
    }

# 店舗キーのマッピング（config -> JSON data）
STORE_KEY_MAPPING = {
    # SBJ
    'island_akihabara_sbj': 'island_akihabara_sbj',
    'island_akihabara': 'island_akihabara_sbj',
    'shibuya_espass_sbj': 'shibuya_espass_sbj',
    'shibuya_espass': 'shibuya_espass_sbj',
    'shinjuku_espass_sbj': 'shinjuku_espass_sbj',
    # 北斗転生2
    'shibuya_espass_hokuto': 'shibuya_espass_hokuto_tensei2',
    'shinjuku_espass_hokuto': 'shinjuku_espass_hokuto_tensei2',
    'akiba_espass_hokuto': 'akiba_espass_hokuto_tensei2',
    'island_akihabara_hokuto': 'island_akihabara_hokuto_tensei2',
}

# 店舗別曜日傾向データ（★評価 1-5）
WEEKDAY_NAMES = ['月', '火', '水', '木', '金', '土', '日']
STORE_DAY_RATINGS = {
    'island_akihabara_sbj': {
        'short_name': 'アイランド秋葉原',
        'day_ratings': {'月': 4, '火': 3, '水': 5, '木': 3, '金': 3, '土': 1, '日': 4},
        'best_days': '水曜が最強、日月も狙い目',
        'worst_days': '土曜は避けるべき',
    },
    'shibuya_espass_sbj': {
        'short_name': 'エスパス渋谷新館',
        'day_ratings': {'月': 3, '火': 4, '水': 4, '木': 5, '金': 3, '土': 3, '日': 1},
        'best_days': '木曜が最強、火水も狙い目',
        'worst_days': '日曜は避けるべき',
    },
    'shinjuku_espass_sbj': {
        'short_name': 'エスパス歌舞伎町',
        'day_ratings': {'月': 2, '火': 3, '水': 3, '木': 3, '金': 4, '土': 5, '日': 3},
        'best_days': '土曜が最強、金曜も狙い目',
        'worst_days': '月曜は控えめ',
    },
    'akiba_espass_sbj': {
        'short_name': 'エスパス秋葉原',
        'day_ratings': {'月': 2, '火': 3, '水': 3, '木': 3, '金': 4, '土': 5, '日': 4},
        'best_days': '土日が狙い目、金曜も可',
        'worst_days': '月曜は控えめ',
    },
    'seibu_shinjuku_espass_sbj': {
        'short_name': 'エスパス西武新宿',
        'day_ratings': {'月': 2, '火': 2, '水': 3, '木': 3, '金': 4, '土': 4, '日': 3},
        'best_days': '金土が狙い目',
        'worst_days': '月火は控えめ',
    },
}


def get_store_weekday_info(store_key: str) -> dict:
    """店舗の今日の曜日傾向を返す"""
    store_info = STORE_DAY_RATINGS.get(store_key, {})
    if not store_info:
        # 同じ店舗の別機種キーを探す（island_akihabara_hokuto → island_akihabara_sbj等）
        base = store_key.rsplit('_', 1)[0] if '_' in store_key else store_key
        for k, v in STORE_DAY_RATINGS.items():
            if k.startswith(base):
                store_info = v
                break
    if not store_info:
        return {}
    today_weekday = WEEKDAY_NAMES[datetime.now().weekday()]
    today_rating = store_info['day_ratings'].get(today_weekday, 3)
    return {
        'short_name': store_info['short_name'],
        'today_weekday': today_weekday,
        'today_rating': today_rating,
        'best_days': store_info['best_days'],
        'worst_days': store_info['worst_days'],
    }


def get_machine_from_store_key(store_key: str) -> str:
    """店舗キーから機種キーを取得"""
    store = STORES.get(store_key)
    if store:
        return store.get('machine', 'sbj')
    # 店舗キーから推測
    if 'hokuto' in store_key:
        return 'hokuto_tensei2'
    return 'sbj'


def get_machine_thresholds(machine_key: str) -> dict:
    """機種別の閾値を取得"""
    return MACHINE_THRESHOLDS.get(machine_key, MACHINE_THRESHOLDS['sbj'])


def load_daily_data(date_str: str = None, machine_key: str = None) -> dict:
    """日別収集データを読み込む

    Args:
        date_str: 日付文字列（YYYYMMDD形式）。Noneの場合は今日
        machine_key: 機種キー（'sbj', 'hokuto_tensei2'）。Noneの場合は全機種を含むファイルを優先

    Returns:
        読み込んだデータ辞書
    """
    if date_str is None:
        date_str = datetime.now().strftime('%Y%m%d')

    data_dir = Path(__file__).parent.parent / 'data' / 'daily'

    # 最新のデータファイルを探す（優先順位順）
    patterns = [
        # 複数機種を含むファイル（最優先）
        f'daily_sbj_hokuto_tensei2_{date_str}.json',
        f'daily_all_{date_str}.json',
        # SBJ専用
        f'daily_sbj_{date_str}.json',
        f'sbj_daily_{date_str}.json',
        # 北斗転生2専用
        f'daily_hokuto_tensei2_{date_str}.json',
    ]

    # 全日付のデータを統合（最新を優先しつつ、古い日付のデータも取り込む）
    merged_data = {'stores': {}}
    found_dates = []

    # 今日 + 直近7日分のデータを全て読み込んで統合
    from datetime import timedelta
    base_date = datetime.strptime(date_str, '%Y%m%d')
    dates_to_check = [date_str] + [(base_date - timedelta(days=d)).strftime('%Y%m%d') for d in range(1, 8)]

    for check_date in dates_to_check:
        found_files = []
        # パターンマッチ
        for pattern in patterns:
            file_path = data_dir / pattern.replace(date_str, check_date)
            if file_path.exists():
                found_files.append(file_path)
        # ワイルドカード
        for wp in [f'daily_*_{check_date}.json', f'*_daily_{check_date}.json']:
            for match in data_dir.glob(wp):
                if match not in found_files:
                    found_files.append(match)

        for file_path in found_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if machine_key:
                    machines = data.get('machines', [])
                    if machines and machine_key not in machines:
                        continue
                # 店舗データを統合（既にある店舗は上書きしない=最新優先）
                for sk, sv in data.get('stores', {}).items():
                    if sk not in merged_data['stores']:
                        merged_data['stores'][sk] = sv
                        found_dates.append(check_date)
                # メタデータをコピー
                for meta_key in ['machines', 'fetched_at', 'data_date']:
                    if meta_key in data and meta_key not in merged_data:
                        merged_data[meta_key] = data[meta_key]
            except Exception:
                pass

    # rawデータで補完（全日付のrawを試行。最新を優先）
    for check_date in dates_to_check:
        merged_data = _merge_raw_data(merged_data, check_date)

    if not merged_data.get('stores'):
        return {}

    return merged_data


def _merge_raw_data(daily_data: dict, date_str: str) -> dict:
    """rawディレクトリのpapimo等のデータをdaily_dataに補完する

    papimo rawデータ（リスト形式）およびdaidata rawデータ（個別ファイル）を
    日別データの形式に変換してマージ
    """
    raw_dir = Path(__file__).parent.parent / 'data' / 'raw'
    if not raw_dir.exists():
        return daily_data

    stores = daily_data.get('stores', {})

    # --- papimo rawデータ（リスト形式）---
    for papimo_pattern, store_key in [
        (f'papimo_island_sbj_{date_str}_*.json', 'island_akihabara_sbj'),
        (f'papimo_island_hokuto_{date_str}_*.json', 'island_akihabara_hokuto_tensei2'),
    ]:
        papimo_files = sorted(raw_dir.glob(papimo_pattern), reverse=True)
        if not papimo_files:
            continue
        try:
            with open(papimo_files[0], 'r', encoding='utf-8') as f:
                raw_units = json.load(f)
            if isinstance(raw_units, list) and raw_units:
                existing = stores.get(store_key, {})
                existing_units = existing.get('units', [])
                existing_days = len(existing_units[0].get('days', [])) if existing_units else 0
                raw_days = len(raw_units[0].get('days', []))
                if raw_days > existing_days:
                    converted_units = []
                    for raw_unit in raw_units:
                        converted_units.append({
                            'unit_id': str(raw_unit.get('unit_id', '')),
                            'days': raw_unit.get('days', []),
                            'machine_key': raw_unit.get('machine_key'),
                        })
                    stores[store_key] = {
                        'units': converted_units,
                        'data_source': 'papimo_raw',
                    }
        except Exception:
            pass

    # --- daidata rawデータ（個別ファイル: sbj_UNITID_history_DATE_TIME.json）---
    # hall_id → store_keyのマッピング
    HALL_STORE_MAP = {
        '100860': {'sbj': 'shibuya_espass_sbj', 'hokuto_tensei2': 'shibuya_espass_hokuto_tensei2'},
        '100949': {'sbj': 'shinjuku_espass_sbj', 'hokuto_tensei2': 'shinjuku_espass_hokuto_tensei2'},
        '100928': {'sbj': 'akiba_espass_sbj', 'hokuto_tensei2': 'akiba_espass_hokuto_tensei2'},
        '100950': {'sbj': 'seibu_shinjuku_espass_sbj', 'hokuto_tensei2': 'seibu_shinjuku_espass_hokuto_tensei2'},
    }
    from collections import defaultdict
    raw_by_store = defaultdict(list)  # store_key -> [unit_data, ...]

    raw_files = sorted(raw_dir.glob(f'sbj_*_history_{date_str}_*.json'))
    for raw_file in raw_files:
        try:
            with open(raw_file, 'r', encoding='utf-8') as f:
                raw_unit = json.load(f)
            if not isinstance(raw_unit, dict):
                continue
            hall_id = str(raw_unit.get('hall_id', ''))
            uid = str(raw_unit.get('unit_id', ''))
            days = raw_unit.get('days', [])
            if not hall_id or not uid or not days:
                continue
            # 機種判定: BB=0なら北斗転生2、BB>0ならSBJ
            bb_total = sum(d.get('bb', 0) for d in days)
            machine_key = 'sbj' if bb_total > 0 else 'hokuto_tensei2'
            hall_map = HALL_STORE_MAP.get(hall_id)
            if not hall_map:
                continue
            store_key = hall_map.get(machine_key)
            if not store_key:
                continue
            raw_by_store[store_key].append({
                'unit_id': uid,
                'days': days,
                'machine_key': machine_key,
            })
        except Exception:
            pass

    # rawデータをstoresにマージ
    for store_key, raw_units in raw_by_store.items():
        existing = stores.get(store_key, {})
        existing_units = existing.get('units', [])
        existing_count = len(existing_units)
        raw_count = len(raw_units)
        # rawの方が台数が多い場合に上書き
        if raw_count > existing_count:
            stores[store_key] = {
                'units': raw_units,
                'data_source': 'daidata_raw',
            }

    daily_data['stores'] = stores
    return daily_data


def calculate_unit_historical_performance(days: List[dict], machine_key: str = 'sbj') -> dict:
    """【改善1】台番号ごとの過去実績（好調率）を計算

    過去の日別データから各台の「好調率」（ART確率が好調域だった日の割合）を算出。
    分析結果: 常に的中する台と常に外れる台で好調率に明確な差がある。

    Args:
        days: 過去日のデータリスト（total_start or games キーに対応）
        machine_key: 機種キー

    Returns:
        {
            'good_day_rate': float,     # 好調日の割合 (0.0-1.0)
            'good_days': int,           # 好調日数
            'total_days': int,          # 有効日数
            'score_bonus': float,       # スコアボーナス (-8 to +10)
            'avg_prob': float,          # 平均ART確率
            'consecutive_bad': int,     # 直近の連続不調日数
        }
    """
    def _get_games(day):
        """gamesフィールド取得 — 蓄積DB(games) or daily JSON(total_start)"""
        return day.get('games', 0) or day.get('total_start', 0)

    # 機種別の好調判定閾値
    good_prob_threshold = get_machine_threshold(machine_key, 'good_prob')
    bad_prob_threshold = get_machine_threshold(machine_key, 'bad_prob')

    good_days = 0
    bad_days = 0
    total_days = 0
    probs = []
    consecutive_bad = 0  # 直近の連続不調日数

    # 日付順にソート（新しい順）
    sorted_days = sorted(days, key=lambda x: x.get('date', ''), reverse=True)

    for day in sorted_days:
        art = day.get('art', 0)
        games = _get_games(day)
        if art > 0 and games > 0:
            prob = games / art
            probs.append(prob)
            total_days += 1
            if prob <= good_prob_threshold:
                good_days += 1
            if prob >= bad_prob_threshold:
                bad_days += 1

    # 直近の連続不調日数を計算
    for day in sorted_days:
        art = day.get('art', 0)
        games = _get_games(day)
        if art > 0 and games > 0:
            prob = games / art
            if prob >= bad_prob_threshold:
                consecutive_bad += 1
            else:
                break

    good_day_rate = good_days / total_days if total_days > 0 else 0.5
    avg_prob = sum(probs) / len(probs) if probs else 0

    # 好調翌日→翌日も好調だった率（据え置き率の目安）
    good_after_good = 0
    good_after_good_total = 0
    # sorted_daysは新しい順なので、i番目の翌日はi+1番目
    # ただし日付連続を確認
    for i in range(len(sorted_days) - 1):
        curr = sorted_days[i]
        nxt = sorted_days[i + 1]  # nxtは前日
        curr_art = curr.get('art', 0)
        curr_games = _get_games(curr)
        nxt_art = nxt.get('art', 0)
        nxt_games = _get_games(nxt)
        if nxt_art > 0 and nxt_games > 0:
            nxt_prob = nxt_games / nxt_art
            if nxt_prob <= good_prob_threshold:
                # 前日が好調だった場合、翌日(curr)も好調か？
                good_after_good_total += 1
                if curr_art > 0 and curr_games > 0:
                    curr_prob = curr_games / curr_art
                    if curr_prob <= good_prob_threshold:
                        good_after_good += 1
    continuation_rate = good_after_good / good_after_good_total if good_after_good_total > 0 else 0

    # 直近3日のART確率推移
    recent_probs = probs[:3]  # 新しい順

    # スコアボーナス計算
    # 好調率が高い台にボーナス、低い台にペナルティ（最大±10点）
    if good_day_rate >= 0.8:
        score_bonus = 10  # 80%以上好調 → 高設定が頻繁に入る台
    elif good_day_rate >= 0.7:
        score_bonus = 7
    elif good_day_rate >= 0.6:
        score_bonus = 4
    elif good_day_rate >= 0.5:
        score_bonus = 0   # 半々 → ニュートラル
    elif good_day_rate >= 0.4:
        score_bonus = -3
    elif good_day_rate >= 0.3:
        score_bonus = -5
    else:
        score_bonus = -8  # 30%未満好調 → 低設定が入りやすい台

    # 好調日の詳細（爆発レベル分析用）
    good_day_details = []
    for d in sorted_days:
        art = d.get('art', 0)
        games = d.get('games', d.get('total_games', 0))
        if art > 0 and games > 0 and (games / art) <= good_prob_threshold:
            good_day_details.append({
                'date': d.get('date', ''),
                'art': art,
                'prob': games / art if art > 0 else 0,
                'max_rensa': d.get('max_rensa', 0),
                'max_medals': d.get('max_medals', 0),
            })

    # 最長連続好調記録
    max_consecutive_good = 0
    current_streak = 0
    for d in reversed(sorted_days):  # 古い順で走査
        art = d.get('art', 0)
        games = d.get('games', d.get('total_games', 0))
        if art > 0 and games > 0 and (games / art) <= good_prob_threshold:
            current_streak += 1
            max_consecutive_good = max(max_consecutive_good, current_streak)
        else:
            current_streak = 0

    return {
        'good_day_rate': good_day_rate,
        'good_days': good_days,
        'total_days': total_days,
        'score_bonus': score_bonus,
        'avg_prob': avg_prob,
        'consecutive_bad': consecutive_bad,
        'continuation_rate': continuation_rate,         # 好調翌日も好調だった率
        'continuation_total': good_after_good_total,    # サンプル数
        'continuation_good': good_after_good,           # 翌日も好調だった回数
        'recent_probs': recent_probs,                   # 直近3日のART確率（新→古）
        'good_day_details': good_day_details,           # 好調日の詳細リスト
        'max_consecutive_good': max_consecutive_good,   # 最長連続好調記録
        'weekday_breakdown': _calc_weekday_breakdown(days, good_prob_threshold),  # 曜日別好調率
    }


def _calc_weekday_breakdown(days: list, good_threshold: int) -> dict:
    """曜日別の好調率を計算"""
    from datetime import datetime as _dt
    WDAYS = ['月','火','水','木','金','土','日']
    stats = {w: {'good': 0, 'total': 0} for w in WDAYS}
    for day in days:
        date_str = day.get('date', '')
        art = day.get('art', 0)
        games = day.get('games', 0) or day.get('total_start', 0)
        if date_str and art > 0 and games > 0:
            try:
                wd = WDAYS[_dt.strptime(date_str, '%Y-%m-%d').weekday()]
                stats[wd]['total'] += 1
                if games / art <= good_threshold:
                    stats[wd]['good'] += 1
            except:
                pass
    return stats


def analyze_activity_pattern(history: List[dict], day_data: dict = None) -> dict:
    """【改善4】稼働パターン分析（時刻データ活用）

    当たり履歴の時刻から稼働パターンを分析:
    - 粘り度: 朝から閉店まで打たれてる台は高設定の可能性UP
    - 途中放棄: 当たり間の時間差1時間以上 = 離席判定
    - 好調台の途中放棄 = おいしい台（ボーナス）
    - 不調台の途中放棄 = 低設定と見切られた（ペナルティ）
    - 100-200Gでやめてる台 = 狙い目（天井狙い余地）

    Args:
        history: 当日の当たり履歴リスト
        day_data: 当日のデータ（art, total_start等）

    Returns:
        {
            'persistence_score': float,    # 粘り度スコア (-5 to +8)
            'abandonment_type': str,       # 'none', 'good_abandoned', 'bad_abandoned', 'early_quit'
            'abandonment_bonus': float,    # 途中放棄ボーナス (-5 to +5)
            'play_duration_hours': float,  # 稼働時間
            'gap_count': int,              # 1時間以上の空きの回数
            'is_hyena_target': bool,       # ハイエナ対象か【改善5】
            'hyena_penalty': float,        # ハイエナペナルティ (0 to -5)
            'description': str,
        }
    """
    result = {
        'persistence_score': 0,
        'abandonment_type': 'none',
        'abandonment_bonus': 0,
        'play_duration_hours': 0,
        'gap_count': 0,
        'is_hyena_target': False,
        'hyena_penalty': 0,
        'description': '',
    }

    if not history or len(history) < 2:
        return result

    # 時刻順にソート
    sorted_hist = sorted(history, key=lambda x: x.get('time', '00:00'))

    # 稼働時間を計算
    try:
        first_time = datetime.strptime(sorted_hist[0].get('time', '10:00'), '%H:%M')
        last_time = datetime.strptime(sorted_hist[-1].get('time', '10:00'), '%H:%M')
        duration_hours = (last_time - first_time).total_seconds() / 3600
        result['play_duration_hours'] = max(0, duration_hours)
    except:
        return result

    # --- 粘り度分析 ---
    # 朝（10:00-11:00）から始まり、夜（19:00以降）まで打ち続けた台は高設定可能性UP
    first_hour = first_time.hour
    last_hour = last_time.hour

    if first_hour <= 11 and last_hour >= 19:
        # 朝から閉店近くまで粘っている → 高設定の可能性
        result['persistence_score'] = 8
        result['description'] = '朝から夜まで粘り → 高設定の可能性'
    elif first_hour <= 11 and last_hour >= 17:
        result['persistence_score'] = 5
        result['description'] = '朝から夕方まで稼働'
    elif first_hour <= 11 and last_hour < 15:
        # 朝から始めて午後早めにやめた → 見切りの可能性
        result['persistence_score'] = -3
        result['description'] = '朝から稼働も早めに撤退'
    elif first_hour >= 15:
        # 夕方以降から稼働 → 天井狙い or 空き台狙いの可能性
        result['persistence_score'] = -2
        result['description'] = '夕方以降から稼働（ハイエナの可能性）'

    # --- 途中放棄分析 ---
    gap_count = 0
    max_gap_minutes = 0
    gap_positions = []  # 空きが発生した位置

    for i in range(1, len(sorted_hist)):
        try:
            t1 = datetime.strptime(sorted_hist[i-1].get('time', '00:00'), '%H:%M')
            t2 = datetime.strptime(sorted_hist[i].get('time', '00:00'), '%H:%M')
            gap_minutes = (t2 - t1).total_seconds() / 60

            if gap_minutes >= 60:  # 1時間以上の空き = 離席判定
                gap_count += 1
                max_gap_minutes = max(max_gap_minutes, gap_minutes)
                gap_positions.append(i)
        except:
            continue

    result['gap_count'] = gap_count

    if gap_count > 0:
        # 空きの前までの確率を計算（好調台の途中放棄かどうか）
        art = day_data.get('art', 0) if day_data else 0
        games = day_data.get('total_start', 0) if day_data else 0
        overall_prob = games / art if art > 0 and games > 0 else 999

        if overall_prob <= 130:
            # 好調台なのに途中放棄 = おいしい台（ボーナス）
            result['abandonment_type'] = 'good_abandoned'
            result['abandonment_bonus'] = 5
            result['description'] = f'好調台(1/{overall_prob:.0f})が途中放棄 → おいしい台'
        elif overall_prob >= 180:
            # 不調台の途中放棄 = 低設定と見切られた（ペナルティ）
            result['abandonment_type'] = 'bad_abandoned'
            result['abandonment_bonus'] = -5
            result['description'] = f'不調台(1/{overall_prob:.0f})が見切られた → 低設定疑い'
        else:
            result['abandonment_type'] = 'neutral_abandoned'
            result['abandonment_bonus'] = 0

    # --- 早期撤退分析（100-200Gでやめてる台） ---
    if sorted_hist:
        last_start = sorted_hist[-1].get('start', 0)
        # 最終当たりが100-200Gの少ないG数で、かつ稼働時間が短い
        if day_data:
            final_start = day_data.get('final_start', 0)
            if 100 <= final_start <= 200:
                result['description'] = f'最終{final_start}Gでやめ → 天井狙い余地あり'

    # --- 【改善5】ハイエナ検知 ---
    # 夕方以降（16時以降）に急に当たり始めた台 = 天井狙いの可能性
    evening_hits = [h for h in sorted_hist if h.get('time', '00:00') >= '16:00']
    morning_hits = [h for h in sorted_hist if h.get('time', '00:00') < '16:00']

    if len(evening_hits) > 0 and len(morning_hits) == 0:
        # 夕方以降にしか当たりがない → ハイエナの可能性
        result['is_hyena_target'] = True
        result['hyena_penalty'] = -5
        result['description'] = '夕方以降のみ稼働 → ハイエナの可能性（高設定とは限らない）'
    elif len(evening_hits) > len(morning_hits) * 2 and len(evening_hits) >= 10:
        # 夕方以降に当たりが集中 → 天井狙い後の連チャンの可能性
        result['is_hyena_target'] = True
        result['hyena_penalty'] = -3
        result['description'] = '夕方以降に当たり集中 → ハイエナ後の連チャンの可能性'

    return result


def analyze_trend(days: List[dict], machine_key: str = 'sbj') -> dict:
    """過去日のトレンドを分析

    Returns:
        {
            'consecutive_plus': int,  # 連続プラス日数
            'consecutive_minus': int, # 連続マイナス日数
            'trend': str,  # 'up', 'down', 'flat'
            'yesterday_result': str,  # 'plus', 'minus', 'unknown'
            'yesterday_diff': int,  # 昨日の推定差枚
            'avg_art_7days': float,  # 7日間平均ART
            'avg_games_7days': float,  # 7日間平均G数
            'best_day': dict,  # 最高の日
            'worst_day': dict,  # 最悪の日
            'art_trend': str,  # ART確率の傾向
            'reasons': list,  # トレンドの根拠
        }
    """
    result = {
        'consecutive_plus': 0,
        'consecutive_minus': 0,
        'trend': 'flat',
        'yesterday_result': 'unknown',
        'yesterday_diff': 0,
        'avg_art_7days': 0,
        'avg_games_7days': 0,
        'best_day': None,
        'worst_day': None,
        'art_trend': 'flat',
        'reasons': [],
    }

    if not days:
        return result

    # 日付順にソート（新しい順）
    sorted_days = sorted(days, key=lambda x: x.get('date', ''), reverse=True)

    # 7日間の統計
    art_counts = []
    game_counts = []
    daily_results = []  # [(date, estimated_diff), ...]

    for day in sorted_days[:7]:
        art = day.get('art', 0)
        games = day.get('total_start', 0)
        art_counts.append(art)
        game_counts.append(games)

        # 差枚推定（historyのmedalsがあれば正確に計算、なければ確率ベース）
        if games > 0:
            estimated_diff = 0
            hist = day.get('history', [])
            if hist:
                # historyがあれば実medalsベースで差枚推定
                try:
                    from analysis.diff_medals_estimator import estimate_diff_medals
                    medals_total = sum(h.get('medals', 0) for h in hist)
                    estimated_diff = estimate_diff_medals(medals_total, games, machine_key)
                except Exception:
                    pass
            if estimated_diff == 0 and art > 0:
                # フォールバック: 確率ベース推定
                art_prob = games / art
                if art_prob <= 80:
                    estimated_diff = games * 0.3
                elif art_prob <= 120:
                    estimated_diff = games * 0.1
                elif art_prob <= 180:
                    estimated_diff = -games * 0.05
                else:
                    estimated_diff = -games * 0.15
            elif estimated_diff == 0:
                estimated_diff = -games * 0.2
            daily_results.append((day.get('date'), estimated_diff, art, games))
        elif art > 0:
            daily_results.append((day.get('date'), 0, art, games))

    if art_counts:
        result['avg_art_7days'] = sum(art_counts) / len(art_counts)
    if game_counts:
        result['avg_games_7days'] = sum(game_counts) / len(game_counts)

    # 連続プラス/マイナス判定
    # 低稼働日の扱い:
    #   - 確率が好調域（prob <= good_threshold）→ プラスとしてカウント（即やめ=高設定の可能性）
    #   - 確率が不調域 → マイナスとしてカウント
    #   - データ極少（ART<3かつG<500）→ スキップ（判定不能）
    good_prob_threshold = get_machine_threshold(machine_key, 'good_prob')
    consecutive_plus = 0
    consecutive_minus = 0
    for date, diff, art, games in daily_results:
        if art < 3 and games < 500 and games > 0:
            # 極少データ（即やめ等）はスキップ（連続を途切れさせない）
            continue
        if diff > 0:
            consecutive_plus += 1
            consecutive_minus = 0
        elif diff < 0:
            # 低稼働でマイナスだが確率が良い場合はプラス扱い
            if art > 0 and games > 0:
                prob = games / art
                if prob <= good_prob_threshold:
                    consecutive_plus += 1
                    consecutive_minus = 0
                    continue
            consecutive_minus += 1
            consecutive_plus = 0
        elif games == 0 and art > 0:
            continue
        else:
            break

    result['consecutive_plus'] = consecutive_plus
    result['consecutive_minus'] = consecutive_minus

    # 昨日の結果
    if daily_results:
        yesterday_date, yesterday_diff, yesterday_art, yesterday_games = daily_results[0]
        result['yesterday_diff'] = int(yesterday_diff)
        result['yesterday_art'] = yesterday_art  # 昨日のART数を追加
        result['yesterday_games'] = int(yesterday_games)  # 昨日のG数
        if yesterday_diff > 500:
            result['yesterday_result'] = 'big_plus'
        elif yesterday_diff > 0:
            result['yesterday_result'] = 'plus'
        elif yesterday_diff < -500:
            result['yesterday_result'] = 'big_minus'
        elif yesterday_diff < 0:
            result['yesterday_result'] = 'minus'
        else:
            result['yesterday_result'] = 'even'

    # 昨日のRB・最大連チャン・最大枚数を取得
    if sorted_days:
        yesterday_day = sorted_days[0]
        result['yesterday_rb'] = yesterday_day.get('rb', 0)
        result['yesterday_date'] = yesterday_day.get('date', '')
        # 昨日の最大連チャン数・最大連チャン枚数
        yesterday_history = yesterday_day.get('history', [])
        if yesterday_history:
            from analysis.analyzer import calculate_max_chain_medals
            result['yesterday_max_rensa'] = calculate_max_rensa(yesterday_history)
            result['yesterday_max_medals'] = calculate_max_chain_medals(yesterday_history)
            result['yesterday_history'] = yesterday_history
        else:
            result['yesterday_max_medals'] = yesterday_day.get('max_medals', 0)

    # 前々日の結果
    if len(daily_results) >= 2:
        db_date, db_diff, db_art, db_games = daily_results[1]
        result['day_before_art'] = db_art
        result['day_before_games'] = int(db_games)
        result['day_before_date'] = db_date
        result['day_before_diff_medals'] = int(db_diff) if db_diff else 0
    if len(sorted_days) >= 2:
        result['day_before_rb'] = sorted_days[1].get('rb', 0)
        db_history = sorted_days[1].get('history', [])
        if db_history:
            from analysis.analyzer import calculate_max_chain_medals
            result['day_before_max_rensa'] = calculate_max_rensa(db_history)
            result['day_before_max_medals'] = calculate_max_chain_medals(db_history)
        else:
            result['day_before_max_rensa'] = sorted_days[1].get('max_rensa', 0)
            result['day_before_max_medals'] = sorted_days[1].get('max_medals', 0)

    # 3日前の結果
    if len(daily_results) >= 3:
        td_date, td_diff, td_art, td_games = daily_results[2]
        result['three_days_ago_art'] = td_art
        result['three_days_ago_games'] = int(td_games)
        result['three_days_ago_date'] = td_date
        result['three_days_ago_diff_medals'] = int(td_diff) if td_diff else 0
    if len(sorted_days) >= 3:
        result['three_days_ago_rb'] = sorted_days[2].get('rb', 0)
        td_history = sorted_days[2].get('history', [])
        if td_history:
            from analysis.analyzer import calculate_max_chain_medals
            result['three_days_ago_max_rensa'] = calculate_max_rensa(td_history)
            result['three_days_ago_max_medals'] = calculate_max_chain_medals(td_history)
        else:
            result['three_days_ago_max_rensa'] = sorted_days[2].get('max_rensa', 0)
            result['three_days_ago_max_medals'] = sorted_days[2].get('max_medals', 0)

    # トレンド判定
    if consecutive_plus >= 3:
        result['trend'] = 'strong_up'
        result['reasons'].append(f'{consecutive_plus}日連続プラス推定')
    elif consecutive_plus >= 2:
        result['trend'] = 'up'
        result['reasons'].append(f'{consecutive_plus}日連続プラス推定')
    elif consecutive_minus >= 3:
        result['trend'] = 'strong_down'
        result['reasons'].append(f'{consecutive_minus}日連続マイナス推定')
    elif consecutive_minus >= 2:
        result['trend'] = 'down'
        result['reasons'].append(f'{consecutive_minus}日連続マイナス推定')

    # 最高/最悪の日
    if daily_results:
        best = max(daily_results, key=lambda x: x[1])
        worst = min(daily_results, key=lambda x: x[1])
        result['best_day'] = {'date': best[0], 'diff': int(best[1]), 'art': best[2]}
        result['worst_day'] = {'date': worst[0], 'diff': int(worst[1]), 'art': worst[2]}

    # ART確率トレンド（直近3日 vs 4-7日前）
    if len(art_counts) >= 5:
        recent_avg = sum(art_counts[:3]) / 3
        older_avg = sum(art_counts[3:min(7, len(art_counts))]) / min(4, len(art_counts) - 3)
        if recent_avg > older_avg * 1.2:
            result['art_trend'] = 'improving'
            result['reasons'].append('直近ART確率改善傾向')
        elif recent_avg < older_avg * 0.8:
            result['art_trend'] = 'declining'
            result['reasons'].append('直近ART確率悪化傾向')

    # --- 【改善2】前日・前々日のART確率を計算（不調→翌日狙い目判定用） ---
    if sorted_days:
        d = sorted_days[0]
        art = d.get('art', 0)
        games = d.get('total_start', 0)
        if art > 0 and games > 0:
            result['yesterday_prob'] = games / art
        else:
            result['yesterday_prob'] = 0
    if len(sorted_days) >= 2:
        d = sorted_days[1]
        art = d.get('art', 0)
        games = d.get('total_start', 0)
        if art > 0 and games > 0:
            result['day_before_prob'] = games / art
        else:
            result['day_before_prob'] = 0

    # --- 実用指標の計算 ---

    # AT間（ART→ART間のG数）を履歴から正しく計算
    # RBを跨いでART到達までの総G数を算出
    all_at_intervals = []
    for day in sorted_days[:7]:
        history = day.get('history', [])
        if history:
            day_intervals = calculate_at_intervals(history)
            all_at_intervals.extend(day_intervals)

    if all_at_intervals:
        result['avg_at_interval'] = sum(all_at_intervals) / len(all_at_intervals)
        result['max_at_interval'] = max(all_at_intervals)
        result['ceiling_count'] = sum(1 for g in all_at_intervals if g >= 999)
    else:
        result['avg_at_interval'] = 0
        result['max_at_interval'] = 0
        result['ceiling_count'] = 0

    # ART確率（total_start / art_count）
    art_probs = []
    for day in sorted_days[:7]:
        art = day.get('art', 0)
        games = day.get('total_start', 0)
        if art > 0 and games > 0:
            art_probs.append(games / art)
    if art_probs:
        result['avg_art_prob'] = sum(art_probs) / len(art_probs)
    else:
        result['avg_art_prob'] = 0

    # 最大出玉日の情報
    max_art_day = None
    if sorted_days:
        max_art_day = max(sorted_days[:7], key=lambda x: x.get('art', 0))
        result['max_art_day'] = {
            'date': max_art_day.get('date', ''),
            'art': max_art_day.get('art', 0),
            'games': max_art_day.get('total_start', 0),
        }

    # 直近7日分のサマリ配列（テンプレート表示用）
    recent_days = []
    for d in sorted_days[:7]:
        art = d.get('art', 0)
        games = d.get('games', 0) or d.get('total_start', 0)
        prob = games / art if art > 0 and games > 0 else 0
        # max_medals: historyがあれば連チャン累計で計算
        day_history = d.get('history', [])
        if day_history:
            from analysis.analyzer import calculate_max_chain_medals
            day_max_medals = calculate_max_chain_medals(day_history)
        else:
            day_max_medals = d.get('max_medals', 0)

        recent_days.append({
            'date': d.get('date', ''),
            'art': art,
            'rb': d.get('rb', 0),
            'games': games,
            'prob': round(prob, 1) if prob > 0 else 0,
            'max_rensa': d.get('max_rensa', 0),
            'max_medals': day_max_medals,
            'history': day_history,
        })
    result['recent_days'] = recent_days

    return result


def analyze_today_data(unit_data: dict, current_hour: int = None, machine_key: str = 'sbj') -> dict:
    """当日データを分析

    Args:
        unit_data: 台データ。'days'キーがある場合は日別データ、
                   ない場合はリアルタイムデータ（直接当日データ）として扱う
        current_hour: 現在時刻（テスト用）
        machine_key: 機種キー（'sbj', 'hokuto_tensei2'）- 閾値判定に使用
    """
    if current_hour is None:
        current_hour = datetime.now().hour

    result = {
        'art_count': 0,
        'bb_count': 0,
        'rb_count': 0,
        'total_games': 0,
        'art_prob': 0,
        'last_hit_time': None,
        'first_hit_time': None,
        'is_running': False,
        'today_score_bonus': 0,
        'status': '-',
        'hourly_rate': 0,  # 1時間あたりのART数
        'expected_games': 0,  # この時間帯での期待G数
        'today_reasons': [],
        'data_date': '',  # データの日付
        'is_today_data': False,  # 本日のデータかどうか
    }

    if not unit_data:
        return result

    # リアルタイムデータ形式（daysキーなし）の場合
    if 'days' not in unit_data:
        today_data = unit_data
        result['status'] = 'リアルタイム'
        result['data_date'] = datetime.now().strftime('%Y-%m-%d')
        result['is_today_data'] = True
    else:
        # 日別データ形式の場合
        days = unit_data.get('days', [])
        if not days:
            return result

        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        today_data = None
        yesterday_data = None

        for day in days:
            if day.get('date') == today:
                today_data = day
                break
            elif day.get('date') == yesterday:
                yesterday_data = day

        if not today_data:
            # 当日データなし → 昨日のデータを使用
            if yesterday_data:
                today_data = yesterday_data
                result['status'] = '昨日データ'
                result['data_date'] = yesterday
                result['today_reasons'].append('本日データなし（昨日のデータを表示）')
            else:
                result['status'] = 'データなし'
                result['today_score_bonus'] = 5  # 未稼働台は狙い目の可能性
                result['today_reasons'].append('本日のデータなし（狙い目の可能性）')
                return result
        else:
            result['data_date'] = today
            result['status'] = '本日データ'
            result['is_today_data'] = True

    result['art_count'] = today_data.get('art', 0)
    result['bb_count'] = today_data.get('bb', 0)
    result['rb_count'] = today_data.get('rb', 0)
    result['total_games'] = today_data.get('total_start', 0)

    if result['art_count'] > 0 and result['total_games'] > 0:
        result['art_prob'] = result['total_games'] / result['art_count']

    # 履歴から時間情報を取得
    history = today_data.get('history', [])
    if history:
        # 時間順でソート
        sorted_history = sorted(history, key=lambda x: x.get('time', '00:00'))
        result['first_hit_time'] = sorted_history[0].get('time')
        result['last_hit_time'] = sorted_history[-1].get('time')

        # 1時間あたりのART数を計算
        if result['first_hit_time'] and result['last_hit_time']:
            try:
                first = datetime.strptime(result['first_hit_time'], '%H:%M')
                last = datetime.strptime(result['last_hit_time'], '%H:%M')
                duration_hours = (last - first).total_seconds() / 3600
                if duration_hours > 0:
                    result['hourly_rate'] = result['art_count'] / duration_hours
            except:
                pass

        # 稼働中判定（最終当たりから30分以内）
        if result['last_hit_time']:
            try:
                last_time = datetime.strptime(result['last_hit_time'], '%H:%M')
                now = datetime.now()
                current_time = datetime.strptime(now.strftime('%H:%M'), '%H:%M')
                diff_minutes = (current_time - last_time).total_seconds() / 60

                if diff_minutes < 0:
                    diff_minutes += 24 * 60  # 日付をまたいだ場合

                if diff_minutes < 30:
                    result['is_running'] = True
                    result['status'] = '稼働中'
                elif diff_minutes < 60:
                    result['status'] = f'空き{int(diff_minutes)}分'
                else:
                    hours = int(diff_minutes // 60)
                    mins = int(diff_minutes % 60)
                    result['status'] = f'空き{hours}時間{mins}分'
            except:
                pass

    # 当日のART確率評価（機種別閾値を使用）
    # ゲーム数が多いほど信頼度が高いため、ボーナスを増やす
    thresholds = get_machine_thresholds(machine_key)
    games_multiplier = 1.0
    if result['total_games'] >= 5000:
        games_multiplier = 1.5  # 5000G以上: 信頼度高
    elif result['total_games'] >= 3000:
        games_multiplier = 1.3  # 3000G以上: やや信頼
    elif result['total_games'] < 1000:
        games_multiplier = 0.5  # 1000G未満: 信頼度低

    if result['art_prob'] > 0:
        if result['art_prob'] <= thresholds['setting6_at_prob']:
            result['today_score_bonus'] = int(25 * games_multiplier)
            result['today_reasons'].append(f'本日ART確率 1/{result["art_prob"]:.0f} (設定6域)')
        elif result['art_prob'] <= thresholds['high_at_prob']:
            result['today_score_bonus'] = int(18 * games_multiplier)
            result['today_reasons'].append(f'本日ART確率 1/{result["art_prob"]:.0f} (高設定域)')
        elif result['art_prob'] <= thresholds['mid_at_prob']:
            result['today_score_bonus'] = int(12 * games_multiplier)
            result['today_reasons'].append(f'本日ART確率 1/{result["art_prob"]:.0f} (中間設定域)')
        elif result['art_prob'] <= thresholds['low_at_prob']:
            # 130-180: 低設定寄り → 強めのペナルティ
            result['today_score_bonus'] = int(-20 * games_multiplier)
            result['today_reasons'].append(f'🚨 本日ART確率 1/{result["art_prob"]:.0f} (低設定寄り)')
        elif result['art_prob'] >= thresholds['very_low_at_prob']:
            # 250以上: 完全に低設定 → 最大ペナルティ
            result['today_score_bonus'] = int(-30 * games_multiplier)
            result['today_reasons'].append(f'🚨 本日ART確率 1/{result["art_prob"]:.0f} (低設定域)')
        else:
            # 180-250: 低設定濃厚 → 強ペナルティ
            result['today_score_bonus'] = int(-25 * games_multiplier)
            result['today_reasons'].append(f'🚨 本日ART確率 1/{result["art_prob"]:.0f} (低設定濃厚)')

    # 時間帯に対する稼働量の評価
    if current_hour >= 10:
        elapsed_hours = current_hour - 10 + (datetime.now().minute / 60)
        expected_games_per_hour = 800  # 設定6なら1時間800Gくらい
        result['expected_games'] = elapsed_hours * expected_games_per_hour * 0.7  # 70%稼働想定

        if result['total_games'] > 0:
            actual_rate = result['total_games'] / result['expected_games'] if result['expected_games'] > 0 else 0
            if actual_rate < 0.3:
                result['today_reasons'].append(f'稼働少なめ（期待値の{actual_rate*100:.0f}%）')

    return result


def compare_with_others(store_key: str, unit_id: str, all_units_today: dict) -> dict:
    """他台との比較分析

    Args:
        store_key: 店舗キー
        unit_id: 対象台番号
        all_units_today: 全台の当日データ

    Returns:
        {
            'rank_in_store': int,  # 店舗内順位
            'total_units': int,  # 総台数
            'avg_art_store': float,  # 店舗平均ART
            'diff_from_avg': float,  # 平均との差
            'is_top_performer': bool,  # トップパフォーマーか
            'comparison_note': str,  # 比較コメント
        }
    """
    result = {
        'rank_in_store': 0,
        'total_units': 0,
        'avg_art_store': 0,
        'diff_from_avg': 0,
        'is_top_performer': False,
        'comparison_note': '',
    }

    if not all_units_today:
        return result

    # 全台のART数を収集
    unit_arts = []
    target_art = 0
    for unit in all_units_today:
        uid = unit.get('unit_id')
        art = unit.get('art', 0)
        if art > 0:
            unit_arts.append((uid, art))
            if uid == unit_id:
                target_art = art

    if not unit_arts:
        return result

    result['total_units'] = len(unit_arts)
    result['avg_art_store'] = sum(a for _, a in unit_arts) / len(unit_arts)

    # 順位計算
    sorted_units = sorted(unit_arts, key=lambda x: -x[1])
    for i, (uid, art) in enumerate(sorted_units, 1):
        if uid == unit_id:
            result['rank_in_store'] = i
            break

    if target_art > 0:
        result['diff_from_avg'] = target_art - result['avg_art_store']
        if result['rank_in_store'] == 1:
            result['is_top_performer'] = True
            result['comparison_note'] = '本日トップ'
        elif result['rank_in_store'] <= 3:
            result['comparison_note'] = f'本日{result["rank_in_store"]}位/{result["total_units"]}台'
        elif result['diff_from_avg'] < -5:
            result['comparison_note'] = f'平均より{abs(result["diff_from_avg"]):.0f}回少ない'

    return result


def analyze_graph_pattern(days: List[dict]) -> dict:
    """グラフパターン分析（ミミズ/モミモミ/右肩上がり等）

    ミミズ: ハマらないが飲まれる、REGの繰り返しで大ハネしない横ばい状態
    モミモミ: 小刻みに上下するが大きな変動なし、このあと跳ねることが多い

    Returns:
        {
            'pattern': str,
            'volatility': float,
            'description': str,
            'likely_to_rise': bool,  # このあと伸びそうか
        }
    """
    if not days or len(days) < 3:
        return {'pattern': 'unknown', 'volatility': 0, 'description': '', 'likely_to_rise': False}

    # 直近7日のART数と最大連チャン数を分析
    arts = []
    max_rensas = []  # 最大連チャン数
    for d in days[:7]:
        art = d.get('art', 0)
        if art > 0:
            arts.append(art)
            # 履歴から最大連チャンを計算
            history = d.get('history', [])
            if history:
                max_rensa = calculate_max_rensa(history)
                max_rensas.append(max_rensa)

    if len(arts) < 3:
        return {'pattern': 'unknown', 'volatility': 0, 'description': '', 'likely_to_rise': False}

    avg = sum(arts) / len(arts)
    if avg == 0:
        return {'pattern': 'unknown', 'volatility': 0, 'description': '', 'likely_to_rise': False}

    # 変動幅
    deviations = [abs(a - avg) for a in arts]
    volatility = sum(deviations) / len(deviations) / avg * 100

    # トレンド
    recent_avg = sum(arts[:3]) / 3
    older_avg = sum(arts[3:]) / len(arts[3:]) if len(arts) > 3 else avg

    # 最大連チャン傾向（10連以上があるか）
    has_big_rensa = any(r >= 10 for r in max_rensas) if max_rensas else False
    avg_max_rensa = sum(max_rensas) / len(max_rensas) if max_rensas else 0

    # パターン判定
    likely_to_rise = False

    if volatility < 15:
        # 変動が少ない = ミミズ（横ばい）
        pattern = 'mimizu'
        if avg >= 35 and has_big_rensa:
            description = f'安定高挙動（平均{avg:.0f}ART、10連+あり）→ 高設定濃厚'
        elif avg >= 30:
            description = f'安定推移（平均{avg:.0f}ART）'
            if not has_big_rensa:
                description += ' → 爆発待ちの可能性'
                likely_to_rise = True
        else:
            description = f'ミミズ（低空飛行で横ばい）'
            if avg >= 20:
                description += ' → このあと跳ねる可能性'
                likely_to_rise = True
    elif volatility < 30:
        # モミモミ（小刻み変動、大ハネしない）
        pattern = 'momimomi'
        if not has_big_rensa and avg >= 20:
            description = f'モミモミ（10連なし、平均{avg:.0f}ART）→ 爆発待ち状態'
            likely_to_rise = True
        elif recent_avg > older_avg * 1.1:
            description = f'モミモミから上昇兆候 → そろそろ跳ねる'
            likely_to_rise = True
        else:
            description = f'モミモミ中（平均{avg:.0f}ART）'
            if not has_big_rensa:
                likely_to_rise = True
    else:
        # 変動が大きい
        if recent_avg > older_avg * 1.2:
            pattern = 'rising'
            description = f'右肩上がり → 高設定に変更された可能性'
        elif recent_avg < older_avg * 0.8:
            pattern = 'falling'
            description = f'右肩下がり → 設定下げ警戒'
        else:
            pattern = 'volatile'
            if has_big_rensa:
                description = f'荒い台（10連+実績あり）→ 一発狙い向き'
            else:
                description = f'変動大だが爆発なし → 様子見推奨'

    return {
        'pattern': pattern,
        'volatility': volatility,
        'description': description,
        'likely_to_rise': likely_to_rise,
        'has_big_rensa': has_big_rensa,
        'avg_max_rensa': avg_max_rensa,
    }


def calc_no_explosion_next_day_stats(machine_key: str = 'sbj') -> dict:
    """確率は好調だが爆発しなかった日の翌日統計（全店舗統合）

    Returns:
        {'total': N, 'next_good': N, 'rate': float}
    """
    import glob
    good_threshold = get_machine_threshold(machine_key, 'good_prob')
    total = 0
    next_good = 0

    hist_base = 'data/history'
    if not os.path.isdir(hist_base):
        return {'total': 0, 'next_good': 0, 'rate': 0.0}

    for store_dir in os.listdir(hist_base):
        if machine_key not in store_dir:
            continue
        store_path = os.path.join(hist_base, store_dir)
        if not os.path.isdir(store_path):
            continue
        for f in glob.glob(os.path.join(store_path, '*.json')):
            try:
                with open(f) as fp:
                    data = json.load(fp)
            except:
                continue
            days = sorted(data.get('days', []), key=lambda d: d.get('date', ''))
            for i, d in enumerate(days):
                art = d.get('art', 0)
                games = d.get('total_start', 0) or d.get('games', 0)
                mr = d.get('max_rensa', 0)
                if art <= 0 or games <= 0 or mr <= 0:
                    continue
                prob = games / art
                # 確率は好調だが最大連チャンが15連未満 → 爆発なし
                if prob <= good_threshold and mr < 15:
                    if i + 1 < len(days):
                        nd = days[i + 1]
                        na = nd.get('art', 0)
                        ng = nd.get('total_start', 0) or nd.get('games', 0)
                        if na > 0 and ng > 0:
                            total += 1
                            if (ng / na) <= good_threshold:
                                next_good += 1

    rate = next_good / total if total > 0 else 0.0
    return {'total': total, 'next_good': next_good, 'rate': rate}


# キャッシュ
_no_explosion_cache = {}


def get_no_explosion_stats(machine_key: str = 'sbj') -> dict:
    if machine_key not in _no_explosion_cache:
        _no_explosion_cache[machine_key] = calc_no_explosion_next_day_stats(machine_key)
    return _no_explosion_cache[machine_key]


def calc_recovery_stats(store_key: str, machine_key: str = 'sbj') -> dict:
    """蓄積データから連続不調→翌日回復率を計算

    Returns:
        {1: {'total': N, 'recovered': N, 'rate': float}, ...}
    """
    import glob
    hist_dir = f'data/history/{store_key}'
    good_threshold = get_machine_threshold(machine_key, 'good_prob')
    results = {}
    for n in range(1, 6):
        results[n] = {'total': 0, 'recovered': 0, 'rate': 0.0}

    if not os.path.isdir(hist_dir):
        return results

    for f in glob.glob(os.path.join(hist_dir, '*.json')):
        try:
            with open(f) as fp:
                data = json.load(fp)
        except:
            continue
        days = sorted(data.get('days', []), key=lambda d: d.get('date', ''))
        probs = []
        for d in days:
            art = d.get('art', 0)
            games = d.get('total_start', 0) or d.get('games', 0)
            if art > 0 and games > 0:
                probs.append(games / art)
            else:
                probs.append(None)

        for i in range(1, len(probs)):
            if probs[i] is None:
                continue
            is_good = probs[i] <= good_threshold
            streak = 0
            for j in range(i-1, -1, -1):
                if probs[j] is None or probs[j] <= good_threshold:
                    break
                streak += 1
            for n in range(1, min(streak+1, 6)):
                results[n]['total'] += 1
                if is_good:
                    results[n]['recovered'] += 1

    for n in results:
        t = results[n]['total']
        results[n]['rate'] = results[n]['recovered'] / t if t > 0 else 0.0

    return results


# 回復率キャッシュ
_recovery_cache = {}

def get_recovery_stats(store_key: str, machine_key: str = 'sbj') -> dict:
    cache_key = f'{store_key}_{machine_key}'
    if cache_key not in _recovery_cache:
        _recovery_cache[cache_key] = calc_recovery_stats(store_key, machine_key)
    return _recovery_cache[cache_key]


def get_machine_recovery_stats(machine_key: str = 'sbj') -> dict:
    """全店舗統合の機種別回復率"""
    cache_key = f'__machine__{machine_key}'
    if cache_key in _recovery_cache:
        return _recovery_cache[cache_key]

    total = {}
    for n in range(1, 6):
        total[n] = {'total': 0, 'recovered': 0, 'rate': 0.0}

    hist_base = 'data/history'
    if os.path.isdir(hist_base):
        for store_dir in os.listdir(hist_base):
            if machine_key in store_dir or (machine_key == 'sbj' and 'sbj' in store_dir):
                r = calc_recovery_stats(store_dir, machine_key)
                for n in range(1, 6):
                    total[n]['total'] += r[n]['total']
                    total[n]['recovered'] += r[n]['recovered']

    for n in total:
        t = total[n]['total']
        total[n]['rate'] = total[n]['recovered'] / t if t > 0 else 0.0

    _recovery_cache[cache_key] = total
    return total


def analyze_rotation_pattern(days: List[dict], machine_key: str = 'sbj') -> dict:
    """ローテーションパターン分析

    Returns:
        {
            'has_pattern': bool,
            'cycle_days': int,  # ローテ周期
            'next_high_chance': bool,  # 次に上がりそうか
            'description': str,
        }
    """
    if not days or len(days) < 5:
        return {'has_pattern': False, 'cycle_days': 0, 'next_high_chance': False, 'description': ''}

    # 直近7日の結果（プラス/マイナス）をパターン化
    SYMBOL_GOOD = '<span class="rot-good">◎</span>'
    SYMBOL_BAD = '<span class="rot-bad">✕</span>'
    SYMBOL_MID = '<span class="rot-mid">△</span>'
    results = []
    for day in days[:7]:
        art = day.get('art', 0)
        games = day.get('total_start', 0)
        if games > 0 and art > 0:
            prob = games / art
            _good = get_machine_threshold(machine_key, 'good_prob')
            _vbad = get_machine_threshold(machine_key, 'very_bad_prob')
            if prob <= _good:
                results.append(SYMBOL_GOOD)
            elif prob >= _vbad:
                results.append(SYMBOL_BAD)
            else:
                results.append(SYMBOL_MID)

    if len(results) < 5:
        return {'has_pattern': False, 'cycle_days': 0, 'next_high_chance': False, 'description': ''}

    # 連続マイナス後のプラスパターンを検出
    is_bad = lambda s: s == SYMBOL_BAD
    is_good = lambda s: s == SYMBOL_GOOD

    # 表示用（古い→新しいの順、→で繋ぐ）
    def _fmt_pattern(r):
        return '→'.join(reversed(r[:min(6, len(r))]))

    # 2日下げて上げるパターン
    if len(results) >= 3 and is_bad(results[2]) and is_bad(results[1]) and is_good(results[0]):
        return {
            'has_pattern': True,
            'cycle_days': 3,
            'next_high_chance': is_bad(results[0]) and is_bad(results[1]),
            'description': f'{_fmt_pattern(results)}（2日下げ→上げのローテ傾向）'
        }

    # 3日下げて上げるパターン
    if len(results) >= 4 and is_bad(results[3]) and is_bad(results[2]) and is_bad(results[1]) and is_good(results[0]):
        return {
            'has_pattern': True,
            'cycle_days': 4,
            'next_high_chance': is_bad(results[0]) and is_bad(results[1]) and is_bad(results[2]),
            'description': f'{_fmt_pattern(results)}（3日下げ→上げのローテ傾向）'
        }

    # 交互パターン
    alternating = sum(1 for i in range(len(results)-1) if results[i] != results[i+1])
    alt_rate = alternating / (len(results) - 1) if len(results) > 1 else 0
    # 80%以上 かつ 直近2日が同じでない場合のみ
    if alt_rate >= 0.8 and len(results) >= 2 and results[0] != results[1]:
        return {
            'has_pattern': True,
            'cycle_days': 2,
            'next_high_chance': is_bad(results[0]),
            'description': f'{_fmt_pattern(results)}（{alternating}/{len(results)-1}回交互）'
        }

    return {'has_pattern': False, 'cycle_days': 0, 'next_high_chance': False, 'description': ''}


def analyze_today_graph(history: List[dict]) -> dict:
    """本日のグラフ分析（ハマりなし/連チャン中/爆発判定等）

    Returns:
        {
            'no_deep_valley': bool,  # 深いハマりなし
            'max_valley': int,  # 最大ハマり
            'is_on_fire': bool,  # 連チャン中
            'has_explosion': bool,  # 10連以上の爆発あり
            'max_rensa': int,  # 最大連チャン数
            'recent_trend': str,  # 直近の傾向
            'description': str,
            'explosion_potential': str,  # 爆発ポテンシャル
        }
    """
    default_result = {
        'no_deep_valley': True,
        'max_valley': 0,
        'is_on_fire': False,
        'has_explosion': False,
        'max_rensa': 0,
        'recent_trend': 'unknown',
        'description': '',
        'explosion_potential': 'unknown',
    }

    if not history:
        return default_result

    # AT間（大当たり間のG数）を正しく計算（RBを跨いで合算）
    valleys = calculate_at_intervals(history)

    # 連チャン数を計算（履歴のstart値から70G以下の連続大当たりを算出）
    max_rensa = calculate_max_rensa(history)

    if not valleys:
        return default_result

    max_valley = max(valleys)
    avg_valley = sum(valleys) / len(valleys)
    recent_valleys = valleys[-5:] if len(valleys) >= 5 else valleys
    has_explosion = max_rensa >= 10  # 10連以上を爆発とみなす

    # 深いハマりなし判定
    no_deep_valley = max_valley < 500

    # 連チャン中判定
    is_on_fire = len(recent_valleys) >= 3 and all(v <= 100 for v in recent_valleys)

    # 爆発ポテンシャル判定
    total_hits = len(history)
    if has_explosion:
        explosion_potential = 'exploded'
    elif total_hits >= 30 and not has_explosion:
        # 30回以上当たって10連なし = 爆発しにくい展開
        explosion_potential = 'low'
    elif total_hits >= 15 and no_deep_valley and not has_explosion:
        # ハマらず淡々と当たるが爆発なし = モミモミ、このあと来る可能性
        explosion_potential = 'building'
    elif total_hits < 15:
        explosion_potential = 'unknown'
    else:
        explosion_potential = 'normal'

    # 直近の傾向と説明
    if is_on_fire:
        recent_trend = 'hot'
        if has_explosion:
            description = f'本日{max_rensa}連達成済み、連チャン継続中'
        else:
            description = f'連チャン中（直近{len(recent_valleys)}回全て100G以内）'
    elif has_explosion:
        recent_trend = 'exploded'
        description = f'【本日】{max_rensa}連の爆発あり！'
    elif explosion_potential == 'low':
        recent_trend = 'flat'
        description = f'【本日】{total_hits}ART消化、連荘控えめ → 高設定でもムラあり'
    elif explosion_potential == 'building':
        recent_trend = 'building'
        description = f'【本日】ハマりなく{total_hits}回当選中 → 連荘期待'
    elif no_deep_valley and avg_valley < 100:
        recent_trend = 'very_hot'
        description = f'絶好調（平均{avg_valley:.0f}G、最大{max_valley}G）'
    elif no_deep_valley:
        recent_trend = 'stable'
        description = f'ハマりなし安定（最大{max_valley}G）'
    elif max_valley >= 800:
        recent_trend = 'recovering'
        description = f'{max_valley}Gハマりあり → 天井後は様子見'
    else:
        recent_trend = 'normal'
        description = ''

    return {
        'no_deep_valley': no_deep_valley,
        'max_valley': max_valley,
        'is_on_fire': is_on_fire,
        'has_explosion': has_explosion,
        'max_rensa': max_rensa,
        'recent_trend': recent_trend,
        'description': description,
        'explosion_potential': explosion_potential,
    }


def generate_reasons(unit_id: str, trend: dict, today: dict, comparison: dict,
                     base_rank: str, final_rank: str, days: List[dict] = None,
                     today_history: List[dict] = None,
                     store_key: str = None,
                     is_today_data: bool = False,
                     current_at_games: int = 0,
                     data_date_label: str = None,
                     prev_date_label: str = None,
                     **kwargs) -> List[str]:
    """推奨理由を生成（台固有の根拠を最優先）

    優先順位:
    1. この台の過去ランク（なぜこの台なのか）
    2. 前日の実績分析（翌日予測の根拠）
    3. 連続パターン（設定変更サイクルの読み）
    4. 本日データ（稼働中の場合のみ）
    5. 店舗曜日傾向（補足情報）
    """
    reasons = []

    weekday_info = get_store_weekday_info(store_key) if store_key else {}
    store_name = weekday_info.get('short_name', '')
    today_weekday = weekday_info.get('today_weekday', '')
    today_rating = weekday_info.get('today_rating', 3)

    # 翌日/本日の表現（0:00〜10:00は「本日」、10:00〜24:00は「翌日」）
    _hour = datetime.now().hour
    next_day_label = '本日' if _hour < 10 else '翌日'

    total_games = today.get('total_games', 0)
    art_prob = today.get('art_prob', 0)

    consecutive_plus = trend.get('consecutive_plus', 0)
    consecutive_minus = trend.get('consecutive_minus', 0)
    yesterday_art = trend.get('yesterday_art', 0)
    yesterday_rb = trend.get('yesterday_rb', 0)
    yesterday_games = trend.get('yesterday_games', 0)
    day_before_art = trend.get('day_before_art', 0)
    day_before_games = trend.get('day_before_games', 0)

    # === 1. この台の過去ランク + 過去実績（なぜこの台を選んだのか） ===
    historical_perf = kwargs.get('historical_perf', {})
    good_day_rate = historical_perf.get('good_day_rate', 0)
    good_days = historical_perf.get('good_days', 0)
    total_perf_days = historical_perf.get('total_days', 0)
    miss_days = total_perf_days - good_days if total_perf_days > 0 else 0

    if total_perf_days > 0 and good_day_rate >= 0.7:
        # 好調率は🔄に連続好調が含まれる場合は省略（重複回避）
        # 連続好調3日以上なら🔄で「N日連続好調 + 曜日」が出るので好調率は冗長
        if consecutive_plus < 3:
            reasons.append(f"📊 {total_perf_days}日間中{good_days}日好調（好調率{good_day_rate:.0%}）→ 高設定が入りやすい台")

        # --- 好調の中身分析（ART回数・最大連チャンで好調レベルを可視化）---
        good_day_details = historical_perf.get('good_day_details', [])
        if good_day_details and len(good_day_details) >= 3:
            # 爆発レベル分類
            def _level(art):
                if art >= 80: return 'big'
                if art >= 50: return 'mid'
                return 'small'
            
            levels = [_level(d.get('art', 0)) for d in good_day_details]  # 新→古
            big_days = levels.count('big')
            mid_days = levels.count('mid')
            small_days = levels.count('small')
            total_good = len(levels)
            
            # --- 直近の推移パターンから次の予測 ---
            recent_levels = levels[:5]  # 直近5日の爆発レベル（新→古）
            
            # 直近で中/小が連続してたら「そろそろ大爆発」
            recent_non_big = 0
            for lv in recent_levels:
                if lv != 'big':
                    recent_non_big += 1
                else:
                    break
            
            # 大爆発→中/小→大爆発の交互パターン検出
            alternating = 0
            for i in range(len(recent_levels) - 1):
                if recent_levels[i] != recent_levels[i+1]:
                    alternating += 1
            alt_rate = alternating / max(len(recent_levels) - 1, 1)
            
            # 「中→中→大→中→大→中→大」のような推移を説明
            level_labels = {'big': '大', 'mid': '中', 'small': '小'}
            trend_str = '→'.join(level_labels[lv] for lv in reversed(recent_levels))
            
            # 最新日からの連続大爆発カウント
            consec_big = 0
            for lv in recent_levels:
                if lv == 'big':
                    consec_big += 1
                else:
                    break
            
            if recent_non_big >= 2 and big_days >= 1:
                # 中/小が2日以上続いてる → 大爆発予測
                reasons.append(f"🔥 直近の推移: {trend_str}（ART80回以上の大爆発がなく{recent_non_big}日経過。過去の傾向では中/小の後に大が来やすい）")
            elif consec_big >= 2:
                # 大爆発が2日以上連続
                reasons.append(f"🔥 直近の推移: {trend_str}（大爆発{consec_big}日連続 → 高設定据え置きの証拠）")
            elif alt_rate >= 0.6 and total_good >= 4:
                # 交互パターン
                last_level = recent_levels[0]
                next_expect = '大爆発' if last_level != 'big' else '中程度'
                reasons.append(f"🔥 直近の推移: {trend_str}（大小交互 → 本日は{next_expect}の可能性）")
            elif recent_non_big == 1 and recent_levels[0] != 'big':
                # 直近1日だけ中/小 → まだ大爆発の射程内
                reasons.append(f"🔥 直近の推移: {trend_str}（前日は低めだが高設定の範囲内 → 大爆発に期待）")
            elif total_good >= 3:
                reasons.append(f"🔥 直近の推移: {trend_str}（好調{total_good}日中、大{big_days}/中{mid_days}/小{small_days}日）")

        # --- この台の好調継続率 ---
        continuation_rate = historical_perf.get('continuation_rate', 0)
        continuation_total = historical_perf.get('continuation_total', 0)
        continuation_good = historical_perf.get('continuation_good', 0)
        if continuation_total >= 3:
            if continuation_rate >= 0.8:
                reasons.append(f"📊 この台は好調が続きやすい（過去{continuation_total}回中{continuation_good}回 = {continuation_rate:.0%}で翌日も好調）")
            elif continuation_rate >= 0.6:
                reasons.append(f"📊 好調→翌日も好調の実績: {continuation_good}/{continuation_total}回（{continuation_rate:.0%}）※やや期待できる程度")
            elif continuation_rate < 0.4:
                reasons.append(f"⚠️ この台は好調が長続きしにくい（{continuation_good}/{continuation_total}回 = {continuation_rate:.0%}）")

        # --- 連続好調の実績 ---
        max_streak = historical_perf.get('max_consecutive_good', 0)
        if consecutive_plus >= 3 and max_streak > 0:
            if consecutive_plus >= max_streak:
                reasons.append(f"📊 {consecutive_plus}日連続好調中 → この台の過去最長を更新中（店が高設定を入れ続けてる可能性）")
            elif consecutive_plus >= max_streak - 1:
                reasons.append(f"📊 {consecutive_plus}日連続好調中（この台の過去最長{max_streak}日にあと1日）")
            else:
                reasons.append(f"📊 {consecutive_plus}日連続好調中（この台の過去最長は{max_streak}日）")

        # 設定変更周期情報（Phase 2+）
        cycle_analysis = kwargs.get('cycle_analysis', {})
        analysis_phase = kwargs.get('analysis_phase', 1)
        if cycle_analysis and analysis_phase >= 2:
            cycle_parts = []
            # 現在の連続不調日数に基づく「次は好調」確率
            if consecutive_minus > 0:
                btg = cycle_analysis.get('bad_to_good', {})
                key = min(consecutive_minus, max(btg.keys())) if btg else 0
                if key and key in btg:
                    rate = btg[key]
                    if rate['total'] >= 2:
                        cycle_parts.append(f"{key}日不調が続いた後→好調に切り替わる率{rate['rate']:.0%}（{rate['good']}/{rate['total']}回）")
            # 連続好調中なら据え置き率（2日以上のみ）
            if consecutive_plus >= 2:
                gtg = cycle_analysis.get('good_to_good', {})
                key = min(consecutive_plus, max(gtg.keys())) if gtg else 0
                if key and key in gtg:
                    rate = gtg[key]
                    if rate['total'] >= 3 and rate['rate'] >= 0.7:
                        cycle_parts.append(f"{key}日連続好調の翌日も好調率{rate['rate']:.0%}（{rate['good']}/{rate['total']}回）")
            # 交互パターン
            alt_score = cycle_analysis.get('alternating_score', 0)
            if alt_score >= 0.6 and cycle_analysis.get('total_days', 0) >= 7:
                cycle_parts.append(f"交互パターン傾向あり({alt_score:.0%})")
            # 平均周期
            avg_cycle = cycle_analysis.get('avg_cycle', 0)
            if avg_cycle > 0 and cycle_analysis.get('total_days', 0) >= 7:
                if avg_cycle <= 1.5:
                    cycle_parts.append(f"ほぼ毎日好調になる台")
                elif avg_cycle <= 2.5:
                    cycle_parts.append(f"2日に1回くらい好調になる台")
                else:
                    cycle_parts.append(f"好調になるのは{avg_cycle:.0f}日に1回ペース")
            if cycle_parts:
                reasons.append(f"🔁 {' / '.join(cycle_parts)}")

        # 曜日パターン（Phase 3+）
        weekday_pattern = kwargs.get('weekday_pattern', {})
        if weekday_pattern and analysis_phase >= 3:
            wd_data = weekday_pattern.get(today_weekday, {})
            if wd_data.get('total', 0) >= 2:
                wd_rate = wd_data['rate']
                wd_total = wd_data['total']
                wd_good = wd_data['good']
                if wd_rate >= 0.7:
                    reasons.append(f"📅 この台の{today_weekday}曜好調率: {wd_good}/{wd_total}回({wd_rate:.0%}) → 期待大")
                elif wd_rate <= 0.3:
                    reasons.append(f"🚨 この台の{today_weekday}曜好調率: {wd_good}/{wd_total}回({wd_rate:.0%}) → 要注意")

        # 台個別の曜日別好調率（蓄積データから）
        unit_weekday = historical_perf.get('weekday_breakdown', {})
        if unit_weekday and today_weekday:
            uwd = unit_weekday.get(today_weekday, {})
            if uwd.get('total', 0) >= 3:  # サンプル3以上
                uwd_rate = uwd['good'] / uwd['total']
                if uwd_rate >= 0.8:
                    reasons.append(f"📅 この台の{today_weekday}曜実績: {uwd['good']}/{uwd['total']}回好調（{uwd_rate:.0%}）")
                elif uwd_rate <= 0.2:
                    reasons.append(f"🚨 この台の{today_weekday}曜実績: {uwd['good']}/{uwd['total']}回好調（{uwd_rate:.0%}）→ この曜日は弱い")

        # なぜ今日も好調と見るかの根拠を追加
        continuation_rate = historical_perf.get('continuation_rate', 0)
        continuation_total = historical_perf.get('continuation_total', 0)
        continuation_good = historical_perf.get('continuation_good', 0)

        # 店舗傾向は台の推奨理由としては表示しない（台固有データのみ）
        # 店舗傾向は店舗分析セクションで別途表示
    elif total_perf_days > 0 and good_day_rate <= 0.4:
        reasons.append(f"📊 {total_perf_days}日間中{good_days}日好調（好調率{good_day_rate:.0%}）→ 低設定が入りやすい台")
    elif base_rank == 'S':
        if total_perf_days > 0 and good_day_rate < 0.5:
            reasons.append(f"📊 過去データSランク（ただし直近{total_perf_days}日は好調{good_days}日のみ={good_day_rate:.0%}）")
        else:
            reasons.append(f"📊 過去データSランク: 高設定が頻繁に入る台")
    elif base_rank == 'A':
        consecutive_bad = historical_perf.get('consecutive_bad', 0)
        if total_perf_days > 0 and good_day_rate < 0.5:
            reasons.append(f"📊 過去データAランク（ただし直近{total_perf_days}日は好調{good_days}日のみ={good_day_rate:.0%}）")
        elif consecutive_bad >= 2:
            reasons.append(f"📊 過去データAランク（好調率{good_day_rate:.0%}だが直近{consecutive_bad}日連続不調中）")
        else:
            reasons.append(f"📊 過去データAランク: 高設定が入りやすい台")
    elif base_rank == 'B':
        reasons.append(f"📊 過去データBランク: 中間設定以上が多い台")

    # === 2. 連続パターン・傾向（設定変更サイクルの読み） ===
    # これが翌日予測の核心 — 前日単体の成績ではなく「流れ」
    # 蓄積データからの回復率統計を取得（店舗 → 足りなければ機種全体）
    _mk = kwargs.get('machine_key', 'sbj')
    _recovery = get_recovery_stats(store_key or '', _mk) if store_key else {}
    _machine_recovery = get_machine_recovery_stats(_mk)

    def _recovery_note(n):
        """N日連続不調の回復率注記を生成（店舗→機種全体フォールバック）"""
        rs = _recovery.get(n, {})
        if rs.get('total', 0) >= 2:
            return f"（この店の過去実績: {rs['recovered']}/{rs['total']}回={rs['rate']:.0%}で翌日回復）"
        mrs = _machine_recovery.get(n, {})
        if mrs.get('total', 0) >= 3:
            return f"（SBJ全店舗実績: {mrs['recovered']}/{mrs['total']}回={mrs['rate']:.0%}で翌日回復）"
        return ""

    if consecutive_minus >= 4:
        _r_note = _recovery_note(4)
        reasons.append(f"🔄 {consecutive_minus}日連続不調 → {next_day_label}設定変更の可能性大{_r_note}")
    elif consecutive_minus >= 3:
        _r_note = _recovery_note(3)
        reasons.append(f"🔄 {consecutive_minus}日連続不調 → そろそろ{next_day_label}設定上げ期待{_r_note}")
    elif consecutive_minus == 2:
        _r_note = _recovery_note(2)
        if today_rating >= 4:
            reasons.append(f"🔄 2日連続不調 + {store_name}の{today_weekday}曜は狙い目 → {next_day_label}リセット期待{_r_note}")
        else:
            reasons.append(f"🔄 2日連続不調 → {next_day_label}リセット期待{_r_note}")

    if consecutive_plus >= 3:
        if today_rating >= 4:
            reasons.append(f"🔄 {consecutive_plus}日連続好調 + {store_name}は{today_weekday}曜が狙い目 → 据え置き濃厚")
        elif today_rating <= 2:
            reasons.append(f"🔄 {consecutive_plus}日連続好調だが{store_name}の{today_weekday}曜は弱い日 → 転落警戒")
        else:
            reasons.append(f"🔄 {consecutive_plus}日連続好調 → 据え置き期待（ただし転落警戒も）")
    elif consecutive_plus == 2:
        if today_rating >= 4:
            reasons.append(f"🔄 2日連続好調 + {store_name}は{today_weekday}曜が狙い目 → 据え置き期待")
        elif today_rating <= 2:
            reasons.append(f"🔄 2日連続好調だが{store_name}の{today_weekday}曜は弱い日 → 下げの可能性")
        else:
            reasons.append(f"🔄 2日連続好調 → 据え置き期待")

    # 2日連続不調→翌日リセット期待
    yesterday_prob_val = trend.get('yesterday_prob', 0)
    day_before_prob_val = trend.get('day_before_prob', 0)
    if yesterday_prob_val >= 150 and day_before_prob_val >= 150:
        _r_note2 = _recovery_note(2)
        reasons.append(f"🔄 直近2日とも不調（1/{day_before_prob_val:.0f}→1/{yesterday_prob_val:.0f}）→ {next_day_label}設定変更期待大{_r_note2}")

    # ローテーションパターン
    if days:
        rotation = analyze_rotation_pattern(days, machine_key=_mk)
        if rotation['has_pattern'] and rotation['next_high_chance']:
            reasons.append(f"🔄 ローテ傾向: {rotation['description']} → {next_day_label}上げ期待")

    # === 2.5 稼働パターン分析 ===
    activity_data = kwargs.get('activity_data', {})
    if activity_data:
        activity_desc = activity_data.get('description', '')
        if activity_data.get('is_hyena_target'):
            reasons.append(f"🚨 {activity_desc}")
        elif activity_data.get('abandonment_type') == 'good_abandoned':
            reasons.append(f"💡 {activity_desc}")
        elif activity_data.get('persistence_score', 0) >= 8:
            reasons.append(f"📊 {activity_desc}")

    # === 3. 当日のリアルタイムデータ（営業中のみ有用） ===
    # 閉店後は「当日の結果」として表示（翌日予測の根拠にはしない）
    if total_games > 0 and is_today_data:
        if art_prob > 0 and art_prob <= 80:
            reasons.append(f"🔥 本日ART確率1/{art_prob:.0f} ({total_games:,}G消化) → 設定6域の挙動")
        elif art_prob > 0 and art_prob <= 100:
            reasons.append(f"🔥 本日ART確率1/{art_prob:.0f} ({total_games:,}G消化) → 高設定濃厚")
        elif art_prob > 0 and art_prob <= 130 and total_games >= 3000:
            reasons.append(f"🔥 本日1/{art_prob:.0f}で安定稼働中 ({total_games:,}G消化)")
        elif art_prob > 0 and art_prob >= 200:
            reasons.append(f"🚨 本日ART確率1/{art_prob:.0f} ({total_games:,}G消化) → 低設定域の挙動")

    # 本日の天井到達・連チャン判定（当日データのみ）
    if today_history and is_today_data:
        today_graph = analyze_today_graph(today_history)
        today_at_intervals = calculate_at_intervals(today_history)
        today_ceiling = sum(1 for g in today_at_intervals if g >= 999)
        if today_ceiling > 0:
            reasons.append(f"🔥 本日天井到達{today_ceiling}回 → 低設定の可能性に注意")
        if today_graph.get('has_explosion'):
            reasons.append(f"🔥 本日{today_graph['max_rensa']}連の爆発あり")
        elif today_graph.get('is_on_fire'):
            if current_at_games <= 100:
                reasons.append("🔥 連チャン中 → 高設定継続の期待")

    # === 3.5 出玉バランス判定 ===
    medal_balance_penalty = kwargs.get('medal_balance_penalty', 0)
    if medal_balance_penalty <= -8:
        reasons.append(f"🚨 出玉バランス悪い: ART多いが最大枚数少ない（低設定の可能性）")
    elif medal_balance_penalty <= -5:
        reasons.append(f"🚨 ART回数の割に出玉が伸びていない")

    # === 6. 店舗曜日傾向（補足情報） ===
    # 好調率の根拠で既に曜日情報を出してたら重複させない
    has_weekday_in_confidence = any('今日も期待できる根拠' in r and today_weekday in r for r in reasons)
    # 店舗の曜日傾向は台の推奨理由には含めない（店舗分析セクションで表示）
    # ただし弱い日の警告だけは残す
    if store_name and today_weekday and not has_weekday_in_confidence:
        if today_rating <= 2:
            worst_info = weekday_info.get('worst_days', '')
            reasons.append(f"⚠️ {store_name}の{today_weekday}曜は出玉が少ない傾向（注意）")

    # === フォールバック ===
    if not reasons:
        if base_rank in ('S', 'A', 'B'):
            reasons.append(f"過去データ{base_rank}ランク")
        if store_name and today_weekday:
            best_info = weekday_info.get('best_days', '')
            rating_label = {5: '高設定投入日', 4: '狙い目', 3: '普通', 2: '弱い日', 1: '回収日'}.get(today_rating, '普通')
            reasons.append(f"{store_name}の{today_weekday}曜は{rating_label}（店舗傾向{'：' + best_info if best_info else ''}）")

    # 根拠の優先度ソート（ローテ・周期・傾向を先に、過去ランクは後に）
    def _reason_priority(r):
        if '🔄' in r and ('ローテ' in r or '連続不調' in r or '連続好調' in r):
            return 0  # ローテ・設定変更パターン（台ごとに違う→差別化できる）
        if '🔁' in r:
            return 1  # 設定変更周期
        if '🔥' in r or '💡' in r:
            return 2  # 本日データ・期待根拠
        if '📈' in r:
            return 3  # 統計データ（台ごとに違う）
        if '📅' in r and 'この台の' in r:
            return 4  # 台固有の曜日傾向
        if '📅' in r:
            return 7  # 店舗全体の曜日傾向（全台共通→差別化できない→後回し）
        if '📊' in r and ('好調率' in r or '高設定' in r):
            return 8  # 過去ランク（差別化弱い→最後）
        return 5

    reasons.sort(key=_reason_priority)

    # 重複除去 + 同カテゴリ重複排除、上位4つ
    seen = set()
    seen_categories = set()
    unique = []
    for r in reasons:
        if r in seen:
            continue
        # 同カテゴリの重複を排除（店舗傾向が2回出るのを防ぐ等）
        category = None
        if '店舗傾向' in r:
            category = 'store_weekday'
        elif '好調翌日' in r:
            category = 'continuation'
        elif '好調率' in r and '台' in r:
            category = 'unit_rate'
        elif '平均ART' in r:
            category = 'avg_prob'
        if category and category in seen_categories:
            continue
        if category:
            seen_categories.add(category)
        seen.add(r)
        unique.append(r)

    # 「本日」「前日」「前々日」を日付ラベルに置換
    if data_date_label or prev_date_label:
        # 前々日ラベルを計算
        prev_prev_label = None
        if prev_date_label:
            try:
                # prev_date_labelから日付を逆算して前々日を求める
                import re as _re
                m = _re.match(r'(\d+)/(\d+)', prev_date_label)
                if m:
                    from datetime import datetime as _dt, timedelta as _td
                    _now = _dt.now()
                    _prev = _now.replace(month=int(m.group(1)), day=int(m.group(2)))
                    _prev2 = _prev - _td(days=1)
                    _weekdays = ['月','火','水','木','金','土','日']
                    prev_prev_label = f"{_prev2.month}/{_prev2.day}({_weekdays[_prev2.weekday()]})"
            except:
                prev_prev_label = f'{prev_date_label}の前日'

        replaced = []
        for r in unique[:5]:
            # 前々日を先に置換（「前日」の前に処理しないと重複置換される）
            if prev_prev_label:
                r = r.replace('前々日', prev_prev_label)
            if data_date_label:
                r = r.replace('本日', data_date_label)
            if prev_date_label:
                r = r.replace('前日', prev_date_label)
            replaced.append(r)
        return replaced

    # 店舗傾向の根拠を末尾に移動
    # 台個別のデータ根拠を優先、店舗全体の傾向は補助情報として最後
    store_reasons = []
    other_reasons = []
    for r in unique[:5]:
        # 「💡 ◯◯の◯曜は」パターン = 店舗傾向
        if r.startswith('💡') and '曜' in r and '店舗傾向' in r:
            store_reasons.append(r)
        # 「📅 ◯◯の◯曜は高設定投入日」パターン = 店舗傾向
        elif r.startswith('📅') and '曜' in r and ('投入日' in r or '狙い目' in r):
            store_reasons.append(r)
        else:
            other_reasons.append(r)
    return (other_reasons + store_reasons)[:5]


def generate_store_analysis(store_key: str, daily_data: dict = None) -> dict:
    """店舗の機種全体分析を生成

    Returns:
        {
            'store_name': str,
            'machine_name': str,
            'total_units': int,
            'rank_dist': str,         # "S:4台 / A:7台 / B:3台"
            'high_count': int,
            'high_ratio': float,
            'overall': str,           # 全体評価テキスト
            'weekday_info': dict,
            'daily_summary': str,
        }
    """
    store = STORES.get(store_key)
    if not store:
        return {}

    store_name = store.get('short_name', store.get('name', ''))
    machine_key = store.get('machine', 'sbj')
    machine_info = MACHINES.get(machine_key, {})
    units = store.get('units', [])
    total_units = len(units)

    # ランク分布（キーのミスマッチを考慮）
    rankings = RANKINGS.get(store_key, {})
    if not rankings:
        for suffix in ['_sbj', '_hokuto', '_hokuto_tensei2']:
            if store_key.endswith(suffix):
                alt_key = store_key[:-len(suffix)]
                rankings = RANKINGS.get(alt_key, {})
                if rankings:
                    break

    rank_counts = {'S': 0, 'A': 0, 'B': 0, 'C': 0, 'D': 0}
    scores = []
    for uid in units:
        rank_data = rankings.get(uid, {'rank': 'C', 'score': 50})
        rank = rank_data.get('rank', 'C')
        rank_counts[rank] = rank_counts.get(rank, 0) + 1
        scores.append(rank_data.get('score', 50))

    avg_score = sum(scores) / len(scores) if scores else 0
    high_count = rank_counts.get('S', 0) + rank_counts.get('A', 0)
    high_ratio = high_count / total_units * 100 if total_units > 0 else 0

    # ランク分布テキスト
    rank_parts = []
    for rank in ['S', 'A', 'B', 'C', 'D']:
        count = rank_counts.get(rank, 0)
        if count > 0:
            rank_parts.append(f"{rank}:{count}台")
    rank_dist_text = " / ".join(rank_parts)

    # 全体評価
    if high_ratio >= 70:
        overall = f"高設定台が非常に多い（全{total_units}台中{high_count}台がA以上）"
    elif high_ratio >= 50:
        overall = f"高設定台が多め（全{total_units}台中{high_count}台がA以上）"
    elif high_ratio >= 30:
        overall = f"高設定台あり（全{total_units}台中{high_count}台がA以上、台選びが重要）"
    else:
        overall = f"高設定台が少ない（全{total_units}台中{high_count}台がA以上）"

    # 曜日傾向
    weekday_info = get_store_weekday_info(store_key)

    # 日別データからの分析
    daily_summary = ""
    if daily_data:
        data_store_key = STORE_KEY_MAPPING.get(store_key, store_key)
        store_data = None
        for key in [data_store_key, store_key]:
            store_data = daily_data.get('stores', {}).get(key, {})
            if store_data:
                break

        if store_data and store_data.get('units'):
            total_art_all = 0
            total_games_all = 0
            total_days = 0
            for unit in store_data['units']:
                for day in unit.get('days', []):
                    art = day.get('art', 0)
                    games = day.get('total_start', 0)
                    if games > 0:
                        total_art_all += art
                        total_games_all += games
                        total_days += 1

            if total_days > 0:
                avg_art_per_unit_day = total_art_all / total_days
                if total_art_all > 0:
                    overall_prob = total_games_all / total_art_all
                    daily_summary = f"全台平均ART {avg_art_per_unit_day:.0f}回/日（確率1/{overall_prob:.0f}）"

    return {
        'store_name': store_name,
        'machine_name': machine_info.get('short_name', ''),
        'total_units': total_units,
        'rank_dist': rank_dist_text,
        'rank_counts': rank_counts,
        'high_count': high_count,
        'high_ratio': high_ratio,
        'overall': overall,
        'avg_score': avg_score,
        'weekday_info': weekday_info,
        'daily_summary': daily_summary,
    }


def recommend_units(store_key: str, realtime_data: dict = None, availability: dict = None,
                    data_date_label: str = None, prev_date_label: str = None) -> list:
    """推奨台リストを生成

    Args:
        store_key: 店舗キー
        realtime_data: リアルタイムで取得したデータ（オプション）
        availability: リアルタイム空き状況 {台番号: '空き' or '遊技中'}

    Returns:
        推奨台リスト（スコア順）
    """
    store = STORES.get(store_key)
    if not store:
        return []

    store_name = store.get('short_name', store.get('name', ''))
    machine_key = get_machine_from_store_key(store_key)
    machine_info = MACHINES.get(machine_key, {})

    # JSONデータ内の店舗キーを取得
    data_store_key = STORE_KEY_MAPPING.get(store_key, store_key)

    store_rankings = RANKINGS.get(store_key, {})
    recommendations = []

    # 日別データを読み込み
    daily_data = load_daily_data(machine_key=machine_key)

    # 全台の当日データを収集（比較用）
    all_units_today = []
    if realtime_data and 'units' in realtime_data:
        all_units_today = realtime_data.get('units', [])
    elif daily_data:
        # データ内の店舗キーで検索（複数パターンを試行）
        store_data = None
        for key_to_try in [data_store_key, store_key, f'{store_key}_sbj']:
            store_data = daily_data.get('stores', {}).get(key_to_try, {})
            if store_data:
                break

        if store_data:
            for unit in store_data.get('units', []):
                # 当日データを探す
                today_str = datetime.now().strftime('%Y-%m-%d')
                for day in unit.get('days', []):
                    if day.get('date') == today_str:
                        all_units_today.append(day)
                        break

    for unit_id in store.get('units', []):
        # 基本ランキング
        ranking = get_unit_ranking(store_key, unit_id)
        base_score = ranking.get('score', 50)
        base_rank = ranking.get('rank', 'C')
        note = ranking.get('note', '')
        has_static_ranking = note != '未評価'

        # 過去データからトレンド分析
        trend_data = {'reasons': []}
        unit_history = None

        # 日別データから過去履歴を取得
        if daily_data:
            # データ内の店舗キーで検索（複数パターンを試行）
            store_data = None
            for key_to_try in [data_store_key, store_key, f'{store_key}_sbj']:
                store_data = daily_data.get('stores', {}).get(key_to_try, {})
                if store_data:
                    break

            if store_data:
                for unit in store_data.get('units', []):
                    if unit.get('unit_id') == unit_id:
                        unit_history = unit
                        days = unit.get('days', [])
                        trend_data = analyze_trend(days, machine_key)
                        break

        # ランキングデータが無い場合、日別データからbase_scoreを動的計算
        if not has_static_ranking and unit_history:
            _days = unit_history.get('days', [])
            # 有効データがある日のみ使用
            _day_probs = []
            for _d in _days:
                _a = _d.get('art', 0)
                _g = _d.get('total_start', 0)
                if _a > 0 and _g > 500:
                    _day_probs.append(_g / _a)
            if len(_day_probs) >= 2:
                _avg = sum(_day_probs) / len(_day_probs)
                _worst = max(_day_probs)  # 最悪日（確率が高い=悪い）

                # 店舗内の全台の平均確率を計算して相対評価
                # これにより「北斗の全ARTベースで全台good域に見える」問題を回避
                _store_probs = []
                if store_data:
                    for _su in store_data.get('units', []):
                        _sd = _su.get('days', [])
                        _sp = []
                        for _dd in _sd:
                            _sa = _dd.get('art', 0); _sg = _dd.get('total_start', 0)
                            if _sa > 0 and _sg > 500:
                                _sp.append(_sg / _sa)
                        if _sp:
                            _store_probs.append(sum(_sp) / len(_sp))

                if len(_store_probs) >= 5:
                    # 店舗内相対評価: パーセンタイルでbase_scoreを決定
                    _store_probs_sorted = sorted(_store_probs)
                    _rank_pos = sum(1 for p in _store_probs_sorted if p > _avg)  # 小さい方が良い
                    _pct = _rank_pos / len(_store_probs_sorted)
                    if _pct >= 0.85:  # 上位15%
                        base_score = 70
                    elif _pct >= 0.65:  # 上位35%
                        base_score = 60
                    elif _pct >= 0.35:  # 中間
                        base_score = 50
                    else:  # 下位35%
                        base_score = 42
                else:
                    # 台数が少ない場合は絶対評価
                    _good = get_machine_threshold(machine_key, 'good_prob')
                    _bad = get_machine_threshold(machine_key, 'bad_prob')
                    if _avg <= _good * 0.65 and _worst <= _good and len(_day_probs) >= 4:
                        base_score = 70
                    elif _avg <= _good * 0.85 and _worst <= _bad:
                        base_score = 60
                    elif _avg <= _bad:
                        base_score = 50
                    else:
                        base_score = 42
                base_rank = get_rank(base_score)

        # 当日データ分析
        today_analysis = {'status': '-', 'today_score_bonus': 0, 'today_reasons': []}

        # リアルタイムデータの日付検証（今日のデータのみ使用）
        realtime_is_today = False
        if realtime_data:
            fetched_at = realtime_data.get('fetched_at', '')
            if fetched_at:
                try:
                    fetch_date = datetime.fromisoformat(fetched_at).strftime('%Y-%m-%d')
                    today_str_check = datetime.now().strftime('%Y-%m-%d')
                    realtime_is_today = (fetch_date == today_str_check)
                except:
                    pass

        # リアルタイムデータがあり、かつ今日のデータの場合のみ使用
        if realtime_data and realtime_is_today:
            units_list = None
            if 'units' in realtime_data:
                units_list = realtime_data.get('units', [])
            elif 'stores' in realtime_data:
                # データ内の店舗キーで検索
                store_data = None
                for key_to_try in [data_store_key, store_key, f'{store_key}_sbj']:
                    store_data = realtime_data.get('stores', {}).get(key_to_try, {})
                    if store_data:
                        break
                if store_data:
                    units_list = store_data.get('units', [])

            if units_list:
                for unit in units_list:
                    if unit.get('unit_id') == unit_id:
                        today_analysis = analyze_today_data(unit, machine_key=machine_key)
                        break

        # 日別データからも分析（リアルタイムデータがない場合）
        if daily_data and today_analysis.get('status') == '-':
            # データ内の店舗キーで検索
            store_data = None
            for key_to_try in [data_store_key, store_key, f'{store_key}_sbj']:
                store_data = daily_data.get('stores', {}).get(key_to_try, {})
                if store_data:
                    break

            if store_data:
                for unit in store_data.get('units', []):
                    if unit.get('unit_id') == unit_id:
                        today_analysis = analyze_today_data(unit, machine_key=machine_key)
                        break

        # 他台との比較
        comparison = compare_with_others(store_key, unit_id, all_units_today)

        # === トレンドによるスコア調整 ===
        trend_bonus = 0
        if trend_data.get('consecutive_minus', 0) >= 3:
            trend_bonus += 10  # 凹み続きは上げ期待
        elif trend_data.get('consecutive_minus', 0) >= 2:
            trend_bonus += 5
        if trend_data.get('yesterday_result') == 'big_minus':
            trend_bonus += 8  # 昨日大幅マイナスは狙い目
        elif trend_data.get('yesterday_result') == 'big_plus':
            trend_bonus -= 5  # 昨日大幅プラスは据え置きor下げ警戒

        if trend_data.get('consecutive_plus', 0) >= 3:
            trend_bonus += 5  # 連続プラスは据え置き期待
        if trend_data.get('art_trend') == 'improving':
            trend_bonus += 3

        # === 【改善1】台番号ごとの的中率（過去実績）をスコアに反映 ===
        # 蓄積DBがあればそちらを優先（長期データ）
        from analysis.history_accumulator import (
            load_unit_history as load_accumulated_history,
            get_analysis_phase, analyze_setting_change_cycle,
            analyze_weekday_pattern,
        )
        accumulated = load_accumulated_history(store_key, unit_id)
        analysis_phase = get_analysis_phase(accumulated)
        cycle_analysis = {}
        weekday_pattern = {}

        # 蓄積データがあれば、unit_historyのdaysをマージ
        if accumulated.get('days') and unit_history:
            # 蓄積DBの日付を優先、unit_historyで補完
            acc_dates = {d['date'] for d in accumulated['days']}
            for d in unit_history.get('days', []):
                if d.get('date') and d['date'] not in acc_dates:
                    accumulated['days'].append({
                        'date': d['date'],
                        'art': d.get('art', 0),
                        'games': d.get('total_start', 0),
                        'prob': d.get('total_start', 0) / d.get('art', 1) if d.get('art', 0) > 0 else 0,
                        'is_good': (d.get('total_start', 0) / d.get('art', 1) if d.get('art', 0) > 0 else 999) <= (130 if machine_key == 'sbj' else 330) and d.get('art', 0) >= (20 if machine_key == 'sbj' else 10),
                    })
            accumulated['days'].sort(key=lambda x: x.get('date', ''))
            analysis_phase = get_analysis_phase(accumulated)

        # 蓄積DBの方がdaily JSONより新しいデータを持っている場合、
        # trend_dataを蓄積DBのdaysで再計算する
        if accumulated.get('days'):
            acc_days_for_trend = []
            for d in accumulated['days']:
                _games = d.get('total_start', 0) or d.get('games', 0)
                acc_days_for_trend.append({
                    'date': d.get('date', ''),
                    'art': d.get('art', 0),
                    'total_start': _games,
                    'games': _games,
                    'rb': d.get('rb', 0),
                    'prob': d.get('prob', 0),
                    'history': d.get('history', []),
                    'max_rensa': d.get('max_rensa', 0),
                    'max_medals': d.get('max_medals', 0),
                })
            trend_from_acc = analyze_trend(acc_days_for_trend, machine_key)
            # 蓄積DBの方が新しいデータがあれば、trend_dataを上書き
            acc_latest = max(d.get('date', '') for d in accumulated['days']) if accumulated['days'] else ''
            trend_latest = trend_data.get('yesterday_date', '')
            if acc_latest > trend_latest:
                trend_data = trend_from_acc

        # Phase 2+: 設定変更周期分析
        if analysis_phase >= 2:
            cycle_analysis = analyze_setting_change_cycle(accumulated, machine_key)
        # Phase 3+: 曜日別パターン
        if analysis_phase >= 3:
            weekday_pattern = analyze_weekday_pattern(accumulated, machine_key)

        # 過去の好調率が高い台にボーナス、低い台にペナルティ
        historical_bonus = 0
        historical_perf = {}
        perf_days = accumulated.get('days', []) if accumulated.get('days') else (unit_history.get('days', []) if unit_history else [])
        if perf_days:
            historical_perf = calculate_unit_historical_performance(perf_days, machine_key)
            historical_bonus = historical_perf.get('score_bonus', 0)

        # === 【改善2】前日不調→翌日狙い目の重み付け強化 ===
        # 前日不調（1/150以上）の台は、翌日設定変更で上がる可能性が75%
        # 2日連続不調の台はさらにスコアアップ（設定変更期待）
        slump_bonus = 0
        yesterday_prob = trend_data.get('yesterday_prob', 0)
        day_before_prob = trend_data.get('day_before_prob', 0)
        bad_prob_threshold = get_machine_threshold(machine_key, 'bad_prob')

        if yesterday_prob >= bad_prob_threshold:
            slump_bonus += 5  # 前日不調 → 翌日設定変更期待
            if day_before_prob >= bad_prob_threshold:
                slump_bonus += 5  # 2日連続不調 → さらに設定変更期待（合計+10）

        # === 出玉バランス判定 ===
        # ART回数が多いのに最大枚数が少ない → 連チャンが弱い = 低設定の可能性
        # 北斗で50回当たって最大2574枚のようなケースにペナルティ
        medal_balance_penalty = 0
        if realtime_data and realtime_is_today:
            units_list = realtime_data.get('units', [])
            for _unit in units_list:
                if _unit.get('unit_id') == unit_id:
                    _art = _unit.get('art', 0)
                    _max_medals = _unit.get('max_medals', 0)
                    if machine_key == 'sbj':
                        if _art >= 50 and _max_medals > 0 and _max_medals < 5000:
                            medal_balance_penalty = -8  # ART50回以上で最大5000枚未満
                        elif _art >= 30 and _max_medals > 0 and _max_medals < 3000:
                            medal_balance_penalty = -5  # ART30回以上で最大3000枚未満
                    elif machine_key == 'hokuto_tensei2':
                        if _art >= 50 and _max_medals > 0 and _max_medals < 3000:
                            medal_balance_penalty = -10  # AT50回以上で最大3000枚未満
                        elif _art >= 30 and _max_medals > 0 and _max_medals < 3000:
                            medal_balance_penalty = -5  # AT30回以上で最大3000枚未満
                    break

        # === 【改善4】稼働パターン分析 ===
        activity_bonus = 0
        activity_data = {}
        if unit_history:
            # 直近日の履歴データで稼働パターン分析
            sorted_unit_days = sorted(
                unit_history.get('days', []),
                key=lambda x: x.get('date', ''), reverse=True
            )
            for day_item in sorted_unit_days:
                hist_for_activity = day_item.get('history', [])
                if hist_for_activity:
                    activity_data = analyze_activity_pattern(hist_for_activity, day_item)
                    activity_bonus = (
                        activity_data.get('persistence_score', 0)
                        + activity_data.get('abandonment_bonus', 0)
                        + activity_data.get('hyena_penalty', 0)  # 【改善5】ハイエナペナルティ
                    )
                    # 稼働パターンボーナスは最大±10に制限
                    activity_bonus = max(-10, min(10, activity_bonus))
                    break

        # === 曜日ボーナス ===
        # 店舗の曜日傾向をスコアに反映（rating 1-5 → -6 〜 +6）
        weekday_info = get_store_weekday_info(store_key) if store_key else {}
        _today_rating = weekday_info.get('today_rating', 3)
        weekday_bonus = (_today_rating - 3) * 3  # rating3=0, rating5=+6, rating1=-6

        # === 前日差枚ボーナス ===
        # 前日に大爆発した台 = 高設定が入ってた = 翌日据え置き期待
        yesterday_diff_bonus = 0
        _yd = trend_data.get('yesterday_diff', 0)
        if _yd >= 5000:
            yesterday_diff_bonus = 8
        elif _yd >= 3000:
            yesterday_diff_bonus = 5
        elif _yd >= 1000:
            yesterday_diff_bonus = 3
        elif _yd <= -3000:
            yesterday_diff_bonus = 3  # 大負け翌日は設定変更期待

        # === 店舗設定投入パターンボーナス ===
        # 店舗固有の設定投入癖（据え置き率、特定日傾向、台番傾向等）を補正
        # 既存の weekday_bonus / slump_bonus は一般的な傾向、
        # pattern_bonus は店舗固有のデータ実績ベース
        pattern_bonus = 0
        try:
            from analysis.store_pattern import calculate_pattern_bonus
            _target_date = datetime.now().strftime('%Y-%m-%d')
            pattern_bonus = calculate_pattern_bonus(store_key, machine_key, unit_id, _target_date)
        except Exception:
            pass

        # === 最終スコア計算 ===
        today_bonus = today_analysis.get('today_score_bonus', 0)
        prediction_bonus = (trend_bonus
                       + historical_bonus   # 【改善1】過去実績ボーナス
                       + slump_bonus        # 【改善2】不調翌日ボーナス
                       + activity_bonus     # 【改善4+5】稼働パターン+ハイエナ
                       + medal_balance_penalty  # 出玉バランスペナルティ
                       + weekday_bonus      # 曜日ボーナス
                       + yesterday_diff_bonus  # 前日差枚ボーナス
                       + pattern_bonus      # 店舗設定投入パターンボーナス
                       )
        # 前日予想ボーナスは±20にキャップ
        prediction_bonus = max(-20, min(20, prediction_bonus))

        # 営業中の当日データはキャップ外（信頼できるリアルタイムデータは重い）
        # 閉店後はtoday_bonus=0なので影響なし
        raw_score = base_score + prediction_bonus + today_bonus

        # === フィードバック補正 ===
        # 過去の答え合わせ結果から台・曜日の補正を適用
        feedback_bonus = 0
        try:
            from analysis.feedback import calculate_correction_factors
            corrections = calculate_correction_factors(store_key, machine_key)
            if corrections['confidence'] > 0:
                # 台番号補正
                uid_str = str(unit_id)
                unit_corr = corrections['unit_corrections'].get(uid_str, 0)
                # 曜日補正
                wd_name = ['月', '火', '水', '木', '金', '土', '日'][datetime.now().weekday()]
                wd_corr = corrections['weekday_corrections'].get(wd_name, 0)
                feedback_bonus = int((unit_corr + wd_corr) * corrections['confidence'])
        except Exception:
            pass

        final_score = raw_score + feedback_bonus
        # 【改善3】ランクは後でまとめて相対評価で決定するため、ここでは仮ランク
        final_rank = get_rank(final_score)

        # 推奨理由を生成（過去日データと当日履歴を渡す）
        unit_days = []
        today_history = []
        history_date = ''
        if unit_history:
            unit_days = unit_history.get('days', [])
            # 当日の履歴を取得（なければ直近日にフォールバック）
            today_str = datetime.now().strftime('%Y-%m-%d')
            yesterday_str = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            for day in unit_days:
                if day.get('date') == today_str:
                    today_history = day.get('history', [])
                    history_date = today_str
                    break
            if not today_history:
                # 当日データがない場合、直近の履歴データを探す（日付降順）
                # ただしフォールバック元の日付を記録（today扱いしない）
                sorted_days = sorted(unit_days, key=lambda x: x.get('date', ''), reverse=True)
                for day in sorted_days:
                    if day.get('history'):
                        today_history = day.get('history', [])
                        history_date = day.get('date', '')
                        break

        # データ日付を取得（今日 or 昨日）
        data_date = today_analysis.get('data_date', '')
        is_today_data = data_date == datetime.now().strftime('%Y-%m-%d') if data_date else False

        # 現在のハマりG数（generate_reasonsで連チャン中判定に必要）
        _final_start = 0
        if realtime_data and realtime_is_today:
            for _u in (realtime_data.get('units', [])):
                if _u.get('unit_id') == unit_id:
                    _final_start = _u.get('final_start', 0)
                    break
        current_at_games = 0
        if today_history and _final_start > 0:
            current_at_games = calculate_current_at_games(today_history, _final_start)
        elif _final_start > 0:
            current_at_games = _final_start

        reasons = generate_reasons(
            unit_id, trend_data, today_analysis, comparison, base_rank, final_rank,
            days=unit_days, today_history=today_history, store_key=store_key,
            is_today_data=is_today_data, current_at_games=current_at_games,
            historical_perf=historical_perf, activity_data=activity_data,
            medal_balance_penalty=medal_balance_penalty,
            data_date_label=data_date_label, prev_date_label=prev_date_label,
            cycle_analysis=cycle_analysis, weekday_pattern=weekday_pattern,
            analysis_phase=analysis_phase,
        )

        # リアルタイム空き状況がある場合は上書き
        status = today_analysis.get('status', '不明')
        is_running = today_analysis.get('is_running', False)
        availability_status = None

        if availability:
            avail = availability.get(unit_id)
            if avail:
                availability_status = avail
                if avail == '遊技中':
                    is_running = True
                    status = '遊技中'
                elif avail == '空き':
                    is_running = False
                    status = '空き'

        # 差枚見込み計算
        total_games = today_analysis.get('total_games', 0)
        art_count = today_analysis.get('art_count', 0)
        profit_info = calculate_expected_profit(total_games, art_count, machine_key)

        # max_medals, final_start をリアルタイムデータから取得（今日のデータのみ）
        max_medals = 0
        final_start = 0
        if realtime_data and realtime_is_today:
            units_list = realtime_data.get('units', [])
            for unit in units_list:
                if unit.get('unit_id') == unit_id:
                    max_medals = unit.get('max_medals', 0)
                    final_start = unit.get('final_start', 0)
                    # リアルタイムデータに当日履歴があればそちらを使う
                    rt_history = unit.get('today_history')
                    if rt_history and (not today_history or len(rt_history) > len(today_history)):
                        today_history = rt_history
                    break

        # 現在のAT間G数を正しく計算（最終大当たりからのG数）
        # final_startだけでは最終RB後のG数しか分からないため、
        # 履歴から最終大当たり以降の全G数を合算する
        current_at_games = 0
        if today_history and final_start > 0:
            current_at_games = calculate_current_at_games(today_history, final_start)
        elif final_start > 0:
            current_at_games = final_start  # 履歴がない場合はfinal_startをそのまま使用

        # 本日のAT間データ（履歴から計算）
        today_at_intervals = calculate_at_intervals(today_history) if today_history else []
        today_deep_hama_count = sum(1 for g in today_at_intervals if g >= 500)  # 500G以上のハマり
        today_max_at_interval = max(today_at_intervals) if today_at_intervals else 0
        today_max_rensa = calculate_max_rensa(today_history) if today_history else 0

        rec = {
            'unit_id': unit_id,
            'store_key': store_key,
            'store_name': store_name,
            'machine_name': machine_info.get('short_name', ''),
            'base_rank': base_rank,
            'base_score': base_score,
            'final_rank': final_rank,
            'final_score': final_score,
            'status': status,
            'is_running': is_running,
            'availability': availability_status,  # リアルタイム空き状況
            'art_count': art_count,
            'bb_count': today_analysis.get('bb_count', 0),
            'rb_count': today_analysis.get('rb_count', 0),
            'total_games': total_games,
            'art_prob': today_analysis.get('art_prob', 0),
            'last_hit_time': today_analysis.get('last_hit_time'),
            'first_hit_time': today_analysis.get('first_hit_time'),
            'note': note,
            'has_static_ranking': has_static_ranking,
            # データ日付情報
            'data_date': data_date,
            'is_today_data': is_today_data,
            # 詳細分析データ
            'trend': trend_data,
            'comparison': comparison,
            'reasons': reasons,
            # サマリー
            'yesterday_diff': trend_data.get('yesterday_diff', 0),
            'yesterday_art': trend_data.get('yesterday_art', 0),
            'yesterday_rb': trend_data.get('yesterday_rb', 0),
            'yesterday_games': trend_data.get('yesterday_games', 0),
            'yesterday_date': trend_data.get('yesterday_date', ''),
            'yesterday_prob': trend_data.get('yesterday_prob', 0),
            'day_before_art': trend_data.get('day_before_art', 0),
            'day_before_rb': trend_data.get('day_before_rb', 0),
            'day_before_games': trend_data.get('day_before_games', 0),
            'day_before_date': trend_data.get('day_before_date', ''),
            'day_before_prob': trend_data.get('day_before_prob', 0),
            'yesterday_max_rensa': trend_data.get('yesterday_max_rensa', 0),
            'yesterday_max_medals': trend_data.get('yesterday_max_medals', 0),
            'max_medals': max_medals,
            # 3日目のデータ（蓄積DBから取得）
            'three_days_ago_art': 0,
            'three_days_ago_rb': 0,
            'three_days_ago_games': 0,
            'three_days_ago_date': '',
            'consecutive_plus': trend_data.get('consecutive_plus', 0),
            'consecutive_minus': trend_data.get('consecutive_minus', 0),
            'avg_art_7days': trend_data.get('avg_art_7days', 0),
            'recent_days': trend_data.get('recent_days', []),
            # 現在のスタート（最終大当たり後のG数、RBを跨いで正確に計算）
            'current_hama': current_at_games,
            # 本日のAT間分析
            'today_deep_hama': today_deep_hama_count,  # 500G以上のハマり回数
            'today_max_at_interval': today_max_at_interval,  # 本日最大AT間
            'today_max_rensa': today_max_rensa,  # 本日最大連チャン数
            # スコア内訳（デバッグ・分析用）
            'score_breakdown': {
                'base': base_score,
                'today_bonus': today_analysis.get('today_score_bonus', 0),
                'trend_bonus': trend_bonus,
                'historical_bonus': historical_bonus,
                'slump_bonus': slump_bonus,
                'activity_bonus': activity_bonus,
                'medal_balance_penalty': medal_balance_penalty,
            },
            # 過去実績データ【改善1】
            'historical_perf': historical_perf,
            # 稼働パターンデータ【改善4+5】
            'activity_data': activity_data,
            # 差枚見込み（内部計算用）
            'current_estimate': profit_info['current_estimate'],
            'closing_estimate': profit_info['closing_estimate'],
            'profit_category': profit_info['profit_category'],
            'estimated_setting': profit_info['setting_info']['estimated_setting'],
            'setting_num': profit_info['setting_info'].get('setting_num', 0),
            'payout_estimate': profit_info['setting_info']['payout_estimate'],
            # 当日履歴（波グラフ・当たり一覧用）
            'today_history': today_history,
            'history_date': history_date,
        }

        # リアルタイムデータが昨日のものだった場合、前日データとして補完
        if realtime_data and not realtime_is_today and not rec['yesterday_art']:
            fetched_at = realtime_data.get('fetched_at', '')
            if fetched_at:
                try:
                    fetch_date_str = datetime.fromisoformat(fetched_at).strftime('%Y-%m-%d')
                    yesterday_check = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
                    if fetch_date_str == yesterday_check:
                        # 昨日のリアルタイムデータを前日データとして使用
                        units_list = realtime_data.get('units', [])
                        for unit in units_list:
                            if unit.get('unit_id') == unit_id:
                                rec['yesterday_art'] = unit.get('art', 0)
                                rec['yesterday_rb'] = unit.get('rb', 0)
                                rec['yesterday_games'] = unit.get('total_start', 0)
                                rec['yesterday_date'] = fetch_date_str
                                break
                except:
                    pass

        # 蓄積DBから3日目+各日の最大連チャン・最大枚数を取得
        if accumulated and accumulated.get('days'):
            acc_days = sorted(accumulated['days'], key=lambda x: x.get('date', ''), reverse=True)
            y_date = rec.get('yesterday_date', '')
            db_date = rec.get('day_before_date', '')

            # 各日の最大連チャン・最大枚数を蓄積DBから補完
            for ad in acc_days:
                d = ad.get('date', '')
                if d == y_date:
                    if not rec.get('yesterday_max_rensa'):
                        rec['yesterday_max_rensa'] = ad.get('max_rensa', 0)
                    if not rec.get('yesterday_max_medals'):
                        rec['yesterday_max_medals'] = ad.get('max_medals', 0)
                    if not rec.get('yesterday_history') and ad.get('history'):
                        rec['yesterday_history'] = ad['history']
                elif d == db_date:
                    rec['day_before_max_rensa'] = ad.get('max_rensa', 0)
                    rec['day_before_max_medals'] = ad.get('max_medals', 0)
                    if ad.get('history'):
                        rec['day_before_history'] = ad['history']
                elif d and d < (db_date or y_date or '9999') and not rec.get('three_days_ago_date'):
                    rec['three_days_ago_art'] = ad.get('art', 0)
                    rec['three_days_ago_rb'] = ad.get('rb', 0)
                    rec['three_days_ago_games'] = ad.get('games', 0)
                    rec['three_days_ago_date'] = d
                    rec['three_days_ago_max_rensa'] = ad.get('max_rensa', 0)
                    rec['three_days_ago_max_medals'] = ad.get('max_medals', 0)
                    if ad.get('history'):
                        rec['three_days_ago_history'] = ad['history']
                    _3d_art = ad.get('art', 0)
                    _3d_games = ad.get('games', 0)
                    rec['three_days_ago_prob'] = round(_3d_games / _3d_art) if _3d_art > 0 and _3d_games > 0 else 0

        # 閉店後: availabilityのデータを補完
        # 注意: availabilityのtoday_historyの日付と蓄積DBのyesterday_dateが異なる場合がある
        # availability=1/27, yesterday_date=1/26 → availabilityは「前日」でなく「最新日」
        if not realtime_is_today and realtime_data:
            # availabilityのデータ日付を取得
            rt_fetched = realtime_data.get('fetched_at', '')
            rt_date = ''
            if rt_fetched:
                try:
                    rt_date = datetime.fromisoformat(rt_fetched.replace('Z', '+00:00')).strftime('%Y-%m-%d')
                except:
                    pass

            y_date = rec.get('yesterday_date', '')
            # availabilityのデータがyesterday_dateより新しい場合、
            # yesterdayフィールドを上にずらして、availabilityデータをyesterdayに入れる
            is_newer = rt_date and y_date and rt_date > y_date

            units_list = realtime_data.get('units', [])
            for _unit in units_list:
                if _unit.get('unit_id') == unit_id:
                    rt_hist = _unit.get('today_history', [])
                    _rt_art = _unit.get('art', 0)
                    _rt_total = _unit.get('total_start', 0)
                    _rt_rb = _unit.get('rb', 0)
                    rt_max = _unit.get('max_medals', 0)

                    if is_newer and _rt_art > 0:
                        # availabilityが最新日 → 全データを1日ずつずらす
                        if rec.get('yesterday_art'):
                            # day_before → three_days_ago
                            rec['three_days_ago_art'] = rec.get('day_before_art', 0)
                            rec['three_days_ago_rb'] = rec.get('day_before_rb', 0)
                            rec['three_days_ago_games'] = rec.get('day_before_games', 0)
                            rec['three_days_ago_date'] = rec.get('day_before_date', '')
                            rec['three_days_ago_diff_medals'] = rec.get('day_before_diff_medals')
                            rec['three_days_ago_max_rensa'] = rec.get('day_before_max_rensa', 0)
                            rec['three_days_ago_max_medals'] = rec.get('day_before_max_medals', 0)
                            rec['three_days_ago_prob'] = rec.get('day_before_prob', 0)

                            # yesterday → day_before
                            rec['day_before_art'] = rec.get('yesterday_art', 0)
                            rec['day_before_rb'] = rec.get('yesterday_rb', 0)
                            rec['day_before_games'] = rec.get('yesterday_games', 0)
                            rec['day_before_date'] = rec.get('yesterday_date', '')
                            rec['day_before_diff_medals'] = rec.get('yesterday_diff_medals')
                            rec['day_before_max_rensa'] = rec.get('yesterday_max_rensa', 0)
                            rec['day_before_max_medals'] = rec.get('yesterday_max_medals', 0)
                            rec['day_before_prob'] = rec.get('yesterday_prob', 0)

                        # availabilityデータをyesterdayに設定
                        rec['yesterday_art'] = _rt_art
                        rec['yesterday_rb'] = _rt_rb
                        rec['yesterday_games'] = _rt_total
                        rec['yesterday_date'] = rt_date
                        rec['yesterday_prob'] = round(_rt_total / _rt_art) if _rt_art > 0 else 0
                        rec['today_history'] = rt_hist

                        # 連チャン・最大枚数
                        if rt_hist:
                            from analysis.history_accumulator import _calc_history_stats
                            calc_rensa, calc_medals = _calc_history_stats(rt_hist)
                            rec['yesterday_max_rensa'] = calc_rensa
                            rec['yesterday_max_medals'] = rt_max if rt_max > 0 else calc_medals
                        else:
                            rec['yesterday_max_rensa'] = 0
                            rec['yesterday_max_medals'] = rt_max

                        # 差枚はgenerate_static.pyのcalculate_expected_profitで計算する
                        # today_historyからの計算は不正確（medalsはART中払い出しのみ）
                        rec['yesterday_diff_medals'] = None
                    else:
                        # 同じ日付 or 日付不明 → 既存yesterdayを補完するだけ
                        if rt_hist and not rec.get('yesterday_max_rensa'):
                            from analysis.history_accumulator import _calc_history_stats
                            calc_rensa, calc_medals = _calc_history_stats(rt_hist)
                            if calc_rensa > 0:
                                rec['yesterday_max_rensa'] = calc_rensa
                            if rt_max > 0:
                                rec['yesterday_max_medals'] = rt_max
                            elif calc_medals > 0:
                                rec['yesterday_max_medals'] = calc_medals
                            rec['today_history'] = rt_hist
                        if not rec.get('yesterday_prob') and _rt_art > 0 and _rt_total > 0:
                            rec['yesterday_prob'] = round(_rt_total / _rt_art)
                        # 差枚はgenerate_static.pyで計算（history計算は不正確）
                        if not rec.get('yesterday_rb') and _rt_rb > 0:
                            rec['yesterday_rb'] = _rt_rb
                    break

        # フォールバック後のデータでローテ傾向を再計算
        # （蓄積DBのdaysにavailabilityの最新日が含まれない問題を修正）
        if rec.get('yesterday_art') and rec.get('yesterday_games'):
            _rot_days = []
            for prefix, date_key in [('yesterday', 'yesterday_date'),
                                      ('day_before', 'day_before_date'),
                                      ('three_days_ago', 'three_days_ago_date')]:
                _a = rec.get(f'{prefix}_art', 0)
                _g = rec.get(f'{prefix}_games', 0)
                if _a > 0 and _g > 0:
                    _rot_days.append({'art': _a, 'total_start': _g, 'date': rec.get(date_key, '')})
            # 蓄積データの残りを追加（3日間以降）
            if unit_days:
                _existing_dates = {d.get('date', '') for d in _rot_days}
                for ud in unit_days:
                    if ud.get('date', '') not in _existing_dates:
                        _rot_days.append(ud)
            if len(_rot_days) >= 5:
                _new_rot = analyze_rotation_pattern(_rot_days, machine_key=machine_key)
                # reasonsのローテ行を差し替え
                _hour = datetime.now().hour
                _ndl = '本日' if _hour < 10 else '翌日'
                _old_rot_prefix = '🔄 ローテ傾向:'
                rec['reasons'] = [r for r in rec['reasons'] if not r.startswith(_old_rot_prefix)]
                if _new_rot['has_pattern'] and _new_rot['next_high_chance']:
                    rec['reasons'].insert(1, f"🔄 ローテ傾向: {_new_rot['description']} → {_ndl}上げ期待")

        # フォールバック後の連続不調判定（trendはフォールバック前なのでrec値で再判定）
        _yp = rec.get('yesterday_prob', 0)
        _dbp = rec.get('day_before_prob', 0)
        _has_2day_bad = any('直近2日とも不調' in r for r in rec['reasons'])
        _bad_th = get_machine_threshold(machine_key, 'bad_prob')
        if _yp >= _bad_th and _dbp >= _bad_th and not _has_2day_bad:
            _hour = datetime.now().hour
            _ndl = '本日' if _hour < 10 else '翌日'
            _mk = machine_info.get('key', 'sbj') if machine_info else 'sbj'
            _mr = get_machine_recovery_stats(_mk)
            _rs = _mr.get(2, {})
            _r_note = f"（SBJ全店舗実績: {_rs['recovered']}/{_rs['total']}回={_rs['rate']:.0%}で翌日回復）" if _rs.get('total', 0) >= 3 else ""
            rec['reasons'].insert(1, f"🔄 直近2日とも不調（1/{_dbp:.0f}→1/{_yp:.0f}）→ {_ndl}設定変更期待大{_r_note}")

        recommendations.append(rec)

    # === 【改善3】ハイブリッド評価（絶対スコア + 相対位置補正）===
    # まず絶対スコアでランクを決定し、相対位置で±1段階だけ補正
    if len(recommendations) >= 3:
        sorted_by_score = sorted(recommendations, key=lambda r: -r['final_score'])
        n = len(sorted_by_score)

        for i, rec in enumerate(sorted_by_score):
            # 1. 絶対スコアでランク決定
            absolute_rank = get_rank(rec['final_score'])

            # 2. 相対位置で±1段階補正
            percentile = i / n  # 0.0 = トップ, 1.0 = 最下位
            has_ranking = rec.get('has_static_ranking', False)
            if percentile < 0.15 and absolute_rank != 'S':
                # 店舗内TOP15%: 1段階アップ
                # ランキングデータありの場合: スコア60以上でOK
                # ランキングデータなしの場合: スコア70以上（より厳しく）
                min_score = 60 if has_ranking else 70
                if rec['final_score'] >= min_score:
                    absolute_rank = rank_up(absolute_rank)
            elif percentile > 0.85 and absolute_rank not in ('C', 'D'):
                # 店舗内ワースト15%: 1段階ダウン
                absolute_rank = rank_down(absolute_rank)

            # 3. ランキングデータが無い台はS予測を制限（データ不足時の過信防止）
            if not has_ranking and absolute_rank == 'S':
                absolute_rank = 'A'

            rec['final_rank'] = absolute_rank

        # 4. S/A台数の上限制限（全台Sにならないようにする）
        # 島の50%以上がS/Aになるのは非現実的
        max_sa_ratio = 0.5  # S+Aは最大50%
        max_sa_count = max(3, int(n * max_sa_ratio))  # 最低3台は許可
        sa_count = 0
        for rec in sorted_by_score:
            if rec['final_rank'] in ('S', 'A'):
                sa_count += 1
                if sa_count > max_sa_count:
                    # 上限超過分はBに降格
                    rec['final_rank'] = 'B'

    # スコア順にソート（稼働中の台は少し下げる）
    def sort_key(r):
        score = r['final_score']
        if r['is_running']:
            score -= 20  # 稼働中は下げる
        return -score

    recommendations.sort(key=sort_key)

    # 「本日」を日付ラベルに置換（today_reasons, comparison_note等）
    if data_date_label:
        for rec in recommendations:
            if rec.get('today_reasons'):
                rec['today_reasons'] = [r.replace('本日', data_date_label) for r in rec['today_reasons']]
            if rec.get('comparison_note'):
                rec['comparison_note'] = rec['comparison_note'].replace('本日', data_date_label)

    # === 稼働率の注記（低稼働日は確率のブレが大きい） ===
    # 店舗×機種の平均G数で判定（台数が少ない場合は最低基準も適用）
    y_games_all = [r.get('yesterday_games', 0) for r in recommendations if r.get('yesterday_games', 0) > 0]
    avg_games = sum(y_games_all) / len(y_games_all) if y_games_all else 0
    # 台数が少ない（5台未満）場合、機種の一般的な稼働基準も考慮
    if len(y_games_all) < 5:
        # SBJの一般的な1日平均は6000-7000G前後
        machine_typical_avg = get_machine_threshold(machine_key, 'typical_daily_games')
        avg_games = max(avg_games, machine_typical_avg * 0.8)
    low_games_threshold = avg_games * 0.6 if avg_games > 0 else 3000
    for rec in recommendations:
        rec['store_avg_games'] = int(avg_games)
        for prefix in ['yesterday', 'day_before', 'three_days_ago']:
            g = rec.get(f'{prefix}_games', 0)
            if g > 0 and g < low_games_threshold:
                rec[f'{prefix}_low_activity'] = True

    # === 前日データの相対評価（店舗内比較） ===
    # 前日の成績が店舗平均より弱い場合は注意を追加
    y_arts = [r.get('yesterday_art', 0) for r in recommendations if r.get('yesterday_art', 0) > 0]
    y_rensas = [r.get('yesterday_max_rensa', 0) for r in recommendations if r.get('yesterday_max_rensa', 0) > 0]
    y_probs = [r.get('yesterday_prob', 0) for r in recommendations if r.get('yesterday_prob') and r.get('yesterday_prob', 0) > 0]
    if len(y_arts) >= 5:
        avg_y_art = sum(y_arts) / len(y_arts)
        avg_y_rensa = sum(y_rensas) / len(y_rensas) if y_rensas else 0
        median_y_prob = sorted(y_probs)[len(y_probs)//2] if y_probs else 0
        for rec in recommendations:
            ya = rec.get('yesterday_art', 0)
            ymr = rec.get('yesterday_max_rensa', 0)
            yp = rec.get('yesterday_prob', 0)
            # 弱い指標の数をカウント
            weak_count = 0
            if ya > 0 and ya < avg_y_art * 0.75:
                weak_count += 1
            if ymr > 0 and avg_y_rensa > 0 and ymr < avg_y_rensa * 0.5:
                weak_count += 1
            if yp > 0 and median_y_prob > 0 and yp > median_y_prob * 1.5:
                weak_count += 1

            if weak_count > 0:
                good_rate = rec.get('historical_perf', {}).get('good_day_rate', 0) if isinstance(rec.get('historical_perf'), dict) else 0
                yg = rec.get('yesterday_games', 0)
                is_low_activity = rec.get('yesterday_low_activity', False)

                # 原因推定: 事実ベースでシンプルに
                if is_low_activity and yp <= 150:
                    msg = f"🚨 前日は{yg:,}G消化で低稼働 → 高設定でも数字が伸びにくい稼働量"
                elif yp > 180:
                    msg = f"🚨 前日はART確率1/{yp:.0f}で低設定濃厚（全台中央値1/{median_y_prob:.0f}）"
                elif yp > 150:
                    msg = f"🚨 前日はART確率1/{yp:.0f}でやや不調（全台中央値1/{median_y_prob:.0f}）"
                else:
                    # 確率OK+爆発なしの翌日統計を追加
                    _ne_stats = get_no_explosion_stats(machine_key)
                    msg = f"🚨 前日はART確率1/{yp:.0f}と悪くないが、最大{ymr}連と爆発なし"
                    if _ne_stats['total'] >= 3:
                        msg += f" → 過去に同パターン→翌日好調: {_ne_stats['next_good']}/{_ne_stats['total']}回={_ne_stats['rate']:.0%}"

                if good_rate >= 0.7:
                    msg += f"（好調率{good_rate:.0%}のため本日も期待）"

                rec['reasons'].append(msg)

    # === 差枚概算（全rec、全日） ===
    # どのページから呼ばれても差枚が入ってる状態にする
    for rec in recommendations:
        for prefix in ['yesterday', 'day_before', 'three_days_ago']:
            _art = rec.get(f'{prefix}_art', 0)
            _games = rec.get(f'{prefix}_games', 0)
            if _art and _art > 0 and _games and _games > 0 and not rec.get(f'{prefix}_diff_medals'):
                _p = calculate_expected_profit(_games, _art, machine_key)
                rec[f'{prefix}_diff_medals'] = _p.get('current_estimate', 0)

    return recommendations


def format_recommendations(recommendations: list, store_name: str, machine_name: str = 'SBJ') -> str:
    """推奨結果をテキスト形式で出力

    Args:
        recommendations: 推奨台リスト
        store_name: 店舗名
        machine_name: 機種名（デフォルト: SBJ）
    """
    lines = []
    lines.append(f"=== {store_name} {machine_name} 推奨台 ===")
    lines.append(f"更新: {datetime.now().strftime('%H:%M')}")
    lines.append("")

    # S/Aランクを推奨台として表示
    top_recs = [r for r in recommendations if r['final_rank'] in ('S', 'A') and not r['is_running']]

    if top_recs:
        lines.append("[推奨]")
        for rec in top_recs[:5]:
            status = rec['status']
            art_info = f" 本日AT{rec['art_count']}回" if rec['art_count'] > 0 else ""
            lines.append(f"  {rec['unit_id']} [{rec['final_rank']}] {status}{art_info}")
            for reason in rec.get('reasons', [])[:3]:
                lines.append(f"    - {reason}")
    else:
        lines.append("[推奨台なし - 全台稼働中の可能性]")

    lines.append("")
    lines.append("[全台状況]")
    for rec in recommendations:
        mark = "*" if rec['is_running'] else " "
        status = rec['status']
        lines.append(f" {mark}{rec['unit_id']} [{rec['final_rank']}] {status}")

    return "\n".join(lines)


if __name__ == "__main__":
    # テスト実行
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--store', '-s', default='island_akihabara',
                        help='店舗キー (island_akihabara, shibuya_espass, shibuya_espass_hokuto, etc.)')
    args = parser.parse_args()

    store = STORES.get(args.store)
    if not store:
        print(f"Unknown store: {args.store}")
        # 利用可能な店舗キーを機種別に表示
        print("\nAvailable stores:")
        for key, s in STORES.items():
            if key in ('island_akihabara', 'shibuya_espass'):  # 旧形式は除外
                continue
            machine = s.get('machine', 'sbj')
            machine_info = MACHINES.get(machine, {})
            print(f"  {key}: {s['name']} ({machine_info.get('short_name', machine)})")
        sys.exit(1)

    # 機種情報を取得
    machine_key = get_machine_from_store_key(args.store)
    machine_info = MACHINES.get(machine_key, {'short_name': 'SBJ'})

    recommendations = recommend_units(args.store)
    output = format_recommendations(recommendations, store['name'], machine_info.get('short_name', 'SBJ'))
    print(output)

    print("\n" + "=" * 50)
    print("詳細分析:")
    for rec in recommendations[:5]:
        print(f"\n【{rec['unit_id']}】{rec['final_rank']} (スコア: {rec['final_score']:.1f})")
        print(f"  昨日推定差枚: {rec['yesterday_diff']:+,}枚")
        print(f"  7日平均AT: {rec['avg_art_7days']:.1f}回")
        if rec['consecutive_plus']:
            print(f"  連続プラス: {rec['consecutive_plus']}日")
        if rec['consecutive_minus']:
            print(f"  連続マイナス: {rec['consecutive_minus']}日")
        print("  理由:")
        for reason in rec.get('reasons', []):
            print(f"    - {reason}")
