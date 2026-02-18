#!/usr/bin/env python3
"""
欠落historyデータを高速補完

改善点:
- ブラウザ1回起動で全台処理
- 同じhall_idの台をまとめて処理
- 欠落日のみ取得（7日全部取らない）
- 元のスクリプトの解析関数を使用
"""

import sys
import json
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

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
    # store_key例: shibuya_espass_sbj → shibuya_espass
    base_store = store_key.rsplit('_', 1)[0]  # 末尾の _sbj や _hokuto2 を除去
    
    # 完全一致を優先
    if base_store in DAIDATA_STORES:
        return DAIDATA_STORES[base_store].get('hall_id')
    
    # 部分一致（長いキーを優先してより具体的なマッチを得る）
    matched = None
    matched_len = 0
    for key, config in DAIDATA_STORES.items():
        if key in base_store and len(key) > matched_len:
            matched = config.get('hall_id')
            matched_len = len(key)
    
    return matched


def fetch_unit_day(page, hall_id: str, unit_id: str, target_date: str, need_agree: bool = True):
    """特定の台の特定日のhistoryを取得（ブラウザは使いまわし）"""
    url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}&target_date={target_date}"
    
    try:
        page.goto(url, wait_until='load', timeout=30000)
        page.wait_for_timeout(1500)
        page.evaluate(REMOVE_ADS_SCRIPT)
        
        # 規約同意（必要な場合のみ）
        if need_agree:
            try:
                agree_btn = page.locator('button:has-text("利用規約に同意する")')
                if agree_btn.count() > 0 and agree_btn.first.is_visible():
                    agree_btn.first.click()
                    page.wait_for_timeout(2000)
                    page.evaluate(REMOVE_ADS_SCRIPT)
            except:
                pass
        
        text = page.inner_text('body')
        
        # 元のスクリプトの解析関数を使用
        day_data = extract_day_history(text, unit_id)
        
        return day_data
        
    except Exception as e:
        print(f"❌ エラー: {e}")
        return None


def fill_missing_fast():
    """高速補完"""
    
    missing_file = Path('data/missing_history.json')
    if not missing_file.exists():
        print("❌ data/missing_history.jsonが見つかりません")
        return
    
    with open(missing_file) as f:
        missing = json.load(f)
    
    print(f"欠落データ: {len(missing)}件")
    
    # hall_id でグループ化
    by_hall = defaultdict(list)
    for m in missing:
        hall_id = get_hall_id(m['store'])
        if hall_id:
            by_hall[hall_id].append(m)
    
    print(f"ホール数: {len(by_hall)}")
    
    filled = 0
    failed = 0
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        
        first_access = True
        
        for hall_id, items in by_hall.items():
            print(f"\n=== Hall {hall_id}: {len(items)}件 ===")
            
            for item in items:
                store_key = item['store']
                unit_id = item['unit_id']
                target_date = item['date']
                
                print(f"  {unit_id} ({target_date})...", end=" ", flush=True)
                
                result = fetch_unit_day(page, hall_id, unit_id, target_date, need_agree=first_access)
                first_access = False
                
                if not result:
                    print("❌ 取得失敗 (result=None)")
                    failed += 1
                    continue
                
                art = result.get('art', 0)
                games = result.get('total_start', 0) or result.get('games', 0)
                hist = result.get('history', [])
                
                # art=0 && games=0 → 本当に稼働していない（正常、補完不要）
                # art=0 && games>0 → 稼働したがART未当選（正常データ）
                # art>0 && hist=0 → 取得失敗（補完が必要だったが取れなかった）
                if art == 0 and games == 0:
                    print(f"⏭️ 稼働なし (art=0, games=0)")
                    # 稼働なしは正常なので、historyに「稼働なし」として記録
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
                
                # 既存のdays辞書を作成
                updated = False
                for day in history_data.get('days', []):
                    if day.get('date') == target_date:
                        if not day.get('history') or len(day.get('history', [])) == 0:
                            day['history'] = result.get('history', [])
                            day['diff_medals'] = result.get('diff_medals')
                            day['max_rensa'] = result.get('max_rensa')
                            day['max_medals'] = result.get('max_medals')
                            day['games'] = result.get('total_start', 0)
                            
                            # diff_medalsを計算（元データにない場合）
                            if day['diff_medals'] is None and day['history']:
                                total_medals = sum(h.get('medals', 0) for h in day['history'])
                                total_start = sum(h.get('start', 0) for h in day['history'])
                                day['diff_medals'] = total_medals - (total_start * 3)
                            
                            # max_rensaを計算
                            if day['history']:
                                max_rensa = 1
                                current_rensa = 1
                                sorted_hist = sorted(day['history'], key=lambda x: x.get('time', ''))
                                for i, h in enumerate(sorted_hist):
                                    if i > 0 and h.get('type') == 'ART' and h.get('start', 999) <= 30:
                                        current_rensa += 1
                                    else:
                                        max_rensa = max(max_rensa, current_rensa)
                                        current_rensa = 1
                                day['max_rensa'] = max(max_rensa, current_rensa)
                                day['max_medals'] = max((h.get('medals', 0) for h in day['history']), default=0)
                            
                            updated = True
                            print(f"✅ hist={len(result.get('history', []))}")
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
        
        browser.close()
    
    print(f"\n=== 完了 ===")
    print(f"補完成功: {filled}件")
    print(f"失敗: {failed}件")


if __name__ == '__main__':
    fill_missing_fast()
