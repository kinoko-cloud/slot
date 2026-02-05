#!/usr/bin/env python3
"""
GitHub Actions用: daidata + papimo.jpから空き状況とリアルタイムデータを取得してJSONに保存
排他ロック付き — 複数プロセスの同時実行を防止
"""
# 排他ロック（最初に取得）
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from scripts.fetch_lock import acquire_lock, release_lock
_lock_fp = acquire_lock()

import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

JST = timezone(timedelta(hours=9))

# 店舗設定 (modelは半角カナでURLエンコード済み)
DAIDATA_STORES = {
    'shibuya_espass_sbj': {
        'hall_id': '100860',
        'name': '渋谷エスパス新館',
        'model_encoded': 'L%EF%BD%BD%EF%BD%B0%EF%BE%8A%EF%BE%9F%EF%BD%B0%EF%BE%8C%EF%BE%9E%EF%BE%97%EF%BD%AF%EF%BD%B8%EF%BD%BC%EF%BE%9E%EF%BD%AC%EF%BD%AF%EF%BD%B8',
        'units': ['3011', '3012', '3013'],
    },
    'shinjuku_espass_sbj': {
        'hall_id': '100949',
        'name': '新宿エスパス歌舞伎町',
        'model_encoded': 'L%EF%BD%BD%EF%BD%B0%EF%BE%8A%EF%BE%9F%EF%BD%B0%EF%BE%8C%EF%BE%9E%EF%BE%97%EF%BD%AF%EF%BD%B8%EF%BD%BC%EF%BE%9E%EF%BD%AC%EF%BD%AF%EF%BD%B8',
        'units': ['682', '683', '684', '685'],
    },
    'akiba_espass_sbj': {
        'hall_id': '100928',
        'name': '秋葉原エスパス駅前',
        'model_encoded': 'L%EF%BD%BD%EF%BD%B0%EF%BE%8A%EF%BE%9F%EF%BD%B0%EF%BE%8C%EF%BE%9E%EF%BE%97%EF%BD%AF%EF%BD%B8%EF%BD%BC%EF%BE%9E%EF%BD%AC%EF%BD%AF%EF%BD%B8',
        'units': ['2158', '2159', '2160', '2161'],
    },
    'seibu_shinjuku_espass_sbj': {
        'hall_id': '100950',
        'name': '西武新宿駅前エスパス',
        'model_encoded': 'L%EF%BD%BD%EF%BD%B0%EF%BE%8A%EF%BE%9F%EF%BD%B0%EF%BE%8C%EF%BE%9E%EF%BE%97%EF%BD%AF%EF%BD%B8%EF%BD%BC%EF%BE%9E%EF%BD%AC%EF%BD%AF%EF%BD%B8',
        'units': ['3185', '3186', '3187'],  # 4000番台は全て低貸のため除外
    },
    # === エスパス上野新館 (hall_id=100196) ===
    'ueno_espass_sbj': {
        'hall_id': '100196',
        'name': 'エスパス上野新館',
        'model_encoded': 'L%EF%BD%BD%EF%BD%B0%EF%BE%8A%EF%BE%9F%EF%BD%B0%EF%BE%8C%EF%BE%9E%EF%BE%97%EF%BD%AF%EF%BD%B8%EF%BD%BC%EF%BE%9E%EF%BD%AC%EF%BD%AF%EF%BD%B8',
        'units': ['3110', '3111', '3112', '3113'],
    },
    # === エスパス上野本館 (hall_id=100947) ===
    'ueno_honkan_espass_sbj': {
        'hall_id': '100947',
        'name': 'エスパス上野本館',
        'model_encoded': 'L%EF%BD%BD%EF%BD%B0%EF%BE%8A%EF%BE%9F%EF%BD%B0%EF%BE%8C%EF%BE%9E%EF%BE%97%EF%BD%AF%EF%BD%B8%EF%BD%BC%EF%BE%9E%EF%BD%AC%EF%BD%AF%EF%BD%B8',
        'units': ['3125', '3126', '3127'],
    },
    # === エスパス高田馬場 (hall_id=100915) ===
    'takadanobaba_espass_sbj': {
        'hall_id': '100915',
        'name': 'エスパス高田馬場',
        'model_encoded': 'L%EF%BD%BD%EF%BD%B0%EF%BE%8A%EF%BE%9F%EF%BD%B0%EF%BE%8C%EF%BE%9E%EF%BE%97%EF%BD%AF%EF%BD%B8%EF%BD%BC%EF%BE%9E%EF%BD%AC%EF%BD%AF%EF%BD%B8',
        'units': ['2060', '2061', '2062'],
    },
    # === エスパス赤坂見附 (hall_id=100952) ===
    'akasaka_espass_sbj': {
        'hall_id': '100952',
        'name': 'エスパス赤坂見附',
        'model_encoded': 'L%EF%BD%BD%EF%BD%B0%EF%BE%8A%EF%BE%9F%EF%BD%B0%EF%BE%8C%EF%BE%9E%EF%BE%97%EF%BD%AF%EF%BD%B8%EF%BD%BC%EF%BE%9E%EF%BD%AC%EF%BD%AF%EF%BD%B8',
        'units': ['2039', '2040', '2041'],
    },
    # === エスパス新大久保 (hall_id=100951) ===
    'shinokubo_espass_sbj': {
        'hall_id': '100951',
        'name': 'エスパス新大久保',
        'model_encoded': 'L%EF%BD%BD%EF%BD%B0%EF%BE%8A%EF%BE%9F%EF%BD%B0%EF%BE%8C%EF%BE%9E%EF%BE%97%EF%BD%AF%EF%BD%B8%EF%BD%BC%EF%BE%9E%EF%BD%AC%EF%BD%AF%EF%BD%B8',
        'units': ['3141', '3142', '3143', '3144'],
    },
    # === エスパス新小岩 (hall_id=100260) ===
    'shinkoiwa_espass_sbj': {
        'hall_id': '100260',
        'name': 'エスパス新小岩',
        'model_encoded': 'L%EF%BD%BD%EF%BD%B0%EF%BE%8A%EF%BE%9F%EF%BD%B0%EF%BE%8C%EF%BE%9E%EF%BE%97%EF%BD%AF%EF%BD%B8%EF%BD%BC%EF%BE%9E%EF%BD%AC%EF%BD%AF%EF%BD%B8',
        'units': ['2050', '2051', '2052'],
    },
    'seibu_shinjuku_espass_hokuto': {
        'hall_id': '100950',
        'name': 'エスパス西武新宿(北斗)',
        'model_encoded': None,
        'units': ['3138', '3139', '3140', '3141', '3142', '3143', '3144', '3145', '3146', '3147', '3148', '3149', '3150', '3151', '3165', '3166'],
    },
    # === 渋谷本館 (hall_id=100930) ===
    'shibuya_honkan_espass_sbj': {
        'hall_id': '100930',
        'name': '渋谷エスパス本館',
        'model_encoded': 'L%EF%BD%BD%EF%BD%B0%EF%BE%8A%EF%BE%9F%EF%BD%B0%EF%BE%8C%EF%BE%9E%EF%BE%97%EF%BD%AF%EF%BD%B8%EF%BD%BC%EF%BE%9E%EF%BD%AC%EF%BD%AF%EF%BD%B8',
        'units': ['3095', '3096', '3097'],
    },
    # === 北斗転生2 (detail page only, model_encoded不要) ===
    # Note: 台数が多いためタイムアウト時間を延長して取得
    'shibuya_espass_hokuto': {
        'hall_id': '100860',
        'name': 'エスパス渋谷新館(北斗)',
        'model_encoded': None,  # detail pageのみで取得
        'units': [str(i) for i in range(2046, 2068)] + [str(i) for i in range(2233, 2241)],
    },
    'shibuya_honkan_espass_hokuto': {
        'hall_id': '100930',
        'name': 'エスパス渋谷本館(北斗)',
        'model_encoded': None,
        'units': [str(i) for i in range(2013, 2020)] + [str(i) for i in range(2030, 2038)],
    },
    'shinjuku_espass_hokuto': {
        'hall_id': '100949',
        'name': 'エスパス歌舞伎町(北斗)',
        'model_encoded': None,
        'units': [str(i) for i in range(1, 38)] + [str(i) for i in range(125, 129)],
    },
    'akiba_espass_hokuto': {
        'hall_id': '100928',
        'name': 'エスパス秋葉原(北斗)',
        'model_encoded': None,
        'units': [str(i) for i in range(2011, 2020)] + [str(i) for i in range(2056, 2069)],
    },
}

