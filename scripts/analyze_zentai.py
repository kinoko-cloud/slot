#!/usr/bin/env python3
"""全台系イベント分析スクリプト"""
import json, sys, glob, os
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from config.rankings import MACHINES, STORES

HISTORY_DIR = PROJECT_ROOT / 'data' / 'history'
ANALYSIS_DIR = PROJECT_ROOT / 'data' / 'analysis'

def get_good_threshold(mk): return MACHINES.get(mk, {}).get('good_prob', 130)

def is_good_day(d, mk):
    art, games = d.get('art', 0), d.get('games', 0) or d.get('total_start', 0)
    return art > 0 and games > 0 and (games / art) <= get_good_threshold(mk)

def detect_zentai_events():
    store_date_units = defaultdict(lambda: defaultdict(list))
    for store_dir in HISTORY_DIR.iterdir():
        if not store_dir.is_dir() or 'backup' in store_dir.name: continue
        sk = store_dir.name
        mk = 'sbj' if 'sbj' in sk else ('hokuto_tensei2' if 'hokuto' in sk or 'tensei' in sk else None)
        if not mk: continue
        for uf in store_dir.glob('*.json'):
            try:
                ud = json.load(open(uf))
                uid = ud.get('unit_id', uf.stem)
                for d in ud.get('days', []):
                    if d.get('date'):
                        d['unit_id'], d['machine_key'] = uid, mk
                        store_date_units[sk][d['date']].append(d)
            except: pass
    
    events = []
    for sk, du in store_date_units.items():
        mk = 'sbj' if 'sbj' in sk else 'hokuto_tensei2'
        for dt, units in du.items():
            if len(units) < 3: continue
            good_cnt = sum(1 for u in units if is_good_day(u, mk))
            rate = good_cnt / len(units)
            if rate >= 0.8:
                probs = [u['total_start']/u['art'] for u in units if u.get('art',0)>0 and u.get('total_start',0)>0]
                events.append({
                    'store_key': sk, 'machine_key': mk, 'date': dt,
                    'weekday_jp': ['月','火','水','木','金','土','日'][datetime.strptime(dt,'%Y-%m-%d').weekday()],
                    'total_units': len(units), 'good_units': good_cnt,
                    'good_rate': round(rate*100,1), 'avg_prob': round(sum(probs)/len(probs),1) if probs else 0,
                    'unit_ids': [u['unit_id'] for u in units if is_good_day(u, mk)],
                })
    return {'events': sorted(events, key=lambda x: (x['store_key'], x['date'])), 'generated_at': datetime.now().isoformat()}

def analyze_patterns(events_data):
    events = events_data.get('events', [])
    store_events = defaultdict(list)
    for e in events: store_events[e['store_key']].append(e)
    
    patterns = {}
    for sk, evts in store_events.items():
        evts = sorted(evts, key=lambda x: x['date'])
        intervals = [(datetime.strptime(evts[i]['date'],'%Y-%m-%d')-datetime.strptime(evts[i-1]['date'],'%Y-%m-%d')).days for i in range(1, len(evts))]
        wd_dist = defaultdict(int)
        for e in evts: wd_dist[e['weekday_jp']] += 1
        unit_freq = defaultdict(int)
        for e in evts:
            for uid in e.get('unit_ids', []): unit_freq[uid] += 1
        patterns[sk] = {
            'event_count': len(evts), 'dates': [e['date'] for e in evts],
            'avg_interval_days': round(sum(intervals)/len(intervals),1) if intervals else None,
            'weekday_distribution': dict(wd_dist),
            'hot_units': sorted(unit_freq.items(), key=lambda x: -x[1])[:10],
            'last_event': evts[-1]['date'] if evts else None,
        }
    return {'patterns': patterns, 'generated_at': datetime.now().isoformat()}

def predict_next(patterns):
    predictions = {}
    for sk, p in patterns.get('patterns', {}).items():
        if p.get('avg_interval_days') and p.get('last_event'):
            last = datetime.strptime(p['last_event'], '%Y-%m-%d')
            pred = last + timedelta(days=p['avg_interval_days'])
            wd = max(p.get('weekday_distribution', {}).items(), key=lambda x: x[1])[0] if p.get('weekday_distribution') else None
            predictions[sk] = {
                'predicted_date': pred.strftime('%Y-%m-%d'),
                'avg_interval_days': p['avg_interval_days'],
                'most_common_weekday': wd,
                'hot_units': p.get('hot_units', [])[:5],
                'confidence': 'low' if p['event_count']<3 else ('medium' if p['event_count']<5 else 'high'),
            }
    return {'predictions': predictions, 'generated_at': datetime.now().isoformat()}

def main():
    print("🎰 全台系イベント分析")
    events = detect_zentai_events()
    print(f"  → {len(events['events'])}件検出")
    patterns = analyze_patterns(events)
    predictions = predict_next(patterns)
    
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    json.dump(events, open(ANALYSIS_DIR/'zentai_events.json','w'), ensure_ascii=False, indent=2)
    json.dump(patterns, open(ANALYSIS_DIR/'zentai_patterns.json','w'), ensure_ascii=False, indent=2)
    json.dump(predictions, open(ANALYSIS_DIR/'zentai_predictions.json','w'), ensure_ascii=False, indent=2)
    print(f"✅ 保存完了: {ANALYSIS_DIR}")
    
    for e in events['events']:
        sn = STORES.get(e['store_key'],{}).get('short_name', e['store_key'])
        print(f"  {e['date']}({e['weekday_jp']}) {sn}: {e['good_units']}/{e['total_units']}台 1/{e['avg_prob']:.0f}")

if __name__ == '__main__': main()
