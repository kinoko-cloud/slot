#!/usr/bin/env python3
"""recommender.pyの修正をテスト"""
import sys
sys.path.insert(0, '.')

from analysis.recommender import recommend_units

print("=== テスト開始 ===")

recs = recommend_units('island_akihabara_sbj')

# 1023番台を確認
rec_1023 = [r for r in recs if r.get('unit_id') == '1023']
if rec_1023:
    r = rec_1023[0]
    print(f"\n1023番台:")
    print(f"  three_days_ago_date: {r.get('three_days_ago_date')}")
    print(f"  three_days_ago_art: {r.get('three_days_ago_art')}")
    print(f"  three_days_ago_max_medals: {r.get('three_days_ago_max_medals')}")

    # 期待値: 3427枚
    expected = 3427
    actual = r.get('three_days_ago_max_medals', 0)
    if actual >= expected * 0.9:  # 10%の誤差を許容
        print(f"  ✅ OK: {actual}枚 (期待値 {expected}枚)")
    else:
        print(f"  ❌ NG: {actual}枚 (期待値 {expected}枚)")

# 1026番台も確認
rec_1026 = [r for r in recs if r.get('unit_id') == '1026']
if rec_1026:
    r = rec_1026[0]
    print(f"\n1026番台:")
    print(f"  day_before_date: {r.get('day_before_date')}")
    print(f"  day_before_art: {r.get('day_before_art')}")
    print(f"  day_before_max_medals: {r.get('day_before_max_medals')}")
