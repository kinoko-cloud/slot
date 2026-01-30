#!/usr/bin/env python3
"""
蓄積DBの一括更新スクリプト

全店舗の全台をスクレイピングして data/history/ を最新日付まで更新する。
daily_collect.py の結果を history_accumulator で蓄積する一連の処理。
"""

import sys
import json
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.rankings import STORES, MACHINES, get_machine_threshold
from analysis.history_accumulator import _accumulate_unit, load_unit_history


def get_stores_needing_update(target_date: str = None) -> list:
    """target_dateまでデータがない店舗・台を洗い出す"""
    if target_date is None:
        target_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    needs_update = []
    old_keys = {'island_akihabara', 'shibuya_espass', 'shinjuku_espass'}
    
    for store_key, store_cfg in STORES.items():
        if store_key in old_keys:
            continue
        units = store_cfg.get('units', [])
        machine_key = store_cfg.get('machine', 'sbj')
        data_source = store_cfg.get('data_source', 'daidata')
        
        missing_units = []
        for uid in units:
            hist = load_unit_history(store_key, uid)
            dates = [d.get('date', '') for d in hist.get('days', [])]
            if target_date not in dates:
                latest = max(dates) if dates else 'NONE'
                missing_units.append({'unit_id': uid, 'latest': latest})
        
        if missing_units:
            needs_update.append({
                'store_key': store_key,
                'name': store_cfg['name'],
                'hall_id': store_cfg.get('hall_id'),
                'machine_key': machine_key,
                'data_source': data_source,
                'missing_units': missing_units,
                'total_units': len(units),
            })
    
    return needs_update


def update_daidata_stores(stores: list, target_date: str):
    """daidata経由の店舗を更新"""
    from scrapers.daidata_detail_history import get_all_history
    
    for store_info in stores:
        store_key = store_info['store_key']
        hall_id = store_info['hall_id']
        machine_key = store_info['machine_key']
        name = store_info['name']
        missing = store_info['missing_units']
        machine = MACHINES.get(machine_key, {})
        verify_kw = machine.get('verify_keywords')
        
        print(f"\n{'='*60}")
        print(f"【{name}】{machine.get('display_name', machine_key)} — {len(missing)}/{store_info['total_units']}台更新")
        print(f"  hall_id={hall_id}, store_key={store_key}")
        print(f"{'='*60}")
        
        for unit_info in missing:
            uid = unit_info['unit_id']
            print(f"\n  台{uid} (latest={unit_info['latest']})...")
            try:
                result = get_all_history(
                    hall_id=hall_id, unit_id=uid,
                    hall_name=name, expected_machine=verify_kw
                )
                if result and not result.get('machine_mismatch'):
                    days = result.get('days', [])
                    if days:
                        added = _accumulate_unit(store_key, uid, days, machine_key)
                        print(f"    ✓ {added}日追加 (取得{len(days)}日分)")
                    else:
                        print(f"    ⚠ データなし")
                else:
                    reason = "機種不一致" if result and result.get('machine_mismatch') else "取得失敗"
                    print(f"    ✗ {reason}")
            except Exception as e:
                print(f"    ✗ エラー: {e}")


def update_papimo_stores(stores: list, target_date: str):
    """papimo経由の店舗を更新"""
    from playwright.sync_api import sync_playwright
    from scrapers.papimo import get_unit_history, PAPIMO_CONFIG
    
    config = PAPIMO_CONFIG['island_akihabara']
    hall_id = config['hall_id']
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        
        try:
            for store_info in stores:
                store_key = store_info['store_key']
                machine_key = store_info['machine_key']
                name = store_info['name']
                missing = store_info['missing_units']
                machine = MACHINES.get(machine_key, {})
                verify_kw = machine.get('verify_keywords')
                
                print(f"\n{'='*60}")
                print(f"【{name}】{machine.get('display_name', machine_key)} — {len(missing)}/{store_info['total_units']}台更新")
                print(f"  papimo hall_id={hall_id}, store_key={store_key}")
                print(f"{'='*60}")
                
                for unit_info in missing:
                    uid = unit_info['unit_id']
                    latest = unit_info['latest']
                    # 差分日数を計算
                    if latest and latest != 'NONE':
                        from datetime import datetime as _dt
                        days_missing = (_dt.strptime(target_date, '%Y-%m-%d') - _dt.strptime(latest, '%Y-%m-%d')).days
                        days_back = min(max(days_missing + 1, 2), 14)
                    else:
                        days_back = 14
                    
                    print(f"\n  台{uid} (latest={latest}, fetch {days_back}日)...")
                    try:
                        result = get_unit_history(
                            page, hall_id, uid,
                            days_back=days_back,
                            expected_machine=verify_kw
                        )
                        if result and not result.get('machine_mismatch'):
                            days = result.get('days', [])
                            if days:
                                added = _accumulate_unit(store_key, uid, days, machine_key)
                                print(f"    ✓ {added}日追加 (取得{len(days)}日分)")
                            else:
                                print(f"    ⚠ データなし")
                        else:
                            reason = "機種不一致" if result and result.get('machine_mismatch') else "取得失敗"
                            print(f"    ✗ {reason}")
                    except Exception as e:
                        print(f"    ✗ エラー: {e}")
        finally:
            browser.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='蓄積DB一括更新')
    parser.add_argument('--target-date', '-d', default=None, help='更新目標日付 (YYYY-MM-DD)')
    parser.add_argument('--check-only', action='store_true', help='更新が必要な台を表示するだけ')
    args = parser.parse_args()
    
    target = args.target_date or (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    print(f"蓄積DB更新チェック: target_date={target}")
    print(f"{'='*60}")
    
    needs = get_stores_needing_update(target)
    
    if not needs:
        print("全店舗が最新です！")
        return
    
    total_missing = sum(len(s['missing_units']) for s in needs)
    print(f"\n更新が必要: {len(needs)}店舗, {total_missing}台")
    for s in needs:
        print(f"  {s['name']} ({s['machine_key']}): {len(s['missing_units'])}/{s['total_units']}台 [{s['data_source']}]")
    
    if args.check_only:
        return
    
    # daidata stores
    daidata_stores = [s for s in needs if s['data_source'] == 'daidata']
    papimo_stores = [s for s in needs if s['data_source'] == 'papimo']
    
    if daidata_stores:
        print(f"\n\n=== DAIDATA ({len(daidata_stores)}店舗) ===")
        update_daidata_stores(daidata_stores, target)
    
    if papimo_stores:
        print(f"\n\n=== PAPIMO ({len(papimo_stores)}店舗) ===")
        update_papimo_stores(papimo_stores, target)
    
    # 結果確認
    print(f"\n\n{'='*60}")
    print("更新結果確認")
    print(f"{'='*60}")
    remaining = get_stores_needing_update(target)
    if not remaining:
        print("✅ 全店舗が最新です！")
    else:
        total_remaining = sum(len(s['missing_units']) for s in remaining)
        print(f"⚠ まだ{total_remaining}台が未更新:")
        for s in remaining:
            print(f"  {s['name']} ({s['machine_key']}): {len(s['missing_units'])}台")


if __name__ == '__main__':
    main()
