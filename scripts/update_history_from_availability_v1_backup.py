#!/usr/bin/env python3
"""
availability.jsonからhistoryファイルを更新するスクリプト
- diff_medals, max_medals, max_rensaを計算して保存
- 当日データのみ更新
"""
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).parent.parent
HISTORY_DIR = ROOT / 'data' / 'history'

def calculate_max_rensa(history: list) -> int:
    """履歴から最大連チャンを計算
    
    連チャン判定ルール（DAIDATA仕様）:
    - RBのstartが65G超 → 新規当たり開始
    - RBのstartが65G以下 → 連チャン継続
    - ARTのstartは常に0か1なので判定に使わない（RBに追従）
    """
    if not history:
        return 0
    
    max_rensa = 0
    current_rensa = 0
    
    # 時刻順（古い順）にソート
    sorted_hist = sorted(history, key=lambda x: x.get('time', ''))
    
    for h in sorted_hist:
        hit_type = h.get('type', 'ART')
        start = h.get('start', 999)
        
        if hit_type == 'RB':
            # RBのstartで新規/継続を判定
            if start > 65:
                max_rensa = max(max_rensa, current_rensa)
                current_rensa = 1
            else:
                current_rensa += 1
        else:
            # ARTは常に連チャン継続扱い
            current_rensa += 1
    
    max_rensa = max(max_rensa, current_rensa)
    return max_rensa


def calculate_max_chain_medals(history: list) -> int:
    """履歴から連チャン累計枚数の最大を計算
    
    連チャン判定ルール（DAIDATA仕様）:
    - RBのstartが65G超 → 新規当たり開始
    - RBのstartが65G以下 → 連チャン継続
    - ARTのstartは常に0か1なので判定に使わない（RBに追従）
    """
    if not history:
        return 0
    
    # 時刻順にソート
    sorted_hist = sorted(history, key=lambda x: x.get('time', ''))
    
    if not sorted_hist:
        return 0
    
    max_chain_medals = 0
    current_chain_medals = 0
    
    for h in sorted_hist:
        hit_type = h.get('type', 'ART')
        start = h.get('start', 999)
        medals = h.get('medals', 0)
        
        if hit_type == 'RB':
            # RBのstartで新規/継続を判定
            if start > 65:
                max_chain_medals = max(max_chain_medals, current_chain_medals)
                current_chain_medals = medals
            else:
                current_chain_medals += medals
        else:
            # ARTは常に連チャン継続扱い
            current_chain_medals += medals
    
    max_chain_medals = max(max_chain_medals, current_chain_medals)
    return max_chain_medals

def update_history_from_availability():
    """availability.jsonからhistoryファイルを更新"""
    avail_path = ROOT / 'data' / 'availability.json'
    if not avail_path.exists():
        print("availability.jsonが存在しません")
        return
    
    with open(avail_path) as f:
        avail = json.load(f)
    
    today = datetime.now(JST).strftime('%Y-%m-%d')
    updated_count = 0
    
    for store_key, store_data in avail.get('stores', {}).items():
        store_dir = HISTORY_DIR / store_key
        store_dir.mkdir(parents=True, exist_ok=True)
        
        for unit in store_data.get('units', []):
            unit_id = unit.get('unit_id')
            if not unit_id:
                continue
            
            history = unit.get('history', [])
            art = unit.get('art', 0)
            games = unit.get('games', 0)
            bb = unit.get('bb', 0)
            rb = unit.get('rb', 0)
            
            # 計算
            diff_medals = unit.get('diff_medals', 0)
            # max_medalsはDAIDATAから取得した値を優先（連チャン累計の最大）
            max_medals = unit.get('max_medals', 0)
            # DAIDATAから取得できない場合のみ履歴から推定（フォールバック）
            if not max_medals and history:
                # 単発最大を使用（連チャン計算は不正確なので）
                max_medals = max((h.get('medals', 0) for h in history), default=0)
            max_rensa = calculate_max_rensa(history)
            
            # historyファイルを読み込み
            hist_file = store_dir / f"{unit_id}.json"
            if hist_file.exists():
                with open(hist_file) as f:
                    hist_data = json.load(f)
            else:
                hist_data = {'unit_id': unit_id, 'days': []}
            
            # 当日データを更新/追加
            day_found = False
            for day in hist_data.get('days', []):
                if day.get('date') == today:
                    day['art'] = art
                    day['bb'] = bb
                    day['rb'] = rb
                    day['games'] = games
                    day['diff_medals'] = diff_medals
                    day['max_medals'] = max_medals
                    day['max_rensa'] = max_rensa
                    day['history'] = history
                    day_found = True
                    break
            
            if not day_found:
                hist_data['days'].append({
                    'date': today,
                    'art': art,
                    'bb': bb,
                    'rb': rb,
                    'games': games,
                    'diff_medals': diff_medals,
                    'max_medals': max_medals,
                    'max_rensa': max_rensa,
                    'history': history,
                })
            
            # 保存
            with open(hist_file, 'w') as f:
                json.dump(hist_data, f, ensure_ascii=False, indent=2)
            
            updated_count += 1
    
    print(f"✅ {updated_count}台のhistoryファイルを更新しました")

if __name__ == '__main__':
    update_history_from_availability()
