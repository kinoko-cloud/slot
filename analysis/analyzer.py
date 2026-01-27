#!/usr/bin/env python3
"""
SBJ データ分析・良台判定システム

評価軸：
1. ART回数・確率 - 高設定の指標
2. 稼働状況 - 十分に回されたか
3. 初当たり時間 - 開店から最初の当たりまで
4. 最終当たり時間 - 最後の当たりから閉店まで
5. 稼働の連続性 - 飛び飛びか集中か
6. 天井到達 - 999G以上のハマり
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# 店舗設定
OPEN_TIME = "10:00"   # 開店時間
CLOSE_TIME = "22:40"  # 閉店時間

# SBJ設定別ART確率（参考値）
SBJ_ART_PROB = {
    1: 1/350,
    2: 1/340,
    3: 1/320,
    4: 1/300,
    5: 1/280,
    6: 1/260,
}

# SBJ天井
SBJ_CEILING = 999

# AT間で連チャンとみなす閾値（この値以下なら連チャン）
RENCHAIN_THRESHOLD = 70


def is_big_hit(hit_type: str) -> bool:
    """大当たり判定（BB/AT/ART = 大当たり、RB/REG = 非大当たり）

    機種や表記によってBB/AT/ARTの呼び方は違うが、全て同一の「大当たり」扱い。
    RB(REG)のみがカウントされない（AT間をリセットしない）。
    """
    return hit_type in ('ART', 'AT', 'BB')


def calculate_at_intervals(history: list) -> list:
    """履歴データからAT間（大当たり間のG数）を正しく計算する

    AT間の定義:
    - 前の大当たり（BB/AT/ART）が終わってから次の大当たりまでの総G数
    - 途中のRB/REGのstartも加算する（RBではAT間はリセットされない）
    - 最大値は天井（999G+α）

    Args:
        history: 当たり履歴リスト。各要素に 'start', 'type' フィールドが必要

    Returns:
        AT間のG数リスト（各大当たりに到達するまでのG数）
    """
    if not history:
        return []

    # 時間順にソート
    sorted_history = sorted(history, key=lambda x: x.get('time', '00:00'))

    at_intervals = []
    accumulated_games = 0  # 大当たり間に蓄積されたG数

    for hit in sorted_history:
        start = hit.get('start', 0)
        hit_type = hit.get('type', '')

        accumulated_games += start

        if is_big_hit(hit_type):
            # 大当たり（BB/AT/ART）に到達 → accumulated_gamesがAT間
            at_intervals.append(accumulated_games)
            accumulated_games = 0  # リセット
        # RB/REGの場合はaccumulated_gamesを継続（AT間に加算）

    return at_intervals


def calculate_current_at_games(history: list, final_start: int = 0) -> int:
    """現在のAT間G数を計算（最終大当たりから現在までの総G数）

    最終大当たり（BB/AT/ART）の後にRBが挟まっている場合、
    final_startだけでは不十分。最終大当たり以降の全hitのstart + final_start を合算する。

    Args:
        history: 当たり履歴リスト
        final_start: 最終当たり後のG数（リアルタイムデータから取得）

    Returns:
        最終大当たりからの総G数（= 現在のAT間）
    """
    if not history:
        return final_start

    # 時間順にソート
    sorted_history = sorted(history, key=lambda x: x.get('time', '00:00'))

    # 最後の大当たり（BB/AT/ART）の位置を探す
    last_big_hit_index = -1
    for i, hit in enumerate(sorted_history):
        if is_big_hit(hit.get('type', '')):
            last_big_hit_index = i

    if last_big_hit_index == -1:
        # 大当たりが1回もない → 全start + final_startが現在のAT間
        total = sum(h.get('start', 0) for h in sorted_history) + final_start
        return total

    # 最終大当たり以降のhit（RB等）のstart + final_startを合算
    games_after_last_big_hit = 0
    for hit in sorted_history[last_big_hit_index + 1:]:
        games_after_last_big_hit += hit.get('start', 0)
    games_after_last_big_hit += final_start

    return games_after_last_big_hit


def time_to_minutes(time_str: str) -> int:
    """時間文字列を分に変換（10:00からの経過分）"""
    h, m = map(int, time_str.split(':'))
    return (h - 10) * 60 + m


def minutes_to_time(minutes: int) -> str:
    """分を時間文字列に変換"""
    h = 10 + minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"


def analyze_day(day_data: dict, store_context: dict = None) -> dict:
    """1日分のデータを分析

    Args:
        day_data: 日別データ
        store_context: 店舗コンテキスト（平均稼働量など）
    """
    result = {
        'date': day_data.get('date'),
        'unit_id': day_data.get('unit_id'),
        'bb': day_data.get('bb', 0),
        'rb': day_data.get('rb', 0),
        'art': day_data.get('art', 0),
        'total_start': day_data.get('total_start', 0),
        'max_medals': day_data.get('max_medals', 0),
        'store_context': store_context,
    }

    history = day_data.get('history', [])
    if not history:
        # 履歴がなくてもサマリーデータで評価を試みる
        art_count = day_data.get('art', 0)
        total_start = day_data.get('total_start', 0)

        if total_start > 0 and art_count > 0:
            result['art_probability'] = total_start / art_count
            result['estimated_setting'] = estimate_setting(result['art_probability'])

        # 稼働十分性判定（相対評価・曜日考慮）
        if store_context:
            date_str = day_data.get('date')
            avg_for_day = store_context.get('avg_games_per_day', 2000)

            if date_str:
                try:
                    from datetime import datetime as dt
                    date = dt.strptime(date_str, '%Y-%m-%d')
                    if date.weekday() >= 5:
                        avg_for_day = store_context.get('weekend_avg', avg_for_day)
                        result['day_type'] = 'weekend'
                    else:
                        avg_for_day = store_context.get('weekday_avg', avg_for_day)
                        result['day_type'] = 'weekday'
                except:
                    pass

            threshold = avg_for_day * 0.5 if avg_for_day > 0 else 2000
            result['sufficient_threshold'] = threshold
            result['day_avg'] = avg_for_day
            result['is_sufficient_plays'] = total_start >= threshold
        else:
            result['sufficient_threshold'] = 2000
            result['is_sufficient_plays'] = total_start >= 2000
        result['is_sporadic'] = False  # 判定不可
        result['ceiling_hits'] = 0
        result['max_hamar'] = 0
        result['first_hit_time'] = None
        result['last_hit_time'] = None
        result['time_to_close_minutes'] = 0

        # 評価を生成
        result['evaluation'] = evaluate_day(result)
        return result

    # 時間順にソート（降順になっているので逆順に）
    history_sorted = sorted(history, key=lambda x: x.get('time', '00:00'))

    # 1. 初当たり時間
    first_hit_time = history_sorted[0].get('time', '10:00')
    first_hit_minutes = time_to_minutes(first_hit_time)
    result['first_hit_time'] = first_hit_time
    result['first_hit_delay_minutes'] = first_hit_minutes

    # 2. 最終当たり時間
    last_hit_time = history_sorted[-1].get('time', '22:00')
    close_minutes = time_to_minutes(CLOSE_TIME)
    last_hit_minutes = time_to_minutes(last_hit_time)
    result['last_hit_time'] = last_hit_time
    result['time_to_close_minutes'] = close_minutes - last_hit_minutes

    # 3. 稼働時間帯
    result['active_hours'] = last_hit_minutes - first_hit_minutes

    # 4. 当たり間隔の分析
    hit_times = [time_to_minutes(h.get('time', '00:00')) for h in history_sorted]
    if len(hit_times) > 1:
        intervals = [hit_times[i+1] - hit_times[i] for i in range(len(hit_times)-1)]
        result['avg_interval_minutes'] = sum(intervals) / len(intervals)
        result['max_interval_minutes'] = max(intervals)

        # 飛び飛び判定（30分以上の空白が3回以上あれば飛び飛び）
        long_gaps = [i for i in intervals if i > 30]
        result['long_gaps_count'] = len(long_gaps)
        result['is_sporadic'] = len(long_gaps) >= 3
    else:
        result['avg_interval_minutes'] = 0
        result['max_interval_minutes'] = 0
        result['long_gaps_count'] = 0
        result['is_sporadic'] = True

    # 5. ART確率計算
    total_start = day_data.get('total_start', 0)
    art_count = day_data.get('art', 0)
    if total_start > 0 and art_count > 0:
        result['art_probability'] = total_start / art_count
        # 設定推測
        result['estimated_setting'] = estimate_setting(result['art_probability'])
    else:
        result['art_probability'] = 0
        result['estimated_setting'] = None

    # 6. 天井到達チェック（AT間ベース: RBを跨いだART→ART間のG数で判定）
    at_intervals = calculate_at_intervals(history_sorted)

    ceiling_hits = [g for g in at_intervals if g >= SBJ_CEILING]
    result['ceiling_hits'] = len(ceiling_hits)
    result['max_hamar'] = max(at_intervals) if at_intervals else 0
    result['at_intervals'] = at_intervals  # 全AT間データを保持

    # 7. 稼働十分性判定
    # 店舗+機種+曜日の平均50%以上なら十分と判定（相対評価）
    if store_context:
        # 曜日を判定
        date_str = day_data.get('date')
        avg_for_day = store_context.get('avg_games_per_day', 2000)

        if date_str:
            try:
                from datetime import datetime as dt
                date = dt.strptime(date_str, '%Y-%m-%d')
                if date.weekday() >= 5:  # 土日
                    avg_for_day = store_context.get('weekend_avg', avg_for_day)
                    result['day_type'] = 'weekend'
                else:
                    avg_for_day = store_context.get('weekday_avg', avg_for_day)
                    result['day_type'] = 'weekday'
            except:
                pass

        threshold = avg_for_day * 0.5 if avg_for_day > 0 else 2000
        result['sufficient_threshold'] = threshold
        result['day_avg'] = avg_for_day
        result['is_sufficient_plays'] = total_start >= threshold
    else:
        # フォールバック: 固定閾値2000G
        result['sufficient_threshold'] = 2000
        result['is_sufficient_plays'] = total_start >= 2000

    # 8. 総合評価
    result['evaluation'] = evaluate_day(result)

    return result


def estimate_setting(art_prob: float) -> Optional[int]:
    """ART確率から設定を推測"""
    if art_prob <= 0:
        return None

    # 最も近い設定を探す
    closest_setting = None
    min_diff = float('inf')

    for setting, prob in SBJ_ART_PROB.items():
        expected_interval = 1 / prob
        diff = abs(art_prob - expected_interval)
        if diff < min_diff:
            min_diff = diff
            closest_setting = setting

    return closest_setting


def evaluate_day(analysis: dict) -> dict:
    """総合評価を生成"""
    score = 50  # 基準点
    reasons = []

    # ART確率による評価
    art_prob = analysis.get('art_probability', 0)
    if art_prob > 0:
        if art_prob <= 280:
            score += 20
            reasons.append(f"ART確率良好 (1/{art_prob:.0f})")
        elif art_prob <= 320:
            score += 10
            reasons.append(f"ART確率やや良 (1/{art_prob:.0f})")
        elif art_prob >= 400:
            score -= 10
            reasons.append(f"ART確率低め (1/{art_prob:.0f})")

    # 稼働十分性
    if not analysis.get('is_sufficient_plays', False):
        score -= 15
        total = analysis.get('total_start', 0)
        reasons.append(f"稼働不足 ({total}G)")

        # ただし、稼働不足でもART確率が良ければ救済
        if art_prob > 0 and art_prob <= 300:
            score += 10
            reasons.append("→ ART確率から高設定の可能性")

    # 飛び飛び稼働
    if analysis.get('is_sporadic', False):
        score -= 5
        reasons.append("稼働が飛び飛び")

    # 初当たりが遅い（開店から2時間以上）
    first_delay = analysis.get('first_hit_delay_minutes')
    if first_delay is not None and first_delay >= 120:
        reasons.append(f"初当たり遅め ({minutes_to_time(first_delay)})")
        # ただし稼働自体が遅いだけの可能性
        if analysis.get('time_to_close_minutes', 0) <= 60:
            reasons.append("→ 閉店まで稼働、問題なし")

    # 閉店前に放置
    time_to_close = analysis.get('time_to_close_minutes', 0)
    if time_to_close >= 120:
        score -= 5
        reasons.append(f"閉店前{time_to_close}分放置")

    # 天井到達
    ceiling_hits = analysis.get('ceiling_hits', 0)
    if ceiling_hits > 0:
        score -= 5 * ceiling_hits
        reasons.append(f"天井到達 {ceiling_hits}回")

    # ART回数
    art_count = analysis.get('art', 0)
    if art_count >= 50:
        score += 15
        reasons.append(f"ART多数 ({art_count}回)")
    elif art_count >= 30:
        score += 5
        reasons.append(f"ART中程度 ({art_count}回)")
    elif art_count <= 10:
        score -= 5
        if analysis.get('total_start', 0) < 1500:
            reasons.append(f"ART少 ({art_count}回) ※稼働不足")
        else:
            reasons.append(f"ART少 ({art_count}回)")

    # 評価ランク
    if score >= 70:
        rank = 'A'
        recommendation = '狙い目'
    elif score >= 55:
        rank = 'B'
        recommendation = 'やや期待'
    elif score >= 40:
        rank = 'C'
        recommendation = '様子見'
    else:
        rank = 'D'
        recommendation = '非推奨'

    return {
        'score': score,
        'rank': rank,
        'recommendation': recommendation,
        'reasons': reasons,
    }


def analyze_unit(unit_data: dict, store_context: dict = None) -> dict:
    """1台分の全日データを分析

    Args:
        unit_data: 台データ
        store_context: 店舗コンテキスト（平均稼働量など）
            - avg_games_per_day: 店舗の1日平均G数
            - weekday_avg: 平日平均
            - weekend_avg: 休日平均
    """
    result = {
        'unit_id': unit_data.get('unit_id'),
        'hall_name': unit_data.get('hall_name'),
        'machine_name': unit_data.get('machine_name'),
        'days': [],
        'trend': {},
    }

    days_analysis = []
    for day in unit_data.get('days', []):
        # 店舗コンテキストがあれば渡す
        day_analysis = analyze_day(day, store_context)
        days_analysis.append(day_analysis)
        result['days'].append(day_analysis)

    # トレンド分析
    if days_analysis:
        scores = [d['evaluation']['score'] for d in days_analysis]
        art_counts = [d.get('art', 0) for d in days_analysis]
        total_starts = [d.get('total_start', 0) for d in days_analysis]

        result['trend'] = {
            'avg_score': sum(scores) / len(scores),
            'max_score': max(scores),
            'min_score': min(scores),
            'total_art': sum(art_counts),
            'avg_art': sum(art_counts) / len(art_counts),
            'total_games': sum(total_starts),
            'avg_games': sum(total_starts) / len(total_starts),
            'best_day': days_analysis[scores.index(max(scores))].get('date'),
            'worst_day': days_analysis[scores.index(min(scores))].get('date'),
        }

        # 台の総合評価
        avg_score = result['trend']['avg_score']
        if avg_score >= 65:
            result['overall_rank'] = 'A'
            result['overall_recommendation'] = '継続して狙い目'
        elif avg_score >= 50:
            result['overall_rank'] = 'B'
            result['overall_recommendation'] = '状況次第で狙い'
        elif avg_score >= 40:
            result['overall_rank'] = 'C'
            result['overall_recommendation'] = '避けた方が良い'
        else:
            result['overall_rank'] = 'D'
            result['overall_recommendation'] = '非推奨'

    return result


def generate_report(all_units: list) -> str:
    """分析レポートを生成"""
    report = []
    report.append("=" * 70)
    report.append("SBJ 良台分析レポート")
    report.append(f"生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    report.append("=" * 70)

    for unit_data in all_units:
        analysis = analyze_unit(unit_data)

        report.append("")
        report.append(f"【台{analysis['unit_id']}】{analysis.get('hall_name', '')}")
        report.append(f"  機種: {analysis.get('machine_name', 'SBJ')}")
        report.append(f"  総合評価: {analysis.get('overall_rank', '-')} - {analysis.get('overall_recommendation', '')}")

        trend = analysis.get('trend', {})
        if trend:
            report.append(f"  平均スコア: {trend.get('avg_score', 0):.1f}")
            report.append(f"  7日間ART合計: {trend.get('total_art', 0)}回")
            report.append(f"  7日間総G数: {trend.get('total_games', 0):,}G")
            report.append(f"  最高日: {trend.get('best_day', '-')}")

        report.append("")
        report.append("  日別詳細:")
        for day in analysis.get('days', []):
            date = day.get('date', '-')
            art = day.get('art', 0)
            total = day.get('total_start', 0)
            eval_data = day.get('evaluation', {})
            score = eval_data.get('score', 0)
            rank = eval_data.get('rank', '-')
            reasons = eval_data.get('reasons', [])

            report.append(f"    {date}: ART {art}回, {total:,}G | {rank}({score}点)")
            for reason in reasons[:3]:
                report.append(f"      - {reason}")

        report.append("-" * 70)

    return "\n".join(report)


def main():
    """メイン処理"""
    # データ読み込み
    data_path = Path('data/raw/sbj_all_history_20260126_1221.json')
    if not data_path.exists():
        print(f"データファイルが見つかりません: {data_path}")
        return

    with open(data_path, 'r', encoding='utf-8') as f:
        all_units = json.load(f)

    # 分析実行
    print("分析中...")

    # レポート生成
    report = generate_report(all_units)
    print(report)

    # レポート保存
    report_path = Path('data/raw') / f'sbj_analysis_report_{datetime.now().strftime("%Y%m%d_%H%M")}.txt'
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n✓ レポート保存: {report_path}")

    # JSON形式でも保存
    analysis_results = [analyze_unit(unit) for unit in all_units]
    json_path = Path('data/raw') / f'sbj_analysis_{datetime.now().strftime("%Y%m%d_%H%M")}.json'
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(analysis_results, f, ensure_ascii=False, indent=2)
    print(f"✓ 分析結果JSON保存: {json_path}")


if __name__ == "__main__":
    main()