# papimo.jp店舗設定
PAPIMO_STORES = {
    'island_akihabara_sbj': {
        'hall_id': '00031715',
        'name': 'アイランド秋葉原',
        'machine_id': '225010000',
        'units': [
            '1015', '1016', '1017', '1018', '1020', '1021', '1022', '1023',
            '1025', '1026', '1027', '1028', '1030', '1031',
        ],
    },
    'island_akihabara_hokuto': {
        'hall_id': '00031715',
        'name': 'アイランド秋葉原(北斗)',
        'machine_id': '225110007',
        # 2026-02-02更新: 0731-0738,0750-0757 → 0811-0818,0820-0825 (16台→14台に減台)
        'units': [f'{i:04d}' for i in range(811, 819)] + [f'{i:04d}' for i in range(820, 826)],
        'list_url': 'https://papimo.jp/h/00031715/hit/index_sort/225110007/1-20-1290529/83/1/0/0',
    },
}


def fetch_store_availability(page, hall_id: str, model_encoded: str, expected_units: list) -> dict:
    """daidata: 店舗の台一覧ページから空き状況を取得"""

    url = f"https://daidata.goraggio.com/{hall_id}/unit_list?model={model_encoded}&ballPrice=21.70&ps=S"
    print(f"  URL: {url}")

    try:
        page.goto(url, timeout=30000, wait_until='domcontentloaded')
        page.wait_for_timeout(5000)  # JSレンダリング待ち

        # 規約同意ボタンをクリック（daidataがスクレイピング対策で追加）
        try:
            accept_btn = page.locator('button:has-text("利用規約に同意する")')
            if accept_btn.count() > 0:
                accept_btn.click()
                page.wait_for_timeout(5000)  # 同意後のリロード待ち
                print("  Accepted terms")
        except Exception as e:
            print(f"  Terms button: {e}")
            pass

        # ポップアップを閉じる
        try:
            close_btn = page.locator('text="Close"')
            if close_btn.count() > 0:
                close_btn.first.click()
                page.wait_for_timeout(300)
        except:
            pass

        # ページ読み込み待機
        page.wait_for_timeout(2000)

        # HTMLを取得
        html = page.content()

        # 遊技中の台を検出
        playing = []
        empty = []

        for unit_id in expected_units:
            pattern = rf'<tr[^>]*>.*?<td[^>]*>(.*?)</td>\s*<td[^>]*>\s*<a[^>]*>\s*{unit_id}\s*</a>'
            match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)

            if match:
                first_td_content = match.group(1)
                if 'icon-user' in first_td_content:
                    playing.append(unit_id)
                    print(f"    {unit_id}: 遊技中")
                else:
                    empty.append(unit_id)
                    print(f"    {unit_id}: 空き")
            else:
                empty.append(unit_id)
                print(f"    {unit_id}: (not found, assuming empty)")

        return {
            'playing': sorted(playing),
            'empty': sorted(empty),
            'total': len(expected_units),
        }

    except Exception as e:
        print(f"  Error: {e}")
        return {
            'playing': [],
            'empty': expected_units,
            'total': len(expected_units),
            'error': str(e)
        }


