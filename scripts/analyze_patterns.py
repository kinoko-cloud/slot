#!/usr/bin/env python3
"""
機種別・設定別の1日挙動パターン分析

SBJと北斗転生2を分けて、高設定/低設定の挙動パターンを分類
"""

import json
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).parent.parent / 'data' / 'history'

def parse_time(time_str):
    """時刻文字列を分に変換"""
    try:
        h, m = map(int, time_str.split(':'))
        return h * 60 + m
    except:
        return None

def analyze_day_pattern(day_data):
    """
    1日の挙動を分析してパターン化
    """
    history = day_data.get('history', [])
    if not history or len(history) < 3:
        return None
    
    art = day_data.get('art', 0)
    games = day_data.get('games', 0)
    prob = day_data.get('prob', 999)
    is_good = day_data.get('is_good', False)
    diff_medals = day_data.get('diff_medals', 0) or 0
    max_rensa = day_data.get('max_rensa', 0)
    
    # 時間帯別の当たり回数
    morning_hits = 0   # 10:00-13:00
    afternoon_hits = 0 # 13:00-17:00
    evening_hits = 0   # 17:00-22:00
    
    # ハマり分析
    hamari_list = []
    sorted_history = sorted(history, key=lambda x: parse_time(x.get('time', '00:00')) or 0)
    
    prev_time = 600  # 10:00開店
    for hit in sorted_history:
        hit_time = parse_time(hit.get('time', ''))
        if not hit_time:
            continue
        
        # 時間帯カウント
        if hit_time < 780:  # 13:00
            morning_hits += 1
        elif hit_time < 1020:  # 17:00
            afternoon_hits += 1
        else:
            evening_hits += 1
        
        # ハマり計算（前回からの分数 × 30G）
        gap_minutes = hit_time - prev_time
        if gap_minutes > 0:
            hamari_list.append(gap_minutes * 30)
        prev_time = hit_time
    
    max_hamari = max(hamari_list) if hamari_list else 0
    avg_hamari = sum(hamari_list) / len(hamari_list) if hamari_list else 0
    big_hamari_count = len([h for h in hamari_list if h >= 500])  # 500G以上のハマり
    
    # 連チャン分析
    rensa_events = 0
    for i in range(1, len(sorted_history)):
        prev = parse_time(sorted_history[i-1].get('time', ''))
        curr = parse_time(sorted_history[i].get('time', ''))
        if prev and curr and (curr - prev) <= 2:
            rensa_events += 1
    
    rensa_rate = rensa_events / len(history) if history else 0
    
    # 出玉推移パターン判定
    medals_by_period = {'morning': 0, 'afternoon': 0, 'evening': 0}
    for hit in sorted_history:
        hit_time = parse_time(hit.get('time', ''))
        medals = hit.get('medals', 0)
        if hit_time < 780:
            medals_by_period['morning'] += medals
        elif hit_time < 1020:
            medals_by_period['afternoon'] += medals
        else:
            medals_by_period['evening'] += medals
    
    return {
        'art': art,
        'games': games,
        'prob': prob,
        'is_good': is_good,
        'diff_medals': diff_medals,
        'max_rensa': max_rensa,
        'morning_hits': morning_hits,
        'afternoon_hits': afternoon_hits,
        'evening_hits': evening_hits,
        'max_hamari': max_hamari,
        'avg_hamari': avg_hamari,
        'big_hamari_count': big_hamari_count,
        'rensa_rate': rensa_rate,
        'medals_morning': medals_by_period['morning'],
        'medals_afternoon': medals_by_period['afternoon'],
        'medals_evening': medals_by_period['evening'],
    }

def classify_pattern(data):
    """
    挙動パターンを分類
    """
    patterns = []
    
    # 出玉パターン
    if data['diff_medals'] >= 3000:
        patterns.append('大勝ち(+3000↑)')
    elif data['diff_medals'] >= 1000:
        patterns.append('勝ち(+1000↑)')
    elif data['diff_medals'] >= -1000:
        patterns.append('トントン(±1000)')
    elif data['diff_medals'] >= -3000:
        patterns.append('負け(-1000↓)')
    else:
        patterns.append('大負け(-3000↓)')
    
    # ハマりパターン
    if data['max_hamari'] >= 800:
        patterns.append('大ハマり(800G↑)')
    elif data['max_hamari'] >= 500:
        patterns.append('中ハマり(500G↑)')
    else:
        patterns.append('ハマりなし(500G↓)')
    
    # 連チャンパターン
    if data['max_rensa'] >= 10:
        patterns.append('大連チャン(10連↑)')
    elif data['max_rensa'] >= 5:
        patterns.append('中連チャン(5連↑)')
    else:
        patterns.append('小連チャン(5連↓)')
    
    # 時間帯パターン
    total_hits = data['morning_hits'] + data['afternoon_hits'] + data['evening_hits']
    if total_hits > 0:
        if data['morning_hits'] / total_hits >= 0.4:
            patterns.append('朝型')
        elif data['evening_hits'] / total_hits >= 0.4:
            patterns.append('夜型')
        else:
            patterns.append('分散型')
    
    return patterns

