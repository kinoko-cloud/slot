#!/usr/bin/env python3
"""毎日の的中結果(verify)を自動生成するスクリプト

閉店後に実行して、当日の予測vs実績を検証する。
Usage: python scripts/generate_verify.py [--date 2026-01-28]
"""
import json
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo('Asia/Tokyo')
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.rankings import STORES, MACHINES
from analysis import recommender
from scripts.backtest import load_all_daily_data, build_filtered_daily_data, load_availability_data, get_actual_for_date
import analysis.recommender


def generate_verify(predict_date: str) -> dict:
    """backtestロジックでverify結果を生成"""
    predict_dt = datetime.strptime(predict_date, '%Y-%m-%d')
    prev_date = (predict_dt - timedelta(days=1)).strftime('%Y-%m-%d')
    
    # データロード
    all_data = load_all_daily_data()
    avail_data, avail_date = load_availability_data()
    actuals = get_actual_for_date(all_data, avail_data, avail_date, predict_date)
    
    # monkeypatch: predict_date前日までのデータで予測
    original_load = recommender.load_daily_data
    filtered = build_filtered_daily_data(all_data, predict_date, all_data)
    
    def patched_load(date_str=None, store_key=None, days=7, machine_key=None, end_date=None):
        return filtered
    
    recommender.load_daily_data = patched_load
    orig_dt = analysis.recommender.datetime
    
    class MockDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return predict_dt.replace(hour=9, minute=0, tzinfo=tz)
    
    analysis.recommender.datetime = MockDT
    
    try:
        results = []
        target = [sk for sk in STORES if sk not in ('island_akihabara', 'shibuya_espass', 'shinjuku_espass')]
        for sk in target:
            store = STORES.get(sk)
            if not store:
                continue
            mk = store.get('machine', 'sbj')
            sn = store.get('short_name', store.get('name', sk))
            preds = recommender.recommend_units(sk)
            sa = actuals.get(sk, {})
            for p in preds:
                uid = str(p.get('unit_id', ''))
                a = sa.get(uid, {})
                results.append({
                    'sk': sk, 'sn': sn, 'mk': mk, 'uid': uid,
                    'rank': p.get('final_rank', 'C'),
                    'score': p.get('final_score', 50),
                    'art': a.get('art', 0),
                    'games': a.get('total_start', 0),
                    'prob': a.get('actual_prob', 0),
                    'mm': a.get('max_medals', 0),
                    'dm': a.get('diff_medals', 0),
                })
    finally:
        recommender.load_daily_data = original_load
        analysis.recommender.datetime = orig_dt
    
    # verify形式に変換
    v = {
        'date': prev_date,
        'prediction_date': predict_date,
        'generated_at': datetime.now(JST).isoformat(),
        'total_sa': 0, 'total_hit': 0, 'overall_rate': 0,
        'stores': {}, 'units': {}
    }
    
    for r in results:
        sk, mk = r['sk'], r['mk']
        th = 145 if mk == 'sbj' else 100
        prob = r['prob']
        good = prob > 0 and prob <= th
        
        if sk not in v['units']:
            v['units'][sk] = []
            v['stores'][sk] = {'name': r['sn'], 'sa_total': 0, 'sa_hit': 0, 'rate': 0, 'surprise': 0}
        
        v['units'][sk].append({
            'unit_id': r['uid'],
            'predicted_rank': r['rank'],
            'predicted_score': r['score'],
            'actual_art': r['art'],
            'actual_games': r['games'],
            'actual_prob': prob,
            'actual_is_good': good,
            'max_medals': r['mm'],
            'diff_medals': r['dm'],
        })
    
    # 集計
    for sk, units in v['units'].items():
        valid = [u for u in units if u['actual_prob'] > 0 and u['actual_games'] >= 500]
        sa = sum(1 for u in valid if u['predicted_rank'] in ('S', 'A'))
        hit = sum(1 for u in valid if u['predicted_rank'] in ('S', 'A') and u['actual_is_good'])
        sur = sum(1 for u in valid if u['predicted_rank'] not in ('S', 'A') and u['actual_is_good'])
        v['stores'][sk].update({
            'sa_total': sa, 'sa_hit': hit,
            'rate': round(hit / sa * 100, 1) if sa > 0 else 0,
            'surprise': sur
        })
        v['total_sa'] += sa
        v['total_hit'] += hit
    
    if v['total_sa'] > 0:
        v['overall_rate'] = round(v['total_hit'] / v['total_sa'] * 100, 1)
    
    return v


def main():
    parser = argparse.ArgumentParser(description='的中結果を自動生成')
    parser.add_argument('--date', help='検証対象日（YYYY-MM-DD）。省略時は当日')
    args = parser.parse_args()
    
    if args.date:
        target = args.date
    else:
        target = datetime.now(JST).strftime('%Y-%m-%d')
    
    print(f"=== {target} の的中結果を生成 ===")
    v = generate_verify(target)
    
    # 保存
    date_str = target.replace('-', '')
    output = PROJECT_ROOT / 'data' / 'verify' / f'verify_{date_str}_results.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    json.dump(v, open(output, 'w'), ensure_ascii=False, indent=2)
    
    print(f"保存: {output}")
    print(f"的中率: {v['total_hit']}/{v['total_sa']} = {v['overall_rate']}%")
    for sk in sorted(v['stores']):
        s = v['stores'][sk]
        print(f"  {s['name']}: {s['sa_hit']}/{s['sa_total']} ({s['rate']}%)")


if __name__ == '__main__':
    main()
