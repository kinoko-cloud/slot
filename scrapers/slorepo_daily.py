#!/usr/bin/env python3
"""
スロレポ日次データ取得スクレイパー

各店舗の日別・機種別の全台データ（差枚・G数・BB・RB・合成確率）を取得。
daidataでは取れない差枚データを補完する。
"""

import requests
from bs4 import BeautifulSoup
import re
import json
from datetime import datetime, timedelta
from pathlib import Path

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept-Language': 'ja,en-US;q=0.7,en;q=0.3',
}

# スロレポの店舗URL
SLOREPO_STORES = {
    'shibuya_espass': {
        'url_key': 'e382a8e382b9e38391e382b9e697a5e68b93e6b88be8b0b7e9a785e5898de696b0e9a4a8code',
        'name': 'エスパス渋谷新館',
    },
    'shinjuku_espass': {
        'url_key': 'e382a8e382b9e38391e382b9e697a5e68b93e696b0e5aebfe6ad8ce8889ee4bc8ee794bae5ba97code',
        'name': 'エスパス歌舞伎町',
    },
    'akiba_espass': {
        'url_key': 'e382a8e382b9e38391e382b9e697a5e68b93e7a78be89189e58e9fe9a785e5898de5ba97code',
        'name': 'エスパス秋葉原駅前',
    },
    'seibu_shinjuku_espass': {
        'url_key': 'e382a8e382b9e38391e382b9e697a5e68b93e8a5bfe6ada6e696b0e5aebfe9a785e5898de5ba97code',
        'name': 'エスパス西武新宿駅前',
    },
    'island_akihabara': {
        'url_key': 'e382a2e382a4e383a9e383b3e38389e7a78be89189e58e9fe5ba97code',
        'name': 'アイランド秋葉原',
    },
}

# 対象機種
TARGET_MACHINES = {
    'sbj': 'Lスーパーブラックジャック',
    'hokuto_tensei2': '北斗の拳 転生の章2',
}


def get_daily_machine_data(store_key: str, date_str: str, machine_key: str) -> list:
    """指定店舗・日付・機種の全台データを取得

    Returns:
        [{unit_id, diff_medals, games, bb, rb, prob_str, prob}, ...]
    """
    store = SLOREPO_STORES.get(store_key, {})
    if not store:
        print(f"  ⚠ 店舗未登録: {store_key}")
        return []

    machine_name = TARGET_MACHINES.get(machine_key, '')
    if not machine_name:
        print(f"  ⚠ 機種未登録: {machine_key}")
        return []

    url_key = store['url_key']
    date_compact = date_str.replace('-', '')

    # URLエンコードされた機種名
    import urllib.parse
    machine_encoded = urllib.parse.quote(machine_name)

    url = f'https://www.slorepo.com/hole/{url_key}/{date_compact}/kishu/?kishu={machine_encoded}'

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            print(f"  ⚠ HTTP {r.status_code}: {url}")
            return []
    except Exception as e:
        print(f"  ⚠ リクエストエラー: {e}")
        return []

    soup = BeautifulSoup(r.text, 'html.parser')
    tables = soup.find_all('table')

    if not tables:
        print(f"  データなし: {store['name']} {machine_name} {date_str}")
        return []

    # 台番号を取得（<font>タグに入ってる）
    unit_ids = []
    for font in soup.find_all('font'):
        text = font.get_text(strip=True)
        if re.match(r'^\d{3,4}$', text):
            unit_ids.append(text)

    results = []
    for i, table in enumerate(tables):
        rows = table.find_all('tr')
        if len(rows) < 2:
            continue

        # ヘッダー確認
        headers = [th.get_text(strip=True) for th in rows[0].find_all(['td', 'th'])]
        if '差枚' not in headers and 'G数' not in headers:
            continue

        # データ行
        cells = [td.get_text(strip=True) for td in rows[1].find_all(['td', 'th'])]
        if len(cells) < 5:
            continue

        try:
            diff_medals = int(cells[0].replace(',', '').replace('+', ''))
            games = int(cells[1].replace(',', ''))
            bb = int(cells[2].replace(',', ''))
            rb = int(cells[3].replace(',', ''))
            prob_str = cells[4] if len(cells) > 4 else ''
            # 確率をパース（1/338 → 338）
            prob = 0
            m = re.match(r'1/(\d+)', prob_str)
            if m:
                prob = int(m.group(1))
        except (ValueError, IndexError):
            continue

        unit_id = unit_ids[i] if i < len(unit_ids) else f'unknown_{i}'

        results.append({
            'unit_id': unit_id,
            'diff_medals': diff_medals,
            'games': games,
            'bb': bb,
            'rb': rb,
            'prob_str': prob_str,
            'prob': prob,
        })

    return results


