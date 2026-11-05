#!/usr/bin/env python3
"""欠損データ補完（20台）"""
import json
import re
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

JST = timezone(timedelta(hours=9))

TARGETS = [
    {'hall_id': '100952', 'store': 'akasaka_espass_sbj', 'units': ['2039', '2040', '2041']},
    {'hall_id': '100260', 'store': 'shinkoiwa_espass_sbj', 'units': ['2050', '2051', '2052']},
    {'hall_id': '100951', 'store': 'shinokubo_espass_sbj', 'units': ['3141', '3142', '3143', '3144']},
    {'hall_id': '100915', 'store': 'takadanobaba_espass_sbj', 'units': ['2060', '2061', '2062']},
    {'hall_id': '100196', 'store': 'ueno_espass_sbj', 'units': ['3110', '3111', '3112', '3113']},
    {'hall_id': '100947', 'store': 'ueno_honkan_espass_sbj', 'units': ['3125', '3126', '3127']},
]

def fetch_and_update(page, hall_id: str, unit_id: str, store: str):
    """台の履歴を取得してhistoryに統合"""
    url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}"
    
    try:
        page.goto(url, timeout=30000, wait_until='domcontentloaded')
        time.sleep(1)
        
        # 規約同意
        try:
            agree = page.locator('button:has-text("同意する")')
            if agree.count() > 0 and agree.is_visible():
                agree.click()
                time.sleep(2)
        except:
            pass
        
        # ページ再読み込み
        page.goto(url, timeout=30000, wait_until='domcontentloaded')
        time.sleep(1)
        
        # 詳細リンク取得
        links = page.locator('a[href*="target_date"]').all()
        
        days_data = []
        for link in links:
            href = link.get_attribute('href')
            date_match = re.search(r'target_date=(\d{4}-\d{2}-\d{2})', href)
            if not date_match:
                continue
            
            date = date_match.group(1)
            detail_url = f"https://daidata.goraggio.com{href}" if not href.startswith('http') else href
            
            page.goto(detail_url, timeout=30000, wait_until='domcontentloaded')
            time.sleep(0.5)
            
            # 同意ボタン
            try:
                agree = page.locator('button:has-text("同意する")')
                if agree.count() > 0 and agree.is_visible():
                    agree.click()
                    time.sleep(1)
            except:
                pass
            
            day_data = {'date': date, 'history': []}
            
            # サマリー抽出
            try:
                text = page.inner_text('body')
                for pattern, key in [(r'BB[：:]\s*(\d+)', 'bb'), (r'RB[：:]\s*(\d+)', 'rb'), (r'ART[：:]\s*(\d+)', 'art')]:
                    m = re.search(pattern, text)
                    if m:
                        day_data[key] = int(m.group(1))
            except:
                pass
            
            # 履歴テーブル
            try:
                rows = page.locator('table tr').all()
                for row in rows[1:]:
                    cells = row.locator('td').all()
                    if len(cells) >= 3:
                        try:
                            item = {
                                'start': int(cells[0].inner_text().strip() or '0'),
                                'type': cells[1].inner_text().strip(),
                                'medals': int(cells[2].inner_text().strip() or '0'),
                            }
                            if len(cells) > 3:
                                item['time'] = cells[3].inner_text().strip()
                            day_data['history'].append(item)
                        except:
                            pass
            except:
                pass
            
            days_data.append(day_data)
        
        if not days_data:
            return 0
        
        # history更新
        history_dir = Path(f'data/history/{store}')
        history_dir.mkdir(parents=True, exist_ok=True)
        history_file = history_dir / f'{unit_id}.json'
        
        if history_file.exists():
            with open(history_file) as f:
                existing = json.load(f)
        else:
            existing = {'store_key': store, 'unit_id': unit_id, 'days': []}
        
        existing_dates = {d['date'] for d in existing.get('days', [])}
        added = 0
        
        for day in days_data:
            if day['date'] not in existing_dates:
                existing['days'].append(day)
                added += 1
        
        if added > 0:
            existing['days'].sort(key=lambda x: x['date'], reverse=True)
            existing['last_updated'] = datetime.now(JST).isoformat()
            with open(history_file, 'w') as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        
        return added
    
    except Exception as e:
        print(f"    ERROR: {e}")
        return 0

def main():
    total = sum(len(t['units']) for t in TARGETS)
    done = 0
    added_total = 0
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        for target in TARGETS:
            print(f"\n【{target['store']}】hall_id={target['hall_id']}")
            
            for unit in target['units']:
                done += 1
                added = fetch_and_update(page, target['hall_id'], unit, target['store'])
                added_total += added
                print(f"  [{done}/{total}] 台{unit}: +{added}日")
        
        browser.close()
    
    print(f"\n完了: {added_total}日分追加")

if __name__ == '__main__':
    main()
