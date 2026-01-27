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
                            return data
                    else:
                        return data
        # ワイルドカードでも探す
        for wp in [f'daily_*_{fallback_date}.json', f'*_daily_{fallback_date}.json']:
            matches = list(data_dir.glob(wp))
            if matches:
                latest = max(matches, key=lambda p: p.stat().st_mtime)
                with open(latest, 'r', encoding='utf-8') as f:
                    return json.load(f)

    return {}


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

    # --- 実用指標の計算 ---
    # AT間平均G数（全日のゲーム数 / ART回数）
    total_art_sum = sum(art_counts)
    total_games_sum = sum(game_counts)
    if total_art_sum > 0 and total_games_sum > 0:
        result['avg_games_per_art'] = total_games_sum / total_art_sum
    else:
        result['avg_games_per_art'] = 0

    # 軽い当たり率の推定（ART確率からの推定）
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
    thresholds = get_machine_thresholds(machine_key)
    if result['art_prob'] > 0:
        if result['art_prob'] <= thresholds['setting6_at_prob']:
            result['today_score_bonus'] = 20
            result['today_reasons'].append(f'本日AT確率 1/{result["art_prob"]:.0f} (設定6域)')
        elif result['art_prob'] <= thresholds['high_at_prob']:
            result['today_score_bonus'] = 15
            result['today_reasons'].append(f'本日AT確率 1/{result["art_prob"]:.0f} (高設定域)')
        elif result['art_prob'] <= thresholds['mid_at_prob']:
            result['today_score_bonus'] = 10
            result['today_reasons'].append(f'本日AT確率 1/{result["art_prob"]:.0f} (中間設定域)')
        elif result['art_prob'] <= thresholds['low_at_prob']:
            result['today_score_bonus'] = 0
        elif result['art_prob'] >= thresholds['very_low_at_prob']:
            result['today_score_bonus'] = -10
            result['today_reasons'].append(f'本日AT確率 1/{result["art_prob"]:.0f} (低設定域)')

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
                max_rensa = max((h.get('rensa', 1) for h in history), default=1)
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

    # 各当たり間のG数と連チャン数を計算
    valleys = []
    rensas = []
    for h in history:
        start = h.get('start', 0) or h.get('games_between', 0)
        if start > 0:
            valleys.append(start)
        rensa = h.get('rensa', 1)
        if rensa > 0:
            rensas.append(rensa)

    if not valleys:
        return default_result

    max_valley = max(valleys)
    avg_valley = sum(valleys) / len(valleys)
    recent_valleys = valleys[-5:] if len(valleys) >= 5 else valleys

    # 連チャン分析
    max_rensa = max(rensas) if rensas else 0
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
                     store_key: str = None) -> List[str]:
    """推奨理由を生成（店舗傾向・具体的根拠付き）"""
    reasons = []

    # --- 店舗の曜日傾向情報を取得 ---
    weekday_info = get_store_weekday_info(store_key) if store_key else {}
    store_name = weekday_info.get('short_name', '')
    today_weekday = weekday_info.get('today_weekday', '')
    today_rating = weekday_info.get('today_rating', 3)

    # 1. 曜日傾向 × 連続マイナス/プラスの組み合わせ（最重要）
    consecutive_plus = trend.get('consecutive_plus', 0)
    consecutive_minus = trend.get('consecutive_minus', 0)
    yesterday = trend.get('yesterday_result', 'unknown')
    yesterday_diff = trend.get('yesterday_diff', 0)

    if today_rating >= 5:
        reasons.append(f"{store_name}は{today_weekday}曜が最強日（★5） → 高設定投入の期待大")
    elif today_rating >= 4:
        reasons.append(f"{store_name}は{today_weekday}曜が狙い目（★4）")

    if consecutive_minus >= 4:
        reasons.append(f"{consecutive_minus}日連続マイナス → この店の投入サイクル的に上げ濃厚")
    elif consecutive_minus >= 3:
        if today_rating >= 4:
            reasons.append(f"{consecutive_minus}日マイナス + {today_weekday}曜★{today_rating} → 設定上げの好条件が揃っている")
        else:
            reasons.append(f"{consecutive_minus}日マイナス継続 → そろそろ上げる可能性")
    elif consecutive_minus == 2:
        if today_rating >= 4:
            reasons.append(f"2日マイナス + {today_weekday}曜★{today_rating} → リセット期待")

    if consecutive_plus >= 4:
        reasons.append(f"{consecutive_plus}日連続プラス → 高設定据え置き中（ただし下げ警戒も）")
    elif consecutive_plus >= 3:
        reasons.append(f"{consecutive_plus}日連続プラス → 据え置き期待だが、そろそろ下げる店もある")

    # 2. 昨日の結果 + 背景根拠
    if yesterday == 'big_minus' and yesterday_diff < -2000:
        if today_rating >= 4:
            reasons.append(f"昨日推定-{abs(yesterday_diff):,}枚 + {today_weekday}曜★{today_rating} → リセット狙い目（天井666Gに短縮）")
        elif consecutive_minus >= 2:
            reasons.append(f"昨日推定-{abs(yesterday_diff):,}枚（{consecutive_minus}日連続凹み）→ リセット期待（天井666G短縮）")
        else:
            reasons.append(f"昨日推定-{abs(yesterday_diff):,}枚 → リセットなら天井666Gに短縮")
    elif yesterday == 'big_minus':
        if consecutive_minus >= 2:
            reasons.append(f"昨日マイナス（{consecutive_minus}日連続）→ 店の傾向的に設定変更の可能性")
        elif today_rating >= 4:
            reasons.append(f"昨日マイナス + {today_weekday}曜★{today_rating} → 設定変更期待")
    elif yesterday == 'big_plus' and yesterday_diff > 3000:
        reasons.append(f"昨日推定+{yesterday_diff:,}枚 → 据え置きなら引き続き狙い目")

    # 3. ローテーションパターン分析
    if days:
        rotation = analyze_rotation_pattern(days)
        if rotation['has_pattern']:
            if rotation['next_high_chance']:
                reasons.append(f"ローテ傾向: {rotation['description']} → 本日上げ期待")

    # 4. 当日のART確率（打つべきか判断する核心データ）
    art_prob = today.get('art_prob', 0)
    total_games = today.get('total_games', 0)

    if total_games > 0:
        if art_prob > 0 and art_prob <= 80:
            reasons.append(f"本日ART確率1/{art_prob:.0f}（{total_games:,}G消化）→ 設定6域の挙動")
        elif art_prob > 0 and art_prob <= 100:
            reasons.append(f"本日ART確率1/{art_prob:.0f}（{total_games:,}G消化）→ 高設定濃厚")
        elif art_prob > 0 and art_prob <= 130:
            if total_games >= 5000:
                reasons.append(f"本日1/{art_prob:.0f}で{total_games:,}G消化 → 中間以上の設定で安定稼働中")

    # 5. AT間平均G数（過去7日）
    avg_games_per_art = trend.get('avg_games_per_art', 0)
    avg_art_prob = trend.get('avg_art_prob', 0)
    if avg_art_prob > 0:
        if avg_art_prob <= 100:
            reasons.append(f"7日間のART確率 平均1/{avg_art_prob:.0f} → 高設定域が続いている台")
        elif avg_art_prob <= 130:
            reasons.append(f"7日間のART確率 平均1/{avg_art_prob:.0f} → 中間設定以上の台")

    # 6. グラフパターン分析
    if days:
        graph = analyze_graph_pattern(days)
        if graph.get('likely_to_rise') and not graph.get('has_big_rensa'):
            if graph['pattern'] in ('mimizu', 'momimomi'):
                reasons.append("過去7日モミモミで大連荘なし → 爆発のタイミングが近い可能性")

    # 7. 本日のグラフ分析
    if today_history:
        today_graph = analyze_today_graph(today_history)
        if today_graph['description']:
            reasons.append(today_graph['description'])

    # 8. 他台との比較
    if comparison.get('is_top_performer'):
        reasons.append("本日この店舗でトップの出玉")
    elif comparison.get('rank_in_store', 99) <= 2 and comparison.get('total_units', 0) > 3:
        reasons.append(f"本日{comparison['rank_in_store']}位/{comparison['total_units']}台中")

    # 9. 曜日が弱い日の警告
    if today_rating <= 2 and store_name:
        reasons.append(f"注意: {store_name}は{today_weekday}曜★{today_rating}（弱い日）→ 回収傾向")

    # 10. 未稼働台の分析
    if total_games == 0:
        if consecutive_minus >= 2 and today_rating >= 4:
            reasons.append(f"未稼働 + {consecutive_minus}日凹み + {today_weekday}曜★{today_rating} → リセット台狙い目")
        elif consecutive_minus >= 2:
            reasons.append(f"未稼働 + {consecutive_minus}日連続マイナス → リセット台の可能性")

    # 本日と7日の確率が同じ場合、7日の方を削除（1日分データしかない場合）
    if art_prob > 0 and avg_art_prob > 0 and abs(art_prob - avg_art_prob) < 5:
        reasons = [r for r in reasons if '7日間のART確率' not in r]

    # reasonsが空の台にデフォルト理由を追加
    if not reasons:
        if total_games > 0 and art_prob > 0:
            if art_prob <= 180:
                reasons.append(f"ART確率1/{art_prob:.0f}（{total_games:,}G消化）")
            else:
                reasons.append(f"ART確率1/{art_prob:.0f} → 低設定域（{total_games:,}G消化）")
        elif today_rating >= 4:
            reasons.append(f"{store_name}は{today_weekday}曜★{today_rating}")
        elif today_rating <= 2:
            reasons.append(f"{store_name}は{today_weekday}曜★{today_rating}（弱い日）")

    # 重複を除去して上位5つに絞る
    seen = set()
    unique_reasons = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            unique_reasons.append(r)

    return unique_reasons[:5]


