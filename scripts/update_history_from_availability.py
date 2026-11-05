#!/usr/bin/env python3
"""
availability.jsonからhistoryファイルを更新するスクリプト（v2: 日付整合性対策版）

v1からの改善点:
- availability.jsonのfetched_atの日付を使用（datetime.now()ではない）
- ユニットごとのdate属性を優先
- データ日付と書き込み先日付の整合性チェック
- 既存データとの重複検出・警告
- 日付境界（0時前後）での安全動作

問題: 3/1のhistoryが2/28のコピーになっていた
原因: 日付境界でdatetime.now()を使うと古いデータが新しい日付で保存される
"""
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List

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
            if start > 65:
                max_rensa = max(max_rensa, current_rensa)
                current_rensa = 1
            else:
                current_rensa += 1
        else:
            current_rensa += 1
    
    max_rensa = max(max_rensa, current_rensa)
    return max_rensa


def get_data_date(avail: Dict, unit: Dict) -> Optional[str]:
    """データの実際の日付を取得
    
    優先順位:
    1. ユニットのdate属性
    2. availability.jsonのfetched_atの日付部分
    
    Returns:
        YYYY-MM-DD形式の日付、または取得できない場合はNone
    """
    # ユニットのdate属性を優先
    unit_date = unit.get('date', '')
    if unit_date and len(unit_date) >= 10:
        return unit_date[:10]
    
    # fetched_atから取得
    fetched_at = avail.get('fetched_at', '')
    if fetched_at and len(fetched_at) >= 10:
        return fetched_at[:10]
    
    return None


def is_data_identical(existing: Dict, new_data: Dict) -> bool:
    """既存データと新データが同一かチェック"""
    # 履歴が同一ならデータは同一とみなす
    existing_hist = existing.get('history', [])
    new_hist = new_data.get('history', [])
    
    if existing_hist and new_hist:
        return existing_hist == new_hist
    
    # 履歴がない場合はart, games等で比較
    return (
        existing.get('art') == new_data.get('art') and
        existing.get('games') == new_data.get('games') and
        existing.get('bb') == new_data.get('bb')
    )


def is_ghost_entry(hist_data: Dict, new_data: Dict) -> bool:
    """ゴーストエントリ判定: art=0, hist=0 かつ前日と同じgames値

    Returns:
        True: ゴーストエントリ（保存しない）
        False: 正常データ
    """
    if new_data.get('art', 0) != 0:
        return False
    if new_data.get('history', []):
        return False
    new_games = new_data.get('games', 0)
    if new_games == 0:
        return False
    # 既存の最新日付データのgamesと比較
    sorted_days = sorted(hist_data.get('days', []), key=lambda x: x.get('date', ''))
    if sorted_days:
        prev_games = sorted_days[-1].get('games', 0)
        if prev_games == new_games:
            return True
    return False


def check_and_warn_duplicate(store_key: str, unit_id: str, hist_data: Dict, target_date: str, new_data: Dict) -> bool:
    """重複データをチェックし、異常があれば警告

    Returns:
        True: 書き込みOK
        False: 書き込みスキップ（異常検出）
    """
    for day in hist_data.get('days', []):
        if day.get('date') == target_date:
            # 同じ日付のデータが既にある
            if is_data_identical(day, new_data):
                # データが同一なら更新しても問題なし
                return True
            else:
                # データが異なる場合、警告
                existing_art = day.get('art', 0)
                new_art = new_data.get('art', 0)
                existing_hist_len = len(day.get('history', []))
                new_hist_len = len(new_data.get('history', []))
                
                # 新データの方が充実している場合は更新OK
                # 2026-07-07: art単独比較だとart=0の詳細取得データ(history実データあり)が
                # GAS由来のart高値スタブ(history=[])に負けて破棄されるバグがあったため、
                # historyが増えている場合は無条件で更新を優先する
                if new_hist_len > existing_hist_len:
                    return True
                if new_art >= existing_art and new_hist_len >= existing_hist_len:
                    return True
                
                # 既存の方が充実している場合は警告してスキップ
                print(f"⚠️ {store_key}/{unit_id}: {target_date}の既存データと異なる新データを検出")
                print(f"   既存: art={existing_art}, hist={existing_hist_len}")
                print(f"   新規: art={new_art}, hist={new_hist_len}")
                print(f"   → 既存データを保持（新規データをスキップ）")
                return False
    
    # 同じ日付のデータがない場合、隣接日とのコピーチェック
    sorted_days = sorted(hist_data.get('days', []), key=lambda x: x.get('date', ''))
    new_hist = new_data.get('history', [])
    
    for day in sorted_days:
        day_date = day.get('date', '')
        day_hist = day.get('history', [])
        
        # 隣接日（前後1日）のデータと同一履歴かチェック
        if new_hist and day_hist and new_hist == day_hist:
            print(f"⚠️ {store_key}/{unit_id}: {target_date}のデータが{day_date}と同一")
            print(f"   → コピー疑惑のため書き込みスキップ")
            return False
    
    return True