def run_analysis():
    """分析実行"""
    # 機種別・設定別に集計
    results = {
        'sbj': {'high': [], 'low': []},
        'hokuto2': {'high': [], 'low': []},
        'hokuto': {'high': [], 'low': []},
    }
    
    for store_dir in sorted(DATA_DIR.iterdir()):
        if not store_dir.is_dir():
            continue
        
        store_key = store_dir.name
        
        # 機種判定
        if 'sbj' in store_key:
            machine = 'sbj'
        elif 'hokuto2' in store_key:
            machine = 'hokuto2'
        elif 'hokuto' in store_key:
            machine = 'hokuto'
        else:
            continue
        
        for unit_file in store_dir.glob('*.json'):
            try:
                with open(unit_file) as f:
                    data = json.load(f)
                
                for day in data.get('days', []):
                    pattern_data = analyze_day_pattern(day)
                    if not pattern_data:
                        continue
                    
                    setting = 'high' if pattern_data['is_good'] else 'low'
                    pattern_data['patterns'] = classify_pattern(pattern_data)
                    pattern_data['store'] = store_key
                    pattern_data['unit'] = data.get('unit_id')
                    pattern_data['date'] = day.get('date')
                    
                    results[machine][setting].append(pattern_data)
            except:
                continue
    
    return results

def print_results(results):
    """結果表示"""
    
    for machine in ['sbj', 'hokuto2', 'hokuto']:
        print("=" * 70)
        print(f"【{machine.upper()}】")
        print("=" * 70)
        
        for setting in ['high', 'low']:
            data_list = results[machine][setting]
            if not data_list:
                continue
            
            setting_label = "高設定" if setting == 'high' else "低設定"
            print(f"\n■ {setting_label} ({len(data_list)}件)")
            print("-" * 50)
            
            # 平均値計算
            avg_art = sum(d['art'] or 0 for d in data_list) / len(data_list)
            avg_prob = sum(d['prob'] or 999 for d in data_list) / len(data_list)
            avg_diff = sum(d['diff_medals'] or 0 for d in data_list) / len(data_list)
            avg_max_hamari = sum(d['max_hamari'] or 0 for d in data_list) / len(data_list)
            avg_max_rensa = sum(d['max_rensa'] or 0 for d in data_list) / len(data_list)
            avg_rensa_rate = sum(d['rensa_rate'] or 0 for d in data_list) / len(data_list)
            
            print(f"  平均ART回数: {avg_art:.1f}")
            print(f"  平均確率: 1/{avg_prob:.1f}")
            print(f"  平均差枚: {avg_diff:.0f}")
            print(f"  平均最大ハマり: {avg_max_hamari:.0f}G")
            print(f"  平均最大連チャン: {avg_max_rensa:.1f}連")
            print(f"  平均連チャン率: {avg_rensa_rate*100:.1f}%")
            
            # パターン集計
            print(f"\n  【パターン分布】")
            pattern_counts = defaultdict(int)
            for d in data_list:
                for p in d['patterns']:
                    pattern_counts[p] += 1
            
            for p, count in sorted(pattern_counts.items(), key=lambda x: -x[1]):
                pct = count / len(data_list) * 100
                print(f"    {p}: {count}件 ({pct:.1f}%)")
            
            # 特徴的な例
            print(f"\n  【典型例】")
            # 最も差枚が多い例
            best = max(data_list, key=lambda x: x['diff_medals'] or 0)
            print(f"    最高: {best['date']} {best['store']} #{best['unit']}")
            print(f"      ART{best['art'] or 0}回, 1/{best['prob'] or 999:.0f}, 差枚{best['diff_medals'] or 0:.0f}, 最大{best['max_rensa'] or 0}連")
            
            # 最も差枚が少ない例
            worst = min(data_list, key=lambda x: x['diff_medals'] or 0)
            print(f"    最低: {worst['date']} {worst['store']} #{worst['unit']}")
            print(f"      ART{worst['art'] or 0}回, 1/{worst['prob'] or 999:.0f}, 差枚{worst['diff_medals'] or 0:.0f}, 最大{worst['max_rensa'] or 0}連")
        
        print()
    
    # 高設定 vs 低設定の比較サマリー
    print("=" * 70)
    print("【高設定 vs 低設定 比較サマリー】")
    print("=" * 70)
    
    for machine in ['sbj', 'hokuto2']:
        high_list = results[machine]['high']
        low_list = results[machine]['low']
        
        if not high_list or not low_list:
            continue
        
        print(f"\n■ {machine.upper()}")
        print(f"{'':20} {'高設定':>12} {'低設定':>12} {'差':>10}")
        print("-" * 55)
        
        metrics = [
            ('平均確率', 'prob', lambda x: f"1/{x:.0f}"),
            ('平均差枚', 'diff_medals', lambda x: f"{x:.0f}"),
            ('平均最大ハマり', 'max_hamari', lambda x: f"{x:.0f}G"),
            ('平均最大連チャン', 'max_rensa', lambda x: f"{x:.1f}連"),
            ('500G↑ハマり率', 'big_hamari_count', lambda x: f"{x:.1f}%"),
        ]
        
        for label, key, fmt in metrics:
            if key == 'big_hamari_count':
                high_val = sum(1 for d in high_list if (d['max_hamari'] or 0) >= 500) / len(high_list) * 100
                low_val = sum(1 for d in low_list if (d['max_hamari'] or 0) >= 500) / len(low_list) * 100
            else:
                high_val = sum(d[key] or 0 for d in high_list) / len(high_list)
                low_val = sum(d[key] or 0 for d in low_list) / len(low_list)
            
            diff = high_val - low_val if key != 'prob' else low_val - high_val
            print(f"{label:20} {fmt(high_val):>12} {fmt(low_val):>12} {diff:>+10.1f}")

if __name__ == '__main__':
    results = run_analysis()
    print_results(results)
