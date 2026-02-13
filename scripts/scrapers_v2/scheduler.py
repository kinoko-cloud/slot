#!/usr/bin/env python3
"""
scrapers_v2/scheduler.py - 統合スケジューラ

全データソース（daidata, papimo）を一括で取得・更新
"""
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from common.base import DataStore, setup_logger, now_jst, today_str
from daidata.scraper import DaidataScraper
from papimo.scraper import PapimoScraper, PAPIMO_STORES
from config import DAIDATA_CONFIG, SCRAPE_TARGETS, get_hall_id

# データ保存先
DATA_DIR = Path(__file__).parent.parent.parent / 'data'
HISTORY_DIR = DATA_DIR / 'history'

logger = setup_logger('scheduler')


def update_history_file(store_key: str, unit_id: str, new_days: list, machine_key: str = None):
    """履歴ファイルを更新"""
    # store_keyに機種が含まれていない場合は追加
    if machine_key and not store_key.endswith(f'_{machine_key}'):
        full_key = f"{store_key}_{machine_key}"
    else:
        full_key = store_key
    
    store_dir = HISTORY_DIR / full_key
    store_dir.mkdir(parents=True, exist_ok=True)
    
    filepath = store_dir / f"{unit_id}.json"
    
    # 既存データ読み込み
    if filepath.exists():
        with open(filepath) as f:
            existing = json.load(f)
    else:
        existing = {'store_key': full_key, 'unit_id': unit_id, 'days': []}
    
    existing_dates = {d['date'] for d in existing.get('days', [])}
    
    # 新規データ追加
    added = 0
    for day in new_days:
        if day.get('date') and day['date'] not in existing_dates:
            existing['days'].append(day)
            added += 1
    
    if added > 0:
        existing['days'].sort(key=lambda x: x['date'], reverse=True)
        existing['last_updated'] = now_jst().isoformat()
        with open(filepath, 'w') as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    
    return added


def run_daidata(stores: list = None, machines: list = None, 
                mode: str = 'realtime', days: int = 7):
    """daidataからデータ取得"""
    logger.info(f"=== DAIDATA {mode} ===")
    
    targets = SCRAPE_TARGETS.get('daidata', {})
    if stores:
        targets = {k: v for k, v in targets.items() if k in stores}
    
    scraper = DaidataScraper(headless=True)
    total_added = 0
    
    with scraper.browser_session():
        for store_key, machine_list in targets.items():
            hall_id = get_hall_id(store_key)
            if not hall_id:
                logger.warning(f"Unknown store: {store_key}")
                continue
            
            if machines:
                machine_list = [m for m in machine_list if m in machines]
            
            for machine_key in machine_list:
                logger.info(f"  {store_key}/{machine_key}")
                
                # 台番号を取得（既存の履歴ファイルから）
                full_key = f"{store_key}_{machine_key}"
                store_dir = HISTORY_DIR / full_key
                
                if store_dir.exists():
                    unit_ids = [f.stem for f in store_dir.glob('*.json')]
                else:
                    logger.warning(f"    No history dir: {store_dir}")
                    continue
                
                if not unit_ids:
                    continue
                
                for unit_id in unit_ids:
                    try:
                        if mode == 'realtime':
                            data = scraper.fetch_realtime(hall_id, unit_id)
                            # リアルタイムは1日分のみ
                            new_days = [{
                                'date': today_str(),
                                'bb': data.get('bb', 0),
                                'rb': data.get('rb', 0),
                                'art': data.get('art', 0),
                                'total_start': data.get('total_start', 0),
                                'final_start': data.get('final_start', 0),
                                'diff_medals': data.get('diff_medals'),
                            }]
                        else:
                            days_data = scraper.fetch_history(hall_id, unit_id, days)
                            new_days = days_data
                        
                        added = update_history_file(store_key, unit_id, new_days, machine_key)
                        if added > 0:
                            logger.info(f"    {unit_id}: +{added}日")
                            total_added += added
                            
                    except Exception as e:
                        logger.error(f"    {unit_id}: {e}")
    
    logger.info(f"DAIDATA完了: +{total_added}日")
    return total_added


def run_papimo(machines: list = None, days: int = 7):
    """papimoからデータ取得"""
    logger.info("=== PAPIMO ===")
    
    scraper = PapimoScraper(headless=True)
    total_added = 0
    
    store_key = 'island_akihabara'
    store = PAPIMO_STORES[store_key]
    
    machine_list = list(store['machines'].keys())
    if machines:
        machine_list = [m for m in machine_list if m in machines]
    
    for machine_key in machine_list:
        logger.info(f"  {store_key}/{machine_key}")
        
        result = scraper.fetch(
            store_key=store_key,
            machine_key=machine_key,
            days_back=days
        )
        
        for unit in result.get('units', []):
            unit_id = unit.get('unit_id')
            new_days = unit.get('days', [])
            
            if new_days:
                added = update_history_file(store_key, unit_id, new_days, machine_key)
                if added > 0:
                    logger.info(f"    {unit_id}: +{added}日")
                    total_added += added
    
    logger.info(f"PAPIMO完了: +{total_added}日")
    return total_added


def main():
    parser = argparse.ArgumentParser(description='スクレイピング統合スケジューラ')
    parser.add_argument('--source', choices=['daidata', 'papimo', 'all'], default='all',
                        help='データソース')
    parser.add_argument('--mode', choices=['realtime', 'history'], default='realtime',
                        help='取得モード')
    parser.add_argument('--stores', nargs='+', help='対象店舗（スペース区切り）')
    parser.add_argument('--machines', nargs='+', help='対象機種（スペース区切り）')
    parser.add_argument('--days', type=int, default=7, help='履歴取得日数')
    
    args = parser.parse_args()
    
    logger.info(f"スケジューラ開始: source={args.source}, mode={args.mode}")
    
    total = 0
    
    if args.source in ('daidata', 'all'):
        total += run_daidata(
            stores=args.stores,
            machines=args.machines,
            mode=args.mode,
            days=args.days
        )
    
    if args.source in ('papimo', 'all'):
        total += run_papimo(
            machines=args.machines,
            days=args.days
        )
    
    logger.info(f"完了: 合計 +{total}日")


if __name__ == '__main__':
    main()
