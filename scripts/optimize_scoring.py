#!/usr/bin/env python3
"""
スコアリング最適化
- 過去データから「最大出玉台を選べる」指標と重みを逆算
"""

import json
import os
import itertools
from collections import defaultdict
from typing import Dict, List, Tuple
import statistics

RENCHAIN_THRESHOLD = 60

def load_history_data(store_machine_path: str) -> Dict:
    """履歴データを読み込む"""
    all_data = {}
    for filename in os.listdir(store_machine_path):
        if filename.endswith('.json'):
            unit_id = filename.replace('.json', '')
            with open(os.path.join(store_machine_path, filename), 'r') as f:
                all_data[unit_id] = json.load(f)
    return all_data

def extract_features(history: List[Dict], target_time: str) -> Dict:
    """指定時刻での特徴量を抽出"""
    target_hour = int(target_time.split(':')[0])
    target_min = int(target_time.split(':')[1]) if ':' in target_time else 0
    
    hits_before = []
    for hit in history:
        hit_time = hit.get('time', '')
        if not hit_time:
            continue
        hit_hour = int(hit_time.split(':')[0])
        hit_min = int(hit_time.split(':')[1]) if ':' in hit_time else 0
        
        if hit_hour < target_hour or (hit_hour == target_hour and hit_min <= target_min):
            hits_before.append(hit)
    
    if len(hits_before) < 3:
        return None
    
    sorted_hits = sorted(hits_before, key=lambda x: x.get('hit_num', 0))
    
    # 基本統計
    total_medals = sum(h.get('medals', 0) for h in hits_before)
    reg_count = sum(1 for h in hits_before if h.get('type') == 'REG')
    art_count = sum(1 for h in hits_before if h.get('type') == 'ART')
    total_hits = len(hits_before)
    
    # 連チャン率
    renchain_hits = sum(1 for h in sorted_hits if h.get('start', 999) <= RENCHAIN_THRESHOLD)
    renchain_rate = renchain_hits / total_hits
    
    # 当たり間G数
    start_values = [h.get('start', 0) for h in sorted_hits]
    avg_start = statistics.mean(start_values) if start_values else 0
    std_start = statistics.stdev(start_values) if len(start_values) > 1 else 0
    
    # 天井到達率（500G以上を天井とみなす）
    ceiling_hits = sum(1 for h in sorted_hits if h.get('start', 0) >= 500)
    ceiling_rate = ceiling_hits / total_hits
    
    # 単発率（連チャンせず終わった回数）
    # 連チャンセッションを計算
    sessions = []
    current_session = 1
    for i, hit in enumerate(sorted_hits):
        if i > 0 and hit.get('start', 999) <= RENCHAIN_THRESHOLD:
            current_session += 1
        else:
            if i > 0:
                sessions.append(current_session)
            current_session = 1
    sessions.append(current_session)
    
    single_rate = sum(1 for s in sessions if s == 1) / len(sessions) if sessions else 0
    avg_session_length = statistics.mean(sessions) if sessions else 0
    max_session_length = max(sessions) if sessions else 0
    
    # REG比率
    reg_rate = reg_count / total_hits
    
    # 平均獲得枚数
    avg_medals = total_medals / total_hits
    
    # 直近5回の連チャン率（勢い）
    recent_hits = sorted_hits[-5:] if len(sorted_hits) >= 5 else sorted_hits
    recent_renchain = sum(1 for h in recent_hits if h.get('start', 999) <= RENCHAIN_THRESHOLD) / len(recent_hits)
    
    return {
        'renchain_rate': renchain_rate,           # 連チャン率
        'avg_medals': avg_medals,                  # 平均獲得枚数
        'reg_rate': reg_rate,                      # REG比率
        'ceiling_rate': ceiling_rate,             # 天井到達率
        'single_rate': single_rate,               # 単発率
        'avg_session_length': avg_session_length, # 平均連チャン長
        'max_session_length': max_session_length, # 最大連チャン長
        'avg_start': avg_start,                   # 平均当たり間G数
        'std_start': std_start,                   # 当たり間G数のばらつき
        'recent_renchain': recent_renchain,       # 直近の連チャン率
        'total_hits': total_hits,                 # 総当たり回数
    }

def score_with_weights(features: Dict, weights: Dict) -> float:
    """重み付きスコア計算"""
    score = 0
    for key, weight in weights.items():
        if key in features:
            score += features[key] * weight
    return score

