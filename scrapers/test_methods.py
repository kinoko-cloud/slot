#!/usr/bin/env python3
"""
複数のスクレイピング方法をテストするスクリプト
"""

import requests
from bs4 import BeautifulSoup
import json
import re

# ユーザーエージェント（ブラウザに見せかける）
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ja,en-US;q=0.7,en;q=0.3',
}

def test_daidata_direct():
    """方法1: 台データオンラインを直接requests"""
    print("=" * 60)
    print("方法1: 台データオンライン（直接リクエスト）")
    print("=" * 60)

    url = "https://daidata.goraggio.com/100860"  # 渋谷エスパス新館

    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = 'utf-8'
        print(f"ステータス: {resp.status_code}")
        print(f"Content-Length: {len(resp.text)} bytes")

        soup = BeautifulSoup(resp.text, 'lxml')

        # ページタイトル
        title = soup.find('title')
        print(f"タイトル: {title.text if title else 'なし'}")

        # スクリプトタグを確認（API URLがあるか）
        scripts = soup.find_all('script')
        print(f"スクリプトタグ数: {len(scripts)}")

        # API URLやJSONデータを探す
        for script in scripts:
            if script.string:
                # APIエンドポイントを探す
                api_matches = re.findall(r'https?://[^\s"\']+api[^\s"\']*', script.string)
                if api_matches:
                    print(f"発見したAPI URL: {api_matches[:3]}")

                # JSONデータを探す
                json_matches = re.findall(r'\{[^{}]{100,}?\}', script.string)
                if json_matches:
                    print(f"埋め込みJSONらしきもの: {len(json_matches)}個")

        # 機種名やデータを探す
        text_content = soup.get_text()
        if 'ブラックジャック' in text_content:
            print("✓ 'ブラックジャック'というテキストを発見")
        else:
            print("✗ 'ブラックジャック'というテキストは見つからず（JS動的の可能性）")

        return resp.text

    except Exception as e:
        print(f"エラー: {e}")
        return None


def test_daidata_api():
    """方法2: 台データオンラインの内部APIを探す"""
    print("\n" + "=" * 60)
    print("方法2: 台データオンライン（内部API調査）")
    print("=" * 60)

    # よくあるAPIパターンを試す
    base_urls = [
        "https://daidata.goraggio.com/api/100860",
        "https://daidata.goraggio.com/api/v1/hall/100860",
        "https://daidata.goraggio.com/api/hall/100860",
        "https://api.daidata.goraggio.com/100860",
        "https://daidata.goraggio.com/100860/data",
        "https://daidata.goraggio.com/100860/machines",
    ]

    for url in base_urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=5)
            content_type = resp.headers.get('Content-Type', '')
            print(f"{url}")
            print(f"  → ステータス: {resp.status_code}, Type: {content_type[:50]}")

            if resp.status_code == 200 and 'json' in content_type:
                print(f"  ✓ JSON発見！")
                print(f"  データ: {resp.text[:200]}...")
                return resp.json()
        except Exception as e:
            print(f"  → エラー: {e}")

    return None


def test_slorepo():
    """方法3: スロレポ（静的HTMLの可能性）"""
    print("\n" + "=" * 60)
    print("方法3: スロレポ")
    print("=" * 60)

    # 渋谷エスパス新館
    url = "https://www.slorepo.com/hole/e382a8e382b9e38391e382b9e697a5e68b93e6b88be8b0b7e9a785e5898de696b0e9a4a8code/"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = 'utf-8'
        print(f"ステータス: {resp.status_code}")
        print(f"Content-Length: {len(resp.text)} bytes")

        soup = BeautifulSoup(resp.text, 'lxml')

        title = soup.find('title')
        print(f"タイトル: {title.text if title else 'なし'}")

        # テーブルデータを探す
        tables = soup.find_all('table')
        print(f"テーブル数: {len(tables)}")

        # 日付データを探す
        text = soup.get_text()
        date_matches = re.findall(r'\d{1,2}/\d{1,2}', text)
        if date_matches:
            print(f"日付らしきもの: {date_matches[:5]}")

        # 差枚データを探す
        sabetsu_matches = re.findall(r'[+-]?\d{1,3},?\d{3}枚?', text)
        if sabetsu_matches:
            print(f"差枚らしきもの: {sabetsu_matches[:5]}")
            print("✓ データが静的HTMLに含まれている可能性あり")

        return resp.text

    except Exception as e:
        print(f"エラー: {e}")
        return None


def test_slorepo_machine():
    """方法4: スロレポの機種別ページ"""
    print("\n" + "=" * 60)
    print("方法4: スロレポ（SBJランキング）")
    print("=" * 60)

    url = "https://www.slorepo.com/ranking/kishu/?kishu=スマスロ+スーパーブラックジャック"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = 'utf-8'
        print(f"ステータス: {resp.status_code}")

        soup = BeautifulSoup(resp.text, 'lxml')

        # ランキングデータを探す
        text = soup.get_text()

        # 店舗名を探す
        if 'エスパス' in text:
            print("✓ 'エスパス'を発見")
        if 'アイランド' in text:
            print("✓ 'アイランド'を発見")

        # 数値データ
        numbers = re.findall(r'[+-]?\d{1,3},?\d{3}', text)
        if numbers:
            print(f"数値データ例: {numbers[:10]}")

        return resp.text

    except Exception as e:
        print(f"エラー: {e}")
        return None


if __name__ == "__main__":
    print("スクレイピング方法テスト開始\n")

    # 各方法をテスト
    test_daidata_direct()
    test_daidata_api()
    test_slorepo()
    test_slorepo_machine()

    print("\n" + "=" * 60)
    print("テスト完了")
    print("=" * 60)
