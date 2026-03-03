#!/usr/bin/env python3
"""
削除したコピーデータから missing_history.json を生成
"""
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
HISTORY_DIR = ROOT / 'data' / 'history'

# 削除した日付のリスト（fix_history_copies.pyの出力から）
DELETED_COPIES = [
    ("shibuya_espass_hokuto2", "2061", "2026-02-08"),
    ("shibuya_espass_hokuto2", "2236", "2026-02-23"),
    ("shibuya_espass_hokuto2", "2049", "2026-02-23"),
    ("shibuya_espass_hokuto2", "2239", "2026-02-09"),
    ("shibuya_espass_hokuto2", "2239", "2026-02-23"),
    ("shibuya_espass_hokuto2", "2239", "2026-02-26"),
    ("shibuya_espass_hokuto2", "2233", "2026-02-08"),
    ("shibuya_espass_hokuto2", "2233", "2026-02-23"),
    ("shibuya_espass_hokuto2", "2235", "2026-02-09"),
    ("shibuya_espass_hokuto2", "2058", "2026-02-09"),
    ("shibuya_espass_hokuto2", "2234", "2026-02-23"),
    ("shibuya_espass_hokuto2", "2237", "2026-02-09"),
    ("shibuya_espass_hokuto2", "2237", "2026-02-23"),
    ("shibuya_espass_hokuto2", "2056", "2026-02-08"),
    ("shibuya_espass_hokuto2", "2056", "2026-02-09"),
    ("shibuya_espass_hokuto2", "2053", "2026-02-08"),
    ("shibuya_espass_hokuto2", "2063", "2026-02-08"),
    ("shibuya_espass_hokuto2", "2059", "2026-02-08"),
    ("shibuya_espass_hokuto2", "2059", "2026-02-09"),
    ("shibuya_espass_hokuto2", "2240", "2026-02-23"),
]

def find_all_missing() -> list:
    """historyファイルをスキャンして欠落日を検出"""
    missing = []
    
    for store_dir in HISTORY_DIR.iterdir():
        if not store_dir.is_dir():
            continue
        
        for hist_file in store_dir.glob('*.json'):
            try:
                with open(hist_file) as f:
                    data = json.load(f)
                
                days = data.get('days', [])
                dates = set(d.get('date') for d in days if d.get('date'))
                
                # 日付範囲を確認（最小から最大まで）
                if not dates:
                    continue
                
                sorted_dates = sorted(dates)
                
                # 各日のデータをチェック
                for day in days:
                    date = day.get('date')
                    art = day.get('art', 0)
                    history = day.get('history', [])
                    
                    # art > 0 なのに history が空 → 欠落
                    if art > 0 and not history:
                        missing.append({
                            'store': store_dir.name,
                            'unit_id': hist_file.stem,
                            'date': date,
                            'art': art,
                            'reason': 'art>0 but no history',
                        })
            except:
                continue
    
    return missing


def main():
    print("欠落データを検出中...")
    missing = find_all_missing()
    
    print(f"検出: {len(missing)}件")
    
    # missing_history.json形式で出力
    output = []
    for m in missing:
        output.append({
            'store': m['store'],
            'unit_id': m['unit_id'],
            'date': m['date'],
        })
    
    output_path = ROOT / 'data' / 'missing_history.json'
    with open(output_path, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"保存: {output_path}")
    
    # サンプル表示
    for m in missing[:10]:
        print(f"  {m['store']}/{m['unit_id']}: {m['date']} (art={m['art']})")
    if len(missing) > 10:
        print(f"  ... 他 {len(missing) - 10}件")


if __name__ == '__main__':
    main()