def evaluate_weights(all_data: Dict, weights: Dict, time_slot: str = '17:00') -> Dict:
    """指定の重みでの精度を評価"""
    
    date_units = defaultdict(dict)
    
    for unit_id, unit_data in all_data.items():
        for day in unit_data.get('days', []):
            history = day.get('history', [])
            if not history:
                continue
            
            date = day.get('date')
            total_medals = sum(h.get('medals', 0) for h in history)
            features = extract_features(history, time_slot)
            
            if features is None:
                continue
            
            date_units[date][unit_id] = {
                'total_medals': total_medals,
                'features': features
            }
    
    results = []
    
    for date, units in date_units.items():
        if len(units) < 3:
            continue
        
        # 各台のスコア計算
        scored_units = []
        for unit_id, info in units.items():
            score = score_with_weights(info['features'], weights)
            scored_units.append({
                'unit_id': unit_id,
                'score': score,
                'total_medals': info['total_medals']
            })
        
        # スコア順にソート
        scored_units.sort(key=lambda x: x['score'], reverse=True)
        
        # 最大出玉台
        best_unit = max(units.items(), key=lambda x: x[1]['total_medals'])
        best_unit_id = best_unit[0]
        avg_medals = statistics.mean(u['total_medals'] for u in units.values())
        
        # 推薦台（スコア1位）
        recommended = scored_units[0]
        
        # 推薦台の出玉順位
        sorted_by_medals = sorted(scored_units, key=lambda x: x['total_medals'], reverse=True)
        rank = next(i+1 for i, u in enumerate(sorted_by_medals) if u['unit_id'] == recommended['unit_id'])
        
        results.append({
            'is_best': recommended['unit_id'] == best_unit_id,
            'is_above_avg': recommended['total_medals'] > avg_medals,
            'rank': rank,
            'total_units': len(units),
            'recommended_medals': recommended['total_medals'],
            'best_medals': best_unit[1]['total_medals']
        })
    
    if not results:
        return {'best_rate': 0, 'above_avg_rate': 0, 'avg_rank': 999}
    
    best_rate = sum(1 for r in results if r['is_best']) / len(results)
    above_avg_rate = sum(1 for r in results if r['is_above_avg']) / len(results)
    avg_rank = statistics.mean(r['rank'] for r in results)
    avg_medals = statistics.mean(r['recommended_medals'] for r in results)
    
    return {
        'best_rate': best_rate,
        'above_avg_rate': above_avg_rate,
        'avg_rank': avg_rank,
        'avg_medals': avg_medals,
        'sample_count': len(results)
    }

def grid_search(all_data: Dict, time_slot: str = '17:00'):
    """グリッドサーチで最適な重みを探索"""
    
    # 探索する重みの範囲
    weight_ranges = {
        'renchain_rate': [0, 50, 100, 150, 200],
        'avg_medals': [0, 0.05, 0.1, 0.2],
        'reg_rate': [-100, -50, -30, 0],
        'ceiling_rate': [-200, -100, -50, 0],
        'single_rate': [-100, -50, 0],
        'avg_session_length': [0, 10, 20, 30],
        'max_session_length': [0, 5, 10],
        'recent_renchain': [0, 30, 50],
    }
    
    print(f"グリッドサーチ開始（時間帯: {time_slot}）")
    print("=" * 60)
    
    best_result = None
    best_weights = None
    best_score = -999
    
    # 重要な指標だけで探索（計算量削減）
    key_weights = {
        'renchain_rate': [50, 100, 150],
        'avg_medals': [0.05, 0.1],
        'reg_rate': [-50, -30, 0],
        'ceiling_rate': [-100, -50, 0],
        'single_rate': [-50, 0],
        'avg_session_length': [10, 20],
    }
    
    combinations = list(itertools.product(*key_weights.values()))
    total = len(combinations)
    
    print(f"探索パターン数: {total}")
    
    for i, combo in enumerate(combinations):
        weights = dict(zip(key_weights.keys(), combo))
        result = evaluate_weights(all_data, weights, time_slot)
        
        # スコア = 最高台的中率 + 平均超え率 - 平均順位/10
        score = result['best_rate'] * 100 + result['above_avg_rate'] * 50 - result['avg_rank'] * 5
        
        if score > best_score:
            best_score = score
            best_result = result
            best_weights = weights
        
        if (i + 1) % 50 == 0:
            print(f"  進捗: {i+1}/{total} - 現在最高スコア: {best_score:.1f}")
    
    return best_weights, best_result

