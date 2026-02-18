#!/usr/bin/env python3
"""
欠落したhistoryデータを補完するスクリプト

data/missing_history.jsonに記録された欠落データを
daidata_detail_history.pyで取得して補完する。
"""

import sys
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from scrapers.daidata_detail_history import get_all_history
from config.stores import DAIDATA_STORES

JST = timezone(timedelta(hours=9))

# store_keyからhall_idを取得するマッピング
def get_hall_id(store_key: str) -> str:
    """store_keyからhall_idを取得"""
    # store_keyから基本店舗名を抽出（例: shibuya_espass_sbj → shibuya_espass）
    base_store = store_key.rsplit('_', 1)[0]
    
    for key, config in DAIDATA_STORES.items():
        if key == base_store or key in store_key:
            return config.get('hall_id')
    
    return None


def fill_missing_history(dry_run: bool = False, limit: int = None):
    """欠落データを補完"""
    
    missing_file = Path('data/missing_history.json')
    if not missing_file.exists():
        print("❌ data/missing_history.jsonが見つかりません")
        return
    
    with open(missing_file) as f:
        missing = json.load(f)
    
    print(f"欠落データ: {len(missing)}件")
    
    # store_key + unit_idでグループ化（同じ台の複数日分をまとめて取得）
    grouped = {}
    for m in missing:
        key = f"{m['store']}:{m['unit_id']}"
        if key not in grouped:
            grouped[key] = {
                'store': m['store'],
                'unit_id': m['unit_id'],
                'dates': []
            }
        grouped[key]['dates'].append(m['date'])
    
    print(f"ユニーク台数: {len(grouped)}台")
    
    if limit:
        grouped = dict(list(grouped.items())[:limit])
        print(f"制限: {limit}台")
    
    filled = 0
    failed = 0
    
    for key, info in grouped.items():
        store_key = info['store']
        unit_id = info['unit_id']
        dates = info['dates']
        
        hall_id = get_hall_id(store_key)
        if not hall_id:
            print(f"⚠️ hall_id不明: {store_key}")
            failed += 1
            continue
        
        # 機種を判定
        machine = 'sbj' if 'sbj' in store_key else 'hokuto2'
        
        print(f"\n--- {store_key}/{unit_id} ({dates}) ---")
        
        if dry_run:
            print(f"  [dry-run] hall_id={hall_id}, machine={machine}")
            continue
        
        try:
            # daidataから履歴を取得
            result = get_all_history(
                hall_id=hall_id,
                unit_id=unit_id,
                hall_name=store_key,
                expected_machine=machine
            )
            
            if not result or not result.get('days'):
                print(f"  ❌ データ取得失敗")
                failed += 1
                continue
            
            # 取得したデータをhistoryファイルに反映
            history_file = Path(f'data/history/{store_key}/{unit_id}.json')
            if not history_file.exists():
                print(f"  ❌ historyファイルなし: {history_file}")
                failed += 1
                continue
            
            with open(history_file) as f:
                history_data = json.load(f)
            
            # 既存のdays辞書を作成
            existing_days = {d['date']: d for d in history_data.get('days', [])}
            
            # 取得したデータで補完
            for day in result.get('days', []):
                date = day.get('date')
                if date not in dates:
                    continue  # 欠落日以外はスキップ
                
                if date in existing_days:
                    existing = existing_days[date]
                    # historyが空の場合のみ上書き
                    if not existing.get('history') or len(existing.get('history', [])) == 0:
                        existing['history'] = day.get('history', [])
                        existing['diff_medals'] = day.get('diff_medals')
                        existing['max_rensa'] = day.get('max_rensa')
                        existing['max_medals'] = day.get('max_medals')
                        existing['games'] = day.get('games', 0)
                        print(f"  ✅ {date} 補完完了: hist={len(day.get('history', []))}")
                        filled += 1
            
            # 保存
            with open(history_file, 'w') as f:
                json.dump(history_data, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            print(f"  ❌ エラー: {e}")
            failed += 1
    
    print(f"\n=== 完了 ===")
    print(f"補完成功: {filled}件")
    print(f"失敗: {failed}件")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='実際には取得しない')
    parser.add_argument('--limit', type=int, help='取得台数制限')
    args = parser.parse_args()
    
    fill_missing_history(dry_run=args.dry_run, limit=args.limit)
