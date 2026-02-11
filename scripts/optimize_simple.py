#!/usr/bin/env python3
"""
スコアリング最適化 - 簡易版
"""

import json
import os
import statistics
from collections import defaultdict

RENCHAIN_THRESHOLD = 60

def load_all_data(path):
    all_data = {}
    for f in os.listdir(path):
        if f.endswith('.json'):
            with open(os.path.join(path, f)) as fp:
                all_data[f.replace('.json','')] = json.load(fp)
    return all_data

def get_features(history, target_hour):
    hits = [h for h in history if h.get('time') and int(h['time'].split(':')[0]) <= target_hour]
    if len(hits) < 3:
        return None
    
    total = len(hits)
    medals = sum(h.get('medals',0) for h in hits)
    regs = sum(1 for h in hits if h.get('type')=='REG')
    
    renchain = sum(1 for h in hits if h.get('start',999) <= RENCHAIN_THRESHOLD) / total
    ceiling = sum(1 for h in hits if h.get('start',0) >= 500) / total
    
    # セッション計算
    sorted_h = sorted(hits, key=lambda x: x.get('hit_num',0))
    sessions = []
    sess = 1
    for i, h in enumerate(sorted_h):
        if i > 0 and h.get('start',999) <= RENCHAIN_THRESHOLD:
            sess += 1
        else:
            if i > 0:
                sessions.append(sess)
            sess = 1
    sessions.append(sess)
    
    single_rate = sum(1 for s in sessions if s == 1) / len(sessions)
    avg_sess = statistics.mean(sessions)
    
    return {
        'renchain': renchain,
        'avg_medals': medals / total,
        'reg_rate': regs / total,
        'ceiling': ceiling,
        'single': single_rate,
        'avg_sess': avg_sess,
        'hits': total
    }

def score(f, w):
    return (f['renchain'] * w['renchain'] +
            f['avg_medals'] * w['avg_medals'] +
            f['reg_rate'] * w['reg_rate'] +
            f['ceiling'] * w['ceiling'] +
            f['single'] * w['single'] +
            f['avg_sess'] * w['avg_sess'])

def evaluate(all_data, weights, hour):
    results = []
    
    date_units = defaultdict(dict)
    for uid, udata in all_data.items():
        for day in udata.get('days', []):
            hist = day.get('history', [])
            if not hist:
                continue
            date = day['date']
            total_medals = sum(h.get('medals',0) for h in hist)
            feat = get_features(hist, hour)
            if feat:
                date_units[date][uid] = {'medals': total_medals, 'feat': feat}
    
    for date, units in date_units.items():
        if len(units) < 3:
            continue
        
        scored = [(uid, score(info['feat'], weights), info['medals']) 
                  for uid, info in units.items()]
        scored.sort(key=lambda x: x[1], reverse=True)
        
        best_uid = max(units.items(), key=lambda x: x[1]['medals'])[0]
        avg_m = statistics.mean(u['medals'] for u in units.values())
        
        rec = scored[0]
        by_medals = sorted(scored, key=lambda x: x[2], reverse=True)
        rank = next(i+1 for i,(u,_,_) in enumerate(by_medals) if u == rec[0])
        
        results.append({
            'is_best': rec[0] == best_uid,
            'above_avg': rec[2] > avg_m,
            'rank': rank,
            'medals': rec[2]
        })
    
    if not results:
        return 0, 0, 99
    
    best_r = sum(r['is_best'] for r in results) / len(results)
    above_r = sum(r['above_avg'] for r in results) / len(results)
    avg_rank = statistics.mean(r['rank'] for r in results)
    return best_r, above_r, avg_rank

def main():
    path = '/home/riichi/works/slot/data/history/island_akihabara_sbj'
    data = load_all_data(path)
    
    print("=" * 70)
    print("【最適化結果】")
    print("=" * 70)
    
    # 探索
    best_w = None
    best_score = -999
    
    for r in [50, 100, 150]:
        for m in [0.05, 0.1]:
            for reg in [-50, -30, 0]:
                for c in [-100, -50, 0]:
                    for s in [-50, 0]:
                        for a in [10, 20]:
                            w = {'renchain': r, 'avg_medals': m, 'reg_rate': reg,
                                 'ceiling': c, 'single': s, 'avg_sess': a}
                            
                            total = 0
                            for h in [14, 16, 18, 20]:
                                b, ab, rk = evaluate(data, w, h)
                                total += b*100 + ab*50 - rk*5
                            
                            if total > best_score:
                                best_score = total
                                best_w = w
    
    print("\n【最適な重み】")
    for k, v in best_w.items():
        print(f"  {k}: {v}")
    
    print("\n【各時間帯の結果】")
    print("-" * 70)
    print(f"{'時間':<6} {'最高台的中':<12} {'平均超え':<12} {'平均順位':<10}")
    print("-" * 70)
    
    for h in [14, 15, 16, 17, 18, 19, 20]:
        b, ab, rk = evaluate(data, best_w, h)
        print(f"{h}:00  {b:>6.0%}        {ab:>6.0%}        {rk:>5.1f}位")
    
    # 各指標の重要度を検証
    print("\n【各指標の重要度検証】")
    print("-" * 70)
    
    base_w = best_w.copy()
    for key in best_w:
        test_w = base_w.copy()
        test_w[key] = 0  # この指標を無効化
        
        total_base = 0
        total_test = 0
        for h in [14, 16, 18, 20]:
            b1, ab1, rk1 = evaluate(data, base_w, h)
            b2, ab2, rk2 = evaluate(data, test_w, h)
            total_base += b1*100 + ab1*50 - rk1*5
            total_test += b2*100 + ab2*50 - rk2*5
        
        impact = total_base - total_test
        importance = "★★★重要" if impact > 30 else "★★中程度" if impact > 10 else "★軽微"
        print(f"  {key}: 影響度 {impact:+.1f} ({importance})")

if __name__ == '__main__':
    main()
