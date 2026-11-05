#!/usr/bin/env python3
"""履歴DBの日付欠落を検出するスクリプト

スクレイピング後またはCI/CDで実行して、欠落を早期検知する。
"""
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
JST = timezone(timedelta(hours=9))
import sys

HISTORY_DIR = Path(__file__).parent.parent / 'data' / 'history'

def check_gaps(days_back=7):
    """過去N日間の日付欠落を検出"""
    today = datetime.now(JST)
    check_dates = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(1, days_back + 1)]
    check_dates.reverse()  # 古い順
    
    gaps = []
    
    for store_dir in sorted(HISTORY_DIR.iterdir()):
        if not store_dir.is_dir():
            continue
        
        for hist_file in sorted(store_dir.glob('*.json')):
            try:
                with open(hist_file) as f:
                    data = json.load(f)
            except:
                continue
            
            dates = set(d.get('date') for d in data.get('days', []) if d.get('date'))
            unit_id = hist_file.stem
            
            # 連続性チェック: 前後の日付があるのに中間がない場合
            for i in range(1, len(check_dates) - 1):
                prev_date = check_dates[i-1]
                curr_date = check_dates[i]
                next_date = check_dates[i+1]
                
                if curr_date not in dates and prev_date in dates and next_date in dates:
                    gaps.append({
                        'store': store_dir.name,
                        'unit': unit_id,
                        'missing': curr_date,
                        'has_prev': prev_date,
                        'has_next': next_date
                    })
    
    return gaps

def main():
    gaps = check_gaps()
    
    if gaps:
        print(f"⚠️ 日付欠落検出: {len(gaps)}件")
        for g in gaps:
            print(f"  {g['store']}/{g['unit']}: {g['missing']}が欠落（{g['has_prev']}と{g['has_next']}はある）")
        sys.exit(1)  # CIで失敗として扱う
    else:
        print("✅ 日付欠落なし")
        sys.exit(0)

if __name__ == '__main__':
    main()
