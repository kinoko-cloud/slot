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

# 広告・ポップアップ削除スクリプト
REMOVE_ADS_SCRIPT = """
() => {
    // インタースティシャル広告を削除
    document.querySelectorAll('#gn_interstitial_outer_area, .gn_interstitial_outer_area').forEach(el => el.remove());
    // その他の広告要素を削除
    document.querySelectorAll('.yads_ad_item, [id*="google_ads"], [class*="ad-"]').forEach(el => el.remove());
}
"""

# 店舗設定（東京喰種のみ）
# model_encoded: L東京喰種 = L%E6%9D%B1%E4%BA%AC%E5%96%B0%E7%A8%AE
DAIDATA_STORES = {
    'shinjuku_espass_tokyoghoul': {
        'hall_id': '100949',
        'name': 'エスパス新宿(東京喰種)',
        'model_encoded': 'L%E6%9D%B1%E4%BA%AC%E5%96%B0%E7%A8%AE',
        'units': [
            '801', '802', '803', '804', '805', '806', '807', '808', '809', '810',
            '811', '812', '813', '814', '815', '830', '831', '832', '833', '834',
            '838', '839', '840', '841', '842', '843', '844', '845', '846', '847',
            '848', '849', '850', '851', '852', '853', '854', '855', '856', '857',
            '858', '859', '860',
        ],
    },
    'akiba_espass_tokyoghoul': {
        'hall_id': '100928',
        'name': 'エスパス秋葉原(東京喰種)',
        'model_encoded': 'L%E6%9D%B1%E4%BA%AC%E5%96%B0%E7%A8%AE',
        'units': [
            '4076', '4077', '4078', '4079', '4080', '4081', '4082', '4083', '4084', '4085', '4086',
            '4156', '4157', '4158', '4159', '4160', '4161', '4162', '4163', '4164',
            '4165', '4166', '4167', '4168', '4169', '4170', '4171', '4172',
        ],
    },
    'seibu_shinjuku_espass_tokyoghoul': {
        'hall_id': '100950',
        'name': 'エスパス西武新宿(東京喰種)',
        'model_encoded': 'L%E6%9D%B1%E4%BA%AC%E5%96%B0%E7%A8%AE',
        'units': [
            '3159', '3160', '3161', '3162', '3163', '3164', '3165', '3166', '3167', '3168',
            '3169', '3170', '3171', '3172', '3173', '3174', '3218',
        ],
    },
    'shibuya_espass_tokyoghoul': {
        'hall_id': '100860',
        'name': 'エスパス渋谷新館(東京喰種)',
        'model_encoded': 'L%E6%9D%B1%E4%BA%AC%E5%96%B0%E7%A8%AE',
        'units': [
            '2075', '2076', '2077', '2078', '2079', '2080', '2081', '2082', '2083', '2084',
            '2085', '2086', '2087', '2088', '2089', '2090', '2091', '2092', '2093', '2094',
            '2095', '2096', '2097', '2098', '2099', '2100', '2101', '2102', '2103', '2104',
            '2105', '2106', '2107',
        ],
    },
}

# papimo.jp店舗設定
# 2026-07-06: papimo.jp実地確認でアイランド秋葉原に東京喰種16台を確認（machine_id 125030007）
PAPIMO_STORES = {
    'island_akihabara_tokyoghoul': {
        'hall_id': '00031715',
        'machine_id': '125030007',
        'name': 'アイランド秋葉原',
        'units': [
            '162', '163', '165', '166', '167', '168', '170', '171',
            '172', '173', '175', '176', '177', '178', '180', '181',
        ],
    },
}


