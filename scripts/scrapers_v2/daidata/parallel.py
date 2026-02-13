#!/usr/bin/env python3
"""
scrapers_v2/daidata/parallel.py - 並列取得

複数台を並列でスクレイピングして高速化
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any, Optional
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scrapers_v2.common.base import setup_logger, now_jst
from scrapers_v2.daidata.scraper import DaidataScraper

logger = setup_logger('parallel')


def fetch_unit_batch(hall_id: str, unit_ids: List[str], mode: str = 'realtime') -> List[Dict]:
    """1つのブラウザセッションで複数台を順次取得"""
    scraper = DaidataScraper(headless=True)
    results = []
    
    with scraper.browser_session():
        for unit_id in unit_ids:
            try:
                if mode == 'realtime':
                    data = scraper.fetch_realtime(hall_id, unit_id)
                else:
                    data = {
                        'unit_id': unit_id,
                        'days': scraper.fetch_history(hall_id, unit_id)
                    }
                results.append(data)
            except Exception as e:
                results.append({'unit_id': unit_id, 'error': str(e)})
    
    return results


def parallel_fetch(store_configs: Dict[str, Dict], 
                   mode: str = 'realtime',
                   max_workers: int = 3,
                   batch_size: int = 5) -> Dict[str, Any]:
    """
    複数店舗を並列取得
    
    Args:
        store_configs: {store_key: {'hall_id': str, 'units': List[str]}}
        mode: 'realtime' or 'history'
        max_workers: 並列ワーカー数（ブラウザ数）
        batch_size: 1ワーカーあたりの台数
    
    Returns:
        {store_key: [unit_data, ...]}
    """
    start_time = time.time()
    results = {}
    tasks = []
    
    # タスク分割
    for store_key, cfg in store_configs.items():
        hall_id = cfg.get('hall_id')
        units = cfg.get('units', [])
        
        if not hall_id or not units:
            continue
        
        # バッチに分割
        for i in range(0, len(units), batch_size):
            batch = units[i:i+batch_size]
            tasks.append({
                'store_key': store_key,
                'hall_id': hall_id,
                'units': batch,
            })
    
    logger.info(f"並列取得開始: {len(tasks)}バッチ, {max_workers}ワーカー")
    
    # 並列実行
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for task in tasks:
            future = executor.submit(
                fetch_unit_batch,
                task['hall_id'],
                task['units'],
                mode
            )
            futures[future] = task['store_key']
        
        for future in as_completed(futures):
            store_key = futures[future]
            try:
                batch_results = future.result()
                if store_key not in results:
                    results[store_key] = []
                results[store_key].extend(batch_results)
            except Exception as e:
                logger.error(f"{store_key}: {e}")
    
    elapsed = time.time() - start_time
    total_units = sum(len(v) for v in results.values())
    logger.info(f"完了: {total_units}台, {elapsed:.1f}秒 ({total_units/elapsed:.1f}台/秒)")
    
    return {
        'results': results,
        'elapsed': elapsed,
        'total_units': total_units,
        'fetched_at': now_jst().isoformat()
    }


# CLI
if __name__ == '__main__':
    from scrapers_v2.config import DAIDATA_CONFIG
    
    # テスト: SBJ店舗のみ
    stores = DAIDATA_CONFIG.get('stores', {})
    sbj_stores = {k: v for k, v in stores.items() if '_sbj' in k}
    
    # 小規模テスト（2店舗）
    test_stores = dict(list(sbj_stores.items())[:2])
    
    print(f"テスト対象: {list(test_stores.keys())}")
    
    result = parallel_fetch(
        test_stores,
        mode='realtime',
        max_workers=2,
        batch_size=3
    )
    
    print(f"\n結果: {result['total_units']}台, {result['elapsed']:.1f}秒")
