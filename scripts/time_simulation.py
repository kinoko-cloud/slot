#!/usr/bin/env python3
"""
時間帯別シミュレーション
- 各時間帯で「最も勝てそうな台」を推薦
- 実際の結果と比較して勝率を算出
"""

import json
import os
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

# 連チャン判定の閾値（60G以下は連チャン中）
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

def get_state_at_time(history: List[Dict], target_time: str, target_date: str) -> Dict:
    """
    指定時刻での台の状態を再構築
    
    Returns:
        - total_hits: その時点までの総当たり回数
        - total_medals: その時点までの総獲得枚数
        - renchain_count: 連チャン回数（60G以下の連続）
        - renchain_rate: 連チャン率
        - last_hit_time: 最後の当たり時刻
        - current_hama: 現在のハマりG数（推定）
        - reg_count: REG回数
        - art_count: ART回数
    """
    target_hour = int(target_time.split(':')[0])
    target_min = int(target_time.split(':')[1]) if ':' in target_time else 0
    
    hits_before = []
    for hit in history:
        hit_time = hit.get('time', '')
        if not hit_time:
            continue
        hit_hour = int(hit_time.split(':')[0])
        hit_min = int(hit_time.split(':')[1]) if ':' in hit_time else 0
        
        # 指定時刻より前の当たりのみ
        if hit_hour < target_hour or (hit_hour == target_hour and hit_min <= target_min):
            hits_before.append(hit)
    
    if not hits_before:
        return {
            'total_hits': 0,
            'total_medals': 0,
            'renchain_count': 0,
            'renchain_rate': 0,
            'last_hit_time': None,
            'current_hama': 0,
            'reg_count': 0,
            'art_count': 0,
            'avg_medals_per_hit': 0
        }
    
    # 連チャン計算（60G以下で連続）
    renchain_sessions = []
    current_session = 1
    
    # historyは新しい順なので逆順で処理
    sorted_hits = sorted(hits_before, key=lambda x: x.get('hit_num', 0))
    
    for i, hit in enumerate(sorted_hits):
        start_g = hit.get('start', 0)
        if i > 0 and start_g <= RENCHAIN_THRESHOLD:
            current_session += 1
        else:
            if current_session > 1:
                renchain_sessions.append(current_session)
            current_session = 1
    if current_session > 1:
        renchain_sessions.append(current_session)
    
    total_medals = sum(h.get('medals', 0) for h in hits_before)
    reg_count = sum(1 for h in hits_before if h.get('type') == 'REG')
    art_count = sum(1 for h in hits_before if h.get('type') == 'ART')
    
    # 連チャン率 = 60G以下で当たった回数 / 総当たり回数
    renchain_hits = sum(1 for h in sorted_hits if h.get('start', 999) <= RENCHAIN_THRESHOLD)
    renchain_rate = renchain_hits / len(hits_before) if hits_before else 0
    
    # 最後の当たりからの推定ハマりG数
    last_hit = max(hits_before, key=lambda x: (int(x.get('time', '00:00').split(':')[0]), 
                                                int(x.get('time', '00:00').split(':')[1]) if ':' in x.get('time', '00:00') else 0))
    last_time = last_hit.get('time', '')
    
    return {
        'total_hits': len(hits_before),
        'total_medals': total_medals,
        'renchain_count': len(renchain_sessions),
        'renchain_rate': renchain_rate,
        'last_hit_time': last_time,
        'reg_count': reg_count,
        'art_count': art_count,
        'avg_medals_per_hit': total_medals / len(hits_before) if hits_before else 0
    }

def get_final_result(history: List[Dict]) -> Dict:
    """最終結果を取得"""
    total_medals = sum(h.get('medals', 0) for h in history)
    total_hits = len(history)
    reg_count = sum(1 for h in history if h.get('type') == 'REG')
    art_count = sum(1 for h in history if h.get('type') == 'ART')
    
    return {
        'total_hits': total_hits,
        'total_medals': total_medals,
        'reg_count': reg_count,
        'art_count': art_count
    }

