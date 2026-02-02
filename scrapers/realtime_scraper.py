#!/usr/bin/env python3
"""
リアルタイムスクレイパー
当日データのみを高速で取得

daidata: requestsベース（PythonAnywhereでも動作）
papimo: Playwrightベース（ローカル専用）

店舗定義はconfig/rankings.pyのSTORESを唯一のソースとする（重複定義を避ける）
"""

import json
import re
import sys
import requests
from datetime import datetime
from pathlib import Path

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

from config.rankings import STORES as CONFIG_STORES

# papimo用hall_id（config/rankings.pyではhall_id=Noneのため別途定義）
PAPIMO_HALL_IDS = {
    'island_akihabara_sbj': '00031715',
    'island_akihabara_hokuto': '00031715',
}

# 旧キー → 新キー互換マップ
_LEGACY_KEY_MAP = {
    'island_akihabara': 'island_akihabara_sbj',
    'shibuya_espass': 'shibuya_espass_sbj',
}


def _build_stores():
    """config/rankings.pyのSTORESからリアルタイムスクレイパー用の設定を構築"""
    stores = {}
    # 旧形式のキーは除外
    old_keys = {'island_akihabara', 'shibuya_espass', 'shinjuku_espass'}
    for store_key, cfg in CONFIG_STORES.items():
        if store_key in old_keys:
            continue
        if not cfg.get('units'):
            continue
        source = cfg.get('data_source', 'daidata')
        hall_id = PAPIMO_HALL_IDS.get(store_key) or cfg.get('hall_id')
        stores[store_key] = {
            'name': cfg['name'],
            'source': source,
            'hall_id': hall_id,
            'units': cfg['units'],
        }
    # 旧キー互換
    for old_key, new_key in _LEGACY_KEY_MAP.items():
        if new_key in stores:
            stores[old_key] = stores[new_key]
    return stores


STORES = _build_stores()


def scrape_papimo_current(page, hall_id: str, units: list) -> list:
    """papimo.jpから当日データを取得"""
    results = []

    for unit_id in units:
        url = f"https://papimo.jp/h/{hall_id}/hit/view/{unit_id}"

        try:
            page.goto(url, wait_until='load', timeout=20000)
            page.wait_for_timeout(1000)

            text = page.inner_text('body')

            # データ抽出
            import re
            data = {'unit_id': unit_id}

            # BB/RB/ART
            bb = re.search(r'BB回数\s*(\d+)', text)
            rb = re.search(r'RB回数\s*(\d+)', text)
            art = re.search(r'ART回数\s*(\d+)', text)
            total = re.search(r'総スタート\s*([\d,]+)', text)
            final = re.search(r'最終スタート\s*([\d,]+)', text)

            if bb: data['bb'] = int(bb.group(1))
            if rb: data['rb'] = int(rb.group(1))
            if art: data['art'] = int(art.group(1))
            if total: data['total_start'] = int(total.group(1).replace(',', ''))
            if final: data['final_start'] = int(final.group(1).replace(',', ''))

            # 履歴（もっと見るをクリック）
            while True:
                more_btn = page.query_selector('text=もっと見る')
                if more_btn and more_btn.is_visible():
                    more_btn.click()
                    page.wait_for_timeout(300)
                else:
                    break

            text = page.inner_text('body')
            history = []
            hist_matches = re.findall(
                r'(\d{1,2}:\d{2})\s+([\d,]+)\s+([\d,]+)\s*\n?\s*(ART|BB|RB|AT|REG)',
                text, re.MULTILINE
            )
            for i, m in enumerate(hist_matches):
                history.append({
                    'hit_num': i + 1,
                    'time': m[0],
                    'start': int(m[1].replace(',', '')),
                    'medals': int(m[2].replace(',', '')),
                    'type': m[3],
                })
            data['history'] = history

            results.append(data)
            print(f"  台{unit_id}: ART={data.get('art', 0)}, G数={data.get('total_start', 0)}")

        except Exception as e:
            print(f"  台{unit_id}: エラー - {e}")
            results.append({'unit_id': unit_id, 'error': str(e)})

    return results


