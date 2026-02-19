#!/usr/bin/env python3
"""
店舗番号（hall_id）の検索

使い方:
    python scripts/scrapers_v2/lookup_hall_id.py 新宿
    python scripts/scrapers_v2/lookup_hall_id.py エスパス
    python scripts/scrapers_v2/lookup_hall_id.py 渋谷
"""

import sys
import urllib.parse
from bs4 import BeautifulSoup

# DaidataScraperをインポート
sys.path.insert(0, str(__file__).rsplit('/', 2)[0])
from scrapers_v2.daidata.scraper import DaidataScraper


def search_stores(keyword: str) -> list:
    """
    店舗名で検索してhall_idを取得
    
    Args:
        keyword: 検索キーワード（例: "新宿", "エスパス", "渋谷"）
    
    Returns:
        [{'hall_id': '100949', 'name': 'エスパス日拓新宿歌舞伎町店', 'address': '東京都新宿区...'}, ...]
    """
    encoded = urllib.parse.quote(keyword)
    search_url = f"https://daidata.goraggio.com/store_list?word={encoded}"
    
    results = []
    
    scraper = DaidataScraper(headless=True)
    with scraper.browser_session():
        scraper.page.goto(search_url, wait_until='networkidle')
        scraper.page.wait_for_timeout(3000)
        
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
            
            # 名前と住所を分離
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            name = lines[0] if lines else ''
            address = ' '.join(lines[1:]) if len(lines) > 1 else ''
            
            results.append({
                'hall_id': hall_id,
                'name': name,
                'address': address,
            })
    
    return results


def main():
    if len(sys.argv) < 2:
        print("使い方: python lookup_hall_id.py <検索キーワード>")
        print("例: python lookup_hall_id.py 新宿")
        sys.exit(1)
    
    keyword = sys.argv[1]
    print(f"「{keyword}」で検索中...")
    print()
    
    results = search_stores(keyword)
    
    if not results:
        print("店舗が見つかりませんでした")
        sys.exit(1)
    
    print(f"=== 検索結果: {len(results)}件 ===")
    for r in results:
        print(f"  {r['hall_id']}: {r['name']}")
        if r['address']:
            print(f"      {r['address']}")


if __name__ == '__main__':
    main()
