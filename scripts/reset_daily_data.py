#!/usr/bin/env python3
"""
営業開始時にavailability.jsonをリセット

毎日10:00 JSTに実行して、前日のキャッシュデータをクリアする。
これにより「G数変化なし」でスキップした台が昨日のデータを表示する問題を防ぐ。
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).parent.parent

def reset_availability():
    """availability.jsonの全台データをリセット"""
    avail_path = ROOT / 'data' / 'availability.json'
    
    if not avail_path.exists():
        print("availability.json not found")
        return 0
    
    with open(avail_path, 'r') as f:
        data = json.load(f)
    
    reset_count = 0
    now = datetime.now(JST)
    
    for store_key, store in data.get('stores', {}).items():
        for unit in store.get('units', []):
            # 全台のリアルタイムデータをリセット
            if unit.get('games', 0) > 0 or unit.get('art', 0) > 0:
                unit['games'] = 0
                unit['art'] = 0
                unit['bb'] = 0
                unit['rb'] = 0
                unit['diff_medals'] = 0
                unit['history'] = []
                unit['cached'] = False
                unit['stale'] = False
                reset_count += 1
    
    data['fetched_at'] = now.isoformat()
    data['daily_reset'] = now.isoformat()
    
    with open(avail_path, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"Reset {reset_count} units at {now.strftime('%Y-%m-%d %H:%M')}")
    return reset_count


def main():
    reset_count = reset_availability()
    print(f"Daily reset complete: {reset_count} units cleared")


if __name__ == '__main__':
    main()