def main():
    base_path = '/home/riichi/works/slot/data/history/island_akihabara_sbj'
    all_data = load_history_data(base_path)
    
    print("=" * 70)
    print("【スコアリング最適化 - アイランド秋葉原 SBJ】")
    print("=" * 70)
    
    # 各時間帯で最適化
    time_slots = ['14:00', '16:00', '18:00', '20:00']
    
    all_best_weights = {}
    
    for time_slot in time_slots:
        print(f"\n■ {time_slot} の最適化")
        print("-" * 50)
        
        best_weights, best_result = grid_search(all_data, time_slot)
        all_best_weights[time_slot] = best_weights
        
        print(f"\n【最適な重み】")
        for key, value in best_weights.items():
            print(f"  {key}: {value}")
        
        print(f"\n【結果】")
        print(f"  最高台的中率: {best_result['best_rate']:.0%}")
        print(f"  平均超え率: {best_result['above_avg_rate']:.0%}")
        print(f"  平均順位: {best_result['avg_rank']:.1f}位")
        print(f"  平均出玉: {best_result['avg_medals']:.0f}枚")
    
    # 全時間帯共通の最適重みを探索
    print("\n" + "=" * 70)
    print("【全時間帯共通の最適化】")
    print("=" * 70)
    
    def evaluate_all_times(weights):
        total_score = 0
        for ts in ['14:00', '15:00', '16:00', '17:00', '18:00', '19:00', '20:00']:
            result = evaluate_weights(all_data, weights, ts)
            total_score += result['best_rate'] * 100 + result['above_avg_rate'] * 50 - result['avg_rank'] * 5
        return total_score
    
    key_weights = {
        'renchain_rate': [50, 100, 150],
        'avg_medals': [0.05, 0.1],
        'reg_rate': [-50, -30, 0],
        'ceiling_rate': [-100, -50, 0],
        'single_rate': [-50, 0],
        'avg_session_length': [10, 20],
    }
    
    combinations = list(itertools.product(*key_weights.values()))
    best_total_score = -999
    best_universal_weights = None
    
    for combo in combinations:
        weights = dict(zip(key_weights.keys(), combo))
        score = evaluate_all_times(weights)
        if score > best_total_score:
            best_total_score = score
            best_universal_weights = weights
    
    print(f"\n【全時間帯で最適な重み】")
    for key, value in best_universal_weights.items():
        print(f"  {key}: {value}")
    
    # 最終検証
    print(f"\n【最適重みでの各時間帯の結果】")
    print("-" * 70)
    print(f"{'時間':<8} {'最高台的中':<12} {'平均超え':<12} {'平均順位':<10} {'平均出玉':<12}")
    print("-" * 70)
    
    for ts in ['14:00', '15:00', '16:00', '17:00', '18:00', '19:00', '20:00']:
        result = evaluate_weights(all_data, best_universal_weights, ts)
        print(f"{ts:<8} {result['best_rate']:>6.0%}        {result['above_avg_rate']:>6.0%}        "
              f"{result['avg_rank']:>5.1f}位     {result['avg_medals']:>8.0f}枚")
    
    # 重みの解釈
    print("\n" + "=" * 70)
    print("【重みの解釈】")
    print("=" * 70)
    
    interpretations = {
        'renchain_rate': '連チャン率 → 高いほど良い（高設定の特徴）',
        'avg_medals': '平均獲得枚数 → 高いほど良い',
        'reg_rate': 'REG比率 → 低いほど良い（マイナス重み）',
        'ceiling_rate': '天井到達率 → 低いほど良い（マイナス重み）',
        'single_rate': '単発率 → 低いほど良い（マイナス重み）',
        'avg_session_length': '平均連チャン長 → 長いほど良い',
    }
    
    for key, value in best_universal_weights.items():
        interp = interpretations.get(key, '')
        impact = "重要" if abs(value) > 50 else "中程度" if abs(value) > 10 else "軽微"
        print(f"  {key}: {value} ({impact})")
        print(f"    → {interp}")
    
    return best_universal_weights

if __name__ == '__main__':
    best_weights = main()