def create_daidata_session(hall_id: str, debug_info: dict = None) -> requests.Session:
    """daidata用セッションを作成し、規約同意を行う"""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })

    try:
        # 規約同意ページにアクセス
        accept_url = f"https://daidata.goraggio.com/{hall_id}/accept"
        resp = session.get(accept_url, timeout=15)

        if debug_info is not None:
            debug_info['session_accept_status'] = resp.status_code
            debug_info['session_accept_length'] = len(resp.text)

        # CSRFトークンを取得してPOST
        if HAS_BS4:
            soup = BeautifulSoup(resp.text, 'html.parser')
            token_input = soup.find('input', {'name': '_token'})
            if token_input:
                token = token_input.get('value')
                post_resp = session.post(accept_url, data={'_token': token}, timeout=15)
                if debug_info is not None:
                    debug_info['session_post_status'] = post_resp.status_code
                    debug_info['csrf_method'] = 'bs4'
            else:
                if debug_info is not None:
                    debug_info['csrf_error'] = 'token not found (bs4)'
        else:
            # bs4がない場合は正規表現で
            match = re.search(r'name="_token"\s+value="([^"]+)"', resp.text)
            if match:
                post_resp = session.post(accept_url, data={'_token': match.group(1)}, timeout=15)
                if debug_info is not None:
                    debug_info['session_post_status'] = post_resp.status_code
                    debug_info['csrf_method'] = 'regex'
            else:
                if debug_info is not None:
                    debug_info['csrf_error'] = 'token not found (regex)'

    except Exception as e:
        if debug_info is not None:
            debug_info['session_error'] = str(e)

    return session


def scrape_daidata_current(session_or_page, hall_id: str, units: list, debug_info: dict = None) -> list:
    """台データオンラインから当日データを取得（requestsベース）"""
    results = []

    # セッション作成（規約同意）
    if isinstance(session_or_page, requests.Session):
        session = session_or_page
    else:
        session = create_daidata_session(hall_id)

    for unit_id in units:
        url = f"https://daidata.goraggio.com/{hall_id}/detail?unit={unit_id}"

        try:
            resp = session.get(url, timeout=15)
            text = resp.text

            data = {'unit_id': unit_id}

            # デバッグ情報（最初の台のみ）
            if debug_info is not None and len(results) == 0:
                debug_info['status_code'] = resp.status_code
                debug_info['html_length'] = len(text)
                debug_info['html_preview'] = text[:500] if text else 'empty'
                debug_info['has_bb_rB_art'] = 'BB' in text and 'RB' in text and 'ART' in text
                debug_info['has_start'] = 'スタート回数' in text
                debug_info['has_bs4'] = HAS_BS4

            # サマリーデータを取得（HTMLテーブルから）
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
                if debug_info is not None and len(results) == 0:
                    debug_info['match_type'] = 'html_table'
            else:
                # フォールバック: テキストからパース
                if HAS_BS4:
                    text_content = BeautifulSoup(text, 'html.parser').get_text()
                else:
                    text_content = re.sub(r'<[^>]+>', ' ', text)
                alt_match = re.search(r'BB\s+RB\s+ART\s+スタート回数\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)', text_content)
                if alt_match:
                    data['bb'] = int(alt_match.group(1))
                    data['rb'] = int(alt_match.group(2))
                    data['art'] = int(alt_match.group(3))
                    data['final_start'] = int(alt_match.group(4))
                    if debug_info is not None and len(results) == 0:
                        debug_info['match_type'] = 'text_fallback'
                else:
                    if debug_info is not None and len(results) == 0:
                        debug_info['match_type'] = 'no_match'
                        debug_info['text_preview'] = text_content[:300] if text_content else 'empty'

            # 累計スタート
            total_match = re.search(r'累計スタート\s*</th>\s*<td[^>]*>(\d+)</td>', text)
            if total_match:
                data['total_start'] = int(total_match.group(1))
            else:
                if HAS_BS4:
                    text_content = BeautifulSoup(text, 'html.parser').get_text()
                else:
                    text_content = re.sub(r'<[^>]+>', ' ', text)
                alt_match = re.search(r'累計スタート\s*(\d+)', text_content)
                if alt_match:
                    data['total_start'] = int(alt_match.group(1))

            # 当日の全当たり履歴を取得
            history = []
            try:
                if HAS_BS4:
                    text_content = BeautifulSoup(text, 'html.parser').get_text()
                else:
                    text_content = re.sub(r'<[^>]+>', ' ', text)
                # パターン: 時刻 スタート 出メダル タイプ
                hist_matches = re.findall(
                    r'(\d{1,2}:\d{2})\s+(\d+)\s+(\d+)\s+(ART|BB|RB|AT|REG)',
                    text_content
                )
                for i, m in enumerate(hist_matches):
                    history.append({
                        'hit_num': i + 1,
                        'time': m[0],
                        'start': int(m[1]),
                        'medals': int(m[2]),
                        'type': m[3],
                    })
            except Exception as e:
                print(f"    履歴取得エラー: {e}")
            
            data['history'] = history
            
            # 最大枚数・最大連チャンを計算
            if history:
                data['max_medals'] = max(h['medals'] for h in history) if history else 0
                # 連チャン計算（70G以内）
                max_rensa = 1
                current_rensa = 1
                sorted_hist = sorted(history, key=lambda h: h['time'])
                for j in range(1, len(sorted_hist)):
                    if sorted_hist[j]['start'] <= 70:
                        current_rensa += 1
                        max_rensa = max(max_rensa, current_rensa)
                    else:
                        current_rensa = 1
                data['max_rensa'] = max_rensa

            results.append(data)
            print(f"  台{unit_id}: ART={data.get('art', 0)}, G数={data.get('total_start', 0)}, 履歴={len(history)}件")

        except Exception as e:
            print(f"  台{unit_id}: エラー - {e}")
            results.append({'unit_id': unit_id, 'error': str(e)})
            if debug_info is not None and len(results) == 1:
                debug_info['exception'] = str(e)

    return results


