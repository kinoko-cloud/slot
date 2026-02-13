#!/usr/bin/env python3
"""欠損データ補完スクリプト"""
import json
import re
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

JST = timezone(timedelta(hours=9))

# 補完対象
TARGETS = [
    {'hall_id': '100196', 'store': 'ueno_espass_sbj', 'units': ['3110', '3111', '3112', '3113']},
    {'hall_id': '100930', 'store': 'shibuya_honkan_espass_sbj', 'units': ['3095', '3096', '3097']},
]

def fetch_unit_history(page, hall_id: str, unit_id: str) -> dict:
    """台の全履歴を取得"""
    url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}"
    page.goto(url, timeout=30000)
    time.sleep(1)
    
    # 規約同意
    try:
        agree_btn = page.locator('button:has-text("同意する")')
        if agree_btn.count() > 0:
            agree_btn.click()
            page.wait_for_load_state('networkidle', timeout=10000)
            time.sleep(1)
    except:
        pass
    
    # 機種名取得
    machine_name = ""
    try:
        machine_el = page.locator('.machine-name, h2, h3').first
        if machine_el.count() > 0:
            machine_name = machine_el.inner_text().strip()
    except:
        pass
    
    # 詳細リンクを探す
    detail_links = page.locator('a[href*="target_date"]').all()
    
    result = {
        'unit_id': unit_id,
        'hall_id': hall_id,
        'machine_name': machine_name,
        'fetched_at': datetime.now(JST).isoformat(),
        'days': []
    }
    
    for link in detail_links:
        href = link.get_attribute('href')
        date_match = re.search(r'target_date=(\d{4}-\d{2}-\d{2})', href)
        if not date_match:
            continue
        
        date = date_match.group(1)
        
        # 詳細ページへ
        if href.startswith('http'):
            detail_url = href
        else:
            detail_url = f"https://daidata.goraggio.com{href}"
        page.goto(detail_url, timeout=30000)
        time.sleep(0.5)
        
        # 規約同意
        try:
            agree_btn = page.locator('button:has-text("同意する")')
            if agree_btn.count() > 0:
                agree_btn.click()
                page.wait_for_load_state('networkidle', timeout=10000)
                time.sleep(1)
        except:
            pass
        
        # データ抽出
        day_data = {'date': date, 'history': []}
        
        # サマリー
        try:
            text = page.locator('body').inner_text()
            bb_m = re.search(r'BB[：:]\s*(\d+)', text)
            rb_m = re.search(r'RB[：:]\s*(\d+)', text)
            art_m = re.search(r'ART[：:]\s*(\d+)', text)
            if bb_m: day_data['bb'] = int(bb_m.group(1))
            if rb_m: day_data['rb'] = int(rb_m.group(1))
            if art_m: day_data['art'] = int(art_m.group(1))
        except:
            pass
        
        # 履歴テーブル
        rows = page.locator('table tr').all()
        for row in rows[1:]:  # ヘッダースキップ
            cells = row.locator('td').all()
            if len(cells) >= 4:
                try:
                    history_item = {
                        'start': int(cells[0].inner_text().strip() or 0),
                        'type': cells[1].inner_text().strip(),
                        'medals': int(cells[2].inner_text().strip() or 0),
                        'time': cells[3].inner_text().strip() if len(cells) > 3 else ''
                    }
                    day_data['history'].append(history_item)
                except:
                    pass
        
        result['days'].append(day_data)
        print(f"  {date}: {len(day_data.get('history', []))}件")
        
        # 少し待機（ページ遷移の問題回避）
        time.sleep(1)
    
    return result

def update_history_file(store: str, unit_id: str, new_data: dict):
    """data/history/のファイルを更新"""
    history_dir = Path(f'data/history/{store}')
    history_dir.mkdir(parents=True, exist_ok=True)
    
    file_path = history_dir / f'{unit_id}.json'
    
    if file_path.exists():
        with open(file_path) as f:
            existing = json.load(f)
    else:
        existing = {'store_key': store, 'unit_id': unit_id, 'days': []}
    
    # 既存の日付セット
    existing_dates = {d['date'] for d in existing.get('days', [])}
    
    # 新しいデータをマージ
    added = 0
    for day in new_data.get('days', []):
        if day['date'] not in existing_dates:
            existing['days'].append(day)
            added += 1
    
    # 日付順にソート
    existing['days'].sort(key=lambda x: x['date'], reverse=True)
    existing['last_updated'] = datetime.now(JST).isoformat()
    
    with open(file_path, 'w') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    
    return added

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        for target in TARGETS:
            print(f"\n=== {target['store']} (hall_id={target['hall_id']}) ===")
            
            for unit in target['units']:
                print(f"\n台{unit}:")
                try:
                    data = fetch_unit_history(page, target['hall_id'], unit)
                    
                    # rawに保存
                    ts = datetime.now(JST).strftime('%Y%m%d_%H%M')
                    raw_path = Path(f"data/raw/sbj_{unit}_history_{ts}.json")
                    with open(raw_path, 'w') as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    print(f"  -> raw保存: {raw_path.name}")
                    
                    # historyに統合
                    added = update_history_file(target['store'], unit, data)
                    print(f"  -> history更新: {added}日追加")
                    
                except Exception as e:
                    print(f"  ERROR: {e}")
        
        browser.close()
    
    print("\n完了")

if __name__ == '__main__':
    main()
