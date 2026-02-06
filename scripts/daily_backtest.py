#!/usr/bin/env python3
"""毎日深夜に実行：バックテスト→パターン更新→精度改善"""
import json
from datetime import datetime
from pathlib import Path
from collections import defaultdict
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.rankings import get_machine_threshold

HISTORY_DIR = Path(__file__).parent.parent / 'data' / 'history'
ANALYSIS_DIR = Path(__file__).parent.parent / 'data' / 'analysis'
ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = ANALYSIS_DIR / 'backtest_log.json'

def load_all_data(store_key):
    store_dir = HISTORY_DIR / store_key
    if not store_dir.exists():
        return {}
    data = defaultdict(dict)
    for f in store_dir.glob('*.json'):
        try:
            d = json.load(open(f))
            uid = str(d.get('unit_id', f.stem))
            for day in d.get('days', []):
                date = day.get('date')
                if date:
                    data[uid][date] = day
        except:
            pass
    return data

def is_good(day_data, machine_key):
    art = day_data.get('art', 0)
    games = day_data.get('games', 0) or day_data.get('total_start', 0)
    if art <= 0 or games < 500:
        return None
    good_prob = get_machine_threshold(machine_key, 'good_prob')
    return (games / art) <= good_prob

def analyze_store_patterns(store_key, mk):
    data = load_all_data(store_key)
    if not data:
        return None
    
    all_dates = set()
    for uid, days in data.items():
        all_dates.update(days.keys())
    dates = sorted(all_dates)
    if len(dates) < 5:
        return None
    
    uids = sorted(data.keys())
    n = len(uids)
    
    r = {'store_key': store_key, 'total_units': n, 'total_days': len(dates),
         'prev_good_next_good': 0, 'prev_good_next_bad': 0, 'prev_bad_next_good': 0, 'prev_bad_next_bad': 0,
         'consec2_continue': 0, 'consec2_break': 0, 'consec2bad_good': 0, 'consec2bad_bad': 0,
         'weekday': {i: {'good': 0, 'total': 0} for i in range(7)},
         'position': {'front': {'good': 0, 'total': 0}, 'center': {'good': 0, 'total': 0}, 'back': {'good': 0, 'total': 0}},
         'unit_rates': {}}
    
    for uid in uids:
        gc, tc = 0, 0
        for date, day in data[uid].items():
            g = is_good(day, mk)
            if g is not None:
                tc += 1
                if g: gc += 1
        if tc >= 5:
            r['unit_rates'][uid] = gc / tc
    
    for uid in uids:
        idx = uids.index(uid)
        pos = 'front' if idx < n*0.2 else ('back' if idx >= n*0.8 else 'center')
        
        for i, date in enumerate(dates[:-1]):
            if date not in data[uid]: continue
            next_date = dates[i+1]
            if next_date not in data[uid]: continue
            
            pg = is_good(data[uid][date], mk)
            ng = is_good(data[uid][next_date], mk)
            if pg is None or ng is None: continue
            
            if pg and ng: r['prev_good_next_good'] += 1
            elif pg and not ng: r['prev_good_next_bad'] += 1
            elif not pg and ng: r['prev_bad_next_good'] += 1
            else: r['prev_bad_next_bad'] += 1
            
            if i >= 1 and dates[i-1] in data[uid]:
                p2g = is_good(data[uid][dates[i-1]], mk)
                if p2g is not None:
                    if p2g and pg:
                        if ng: r['consec2_continue'] += 1
                        else: r['consec2_break'] += 1
                    elif not p2g and not pg:
                        if ng: r['consec2bad_good'] += 1
                        else: r['consec2bad_bad'] += 1
            
            wd = datetime.strptime(next_date, '%Y-%m-%d').weekday()
            r['weekday'][wd]['total'] += 1
            if ng: r['weekday'][wd]['good'] += 1
            r['position'][pos]['total'] += 1
            if ng: r['position'][pos]['good'] += 1
    
    return r

