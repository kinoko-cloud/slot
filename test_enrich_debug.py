#!/usr/bin/env python3
"""enrich_recsの前後で値を比較"""
import sys
sys.path.insert(0, '.')

from analysis.recommender import recommend_units
from scripts.enrich_rec import enrich_recs

print("=== recommender.py実行 ===")
recs = recommend_units('island_akihabara_sbj')

rec_1023 = [r for r in recs if r.get('unit_id') == '1023'][0]

print(f"\n【enrich_recs前】")
print(f"  three_days_ago_date: {rec_1023.get('three_days_ago_date')}")
print(f"  three_days_ago_art: {rec_1023.get('three_days_ago_art')}")
print(f"  three_days_ago_max_medals: {rec_1023.get('three_days_ago_max_medals')}")
before_value = rec_1023.get('three_days_ago_max_medals')

# enrich_recsを呼ぶ
rec_1023['store_key'] = 'island_akihabara_sbj'
rec_1023['machine_key'] = 'sbj'

print("\n=== enrich_recs実行 ===")
enrich_recs([rec_1023])

print(f"\n【enrich_recs後】")
print(f"  three_days_ago_date: {rec_1023.get('three_days_ago_date')}")
print(f"  three_days_ago_art: {rec_1023.get('three_days_ago_art')}")
print(f"  three_days_ago_max_medals: {rec_1023.get('three_days_ago_max_medals')}")
after_value = rec_1023.get('three_days_ago_max_medals')

if before_value != after_value:
    print(f"\n❌ 値が変更されました: {before_value} → {after_value}")
else:
    print(f"\n✅ 値は変更されていません: {before_value}")
