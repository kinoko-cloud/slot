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
from papimo.scraper import PapimoScraper, PAPIMO_STORES as PAPIMO_CONFIG
from common.base import setup_logger, now_jst
from common.games_cache import get_changed_units, save_cache

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
        self._previous_availability = self._load_previous_availability()
        
    def _load_previous_availability(self) -> Dict:
        """前回のavailability.jsonを読み込む"""
        avail_path = ROOT / 'data' / 'availability.json'
        if avail_path.exists():
            try:
                with open(avail_path) as f:
                    return json.load(f)
            except:
                pass
        return {}
    
    def _get_previous_unit_data(self, store_key: str, unit_id: str) -> Dict:
        """前回の台データを取得"""
        stores = self._previous_availability.get('stores', {})
        store = stores.get(store_key, {})
        units = store.get('units', [])
        for u in units:
            if u.get('unit_id') == unit_id:
                return u
        return {}
        
    def fetch_store_daidata(self, store_key: str, config: Dict) -> Dict[str, Any]:
        """
        1店舗のdaidataデータ取得
        
        1. 一覧ページでG数＋空き/遊技中を一括取得
        2. G数が変化した台のみ詳細取得（差分取得）
        3. G数変化なしの台は前回のキャッシュを使用
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
            'changed_count': 0,
            'skipped_count': 0,
            'fetched_at': None,
            'error': None,
        }
        
        try:
            scraper = DaidataScraper(headless=self.headless)
            
            with scraper.browser_session():
                # 一覧ページでG数＋空き/遊技中を取得
                list_data = scraper.fetch_list_with_availability(
                    hall_id, model_encoded, expected_units
                )
                
                result['playing'] = list_data.get('playing', [])
                result['empty'] = list_data.get('empty', [])
                
                # G数マップ
                games_map = list_data.get('games', {})
                
                # G数が変化した台を特定
                changed_units = get_changed_units(store_key, games_map)
                
                for unit_id in expected_units:
                    games = games_map.get(unit_id, 0)
                    
                    # G数が変化した台のみ詳細取得
                    if unit_id in changed_units:
                        detail = scraper.fetch_realtime(hall_id, unit_id)
                        result['units'][unit_id] = detail
                        result['changed_count'] += 1
                    else:
                        # G数変化なし → 前回のデータを使用
                        # availability.jsonから前回のデータを取得
                        prev_data = self._get_previous_unit_data(store_key, unit_id)
                        result['units'][unit_id] = {
                            'unit_id': unit_id,
                            'total_start': games,
                            'art': prev_data.get('art', 0),
                            'bb': prev_data.get('bb', 0),
                            'rb': prev_data.get('rb', 0),
                            'final_start': prev_data.get('final_start', 0),
                            'diff_medals': prev_data.get('diff_medals', 0),
                            'cached': True,
                            'status': 'empty' if unit_id in result['empty'] else 'playing',
                        }
                        result['skipped_count'] += 1
                
                result['fetched_at'] = now_jst().isoformat()
                logger.info(f"✓ {store_key}: {result['changed_count']}台取得, {result['skipped_count']}台スキップ (G数変化なし)")
                
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"✗ {store_key}: {e}")
        
        return result
    
    def fetch_store_papimo(self, store_key: str, machine_key: str) -> Dict[str, Any]:
        """
        1店舗のpapimoデータ取得（リアルタイム）
        """
        result = {
            'store_key': f"{store_key}_{machine_key}",
            'name': f"アイランド秋葉原 {machine_key.upper()}",
            'units': {},
            'fetched_at': None,
            'error': None,
        }
        
        try:
            scraper = PapimoScraper(headless=self.headless)
            
            # 最新1日分だけ取得（リアルタイム用）
            data = scraper.fetch(store_key=store_key, machine_key=machine_key, days_back=1)
            
            for unit_data in data.get('units', []):
                unit_id = unit_data.get('unit_id')
                days = unit_data.get('days', [])
                
                if days:
                    today = days[0]
                    result['units'][unit_id] = {
                        'unit_id': unit_id,
                        'art': today.get('art', 0),
                        'bb': today.get('bb', 0),
                        'rb': today.get('rb', 0),
                        'total_start': today.get('total_start', 0),
                        'today_history': today.get('history', []),
                    }
                else:
                    result['units'][unit_id] = {
                        'unit_id': unit_id,
                        'art': 0,
                        'bb': 0,
                        'rb': 0,
                        'total_start': 0,
                        'today_history': [],
                    }
            
            result['fetched_at'] = now_jst().isoformat()
            logger.info(f"✓ {result['store_key']}: {len(result['units'])}台取得")
            
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"✗ {store_key}_{machine_key}: {e}")
        
        return result
    
    def fetch_all_papimo(self) -> Dict[str, Any]:
        """papimo全店舗を取得"""
        results = {}
        
        for store_key, config in PAPIMO_CONFIG.items():
            for machine_key in config.get('machines', {}).keys():
                full_key = f"{store_key}_{machine_key}"
                results[full_key] = self.fetch_store_papimo(store_key, machine_key)
        
        return results
    
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
        ts = now_jst().isoformat()
        v1_data = {
            'last_updated': ts,
            'fetched_at': ts,  # ヘルスチェック用
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
    
    Returns:
        {store_key: detected_units} - 変更があった店舗のみ
    """
    discovery = DaidataDiscovery(headless=True)
    updates = {}
    
    logger.info("=== 台番号自動検出 ===")
    
    with discovery.browser_session():
        for store_key, config in DAIDATA_STORES.items():
            hall_id = config['hall_id']
            machine_key = 'sbj' if 'sbj' in store_key else 'hokuto2'
            expected = set(config.get('units', []))
            
            result = discovery.discover_units(hall_id, machine_key)
            detected = set(u['unit_id'] for u in result.get('units', []))
            machine_name = result.get('machine_name', '')
            
            # 差分を計算
            missing = expected - detected  # 設定にあるが検出されない（消えた台）
            added = detected - expected    # 検出されたが設定にない（増えた台）
            
            if missing or added:
                logger.warning(f"⚠️ {store_key}: 台番号変更検出 [{machine_name}]")
                if missing:
                    logger.warning(f"   🔴 消えた台: {sorted(missing)}")
                if added:
                    logger.warning(f"   🟢 増えた台: {sorted(added)}")
                logger.warning(f"   設定: {sorted(expected)}")
                logger.warning(f"   検出: {sorted(detected)}")
                updates[store_key] = sorted(detected)
            else:
                logger.info(f"✓ {store_key}: OK ({len(detected)}台) [{machine_name}]")
    
    return updates


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='v2統合スクレイパー')
    parser.add_argument('--discover', action='store_true', help='台番号自動検出のみ')
    parser.add_argument('--sbj-only', action='store_true', help='SBJのみ')
    parser.add_argument('--hokuto-only', action='store_true', help='北斗のみ')
    parser.add_argument('--daidata-only', action='store_true', help='daidataのみ（papimoスキップ）')
    parser.add_argument('--papimo-only', action='store_true', help='papimoのみ')
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
    
    fetcher = V2Fetcher(headless=True, discover=False)
    start = time.time()
    all_results = {}
    
    # daidata取得
    if not args.papimo_only:
        stores = DAIDATA_STORES.copy()
        if args.sbj_only:
            stores = {k: v for k, v in stores.items() if 'sbj' in k}
        elif args.hokuto_only:
            stores = {k: v for k, v in stores.items() if 'hokuto' in k}
        if args.store:
            stores = {k: v for k, v in stores.items() if args.store in k}
        
        daidata_results = fetcher.fetch_all_daidata(stores, parallel=args.parallel)
        all_results.update(daidata_results)
    
    # papimo取得
    if not args.daidata_only and not args.sbj_only and not args.hokuto_only:
        papimo_results = fetcher.fetch_all_papimo()
        all_results.update(papimo_results)
    
    # 保存
    fetcher.save_availability(all_results)
    
    elapsed = time.time() - start
    success = sum(1 for r in all_results.values() if not r.get('error'))
    logger.info(f"完了: {success}/{len(all_results)}店舗, {elapsed:.1f}秒")


if __name__ == '__main__':
    main()
