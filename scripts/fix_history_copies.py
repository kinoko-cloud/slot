#!/usr/bin/env python3
"""
historyファイルの連続日コピー問題を修正

問題: 連続する2日のデータが同一（例: 2/28のデータが3/1にもコピー）
対策: 古い方のデータを保持し、新しい方の重複データを削除
"""
import json
from pathlib import Path
from typing import List, Dict

ROOT = Path(__file__).parent.parent
HISTORY_DIR = ROOT / 'data' / 'history'


def find_and_fix_copies(dry_run: bool = True) -> Dict:
    """連続日コピーを検出して修正
    
    Args:
        dry_run: Trueなら検出のみ、Falseなら実際に修正
    
    Returns:
        修正結果のサマリ
    """
    results = {
        'checked_files': 0,
        'found_copies': [],
        'fixed_copies': [],
        'errors': [],
    }
    
    for store_dir in HISTORY_DIR.iterdir():
        if not store_dir.is_dir():
            continue
        
        for hist_file in store_dir.glob('*.json'):
            results['checked_files'] += 1
            
            try:
                with open(hist_file) as f:
                    data = json.load(f)
                
                days = data.get('days', [])
                if len(days) < 2:
                    continue
                
                # 日付順にソート
                sorted_days = sorted(days, key=lambda x: x.get('date', ''))
                
                # コピーを検出
                copies_to_remove = []
                for i in range(len(sorted_days) - 1):
                    d1 = sorted_days[i]
                    d2 = sorted_days[i + 1]
                    
                    # 両方ともデータがある場合のみチェック
                    if d1.get('art', 0) == 0 or d2.get('art', 0) == 0:
                        continue
                    
                    h1 = d1.get('history', [])
                    h2 = d2.get('history', [])
                    
                    if h1 and h2 and h1 == h2:
                        copy_info = {
                            'store': store_dir.name,
                            'unit_id': hist_file.stem,
                            'date1': d1.get('date'),
                            'date2': d2.get('date'),
                            'art': d1.get('art'),
                            'history_len': len(h1),
                        }
                        results['found_copies'].append(copy_info)
                        
                        # 新しい方（d2）を削除対象に
                        copies_to_remove.append(d2.get('date'))
                
                # 修正実行
                if copies_to_remove and not dry_run:
                    original_len = len(data['days'])
                    data['days'] = [d for d in data['days'] if d.get('date') not in copies_to_remove]
                    new_len = len(data['days'])
                    
                    if new_len < original_len:
                        with open(hist_file, 'w') as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)
                        
                        for date in copies_to_remove:
                            results['fixed_copies'].append({
                                'store': store_dir.name,
                                'unit_id': hist_file.stem,
                                'removed_date': date,
                            })
            
            except Exception as e:
                results['errors'].append({
                    'file': str(hist_file),
                    'error': str(e),
                })
    
    return results


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='historyファイルのコピー問題を修正')
    parser.add_argument('--fix', action='store_true', help='実際に修正を実行（デフォルトはdry-run）')
    args = parser.parse_args()
    
    dry_run = not args.fix
    
    print("=" * 60)
    print("historyファイル コピー問題修正")
    print("=" * 60)
    print(f"モード: {'dry-run（検出のみ）' if dry_run else '修正実行'}")
    print()
    
    results = find_and_fix_copies(dry_run=dry_run)
    
    print(f"チェックしたファイル: {results['checked_files']}")
    print(f"検出したコピー: {len(results['found_copies'])}件")
    
    if results['found_copies']:
        print("\n--- 検出したコピー ---")
        for c in results['found_copies'][:20]:
            print(f"  {c['store']}/{c['unit_id']}: {c['date1']}={c['date2']} (art={c['art']})")
        if len(results['found_copies']) > 20:
            print(f"  ... 他 {len(results['found_copies']) - 20}件")
    
    if results['fixed_copies']:
        print(f"\n--- 修正したコピー: {len(results['fixed_copies'])}件 ---")
        for f in results['fixed_copies'][:10]:
            print(f"  {f['store']}/{f['unit_id']}: {f['removed_date']}を削除")
        if len(results['fixed_copies']) > 10:
            print(f"  ... 他 {len(results['fixed_copies']) - 10}件")
    
    if results['errors']:
        print(f"\n--- エラー: {len(results['errors'])}件 ---")
        for e in results['errors'][:5]:
            print(f"  {e['file']}: {e['error']}")
    
    if dry_run and results['found_copies']:
        print("\n" + "=" * 60)
        print("修正を実行するには: python fix_history_copies.py --fix")
        print("=" * 60)


if __name__ == '__main__':
    main()
