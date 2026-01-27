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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.rankings import STORES, RANKINGS, get_rank, get_unit_ranking, MACHINES
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
    'akihabara_espass_hokuto': 'akiba_espass_hokuto_tensei2',
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
    'akihabara_espass_sbj': {
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

    for pattern in patterns:
        file_path = data_dir / pattern
        if file_path.exists():
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 機種キーが指定されている場合、該当機種のデータがあるか確認
                if machine_key:
                    machines = data.get('machines', [])
                    if machine_key in machines or not machines:
                        return data
                else:
                    return data

    # パターンマッチでファイルを探す
    import glob
    for pattern in [f'daily_*_{date_str}.json', f'*_daily_{date_str}.json']:
        matches = list(data_dir.glob(pattern))
        if matches:
            # 最新のファイルを使用
            latest = max(matches, key=lambda p: p.stat().st_mtime)
            with open(latest, 'r', encoding='utf-8') as f:
                return json.load(f)

    # 今日のデータがない場合、直近7日間のデータを探す（フォールバック）
    from datetime import timedelta
    base_date = datetime.strptime(date_str, '%Y%m%d')
    for days_back in range(1, 8):
        fallback_date = (base_date - timedelta(days=days_back)).strftime('%Y%m%d')
        for pattern in patterns:
            fallback_pattern = pattern.replace(date_str, fallback_date)
            file_path = data_dir / fallback_pattern
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if machine_key:
                        machines = data.get('machines', [])
                        if machine_key in machines or not machines:
                            # rawデータからIsland等のデータを補完
                            data = _merge_raw_data(data, fallback_date)
                            return data
                    else:
                        data = _merge_raw_data(data, fallback_date)
                        return data
        # ワイルドカードでも探す
        for wp in [f'daily_*_{fallback_date}.json', f'*_daily_{fallback_date}.json']:
            matches = list(data_dir.glob(wp))
            if matches:
                latest = max(matches, key=lambda p: p.stat().st_mtime)
                with open(latest, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    data = _merge_raw_data(data, fallback_date)
                    return data

    return {}


def _merge_raw_data(daily_data: dict, date_str: str) -> dict:
    """rawディレクトリのpapimo等のデータをdaily_dataに補完する

    papimo rawデータ（リスト形式）を日別データの形式に変換してマージ
    """
    raw_dir = Path(__file__).parent.parent / 'data' / 'raw'
    if not raw_dir.exists():
        return daily_data

    stores = daily_data.get('stores', {})

    # papimo rawデータを検索
    papimo_files = sorted(raw_dir.glob(f'papimo_island_sbj_{date_str}_*.json'), reverse=True)
    if papimo_files:
        try:
            with open(papimo_files[0], 'r', encoding='utf-8') as f:
                raw_units = json.load(f)

            if isinstance(raw_units, list) and raw_units:
                # island_akihabara_sbjのデータが既にあれば日数を確認
                existing = stores.get('island_akihabara_sbj', {})
                existing_units = existing.get('units', [])

                # 既存データの日数が少ない場合にrawデータで上書き
                existing_days = 0
                if existing_units:
                    existing_days = len(existing_units[0].get('days', []))

                raw_days = len(raw_units[0].get('days', []))

                if raw_days > existing_days:
                    # rawデータを日別形式に変換
                    converted_units = []
                    for raw_unit in raw_units:
                        converted_units.append({
                            'unit_id': str(raw_unit.get('unit_id', '')),
                            'days': raw_unit.get('days', []),
                        })

                    stores['island_akihabara_sbj'] = {
                        'units': converted_units,
                        'data_source': 'papimo_raw',
                    }
                    daily_data['stores'] = stores
        except Exception as e:
            pass  # rawデータ読み込み失敗は無視

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
    good_prob_threshold = 130 if machine_key == 'sbj' else 330  # ART確率がこれ以下なら好調
    bad_prob_threshold = 150 if machine_key == 'sbj' else 366   # ART確率がこれ以上なら不調

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
    }


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


def analyze_trend(days: List[dict]) -> dict:
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

        # 差枚推定（簡易計算：ART1回あたり+50枚、1G消費3枚）
        if games > 0:
            estimated_diff = (art * 50) - (games * 3 / 50)  # 超簡易推定
            # より正確な推定：ART確率で判定
            if art > 0:
                art_prob = games / art
                if art_prob <= 80:
                    estimated_diff = games * 0.3  # 高設定域
                elif art_prob <= 120:
                    estimated_diff = games * 0.1
                elif art_prob <= 180:
                    estimated_diff = -games * 0.05
                else:
                    estimated_diff = -games * 0.15
            else:
                estimated_diff = -games * 0.2
            daily_results.append((day.get('date'), estimated_diff, art, games))

    if art_counts:
        result['avg_art_7days'] = sum(art_counts) / len(art_counts)
    if game_counts:
        result['avg_games_7days'] = sum(game_counts) / len(game_counts)

    # 連続プラス/マイナス判定
    consecutive_plus = 0
    consecutive_minus = 0
    for date, diff, art, games in daily_results:
        if diff > 0:
            consecutive_plus += 1
            consecutive_minus = 0
        elif diff < 0:
            consecutive_minus += 1
            consecutive_plus = 0
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
        result['yesterday_max_medals'] = yesterday_day.get('max_medals', 0)
        # 昨日の最大連チャン数
        yesterday_history = yesterday_day.get('history', [])
        if yesterday_history:
            result['yesterday_max_rensa'] = calculate_max_rensa(yesterday_history)

    # 前々日の結果
    if len(daily_results) >= 2:
        db_date, db_diff, db_art, db_games = daily_results[1]
        result['day_before_art'] = db_art
        result['day_before_games'] = int(db_games)
        result['day_before_date'] = db_date
    if len(sorted_days) >= 2:
        result['day_before_rb'] = sorted_days[1].get('rb', 0)

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
            result['today_score_bonus'] = 0
        elif result['art_prob'] >= thresholds['very_low_at_prob']:
            result['today_score_bonus'] = int(-10 * games_multiplier)
            result['today_reasons'].append(f'本日ART確率 1/{result["art_prob"]:.0f} (低設定域)')

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


def analyze_rotation_pattern(days: List[dict]) -> dict:
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
    results = []
    for day in days[:7]:
        art = day.get('art', 0)
        games = day.get('total_start', 0)
        if games > 0 and art > 0:
            prob = games / art
            if prob <= 130:  # 高設定域
                results.append('+')
            elif prob >= 200:  # 低設定域
                results.append('-')
            else:
                results.append('=')

    if len(results) < 5:
        return {'has_pattern': False, 'cycle_days': 0, 'next_high_chance': False, 'description': ''}

    # 連続マイナス後のプラスパターンを検出
    pattern_str = ''.join(results)

    # 2日下げて上げるパターン
    if '--+' in pattern_str[:4]:
        return {
            'has_pattern': True,
            'cycle_days': 3,
            'next_high_chance': results[0] == '-' and results[1] == '-',
            'description': '2日下げ→上げのローテ傾向あり'
        }

    # 3日下げて上げるパターン
    if '---+' in pattern_str[:5]:
        return {
            'has_pattern': True,
            'cycle_days': 4,
            'next_high_chance': results[0] == '-' and results[1] == '-' and results[2] == '-',
            'description': '3日下げ→上げのローテ傾向あり'
        }

    # 交互パターン（+-+-）
    alternating = sum(1 for i in range(len(results)-1) if results[i] != results[i+1])
    if alternating >= 4:
        return {
            'has_pattern': True,
            'cycle_days': 2,
            'next_high_chance': results[0] == '-',
            'description': '日替わりローテ傾向（交互に変動）'
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
        reasons.append(f"📊 {total_perf_days}日間中{good_days}日好調（好調率{good_day_rate:.0%}）→ 高設定が入りやすい台")

        # 補足: 平均ART確率 + 好調翌日継続率
        sub_parts = []
        hp_avg_prob = historical_perf.get('avg_prob', 0)
        if hp_avg_prob > 0:
            sub_parts.append(f"平均ART確率 1/{hp_avg_prob:.0f}")
        continuation_rate = historical_perf.get('continuation_rate', 0)
        continuation_total = historical_perf.get('continuation_total', 0)
        continuation_good = historical_perf.get('continuation_good', 0)
        if continuation_total >= 2:
            sub_parts.append(f"好調翌日も好調: {continuation_good}/{continuation_total}回({continuation_rate:.0%})")
        elif continuation_total == 1:
            sub_parts.append(f"好調翌日も好調: {continuation_good}/{continuation_total}回")
        # 直近3日の確率推移
        recent_probs = historical_perf.get('recent_probs', [])
        if len(recent_probs) >= 2:
            prob_strs = [f"1/{int(p)}" for p in recent_probs]
            sub_parts.append(f"直近: {' → '.join(reversed(prob_strs))}")
        if sub_parts:
            reasons.append(f"📈 {' / '.join(sub_parts)}")

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
                        cycle_parts.append(f"{key}日不調→翌日好調: {rate['good']}/{rate['total']}回({rate['rate']:.0%})")
            # 連続好調中なら据え置き率
            if consecutive_plus > 0:
                gtg = cycle_analysis.get('good_to_good', {})
                key = min(consecutive_plus, max(gtg.keys())) if gtg else 0
                if key and key in gtg:
                    rate = gtg[key]
                    if rate['total'] >= 2:
                        cycle_parts.append(f"{key}日連続好調→翌日も: {rate['good']}/{rate['total']}回({rate['rate']:.0%})")
            # 交互パターン
            alt_score = cycle_analysis.get('alternating_score', 0)
            if alt_score >= 0.6 and cycle_analysis.get('total_days', 0) >= 7:
                cycle_parts.append(f"交互パターン傾向あり({alt_score:.0%})")
            # 平均周期
            avg_cycle = cycle_analysis.get('avg_cycle', 0)
            if avg_cycle > 0 and cycle_analysis.get('total_days', 0) >= 7:
                cycle_parts.append(f"好調周期: 平均{avg_cycle:.1f}日間隔")
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
                    reasons.append(f"📅 {today_weekday}曜の好調率: {wd_good}/{wd_total}回({wd_rate:.0%}) → 期待大")
                elif wd_rate <= 0.3:
                    reasons.append(f"⚠ {today_weekday}曜の好調率: {wd_good}/{wd_total}回({wd_rate:.0%}) → 要注意")

        # なぜ今日も好調と見るかの根拠を追加
        today_confidence_parts = []
        if today_rating >= 4:
            rating_label = '高設定投入日' if today_rating >= 5 else '狙い目の曜日'
            today_confidence_parts.append(f"{store_name}の{today_weekday}曜は{rating_label}（店舗傾向）")
        if consecutive_plus >= 2:
            today_confidence_parts.append(f"現在{consecutive_plus}日連続好調中")
        elif consecutive_minus >= 2:
            today_confidence_parts.append(f"{consecutive_minus}日不調→反転期待")
        if miss_days <= 1 and total_perf_days >= 5:
            today_confidence_parts.append(f"外れたのは{total_perf_days}日中{miss_days}日のみ")
        if today_confidence_parts:
            reasons.append(f"💡 今日も期待できる根拠: {' / '.join(today_confidence_parts)}")
        elif today_rating >= 3:
            reasons.append(f"💡 {store_name}の{today_weekday}曜は過去実績から普通〜やや期待できる日")
    elif total_perf_days > 0 and good_day_rate <= 0.4:
        reasons.append(f"📊 {total_perf_days}日間中{good_days}日好調（好調率{good_day_rate:.0%}）→ 低設定が入りやすい台")
    elif base_rank == 'S':
        reasons.append(f"📊 過去データSランク: 高設定が頻繁に入る台")
    elif base_rank == 'A':
        reasons.append(f"📊 過去データAランク: 高設定が入りやすい台")
    elif base_rank == 'B':
        reasons.append(f"📊 過去データBランク: 中間設定以上が多い台")

    # === 2. 連続パターン・傾向（設定変更サイクルの読み） ===
    # これが翌日予測の核心 — 前日単体の成績ではなく「流れ」
    if consecutive_minus >= 4:
        reasons.append(f"🔄 {consecutive_minus}日連続不調 → 設定変更の可能性大")
    elif consecutive_minus >= 3:
        reasons.append(f"🔄 {consecutive_minus}日連続不調 → そろそろ設定上げ期待")
    elif consecutive_minus == 2:
        if today_rating >= 4:
            reasons.append(f"🔄 2日連続不調 + {store_name}の{today_weekday}曜は狙い目 → リセット期待")
        else:
            reasons.append(f"🔄 2日連続不調 → リセット期待")

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
        reasons.append(f"🔄 直近2日とも不調（1/{day_before_prob_val:.0f}→1/{yesterday_prob_val:.0f}）→ 設定変更期待大")

    # ローテーションパターン
    if days:
        rotation = analyze_rotation_pattern(days)
        if rotation['has_pattern'] and rotation['next_high_chance']:
            reasons.append(f"🔄 ローテ傾向: {rotation['description']} → 次回上げ期待")

    # === 2.5 稼働パターン分析 ===
    activity_data = kwargs.get('activity_data', {})
    if activity_data:
        activity_desc = activity_data.get('description', '')
        if activity_data.get('is_hyena_target'):
            reasons.append(f"⚠ {activity_desc}")
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
            reasons.append(f"⚠ 本日ART確率1/{art_prob:.0f} ({total_games:,}G消化) → 低設定域の挙動")

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
        reasons.append(f"⚠ 出玉バランス悪い: ART多いが最大枚数少ない（低設定の可能性）")
    elif medal_balance_penalty <= -5:
        reasons.append(f"⚠ ART回数の割に出玉が伸びていない")

    # === 6. 店舗曜日傾向（補足情報） ===
    # 好調率の根拠で既に曜日情報を出してたら重複させない
    has_weekday_in_confidence = any('今日も期待できる根拠' in r and today_weekday in r for r in reasons)
    if store_name and today_weekday and not has_weekday_in_confidence:
        best_info = weekday_info.get('best_days', '')
        if today_rating >= 5:
            reasons.append(f"📅 {store_name}の{today_weekday}曜は高設定投入日（店舗傾向: {best_info}）")
        elif today_rating >= 4:
            reasons.append(f"📅 {store_name}は{today_weekday}曜が狙い目（店舗傾向: {best_info}）")
        elif today_rating <= 2:
            worst_info = weekday_info.get('worst_days', '')
            reasons.append(f"⚠ {store_name}の{today_weekday}曜は弱い日（店舗傾向: {worst_info}）→ 回収傾向")

    # === フォールバック ===
    if not reasons:
        if base_rank in ('S', 'A', 'B'):
            reasons.append(f"過去データ{base_rank}ランク")
        if store_name and today_weekday:
            best_info = weekday_info.get('best_days', '')
            rating_label = {5: '高設定投入日', 4: '狙い目', 3: '普通', 2: '弱い日', 1: '回収日'}.get(today_rating, '普通')
            reasons.append(f"{store_name}の{today_weekday}曜は{rating_label}（店舗傾向{'：' + best_info if best_info else ''}）")

    # 重複除去、上位5つ
    seen = set()
    unique = []
    for r in reasons:
        if r not in seen:
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

    return unique[:5]


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

    # 機種キーを取得
    machine_key = get_machine_from_store_key(store_key)

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
                        trend_data = analyze_trend(days)
                        break

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
                        'is_good': (d.get('total_start', 0) / d.get('art', 1) if d.get('art', 0) > 0 else 999) <= (130 if machine_key == 'sbj' else 330),
                    })
            accumulated['days'].sort(key=lambda x: x.get('date', ''))
            analysis_phase = get_analysis_phase(accumulated)

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
        bad_prob_threshold = 150 if machine_key == 'sbj' else 366

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

        # === 最終スコア計算 ===
        raw_score = (base_score
                     + today_analysis.get('today_score_bonus', 0)
                     + trend_bonus
                     + historical_bonus   # 【改善1】過去実績ボーナス
                     + slump_bonus        # 【改善2】不調翌日ボーナス
                     + activity_bonus     # 【改善4+5】稼働パターン+ハイエナ
                     + medal_balance_penalty  # 出玉バランスペナルティ
                     )
        final_score = raw_score
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
                # 直近の履歴データを探す（日付降順）
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
            'day_before_art': trend_data.get('day_before_art', 0),
            'day_before_rb': trend_data.get('day_before_rb', 0),
            'day_before_games': trend_data.get('day_before_games', 0),
            'day_before_date': trend_data.get('day_before_date', ''),
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
                elif d == db_date:
                    rec['day_before_max_rensa'] = ad.get('max_rensa', 0)
                    rec['day_before_max_medals'] = ad.get('max_medals', 0)
                elif d and d < (db_date or y_date or '9999') and not rec.get('three_days_ago_date'):
                    rec['three_days_ago_art'] = ad.get('art', 0)
                    rec['three_days_ago_rb'] = ad.get('rb', 0)
                    rec['three_days_ago_games'] = ad.get('games', 0)
                    rec['three_days_ago_date'] = d
                    rec['three_days_ago_max_rensa'] = ad.get('max_rensa', 0)
                    rec['three_days_ago_max_medals'] = ad.get('max_medals', 0)

        # 閉店後: availabilityのデータを前日データとして補完
        if not realtime_is_today and realtime_data:
            units_list = realtime_data.get('units', [])
            for _unit in units_list:
                if _unit.get('unit_id') == unit_id:
                    # 連チャン・最大枚数
                    rt_hist = _unit.get('today_history', [])
                    if rt_hist and not rec.get('yesterday_max_rensa'):
                        from analysis.history_accumulator import _calc_history_stats
                        calc_rensa, calc_medals = _calc_history_stats(rt_hist)
                        if calc_rensa > 0:
                            rec['yesterday_max_rensa'] = calc_rensa
                        rt_max = _unit.get('max_medals', 0)
                        if rt_max > 0:
                            rec['yesterday_max_medals'] = rt_max
                        elif calc_medals > 0:
                            rec['yesterday_max_medals'] = calc_medals
                        rec['today_history'] = rt_hist
                    # ART確率
                    if not rec.get('yesterday_prob'):
                        _rt_art = _unit.get('art', 0)
                        _rt_total = _unit.get('total_start', 0)
                        if _rt_art > 0 and _rt_total > 0:
                            rec['yesterday_prob'] = round(_rt_total / _rt_art)
                    # 差枚（履歴から概算: メダル獲得 - 投入3枚掛け）
                    if not rec.get('yesterday_diff_medals') and rt_hist:
                        _diff = sum(h.get('medals', 0) - h.get('start', 0) * 3 for h in rt_hist)
                        if _diff != 0:
                            rec['yesterday_diff_medals'] = _diff
                    # RB
                    if not rec.get('yesterday_rb'):
                        _rt_rb = _unit.get('rb', 0)
                        if _rt_rb > 0:
                            rec['yesterday_rb'] = _rt_rb
                    break

        recommendations.append(rec)

    # === 【改善3】相対評価によるランク付け ===
    # 全台がS/Aにならないよう、スコアの分布に基づいてランクを再割り当て
    # 上位20%=S, 次20%=A, 次30%=B, 次20%=C, 残り=D
    if len(recommendations) >= 3:
        # スコア降順でソート
        sorted_by_score = sorted(recommendations, key=lambda r: -r['final_score'])
        n = len(sorted_by_score)

        # パーセンタイルでランク割り当て
        for i, rec in enumerate(sorted_by_score):
            percentile = i / n  # 0.0 = トップ, 1.0 = 最下位
            if percentile < 0.20:
                rec['final_rank'] = 'S'
            elif percentile < 0.40:
                rec['final_rank'] = 'A'
            elif percentile < 0.70:
                rec['final_rank'] = 'B'
            elif percentile < 0.90:
                rec['final_rank'] = 'C'
            else:
                rec['final_rank'] = 'D'

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