def fetch_unit_detail(page, hall_id: str, unit_id: str) -> dict:
    """daidata: 台詳細ページからリアルタイムデータを取得"""
    url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}"

    try:
        page.goto(url, timeout=60000, wait_until='domcontentloaded')
        page.wait_for_timeout(3000)

        # 規約同意ボタンがある場合（店舗ごとに別セッション）
        try:
            accept_btn = page.locator('button:has-text("利用規約に同意する")')
            if accept_btn.count() > 0:
                accept_btn.click()
                page.wait_for_timeout(5000)
                # 規約同意後、元のdetailページに戻る
                page.goto(url, timeout=60000, wait_until='domcontentloaded')
                page.wait_for_timeout(3000)
                print(f"  unit {unit_id}: 規約同意完了")
        except:
            pass

        # テキストからデータを抽出（最大2回試行）
        text = page.inner_text('body', timeout=60000)

        data = {'unit_id': unit_id, 'bb': 0, 'rb': 0, 'art': 0, 'total_start': 0, 'final_start': 0}

        # BB/RB/ART/スタート回数を取得
        # パターン: BB RB ART スタート回数\n数値 数値 数値 数値
        match = re.search(r'BB\s+RB\s+ART\s+スタート回数\s*\n?\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)', text)

        # マッチしない場合、規約ページが表示されてる可能性 → リトライ
        if not match:
            try:
                page.goto(url, timeout=60000, wait_until='domcontentloaded')
                accept_btn = page.locator('text="利用規約に同意する"')
                if accept_btn.count() > 0:
                    accept_btn.click()
                    page.wait_for_timeout(2000)
                page.goto(url, timeout=60000, wait_until='domcontentloaded')
                page.wait_for_timeout(2000)
                text = page.inner_text('body', timeout=60000)
                match = re.search(r'BB\s+RB\s+ART\s+スタート回数\s*\n?\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)', text)
            except:
                pass
        if match:
            data['bb'] = int(match.group(1))
            data['rb'] = int(match.group(2))
            data['art'] = int(match.group(3))
            data['final_start'] = int(match.group(4))
        else:
            # 別のパターンを試す
            bb_match = re.search(r'BB[^\d]*(\d+)', text)
            rb_match = re.search(r'RB[^\d]*(\d+)', text)
            art_match = re.search(r'ART[^\d]*(\d+)', text)

            if bb_match:
                data['bb'] = int(bb_match.group(1))
            if rb_match:
                data['rb'] = int(rb_match.group(1))
            if art_match:
                data['art'] = int(art_match.group(1))

        # 累計スタート
        total_match = re.search(r'累計スタート\s*\n?\s*(\d+)', text)
        if total_match:
            data['total_start'] = int(total_match.group(1))

        # 差枚
        diff_match = re.search(r'差枚\s*\n?\s*([+-]?\d+)', text)
        if diff_match:
            data['diff_medals'] = int(diff_match.group(1))

        # 最大メダル（最大持ちコイン/最大持ち玉）
        max_match = re.search(r'(?:最大メダル|最大持ちコイン|最大枚数|最大持ち玉)\s*\n?\s*([\d,]+)', text)
        if max_match:
            data['max_medals'] = int(max_match.group(1).replace(',', ''))

        # 当日の全当たり履歴を取得（台詳細ページに直接表示されている）
        # daidataの形式: "0 スタート 出玉 種別 時間" のテーブル
        try:
            history = []
            # パターン: 0\tスタート\t出玉\t種別\t時間
            hits = re.findall(
                r'0\s+(\d+)\s+(\d+)\s+(ART|BB|RB|AT|REG)\s+(\d{1,2}:\d{2})',
                text
            )

            for i, match in enumerate(hits):
                history.append({
                    'hit_num': i + 1,
                    'time': match[3],
                    'start': int(match[0]),
                    'medals': int(match[1]),
                    'type': match[2],
                })

            if history:
                data['today_history'] = history
                # 最大連チャン数を計算（70G以内の連続当たり）
                # 履歴は時間降順（新しい順）なので逆順で計算
                sorted_hist = sorted(history, key=lambda h: h['time'])
                max_rensa = 1
                current_rensa = 1
                for j in range(1, len(sorted_hist)):
                    if sorted_hist[j]['start'] <= 70:
                        current_rensa += 1
                        max_rensa = max(max_rensa, current_rensa)
                    else:
                        current_rensa = 1
                data['today_max_rensa'] = max_rensa
        except Exception as e:
            print(f"    {unit_id}: 履歴取得エラー（スキップ）: {e}")

        print(f"    {unit_id}: ART={data.get('art', '?')}, G数={data.get('total_start', '?')}, "
              f"最大={data.get('max_medals', '?')}, 履歴={len(data.get('today_history', []))}件, 最大連={data.get('today_max_rensa', 0)}連")
        return data

    except Exception as e:
        print(f"    {unit_id}: Error - {e}")
        return {'unit_id': unit_id, 'bb': 0, 'rb': 0, 'art': 0, 'total_start': 0, 'final_start': 0, 'error': str(e)}


