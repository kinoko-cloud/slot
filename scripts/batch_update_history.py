#!/usr/bin/env python3
"""
蓄積DBバッチ更新 — ブラウザセッション再利用で高速取得

既存の extract_day_history() / get_unit_history() をそのまま使いつつ
Playwright ブラウザを共有して per-unit のオーバーヘッドを削減する。
"""
import sys
import re
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.rankings import STORES, MACHINES
from analysis.history_accumulator import _accumulate_unit, load_unit_history
from scrapers.daidata_detail_history import (
    extract_day_history, _parse_overview_summary, REMOVE_ADS_SCRIPT
)

TARGET_DATE = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')


def get_missing_units(target_date: str):
    """更新が必要な台を収集"""
    old_keys = {'island_akihabara', 'shibuya_espass', 'shinjuku_espass'}
    daidata_by_hall = {}  # hall_id -> [task, ...]
    papimo_tasks = []

    for store_key, store_cfg in STORES.items():
        if store_key in old_keys:
            continue
        units = store_cfg.get('units', [])
        machine_key = store_cfg.get('machine', 'sbj')
        data_source = store_cfg.get('data_source', 'daidata')
        hall_id = store_cfg.get('hall_id')
        name = store_cfg.get('name', store_key)
        machine = MACHINES.get(machine_key, {})

        for uid in units:
            hist = load_unit_history(store_key, uid)
            dates = [d.get('date', '') for d in hist.get('days', [])]
            if target_date in dates:
                continue

            task = {
                'store_key': store_key, 'unit_id': uid,
                'machine_key': machine_key, 'hall_id': hall_id,
                'name': name, 'verify_kw': machine.get('verify_keywords'),
            }

            if data_source == 'papimo':
                papimo_tasks.append(task)
            else:
                hid = hall_id or '0'
                daidata_by_hall.setdefault(hid, []).append(task)

    return daidata_by_hall, papimo_tasks


def _scrape_unit_shared(page, hall_id, unit_id, hall_name, expected_machine=None):
    """get_all_history()のブラウザ共有版。既存のextract_day_historyを使う"""
    url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}"
    page.goto(url, wait_until='load', timeout=60000)
    page.wait_for_timeout(2500)
    page.evaluate(REMOVE_ADS_SCRIPT)

    result = {'unit_id': unit_id, 'hall_id': hall_id, 'days': []}

    text = page.inner_text('body')

    # 機種名バリデーション
    if expected_machine:
        machine_match = re.search(r'(L[ｱ-ﾝァ-ヶー\w]+)\s*\(', text)
        if machine_match:
            actual = machine_match.group(1)
            keywords = expected_machine if isinstance(expected_machine, list) else [expected_machine]
            missing = [kw for kw in keywords if kw not in actual]
            if missing:
                return {'machine_mismatch': True, 'actual': actual}

    # 概要データ（最大持ち玉・累計スタート）
    overview_by_date = _parse_overview_summary(text)

    # 「詳細を見る」リンクを取得
    detail_links = page.evaluate('''() => {
        const links = [];
        document.querySelectorAll('a').forEach(a => {
            if (a.innerText.includes('詳細を見る')) {
                links.push({ text: a.innerText.trim(), href: a.href });
            }
        });
        return links;
    }''')

    # 各詳細ページを巡回
    for link in detail_links:
        try:
            page.goto(link['href'], wait_until='load', timeout=60000)
            page.wait_for_timeout(2000)
            page.evaluate(REMOVE_ADS_SCRIPT)
            detail_text = page.inner_text('body')
            day_data = extract_day_history(detail_text, unit_id)

            if day_data and day_data.get('date'):
                # 概要データをマージ
                date_key = day_data['date']
                if date_key in overview_by_date:
                    ov = overview_by_date[date_key]
                    if ov.get('max_medals_day') and not day_data.get('max_medals_day'):
                        day_data['max_medals_day'] = ov['max_medals_day']
                    if ov.get('total_start') and not day_data.get('total_start'):
                        day_data['total_start'] = ov['total_start']
                result['days'].append(day_data)
        except Exception as e:
            print(f"      詳細ページエラー: {e}")

    return result


