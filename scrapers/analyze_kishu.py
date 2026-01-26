#!/usr/bin/env python3
"""
スロレポの機種別ページを解析
"""

import requests
from bs4 import BeautifulSoup
import re

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept-Language': 'ja,en-US;q=0.7,en;q=0.3',
}

BASE_ISLAND = "https://www.slorepo.com/hole/e382a2e382a4e383a9e383b3e38389e7a78be89189e58e9fe5ba97code"
BASE_ESPASS = "https://www.slorepo.com/hole/e382a8e382b9e38391e382b9e697a5e68b93e6b88be8b0b7e9a785e5898de696b0e9a4a8code"


def analyze_kishu_tusan(base_url, shop_name):
    """機種別累計差枚ページ"""
    print("=" * 70)
    print(f"機種別累計差枚: {shop_name}")
    print("=" * 70)

    url = f"{base_url}/kishu_tusan"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, 'lxml')

    print(f"ステータス: {resp.status_code}, サイズ: {len(resp.text)}")

    tables = soup.find_all('table')
    print(f"テーブル数: {len(tables)}")

    for i, table in enumerate(tables[:2]):
        print(f"\n--- テーブル {i+1} ---")
        headers = table.find_all('th')
        if headers:
            header_text = [th.get_text(strip=True) for th in headers[:10]]
            print(f"ヘッダー: {header_text}")

        rows = table.find_all('tr')
        print(f"行数: {len(rows)}")

        # ブラックジャック行を探す
        for row in rows:
            text = row.get_text()
            if 'ブラックジャック' in text:
                cells = row.find_all(['td', 'th'])
                cell_text = [c.get_text(strip=True) for c in cells]
                print(f"  ★SBJ発見: {cell_text}")

        # 最初の5行を表示
        for j, row in enumerate(rows[1:6]):
            cells = row.find_all(['td', 'th'])
            cell_text = [c.get_text(strip=True)[:20] for c in cells[:6]]
            print(f"  行{j+1}: {cell_text}")


def analyze_daily_detail(base_url, shop_name):
    """日別詳細ページ（最新日）"""
    print("\n" + "=" * 70)
    print(f"日別詳細: {shop_name}")
    print("=" * 70)

    # まず店舗ページから最新日付のリンクを取得
    resp = requests.get(base_url + "/", headers=HEADERS, timeout=15)
    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, 'lxml')

    # 日付形式のリンクを探す (例: /20260125/)
    links = soup.find_all('a', href=True)
    date_links = []
    for link in links:
        href = link.get('href', '')
        match = re.search(r'/(\d{8})/', href)
        if match:
            date_links.append((match.group(1), href))

    if date_links:
        # 最新日付を取得
        date_links.sort(reverse=True)
        latest_date, latest_href = date_links[0]
        print(f"最新日付: {latest_date}")

        # 日付詳細ページにアクセス
        if not latest_href.startswith('http'):
            latest_href = f"{base_url}/{latest_date}/"

        print(f"URL: {latest_href}")
        resp2 = requests.get(latest_href, headers=HEADERS, timeout=15)
        resp2.encoding = 'utf-8'
        soup2 = BeautifulSoup(resp2.text, 'lxml')

        print(f"ステータス: {resp2.status_code}, サイズ: {len(resp2.text)}")

        tables = soup2.find_all('table')
        print(f"テーブル数: {len(tables)}")

        # ブラックジャック行を探す
        for table in tables:
            for row in table.find_all('tr'):
                text = row.get_text()
                if 'ブラックジャック' in text:
                    cells = row.find_all(['td', 'th'])
                    cell_text = [c.get_text(strip=True) for c in cells]
                    print(f"  ★SBJ発見: {cell_text}")


def analyze_daiban_detail(base_url, shop_name):
    """台番別詳細ページ"""
    print("\n" + "=" * 70)
    print(f"台番別詳細: {shop_name}")
    print("=" * 70)

    url = f"{base_url}/daiban"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, 'lxml')

    print(f"ステータス: {resp.status_code}, サイズ: {len(resp.text)}")

    # divやsectionを探す
    divs = soup.find_all('div', class_=True)
    print(f"div数: {len(divs)}")

    # データを含むテキストを探す
    text = soup.get_text()
    if 'ブラックジャック' in text:
        print("✓ 'ブラックジャック' を発見")

    # 台番号パターンを探す
    daiban_pattern = re.findall(r'台番[^\d]*(\d+)', text)
    if daiban_pattern:
        print(f"台番号: {daiban_pattern[:10]}")

    # 差枚パターン
    sabetsu = re.findall(r'[+-]?\d{1,3},?\d{3}枚?', text)
    if sabetsu:
        print(f"差枚データ: {sabetsu[:10]}")

    # HTMLの一部を表示
    print("\n【HTML構造サンプル】")
    print(resp.text[5000:6000])


def find_sbj_specific_page():
    """SBJ専用ページを探す"""
    print("\n" + "=" * 70)
    print("SBJ専用ページ探索")
    print("=" * 70)

    # 機種名でURLエンコード
    import urllib.parse
    kishu_name = urllib.parse.quote("スマスロ スーパーブラックジャック")

    patterns = [
        f"{BASE_ISLAND}/kishu/?name={kishu_name}",
        f"{BASE_ISLAND}/kishu_tusan?kishu={kishu_name}",
    ]

    for url in patterns:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            print(f"URL: {url[:80]}...")
            print(f"  → ステータス: {resp.status_code}, サイズ: {len(resp.text)}")
            if resp.status_code == 200 and len(resp.text) > 5000:
                soup = BeautifulSoup(resp.text, 'lxml')
                if 'ブラックジャック' in soup.get_text():
                    print("  ✓ SBJデータあり")
        except Exception as e:
            print(f"  → エラー: {e}")


if __name__ == "__main__":
    # 秋葉原アイランド
    analyze_kishu_tusan(BASE_ISLAND, "秋葉原アイランド")
    analyze_daily_detail(BASE_ISLAND, "秋葉原アイランド")

    # 渋谷エスパス新館
    analyze_kishu_tusan(BASE_ESPASS, "渋谷エスパス新館")

    # SBJ専用ページ
    find_sbj_specific_page()
