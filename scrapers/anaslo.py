#!/usr/bin/env python3
"""
アナスロ（ana-slo.com）スクレイパー
店舗の日別データ（総差枚・平均差枚・勝率）と旧イベント日を取得
"""

from playwright.sync_api import sync_playwright
import re
import json
from datetime import datetime
from pathlib import Path
import time

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
BASE = 'https://ana-slo.com'

# 店舗URL（ホールデータ一覧）
ANASLO_STORES = {
    'shibuya_espass': {
        'name': 'エスパス日拓渋谷駅前新館',
        'slug': 'エスパス日拓渋谷駅前新館-データ一覧',
    },
    'shinjuku_espass': {
        'name': 'エスパス日拓新宿歌舞伎町店',
        'slug': 'エスパス日拓新宿歌舞伎町店-データ一覧',
    },
    'akiba_espass': {
        'name': 'エスパス日拓秋葉原駅前店',
        'slug': 'エスパス日拓秋葉原駅前店-データ一覧',
    },
    'seibu_shinjuku_espass': {
        'name': 'エスパス日拓西武新宿駅前店',
        'slug': 'エスパス日拓西武新宿駅前店-データ一覧',
    },
    'island_akihabara': {
        'name': 'アイランド秋葉原店',
        'slug': 'アイランド秋葉原店-データ一覧',
    },
}


def _create_browser(p):
    """Cloudflare対策済みブラウザを作成"""
    browser = p.chromium.launch(
        headless=True,
        args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
    )
    ctx = browser.new_context(
        user_agent=UA,
        viewport={'width': 1920, 'height': 1080},
    )
    page = ctx.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return browser, page


def get_store_daily_data(store_key: str) -> dict:
    """店舗の日別データ一覧を取得

    Returns:
        {
            'store_key': str,
            'store_name': str,
            'event_days': str,  # 旧イベント日
            'anniversary': str,  # 周年日
            'days': [{date, total_diff, avg_diff, avg_games, win_rate, wins, total_units}]
        }
    """
    store = ANASLO_STORES.get(store_key)
    if not store:
        print(f'⚠ 店舗未登録: {store_key}')
        return {}

    slug = store['slug']
    url = f'{BASE}/%E3%83%9B%E3%83%BC%E3%83%AB%E3%83%87%E3%83%BC%E3%82%BF/%E6%9D%B1%E4%BA%AC%E9%83%BD/{slug}/'

    print(f'アナスロ取得: {store["name"]}')
    print(f'  URL: {url}')

    result = {
        'store_key': store_key,
        'store_name': store['name'],
        'event_days': '',
        'anniversary': '',
        'fetched_at': datetime.now().isoformat(),
        'days': [],
    }

    with sync_playwright() as p:
        browser, page = _create_browser(p)

        try:
            page.goto(url, timeout=30000)
            page.wait_for_timeout(8000)

            title = page.title()
            if 'Cloudflare' in title or not title:
                print('  ⚠ Cloudflareブロック')
                browser.close()
                return result

            text = page.inner_text('body')

            # 旧イベント日
            event_match = re.search(r'旧イベント日\s+(.*?)(?:\n|周年)', text)
            if event_match:
                result['event_days'] = event_match.group(1).strip()
                print(f'  旧イベント日: {result["event_days"]}')

            # 周年日
            anni_match = re.search(r'周年日\s+(\d+月\d+日)', text)
            if anni_match:
                result['anniversary'] = anni_match.group(1)
                print(f'  周年日: {result["anniversary"]}')

            # 日別データ
            # パターン: 2026/01/27(火) +71,500 +170 4,403 41.3%(174/421)
            pattern = re.compile(
                r'(\d{4}/\d{2}/\d{2})\(.\)\s+'
                r'([+–-]?[\d,]+|–)\s+'
                r'([+–-]?[\d,]+|–)\s+'
                r'([\d,]+|–)\s+'
                r'([\d.]+%\((\d+)/(\d+)\)|–)'
            )

            for m in pattern.finditer(text):
                date_str = m.group(1).replace('/', '-')
                total_diff = m.group(2).replace(',', '').replace('–', '').replace('+', '')
                avg_diff = m.group(3).replace(',', '').replace('–', '').replace('+', '')
                avg_games = m.group(4).replace(',', '').replace('–', '')

                entry = {
                    'date': date_str,
                    'total_diff': int(total_diff) if total_diff else None,
                    'avg_diff': int(avg_diff) if avg_diff else None,
                    'avg_games': int(avg_games) if avg_games else None,
                }

                # 勝率
                if m.group(5) != '–':
                    entry['win_rate'] = float(m.group(5).split('%')[0])
                    entry['wins'] = int(m.group(6))
                    entry['total_units'] = int(m.group(7))
                else:
                    entry['win_rate'] = None

                # 符号復元
                orig_total = m.group(2)
                if orig_total.startswith('–') or orig_total.startswith('-'):
                    entry['total_diff'] = -(entry['total_diff'] or 0) if entry['total_diff'] else None

                orig_avg = m.group(3)
                if orig_avg.startswith('–') or orig_avg.startswith('-'):
                    entry['avg_diff'] = -(entry['avg_diff'] or 0) if entry['avg_diff'] else None

                result['days'].append(entry)

            print(f'  取得: {len(result["days"])}日分')

        finally:
            browser.close()

    return result


def collect_and_save(store_key: str = None) -> dict:
    """データ取得して保存"""
    save_dir = Path('data/anaslo')
    save_dir.mkdir(parents=True, exist_ok=True)

    stores = {store_key: ANASLO_STORES[store_key]} if store_key else ANASLO_STORES
    results = {}

    for sk in stores:
        data = get_store_daily_data(sk)
        if data and data.get('days'):
            save_path = save_dir / f'anaslo_{sk}.json'
            with open(save_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f'  保存: {save_path}')
            results[sk] = data

        # Cloudflare対策: 店舗間で待機
        if len(stores) > 1:
            print('  (30秒待機...)')
            time.sleep(30)

    return results


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='アナスロ データ取得')
    parser.add_argument('--store', '-s', default=None, help='店舗キー')
    args = parser.parse_args()

    collect_and_save(args.store)