def backtest_store(store_key, mk, patterns):
    data = load_all_data(store_key)
    if not data: return None
    
    all_dates = set()
    for uid, days in data.items():
        all_dates.update(days.keys())
    dates = sorted(all_dates)
    if len(dates) < 5: return None
    
    sp = patterns.get(store_key, {})
    uids = sorted(data.keys())
    n = len(uids)
    use_sp = sp.get('total_days', 0) >= 50
    
    res = {'pred': 0, 'hits': 0, 'actual': 0}
    
    for i in range(2, len(dates)-1):
        date = dates[i]
        next_date = dates[i+1]
        
        pgc = 0
        prev_res = {}
        for uid in uids:
            if date in data[uid]:
                g = is_good(data[uid][date], mk)
                if g is not None:
                    prev_res[uid] = g
                    if g: pgc += 1
        
        scores = {}
        for j, uid in enumerate(uids):
            sc = 0
            if uid in prev_res:
                if prev_res[uid]:
                    sc += 25
                    if i >= 1 and dates[i-1] in data[uid]:
                        if is_good(data[uid][dates[i-1]], mk):
                            sc += 10
                            if i >= 2 and dates[i-2] in data[uid]:
                                if is_good(data[uid][dates[i-2]], mk): sc -= 5
                else:
                    sc += 10
                    if i >= 1 and dates[i-1] in data[uid]:
                        if not is_good(data[uid][dates[i-1]], mk): sc += 15
            
            pr = j / n
            if use_sp:
                pd = sp.get('position', {})
                rates = {p: pd[p]['good']/pd[p]['total'] for p in ['front','center','back'] if pd.get(p,{}).get('total',0)>=5}
                if rates:
                    pos = 'front' if pr<0.2 else ('back' if pr>=0.8 else 'center')
                    avg = sum(rates.values())/len(rates)
                    sc += int((rates.get(pos,avg)-avg)*25)
            else:
                sc += 8 if 0.2<=pr<=0.8 else -5
            
            sc += -10 if pgc>=12 else (5 if pgc<=6 else 0)
            wd = datetime.strptime(next_date,'%Y-%m-%d').weekday()
            sc += 8 if wd in (5,6) else (-5 if wd==0 else 0)
            scores[uid] = sc
        
        quota = max(2, pgc)
        pred = set(uid for uid,_ in sorted(scores.items(), key=lambda x:-x[1])[:quota])
        actual = {uid for uid in data if next_date in data[uid] and is_good(data[uid][next_date], mk)}
        
        if actual:
            res['pred'] += len(pred)
            res['hits'] += len(pred & actual)
            res['actual'] += len(actual)
    
    return res

def main():
    print(f"[{datetime.now().isoformat()}] 日次バックテスト開始")
    
    stores = []
    for sd in HISTORY_DIR.iterdir():
        if sd.is_dir() and 'backup' not in sd.name:
            sk = sd.name
            stores.append((sk, 'sbj' if 'sbj' in sk else 'hokuto2'))
    
    patterns = {}
    for sk, mk in stores:
        p = analyze_store_patterns(sk, mk)
        if p: patterns[sk] = p
    
    with open(ANALYSIS_DIR/'store_patterns.json','w') as f:
        json.dump({'stores':patterns,'generated_at':datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)
    print(f"  パターン更新: {len(patterns)}店舗")
    
    total = {'pred':0,'hits':0,'actual':0}
    sr = {}
    for sk, mk in stores:
        r = backtest_store(sk, mk, patterns)
        if r and r['pred']>0:
            sr[sk] = r
            total['pred']+=r['pred']; total['hits']+=r['hits']; total['actual']+=r['actual']
    
    acc = total['hits']/total['pred']*100 if total['pred']>0 else 0
    cov = total['hits']/total['actual']*100 if total['actual']>0 else 0
    print(f"  精度: {acc:.1f}% / カバー: {cov:.1f}%")
    
    entry = {'date':datetime.now().strftime('%Y-%m-%d'),'accuracy':round(acc,2),'coverage':round(cov,2),
             'total_pred':total['pred'],'total_hits':total['hits'],
             'stores':{sk:{'acc':round(r['hits']/r['pred']*100,1) if r['pred']>0 else 0} for sk,r in sr.items()}}
    
    logs = []
    if LOG_FILE.exists():
        try: logs = json.load(open(LOG_FILE))
        except: pass
    logs.append(entry)
    logs = logs[-30:]
    with open(LOG_FILE,'w') as f: json.dump(logs, f, ensure_ascii=False, indent=2)
    print(f"[{datetime.now().isoformat()}] 完了")

if __name__ == '__main__':
    main()
