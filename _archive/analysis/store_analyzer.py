#!/usr/bin/env python3
"""
店舗別SBJ傾向分析スクリプト

使い方:
    python analysis/store_analyzer.py
    python analysis/store_analyzer.py --store island
    python analysis/store_analyzer.py --store shibuya
"""

import json
import argparse
from collections import defaultdict
from pathlib import Path
from datetime import datetime


def load_island_data():
    """アイランド秋葉原のデータを読み込み"""
    path = Path('data/raw/papimo_island_sbj_20260126_1237.json')
    if not path.exists():
        # 最新のファイルを探す
        files = sorted(Path('data/raw').glob('papimo_island_sbj_*.json'))
        if files:
            path = files[-1]
        else:
            return None

    with open(path, 'r') as f:
        return json.load(f)


def load_shibuya_data():
    """渋谷エスパスのデータを読み込み"""
    path = Path('data/raw/sbj_all_history_20260126_1221.json')
    if not path.exists():
        files = sorted(Path('data/raw').glob('sbj_all_history_*.json'))
        if files:
            path = files[-1]
        else:
            return None

    with open(path, 'r') as f:
        return json.load(f)


def analyze_store(data, store_name):
    """店舗データを分析"""
    print("=" * 80)
    print(f"{store_name} SBJ 傾向分析")
    print("=" * 80)

    # データ整理
    all_days = []
    unit_stats = defaultdict(lambda: {'days': [], 'total_art': 0, 'total_games': 0})

    for unit in data:
        unit_id = unit['unit_id']
        for day in unit.get('days', []):
            date = day.get('date', '')
            art = day.get('art', 0)
            total_start = day.get('total_start', 0)
            max_medals = day.get('max_medals', 0)
            art_games = day.get('art_games', 0)

            if total_start > 0:
                art_prob = total_start / art if art > 0 else 9999
                art_ratio = art_games / total_start * 100 if total_start > 0 else 0

                day_info = {
                    'unit_id': unit_id,
                    'date': date,
                    'art': art,
                    'games': total_start,
                    'art_prob': art_prob,
                    'max_medals': max_medals,
                    'art_games': art_games,
                    'art_ratio': art_ratio
                }

                all_days.append(day_info)
                unit_stats[unit_id]['days'].append(day_info)
                unit_stats[unit_id]['total_art'] += art
                unit_stats[unit_id]['total_games'] += total_start

    # 概要
    print(f"\n【データ概要】")
    print(f"台数: {len(unit_stats)}台")
    num_days = len(set(d['date'] for d in all_days))
    print(f"データ日数: {num_days}日")

    total_art = sum(d['art'] for d in all_days)
    total_games = sum(d['games'] for d in all_days)
    overall_prob = total_games / total_art if total_art > 0 else 0
    print(f"全体ART確率: 1/{overall_prob:.1f}")

    # 日別分析
    print(f"\n【日別 高設定投入推定】")
    print(f"{'日付':<12} {'高設定(~150)':<14} {'中間(150-200)':<14} {'低設定(200~)':<12}")
    print("-" * 55)

    date_stats = defaultdict(list)
    for d in all_days:
        date_stats[d['date']].append(d)

    for date in sorted(date_stats.keys(), reverse=True):
        day_data = date_stats[date]
        min_games = 3000 if len(unit_stats) > 5 else 2000
        high = sum(1 for d in day_data if d['art_prob'] < 150 and d['games'] > min_games)
        mid = sum(1 for d in day_data if 150 <= d['art_prob'] < 200 and d['games'] > min_games)
        low = sum(1 for d in day_data if d['art_prob'] >= 200 and d['games'] > min_games)

        print(f"{date:<12} {high:<14} {mid:<14} {low:<12}")

    # 台別成績
    print(f"\n【台別 成績（勝率順）】")
    print(f"{'台番号':<8} {'総ART':<8} {'総G数':<10} {'平均確率':<10} {'勝敗':<12} {'評価'}")
    print("-" * 65)

    unit_results = []
    for unit_id, stats in unit_stats.items():
        avg_prob = stats['total_games'] / stats['total_art'] if stats['total_art'] > 0 else 9999

        wins = 0
        losses = 0
        for d in stats['days']:
            if d['games'] < 1000:
                continue
            # 勝ち判定: 最大持ち3000以上 or ART比率40%以上 or ART確率1/120以下
            if d['max_medals'] >= 3000 or d.get('art_ratio', 0) >= 40 or d['art_prob'] <= 120:
                wins += 1
            else:
                losses += 1

        if avg_prob < 130:
            rating = "★★★"
        elif avg_prob < 150:
            rating = "★★"
        elif avg_prob < 180:
            rating = "★"
        else:
            rating = "-"

        win_rate = wins/(wins+losses)*100 if wins+losses > 0 else 0
        unit_results.append({
            'unit_id': unit_id,
            'total_art': stats['total_art'],
            'total_games': stats['total_games'],
            'avg_prob': avg_prob,
            'wins': wins,
            'losses': losses,
            'win_rate': win_rate,
            'rating': rating
        })

    # 勝率順にソート
    for u in sorted(unit_results, key=lambda x: x['win_rate'], reverse=True):
        print(f"{u['unit_id']:<8} {u['total_art']:<8} {u['total_games']:<10,} 1/{u['avg_prob']:<8.1f} {u['wins']}勝{u['losses']}敗({u['win_rate']:.0f}%) {u['rating']}")

    # 爆発台TOP3
    print(f"\n【爆発台 TOP3（最大持ちメダル合計）】")

    unit_explosion = defaultdict(lambda: {'max_sum': 0, 'max_single': 0})
    for unit in data:
        unit_id = unit['unit_id']
        for day in unit.get('days', []):
            max_medals = day.get('max_medals', 0)
            unit_explosion[unit_id]['max_sum'] += max_medals
            if max_medals > unit_explosion[unit_id]['max_single']:
                unit_explosion[unit_id]['max_single'] = max_medals

    top3 = sorted(unit_explosion.items(), key=lambda x: x[1]['max_sum'], reverse=True)[:3]
    for i, (unit_id, stats) in enumerate(top3, 1):
        print(f"{i}位: 台{unit_id} - 合計{stats['max_sum']:,}枚 (最高{stats['max_single']:,}枚)")

    return {
        'store_name': store_name,
        'unit_count': len(unit_stats),
        'days': num_days,
        'overall_prob': overall_prob,
        'unit_results': unit_results,
        'top3': top3
    }


