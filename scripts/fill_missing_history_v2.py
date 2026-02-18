#!/usr/bin/env python3
"""
欠落historyデータを高速補完 v2

効率化:
- ブラウザ1回起動で全台処理
- 同じhall_id内は「次の台」リンクで遷移（URL再読込最小化）
- 欠落日のみ「詳細を見る」で取得
- 取得後、ページに表示されている台番号・日付を検証
"""

import sys
import json
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import time

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import sync_playwright
from config.stores import DAIDATA_STORES
from scrapers.daidata_detail_history import extract_day_history

JST = timezone(timedelta(hours=9))

REMOVE_ADS_SCRIPT = """
() => {
    document.querySelectorAll('#gn_interstitial_outer_area, .yads_ad_item, [id*="google_ads"]').forEach(el => el.remove());
}
"""


def get_hall_id(store_key: str) -> str:
    """store_keyからhall_idを取得"""
    base_store = store_key.rsplit('_', 1)[0]
    if base_store in DAIDATA_STORES:
        return DAIDATA_STORES[base_store].get('hall_id')
    matched = None
    matched_len = 0
    for key, config in DAIDATA_STORES.items():
        if key in base_store and len(key) > matched_len:
            matched = config.get('hall_id')
            matched_len = len(key)
    return matched


def verify_page_unit(page, expected_unit_id: str) -> bool:
    """ページに表示されている台番号が期待通りか検証"""
    text = page.inner_text('body')
    # パターン: "3011番台" or "台番号: 3011"
    pattern = rf'\b{expected_unit_id}番台|\b台番号[:\s]*{expected_unit_id}\b'
    return bool(re.search(pattern, text))


def verify_page_date(page, expected_date: str) -> bool:
    """ページに表示されている日付が期待通りか検証"""
    text = page.inner_text('body')
    # expected_date: "2026-02-18" → "2月18日"
    dt = datetime.strptime(expected_date, '%Y-%m-%d')
    date_str = f'{dt.month}月{dt.day}日'
    return date_str in text


def navigate_to_unit(page, hall_id: str, unit_id: str, need_agree: bool = False) -> bool:
    """台の詳細ページに遷移"""
    # まず現在のページから「{unit_id}番台」リンクを探す
    link = page.locator(f'a:has-text("{unit_id}番台")').first
    if link.count() > 0:
        try:
            link.click()
            page.wait_for_timeout(1500)
            page.evaluate(REMOVE_ADS_SCRIPT)
            if verify_page_unit(page, unit_id):
                return True
        except:
            pass
    
    # リンクがない場合はURL直接アクセス
    url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}"
    page.goto(url, wait_until='load', timeout=30000)
    page.wait_for_timeout(1500)
    page.evaluate(REMOVE_ADS_SCRIPT)
    
    if need_agree:
        try:
            agree_btn = page.locator('button:has-text("利用規約に同意する")')
            if agree_btn.count() > 0 and agree_btn.first.is_visible():
                agree_btn.first.click()
                page.wait_for_timeout(2000)
                page.evaluate(REMOVE_ADS_SCRIPT)
        except:
            pass
    
    return verify_page_unit(page, unit_id)


def fetch_day_data(page, hall_id: str, unit_id: str, target_date: str) -> dict:
    """特定日のデータを取得（「詳細を見る」リンク経由）"""
    # 「詳細を見る」リンクからtarget_date付きのURLを探す
    dt = datetime.strptime(target_date, '%Y-%m-%d')
    date_str = f'{dt.month}月{dt.day}日'
    
    # 方法1: 「詳細を見る」リンクをクリック
    detail_links = page.locator(f'a[href*="target_date={target_date}"]')
    if detail_links.count() > 0:
        try:
            detail_links.first.click()
            page.wait_for_timeout(1500)
            page.evaluate(REMOVE_ADS_SCRIPT)
        except:
            pass
    else:
        # 方法2: URL直接アクセス
        url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}&target_date={target_date}"
        page.goto(url, wait_until='load', timeout=30000)
        page.wait_for_timeout(1500)
        page.evaluate(REMOVE_ADS_SCRIPT)
    
    # ページテキストからデータ抽出
    text = page.inner_text('body')
    
    # 検証: 正しい日付のページか
    if not verify_page_date(page, target_date):
        return None
    
    # 検証: 正しい台番号のページか
    if not verify_page_unit(page, unit_id):
        return None
    
    # データ抽出
    day_data = extract_day_history(text, unit_id)
    if day_data:
        day_data['date'] = target_date
    
    return day_data