def fetch_store_availability(page, hall_id: str, model_encoded: str, expected_units: list) -> dict:
    """daidata: 店舗の台一覧ページから空き状況を取得"""

    url = f"https://daidata.goraggio.com/{hall_id}/unit_list?model={model_encoded}&ballPrice=21.70&ps=S"
    print(f"  URL: {url}")

    try:
        page.goto(url, timeout=15000, wait_until='domcontentloaded')
        page.wait_for_timeout(2000)  # JSレンダリング待ち
        page.evaluate(REMOVE_ADS_SCRIPT)  # 広告削除

        # 規約同意ボタンをクリック（daidataがスクレイピング対策で追加）
        try:
            accept_btn = page.locator('button:has-text("利用規約に同意する")')
            if accept_btn.count() > 0:
                accept_btn.click()
                page.wait_for_timeout(1500)  # 同意後のリダイレクト待ち（長めに）
                print("  Accepted terms, re-navigating...")
                # 同意後はトップページにリダイレクトされるため、再度unit_listに遷移
                page.goto(url, timeout=20000, wait_until='domcontentloaded')
                page.wait_for_timeout(2000)  # JSレンダリング待ち
                page.evaluate(REMOVE_ADS_SCRIPT)  # 広告削除（再度）
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


def fetch_unit_detail(page, hall_id: str, unit_id: str, last_hit_time: str = None) -> dict:
    """daidata: 台詳細ページからリアルタイムデータを取得（差分対応）
    
    Args:
        last_hit_time: 前回取得時の最新当たり時刻（例: "14:23"）。これ以降の履歴のみ取得
    """
    url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}"

    try:
        page.goto(url, timeout=20000, wait_until='domcontentloaded')
        page.wait_for_timeout(1500)
        page.evaluate(REMOVE_ADS_SCRIPT)  # 広告削除

        # 規約同意ボタンがある場合（店舗ごとに別セッション）
        try:
            accept_btn = page.locator('button:has-text("利用規約に同意する")')
            if accept_btn.count() > 0:
                accept_btn.click()
                page.wait_for_timeout(1500)  # 同意後のリダイレクト待ち（長めに）
                # 規約同意後、元のdetailページに戻る
                page.goto(url, timeout=20000, wait_until='domcontentloaded')
                page.wait_for_timeout(1500)
                page.evaluate(REMOVE_ADS_SCRIPT)  # 広告削除（再度）
                print(f"  unit {unit_id}: 規約同意完了")
        except:
            pass

        # テキストからデータを抽出（最大2回試行）
        text = page.inner_text('body', timeout=20000)

        data = {'unit_id': unit_id, 'bb': 0, 'rb': 0, 'art': 0, 'total_start': 0, 'final_start': 0}

        # BB/RB/ART/スタート回数を取得
        # パターン: BB RB ART スタート回数\n数値 数値 数値 数値
        match = re.search(r'BB\s+RB\s+ART\s+スタート回数\s*\n?\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)', text)

        # マッチしない場合、規約ページが表示されてる可能性 → リトライ
        if not match:
            try:
                page.goto(url, timeout=20000, wait_until='domcontentloaded')
                page.wait_for_timeout(1500)
                page.evaluate(REMOVE_ADS_SCRIPT)  # 広告削除
                accept_btn = page.locator('text="利用規約に同意する"')
                if accept_btn.count() > 0:
                    accept_btn.click()
                    page.wait_for_timeout(1500)
                page.goto(url, timeout=20000, wait_until='domcontentloaded')
                page.wait_for_timeout(2000)
                page.evaluate(REMOVE_ADS_SCRIPT)  # 広告削除
                text = page.inner_text('body', timeout=20000)
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
                hit_time = match[3]
                # 差分取得: last_hit_time以降の履歴のみ取得
                # 日付変更を考慮した時刻比較
                if last_hit_time:
                    # 時刻を時・分に分解して数値比較
                    try:
                        hit_h, hit_m = map(int, hit_time.split(':'))
                        last_h, last_m = map(int, last_hit_time.split(':'))
                        hit_minutes = hit_h * 60 + hit_m
                        last_minutes = last_h * 60 + last_m

                        # last_hit_timeが未来の時刻（＝昨日のデータ）かチェック
                        # 現在時刻より2時間以上未来なら昨日のデータと判断してスキップしない
                        now = datetime.now(JST)
                        current_minutes = now.hour * 60 + now.minute
                        is_last_from_yesterday = (last_minutes > current_minutes + 120)

                        # 昨日のデータでない場合のみ比較
                        if not is_last_from_yesterday and hit_minutes <= last_minutes:
                            break  # 時刻降順なので、ここで終了
                    except (ValueError, AttributeError):
                        # パースエラー時は比較せず全て取得
                        pass

                history.append({
                    'hit_num': i + 1,
                    'time': hit_time,
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


def fetch_papimo_unit_detail(page, hall_id: str, unit_id: str, last_hit_time: str = None, full_history: bool = False) -> dict:
    """papimo.jp: 台詳細ページから当日リアルタイムデータ+当たり履歴を取得（差分対応）
    
    Args:
        last_hit_time: 前回取得時の最新当たり時刻（例: "14:23"）。これ以降の履歴のみ取得
        full_history: Trueの場合は全履歴取得（日次収集用）
    """
    url = f"https://papimo.jp/h/{hall_id}/hit/view/{unit_id}"

    try:
        page.goto(url, timeout=20000, wait_until='domcontentloaded')
        page.wait_for_timeout(1500)

        # 全履歴が必要な場合のみ「もっと見る」をクリック
        if full_history:
            for _ in range(50):
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
            hit_time = match[0]
            # 差分取得: last_hit_time以降の履歴のみ取得
            if last_hit_time and hit_time <= last_hit_time:
                break  # 時刻降順なので、ここで終了
            history.append({
                'hit_num': i + 1,
                'time': hit_time,
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
    
    # 対象店舗をフィルタリング
    daidata_stores = DAIDATA_STORES
    papimo_stores = PAPIMO_STORES

    # 前回データを読み込み（差分取得用）
    prev_data = {'stores': {}}
    availability_path = Path(__file__).parent.parent / 'data' / 'availability.json'
    if availability_path.exists():
        try:
            with open(availability_path) as f:
                prev_data = json.load(f)
            print(f"前回データ読み込み: {len(prev_data.get('stores', {}))}店舗")
        except Exception as e:
            print(f"前回データ読み込みエラー（新規取得）: {e}")
    
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
                page.wait_for_timeout(2000)  # JSレンダリング完了待ち
                # ボタンクリックで同意
                agree_btn = page.locator('button:has-text("利用規約に同意する")')
                if agree_btn.count() > 0:
                    agree_btn.click()
                    page.wait_for_timeout(2000)  # 同意後のページ更新待ち
                    print(f"daidata規約同意完了（ボタンクリック）: {hall_id} ({config['name']})")
                else:
                    # ボタンがない場合はformサブミットを試行
                    page.evaluate('() => { const form = document.querySelector("form"); if (form) form.submit(); }')
                    page.wait_for_timeout(2000)
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

            # 各台の詳細データを取得（差分取得）
            units_data = []
            print(f"  Fetching unit details (incremental)...")
            
            # 前回データから最新hit時刻を取得
            prev_store = prev_data.get('stores', {}).get(store_key, {})
            prev_units = {u['unit_id']: u for u in prev_store.get('units', [])}
            
            for unit_id in config['units']:
                # 前回の最新hit時刻を取得
                prev_unit = prev_units.get(unit_id, {})
                prev_history = prev_unit.get('today_history', [])
                last_hit_time = prev_history[0]['time'] if prev_history else None
                
                # 差分取得
                unit_data = fetch_unit_detail(page, config['hall_id'], unit_id, last_hit_time=last_hit_time)
                
                # 差分を既存履歴にマージ（昨日のデータを検出してスキップ）
                new_history = unit_data.get('today_history', [])
                now_hour = datetime.now(JST).hour
                now_minute = datetime.now(JST).minute
                
                def is_stale_history(history):
                    """履歴が昨日のデータか判定（22時以降のデータを昨日と判定）"""
                    if not history:
                        return False
                    first_time = history[0].get('time', '')
                    if ':' in first_time:
                        h, m = map(int, first_time.split(':'))
                        # 22時台以降のデータを昨日と判定（閉店時間22:50考慮）
                        # ただし、現在時刻が22時以降の場合は今日のデータと判定
                        if now_hour < 22 and h >= 22:
                            return True
                    return False
                
                if new_history and prev_history:
                    # 新しい履歴が昨日のデータならスキップ
                    if is_stale_history(new_history):
                        print(f"    {unit_id}: 昨日のデータを検出、履歴をスキップ")
                        unit_data['today_history'] = []
                        unit_data['is_stale'] = True
                    else:
                        merged = new_history + [h for h in prev_history if h['time'] < new_history[-1]['time']] if new_history else prev_history
                        unit_data['today_history'] = merged
                elif prev_history and not new_history:
                    # 前回履歴が昨日のデータならスキップ
                    if is_stale_history(prev_history):
                        print(f"    {unit_id}: 前回履歴が昨日のデータ、スキップ")
                        unit_data['today_history'] = []
                        unit_data['is_stale'] = True
                    else:
                        unit_data['today_history'] = prev_history
                elif new_history:
                    if is_stale_history(new_history):
                        print(f"    {unit_id}: 新規履歴が昨日のデータ、スキップ")
                        unit_data['today_history'] = []
                        unit_data['is_stale'] = True
                
                # model_encoded無しの場合、稼働データから空き判定
                if not config.get('model_encoded'):
                    if unit_data.get('total_start', 0) > 0 or unit_data.get('art', 0) > 0:
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

            # 各台の詳細データを取得（差分取得）
            units_data = []
            print(f"  Fetching unit details (incremental)...")
            
            # 前回データから最新hit時刻を取得
            prev_store = prev_data.get('stores', {}).get(store_key, {})
            prev_units = {u['unit_id']: u for u in prev_store.get('units', [])}
            
            for unit_id in config['units']:
                # 前回の最新hit時刻を取得
                prev_unit = prev_units.get(unit_id, {})
                prev_history = prev_unit.get('today_history', [])
                last_hit_time = prev_history[0]['time'] if prev_history else None
                
                # papimoは常に全履歴取得（差分マージが複雑なため）
                unit_data = fetch_papimo_unit_detail(
                    page, config['hall_id'], unit_id,
                    last_hit_time=None,  # 差分取得しない
                    full_history=True    # 常に全履歴
                )
                
                # 差分を既存履歴にマージ（昨日のデータを検出してスキップ）
                new_history = unit_data.get('today_history', [])
                now_hour = datetime.now(JST).hour
                now_minute = datetime.now(JST).minute
                
                def is_stale_history(history):
                    """履歴が昨日のデータか判定（時刻が現在より未来なら昨日）"""
                    if not history:
                        return False
                    first_time = history[0].get('time', '')
                    if ':' in first_time:
                        h, m = map(int, first_time.split(':'))
                        if h > now_hour + 1 or (h == now_hour + 1 and m > now_minute):
                            return True
                    return False
                
                if new_history and prev_history:
                    if is_stale_history(new_history):
                        print(f"    {unit_id}: 昨日のデータを検出、履歴をスキップ")
                        unit_data['today_history'] = []
                        unit_data['is_stale'] = True
                    else:
                        merged = new_history + [h for h in prev_history if h['time'] < new_history[-1]['time']] if new_history else prev_history
                        unit_data['today_history'] = merged
                elif prev_history and not new_history:
                    if is_stale_history(prev_history):
                        print(f"    {unit_id}: 前回履歴が昨日のデータ、スキップ")
                        unit_data['today_history'] = []
                        unit_data['is_stale'] = True
                    else:
                        unit_data['today_history'] = prev_history
                elif new_history:
                    if is_stale_history(new_history):
                        print(f"    {unit_id}: 新規履歴が昨日のデータ、スキップ")
                        unit_data['today_history'] = []
                        unit_data['is_stale'] = True
                
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
    _save_result(result)


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