def main():
    parser = argparse.ArgumentParser(description='店舗別SBJ傾向分析')
    parser.add_argument('--store', choices=['island', 'shibuya', 'all'],
                        default='all', help='分析対象店舗')
    args = parser.parse_args()

    results = []

    if args.store in ['island', 'all']:
        data = load_island_data()
        if data:
            result = analyze_store(data, 'アイランド秋葉原店')
            results.append(result)
        else:
            print("アイランド秋葉原のデータが見つかりません")

    if args.store in ['shibuya', 'all']:
        data = load_shibuya_data()
        if data:
            result = analyze_store(data, '渋谷エスパス新館')
            results.append(result)
        else:
            print("渋谷エスパスのデータが見つかりません")

    # 比較
    if len(results) >= 2:
        print("\n" + "=" * 80)
        print("【店舗比較】")
        print("=" * 80)
        print(f"{'項目':<20} ", end="")
        for r in results:
            print(f"{r['store_name']:<20} ", end="")
        print()
        print("-" * 60)

        print(f"{'台数':<20} ", end="")
        for r in results:
            print(f"{r['unit_count']}台{'':<16} ", end="")
        print()

        print(f"{'全体ART確率':<20} ", end="")
        for r in results:
            print(f"1/{r['overall_prob']:.1f}{'':<13} ", end="")
        print()


if __name__ == '__main__':
    main()
