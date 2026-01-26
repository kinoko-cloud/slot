#!/usr/bin/env python3
"""
スロレポ スクレイパー
- 店舗別の機種データを取得
- 日別データを取得
"""

import requests
from bs4 import BeautifulSoup
import re
import json
from datetime import datetime
from pathlib import Path

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept-Language': 'ja,en-US;q=0.7,en;q=0.3',
}

# 店舗設定
SHOPS = {
    'island_akiba': {
        'name': '秋葉原アイランド',
        'base_url': 'https://www.slorepo.com/hole/e382a2e382a4e383a9e383b3e38389e7a78be89189e58e9fe5ba97code',
    },
    'espass_shibuya_shinkan': {
        'name': '渋谷エスパス新館',
        'base_url': 'https://www.slorepo.com/hole/e382a8e382b9e38391e382b9e697a5e68b93e6b88be8b0b7e9a785e5898de696b0e9a4a8code',
    },
    'espass_shinjuku': {
        'name': '新宿エスパス歌舞伎町',
        'base_url': 'https://www.slorepo.com/hole/e382a8e382b9e38391e382b9e697a5e68b93e696b0e5aebfe6ad8ce8889ee4bc8ee794bae5ba97code',
    },
}

# 対象機種
TARGET_MACHINES = [
    'スマスロ スーパーブラックジャック',
    'スーパーブラックジャック',
    'SBJ',
]


def get_machine_monthly_data(shop_id: str) -> list[dict]:
    """
    機種別月間累計データを取得
    """
    shop = SHOPS.get(shop_id)
    if not shop:
        raise ValueError(f"Unknown shop: {shop_id}")

    url = f"{shop['base_url']}/kishu_tusan"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, 'lxml')

    results = []
    tables = soup.find_all('table')

    for table in tables:
        headers = [th.get_text(strip=True) for th in table.find_all('th')]
        if '機種' not in headers:
            continue

        rows = table.find_all('tr')
        for row in rows[1:]:  # ヘッダーをスキップ
            cells = row.find_all(['td', 'th'])
            if len(cells) >= 4:
                machine_name = cells[0].get_text(strip=True)
                num_units = cells[1].get_text(strip=True)
                this_month = cells[2].get_text(strip=True)
                last_month = cells[3].get_text(strip=True) if len(cells) > 3 else ''

                # 差枚と日平均を抽出
                this_match = re.search(r'([+-]?\d[\d,]*)', this_month)
                avg_match = re.search(r'([+-]?\d+)/台/日', this_month)

                results.append({
                    'shop_id': shop_id,
                    'shop_name': shop['name'],
                    'machine_name': machine_name,
                    'num_units': int(num_units) if num_units.isdigit() else 0,
                    'this_month_total': int(this_match.group(1).replace(',', '')) if this_match else 0,
                    'this_month_avg_per_day': int(avg_match.group(1)) if avg_match else 0,
                    'last_month_raw': last_month,
                    'fetched_at': datetime.now().isoformat(),
                })

    return results


def get_daily_machine_data(shop_id: str, date_str: str = None) -> list[dict]:
    """
    日別の機種データを取得
    date_str: YYYYMMDD形式（Noneの場合は最新）
    """
    shop = SHOPS.get(shop_id)
    if not shop:
        raise ValueError(f"Unknown shop: {shop_id}")

    # まず店舗トップページで最新日付を取得
    if date_str is None:
        resp = requests.get(f"{shop['base_url']}/", headers=HEADERS, timeout=15)
        resp.encoding = 'utf-8'
        date_matches = re.findall(r'/(\d{8})/', resp.text)
        if date_matches:
            date_str = max(date_matches)
        else:
            raise ValueError("Could not find latest date")

    url = f"{shop['base_url']}/{date_str}/"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, 'lxml')

    results = []
    tables = soup.find_all('table')

    for table in tables:
        # ヘッダーを確認
        headers = [th.get_text(strip=True) for th in table.find_all('th')]

        rows = table.find_all('tr')
        for row in rows[1:]:
            cells = row.find_all(['td', 'th'])
            if len(cells) >= 4:
                text = row.get_text()

                # 機種データっぽい行を探す
                machine_name = cells[0].get_text(strip=True)
                if not machine_name or machine_name.startswith('20'):
                    continue

                # 差枚、G数、勝率を抽出
                sabetsu = cells[1].get_text(strip=True) if len(cells) > 1 else ''
                games = cells[2].get_text(strip=True) if len(cells) > 2 else ''
                win_rate = cells[3].get_text(strip=True) if len(cells) > 3 else ''

                sabetsu_match = re.search(r'([+-]?\d[\d,]*)', sabetsu)
                games_match = re.search(r'(\d[\d,]*)', games)
                win_match = re.search(r'(\d+)/(\d+)', win_rate)

                results.append({
                    'shop_id': shop_id,
                    'shop_name': shop['name'],
                    'date': date_str,
                    'machine_name': machine_name,
                    'sabetsu': int(sabetsu_match.group(1).replace(',', '')) if sabetsu_match else 0,
                    'total_games': int(games_match.group(1).replace(',', '')) if games_match else 0,
                    'win_units': int(win_match.group(1)) if win_match else 0,
                    'total_units': int(win_match.group(2)) if win_match else 0,
                    'fetched_at': datetime.now().isoformat(),
                })

    return results


def filter_target_machines(data: list[dict]) -> list[dict]:
    """対象機種のみをフィルタ"""
    filtered = []
    for item in data:
        for target in TARGET_MACHINES:
            if target in item.get('machine_name', ''):
                filtered.append(item)
                break
    return filtered


def save_json(data: list[dict], filename: str):
    """JSONファイルとして保存"""
    path = Path('data/raw') / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"保存: {path} ({len(data)}件)")


def main():
    """メイン処理"""
    print("=" * 60)
    print("スロレポ データ収集")
    print("=" * 60)

    all_monthly = []
    all_daily = []

    for shop_id in SHOPS:
        print(f"\n【{SHOPS[shop_id]['name']}】")

        # 月間データ
        try:
            monthly = get_machine_monthly_data(shop_id)
            sbj_monthly = filter_target_machines(monthly)
            print(f"  月間データ: 全{len(monthly)}機種、SBJ: {len(sbj_monthly)}件")
            for item in sbj_monthly:
                print(f"    - {item['machine_name']}: {item['num_units']}台, "
                      f"今月{item['this_month_total']:+}枚 ({item['this_month_avg_per_day']:+}/台/日)")
            all_monthly.extend(sbj_monthly)
        except Exception as e:
            print(f"  月間データ取得エラー: {e}")

        # 日別データ（最新）
        try:
            daily = get_daily_machine_data(shop_id)
            sbj_daily = filter_target_machines(daily)
            print(f"  日別データ: 全{len(daily)}機種、SBJ: {len(sbj_daily)}件")
            for item in sbj_daily:
                print(f"    - {item['date']}: {item['sabetsu']:+}枚, "
                      f"{item['total_games']}G, 勝率{item['win_units']}/{item['total_units']}")
            all_daily.extend(sbj_daily)
        except Exception as e:
            print(f"  日別データ取得エラー: {e}")

    # 保存
    print("\n" + "=" * 60)
    today = datetime.now().strftime('%Y%m%d')
    save_json(all_monthly, f'sbj_monthly_{today}.json')
    save_json(all_daily, f'sbj_daily_{today}.json')


if __name__ == "__main__":
    main()
