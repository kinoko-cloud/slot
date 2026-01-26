#!/usr/bin/env python3
"""
スロレポのHTML構造を詳しく解析
"""

import requests
from bs4 import BeautifulSoup
import re
import json

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept-Language': 'ja,en-US;q=0.7,en;q=0.3',
}


def analyze_shop_page():
    """店舗ページ（渋谷エスパス新館）を解析"""
    print("=" * 70)
    print("店舗ページ解析: 渋谷エスパス新館")
    print("=" * 70)

    url = "https://www.slorepo.com/hole/e382a8e382b9e38391e382b9e697a5e68b93e6b88be8b0b7e9a785e5898de696b0e9a4a8code/"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, 'lxml')

    # 店舗基本情報
    print("\n【店舗情報】")
    h1 = soup.find('h1')
    if h1:
        print(f"店舗名: {h1.get_text(strip=True)}")

    # テーブルを解析
    tables = soup.find_all('table')
    print(f"\nテーブル数: {len(tables)}")

    for i, table in enumerate(tables):
        print(f"\n--- テーブル {i+1} ---")
        caption = table.find('caption')
        if caption:
            print(f"キャプション: {caption.get_text(strip=True)}")

        # ヘッダー
        headers = table.find_all('th')
        if headers:
            header_text = [th.get_text(strip=True) for th in headers[:10]]
            print(f"ヘッダー: {header_text}")

        # 最初の数行のデータ
        rows = table.find_all('tr')
        print(f"行数: {len(rows)}")
        for j, row in enumerate(rows[1:4]):  # 最初の3行
            cells = row.find_all(['td', 'th'])
            cell_text = [c.get_text(strip=True)[:20] for c in cells[:8]]
            print(f"  行{j+1}: {cell_text}")

    # 機種別データへのリンクを探す
    print("\n【機種別ページへのリンク】")
    links = soup.find_all('a', href=True)
    machine_links = [a for a in links if '/kishu/' in a.get('href', '')]
    print(f"機種リンク数: {len(machine_links)}")
    for link in machine_links[:5]:
        print(f"  - {link.get_text(strip=True)}: {link['href']}")

    return soup


def analyze_ranking_page():
    """SBJランキングページを解析"""
    print("\n" + "=" * 70)
    print("ランキングページ解析: スマスロ スーパーブラックジャック")
    print("=" * 70)

    url = "https://www.slorepo.com/ranking/kishu/?kishu=スマスロ+スーパーブラックジャック"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, 'lxml')

    # テーブルを解析
    tables = soup.find_all('table')
    print(f"\nテーブル数: {len(tables)}")

    for i, table in enumerate(tables):
        print(f"\n--- テーブル {i+1} ---")
        headers = table.find_all('th')
        if headers:
            header_text = [th.get_text(strip=True) for th in headers[:10]]
            print(f"ヘッダー: {header_text}")

        rows = table.find_all('tr')
        print(f"行数: {len(rows)}")

        # 対象店舗を探す
        for row in rows:
            text = row.get_text()
            if 'エスパス' in text and '渋谷' in text:
                cells = row.find_all(['td', 'th'])
                cell_text = [c.get_text(strip=True) for c in cells]
                print(f"  ★渋谷エスパス発見: {cell_text}")
            elif 'アイランド' in text and '秋葉原' in text:
                cells = row.find_all(['td', 'th'])
                cell_text = [c.get_text(strip=True) for c in cells]
                print(f"  ★秋葉原アイランド発見: {cell_text}")

    return soup


def find_detail_pages():
    """詳細データページを探す"""
    print("\n" + "=" * 70)
    print("詳細データページ探索")
    print("=" * 70)

    # 渋谷エスパス新館の機種別ページを探す
    base_url = "https://www.slorepo.com/hole/e382a8e382b9e38391e382b9e697a5e68b93e6b88be8b0b7e9a785e5898de696b0e9a4a8code/"
    resp = requests.get(base_url, headers=HEADERS, timeout=15)
    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, 'lxml')

    # 全リンクを探す
    links = soup.find_all('a', href=True)
    print(f"総リンク数: {len(links)}")

    # ブラックジャック関連のリンク
    for link in links:
        href = link.get('href', '')
        text = link.get_text(strip=True)
        if 'ブラックジャック' in text or 'SBJ' in text.upper():
            print(f"  ★発見: {text} → {href}")

    # 台番号関連のリンク
    print("\n台番号ページへのリンク:")
    for link in links:
        href = link.get('href', '')
        if '/daiban/' in href or 'daiban' in href:
            print(f"  - {link.get_text(strip=True)}: {href}")
            break  # 1つだけ表示


def check_akihabara_island():
    """秋葉原アイランドのデータを確認"""
    print("\n" + "=" * 70)
    print("秋葉原アイランドのデータ確認")
    print("=" * 70)

    url = "https://www.slorepo.com/hole/e382a2e382a4e383a9e383b3e38389e7a78be89189e58e9fe5ba97code/"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, 'lxml')

    h1 = soup.find('h1')
    if h1:
        print(f"店舗名: {h1.get_text(strip=True)}")

    # ブラックジャック関連のリンク
    links = soup.find_all('a', href=True)
    for link in links:
        text = link.get_text(strip=True)
        if 'ブラックジャック' in text:
            print(f"  ★発見: {text} → {link['href']}")


if __name__ == "__main__":
    analyze_shop_page()
    analyze_ranking_page()
    find_detail_pages()
    check_akihabara_island()