# ===== papimo.jp対応 =====

def fetch_papimo_availability(page, hall_id: str, machine_id: str, expected_units: list) -> dict:
    """papimo.jp: 台一覧ページから空き状況を取得"""
    url = f"https://papimo.jp/h/{hall_id}/hit/index_sort/{machine_id}/1-20-1274324"
    print(f"  URL: {url}")

    try:
        page.goto(url, timeout=20000, wait_until='domcontentloaded')
        page.wait_for_timeout(2000)

        html = page.content()

        # 遊技中の台を検出: <span class="badge-work">遊技中</span> の直後に台番号
        playing_matches = re.findall(r'badge-work[^>]*>遊技中</span>\s*(\d{4})', html)
        playing = [u for u in playing_matches if u in expected_units]

        # 空き = 全台 - 遊技中
        empty = [u for u in expected_units if u not in playing]

        for u in expected_units:
            status = '遊技中' if u in playing else '空き'
            print(f"    {u}: {status}")

        return {
            'playing': sorted(playing),
            'empty': sorted(empty),
            'total': len(expected_units),
        }

    except Exception as e:
        print(f"  Error: {e}")
        return {
            'playing': [],
            'empty': expected_units,
            'total': len(expected_units),
            'error': str(e)
        }


def fetch_papimo_unit_detail(page, hall_id: str, unit_id: str) -> dict:
    """papimo.jp: 台詳細ページから当日リアルタイムデータ+全当たり履歴を取得"""
    url = f"https://papimo.jp/h/{hall_id}/hit/view/{unit_id}"

    try:
        page.goto(url, timeout=20000, wait_until='domcontentloaded')
        page.wait_for_timeout(2000)

        # 「もっと見る」ボタンをクリックして全当たり履歴を表示
        for _ in range(50):  # 最大50回クリック（安全弁）
            try:
                more_btn = page.query_selector('text=もっと見る')
                if more_btn and more_btn.is_visible():
                    more_btn.click()
                    page.wait_for_timeout(300)
                else:
                    break
            except:
                break

        text = page.inner_text('body')

        data = {'unit_id': unit_id, 'bb': 0, 'rb': 0, 'art': 0, 'total_start': 0, 'final_start': 0}

        def parse_num(s):
            return int(s.replace(',', ''))

        # BB/RB/ART回数
        bb_match = re.search(r'BB回数\s*(\d+)', text)
        rb_match = re.search(r'RB回数\s*(\d+)', text)
        art_match = re.search(r'ART回数\s*(\d+)', text)

        if bb_match:
            data['bb'] = int(bb_match.group(1))
        if rb_match:
            data['rb'] = int(rb_match.group(1))
        if art_match:
            data['art'] = int(art_match.group(1))

        # 総スタート
        total_match = re.search(r'総スタート\s*([\d,]+)', text)
        if total_match:
            data['total_start'] = parse_num(total_match.group(1))

        # 最終スタート（= 現在のハマりG数）
        final_match = re.search(r'最終スタート\s*([\d,]+)', text)
        if final_match:
            data['final_start'] = parse_num(final_match.group(1))

        # 最大出メダル
        max_match = re.search(r'最大出メダル\s*([\d,]+)', text)
        if max_match:
            data['max_medals'] = parse_num(max_match.group(1))

        # 合成確率
        prob_match = re.search(r'合成確率\s*1/([\d,.]+)', text)
        if prob_match:
            data['combined_prob'] = parse_num(prob_match.group(1))

        # 当日の全当たり履歴（時間、スタート、出メダル、タイプ）
        history = []
        history_pattern = re.findall(
            r'(\d{1,2}:\d{2})\s+([\d,]+)\s+([\d,]+)\s*\n?\s*(ART|BB|RB|AT|REG)',
            text,
            re.MULTILINE
        )
        for i, match in enumerate(history_pattern):
            history.append({
                'hit_num': i + 1,
                'time': match[0],
                'start': parse_num(match[1]),
                'medals': parse_num(match[2]),
                'type': match[3],
            })

        if history:
            data['today_history'] = history
            # 最大連チャン数を計算（70G以内の連続当たり）
            max_rensa = 1
            current_rensa = 1
            for j in range(1, len(history)):
                if history[j]['start'] <= 70:
                    current_rensa += 1
                    max_rensa = max(max_rensa, current_rensa)
                else:
                    current_rensa = 1
            data['today_max_rensa'] = max_rensa

        print(f"    {unit_id}: ART={data.get('art', '?')}, G数={data.get('total_start', '?')}, "
              f"最大={data.get('max_medals', '?')}, 履歴={len(history)}件, 最大連={data.get('today_max_rensa', 0)}連")
        return data

    except Exception as e:
        print(f"    {unit_id}: Error - {e}")
        return {'unit_id': unit_id, 'bb': 0, 'rb': 0, 'art': 0, 'total_start': 0, 'final_start': 0, 'error': str(e)}


