#!/usr/bin/env python3
"""
過去N日間の履歴データ欠損を検出・補完するスクリプト

GitHub Actionsで毎日深夜に実行し、欠損データを自動補完する。
"""
import json
import sys
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).parent / 'scrapers_v2'))
sys.path.insert(0, str(Path(__file__).parent))

from daidata.scraper import DaidataScraper
from fetch_daidata_availability import DAIDATA_STORES

JST = timezone(timedelta(hours=9))
HISTORY_DIR = Path(__file__).parent.parent / 'data' / 'history'


def parse_history_from_text(text):
    """テキストから履歴をパース"""
    pattern = r'(\d+)\s+(\d+)\s+(\d+)\s+(ART|RB|BB)\s+(\d{1,2}:\d{2})'
    matches = re.findall(pattern, text)
    history = []
    for m in matches:
        history.append({
            'hit_num': int(m[0]),
            'start': int(m[1]),
            'medals': int(m[2]),
            'type': m[3],
            'time': m[4],
        })
    history.sort(key=lambda x: x['time'])
    return history


def find_missing_dates(days_back=7):
    """過去N日間で欠損している日付を検出"""
    today = datetime.now(JST).date()
    expected_dates = set()
    for i in range(1, days_back + 1):
        d = today - timedelta(days=i)
        expected_dates.add(d.strftime('%Y-%m-%d'))
    
    missing = []
    
    for store_dir in HISTORY_DIR.iterdir():
        if not store_dir.is_dir():
            continue
        store_key = store_dir.name
        if store_key.startswith('island_'):
            continue  # papimoは別管理
        
        for hist_file in store_dir.glob('*.json'):
            unit_id = hist_file.stem
            try:
                with open(hist_file) as f:
                    data = json.load(f)
                
                existing_dates = set(d.get('date') for d in data.get('days', []))
                
                # 最古の日付より前は無視
                if existing_dates:
                    oldest = min(existing_dates)
                    relevant_expected = {d for d in expected_dates if d >= oldest}
                else:
                    relevant_expected = expected_dates
                
                # 欠損日付を検出
                for date in relevant_expected:
                    if date not in existing_dates:
                        # 前後の日付があるか確認（孤立した欠損のみ対象）
                        prev_date = (datetime.strptime(date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
                        next_date = (datetime.strptime(date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
                        
                        if prev_date in existing_dates or next_date in existing_dates:
                            missing.append((store_key, unit_id, date))
            except Exception as e:
                print(f"Error reading {hist_file}: {e}")
    
    return missing


def fill_missing_data(missing_list, dry_run=False):
    """欠損データを取得して補完"""
    if not missing_list:
        print("欠損データなし")
        return 0
    
    print(f"欠損データ: {len(missing_list)}件")
    
    if dry_run:
        for store_key, unit_id, date in missing_list[:10]:
            print(f"  {store_key}/{unit_id}: {date}")
        return 0
    
    # 日付ごとにグループ化
    by_date = {}
    for store_key, unit_id, date in missing_list:
        if date not in by_date:
            by_date[date] = []
        by_date[date].append((store_key, unit_id))
    
    scraper = DaidataScraper(headless=True)
    filled = 0
    
    with scraper.browser_session('daidata'):
        for date, units in sorted(by_date.items()):
            print(f"\n{date}のデータを取得中...")
            
            # hist_numを計算（今日からの日数）
            today = datetime.now(JST).date()
            target = datetime.strptime(date, '%Y-%m-%d').date()
            hist_num = (today - target).days
            
            for store_key, unit_id in units:
                config = DAIDATA_STORES.get(store_key, {})
                hall_id = config.get('hall_id')
                if not hall_id:
                    continue
                
                url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}&hist_num={hist_num}"
                
                try:
                    if not scraper._goto_with_terms(url, hall_id):
                        continue
                    
                    # リスト表示に切り替え
                    link = scraper.page.locator('text=リスト表示に切り替える')
                    if link.count() > 0:
                        link.click()
                        scraper.wait(2000)
                    
                    text = scraper.get_text()
                    hist = parse_history_from_text(text)
                    
                    if hist:
                        # 履歴ファイルに追加
                        hist_file = HISTORY_DIR / store_key / f"{unit_id}.json"
                        hist_file.parent.mkdir(parents=True, exist_ok=True)
                        
                        if hist_file.exists():
                            with open(hist_file) as f:
                                hist_data = json.load(f)
                        else:
                            hist_data = {'unit_id': unit_id, 'days': []}
                        
                        # 既存チェック
                        found = False
                        for d in hist_data.get('days', []):
                            if d.get('date') == date:
                                d['history'] = hist
                                found = True
                                break
                        if not found:
                            hist_data['days'].append({'date': date, 'history': hist})
                            hist_data['days'].sort(key=lambda x: x.get('date', ''))
                        
                        with open(hist_file, 'w') as f:
                            json.dump(hist_data, f, ensure_ascii=False)
                        
                        print(f"  {store_key}/{unit_id}: {len(hist)}件")
                        filled += 1
                except Exception as e:
                    print(f"  {store_key}/{unit_id}: エラー {e}")
    
    return filled


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=7, help='チェックする日数')
    parser.add_argument('--dry-run', action='store_true', help='実際には取得しない')
    args = parser.parse_args()
    
    print(f"過去{args.days}日間の欠損データをチェック中...")
    missing = find_missing_dates(args.days)
    
    if missing:
        print(f"\n欠損検出: {len(missing)}件")
        filled = fill_missing_data(missing, dry_run=args.dry_run)
        print(f"\n補完完了: {filled}件")
    else:
        print("欠損データなし")


if __name__ == '__main__':
    main()
