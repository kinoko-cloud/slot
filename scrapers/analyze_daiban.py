#!/usr/bin/env python3
"""
スロレポの台番別ページと機種別詳細を解析
"""

import requests
from bs4 import BeautifulSoup
import re

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept-Language': 'ja,en-US;q=0.7,en;q=0.3',
}


def analyze_daiban_page():
    """台番別ページを解析"""
    print("=" * 70)
    print("台番別ページ解析: 渋谷エスパス新館")
    print("=" * 70)

    # 台番別ページ
    url = "https://www.slorepo.com/hole/e382a8e382b9e38391e382b9e697a5e68b93e6b88be8b0b7e9a785e5898de696b0e9a4a8code/daiban"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, 'lxml')

    print(f"ステータス: {resp.status_code}")
    print(f"Content-Length: {len(resp.text)} bytes")

    # テーブル解析
    tables = soup.find_all('table')
    print(f"\nテーブル数: {len(tables)}")

    for i, table in enumerate(tables[:3]):  # 最初の3テーブル
        print(f"\n--- テーブル {i+1} ---")
        headers = table.find_all('th')
        if headers:
            header_text = [th.get_text(strip=True) for th in headers[:10]]
            print(f"ヘッダー: {header_text}")

        rows = table.find_all('tr')
        print(f"行数: {len(rows)}")
        for j, row in enumerate(rows[1:6]):  # 最初の5行
            cells = row.find_all(['td', 'th'])
            cell_text = [c.get_text(strip=True)[:15] for c in cells[:8]]
            print(f"  行{j+1}: {cell_text}")

    # ブラックジャック関連を探す
    print("\n【ブラックジャック関連の検索】")
    text = soup.get_text()
    if 'ブラックジャック' in text:
        print("✓ 'ブラックジャック'を発見")
        # 前後のテキストを取得
        idx = text.find('ブラックジャック')
        context = text[max(0, idx-50):idx+100]
        print(f"コンテキスト: {context[:150]}...")


def analyze_machine_detail():
    """機種別の詳細ページを探す"""
    print("\n" + "=" * 70)
    print("機種別詳細ページ探索")
    print("=" * 70)

    # スロレポのSBJランキングから店舗詳細へ
    # まずランキングページのリンク構造を確認
    url = "https://www.slorepo.com/ranking/kishu/?kishu=スマスロ+スーパーブラックジャック"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, 'lxml')

    # 秋葉原アイランドへのリンクを探す
    links = soup.find_all('a', href=True)
    for link in links:
        text = link.get_text()
        if 'アイランド秋葉原' in text:
            href = link.get('href')
            print(f"秋葉原アイランドリンク: {href}")

            # そのリンク先を確認
            if href and not href.startswith('http'):
                href = "https://www.slorepo.com" + href
            if href:
                print(f"アクセス中: {href}")
                resp2 = requests.get(href, headers=HEADERS, timeout=15)
                resp2.encoding = 'utf-8'
                soup2 = BeautifulSoup(resp2.text, 'lxml')

                # SBJ関連のリンクを探す
                links2 = soup2.find_all('a', href=True)
                for link2 in links2:
                    text2 = link2.get_text()
                    href2 = link2.get('href', '')
                    if 'ブラックジャック' in text2 or 'kishu' in href2:
                        print(f"  → {text2}: {href2}")
            break


def analyze_island_akiba():
    """秋葉原アイランドの詳細"""
    print("\n" + "=" * 70)
    print("秋葉原アイランド詳細")
    print("=" * 70)

    url = "https://www.slorepo.com/hole/e382a2e382a4e383a9e383b3e38389e7a78be89189e58e9fe5ba97code/"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, 'lxml')

    # 機種一覧を探す
    print("【機種リンク検索】")
    links = soup.find_all('a', href=True)
    sbj_links = []
    for link in links:
        text = link.get_text(strip=True)
        href = link.get('href', '')
        if 'ブラックジャック' in text or 'SBJ' in text.upper():
            print(f"  ★ {text}: {href}")
            sbj_links.append(href)
        elif 'kishu' in href and text:
            if len(sbj_links) < 3:  # 最初の3機種も表示
                print(f"  機種: {text}: {href}")

    # 機種別ページのパターンを探す
    print("\n【ページ構造】")
    # /kishu/ パターンを探す
    kishu_pattern = re.findall(r'/kishu/[^"\'>\s]+', resp.text)
    if kishu_pattern:
        unique_patterns = list(set(kishu_pattern))[:5]
        print(f"機種ページパターン: {unique_patterns}")


def try_machine_page():
    """機種別ページを直接試す"""
    print("\n" + "=" * 70)
    print("機種別ページ直接アクセステスト")
    print("=" * 70)

    # よくあるURL パターンを試す
    base = "https://www.slorepo.com"
    patterns = [
        "/hole/e382a2e382a4e383a9e383b3e38389e7a78be89189e58e9fe5ba97code/kishu/スマスロ+スーパーブラックジャック",
        "/hole/e382a2e382a4e383a9e383b3e38389e7a78be89189e58e9fe5ba97code/kishu/?name=スマスロ+スーパーブラックジャック",
    ]

    for pattern in patterns:
        url = base + pattern
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
            print(f"{pattern[:60]}...")
            print(f"  → ステータス: {resp.status_code}, サイズ: {len(resp.text)}")
            if resp.status_code == 200 and len(resp.text) > 10000:
                soup = BeautifulSoup(resp.text, 'lxml')
                tables = soup.find_all('table')
                print(f"  → テーブル数: {len(tables)}")
        except Exception as e:
            print(f"  → エラー: {e}")


if __name__ == "__main__":
    analyze_daiban_page()
    analyze_machine_detail()
    analyze_island_akiba()
    try_machine_page()
