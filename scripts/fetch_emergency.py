#!/usr/bin/env python3
"""
緊急データ取得モード（並列化）

大量のデータ欠落時に並列処理で高速取得する。
通常の2倍〜4倍の速度でデータを収集可能。

使用方法:
  python scripts/fetch_emergency.py           # 2並列（デフォルト）
  python scripts/fetch_emergency.py --workers 4  # 4並列
  python scripts/fetch_emergency.py --dry-run    # 実行せずに分割を確認
"""
import sys
import json
import time
import argparse
import multiprocessing
from pathlib import Path
from datetime import datetime, timedelta, timezone
JST = timezone(timedelta(hours=9))
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.stores import DAIDATA_STORES
from scrapers.daidata_detail_history import get_all_history
from analysis.history_accumulator import _accumulate_unit, load_unit_history

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TARGET_DATE = (datetime.now(JST) - timedelta(days=1)).strftime('%Y-%m-%d')

def get_all_units():
    """全店舗・全台のリストを取得"""
    units = []
    for store_name, store_cfg in DAIDATA_STORES.items():
        hall_id = store_cfg.get('hall_id')
        hall_name = store_cfg.get('name', store_name)
        machines = store_cfg.get('machines', {})
        
        if not hall_id or not machines:
            continue
        
        for machine_key, unit_ids in machines.items():
            if not unit_ids:
                continue
            store_key = f"{store_name}_{machine_key}"
            for unit_id in unit_ids:
                units.append({
                    'store_key': store_key,
                    'store_name': store_name,
                    'hall_id': hall_id,
                    'hall_name': hall_name,
                    'machine_key': machine_key,
                    'unit_id': unit_id
                })
    return units

def check_needs_update(store_key, unit_id):
    """更新が必要かチェック"""
    hist = load_unit_history(store_key, unit_id)
    existing_dates = set(d.get('date', '') for d in hist.get('days', []))
    return TARGET_DATE not in existing_dates

def fetch_unit(unit_info):
    """1台のデータを取得"""
    store_key = unit_info['store_key']
    unit_id = unit_info['unit_id']
    hall_id = unit_info['hall_id']
    hall_name = unit_info['hall_name']
    machine_key = unit_info['machine_key']
    
    # 更新不要ならスキップ
    if not check_needs_update(store_key, unit_id):
        return {'status': 'skip', 'unit': unit_id, 'store': store_key}
    
    try:
        # データ取得
        result = get_all_history(
            hall_id=hall_id,
            unit_id=str(unit_id),
            hall_name=hall_name,
            expected_machine=machine_key
        )
        
        new_days = result.get('days', [])
        
        # 既存データを取得
        hist = load_unit_history(store_key, unit_id)
        existing_dates = set(d.get('date', '') for d in hist.get('days', []))
        
        # 新しい日付のみ追加
        days_to_add = [d for d in new_days if d.get('date', '') and d.get('date', '') not in existing_dates]
        
        if days_to_add:
            _accumulate_unit(store_key, unit_id, days_to_add, machine_key)
            return {'status': 'updated', 'unit': unit_id, 'store': store_key, 'days': len(days_to_add)}
        else:
            return {'status': 'no_new', 'unit': unit_id, 'store': store_key}
            
    except Exception as e:
        return {'status': 'error', 'unit': unit_id, 'store': store_key, 'error': str(e)}
    finally:
        # レート制限対策（各ワーカーで1秒待機）
        time.sleep(1)

def main():
    parser = argparse.ArgumentParser(description='緊急データ取得モード（並列化）')
    parser.add_argument('--workers', type=int, default=2, help='並列数（デフォルト: 2）')
    parser.add_argument('--dry-run', action='store_true', help='実行せずに確認のみ')
    args = parser.parse_args()
    
    print("=" * 60)
    print("🚨 緊急データ取得モード")
    print(f"目標日付: {TARGET_DATE}")
    print(f"並列数: {args.workers}")
    print("=" * 60)
    
    # 全台リスト取得
    all_units = get_all_units()
    print(f"\n全台数: {len(all_units)}")
    
    # 更新が必要な台を抽出
    needs_update = [u for u in all_units if check_needs_update(u['store_key'], u['unit_id'])]
    print(f"更新必要: {len(needs_update)}台")
    
    if args.dry_run:
        print("\n[DRY-RUN] 実行せずに終了")
        # 店舗別内訳
        by_store = {}
        for u in needs_update:
            sk = u['store_key']
            by_store[sk] = by_store.get(sk, 0) + 1
        print("\n店舗別内訳:")
        for sk, cnt in sorted(by_store.items()):
            print(f"  {sk}: {cnt}台")
        return
    
    if not needs_update:
        print("\n✅ 全台更新済み！")
        return
    
    # 並列実行
    print(f"\n🔄 {len(needs_update)}台を{args.workers}並列で取得開始...")
    start_time = time.time()
    
    results = {'updated': 0, 'skip': 0, 'no_new': 0, 'error': 0}
    
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(fetch_unit, u): u for u in needs_update}
        
        for i, future in enumerate(as_completed(futures), 1):
            try:
                result = future.result(timeout=120)
                status = result.get('status', 'error')
                results[status] = results.get(status, 0) + 1
                
                # 進捗表示
                if status == 'updated':
                    print(f"[{i}/{len(needs_update)}] ✅ {result['store']} 台{result['unit']} (+{result['days']}日)")
                elif status == 'error':
                    print(f"[{i}/{len(needs_update)}] ❌ {result['store']} 台{result['unit']}: {result.get('error', '')[:50]}")
                elif i % 10 == 0:
                    print(f"[{i}/{len(needs_update)}] 処理中...")
                    
            except Exception as e:
                results['error'] += 1
                print(f"[{i}/{len(needs_update)}] ❌ 例外: {e}")
    
    elapsed = time.time() - start_time
    
    print("\n" + "=" * 60)
    print("📊 完了!")
    print(f"  更新: {results['updated']}台")
    print(f"  スキップ（既に最新）: {results['skip']}台")
    print(f"  新規データなし: {results['no_new']}台")
    print(f"  エラー: {results['error']}台")
    print(f"  所要時間: {int(elapsed)}秒（{int(elapsed/60)}分）")
    print("=" * 60)

if __name__ == '__main__':
    multiprocessing.set_start_method('spawn', force=True)
    main()