def scrape_realtime(store_key: str = None) -> dict:
    """リアルタイムデータを取得"""
    results = {}
    now = datetime.now()

    stores_to_scrape = [store_key] if store_key else STORES.keys()

    # daidata用セッション（複数店舗で使いまわし可能）
    daidata_sessions = {}

    # Playwright用（papimoのみ）
    playwright_ctx = None
    browser = None
    page = None

    for key in stores_to_scrape:
        if key not in STORES:
            continue

        store = STORES[key]
        print(f"\n【{store['name']}】")

        try:
            debug_info = {'source': store['source'], 'hall_id': store.get('hall_id')}
            if store['source'] == 'daidata':
                # requestsベースで取得（PythonAnywhereでも動作）
                hall_id = store['hall_id']
                if hall_id not in daidata_sessions:
                    daidata_sessions[hall_id] = create_daidata_session(hall_id, debug_info)
                session = daidata_sessions[hall_id]
                data = scrape_daidata_current(session, hall_id, store['units'], debug_info)

            elif store['source'] == 'papimo':
                # Playwrightベース（ローカル専用）
                if not HAS_PLAYWRIGHT:
                    print("  Playwrightがインストールされていません")
                    data = [{'unit_id': u, 'error': 'Playwright not available'} for u in store['units']]
                else:
                    if playwright_ctx is None:
                        playwright_ctx = sync_playwright().start()
                        browser = playwright_ctx.chromium.launch(headless=True)
                        page = browser.new_page()
                    data = scrape_papimo_current(page, store['hall_id'], store['units'])
            else:
                continue

            results[key] = {
                'store_name': store['name'],
                'fetched_at': now.isoformat(),
                'units': data,
                'debug': debug_info,  # 空でも辞書として保持
            }
        except Exception as e:
            print(f"  エラー: {e}")
            results[key] = {
                'store_name': store['name'],
                'fetched_at': now.isoformat(),
                'units': [{'unit_id': u, 'error': str(e)} for u in store['units']],
            }

    # Playwright後処理
    if browser:
        browser.close()
    if playwright_ctx:
        playwright_ctx.stop()

    return results


def main():
    """メイン処理"""
    print("=" * 60)
    print("リアルタイムデータ取得（全機種対応）")
    print(f"取得時刻: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"対象店舗: {len([k for k in STORES if k not in _LEGACY_KEY_MAP])}店舗")
    print("=" * 60)

    # 全店舗のデータを取得
    results = scrape_realtime()

    # 保存
    save_path = Path('data/raw') / f'realtime_{datetime.now().strftime("%Y%m%d_%H%M")}.json'
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n✓ 保存: {save_path}")

    return results


if __name__ == "__main__":
    main()