def fill_missing_v2(dry_run: bool = False, limit: int = None):
    """高速補完 v2"""
    
    missing_file = Path('data/missing_history.json')
    if not missing_file.exists():
        print("❌ data/missing_history.jsonが見つかりません")
        return
    
    with open(missing_file) as f:
        missing = json.load(f)
    
    print(f"欠落データ: {len(missing)}件")
    
    # hall_id でグループ化し、同じhall_id内は台番号順にソート
    by_hall = defaultdict(list)
    for m in missing:
        hall_id = get_hall_id(m['store'])
        if hall_id:
            by_hall[hall_id].append(m)
    
    # 各hall_id内で台番号順にソート
    for hall_id in by_hall:
        by_hall[hall_id].sort(key=lambda x: x['unit_id'])
    
    print(f"ホール数: {len(by_hall)}")
    
    if limit:
        # 制限付きの場合、最初のhall_idのみ
        first_hall = list(by_hall.keys())[0]
        by_hall = {first_hall: by_hall[first_hall][:limit]}
        print(f"制限: {limit}件")
    
    if dry_run:
        print("\n=== DRY RUN ===")
        for hall_id, items in by_hall.items():
            print(f"\nHall {hall_id}: {len(items)}件")
            for item in items[:5]:
                print(f"  {item['unit_id']} ({item['date']})")
        return
    
    filled = 0
    failed = 0
    start_time = time.time()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        
        first_access = True
        
        for hall_id, items in by_hall.items():
            print(f"\n=== Hall {hall_id}: {len(items)}件 ===")
            
            current_unit = None
            
            for item in items:
                store_key = item['store']
                unit_id = item['unit_id']
                target_date = item['date']
                
                print(f"  {unit_id} ({target_date})...", end=" ", flush=True)
                
                # 台が変わった場合、または最初のアクセス
                if current_unit != unit_id:
                    if not navigate_to_unit(page, hall_id, unit_id, need_agree=first_access):
                        print("❌ 台遷移失敗")
                        failed += 1
                        continue
                    first_access = False
                    current_unit = unit_id
                
                # 特定日のデータ取得
                result = fetch_day_data(page, hall_id, unit_id, target_date)
                
                if not result:
                    print("❌ 取得失敗 (result=None)")
                    failed += 1
                    continue
                
                art = result.get('art', 0)
                games = result.get('total_start', 0) or result.get('games', 0)
                hist = result.get('history', [])
                
                # 判定
                if art == 0 and games == 0:
                    print(f"⏭️ 稼働なし")
                    continue
                
                if art > 0 and not hist:
                    print(f"❌ 取得失敗 (art={art}, hist=0)")
                    failed += 1
                    continue
                
                # historyファイルに反映
                history_file = Path(f'data/history/{store_key}/{unit_id}.json')
                if not history_file.exists():
                    print("❌ ファイルなし")
                    failed += 1
                    continue
                
                with open(history_file) as f:
                    history_data = json.load(f)
                
                updated = False
                for day in history_data.get('days', []):
                    if day.get('date') == target_date:
                        if not day.get('history') or len(day.get('history', [])) == 0:
                            day['history'] = hist
                            day['diff_medals'] = result.get('diff_medals')
                            day['max_rensa'] = result.get('max_rensa')
                            day['max_medals'] = result.get('max_medals')
                            day['games'] = games
                            
                            # diff_medalsを計算（元データにない場合）
                            if day['diff_medals'] is None and hist:
                                total_medals = sum(h.get('medals', 0) for h in hist)
                                total_start = sum(h.get('start', 0) for h in hist)
                                day['diff_medals'] = total_medals - (total_start * 3)
                            
                            # max_rensaを計算
                            if hist:
                                max_rensa = 1
                                current_rensa = 1
                                sorted_hist = sorted(hist, key=lambda x: x.get('time', ''))
                                for i, h in enumerate(sorted_hist):
                                    if i > 0 and h.get('type') == 'ART' and h.get('start', 999) <= 30:
                                        current_rensa += 1
                                    else:
                                        max_rensa = max(max_rensa, current_rensa)
                                        current_rensa = 1
                                day['max_rensa'] = max(max_rensa, current_rensa)
                                day['max_medals'] = max((h.get('medals', 0) for h in hist), default=0)
                            
                            updated = True
                            print(f"✅ hist={len(hist)}")
                            filled += 1
                        else:
                            print("⏭️ 既存あり")
                        break
                else:
                    print("❌ 日付なし")
                    failed += 1
                    continue
                
                if updated:
                    with open(history_file, 'w') as f:
                        json.dump(history_data, f, ensure_ascii=False, indent=2)
                
                # 元の概要ページに戻る（次の日付取得のため）
                page.go_back()
                page.wait_for_timeout(500)
        
        browser.close()
    
    elapsed = time.time() - start_time
    print(f"\n=== 完了 ===")
    print(f"補完成功: {filled}件")
    print(f"失敗: {failed}件")
    print(f"所要時間: {elapsed:.1f}秒 ({elapsed/max(filled+failed, 1):.1f}秒/件)")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='実際には取得しない')
    parser.add_argument('--limit', type=int, help='取得件数制限')
    args = parser.parse_args()
    
    fill_missing_v2(dry_run=args.dry_run, limit=args.limit)
