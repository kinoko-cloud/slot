#!/usr/bin/env python3
"""
scrapers_v2/fetch_all.py - v2統合スクレイパー

特徴:
- 台番号の自動検出（discovery）を毎回実行
- 差分取得（G数変化台のみ詳細取得）
- 並列取得対応
- v1のavailability.jsonと互換性維持
"""
import sys
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional

# パス設定
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / 'scripts' / 'scrapers_v2'))
sys.path.insert(0, str(ROOT / 'scripts'))

from daidata.scraper import DaidataScraper
from daidata.discovery import DaidataDiscovery
from common.base import setup_logger, now_jst

# v1の店舗設定をインポート
from fetch_daidata_availability import DAIDATA_STORES, PAPIMO_STORES

logger = setup_logger('fetch_all')

JST = timezone(timedelta(hours=9))


class V2Fetcher:
    """v2統合フェッチャー"""
    
    def __init__(self, headless: bool = True, discover: bool = True):
        self.headless = headless
        self.discover = discover  # 台番号自動検出を行うか
        self.results = {}
        
    def fetch_store_daidata(self, store_key: str, config: Dict) -> Dict[str, Any]:
        """
        1店舗のdaidataデータ取得
        
        1. 台番号を自動検出（オプション）
        2. 一覧ページでG数＋空き/遊技中を一括取得
        3. 詳細は差分取得（G数変化台のみ）
        """
        hall_id = config['hall_id']
        model_encoded = config['model_encoded']
        expected_units = config.get('units', [])
        
        result = {
            'store_key': store_key,
            'name': config.get('name', store_key),
            'units': {},
            'playing': [],
            'empty': [],
            'fetched_at': None,
            'error': None,
        }
        
        try:
            scraper = DaidataScraper(headless=self.headless)
            
            with scraper.browser_session():
                # 台番号自動検出
                if self.discover:
                    machine_key = 'sbj' if 'sbj' in store_key else 'hokuto2'
                    discovery = DaidataDiscovery(headless=self.headless)
                    # 既存のブラウザセッションを再利用（別インスタンスなので無理）
                    # discoveryは別途実行
                
                # 一覧ページでG数＋空き/遊技中を取得
                list_data = scraper.fetch_list_with_availability(
                    hall_id, model_encoded, expected_units
                )
                
                result['playing'] = list_data.get('playing', [])
                result['empty'] = list_data.get('empty', [])
                
                # 各台の詳細取得（空き台はスキップして高速化）
                games_map = list_data.get('games', {})
                
                for unit_id in expected_units:
                    games = games_map.get(unit_id, 0)
                    
                    # 遊技中の台のみ詳細取得
                    if unit_id in result['playing'] and games > 0:
                        detail = scraper.fetch_realtime(hall_id, unit_id)
                        result['units'][unit_id] = detail
                    else:
                        # 空き台は基本情報のみ
                        result['units'][unit_id] = {
                            'unit_id': unit_id,
                            'total_start': games,
                            'art': 0,
                            'bb': 0,
                            'rb': 0,
                            'status': 'empty' if unit_id in result['empty'] else 'unknown',
                        }
                
                result['fetched_at'] = now_jst().isoformat()
                logger.info(f"✓ {store_key}: {len(result['playing'])}台遊技中, {len(result['empty'])}台空き")
                
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"✗ {store_key}: {e}")
        
        return result
    
    def fetch_all_daidata(self, stores: Dict = None, parallel: int = 1) -> Dict[str, Any]:
        """
        全daidata店舗を取得
        
        Args:
            stores: 店舗設定（デフォルトはDAIDATA_STORES）
            parallel: 並列数（1=直列）
        """
        stores = stores or DAIDATA_STORES
        results = {}
        
        if parallel <= 1:
            # 直列実行
            for store_key, config in stores.items():
                results[store_key] = self.fetch_store_daidata(store_key, config)
        else:
            # 並列実行
            with ThreadPoolExecutor(max_workers=parallel) as executor:
                futures = {
                    executor.submit(self.fetch_store_daidata, k, v): k
                    for k, v in stores.items()
                }
                for future in as_completed(futures):
                    store_key = futures[future]
                    try:
                        results[store_key] = future.result()
                    except Exception as e:
                        results[store_key] = {'error': str(e)}
        
        return results
    
    def to_v1_format(self, results: Dict) -> Dict:
        """
        v1のavailability.json形式に変換
        """
        v1_data = {
            'last_updated': now_jst().isoformat(),
            'stores': {}
        }
        
        for store_key, data in results.items():
            if data.get('error'):
                continue
            
            store_data = {
                'name': data.get('name', store_key),
                'units': [],
            }
            
            for unit_id, unit_data in data.get('units', {}).items():
                store_data['units'].append({
                    'unit_id': unit_id,
                    'art': unit_data.get('art', 0),
                    'bb': unit_data.get('bb', 0),
                    'rb': unit_data.get('rb', 0),
                    'total_start': unit_data.get('total_start', 0),
                    'games': unit_data.get('total_start', 0),
                    'status': unit_data.get('status', 'unknown'),
                })
            
            v1_data['stores'][store_key] = store_data
        
        return v1_data
    
    def save_availability(self, results: Dict, path: Path = None):
        """availability.jsonに保存"""
        path = path or ROOT / 'data' / 'availability.json'
        v1_data = self.to_v1_format(results)
        
        # 既存データとマージ
        if path.exists():
            try:
                with open(path) as f:
                    existing = json.load(f)
                for k, v in existing.get('stores', {}).items():
                    if k not in v1_data['stores']:
                        v1_data['stores'][k] = v
            except:
                pass
        
        with open(path, 'w') as f:
            json.dump(v1_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Saved to {path}")


def discover_all_units() -> Dict[str, List[str]]:
    """
    全店舗の台番号を自動検出
    """
    discovery = DaidataDiscovery(headless=True)
    updates = {}
    
    logger.info("=== 台番号自動検出 ===")
    
    with discovery.browser_session():
        for store_key, config in DAIDATA_STORES.items():
            hall_id = config['hall_id']
            machine_key = 'sbj' if 'sbj' in store_key else 'hokuto2'
            expected = config.get('units', [])
            
            result = discovery.discover_units(hall_id, machine_key)
            detected = [u['unit_id'] for u in result.get('units', [])]
            
            if set(expected) != set(detected):
                logger.warning(f"⚠️ {store_key}: 台番号変更検出")
                logger.warning(f"   設定: {expected}")
                logger.warning(f"   検出: {detected}")
                updates[store_key] = detected
            else:
                logger.info(f"✓ {store_key}: OK ({len(detected)}台)")
    
    return updates


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='v2統合スクレイパー')
    parser.add_argument('--discover', action='store_true', help='台番号自動検出のみ')
    parser.add_argument('--sbj-only', action='store_true', help='SBJのみ')
    parser.add_argument('--hokuto-only', action='store_true', help='北斗のみ')
    parser.add_argument('--parallel', type=int, default=1, help='並列数')
    parser.add_argument('--store', type=str, help='特定店舗のみ')
    args = parser.parse_args()
    
    if args.discover:
        # 台番号検出のみ
        updates = discover_all_units()
        if updates:
            print(f"\n⚠️ {len(updates)}店舗で台番号変更を検出")
            for store, units in updates.items():
                print(f"  {store}: {units}")
        else:
            print("\n✅ 全店舗の台番号OK")
        return
    
    # フィルタリング
    stores = DAIDATA_STORES.copy()
    if args.sbj_only:
        stores = {k: v for k, v in stores.items() if 'sbj' in k}
    elif args.hokuto_only:
        stores = {k: v for k, v in stores.items() if 'hokuto' in k}
    if args.store:
        stores = {k: v for k, v in stores.items() if args.store in k}
    
    # 取得実行
    fetcher = V2Fetcher(headless=True, discover=False)
    start = time.time()
    
    results = fetcher.fetch_all_daidata(stores, parallel=args.parallel)
    fetcher.save_availability(results)
    
    elapsed = time.time() - start
    success = sum(1 for r in results.values() if not r.get('error'))
    logger.info(f"完了: {success}/{len(stores)}店舗, {elapsed:.1f}秒")


if __name__ == '__main__':
    main()