def score_machine(state: Dict) -> float:
    """
    台の「良さそう度」をスコア化
    高いほど高設定の可能性が高い
    """
    if state['total_hits'] < 3:
        return -999  # データ不足
    
    score = 0
    
    # 連チャン率が高いほど高評価
    score += state['renchain_rate'] * 100
    
    # 平均獲得枚数が多いほど高評価
    score += state['avg_medals_per_hit'] / 10
    
    # REG比率が低いほど高評価（AT機の場合）
    if state['total_hits'] > 0:
        reg_ratio = state['reg_count'] / state['total_hits']
        score -= reg_ratio * 30
    
    return score

def simulate_time_slot(all_data: Dict, target_date: str, target_time: str) -> Optional[Dict]:
    """
    指定時刻でのシミュレーション
    最も良さそうな台を推薦し、最終結果と比較
    """
    candidates = []
    
    for unit_id, unit_data in all_data.items():
        for day in unit_data.get('days', []):
            if day.get('date') != target_date:
                continue
            
            history = day.get('history', [])
            if not history:
                continue
            
            # 指定時刻での状態
            state = get_state_at_time(history, target_time, target_date)
            if state['total_hits'] < 3:
                continue  # データ不足はスキップ
            
            # 最終結果
            final = get_final_result(history)
            
            # スコア計算
            score = score_machine(state)
            
            candidates.append({
                'unit_id': unit_id,
                'state': state,
                'final': final,
                'score': score,
                # 指定時刻以降の獲得枚数（座った場合の結果）
                'remaining_medals': final['total_medals'] - state['total_medals']
            })
    
    if not candidates:
        return None
    
    # スコア順にソート
    candidates.sort(key=lambda x: x['score'], reverse=True)
    
    # 最も良さそうな台を推薦
    recommended = candidates[0]
    
    return {
        'target_time': target_time,
        'target_date': target_date,
        'recommended': recommended,
        'all_candidates': candidates
    }

def run_simulation(store_machine_path: str, dates: List[str] = None):
    """シミュレーション実行"""
    all_data = load_history_data(store_machine_path)
    
    # 日付を取得
    if dates is None:
        dates = set()
        for unit_data in all_data.values():
            for day in unit_data.get('days', []):
                if day.get('history'):  # 履歴があるもののみ
                    dates.add(day.get('date'))
        dates = sorted(dates)
    
    time_slots = ['14:00', '15:00', '16:00', '17:00', '18:00', '19:00', '20:00']
    
    results = defaultdict(list)
    
    print("=" * 60)
    print("【アイランド秋葉原 SBJ シミュレーション結果】")
    print("=" * 60)
    
    for date in dates:
        print(f"\n■ {date}")
        print("-" * 40)
        
        for time_slot in time_slots:
            result = simulate_time_slot(all_data, date, time_slot)
            
            if result is None:
                print(f"  {time_slot}: データなし")
                continue
            
            rec = result['recommended']
            remaining = rec['remaining_medals']
            win = remaining > 0
            
            results[time_slot].append({
                'date': date,
                'unit_id': rec['unit_id'],
                'remaining_medals': remaining,
                'win': win,
                'score': rec['score'],
                'state': rec['state'],
                'final': rec['final']
            })
            
            status = "✅勝ち" if win else "❌負け"
            print(f"  {time_slot}: 推薦→{rec['unit_id']}番台 → "
                  f"結果{remaining:+}枚 {status} "
                  f"(スコア:{rec['score']:.1f}, 連チャン率:{rec['state']['renchain_rate']:.1%})")
    
    # 時間帯別サマリー
    print("\n" + "=" * 60)
    print("【時間帯別勝率サマリー】")
    print("=" * 60)
    
    for time_slot in time_slots:
        slot_results = results[time_slot]
        if not slot_results:
            continue
        
        wins = sum(1 for r in slot_results if r['win'])
        total = len(slot_results)
        win_rate = wins / total if total > 0 else 0
        avg_medals = sum(r['remaining_medals'] for r in slot_results) / total if total > 0 else 0
        
        print(f"{time_slot}: 勝率 {win_rate:.0%} ({wins}/{total}) | 平均{avg_medals:+.0f}枚")
    
    return results

if __name__ == '__main__':
    import sys
    
    # デフォルトパス
    base_path = '/home/riichi/works/slot/data/history/island_akihabara_sbj'
    
    if len(sys.argv) > 1:
        base_path = sys.argv[1]
    
    results = run_simulation(base_path)
