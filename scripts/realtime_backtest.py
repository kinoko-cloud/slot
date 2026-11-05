#!/usr/bin/env python3
"""
リアルタイム予測バックテスト

指定時刻（14時、15時、16時、17時、18時）時点で
「高設定っぽいのに出ていない台」を抽出し、
その後の挙動を検証する。
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

DATA_DIR = Path(__file__).parent.parent / 'data' / 'history'

def parse_time(time_str):
    """時刻文字列をdatetimeに変換"""
    try:
        h, m = map(int, time_str.split(':'))
        return h * 60 + m  # 分に変換
    except:
        return None

def analyze_unit_at_time(history, check_hour):
    """
    指定時刻時点での台の状態を分析
    
    Returns:
        dict: {
            'art_count': その時点までのART回数,
            'last_hit_time': 最後の大当たり時刻,
            'minutes_since_last_hit': 最後の大当たりからの経過分,
            'total_medals': その時点までの出玉,
            'is_candidate': 狙い目候補かどうか,
        }
    """
    check_minutes = check_hour * 60  # 14時 = 840分
    
    # その時刻までの大当たりを抽出（履歴は新しい順）
    hits_before = []
    for hit in reversed(history):  # 古い順に処理
        hit_time = parse_time(hit.get('time', ''))
        if hit_time and hit_time <= check_minutes:
            hits_before.append(hit)
    
    if not hits_before:
        return None
    
    art_count = len([h for h in hits_before if h.get('type') == 'ART'])
    last_hit = hits_before[-1]
    last_hit_time = parse_time(last_hit.get('time', ''))
    minutes_since_last = check_minutes - last_hit_time if last_hit_time else 0
    
    total_medals = sum(h.get('medals', 0) for h in hits_before)
    
    # 狙い目候補の条件
    # 1. ART回数が10回以上（稼働している）
    # 2. 最後の大当たりから60分以上経過（放置されている）
    # 3. 確率が1/300以下（高設定っぽい）
    is_candidate = (
        art_count >= 10 and
        minutes_since_last >= 60
    )
    
    return {
        'art_count': art_count,
        'last_hit_time': last_hit.get('time'),
        'minutes_since_last_hit': minutes_since_last,
        'total_medals': total_medals,
        'is_candidate': is_candidate,
    }

def analyze_after_time(history, check_hour):
    """
    指定時刻以降の挙動を分析
    
    Returns:
        dict: {
            'art_after': 以降のART回数,
            'medals_after': 以降の出玉,
            'is_good_result': 良い結果だったか（厳格版）,
            'is_ok_result': まあまあの結果だったか,
        }
    """
    check_minutes = check_hour * 60
    
    hits_after = []
    for hit in history:
        hit_time = parse_time(hit.get('time', ''))
        if hit_time and hit_time > check_minutes:
            hits_after.append(hit)
    
    art_after = len([h for h in hits_after if h.get('type') == 'ART'])
    medals_after = sum(h.get('medals', 0) for h in hits_after)
    
    # 厳格な成功条件：出玉1000枚以上
    is_good_result = medals_after >= 1000
    # まあまあの条件：出玉500枚以上
    is_ok_result = medals_after >= 500
    
    return {
        'art_after': art_after,
        'medals_after': medals_after,
        'is_good_result': is_good_result,
        'is_ok_result': is_ok_result,
    }

def run_backtest():
    """バックテスト実行"""
    results = defaultdict(lambda: {'total': 0, 'hit': 0, 'ok': 0, 'is_good_count': 0, 'candidates': []})
    
    check_hours = [14, 15, 16, 17, 18]
    seen = set()  # 重複排除用
    
    for store_dir in sorted(DATA_DIR.iterdir()):
        if not store_dir.is_dir():
            continue
        
        store_key = store_dir.name
        
        for unit_file in store_dir.glob('*.json'):
            try:
                with open(unit_file) as f:
                    data = json.load(f)
                
                unit_id = data.get('unit_id', unit_file.stem)
                
                for day in data.get('days', []):
                    date = day.get('date')
                    history = day.get('history', [])
                    is_good_day = day.get('is_good', False)
                    final_art = day.get('art', 0)
                    final_prob = day.get('prob', 999)
                    
                    if not history or len(history) < 5:
                        continue
                    
                    for check_hour in check_hours:
                        # 重複チェック
                        unique_key = f"{store_key}_{unit_id}_{date}_{check_hour}"
                        if unique_key in seen:
                            continue
                        seen.add(unique_key)
                        
                        state = analyze_unit_at_time(history, check_hour)
                        if not state or not state['is_candidate']:
                            continue
                        
                        after = analyze_after_time(history, check_hour)
                        
                        key = f"{check_hour}時"
                        results[key]['total'] += 1
                        if after['is_good_result']:
                            results[key]['hit'] += 1
                        if after['is_ok_result']:
                            results[key]['ok'] += 1
                        if is_good_day:
                            results[key]['is_good_count'] += 1
                        
                        results[key]['candidates'].append({
                            'store': store_key,
                            'unit': unit_id,
                            'date': date,
                            'state': state,
                            'after': after,
                            'final_is_good': is_good_day,
                            'final_prob': final_prob,
                        })
            except Exception as e:
                continue
    
    return results

def print_results(results):
    """結果を表示"""
    print("=" * 60)
    print("リアルタイム予測バックテスト結果【厳格版】")
    print("=" * 60)
    print()
    print("条件：ART10回以上 & 最後の大当たりから60分以上経過")
    print("成功：その後出玉1000枚以上")
    print()
    
    for hour in [14, 15, 16, 17, 18]:
        key = f"{hour}時"
        if key not in results:
            continue
        
        r = results[key]
        total = r['total']
        hit = r['hit']
        ok = r.get('ok', 0)
        is_good_count = r.get('is_good_count', 0)
        rate = (hit / total * 100) if total > 0 else 0
        ok_rate = (ok / total * 100) if total > 0 else 0
        is_good_rate = (is_good_count / total * 100) if total > 0 else 0
        
        print(f"【{key}時点】")
        print(f"  候補台数: {total}")
        print(f"  出玉1000枚以上: {hit} ({rate:.1f}%)")
        print(f"  出玉500枚以上: {ok} ({ok_rate:.1f}%)")
        print(f"  最終高設定判定: {is_good_count} ({is_good_rate:.1f}%)")
        print()
    
    # 全体サマリー
    total_all = sum(r['total'] for r in results.values())
    hit_all = sum(r['hit'] for r in results.values())
    ok_all = sum(r.get('ok', 0) for r in results.values())
    is_good_all = sum(r.get('is_good_count', 0) for r in results.values())
    
    rate_all = (hit_all / total_all * 100) if total_all > 0 else 0
    ok_rate_all = (ok_all / total_all * 100) if total_all > 0 else 0
    is_good_rate_all = (is_good_all / total_all * 100) if total_all > 0 else 0
    
    print("=" * 60)
    print(f"【全体】")
    print(f"  候補: {total_all}")
    print(f"  出玉1000枚以上: {hit_all} ({rate_all:.1f}%)")
    print(f"  出玉500枚以上: {ok_all} ({ok_rate_all:.1f}%)")
    print(f"  最終高設定判定: {is_good_all} ({is_good_rate_all:.1f}%)")
    print("=" * 60)

if __name__ == '__main__':
    results = run_backtest()
    print_results(results)