def run_daidata_batch(daidata_by_hall: dict):
    """daidata全店舗をバッチ処理（ブラウザ共有）"""
    from playwright.sync_api import sync_playwright

    total_added = 0
    total_units = sum(len(tasks) for tasks in daidata_by_hall.values())
    processed = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            '--disable-gpu', '--disable-dev-shm-usage', '--no-sandbox',
        ])

        for hall_id, tasks in daidata_by_hall.items():
            store_name = tasks[0]['name']
            print(f"\n{'='*60}")
            print(f"【{store_name}】hall_id={hall_id}, {len(tasks)}台")
            print(f"{'='*60}")

            page = browser.new_page(viewport={'width': 1280, 'height': 900})

            # TOS同意（1回だけ）
            try:
                page.goto(f"https://daidata.goraggio.com/{hall_id}/accept",
                          wait_until='load', timeout=30000)
                page.wait_for_timeout(2000)
                page.evaluate(REMOVE_ADS_SCRIPT)
                agree_btn = page.locator('button:has-text("利用規約に同意する")')
                if agree_btn.count() > 0:
                    agree_btn.first.click()
                    page.wait_for_timeout(3000)
                else:
                    page.evaluate('() => { const f = document.querySelector("form"); if(f) f.submit(); }')
                    page.wait_for_timeout(3000)
                print("  ✓ TOS同意")
            except Exception as e:
                print(f"  ⚠ TOS同意エラー（続行）: {e}")

            for task in tasks:
                uid = task['unit_id']
                sk = task['store_key']
                mk = task['machine_key']
                processed += 1

                print(f"  [{processed}/{total_units}] 台{uid} ({sk})...", end=" ", flush=True)
                try:
                    result = _scrape_unit_shared(page, hall_id, uid, store_name, task.get('verify_kw'))
                    if result.get('machine_mismatch'):
                        print(f"✗ 機種不一致({result.get('actual','')})")
                        continue

                    days = result.get('days', [])
                    if days:
                        added = _accumulate_unit(sk, uid, days, mk)
                        total_added += added
                        dates = [d.get('date','') for d in days]
                        print(f"✓ +{added}日 (取得{len(days)}日: {min(dates)}~{max(dates)})")
                    else:
                        print("⚠ データなし")
                except Exception as e:
                    print(f"✗ {e}")

            page.close()

        browser.close()

    return total_added


def run_papimo_batch(papimo_tasks: list):
    """papimo全台をバッチ処理（ブラウザ共有）"""
    from playwright.sync_api import sync_playwright
    from scrapers.papimo import get_unit_history, PAPIMO_CONFIG

    hall_id = PAPIMO_CONFIG['island_akihabara']['hall_id']
    total_added = 0

    print(f"\n{'='*60}")
    print(f"【PAPIMO アイランド秋葉原】{len(papimo_tasks)}台")
    print(f"{'='*60}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            '--disable-gpu', '--disable-dev-shm-usage', '--no-sandbox',
        ])
        page = browser.new_page(viewport={'width': 1280, 'height': 900})

        for i, task in enumerate(papimo_tasks):
            uid = task['unit_id']
            sk = task['store_key']
            mk = task['machine_key']

            # 差分日数
            hist = load_unit_history(sk, uid)
            existing_dates = [d.get('date','') for d in hist.get('days',[])]
            latest = max(existing_dates) if existing_dates else ''
            if latest:
                days_missing = (datetime.strptime(TARGET_DATE, '%Y-%m-%d') -
                                datetime.strptime(latest, '%Y-%m-%d')).days
                days_back = min(max(days_missing + 1, 2), 14)
            else:
                days_back = 14

            print(f"  [{i+1}/{len(papimo_tasks)}] 台{uid} ({sk}, {days_back}日)...", end=" ", flush=True)
            try:
                result = get_unit_history(page, hall_id, uid, days_back=days_back,
                                          expected_machine=task.get('verify_kw'))
                if result.get('machine_mismatch'):
                    print("✗ 機種不一致")
                    continue

                days = result.get('days', [])
                if days:
                    added = _accumulate_unit(sk, uid, days, mk)
                    total_added += added
                    print(f"✓ +{added}日 (取得{len(days)}日)")
                else:
                    print("⚠ データなし")
            except Exception as e:
                print(f"✗ {e}")

        browser.close()

    return total_added


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--target-date', '-d', default=None)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--daidata-only', action='store_true')
    parser.add_argument('--papimo-only', action='store_true')
    args = parser.parse_args()

    global TARGET_DATE
    if args.target_date:
        TARGET_DATE = args.target_date

    print(f"===== 蓄積DBバッチ更新 =====")
    print(f"target: {TARGET_DATE}")
    print(f"時刻: {datetime.now().strftime('%H:%M:%S')}")

    daidata_by_hall, papimo_tasks = get_missing_units(TARGET_DATE)
    n_dai = sum(len(v) for v in daidata_by_hall.values())
    n_pap = len(papimo_tasks)

    print(f"\n更新必要: daidata={n_dai}台 ({len(daidata_by_hall)}店), papimo={n_pap}台")
    for hid, tasks in daidata_by_hall.items():
        print(f"  [{hid}] {tasks[0]['name']}: {len(tasks)}台")
    if papimo_tasks:
        print(f"  [papimo] アイランド: {n_pap}台")

    if n_dai + n_pap == 0:
        print("✅ 全台が最新！")
        return

    if args.check_only:
        return

    start = time.time()
    total = 0

    if not args.papimo_only and daidata_by_hall:
        total += run_daidata_batch(daidata_by_hall)

    if not args.daidata_only and papimo_tasks:
        total += run_papimo_batch(papimo_tasks)

    elapsed = time.time() - start
    print(f"\n===== 完了: {total}日分追加 ({elapsed:.0f}秒) =====")

    # 残り確認
    rd, rp = get_missing_units(TARGET_DATE)
    rem = sum(len(v) for v in rd.values()) + len(rp)
    if rem == 0:
        print("✅ 全台が最新！")
    else:
        print(f"⚠ まだ{rem}台が未更新")


if __name__ == '__main__':
    main()
