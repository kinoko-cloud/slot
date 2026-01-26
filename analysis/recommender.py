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

# 機種別のART確率閾値
# SBJ: 設定1=1/241.7, 設定6=1/181.3
# 北斗転生2: 設定1=1/366.0, 設定6=1/273.1
MACHINE_THRESHOLDS = {
    'sbj': {
        'setting6_at_prob': 80,   # 設定6域（非常に良い）
        'high_at_prob': 100,      # 高設定域
        'mid_at_prob': 130,       # 中間設定域
        'low_at_prob': 180,       # 低設定域の境界
        'very_low_at_prob': 250,  # 非常に悪い
    },
    'hokuto_tensei2': {
        'setting6_at_prob': 273,  # 設定6域（非常に良い）
        'high_at_prob': 300,      # 高設定域
        'mid_at_prob': 340,       # 中間設定域
        'low_at_prob': 366,       # 低設定域の境界
        'very_low_at_prob': 450,  # 非常に悪い
    },
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
        'status': '未稼働',
        'hourly_rate': 0,  # 1時間あたりのART数
        'expected_games': 0,  # この時間帯での期待G数
        'today_reasons': [],
    }

    if not unit_data:
        return result

    # リアルタイムデータ形式（daysキーなし）の場合
    if 'days' not in unit_data:
        today_data = unit_data
    else:
        # 日別データ形式の場合
        days = unit_data.get('days', [])
        if not days:
            return result

        today = datetime.now().strftime('%Y-%m-%d')
        today_data = None
        for day in days:
            if day.get('date') == today:
                today_data = day
                break

        if not today_data:
            # 当日データなし = 未稼働の可能性
            result['status'] = '未稼働'
            result['today_score_bonus'] = 5  # 未稼働台は狙い目の可能性
            result['today_reasons'].append('本日未稼働（誰も座っていない可能性）')
            return result

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


def generate_reasons(unit_id: str, trend: dict, today: dict, comparison: dict,
                     base_rank: str, final_rank: str) -> List[str]:
    """推奨理由を生成"""
    reasons = []

    # 1. 過去トレンドからの理由
    if trend.get('consecutive_plus', 0) >= 3:
        reasons.append(f"過去{trend['consecutive_plus']}日連続でプラス推定 → 高設定据え置き濃厚")
    elif trend.get('consecutive_plus', 0) >= 2:
        reasons.append(f"直近{trend['consecutive_plus']}日連続プラス")

    if trend.get('consecutive_minus', 0) >= 3:
        reasons.append(f"過去{trend['consecutive_minus']}日連続マイナス → そろそろ上げる可能性")
    elif trend.get('consecutive_minus', 0) >= 2:
        reasons.append(f"直近{trend['consecutive_minus']}日連続マイナス")

    # 2. 昨日の結果
    yesterday = trend.get('yesterday_result', 'unknown')
    yesterday_diff = trend.get('yesterday_diff', 0)
    if yesterday == 'big_minus':
        reasons.append(f"昨日大幅マイナス（推定{yesterday_diff:+,}枚）→ 今日は上げ狙い目")
    elif yesterday == 'minus':
        reasons.append(f"昨日マイナス（推定{yesterday_diff:+,}枚）")
    elif yesterday == 'big_plus':
        reasons.append(f"昨日大幅プラス（推定{yesterday_diff:+,}枚）→ 据え置きor下げ注意")
    elif yesterday == 'plus':
        reasons.append(f"昨日プラス（推定{yesterday_diff:+,}枚）")

    # 3. ART確率トレンド
    if trend.get('art_trend') == 'improving':
        reasons.append("直近のART確率が改善傾向")
    elif trend.get('art_trend') == 'declining':
        reasons.append("直近のART確率が悪化傾向")

    # 4. 7日間平均
    avg_art = trend.get('avg_art_7days', 0)
    if avg_art >= 40:
        reasons.append(f"7日平均ART{avg_art:.0f}回（高稼働・高設定傾向）")
    elif avg_art >= 25:
        reasons.append(f"7日平均ART{avg_art:.0f}回")

    # 5. 当日の状況
    reasons.extend(today.get('today_reasons', []))

    # 6. 他台との比較
    if comparison.get('is_top_performer'):
        reasons.append("本日この店舗でトップの出玉")
    elif comparison.get('rank_in_store', 99) <= 3 and comparison.get('total_units', 0) > 3:
        reasons.append(f"本日{comparison['rank_in_store']}位/{comparison['total_units']}台")

    # 7. ランク変動
    if final_rank < base_rank:  # ランクが上がった（A < B）
        reasons.append(f"本日データで評価上昇（{base_rank}→{final_rank}）")

    return reasons


def recommend_units(store_key: str, realtime_data: dict = None) -> list:
    """推奨台リストを生成

    Args:
        store_key: 店舗キー
        realtime_data: リアルタイムで取得したデータ（オプション）

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
        today_analysis = {'status': '未稼働', 'today_score_bonus': 0, 'today_reasons': []}

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
        if daily_data and today_analysis.get('status') == '未稼働':
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

        # 推奨理由を生成
        reasons = generate_reasons(
            unit_id, trend_data, today_analysis, comparison, base_rank, final_rank
        )

        rec = {
            'unit_id': unit_id,
            'base_rank': base_rank,
            'base_score': base_score,
            'final_rank': final_rank,
            'final_score': final_score,
            'status': today_analysis.get('status', '不明'),
            'is_running': today_analysis.get('is_running', False),
            'art_count': today_analysis.get('art_count', 0),
            'bb_count': today_analysis.get('bb_count', 0),
            'rb_count': today_analysis.get('rb_count', 0),
            'total_games': today_analysis.get('total_games', 0),
            'art_prob': today_analysis.get('art_prob', 0),
            'last_hit_time': today_analysis.get('last_hit_time'),
            'first_hit_time': today_analysis.get('first_hit_time'),
            'note': note,
            # 詳細分析データ
            'trend': trend_data,
            'comparison': comparison,
            'reasons': reasons,
            # サマリー
            'yesterday_diff': trend_data.get('yesterday_diff', 0),
            'consecutive_plus': trend_data.get('consecutive_plus', 0),
            'consecutive_minus': trend_data.get('consecutive_minus', 0),
            'avg_art_7days': trend_data.get('avg_art_7days', 0),
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
