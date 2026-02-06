#!/usr/bin/env python3
"""
リアルタイムデータをhistoryに同期

全店舗のリアルタイムデータを取得し、data/history/に蓄積する。
閉店後に実行することで、当日の確定データをhistoryに保存。
"""
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.rankings import STORES, MACHINES
from analysis.history_accumulator import _calc_history_stats
from analysis.diff_medals_estimator import estimate_diff_medals

HISTORY_DIR = PROJECT_ROOT / 'data' / 'history'


def get_machine_key_from_store(store_key: str) -> str:
    """store_keyから機種キーを推定"""
    if '_sbj' in store_key:
        return 'sbj'
    elif '_hokuto' in store_key:
        return 'hokuto2'
    return 'sbj'


def scrape_daidata_realtime(hall_id: str, unit_ids: list) -> dict:
    """DAIDATAからリアルタイムデータを取得"""
    import requests
    from bs4 import BeautifulSoup
    
    results = {}
    
    for unit_id in unit_ids:
        url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}"
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code != 200:
                continue
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # 概要データを取得
            # BB/RB/ART/G数などをスクレイプ
            data = {'unit_id': unit_id}
            
            # 当日データを取得
            # ...（簡略化）
            
            results[unit_id] = data
        except Exception as e:
            print(f"  Error {unit_id}: {e}")
    
    return results


def scrape_papimo_realtime(hall_id: str, unit_ids: list) -> dict:
    """PAPILOからリアルタイムデータを取得"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not available")
        return {}
    
    results = {}
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        for unit_id in unit_ids:
            try:
                url = f"https://papimo.jp/hall/{hall_id}/unit/{unit_id}"
                page.goto(url, timeout=30000)
                page.wait_for_timeout(2000)
                
                # データ取得
                data = {'unit_id': unit_id}
                
                # ART回数
                art_el = page.query_selector('.art-count')
                if art_el:
                    data['art'] = int(art_el.inner_text())
                
                results[unit_id] = data
            except Exception as e:
                print(f"  Error {unit_id}: {e}")
        
        browser.close()
    
    return results


def load_availability_json() -> dict:
    """既存のavailability.jsonを読み込む"""
    avail_path = PROJECT_ROOT / 'data' / 'availability.json'
    if avail_path.exists():
        with open(avail_path) as f:
            return json.load(f)
    return {}


def sync_store_to_history(store_key: str, store_data: dict, date_str: str):
    """1店舗のデータをhistoryに同期"""
    machine_key = get_machine_key_from_store(store_key)
    
    store_history_dir = HISTORY_DIR / store_key
    store_history_dir.mkdir(parents=True, exist_ok=True)
    
    units = store_data.get('units', [])
    updated = 0
    
    for unit_data in units:
        unit_id = str(unit_data.get('unit_id', ''))
        if not unit_id:
            continue
        
        # 履歴ファイル
        history_file = store_history_dir / f'{unit_id}.json'
        if history_file.exists():
            with open(history_file) as f:
                unit_history = json.load(f)
        else:
            unit_history = {
                'store_key': store_key,
                'unit_id': unit_id,
                'machine_key': machine_key,
                'days': [],
                'last_updated': ''
            }
        
        # 既存日付確認
        existing_dates = {d.get('date') for d in unit_history.get('days', [])}
        if date_str in existing_dates:
            continue
        
        # 当日データ構築
        art = unit_data.get('art', 0) or 0
        rb = unit_data.get('rb', 0) or 0
        total_games = unit_data.get('total_start', 0) or 0
        today_history = unit_data.get('today_history', [])
        max_medals = unit_data.get('max_medals', 0) or 0
        today_max_rensa = unit_data.get('today_max_rensa', 0) or 0
        
        # 確率
        prob = total_games / art if art > 0 else None
        
        # 差枚推定
        if today_history and total_games > 0:
            medals_total = sum(h.get('medals', 0) for h in today_history)
            diff_medals = estimate_diff_medals(medals_total, total_games, machine_key)
        else:
            diff_medals = None
        
        # 好調判定
        good_prob = MACHINES.get(machine_key, {}).get('good_prob', 130)
        is_good = prob is not None and prob < good_prob
        
        # 連チャン再計算
        max_rensa, calc_max_medals = _calc_history_stats(today_history)
        if calc_max_medals > 0:
            max_medals = calc_max_medals
        if max_rensa > 0:
            today_max_rensa = max_rensa
        
        day_entry = {
            'date': date_str,
            'art': art,
            'rb': rb,
            'games': total_games,
            'prob': round(prob, 1) if prob else None,
            'is_good': is_good,
            'history': today_history,
            'max_rensa': today_max_rensa,
            'max_medals': max_medals,
            'diff_medals': diff_medals
        }
        
        unit_history['days'].append(day_entry)
        unit_history['last_updated'] = datetime.now().isoformat()
        
        with open(history_file, 'w') as f:
            json.dump(unit_history, f, ensure_ascii=False, indent=2)
        
        updated += 1
    
    return updated


def main():
    print(f"=== リアルタイム→History同期 ===")
    print(f"実行時刻: {datetime.now()}")
    
    # availability.jsonを読み込み
    avail = load_availability_json()
    fetched_at = avail.get('fetched_at', '')
    date_str = fetched_at[:10] if fetched_at else datetime.now().strftime('%Y-%m-%d')
    print(f"データ日付: {date_str}")
    
    stores = avail.get('stores', {})
    total_updated = 0
    
    for store_key, store_data in stores.items():
        updated = sync_store_to_history(store_key, store_data, date_str)
        if updated > 0:
            print(f"  {store_key}: {updated}台更新")
            total_updated += updated
    
    print(f"\n合計: {total_updated}台更新")
    
    # 結果確認
    print("\n=== 2/2データ状況 ===")
    for store_dir in sorted(HISTORY_DIR.iterdir()):
        if not store_dir.is_dir():
            continue
        has_date = 0
        total = 0
        for f in store_dir.glob('*.json'):
            total += 1
            try:
                with open(f) as fp:
                    data = json.load(fp)
                dates = [d.get('date') for d in data.get('days', [])]
                if date_str in dates:
                    has_date += 1
            except:
                pass
        if total > 0:
            status = '✅' if has_date == total else f'⚠️ {has_date}/{total}'
            print(f"  {store_dir.name}: {status}")


if __name__ == '__main__':
    main()