def get_store_summary(store_key: str, date_str: str) -> dict:
    """店舗の日別サマリ（全機種の平均差枚・勝率等）"""
    store = SLOREPO_STORES.get(store_key, {})
    if not store:
        return {}

    url_key = store['url_key']
    date_compact = date_str.replace('-', '')
    url = f'https://www.slorepo.com/hole/{url_key}/{date_compact}'

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return {}
    except Exception:
        return {}

    soup = BeautifulSoup(r.text, 'html.parser')
    tables = soup.find_all('table')

    result = {'store': store['name'], 'date': date_str}

    # 最初のテーブル=店舗全体サマリ
    if tables:
        rows = tables[0].find_all('tr')
        if len(rows) >= 2:
            cells = [td.get_text(strip=True) for td in rows[1].find_all(['td', 'th'])]
            if len(cells) >= 4:
                result['total_diff'] = cells[0]
                result['avg_diff'] = cells[1]
                result['avg_games'] = cells[2]
                result['win_rate'] = cells[3]

    # 機種別テーブル
    if len(tables) >= 2:
        machine_data = []
        for row in tables[1].find_all('tr')[1:]:
            cells = [td.get_text(strip=True) for td in row.find_all(['td', 'th'])]
            if len(cells) >= 4:
                machine_data.append({
                    'machine': cells[0],
                    'avg_diff': cells[1],
                    'avg_games': cells[2],
                    'win_rate': cells[3],
                })
        result['machines'] = machine_data

    return result


def scrape_all_stores(date_str: str = None, days_back: int = 1) -> dict:
    """全店舗の対象機種データを取得"""
    import time

    if not date_str:
        # 昨日
        date_str = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    all_data = {}

    for store_key, store in SLOREPO_STORES.items():
        print(f"\n{'='*50}")
        print(f"{store['name']} - {date_str}")
        print(f"{'='*50}")

        store_data = {'summary': {}, 'machines': {}}

        # サマリ
        summary = get_store_summary(store_key, date_str)
        store_data['summary'] = summary
        if summary:
            print(f"  店舗全体: {summary.get('total_diff', 'N/A')} / 勝率 {summary.get('win_rate', 'N/A')}")

        # 機種別
        for mk, mn in TARGET_MACHINES.items():
            print(f"\n  --- {mn} ---")
            units = get_daily_machine_data(store_key, date_str, mk)
            store_data['machines'][mk] = units
            if units:
                avg_diff = sum(u['diff_medals'] for u in units) / len(units)
                wins = sum(1 for u in units if u['diff_medals'] > 0)
                print(f"  {len(units)}台: 平均差枚{avg_diff:+,.0f} / 勝率{wins}/{len(units)}")
                for u in sorted(units, key=lambda x: -x['diff_medals'])[:3]:
                    print(f"    {u['unit_id']}番: {u['diff_medals']:+,}枚 ({u['prob_str']})")

            time.sleep(1)

        all_data[store_key] = store_data
        time.sleep(1)

    # 保存
    save_path = Path('data/raw') / f'slorepo_{date_str.replace("-","")}.json'
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    print(f"\n✓ 保存: {save_path}")

    return all_data


if __name__ == '__main__':
    import sys
    date = sys.argv[1] if len(sys.argv) > 1 else None
    scrape_all_stores(date)
