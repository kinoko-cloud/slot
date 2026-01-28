#!/usr/bin/env python3
"""
サイトセブン（site777.jp）スクレイパー
無料部分から全台のBB/RB/ART/最高出玉を取得
"""

from playwright.sync_api import sync_playwright
import re
import json
from datetime import datetime, timedelta
from pathlib import Path

UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15'
BASE = 'https://m.site777.jp/f'

# 店舗設定
# pmc: サイトセブンの店舗コード
# urt: 貸玉区分（スロット台番号の範囲識別子）
# machines: {machine_key: mdc（機種コード）}
SITE777_STORES = {
    'shibuya_espass': {
        'name': 'エスパス渋谷新館',
        'pmc': '13031030',
        'urt': '2173',
        'machines': {
            'sbj': '120273',
            'hokuto_tensei2': '120343',
        },
    },
    # 他店舗は pmc が判明次第追加
}


def get_machine_data(pmc: str, mdc: str, urt: str, dtdd: int = 1, hall_name: str = '') -> list:
    """1機種・1日分の全台データを取得

    Args:
        pmc: 店舗コード
        mdc: 機種コード
        urt: 貸玉区分
        dtdd: 日付オフセット（0=今日, 1=昨日, ...7=7日前）
        hall_name: ログ用

    Returns:
        [{unit_id, bb, rb, art, max_medals}]
    """
    url = f'{BASE}/D3310.do?pmc={pmc}&mdc={mdc}&bn=1&soc=1&sw=1&pan=1&urt={urt}&dtdd={dtdd}'

    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()

        try:
            page.goto(url, timeout=30000)
            page.wait_for_timeout(3000)

            text = page.inner_text('body')
            lines = text.split('\n')

            # データ行をパース
            # ヘッダーは「台番」「BB回数」等が各行に分かれる
            # データ行は「3011\t0\t14\t71\t6422」のタブ区切り
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if '累計' in line or '月額' in line or '有料' in line:
                    continue
                # タブ区切りで5カラム（台番号 BB RB ART 最高出玉）
                parts = line.split('\t')
                if len(parts) >= 5:
                    try:
                        uid = parts[0].strip()
                        # 数字で始まる台番号のみ（「平均」等はスキップ）
                        if not uid or not uid[0].isdigit():
                            continue
                        entry = {
                            'unit_id': uid,
                            'bb': int(parts[1]),
                            'rb': int(parts[2]),
                            'art': int(parts[3]),
                            'max_medals': int(parts[4]),
                        }
                        results.append(entry)
                    except (ValueError, IndexError):
                        continue
        finally:
            browser.close()

    return results


def get_store_data(store_key: str, days_back: int = 1) -> dict:
    """1店舗の全機種・指定日数分のデータを取得

    Args:
        store_key: 店舗キー（SITE777_STORESのキー）
        days_back: 取得日数（1=昨日のみ, 7=過去7日分）

    Returns:
        {machine_key: [{unit_id, bb, rb, art, max_medals, date}]}
    """
    store = SITE777_STORES.get(store_key)
    if not store:
        print(f'⚠ 店舗未登録: {store_key}')
        return {}

    pmc = store['pmc']
    urt = store['urt']
    name = store['name']
    results = {}

    for machine_key, mdc in store['machines'].items():
        machine_data = []
        for dtdd in range(1, days_back + 1):
            target_date = (datetime.now() - timedelta(days=dtdd)).strftime('%Y-%m-%d')
            print(f'  {name} {machine_key} {target_date} (dtdd={dtdd})...', end='', flush=True)

            try:
                data = get_machine_data(pmc, mdc, urt, dtdd, name)
                for d in data:
                    d['date'] = target_date
                    d['machine_key'] = machine_key
                machine_data.extend(data)
                print(f' {len(data)}台')
            except Exception as e:
                print(f' エラー: {e}')

        results[machine_key] = machine_data

    return results


def collect_and_save(store_key: str = None, days_back: int = 1) -> dict:
    """データ取得して保存

    Args:
        store_key: 特定店舗のみ。Noneなら全店舗
        days_back: 取得日数
    """
    save_dir = Path('data/site777')
    save_dir.mkdir(parents=True, exist_ok=True)

    stores = {store_key: SITE777_STORES[store_key]} if store_key else SITE777_STORES
    all_results = {}

    for sk, store in stores.items():
        print(f'\n{"="*60}')
        print(f'サイトセブン: {store["name"]}')
        print(f'{"="*60}')

        data = get_store_data(sk, days_back)

        for mk, units in data.items():
            if units:
                date_str = units[0]['date'].replace('-', '')
                save_path = save_dir / f's777_{sk}_{mk}_{date_str}.json'
                with open(save_path, 'w', encoding='utf-8') as f:
                    json.dump({
                        'store_key': sk,
                        'store_name': store['name'],
                        'machine_key': mk,
                        'pmc': store['pmc'],
                        'mdc': store['machines'][mk],
                        'fetched_at': datetime.now().isoformat(),
                        'units': units,
                    }, f, ensure_ascii=False, indent=2)
                print(f'  保存: {save_path}')

        all_results[sk] = data

    return all_results


def merge_to_history(store_key: str = None):
    """サイトセブンデータをhistoryにマージ（max_medalsを追加）"""
    site777_dir = Path('data/site777')
    history_dir = Path('data/history')

    # store_keyとhistory_dirのマッピング
    STORE_TO_HISTORY = {
        'shibuya_espass': {
            'sbj': 'shibuya_espass_sbj',
            'hokuto_tensei2': 'shibuya_espass_hokuto_tensei2',
        },
    }

    for sk, mapping in STORE_TO_HISTORY.items():
        if store_key and sk != store_key:
            continue

        for mk, hist_dir_name in mapping.items():
            hdir = history_dir / hist_dir_name
            if not hdir.exists():
                continue

            # サイトセブンデータ読み込み
            s777_files = sorted(site777_dir.glob(f's777_{sk}_{mk}_*.json'))
            s777_by_unit = {}  # {unit_id: {date: data}}

            for f in s777_files:
                d = json.loads(f.read_text())
                for u in d.get('units', []):
                    uid = str(u['unit_id'])
                    date = u.get('date', '')
                    s777_by_unit.setdefault(uid, {})[date] = u

            # historyにマージ
            merged = 0
            for hf in hdir.glob('*.json'):
                hdata = json.loads(hf.read_text())
                uid = str(hdata.get('unit_id', hf.stem))
                changed = False

                for day in hdata.get('days', []):
                    date = day.get('date', '')
                    s777 = s777_by_unit.get(uid, {}).get(date)
                    if s777:
                        # max_medals_day（1日の最高出玉）をマージ
                        if s777.get('max_medals') and not day.get('max_medals_day'):
                            day['max_medals_day'] = s777['max_medals']
                            changed = True
                        # diff_medals_s777として保存
                        if s777.get('max_medals') and not day.get('diff_medals_s777'):
                            day['diff_medals_s777'] = s777['max_medals']
                            changed = True

                if changed:
                    hf.write_text(json.dumps(hdata, ensure_ascii=False, indent=2))
                    merged += 1

            if merged:
                print(f'  {hist_dir_name}: {merged}台マージ')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='サイトセブン データ取得')
    parser.add_argument('--store', '-s', default=None, help='店舗キー')
    parser.add_argument('--days', '-d', type=int, default=1, help='取得日数')
    parser.add_argument('--merge', action='store_true', help='historyにマージ')
    args = parser.parse_args()

    if args.merge:
        merge_to_history(args.store)
    else:
        collect_and_save(args.store, args.days)
