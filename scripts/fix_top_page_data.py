#!/usr/bin/env python3
"""
トップページに表示される台の過去データ完全性をチェック・修正するスクリプト

- 過去3日分のデータが正しい形式か確認
- 不正なデータ（type=数字など）があれば再取得
- 欠損があれば補完
"""
import json
import sys
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent / 'scrapers_v2'))
sys.path.insert(0, str(Path(__file__).parent))

from daidata.scraper import DaidataScraper
from fetch_daidata_availability import DAIDATA_STORES

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).parent.parent
HISTORY_DIR = ROOT / 'data' / 'history'


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


def is_valid_history(history):
    """履歴データが正しい形式かチェック"""
    if not history:
        return True  # 空は正常（当たりなしの日）
    
    for h in history:
        # typeがART/RB/BB/REG以外ならNG（REGはpapimoで使用）
        if h.get('type') not in ('ART', 'RB', 'BB', 'REG'):
            return False
        # timeが時刻形式でなければNG
        time = h.get('time', '')
        if not re.match(r'^\d{1,2}:\d{2}$', time):
            return False
    return True


def get_top_page_units(top_n=25):
    """トップページから上位N台を取得"""
    index_file = ROOT / 'docs' / 'index.html'
    if not index_file.exists():
        return []
    
    with open(index_file) as f:
        soup = BeautifulSoup(f.read(), 'html.parser')
    
    cards = soup.select('.unit-card')
    units = []
    
    for card in cards[:top_n]:
        href = card.get('href', '')
        card_text = card.get_text()
        
        unit_match = re.search(r'(\d{3,4})番', card_text)
        unit_id = unit_match.group(1) if unit_match else None
        
        store_match = re.search(r'/recommend/([^.]+)\.html', href)
        store_key = store_match.group(1) if store_match else None
        
        if store_key and unit_id:
            # 先頭の0を除去
            unit_id = unit_id.lstrip('0') or '0'
            units.append((store_key, unit_id))
    
    return units


def check_unit_data(store_key, unit_id, days_back=3):
    """台の過去N日分のデータをチェック"""
    issues = []
    
    # 台番号のバリエーションを試す（0付き/なし）
    hist_file = HISTORY_DIR / store_key / f"{unit_id}.json"
    if not hist_file.exists():
        # 4桁の0埋めも試す
        padded_id = unit_id.zfill(4)
        hist_file = HISTORY_DIR / store_key / f"{padded_id}.json"
        if not hist_file.exists():
            return [('missing_file', None)]
    
    with open(hist_file) as f:
        data = json.load(f)
    
    today = datetime.now(JST).date()
    
    for i in range(1, days_back + 1):
        target_date = (today - timedelta(days=i)).strftime('%Y-%m-%d')
        
        # その日のデータを探す
        day_data = None
        for d in data.get('days', []):
            if d.get('date') == target_date:
                day_data = d
                break
        
        if day_data is None:
            issues.append(('missing', target_date))
        elif not is_valid_history(day_data.get('history', [])):
            issues.append(('invalid', target_date))
    
    return issues


def fix_unit_data(scraper, store_key, unit_id, target_date):
    """指定日のデータを再取得して修正"""
    config = DAIDATA_STORES.get(store_key, {})
    hall_id = config.get('hall_id')
    if not hall_id:
        return False
    
    # hist_numを計算
    today = datetime.now(JST).date()
    target = datetime.strptime(target_date, '%Y-%m-%d').date()
    hist_num = (today - target).days
    
    url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}&hist_num={hist_num}"
    
    try:
        if not scraper._goto_with_terms(url, hall_id):
            return False
        
        # リスト表示に切り替え
        link = scraper.page.locator('text=リスト表示に切り替える')
        if link.count() > 0:
            link.click()
            scraper.wait(2000)
        
        text = scraper.get_text()
        hist = parse_history_from_text(text)
        
        # 履歴ファイルを更新
        hist_file = HISTORY_DIR / store_key / f"{unit_id}.json"
        hist_file.parent.mkdir(parents=True, exist_ok=True)
        
        if hist_file.exists():
            with open(hist_file) as f:
                hist_data = json.load(f)
        else:
            hist_data = {'unit_id': unit_id, 'days': []}
        
        # 既存データを更新または追加
        found = False
        for d in hist_data.get('days', []):
            if d.get('date') == target_date:
                d['history'] = hist
                found = True
                break
        if not found:
            hist_data['days'].append({'date': target_date, 'history': hist})
            hist_data['days'].sort(key=lambda x: x.get('date', ''))
        
        with open(hist_file, 'w') as f:
            json.dump(hist_data, f, ensure_ascii=False)
        
        return True
    except Exception as e:
        print(f"  エラー: {e}")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--top', type=int, default=25, help='チェックする台数')
    parser.add_argument('--days', type=int, default=3, help='チェックする日数')
    parser.add_argument('--dry-run', action='store_true', help='実際には修正しない')
    args = parser.parse_args()
    
    print(f"トップ{args.top}台の過去{args.days}日分をチェック中...")
    
    units = get_top_page_units(args.top)
    print(f"対象台数: {len(units)}")
    
    all_issues = []
    for store_key, unit_id in units:
        issues = check_unit_data(store_key, unit_id, args.days)
        if issues:
            all_issues.append((store_key, unit_id, issues))
    
    if not all_issues:
        print("問題なし")
        return
    
    print(f"\n問題検出: {len(all_issues)}台")
    for store_key, unit_id, issues in all_issues:
        print(f"  {store_key}/{unit_id}: {issues}")
    
    if args.dry_run:
        return
    
    print("\n修正中...")
    scraper = DaidataScraper(headless=True)
    scraper.timeout = 60000
    
    fixed = 0
    with scraper.browser_session('daidata'):
        for store_key, unit_id, issues in all_issues:
            for issue_type, target_date in issues:
                if issue_type in ('missing', 'invalid') and target_date:
                    print(f"  {store_key}/{unit_id} ({target_date})...")
                    if fix_unit_data(scraper, store_key, unit_id, target_date):
                        fixed += 1
    
    print(f"\n修正完了: {fixed}件")


if __name__ == '__main__':
    main()