def recommend_units(store_key: str, realtime_data: dict = None, availability: dict = None) -> list:
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

        # リアルタイムデータがあれば使用
        if realtime_data:
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

        # トレンドによるスコア調整
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

        # 最終スコア計算
        final_score = base_score + today_analysis.get('today_score_bonus', 0) + trend_bonus
        final_rank = get_rank(final_score)

        # 推奨理由を生成（過去日データと当日履歴を渡す）
        unit_days = []
        today_history = []
        if unit_history:
            unit_days = unit_history.get('days', [])
            # 当日の履歴を取得
            today_str = datetime.now().strftime('%Y-%m-%d')
            for day in unit_days:
                if day.get('date') == today_str:
                    today_history = day.get('history', [])
                    break

        reasons = generate_reasons(
            unit_id, trend_data, today_analysis, comparison, base_rank, final_rank,
            days=unit_days, today_history=today_history, store_key=store_key
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

        # データ日付を取得（今日 or 昨日）
        data_date = today_analysis.get('data_date', '')
        is_today_data = data_date == datetime.now().strftime('%Y-%m-%d') if data_date else False

        # max_medals, final_start（現在ハマり）をリアルタイムデータから取得
        max_medals = 0
        final_start = 0  # 現在ハマり（最終ART後のG数）
        if realtime_data:
            units_list = realtime_data.get('units', [])
            for unit in units_list:
                if unit.get('unit_id') == unit_id:
                    max_medals = unit.get('max_medals', 0)
                    final_start = unit.get('final_start', 0)
                    break

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
            'max_medals': max_medals,
            'consecutive_plus': trend_data.get('consecutive_plus', 0),
            'consecutive_minus': trend_data.get('consecutive_minus', 0),
            'avg_art_7days': trend_data.get('avg_art_7days', 0),
            # 差枚見込み
            'current_estimate': profit_info['current_estimate'],
            'closing_estimate': final_start if final_start > 0 else profit_info['closing_estimate'],
            'profit_category': profit_info['profit_category'],
            'estimated_setting': profit_info['setting_info']['estimated_setting'],
            'setting_num': profit_info['setting_info'].get('setting_num', 0),
            'payout_estimate': profit_info['setting_info']['payout_estimate'],
        }

        recommendations.append(rec)

    # スコア順にソート（稼働中の台は少し下げる）
    def sort_key(r):
        score = r['final_score']
        if r['is_running']:
            score -= 20  # 稼働中は下げる
        return -score

    recommendations.sort(key=sort_key)

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
