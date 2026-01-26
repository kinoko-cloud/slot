#!/usr/bin/env python3
"""
daidata リアルタイムスクレイパー (requests版)
Playwrightなしでデータ取得
"""

import requests
import re
from datetime import datetime
from bs4 import BeautifulSoup

# 店舗設定
DAIDATA_STORES = {
    'shibuya_espass_sbj': {
        'name': 'エスパス日拓渋谷新館',
        'hall_id': '100860',
        'units': ['3011', '3012', '3013'],
    },
    'shinjuku_espass_sbj': {
        'name': 'エスパス日拓新宿歌舞伎町店',
        'hall_id': '100949',
        'units': ['682', '683', '684', '685'],
    },
    'akihabara_espass_sbj': {
        'name': 'エスパス日拓秋葉原駅前店',
        'hall_id': '100928',
        'units': ['2158', '2159', '2160', '2161'],
    },
    'seibu_shinjuku_espass_sbj': {
        'name': 'エスパス日拓西武新宿駅前店',
        'hall_id': '100950',
        'units': ['3185', '3186', '3187', '4109', '4118', '4125', '4168'],
    },
}


def create_session(hall_id: str) -> requests.Session:
    """セッションを作成し、規約同意を行う"""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })

    # 規約同意ページにアクセス
    accept_url = f"https://daidata.goraggio.com/{hall_id}/accept"
    resp = session.get(accept_url)

    # CSRFトークンを取得
    soup = BeautifulSoup(resp.text, 'html.parser')
    token_input = soup.find('input', {'name': '_token'})
    if not token_input:
        print(f"警告: CSRFトークンが見つかりません")
        return session

    token = token_input.get('value')

    # 同意をPOST
    resp = session.post(accept_url, data={'_token': token})

    return session


def scrape_unit_detail(session: requests.Session, hall_id: str, unit_id: str) -> dict:
    """台詳細ページからデータを取得"""
    url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}"

    try:
        resp = session.get(url, timeout=15)
        text = resp.text

        data = {'unit_id': unit_id}

        # サマリーデータを取得（BB RB ART スタート回数）
        # パターン: BB RB ART スタート回数\n数値 数値 数値 数値
        summary_match = re.search(
            r'BB\s+RB\s+ART\s+スタート回数\s*</th>\s*</tr>\s*<tr[^>]*>\s*'
            r'<td[^>]*>(\d+)</td>\s*<td[^>]*>(\d+)</td>\s*<td[^>]*>(\d+)</td>\s*<td[^>]*>(\d+)</td>',
            text, re.DOTALL
        )

        if summary_match:
            data['bb'] = int(summary_match.group(1))
            data['rb'] = int(summary_match.group(2))
            data['art'] = int(summary_match.group(3))
            data['final_start'] = int(summary_match.group(4))
        else:
            # フォールバック: テキストからパース
            text_content = BeautifulSoup(text, 'html.parser').get_text()
            alt_match = re.search(r'BB\s+RB\s+ART\s+スタート回数\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)', text_content)
            if alt_match:
                data['bb'] = int(alt_match.group(1))
                data['rb'] = int(alt_match.group(2))
                data['art'] = int(alt_match.group(3))
                data['final_start'] = int(alt_match.group(4))

        # 累計スタート
        total_match = re.search(r'累計スタート\s*</th>\s*<td[^>]*>(\d+)</td>', text)
        if total_match:
            data['total_start'] = int(total_match.group(1))
        else:
            text_content = BeautifulSoup(text, 'html.parser').get_text()
            alt_match = re.search(r'累計スタート\s*(\d+)', text_content)
            if alt_match:
                data['total_start'] = int(alt_match.group(1))

        # 履歴は省略（重要なのはART回数とスタート数）
        data['history'] = []

        return data

    except Exception as e:
        return {'unit_id': unit_id, 'error': str(e)}


def scrape_daidata_realtime(store_key: str) -> dict:
    """daidataからリアルタイムデータを取得"""
    if store_key not in DAIDATA_STORES:
        return {}

    store = DAIDATA_STORES[store_key]
    hall_id = store['hall_id']
    units = store['units']

    print(f"【{store['name']}】")

    # セッション作成（規約同意）
    session = create_session(hall_id)

    results = []
    for unit_id in units:
        data = scrape_unit_detail(session, hall_id, unit_id)
        results.append(data)
        art = data.get('art', '?')
        total = data.get('total_start', '?')
        print(f"  台{unit_id}: ART={art}, G数={total}")

    return {
        'store_name': store['name'],
        'fetched_at': datetime.now().isoformat(),
        'units': results,
    }


def main():
    """テスト実行"""
    print("=" * 50)
    print("daidata リアルタイムデータ取得 (requests版)")
    print(f"取得時刻: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    for store_key in DAIDATA_STORES:
        result = scrape_daidata_realtime(store_key)
        print()


if __name__ == "__main__":
    main()
