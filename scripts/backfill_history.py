#!/usr/bin/env python3
"""
履歴データ欠落補完スクリプト

特定の店舗・台・日付の履歴データをdaidataから取得して補完する。
"""
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / 'scripts' / 'scrapers_v2'))

from daidata.scraper import DaidataScraper

# 直接JSONファイルを操作（インポート問題を回避）
HISTORY_DIR = PROJECT_ROOT / 'data' / 'history'
STORES_FILE = PROJECT_ROOT / 'config' / 'stores.py'

def load_stores():
    """stores.pyからDAIDATA_STORESを読み込み"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("stores", STORES_FILE)
    stores_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stores_module)
    return stores_module.DAIDATA_STORES

DAIDATA_STORES = load_stores()

def load_unit_history(store_key, unit_id):
    """履歴ファイルを読み込み"""
    path = HISTORY_DIR / store_key / f'{unit_id}.json'
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None

def save_unit_history(store_key, unit_id, data):
    """履歴ファイルを保存"""
    dir_path = HISTORY_DIR / store_key
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / f'{unit_id}.json'
    with open(path, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def backfill_store(store_key: str, target_dates: list = None, dry_run: bool = False):
    """
    指定店舗の欠落データを補完
    
    Args:
        store_key: 店舗キー（例: 'seibu_shinjuku_espass_sbj'）
        target_dates: 取得対象日付リスト（例: ['2026-02-12', '2026-02-13']）
        dry_run: Trueなら保存せずに表示のみ
    """
    # store_keyから物理店舗キーと機種を分離
    # 例: 'seibu_shinjuku_espass_sbj' → 'seibu_shinjuku_espass' + 'sbj'
    parts = store_key.rsplit('_', 1)
    if len(parts) != 2 or parts[1] not in ('sbj', 'hokuto2'):
        print(f"❌ 無効なstore_key: {store_key}")
        return
    
    base_store_key = parts[0]  # 例: 'seibu_shinjuku_espass'
    machine_key = parts[1]
    
    store = DAIDATA_STORES.get(base_store_key)
    if not store:
        print(f"❌ 店舗が見つかりません: {base_store_key}")
        return
    
    hall_id = store.get('hall_id')
    if not hall_id:
        print(f"❌ hall_idが設定されていません: {base_store_key}")
        return
    
    # 履歴DBから台番号リストを取得
    history_dir = Path(__file__).parent.parent / 'data' / 'history' / store_key
    if not history_dir.exists():
        print(f"❌ 履歴ディレクトリが見つかりません: {history_dir}")
        return
    
    unit_files = list(history_dir.glob('*.json'))
    if not unit_files:
        print(f"❌ 履歴ファイルが見つかりません: {history_dir}")
        return
    
    print(f"📂 {store_key}: {len(unit_files)}台")
    print(f"🏢 hall_id: {hall_id}")
    print(f"📅 対象日付: {target_dates or '全て'}")
    print()
    
    if not target_dates:
        # デフォルトで過去7日
        today = datetime.now()
        target_dates = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(1, 8)]
    
    # スクレイパー起動
    scraper = DaidataScraper(headless=True)
    
    updated_count = 0
    with scraper.browser_session('daidata'):
        for unit_file in unit_files:
            unit_id = unit_file.stem
            
            # 現在の履歴を読み込み
            current = load_unit_history(store_key, unit_id)
            current_dates = {d['date'] for d in current.get('days', []) if d.get('date')} if current else set()
            
            # 欠落日付を特定（art=0のデータも含める）
            current_days_map = {d['date']: d for d in current.get('days', []) if d.get('date')} if current else {}
            missing_dates = []
            for d in target_dates:
                if d not in current_dates:
                    missing_dates.append(d)
                elif current_days_map.get(d, {}).get('art', 0) == 0:
                    # art=0のデータも更新対象（実際には稼働していた可能性）
                    missing_dates.append(d)
            
            if not missing_dates:
                print(f"  ✅ {unit_id}: 欠落なし")
                continue
            
            print(f"  ⏳ {unit_id}: {len(missing_dates)}日分欠落 → 取得中...")
            
            # daidataから履歴取得
            history = scraper.fetch_history(hall_id, unit_id, days=7)
            
            if not history:
                print(f"  ⚠️ {unit_id}: データ取得失敗")
                continue
            
            # 欠落日付のデータを抽出
            new_days = []
            for day in history:
                if day.get('date') in missing_dates:
                    new_days.append(day)
            
            if not new_days:
                print(f"  ⚠️ {unit_id}: 対象日付のデータなし")
                continue
            
            # 現在の履歴にマージ
            if current:
                all_days = current.get('days', []) + new_days
            else:
                all_days = new_days
            
            # 日付でソート・重複除去
            seen = set()
            unique_days = []
            for d in sorted(all_days, key=lambda x: x.get('date', ''), reverse=True):
                if d.get('date') not in seen:
                    seen.add(d.get('date'))
                    unique_days.append(d)
            
            if dry_run:
                print(f"  🔍 {unit_id}: {len(new_days)}日分追加予定")
                for d in new_days:
                    print(f"      {d.get('date')}: art={d.get('art', 0)}, history={len(d.get('history', []))}件")
            else:
                # 保存
                save_data = {'days': unique_days}
                save_unit_history(store_key, unit_id, save_data)
                print(f"  ✅ {unit_id}: {len(new_days)}日分追加")
                for d in new_days:
                    print(f"      {d.get('date')}: art={d.get('art', 0)}")
                updated_count += 1
    
    print()
    print(f"✅ 完了: {updated_count}台更新")


def main():
    parser = argparse.ArgumentParser(description='履歴データ欠落補完')
    parser.add_argument('store_key', help='店舗キー（例: seibu_shinjuku_espass_sbj）')
    parser.add_argument('--dates', nargs='+', help='対象日付（例: 2026-02-12 2026-02-13）')
    parser.add_argument('--dry-run', action='store_true', help='保存せずに確認のみ')
    
    args = parser.parse_args()
    
    backfill_store(args.store_key, args.dates, args.dry_run)


if __name__ == '__main__':
    main()
