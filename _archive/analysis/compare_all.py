#!/usr/bin/env python3
"""
全店舗SBJ比較分析
複数店舗のSBJデータを統合して比較レポートを生成
"""

import json
from datetime import datetime
from pathlib import Path
from analyzer import analyze_unit


def load_latest_data() -> list:
    """最新のデータファイルを読み込む"""
    data_dir = Path('data/raw')
    all_units = []

    # 渋谷エスパス（daidata）
    daidata_files = sorted(data_dir.glob('sbj_all_history_*.json'), reverse=True)
    if daidata_files:
        with open(daidata_files[0], 'r', encoding='utf-8') as f:
            all_units.extend(json.load(f))
        print(f"✓ 読込: {daidata_files[0].name}")

    # 秋葉原アイランド（papimo）
    papimo_files = sorted(data_dir.glob('papimo_island_sbj_*.json'), reverse=True)
    if papimo_files:
        with open(papimo_files[0], 'r', encoding='utf-8') as f:
            all_units.extend(json.load(f))
        print(f"✓ 読込: {papimo_files[0].name}")

    return all_units


def calculate_store_contexts(all_units: list) -> dict:
    """店舗+機種+曜日ごとの平均稼働量を計算"""
    from datetime import datetime as dt
    contexts = {}

    # 店舗+機種ごとにグループ化
    by_hall_machine = {}
    for unit in all_units:
        hall = unit.get('hall_name', '不明')
        machine = unit.get('machine_name', '不明')
        key = f"{hall}|{machine}"
        if key not in by_hall_machine:
            by_hall_machine[key] = []
        by_hall_machine[key].append(unit)

    # 各店舗+機種の平均を計算（曜日別）
    for key, units in by_hall_machine.items():
        hall, machine = key.split('|')
        weekday_games = []  # 月〜金
        weekend_games = []  # 土日

        for unit in units:
            for day in unit.get('days', []):
                total = day.get('total_start', 0)
                date_str = day.get('date')
                if total > 0 and date_str:
                    try:
                        date = dt.strptime(date_str, '%Y-%m-%d')
                        if date.weekday() >= 5:  # 土日
                            weekend_games.append(total)
                        else:
                            weekday_games.append(total)
                    except:
                        weekday_games.append(total)

        all_games = weekday_games + weekend_games
        if all_games:
            contexts[key] = {
                'avg_games_per_day': sum(all_games) / len(all_games),
                'weekday_avg': sum(weekday_games) / len(weekday_games) if weekday_games else 0,
                'weekend_avg': sum(weekend_games) / len(weekend_games) if weekend_games else 0,
                'unit_count': len(units),
                'hall_name': hall,
                'machine_name': machine,
            }
            wd = contexts[key]['weekday_avg']
            we = contexts[key]['weekend_avg']
            print(f"  {hall} - {machine}:")
            print(f"    平日: {wd:.0f}G/日, 休日: {we:.0f}G/日 ({len(units)}台)")

    return contexts


