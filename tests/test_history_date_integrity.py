#!/usr/bin/env python3
"""
テスト: history日付整合性チェック

問題: 3/1のhistoryデータが2/28のコピーになっていた
原因: update_history_from_availability.pyで日付境界の問題

テスト内容:
1. 日付重複検出テスト
2. 日付境界シミュレーション
3. データ整合性チェック
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple
from collections import defaultdict

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

JST = timezone(timedelta(hours=9))


def find_duplicate_dates() -> List[Dict]:
    """全historyファイルから日付重複を検出"""
    history_dir = ROOT / 'data' / 'history'
    duplicates = []
    
    for store_dir in history_dir.iterdir():
        if not store_dir.is_dir():
            continue
        for hist_file in store_dir.glob('*.json'):
            try:
                with open(hist_file) as f:
                    data = json.load(f)
                
                dates = [d.get('date') for d in data.get('days', [])]
                seen = set()
                for date in dates:
                    if date in seen:
                        duplicates.append({
                            'store': store_dir.name,
                            'unit_id': hist_file.stem,
                            'date': date,
                        })
                    seen.add(date)
            except:
                continue
    
    return duplicates


def find_identical_adjacent_days() -> List[Dict]:
    """連続した日のデータが同一のものを検出（コピー問題）"""
    history_dir = ROOT / 'data' / 'history'
    issues = []
    
    for store_dir in history_dir.iterdir():
        if not store_dir.is_dir():
            continue
        for hist_file in store_dir.glob('*.json'):
            try:
                with open(hist_file) as f:
                    data = json.load(f)
                
                days = sorted(data.get('days', []), key=lambda x: x.get('date', ''))
                
                for i in range(len(days) - 1):
                    d1 = days[i]
                    d2 = days[i + 1]
                    
                    # 両方ともデータがある場合のみチェック
                    if d1.get('art', 0) == 0 or d2.get('art', 0) == 0:
                        continue
                    
                    # 履歴が同一かチェック
                    h1 = d1.get('history', [])
                    h2 = d2.get('history', [])
                    
                    if h1 and h2 and h1 == h2:
                        issues.append({
                            'store': store_dir.name,
                            'unit_id': hist_file.stem,
                            'date1': d1.get('date'),
                            'date2': d2.get('date'),
                            'art': d1.get('art'),
                            'history_len': len(h1),
                        })
            except:
                continue
    
    return issues


def check_availability_date_integrity() -> Dict:
    """availability.jsonのデータ日付をチェック"""
    avail_path = ROOT / 'data' / 'availability.json'
    if not avail_path.exists():
        return {'error': 'availability.json not found'}
    
    with open(avail_path) as f:
        avail = json.load(f)
    
    fetched_at = avail.get('fetched_at', '')
    if not fetched_at:
        return {'error': 'fetched_at not found'}
    
    # fetched_atの日付部分
    fetched_date = fetched_at[:10]
    today = datetime.now(JST).strftime('%Y-%m-%d')
    
    result = {
        'fetched_at': fetched_at,
        'fetched_date': fetched_date,
        'today': today,
        'date_match': fetched_date == today,
        'units_with_date': 0,
        'units_without_date': 0,
        'date_mismatches': [],
    }
    
    for store_key, store_data in avail.get('stores', {}).items():
        for unit in store_data.get('units', []):
            unit_date = unit.get('date', '')
            if unit_date:
                result['units_with_date'] += 1
                if unit_date != fetched_date:
                    result['date_mismatches'].append({
                        'store': store_key,
                        'unit_id': unit.get('unit_id'),
                        'unit_date': unit_date,
                        'fetched_date': fetched_date,
                    })
            else:
                result['units_without_date'] += 1
    
    return result


def simulate_date_boundary_issue():
    """日付境界問題のシミュレーション
    
    シナリオ:
    1. 23:50にfetch_all.pyでデータ取得（日付=2/28）
    2. 0:05にupdate_history_from_availability.pyが実行（today=3/1）
    3. 2/28のデータが3/1として書き込まれる
    """
    print("\n=== 日付境界問題シミュレーション ===")
    
    # 現在のavailability.jsonをチェック
    result = check_availability_date_integrity()
    
    print(f"fetched_at: {result.get('fetched_at')}")
    print(f"fetched_date: {result.get('fetched_date')}")
    print(f"today: {result.get('today')}")
    print(f"date_match: {result.get('date_match')}")
    
    if not result.get('date_match'):
        print("\n⚠️ 警告: availability.jsonの日付と今日の日付が一致しません！")
        print("   この状態でupdate_history_from_availability.pyを実行すると、")
        print("   古いデータが今日の日付で書き込まれます。")
    
    mismatches = result.get('date_mismatches', [])
    if mismatches:
        print(f"\n⚠️ ユニット日付不整合: {len(mismatches)}件")
        for m in mismatches[:5]:
            print(f"   {m['store']}/{m['unit_id']}: unit_date={m['unit_date']}, fetched={m['fetched_date']}")
    
    return result


def run_all_tests():
    """全テスト実行"""
    print("=" * 60)
    print("history日付整合性テスト")
    print("=" * 60)
    
    # 1. 日付重複チェック
    print("\n--- 1. 日付重複チェック ---")
    duplicates = find_duplicate_dates()
    if duplicates:
        print(f"❌ 重複あり: {len(duplicates)}件")
        for d in duplicates[:5]:
            print(f"   {d['store']}/{d['unit_id']}: {d['date']}")
    else:
        print("✅ 重複なし")
    
    # 2. 連続日コピーチェック
    print("\n--- 2. 連続日コピーチェック ---")
    copies = find_identical_adjacent_days()
    if copies:
        print(f"❌ コピー検出: {len(copies)}件")
        for c in copies[:5]:
            print(f"   {c['store']}/{c['unit_id']}: {c['date1']}={c['date2']} (art={c['art']})")
    else:
        print("✅ コピーなし")
    
    # 3. availability日付チェック
    print("\n--- 3. availability日付チェック ---")
    avail_result = simulate_date_boundary_issue()
    
    # 4. サマリ
    print("\n" + "=" * 60)
    print("サマリ")
    print("=" * 60)
    
    issues = len(duplicates) + len(copies)
    if not avail_result.get('date_match'):
        issues += 1
    
    if issues == 0:
        print("✅ 全テスト合格")
    else:
        print(f"❌ {issues}件の問題を検出")
    
    return {
        'duplicates': duplicates,
        'copies': copies,
        'availability': avail_result,
    }


if __name__ == '__main__':
    run_all_tests()