def update_history_from_availability():
    """availability.jsonからhistoryファイルを更新（日付整合性対策版）"""
    avail_path = ROOT / 'data' / 'availability.json'
    if not avail_path.exists():
        print("❌ availability.jsonが存在しません")
        return
    
    with open(avail_path) as f:
        avail = json.load(f)
    
    # availability.jsonのfetched_at日付を取得
    fetched_at = avail.get('fetched_at', '')
    if not fetched_at:
        print("❌ fetched_atが存在しません")
        return
    
    base_date = fetched_at[:10]  # YYYY-MM-DD
    today = datetime.now(JST).strftime('%Y-%m-%d')
    
    print(f"📅 availability.json fetched_at: {fetched_at}")
    print(f"📅 base_date: {base_date}")
    print(f"📅 today: {today}")
    
    # 日付が異なる場合は警告
    if base_date != today:
        print(f"\n⚠️ 警告: データ日付({base_date})と今日({today})が異なります")
        print(f"   データ日付({base_date})を使用して書き込みます")
    
    updated_count = 0
    skipped_count = 0
    
    for store_key, store_data in avail.get('stores', {}).items():
        store_dir = HISTORY_DIR / store_key
        store_dir.mkdir(parents=True, exist_ok=True)
        
        for unit in store_data.get('units', []):
            unit_id = unit.get('unit_id')
            if not unit_id:
                continue
            
            # このユニットのデータ日付を取得
            data_date = get_data_date(avail, unit)
            if not data_date:
                print(f"⚠️ {store_key}/{unit_id}: データ日付を特定できません、スキップ")
                skipped_count += 1
                continue
            
            history = unit.get('history', [])
            art = unit.get('art', 0)
            games = unit.get('games', 0)
            bb = unit.get('bb', 0)
            rb = unit.get('rb', 0)
            
            # 計算
            diff_medals = unit.get('diff_medals', 0)
            max_medals = unit.get('max_medals', 0)
            if not max_medals and history:
                max_medals = max((h.get('medals', 0) for h in history), default=0)
            max_rensa = calculate_max_rensa(history)
            
            new_data = {
                'date': data_date,
                'art': art,
                'bb': bb,
                'rb': rb,
                'games': games,
                'diff_medals': diff_medals,
                'max_medals': max_medals,
                'max_rensa': max_rensa,
                'history': history,
            }
            
            # historyファイルを読み込み
            hist_file = store_dir / f"{unit_id}.json"
            if hist_file.exists():
                with open(hist_file) as f:
                    hist_data = json.load(f)
            else:
                hist_data = {'unit_id': unit_id, 'days': []}
            
            # ゴーストエントリチェック（art=0, hist=0, 前日と同じgames → 保存しない）
            if is_ghost_entry(hist_data, new_data):
                print(f"  ⚠️ GHOST SKIP {store_key}/{unit_id} ({data_date}): art=0, hist=0, games={new_data.get('games')}（前日コピー）")
                skipped_count += 1
                continue

            # 重複・コピーチェック
            if not check_and_warn_duplicate(store_key, unit_id, hist_data, data_date, new_data):
                skipped_count += 1
                continue
            
            # 当日データを更新/追加
            day_found = False
            for day in hist_data.get('days', []):
                if day.get('date') == data_date:
                    day.update(new_data)
                    day_found = True
                    break
            
            if not day_found:
                hist_data['days'].append(new_data)
            
            # 保存
            with open(hist_file, 'w') as f:
                json.dump(hist_data, f, ensure_ascii=False, indent=2)
            
            updated_count += 1
    
    print(f"\n✅ 更新: {updated_count}台")
    if skipped_count > 0:
        print(f"⚠️ スキップ: {skipped_count}台")


if __name__ == '__main__':
    update_history_from_availability()
