#!/usr/bin/env python3
"""
時間帯別シミュレーション v2
- 最大出玉ベースで勝敗判定
"""

import json
import os
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Optional

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

def get_state_at_time(history: List[Dict], target_time: str) -> Dict:
    """指定時刻での台の状態を再構築"""
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
    
    if not hits_before:
        return {
            'total_hits': 0,
            'total_medals': 0,
            'renchain_rate': 0,
            'reg_count': 0,
            'art_count': 0,
            'avg_medals_per_hit': 0
        }
    
    sorted_hits = sorted(hits_before, key=lambda x: x.get('hit_num', 0))
    
    total_medals = sum(h.get('medals', 0) for h in hits_before)
    reg_count = sum(1 for h in hits_before if h.get('type') == 'REG')
    art_count = sum(1 for h in hits_before if h.get('type') == 'ART')
    
    renchain_hits = sum(1 for h in sorted_hits if h.get('start', 999) <= RENCHAIN_THRESHOLD)
    renchain_rate = renchain_hits / len(hits_before) if hits_before else 0
    
    return {
        'total_hits': len(hits_before),
        'total_medals': total_medals,
        'renchain_rate': renchain_rate,
        'reg_count': reg_count,
        'art_count': art_count,
        'avg_medals_per_hit': total_medals / len(hits_before) if hits_before else 0
    }

def score_machine(state: Dict) -> float:
    """台の「良さそう度」をスコア化"""
    if state['total_hits'] < 3:
        return -999
    
    score = 0
    score += state['renchain_rate'] * 100
    score += state['avg_medals_per_hit'] / 10
    
    if state['total_hits'] > 0:
        reg_ratio = state['reg_count'] / state['total_hits']
        score -= reg_ratio * 30
    
    return score

def run_simulation(store_machine_path: str):
    """シミュレーション実行 - 最大出玉ベース"""
    all_data = load_history_data(store_machine_path)
    
    # 日付と最大出玉を取得
    date_units = defaultdict(dict)  # date -> {unit_id: max_medals}
    
    for unit_id, unit_data in all_data.items():
        for day in unit_data.get('days', []):
            history = day.get('history', [])
            if not history:
                continue
            
            date = day.get('date')
            total_medals = sum(h.get('medals', 0) for h in history)
            date_units[date][unit_id] = {
                'total_medals': total_medals,
                'history': history,
                'total_hits': len(history)
            }
    
    time_slots = ['14:00', '15:00', '16:00', '17:00', '18:00', '19:00', '20:00']
    
    results = defaultdict(list)
    
    print("=" * 70)
    print("【アイランド秋葉原 SBJ シミュレーション - 最大出玉ベース】")
    print("=" * 70)
    
    for date in sorted(date_units.keys()):
        units = date_units[date]
        if len(units) < 3:
            continue
        
        # その日の最大出玉台を特定
        best_unit = max(units.items(), key=lambda x: x[1]['total_medals'])
        best_unit_id = best_unit[0]
        best_medals = best_unit[1]['total_medals']
        
        # 全台の平均出玉
        avg_medals = sum(u['total_medals'] for u in units.values()) / len(units)
        
        print(f"\n■ {date} (全{len(units)}台, 最高:{best_unit_id}→{best_medals}枚, 平均:{avg_medals:.0f}枚)")
        print("-" * 50)
        
        for time_slot in time_slots:
            candidates = []
            
            for unit_id, unit_info in units.items():
                history = unit_info['history']
                state = get_state_at_time(history, time_slot)
                
                if state['total_hits'] < 3:
                    continue
                
                score = score_machine(state)
                
                candidates.append({
                    'unit_id': unit_id,
                    'state': state,
                    'final_medals': unit_info['total_medals'],
                    'score': score
                })
            
            if not candidates:
                print(f"  {time_slot}: データ不足")
                continue
            
            candidates.sort(key=lambda x: x['score'], reverse=True)
            recommended = candidates[0]
            
            # 勝敗判定: 推薦台が最大出玉台と一致 or 平均以上
            is_best = recommended['unit_id'] == best_unit_id
            is_above_avg = recommended['final_medals'] > avg_medals
            
            # 推薦台の順位
            sorted_by_medals = sorted(candidates, key=lambda x: x['final_medals'], reverse=True)
            rank = next(i+1 for i, c in enumerate(sorted_by_medals) if c['unit_id'] == recommended['unit_id'])
            
            results[time_slot].append({
                'date': date,
                'recommended_unit': recommended['unit_id'],
                'recommended_medals': recommended['final_medals'],
                'best_unit': best_unit_id,
                'best_medals': best_medals,
                'avg_medals': avg_medals,
                'is_best': is_best,
                'is_above_avg': is_above_avg,
                'rank': rank,
                'total_units': len(candidates)
            })
            
            status = "🏆最高" if is_best else ("✅平均超" if is_above_avg else "❌平均以下")
            print(f"  {time_slot}: 推薦→{recommended['unit_id']} ({recommended['final_medals']}枚) "
                  f"{rank}位/{len(candidates)}台 {status}")
    
    # サマリー
    print("\n" + "=" * 70)
    print("【時間帯別サマリー】")
    print("=" * 70)
    print(f"{'時間':<8} {'最高台的中':<12} {'平均超え率':<12} {'平均順位':<10} {'平均出玉':<12}")
    print("-" * 70)
    
    for time_slot in time_slots:
        slot_results = results[time_slot]
        if not slot_results:
            continue
        
        best_hits = sum(1 for r in slot_results if r['is_best'])
        above_avg = sum(1 for r in slot_results if r['is_above_avg'])
        total = len(slot_results)
        
        avg_rank = sum(r['rank'] for r in slot_results) / total
        avg_medals = sum(r['recommended_medals'] for r in slot_results) / total
        
        best_rate = best_hits / total if total > 0 else 0
        above_avg_rate = above_avg / total if total > 0 else 0
        
        print(f"{time_slot:<8} {best_rate:>6.0%} ({best_hits}/{total})  "
              f"{above_avg_rate:>6.0%} ({above_avg}/{total})  "
              f"{avg_rank:>6.1f}位     {avg_medals:>8.0f}枚")
    
    return results

if __name__ == '__main__':
    import sys
    base_path = '/home/riichi/works/slot/data/history/island_akihabara_sbj'
    if len(sys.argv) > 1:
        base_path = sys.argv[1]
    
    results = run_simulation(base_path)
