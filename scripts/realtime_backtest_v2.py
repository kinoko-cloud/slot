#!/usr/bin/env python3
"""
リアルタイム予測バックテスト v2

「設定は良さそうだけど出玉が伴っていない台」を狙う
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

def analyze_unit_at_time(history, games_at_check, check_hour):
    """
    指定時刻時点での台の状態を詳細分析
    """
    check_minutes = check_hour * 60
    
    # その時刻までの大当たりを抽出
    hits_before = []
    for hit in reversed(history):
        hit_time = parse_time(hit.get('time', ''))
        if hit_time and hit_time <= check_minutes:
            hits_before.append(hit)
    
    if len(hits_before) < 5:
        return None
    
    art_count = len([h for h in hits_before if h.get('type') == 'ART'])
    if art_count < 5:
        return None
    
    # 初当たり確率を推定（ゲーム数は概算）
    # 営業開始10時から現在までの時間（分）× 約30G/分 = 推定ゲーム数
    hours_played = (check_minutes - 600) / 60  # 10時からの時間
    estimated_games = hours_played * 30 * 60  # 1時間で約1800G
    if estimated_games > 0 and art_count > 0:
        estimated_prob = estimated_games / art_count
    else:
        estimated_prob = 999
    
    # 出玉計算
    total_medals = sum(h.get('medals', 0) for h in hits_before)
    
    # 最大ハマり計算（連続する当たり間の時間差から推定）
    max_gap_minutes = 0
    sorted_hits = sorted(hits_before, key=lambda x: parse_time(x.get('time', '00:00')) or 0)
    for i in range(1, len(sorted_hits)):
        prev_time = parse_time(sorted_hits[i-1].get('time', ''))
        curr_time = parse_time(sorted_hits[i].get('time', ''))
        if prev_time and curr_time:
            gap = curr_time - prev_time
            if gap > max_gap_minutes:
                max_gap_minutes = gap
    # 分をゲーム数に変換（1分≒30G）
    max_hamari = max_gap_minutes * 30
    
    # 連チャン分析（連続して1分以内に当たった回数）
    rensa_count = 0
    single_count = 0
    for i in range(1, len(sorted_hits)):
        prev_time = parse_time(sorted_hits[i-1].get('time', ''))
        curr_time = parse_time(sorted_hits[i].get('time', ''))
        if prev_time and curr_time:
            if curr_time - prev_time <= 2:  # 2分以内は連チャン
                rensa_count += 1
            else:
                single_count += 1
    
    rensa_rate = rensa_count / (rensa_count + single_count) if (rensa_count + single_count) > 0 else 0
    
    # 最後の当たりからの経過時間
    last_hit = hits_before[-1]
    last_hit_time = parse_time(last_hit.get('time', ''))
    minutes_since_last = check_minutes - last_hit_time if last_hit_time else 0
    
    # 差枚推定（投資を引いた出玉）
    # 投資 = ゲーム数 × 3枚 / 50（1000円で50枚、3枚掛け）
    estimated_investment = estimated_games * 3 / 50 * 20  # 1000円=20枚換算
    diff_medals = total_medals - estimated_investment
    
    return {
        'art_count': art_count,
        'estimated_prob': estimated_prob,
        'total_medals': total_medals,
        'diff_medals': diff_medals,
        'max_hamari': max_hamari,
        'rensa_rate': rensa_rate,
        'minutes_since_last': minutes_since_last,
        'last_hit_time': last_hit.get('time'),
    }

def is_target_unit(state):
    """
    狙うべき台かどうか判定
    
    条件：
    1. 初当たり確率が良い（1/350以下）
    2. 大ハマりしていない（最大600G≒20分以下）
    3. 差枚がマイナス〜微プラス（-2000〜+1000）
    4. 連チャン率が低め（60%以下）
    5. 台が空いている（30分以上放置）
    """
    # 条件1: 初当たり確率
    if state['estimated_prob'] > 350:
        return False, "確率悪い"
    
    # 条件2: 大ハマりなし
    if state['max_hamari'] > 600:
        return False, "大ハマりあり"
    
    # 条件3: 差枚
    if state['diff_medals'] > 1500:  # 出すぎている台は除外
        return False, "出すぎ"
    
    # 条件4: 連チャン率（高すぎると既に出ている）
    if state['rensa_rate'] > 0.7:
        return False, "連チャン多い"
    
    # 条件5: 空き台（30分以上）
    if state['minutes_since_last'] < 30:
        return False, "まだ打ってる"
    
    return True, "OK"

def analyze_after_time(history, check_hour):
    """指定時刻以降の挙動を分析"""
    check_minutes = check_hour * 60
    
    hits_after = []
    for hit in history:
        hit_time = parse_time(hit.get('time', ''))
        if hit_time and hit_time > check_minutes:
            hits_after.append(hit)
    
    art_after = len([h for h in hits_after if h.get('type') == 'ART'])
    medals_after = sum(h.get('medals', 0) for h in hits_after)
    
    return {
        'art_after': art_after,
        'medals_after': medals_after,
    }

def run_backtest():
    """バックテスト実行"""
    results = defaultdict(lambda: {
        'total': 0, 
        'hit_1000': 0, 
        'hit_500': 0,
        'is_good_count': 0,
        'medals_sum': 0,
        'candidates': []
    })
    
    check_hours = [14, 15, 16, 17, 18]
    seen = set()
    
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
                    final_games = day.get('games', 0)
                    
                    if not history or len(history) < 10:
                        continue
                    
                    for check_hour in check_hours:
                        unique_key = f"{store_key}_{unit_id}_{date}_{check_hour}"
                        if unique_key in seen:
                            continue
                        seen.add(unique_key)
                        
                        state = analyze_unit_at_time(history, final_games, check_hour)
                        if not state:
                            continue
                        
                        is_target, reason = is_target_unit(state)
                        if not is_target:
                            continue
                        
                        after = analyze_after_time(history, check_hour)
                        
                        key = f"{check_hour}時"
                        results[key]['total'] += 1
                        results[key]['medals_sum'] += after['medals_after']
                        
                        if after['medals_after'] >= 1000:
                            results[key]['hit_1000'] += 1
                        if after['medals_after'] >= 500:
                            results[key]['hit_500'] += 1
                        if is_good_day:
                            results[key]['is_good_count'] += 1
                        
                        results[key]['candidates'].append({
                            'store': store_key,
                            'unit': unit_id,
                            'date': date,
                            'state': state,
                            'after': after,
                            'is_good': is_good_day,
                        })
            except Exception as e:
                continue
    
    return results

def print_results(results):
    """結果を表示"""
    print("=" * 70)
    print("リアルタイム予測バックテスト v2【精密版】")
    print("=" * 70)
    print()
    print("狙い条件：")
    print("  - 初当たり確率 1/350以下")
    print("  - 最大ハマり 600G以下")
    print("  - 差枚 -∞〜+1500枚")
    print("  - 連チャン率 70%以下")
    print("  - 空き時間 30分以上")
    print()
    
    for hour in [14, 15, 16, 17, 18]:
        key = f"{hour}時"
        if key not in results or results[key]['total'] == 0:
            print(f"【{key}時点】候補なし")
            continue
        
        r = results[key]
        total = r['total']
        hit_1000 = r['hit_1000']
        hit_500 = r['hit_500']
        is_good = r['is_good_count']
        avg_medals = r['medals_sum'] / total if total > 0 else 0
        
        print(f"【{key}時点】")
        print(f"  候補台数: {total}")
        print(f"  1000枚以上: {hit_1000} ({hit_1000/total*100:.1f}%)")
        print(f"  500枚以上: {hit_500} ({hit_500/total*100:.1f}%)")
        print(f"  高設定判定: {is_good} ({is_good/total*100:.1f}%)")
        print(f"  平均獲得: {avg_medals:.0f}枚")
        print()
        
        # サンプル
        if r['candidates']:
            print("  成功例:")
            successes = [c for c in r['candidates'] if c['after']['medals_after'] >= 1000][:2]
            for c in successes:
                print(f"    {c['date']} {c['store']} #{c['unit']}")
                print(f"      確率1/{c['state']['estimated_prob']:.0f}, 差枚{c['state']['diff_medals']:.0f}, 空き{c['state']['minutes_since_last']}分")
                print(f"      → その後 {c['after']['medals_after']}枚獲得 {'(高設定)' if c['is_good'] else ''}")
            print()
    
    # 全体
    total_all = sum(r['total'] for r in results.values())
    hit_1000_all = sum(r['hit_1000'] for r in results.values())
    hit_500_all = sum(r['hit_500'] for r in results.values())
    is_good_all = sum(r['is_good_count'] for r in results.values())
    medals_all = sum(r['medals_sum'] for r in results.values())
    
    print("=" * 70)
    print(f"【全体】")
    print(f"  候補: {total_all}")
    if total_all > 0:
        print(f"  1000枚以上: {hit_1000_all} ({hit_1000_all/total_all*100:.1f}%)")
        print(f"  500枚以上: {hit_500_all} ({hit_500_all/total_all*100:.1f}%)")
        print(f"  高設定判定: {is_good_all} ({is_good_all/total_all*100:.1f}%)")
        print(f"  平均獲得: {medals_all/total_all:.0f}枚")
    print("=" * 70)

if __name__ == '__main__':
    results = run_backtest()
    print_results(results)