def main():
    import sys
    
    # オプション解析
    sbj_only = '--sbj-only' in sys.argv
    hokuto_only = '--hokuto-only' in sys.argv
    
    # 対象店舗をフィルタリング
    daidata_stores = DAIDATA_STORES
    papimo_stores = PAPIMO_STORES
    
    if sbj_only:
        daidata_stores = {k: v for k, v in DAIDATA_STORES.items() if 'sbj' in k}
        papimo_stores = {k: v for k, v in PAPIMO_STORES.items() if 'sbj' in k}
        print(f"SBJのみモード: {len(daidata_stores) + len(papimo_stores)}店舗")
    elif hokuto_only:
        daidata_stores = {k: v for k, v in DAIDATA_STORES.items() if 'hokuto' in k}
        papimo_stores = {k: v for k, v in PAPIMO_STORES.items() if 'hokuto' in k}
        print(f"北斗のみモード: {len(daidata_stores) + len(papimo_stores)}店舗")
    
    result = {
        'stores': {},
        'fetched_at': datetime.now(JST).isoformat(),
    }

    try:
      with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-gpu',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-extensions',
                '--disable-background-networking',
                '--disable-sync',
            ]
        )
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 900},
            java_script_enabled=True,
        )

        # 不要なリソースをブロック
        page = context.new_page()
        page.route("**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf}", lambda route: route.abort())
        page.route("**/google*", lambda route: route.abort())
        page.route("**/geniee*", lambda route: route.abort())
        page.route("**/doubleclick*", lambda route: route.abort())

        # ===== daidata規約同意（店舗ごとに必要）=====
        # daidataは利用規約画面がJSで表示され、「利用規約に同意する」ボタンをクリックしないと
        # データが見られない。店舗ごとにセッションが分かれるため、全店舗で同意が必要。
        agreed_halls = set()
        for config in daidata_stores.values():
            hall_id = config['hall_id']
            if hall_id in agreed_halls:
                continue
            try:
                page.goto(f'https://daidata.goraggio.com/{hall_id}/all_list?ps=S', wait_until='domcontentloaded', timeout=30000)
                page.wait_for_timeout(5000)  # JSレンダリング完了待ち
                # ボタンクリックで同意
                agree_btn = page.locator('button:has-text("利用規約に同意する")')
                if agree_btn.count() > 0:
                    agree_btn.click()
                    page.wait_for_timeout(5000)  # 同意後のページ更新待ち
                    print(f"daidata規約同意完了（ボタンクリック）: {hall_id} ({config['name']})")
                else:
                    # ボタンがない場合はformサブミットを試行
                    page.evaluate('() => { const form = document.querySelector("form"); if (form) form.submit(); }')
                    page.wait_for_timeout(3000)
                    print(f"daidata規約同意完了（formサブミット）: {hall_id} ({config['name']})")
                agreed_halls.add(hall_id)
            except Exception as e:
                print(f"daidata規約同意エラー（続行）: {hall_id} - {e}")

        # CI環境判定（Circle CI / GitHub Actions）
        # ===== daidata店舗 =====
        for store_key, config in daidata_stores.items():
            print(f"\n[daidata] Fetching {config['name']}...")

            # model_encodedがある場合のみ一覧ページで空き状況を取得
            if config.get('model_encoded'):
                avail_data = fetch_store_availability(
                    page,
                    config['hall_id'],
                    config['model_encoded'],
                    config['units']
                )
            else:
                # detail pageのみモード（北斗等）: 空き状況は各detail pageから判定
                avail_data = {
                    'playing': [],
                    'empty': list(config['units']),
                    'total': len(config['units']),
                }

            # 各台の詳細データを取得
            units_data = []
            print(f"  Fetching unit details...")
            for unit_id in config['units']:
                unit_data = fetch_unit_detail(page, config['hall_id'], unit_id)
                # model_encoded無しの場合、稼働データから空き判定
                if not config.get('model_encoded'):
                    if unit_data.get('total_start', 0) > 0 or unit_data.get('art', 0) > 0:
                        # データがあれば遊技中の可能性（detail pageでは正確に判定不可）
                        unit_data['availability'] = '不明'
                    else:
                        unit_data['availability'] = '空き'
                else:
                    if unit_id in avail_data.get('playing', []):
                        unit_data['availability'] = '遊技中'
                    else:
                        unit_data['availability'] = '空き'
                units_data.append(unit_data)

            result['stores'][store_key] = {
                'name': config['name'],
                'hall_id': config['hall_id'],
                'playing': avail_data.get('playing', []),
                'empty': avail_data.get('empty', []),
                'total': avail_data.get('total', len(config['units'])),
                'units': units_data,
            }

            print(f"  Done - Playing: {avail_data.get('playing', [])}, Empty: {avail_data.get('empty', [])}")

        # ===== papimo.jp店舗 =====
        for store_key, config in papimo_stores.items():
            print(f"\n[papimo] Fetching {config['name']}...")

            # 空き状況を取得
            avail_data = fetch_papimo_availability(
                page,
                config['hall_id'],
                config['machine_id'],
                config['units']
            )

            # 各台の詳細データを取得
            units_data = []
            print(f"  Fetching unit details...")
            for unit_id in config['units']:
                unit_data = fetch_papimo_unit_detail(page, config['hall_id'], unit_id)
                # 空き状況を追加
                if unit_id in avail_data.get('playing', []):
                    unit_data['availability'] = '遊技中'
                else:
                    unit_data['availability'] = '空き'
                units_data.append(unit_data)

            result['stores'][store_key] = {
                'name': config['name'],
                'hall_id': config['hall_id'],
                'playing': avail_data.get('playing', []),
                'empty': avail_data.get('empty', []),
                'total': avail_data.get('total', len(config['units'])),
                'units': units_data,
            }

            print(f"  Done - Playing: {avail_data.get('playing', [])}, Empty: {avail_data.get('empty', [])}")

        try:
            browser.close()
        except Exception as e:
            print(f"Warning: browser close error: {e}")

    except Exception as e:
        print(f"\nFATAL: Playwright crashed: {e}")
        print("Saving partial data...")

    # JSONに保存（クラッシュ時も部分データを書き出す）
    # --sbj-only や --hokuto-only の場合は部分更新（既存データ保持）
    _save_result(result, partial_update=(sbj_only or hokuto_only))


def _save_result(result, partial_update=False):
    """resultをavailability.jsonに書き込み
    
    Args:
        result: 取得したデータ
        partial_update: True=部分更新モード（既存データとマージ）
    """
    if not result.get('stores'):
        print("Warning: no store data to save")
        return

    output_path = Path(__file__).parent.parent / 'data' / 'availability.json'
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 部分更新モード：既存データとマージ
    if partial_update and output_path.exists():
        try:
            with open(output_path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
            # 既存店舗データを保持し、新規取得分だけ上書き
            merged_stores = existing.get('stores', {})
            for store_key, store_data in result.get('stores', {}).items():
                merged_stores[store_key] = store_data
            result['stores'] = merged_stores
            print(f"Partial update: merged {len(result['stores'])} stores (new: {len(result.get('stores', {}))})")
        except Exception as e:
            print(f"Warning: failed to merge existing data: {e}")

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\nSaved to {output_path}")
    print(f"Total stores: {len(result['stores'])}")
    for sk, sd in result['stores'].items():
        print(f"  {sk}: {len(sd.get('units', []))} units")


if __name__ == '__main__':
    main()
