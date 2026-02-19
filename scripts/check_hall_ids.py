#!/usr/bin/env python3
"""
店舗番号（hall_id）の整合性チェック

毎朝10時に実行して、店舗番号が変わっていないか確認する。
変更があればエラーを出力し、GitHub Actionsで検出可能にする。
"""

import sys
import urllib.parse
from pathlib import Path
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from scripts.scrapers_v2.daidata.scraper import DaidataScraper
from scripts.fetch_daidata_availability import DAIDATA_STORES

# 検索キーワードと期待する店舗のマッピング
EXPECTED_STORES = {
    '新宿': {
        '100949': 'エスパス日拓新宿歌舞伎町店',
        '100950': 'エスパス日拓西武新宿駅前店',
        '100951': 'エスパス日拓新大久保駅前店',
        '100915': 'エスパス日拓高田馬場本店',
    },
    '渋谷': {
        '100860': 'エスパス日拓渋谷駅前新館',
        '100930': 'エスパス日拓渋谷本館',
    },
    '秋葉原': {
        '100928': 'エスパス日拓秋葉原駅前店',
    },
    '上野': {
        '100196': 'エスパス日拓上野新館',
        '100947': 'エスパス日拓上野本館',
    },
    '新小岩': {
        '100260': 'エスパス１３００新小岩北口駅前店',
    },
    '赤坂': {
        '100952': 'エスパス日拓赤坂見附駅前新館',
    },
}


def search_stores_by_keyword(scraper, keyword: str) -> dict:
    """キーワードで店舗を検索してhall_idと店舗名を取得"""
    encoded = urllib.parse.quote(keyword)
    search_url = f"https://daidata.goraggio.com/store_list?word={encoded}"
    
    results = {}
    
    scraper.page.goto(search_url, wait_until='load', timeout=60000)
    scraper.page.wait_for_timeout(5000)
    
    html = scraper.page.content()
    soup = BeautifulSoup(html, 'html.parser')
    
    table = soup.find('table', id='sorter')
    if not table:
        return results
    
    for row in table.find_all('tr')[1:]:
        cells = row.find_all('td')
        if not cells:
            continue
        
        link = cells[0].find('a')
        if not link:
            continue
        
        href = link.get('href', '')
        text = link.text.strip()
        
        # URLからhall_idを抽出
        if 'daidata.goraggio.com/' in href:
            hall_id = href.split('/')[-1]
        elif href.startswith('/'):
            hall_id = href[1:]
        else:
            hall_id = href
        
        # 店舗名を取得（改行前の部分）
        name = text.split('\n')[0].strip()
        
        results[hall_id] = name
    
    return results


def check_hall_ids():
    """店舗番号の整合性をチェック"""
    errors = []
    warnings = []
    
    scraper = DaidataScraper(headless=True)
    with scraper.browser_session():
        for keyword, expected in EXPECTED_STORES.items():
            print(f"Checking: {keyword}...")
            
            try:
                actual = search_stores_by_keyword(scraper, keyword)
            except Exception as e:
                errors.append(f"{keyword}: 検索失敗 - {e}")
                continue
            
            # 期待するhall_idがあるか確認
            for hall_id, name in expected.items():
                if hall_id not in actual:
                    errors.append(f"⚠️ {keyword}: hall_id {hall_id} ({name}) が見つかりません")
                elif 'エスパス' in name and 'エスパス' not in actual.get(hall_id, ''):
                    warnings.append(f"⚠️ {keyword}: {hall_id} の店舗名が変わった可能性: {actual.get(hall_id)}")
    
    return errors, warnings


def main():
    print("=== 店舗番号チェック ===")
    print()
    
    errors, warnings = check_hall_ids()
    
    if warnings:
        print("\n=== 警告 ===")
        for w in warnings:
            print(w)
    
    if errors:
        print("\n=== エラー ===")
        for e in errors:
            print(e)
        print("\n❌ 店舗番号に変更がある可能性があります")
        print("手動で確認してください: https://daidata.goraggio.com/store_list")
        sys.exit(1)
    else:
        print("\n✅ 店舗番号チェック完了: 問題なし")


if __name__ == '__main__':
    main()