def generate_comparison_report(all_units: list) -> str:
    """全店舗比較レポートを生成"""
    report = []
    report.append("=" * 70)
    report.append("SBJ 良台ランキング - 全店舗比較")
    report.append(f"生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    report.append("=" * 70)

    # 店舗コンテキストを計算
    print("\n【店舗平均稼働量】")
    store_contexts = calculate_store_contexts(all_units)

    # 全台を分析（店舗+機種コンテキストを渡す）
    all_analysis = []
    for unit in all_units:
        hall = unit.get('hall_name', '不明')
        machine = unit.get('machine_name', '不明')
        key = f"{hall}|{machine}"
        context = store_contexts.get(key)
        analysis = analyze_unit(unit, context)
        all_analysis.append(analysis)

    # スコア順にソート
    all_analysis.sort(key=lambda x: x.get('trend', {}).get('avg_score', 0), reverse=True)

    # ランキング表
    report.append("")
    report.append("【総合ランキング（平均スコア順）】")
    report.append("")
    report.append(f"{'順位':^4} {'店舗':<18} {'台番':^6} {'評価':^4} {'平均':^6} {'7日ART':^8} {'7日G数':^10}")
    report.append("-" * 70)

    for i, analysis in enumerate(all_analysis, 1):
        hall = analysis.get('hall_name', '')[:16]
        unit_id = analysis.get('unit_id', '')
        rank = analysis.get('overall_rank', '-')
        trend = analysis.get('trend', {})
        avg_score = trend.get('avg_score', 0)
        total_art = trend.get('total_art', 0)
        total_games = trend.get('total_games', 0)

        report.append(f"{i:^4} {hall:<18} {unit_id:^6} {rank:^4} {avg_score:>5.1f}  {total_art:>6}回  {total_games:>8,}G")

    # 店舗別サマリー
    report.append("")
    report.append("=" * 70)
    report.append("【店舗別サマリー】")
    report.append("=" * 70)

    halls = {}
    for analysis in all_analysis:
        hall = analysis.get('hall_name', '不明')
        if hall not in halls:
            halls[hall] = {'scores': [], 'art': [], 'games': []}
        trend = analysis.get('trend', {})
        halls[hall]['scores'].append(trend.get('avg_score', 0))
        halls[hall]['art'].append(trend.get('total_art', 0))
        halls[hall]['games'].append(trend.get('total_games', 0))

    best_hall = None
    best_score = 0

    for hall, data in halls.items():
        avg_score = sum(data['scores']) / len(data['scores'])
        total_art = sum(data['art'])
        total_games = sum(data['games'])
        count = len(data['scores'])

        report.append(f"  {hall}: 平均スコア {avg_score:.1f} ({count}台)")
        report.append(f"    - 合計ART: {total_art}回")
        report.append(f"    - 合計G数: {total_games:,}G")

        if avg_score > best_score:
            best_score = avg_score
            best_hall = hall

    report.append("")
    if best_hall:
        report.append(f"  ★ 推奨: {best_hall} (平均スコア {best_score:.1f})")

    # 詳細レポート
    report.append("")
    report.append("=" * 70)
    report.append("【台別詳細】")
    report.append("=" * 70)

    for analysis in all_analysis:
        report.append("")
        report.append(f"【台{analysis['unit_id']}】{analysis.get('hall_name', '')}")
        report.append(f"  評価: {analysis.get('overall_rank', '-')} - {analysis.get('overall_recommendation', '')}")

        trend = analysis.get('trend', {})
        if trend:
            report.append(f"  平均スコア: {trend.get('avg_score', 0):.1f}")
            report.append(f"  期間ART: {trend.get('total_art', 0)}回 / 総G数: {trend.get('total_games', 0):,}G")

        report.append("  日別:")
        for day in analysis.get('days', [])[:5]:  # 最新5日
            date = day.get('date', '-')
            art = day.get('art', 0)
            total = day.get('total_start', 0)
            eval_data = day.get('evaluation', {})
            score = eval_data.get('score', 0)
            rank = eval_data.get('rank', '-')
            report.append(f"    {date}: ART {art:2d}回, {total:,}G | {rank}({score}点)")

        report.append("-" * 70)

    return "\n".join(report)


def main():
    """メイン処理"""
    print("SBJ 全店舗分析")
    print("=" * 50)

    # データ読み込み
    all_units = load_latest_data()

    if not all_units:
        print("データファイルが見つかりません")
        return

    print(f"対象台数: {len(all_units)}台")
    print()

    # レポート生成
    report = generate_comparison_report(all_units)
    print(report)

    # レポート保存
    report_path = Path('data/raw') / f'sbj_comparison_{datetime.now().strftime("%Y%m%d_%H%M")}.txt'
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n✓ レポート保存: {report_path}")


if __name__ == "__main__":
    main()
