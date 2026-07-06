#!/usr/bin/env python3
"""
scrapers_v2/fetch_all.py - v2統合スクレイパー

特徴:
- 台番号の自動検出（discovery）を毎回実行
- 差分取得（G数変化台のみ詳細取得）
- 並列取得対応
- v1のavailability.jsonと互換性維持
"""
import sys
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional

# パス設定
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / 'scripts' / 'scrapers_v2'))
sys.path.insert(0, str(ROOT / 'scripts'))

from daidata.scraper import DaidataScraper
from daidata.discovery import DaidataDiscovery
from papimo.scraper import PapimoScraper, PAPIMO_STORES as PAPIMO_CONFIG
from common.base import setup_logger, now_jst
from common.games_cache import get_changed_units, save_cache
from unit_status import (
    is_unit_active, mark_unit_missing, mark_unit_found,
    get_stopped_units, get_pending_units
)

# v1の店舗設定をインポート
from fetch_daidata_availability import DAIDATA_STORES, PAPIMO_STORES
sys.path.insert(0, str(ROOT))
from analysis.diff_medals_estimator import estimate_diff_medals

logger = setup_logger('fetch_all')

JST = timezone(timedelta(hours=9))


def fetch_with_retry(scraper, hall_id: str, unit_id: str, max_time: int = 60, machine_key: str = None) -> Dict[str, Any]:
    """
    取れるまでリトライする詳細取得
    
    Args:
        scraper: DaidataScraperインスタンス
        hall_id: ホールID
        unit_id: 台番号
        max_time: 最大試行時間（秒）
        machine_key: 機種キー（sbj/hokuto2）。指定時は機種名チェックを行う
    
    Returns:
        取得データ。success=Trueなら成功、Falseなら失敗
    """
    import time as time_module
    
    start_time = time_module.time()
    delays = [2, 4, 8, 8, 8, 8, 8, 8]  # リトライ間隔（秒）
    attempt = 0
    
    while True:
        elapsed = time_module.time() - start_time
        if elapsed > max_time:
            logger.error(f"⚠️ {hall_id}/{unit_id}: {max_time}秒経過しても取得できず（異常事態）")
            return {'unit_id': unit_id, 'success': False, 'error': 'max_time_exceeded'}
        
        data = scraper.fetch_realtime(hall_id, unit_id, expected_machine=machine_key)

        # 台変動（パチンコ・見つからない・機種不一致）はリトライせず即返す
        if data.get('machine_mismatch') or data.get('not_found'):
            data['success'] = True  # 取得自体は成功（台変動を正常検知）
            return data

        # 成功判定: 必須フィールドがパースできているか
        # （値が0でもパースできていれば成功）
        has_data = 'bb' in data and 'art' in data and data.get('error') is None

        if has_data:
            data['success'] = True
            return data
        
        # 失敗 → リトライ
        attempt += 1
        delay = delays[min(attempt - 1, len(delays) - 1)]
        logger.debug(f"{hall_id}/{unit_id}: 取得失敗、{delay}秒後にリトライ (試行{attempt})")
        time_module.sleep(delay)


class V2Fetcher:
    """v2統合フェッチャー"""
    
    def __init__(self, headless: bool = True, discover: bool = True):
        self.headless = headless
        self.discover = discover  # 台番号自動検出を行うか
        self.results = {}
        self._previous_availability = self._load_previous_availability()
        self._date_changed = self._check_date_changed()
        if self._date_changed:
            logger.info("📅 日付変更検出 → 全台強制取得モード")
    
    def _check_date_changed(self) -> bool:
        """前回取得日と現在日が異なるかチェック"""
        fetched_at = self._previous_availability.get('fetched_at', '')
        if not fetched_at:
            return True  # 前回データなし → 強制取得
        try:
            today = now_jst().strftime('%Y-%m-%d')
            daily_reset = self._previous_availability.get('daily_reset', '')
            if daily_reset and daily_reset[:10] == today:
                # games_cacheがリセット後に更新済みなら差分モードOK（初回fast path済み）
                if self._is_games_cache_fresh(daily_reset):
                    logger.info("📅 日次リセット後2回目以降 → 差分モードで動作（hit history取得）")
                    return False
                logger.info("📅 日次リセット直後（初回）→ 高速パスで動作")
                return True
            prev_date = fetched_at[:10]
            return prev_date != today
        except:
            return True  # パース失敗 → 強制取得

    def _is_games_cache_fresh(self, reset_time: str) -> bool:
        """games_cacheがリセット後に更新済みか確認（初回fast path完了の判定）"""
        cache_dir = ROOT / 'data' / '.games_cache'
        if not cache_dir.exists():
            return False
        today = now_jst().strftime('%Y-%m-%d')
        for cache_file in cache_dir.glob('*.json'):
            try:
                data = json.load(open(cache_file))
                updated_at = data.get('updated_at', '')
                # 今日のリセット後に更新されたキャッシュがあれば初回fast path完了
                if updated_at[:10] == today and updated_at > reset_time:
                    return True
            except:
                pass
        return False
        
    def _load_previous_availability(self) -> Dict:
        """前回のavailability.jsonを読み込む"""
        avail_path = ROOT / 'data' / 'availability.json'
        if avail_path.exists():
            try:
                with open(avail_path) as f:
                    return json.load(f)
            except:
                pass
        return {}
    
    def _get_previous_unit_data(self, store_key: str, unit_id: str) -> Dict:
        """前回の台データを取得（availability.json + historyファイルから補完）"""
        result = {}
        
        # まずavailability.jsonから
        stores = self._previous_availability.get('stores', {})
        store = stores.get(store_key, {})
        units = store.get('units', [])
        for u in units:
            if str(u.get('unit_id')) == str(unit_id):
                result = dict(u)
                break
        
        # historyファイルから今日のデータを補完
        history_file = ROOT / 'data' / 'history' / store_key / f"{unit_id}.json"
        if history_file.exists():
            try:
                with open(history_file) as f:
                    hist = json.load(f)
                days = hist.get('days', [])
                today = now_jst().strftime('%Y-%m-%d')
                for d in days:
                    if d.get('date') == today:
                        # historyファイルにデータがあれば補完
                        if d.get('art', 0) > 0 and not result.get('art'):
                            result['art'] = d.get('art', 0)
                        if d.get('bb', 0) > 0 and not result.get('bb'):
                            result['bb'] = d.get('bb', 0)
                        if d.get('rb', 0) > 0 and not result.get('rb'):
                            result['rb'] = d.get('rb', 0)
                        if d.get('final_start', 0) > 0 and not result.get('final_start'):
                            result['final_start'] = d.get('final_start', 0)
                        # historyが空ならhistoryファイルから補完
                        if not result.get('history') and d.get('history'):
                            result['history'] = d.get('history', [])
                        break
            except:
                pass
        
        return result
        
    def fetch_store_daidata(self, store_key: str, config: Dict, max_retries: int = 2) -> Dict[str, Any]:
        """
        1店舗のdaidataデータ取得（リトライ付き）
        
        1. 一覧ページでG数＋空き/遊技中を一括取得
        2. G数が変化した台のみ詳細取得（差分取得）
        3. G数変化なしの台は前回のキャッシュを使用
        """
        hall_id = config['hall_id']
        model_encoded = config['model_encoded']
        expected_units = config.get('units', [])
        
        result = {
            'store_key': store_key,
            'name': config.get('name', store_key),
            'units': {},
            'playing': [],
            'empty': [],
            'changed_count': 0,
            'skipped_count': 0,
            'fetched_at': None,
            'error': None,
        }
        
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    logger.info(f"⟳ {store_key}: リトライ {attempt}/{max_retries}")
                    import time
                    time.sleep(2)  # リトライ前に少し待機
                
                return self._fetch_store_daidata_inner(store_key, config, result.copy())
            except Exception as e:
                last_error = e
                error_str = str(e)
                # EPIPE, timeout, その他のブラウザクラッシュ系エラーはリトライ
                if 'EPIPE' in error_str or 'timeout' in error_str.lower() or 'Target page' in error_str:
                    logger.warning(f"⚠️ {store_key}: ブラウザエラー ({error_str[:50]}...), リトライします")
                    continue
                else:
                    # その他のエラーはリトライせず終了
                    break
        
        result['error'] = str(last_error)
        logger.error(f"✗ {store_key}: {last_error}")
        return result
    
    def _fetch_store_daidata_inner(self, store_key: str, config: Dict, result: Dict) -> Dict[str, Any]:
        """fetch_store_daidataの内部実装"""
        hall_id = config['hall_id']
        model_encoded = config['model_encoded']
        expected_units = config.get('units', [])
        
        scraper = DaidataScraper(headless=self.headless)
        
        with scraper.browser_session():
            # model_encoded=Noneの場合のみ詳細ページで取得（旧方式）
            # model_encodedがあれば一覧から動的取得（北斗2含む）
            machine_key = store_key.rsplit('_', 1)[-1]  # sbj / yoshimune / toloveru 等
            if model_encoded is None:
                # 一覧ページからスタート回数を先に取得（詳細ページfinal_start=0の補完用）
                starts_map = {}
                if model_encoded:
                    try:
                        list_data = scraper.fetch_list_with_availability(
                            hall_id, model_encoded, expected_units
                        )
                        starts_map = list_data.get('starts', {})
                        logger.info(f"{store_key}: 一覧ページstarts取得: {len(starts_map)}台")
                    except Exception as e:
                        logger.warning(f"{store_key}: 一覧ページ取得失敗 ({e}), 詳細のみで継続")
                for unit_id in expected_units:
                    # 停止中の台はスキップ
                    if not is_unit_active(store_key, unit_id):
                        logger.debug(f"{store_key}/{unit_id}: 停止中のためスキップ")
                        result['skipped_count'] += 1
                        continue
                    
                    detail = fetch_with_retry(scraper, hall_id, unit_id, max_time=60, machine_key=machine_key)

                    if detail.get('success'):
                        # 台変動（パチンコ・見つからない・機種不一致）
                        if detail.get('machine_mismatch') or detail.get('not_found'):
                            reason = detail.get('error', 'unknown')
                            new_status = mark_unit_missing(store_key, unit_id, reason)
                            logger.info(f"{store_key}/{unit_id}: 台変動検知 → {new_status}")
                            result['skipped_count'] += 1
                            continue
                        # 正常取得 → activeに戻す（pending/stoppedから復帰）
                        mark_unit_found(store_key, unit_id)
                        # 詳細ページでfinal_start=0の場合、一覧ページのスタート回数で補完
                        if detail.get('final_start', 0) == 0 and starts_map.get(unit_id, 0) > 0:
                            detail['final_start'] = starts_map[unit_id]
                            logger.debug(f"{store_key}/{unit_id}: final_start補完 → {starts_map[unit_id]}")
                        result['units'][unit_id] = detail
                        result['changed_count'] += 1
                    else:
                        # 60秒取得できなかった → 異常事態、警告出して前回データ保持
                        logger.error(f"🚨 {store_key}/{unit_id}: 取得失敗（台番号確認が必要）")
                        prev_data = self._get_previous_unit_data(store_key, unit_id)
                        result['units'][unit_id] = {
                            'unit_id': unit_id,
                            'total_start': prev_data.get('total_start', 0),
                            'art': prev_data.get('art', 0),
                            'bb': prev_data.get('bb', 0),
                            'rb': prev_data.get('rb', 0),
                            'final_start': prev_data.get('final_start', 0),
                            'diff_medals': prev_data.get('diff_medals', 0),
                            'today_history': prev_data.get('history', []),
                            'fetch_failed': True,
                            'status': 'unknown',
                        }
                        result['skipped_count'] += 1
                
                result['fetched_at'] = now_jst().isoformat()
                logger.info(f"✓ {result['store_key']}: {result['changed_count']}台取得, {result['skipped_count']}台スキップ (詳細のみモード)")
                return result
            
            # 一覧ページでG数＋空き/遊技中を取得
            # 動的台番号取得: 設定の台番号ではなく、一覧で見つかった全台を対象にする
            list_data = scraper.fetch_list_with_availability(
                hall_id, model_encoded, expected_units
            )
            
            result['playing'] = list_data.get('playing', [])
            result['empty'] = list_data.get('empty', [])
            
            # G数・ART・スタート回数マップ
            games_map = list_data.get('games', {})
            arts_map = list_data.get('arts', {})
            starts_map = list_data.get('starts', {})  # スタート回数（現在のハマり）
            
            # 一覧で見つかった台番号を使用（設定に依存しない）
            detected_units = sorted(games_map.keys())
            if not detected_units:
                logger.warning(f"⚠️ {store_key}: 一覧ページに台が見つからない")
                result['error'] = 'no_units_detected'
                return result
            
            # 台番号変更の検出（警告のみ、処理は続行）
            expected_set = set(expected_units)
            detected_set = set(detected_units)
            if expected_set != detected_set:
                missing = expected_set - detected_set
                added = detected_set - expected_set
                if missing:
                    logger.warning(f"⚠️ {store_key}: 設定から消えた台: {sorted(missing)}")
                if added:
                    logger.info(f"🆕 {store_key}: 新規検出台: {sorted(added)}")
                logger.info(f"📋 {store_key}: 検出された台番号で処理続行: {detected_units}")
            
            # G数が変化した台を特定（差分取得）
            changed_units = get_changed_units(store_key, games_map)

            for unit_id in detected_units:
                # 停止中の台はスキップ
                if not is_unit_active(store_key, unit_id):
                    logger.debug(f"{store_key}/{unit_id}: 停止中のためスキップ")
                    result['skipped_count'] += 1
                    continue
                
                # 一覧ページに台がない場合 → 台変動検知
                if unit_id not in games_map:
                    new_status = mark_unit_missing(store_key, unit_id, 'not_in_list')
                    logger.info(f"{store_key}/{unit_id}: 一覧に存在しない → {new_status}")
                    result['skipped_count'] += 1
                    continue
                
                games = games_map.get(unit_id, 0)

                # 🔧 日次リセット直後（_date_changed=True）は全台個別フェッチをスキップ
                # → 一覧ページのARTを使って高速完了（タイムアウト防止）
                # 履歴は後続のbatch_update/auto-recoveryが補完する
                if self._date_changed and unit_id in changed_units:
                    mark_unit_found(store_key, unit_id)
                    prev_data = self._get_previous_unit_data(store_key, unit_id)
                    prev_art = prev_data.get('art', 0)
                    prev_hist = prev_data.get('history', prev_data.get('today_history', []))
                    if prev_art > 0 and prev_hist:
                        # 既取得済みデータを引き継ぐ
                        result['units'][unit_id] = prev_data
                        result['units'][unit_id]['total_start'] = games
                        result['skipped_count'] += 1
                    else:
                        art = arts_map.get(unit_id, 0)
                        logger.debug(f"{store_key}/{unit_id}: 日次リセット・G数変化 → 一覧ARTを使用 (art={art})")
                        result['units'][unit_id] = {
                            'unit_id': unit_id,
                            'total_start': games,
                            'art': art,
                            'bb': 0,
                            'rb': 0,
                            'final_start': starts_map.get(unit_id, 0),
                            'diff_medals': 0,
                            'history': [],
                            'date': now_jst().strftime('%Y-%m-%d'),
                        }
                        result['changed_count'] += 1
                    continue

                # G数が変化した台のみ詳細取得
                if unit_id in changed_units:
                    # 機種キーをstore_keyから判定
                    detail = fetch_with_retry(scraper, hall_id, unit_id, max_time=60, machine_key=machine_key)

                    if not detail.get('success'):
                        # 60秒取得できなかった → 異常事態
                        logger.error(f"🚨 {store_key}/{unit_id}: 取得失敗（台番号確認が必要）")
                        prev_data = self._get_previous_unit_data(store_key, unit_id)
                        result['units'][unit_id] = {
                            'unit_id': unit_id,
                            'total_start': games,
                            'art': prev_data.get('art', 0),
                            'bb': prev_data.get('bb', 0),
                            'rb': prev_data.get('rb', 0),
                            'final_start': prev_data.get('final_start', 0),
                            'diff_medals': prev_data.get('diff_medals', 0),
                            'today_history': prev_data.get('history', []),
                            'fetch_failed': True,
                            'status': 'unknown',
                        }
                        result['skipped_count'] += 1
                        continue

                    # 台変動（パチンコ・見つからない・機種不一致）
                    if detail.get('machine_mismatch') or detail.get('not_found'):
                        reason = detail.get('error', 'unknown')
                        new_status = mark_unit_missing(store_key, unit_id, reason)
                        logger.info(f"{store_key}/{unit_id}: 台変動検知 → {new_status}")
                        result['skipped_count'] += 1
                        continue
                    # 正常取得 → activeに戻す
                    mark_unit_found(store_key, unit_id)

                    # 詳細ページの日付が今日かチェック
                    today = now_jst().strftime('%Y-%m-%d')
                    detail_date = detail.get('date', '')
                    is_today_data = (detail_date == today) or (detail.get('art', 0) > 0 and detail.get('today_history'))
                    
                    # 詳細ページでART=0の場合、一覧ページのARTを使う
                    # ただし、日付変更後で詳細が今日のデータでない場合は0のまま（昨日データ混入防止）
                    if detail.get('art', 0) == 0 and arts_map.get(unit_id, 0) > 0:
                        if not self._date_changed or is_today_data:
                            detail['art'] = arts_map[unit_id]
                        else:
                            logger.info(f"{store_key}/{unit_id}: 日付変更後、一覧ARTをスキップ（昨日データの可能性）")

                    # 詳細ページでfinal_start=0の場合、一覧ページのスタート回数を使う
                    if detail.get('final_start', 0) == 0 and starts_map.get(unit_id, 0) > 0:
                        if not self._date_changed or is_today_data:
                            detail['final_start'] = starts_map[unit_id]

                    result['units'][unit_id] = detail
                    result['changed_count'] += 1
                else:
                    # G数変化なし → 前回のデータを使用
                    # 一覧ページに台があるのでactiveに戻す（pendingから復帰）
                    mark_unit_found(store_key, unit_id)
                    # availability.jsonから前回のデータを取得
                    prev_data = self._get_previous_unit_data(store_key, unit_id)

                    # 🔧 日付変更時: G数変化なし = 今日まだプレイされていない台
                    # 個別フェッチするとN台×最大60sでタイムアウトする → 一覧ページのARTを使用
                    if self._date_changed:
                        # 既に良いデータ（history付き）があれば引き継ぐ
                        prev_art = prev_data.get('art', 0)
                        prev_hist = prev_data.get('history', prev_data.get('today_history', []))
                        if prev_art > 0 and prev_hist:
                            # 既取得済みデータを引き継ぐ（上書き禁止）
                            result['units'][unit_id] = prev_data
                            result['units'][unit_id]['total_start'] = games
                            result['skipped_count'] += 1
                        else:
                            art = arts_map.get(unit_id, 0)
                            logger.debug(f"{store_key}/{unit_id}: 日付変更・G数変化なし → 一覧ARTを使用 (art={art})")
                            result['units'][unit_id] = {
                                'unit_id': unit_id,
                                'total_start': games,
                                'art': art,
                                'bb': 0,
                                'rb': 0,
                                'final_start': starts_map.get(unit_id, 0),
                                'diff_medals': 0,
                                'history': [],
                                'date': now_jst().strftime('%Y-%m-%d'),
                            }
                            result['changed_count'] += 1
                        continue

                    # 🔧 空データ/履歴なし自動復旧: 前回データが空、または履歴なしなら強制的に詳細取得
                    prev_art = prev_data.get('art', 0)
                    prev_hist = prev_data.get('history', [])
                    needs_force_fetch = (
                        (prev_art == 0 and prev_data.get('total_start', 0) == 0) or
                        (prev_art > 0 and len(prev_hist) == 0)  # ART/履歴矛盾
                    )
                    
                    if needs_force_fetch:
                        reason = "前回データが空" if prev_art == 0 else "ART/履歴矛盾"
                        logger.warning(f"{store_key}/{unit_id}: {reason} → 強制詳細取得")
                        detail = fetch_with_retry(scraper, hall_id, unit_id, max_time=60, machine_key=machine_key)
                        
                        if not detail.get('success'):
                            logger.error(f"🚨 {store_key}/{unit_id}: 取得失敗（台番号確認が必要）")
                            result['units'][unit_id] = {
                                'unit_id': unit_id,
                                'total_start': games,
                                'art': prev_art,
                                'bb': prev_data.get('bb', 0),
                                'rb': prev_data.get('rb', 0),
                                'fetch_failed': True,
                                'status': 'unknown',
                            }
                            result['skipped_count'] += 1
                        else:
                            # 詳細ページでART=0の場合、一覧ページのARTを使う
                            if detail.get('art', 0) == 0 and arts_map.get(unit_id, 0) > 0:
                                detail['art'] = arts_map[unit_id]
                            result['units'][unit_id] = detail
                            result['changed_count'] += 1
                    else:
                        # 正常な前回データがある場合は引き継ぐ
                        # スタート回数は一覧ページから最新値を取得
                        final_start = starts_map.get(unit_id, 0) or prev_data.get('final_start', 0)
                        result['units'][unit_id] = {
                            'unit_id': unit_id,
                            'total_start': games,
                            'art': prev_data.get('art', 0),
                            'bb': prev_data.get('bb', 0),
                            'rb': prev_data.get('rb', 0),
                            'final_start': final_start,  # 一覧ページから取得
                            'diff_medals': prev_data.get('diff_medals', 0),
                            'today_history': prev_data.get('history', []),  # 履歴も引き継ぐ
                            'cached': True,
                            'status': 'empty' if unit_id in result['empty'] else 'playing',
                        }
                        result['skipped_count'] += 1
            
            result['fetched_at'] = now_jst().isoformat()
            logger.info(f"✓ {store_key}: {result['changed_count']}台取得, {result['skipped_count']}台スキップ (G数変化なし)")
        
        return result
    
    def fetch_store_papimo(self, store_key: str, machine_key: str) -> Dict[str, Any]:
        """
        1店舗のpapimoデータ取得（リアルタイム）
        """
        result = {
            'store_key': f"{store_key}_{machine_key}",
            'name': f"アイランド秋葉原 {machine_key.upper()}",
            'units': {},
            'fetched_at': None,
            'error': None,
        }
        
        try:
            scraper = PapimoScraper(headless=self.headless)
            
            # 最新1日分だけ取得（リアルタイム用）
            data = scraper.fetch(store_key=store_key, machine_key=machine_key, days_back=1)
            
            for unit_data in data.get('units', []):
                unit_id = unit_data.get('unit_id')
                days = unit_data.get('days', [])
                
                if days:
                    today = days[0]
                    result['units'][unit_id] = {
                        'unit_id': unit_id,
                        'art': today.get('art', 0),
                        'bb': today.get('bb', 0),
                        'rb': today.get('rb', 0),
                        'total_start': today.get('total_start', 0),
                        'final_start': today.get('final_start', 0),  # スタート数
                        'status': today.get('status', 'unknown'),
                        'today_history': today.get('history', []),
                    }
                else:
                    result['units'][unit_id] = {
                        'unit_id': unit_id,
                        'art': 0,
                        'bb': 0,
                        'rb': 0,
                        'total_start': 0,
                        'status': 'empty',  # データなし=空台
                        'today_history': [],
                    }
            
            result['fetched_at'] = now_jst().isoformat()
            logger.info(f"✓ {result['store_key']}: {len(result['units'])}台取得")
            
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"✗ {store_key}_{machine_key}: {e}")
        
        return result
    
    def fetch_all_papimo(self) -> Dict[str, Any]:
        """papimo全店舗を取得"""
        results = {}
        
        for store_key, config in PAPIMO_CONFIG.items():
            for machine_key in config.get('machines', {}).keys():
                full_key = f"{store_key}_{machine_key}"
                results[full_key] = self.fetch_store_papimo(store_key, machine_key)
        
        return results
    
    def fetch_all_daidata(self, stores: Dict = None, parallel: int = 1) -> Dict[str, Any]:
        """
        全daidata店舗を取得
        
        Args:
            stores: 店舗設定（デフォルトはDAIDATA_STORES）
            parallel: 並列数（1=直列）
        """
        stores = stores or DAIDATA_STORES
        results = {}
        
        if parallel <= 1:
            # 直列実行
            for store_key, config in stores.items():
                results[store_key] = self.fetch_store_daidata(store_key, config)
        else:
            # 並列実行
            with ThreadPoolExecutor(max_workers=parallel) as executor:
                futures = {
                    executor.submit(self.fetch_store_daidata, k, v): k
                    for k, v in stores.items()
                }
                for future in as_completed(futures):
                    store_key = futures[future]
                    try:
                        results[store_key] = future.result()
                    except Exception as e:
                        results[store_key] = {'error': str(e)}
        
        return results
    
    def to_v1_format(self, results: Dict) -> Dict:
        """
        v1のavailability.json形式に変換
        """
        ts_str = now_jst().isoformat()
        v1_data = {
            'last_updated': ts_str,
            'fetched_at': ts_str,  # ヘルスチェック用
            'stores': {}
        }
        
        for store_key, data in results.items():
            if data.get('error'):
                continue
            
            store_data = {
                'name': data.get('name', store_key),
                'units': [],
            }
            
            for unit_id, unit_data in data.get('units', {}).items():
                # 履歴からdiff_medalsとmax_medalsを計算
                today_history = unit_data.get('today_history', [])
                total_start = unit_data.get('total_start', 0)

                # total_startが0でも履歴があれば履歴から計算
                if total_start == 0 and today_history:
                    total_start = sum(h.get('start', 0) for h in today_history)

                # 総獲得枚数を計算
                total_medals = sum(h.get('medals', 0) for h in today_history) if today_history else 0
                # max_medalsはPapimoから直接取得した値を優先、なければhistoryから計算
                max_medals = unit_data.get('max_medals', 0)
                if not max_medals and today_history:
                    max_medals = max((h.get('medals', 0) for h in today_history), default=0)

                # 差枚: スクレイパーが取得した値（DaIData）を優先、なければestimate_diff_medalsで推定
                site_diff = unit_data.get('diff_medals')
                if site_diff is not None:
                    diff_medals = site_diff
                elif today_history and total_start > 0:
                    mk = store_key.rsplit('_', 1)[-1]
                    diff_medals = estimate_diff_medals(total_medals, total_start, mk)
                else:
                    diff_medals = 0
                
                # statusを日本語のavailabilityに変換
                status = unit_data.get('status', 'unknown')
                availability_map = {
                    'playing': '遊技中',
                    'empty': '空き',
                    'unknown': '?'
                }
                availability = availability_map.get(status, '?')

                unit_entry = {
                    'unit_id': unit_id,
                    'art': unit_data.get('art', 0),
                    'bb': unit_data.get('bb', 0),
                    'rb': unit_data.get('rb', 0),
                    'total_start': total_start,  # 履歴から計算済み
                    'games': total_start,  # 履歴から計算済み
                    'final_start': unit_data.get('final_start', 0),  # 現在のスタート数（ハマり）
                    'availability': availability,  # v1形式
                    'history': today_history,      # v1形式（today_history → history）
                    'diff_medals': diff_medals,
                    'max_medals': max_medals,
                }
                # データページから取得した日付があれば追加
                if unit_data.get('date'):
                    unit_entry['date'] = unit_data.get('date')
                store_data['units'].append(unit_entry)
            
            v1_data['stores'][store_key] = store_data
        
        return v1_data
    
    def save_availability(self, results: Dict, path: Path = None):
        """availability.jsonに保存"""
        path = path or ROOT / 'data' / 'availability.json'
        v1_data = self.to_v1_format(results)

        # 取得対象のconfig台番号マップを構築（余分な台を除外するため）
        # Note: units: [] の店舗は動的取得なので除外しない
        config_units_map = {}  # store_key -> set of unit_ids (None = 除外しない)
        for store_key in results:
            # DAIDATA_STORESからconfigの台番号を取得
            cfg = DAIDATA_STORES.get(store_key)
            if cfg:
                units = cfg.get('units', [])
                if units:  # 空でない場合のみフィルタリング
                    config_units_map[store_key] = set(str(u) for u in units)
                else:
                    # units: [] は動的取得なので除外しない
                    config_units_map[store_key] = None
            else:
                # Papimo等: 取得できた台のみ
                config_units_map[store_key] = None

        # configにない台を除外（merge時のデータ混入を防ぐ）
        for store_key, store_data in v1_data['stores'].items():
            expected = config_units_map.get(store_key)
            if expected is not None:
                before = len(store_data['units'])
                store_data['units'] = [
                    u for u in store_data['units']
                    if str(u.get('unit_id')) in expected
                ]
                after = len(store_data['units'])
                if before != after:
                    logger.info(f"{store_key}: config外の台を除外 {before}台→{after}台")

        # 既存データとマージ（今回取得していない店舗のデータを保持）
        if path.exists():
            try:
                with open(path) as f:
                    existing = json.load(f)
                for k, v in existing.get('stores', {}).items():
                    if k not in v1_data['stores']:
                        v1_data['stores'][k] = v
                # daily_resetを今日の日付なら引き継ぐ（翌フェッチでも高速パスを維持するため）
                existing_reset = existing.get('daily_reset', '')
                if existing_reset and existing_reset[:10] == now_jst().strftime('%Y-%m-%d'):
                    v1_data['daily_reset'] = existing_reset
            except:
                pass

        with open(path, 'w') as f:
            json.dump(v1_data, f, ensure_ascii=False, indent=2)

        logger.info(f"Saved to {path}")


def discover_all_units() -> Dict[str, List[str]]:
    """
    全店舗の台番号を自動検出
    
    Returns:
        {store_key: detected_units} - 変更があった店舗のみ
    """
    discovery = DaidataDiscovery(headless=True)
    updates = {}
    
    logger.info("=== 台番号自動検出 ===")
    
    with discovery.browser_session():
        for store_key, config in DAIDATA_STORES.items():
            hall_id = config['hall_id']
            machine_key = store_key.rsplit('_', 1)[-1]
            expected = set(config.get('units', []))
            
            result = discovery.discover_units(hall_id, machine_key)
            detected = set(u['unit_id'] for u in result.get('units', []))
            machine_name = result.get('machine_name', '')
            
            # 差分を計算
            missing = expected - detected  # 設定にあるが検出されない（消えた台）
            added = detected - expected    # 検出されたが設定にない（増えた台）
            
            if missing or added:
                logger.warning(f"⚠️ {store_key}: 台番号変更検出 [{machine_name}]")
                if missing:
                    logger.warning(f"   🔴 消えた台: {sorted(missing)}")
                if added:
                    logger.warning(f"   🟢 増えた台: {sorted(added)}")
                logger.warning(f"   設定: {sorted(expected)}")
                logger.warning(f"   検出: {sorted(detected)}")
                updates[store_key] = sorted(detected)
            else:
                logger.info(f"✓ {store_key}: OK ({len(detected)}台) [{machine_name}]")
    
    return updates


# 主要店舗（15分間隔でリアルタイム取得）
PRIORITY_STORES = [
    'shibuya_espass',  # shibuya_honkan_espass は 2026-03-31 閉店
    'shinjuku_espass', 'seibu_shinjuku_espass',
    'akiba_espass',
    'island_akihabara',
]

# サブ店舗（1日2回: 22:50, 00:10）
SUB_STORES = [
    'akasaka_espass',
    'ueno_espass', 'ueno_honkan_espass',
    'takadanobaba_espass',
    'shinokubo_espass',
    'shinkoiwa_espass',
]


def is_priority_store(store_key: str) -> bool:
    """主要店舗かどうか"""
    base = store_key.rsplit('_', 1)[0]  # _sbj, _yoshimune, _toloveru等を除去
    return any(p in store_key or p == base for p in PRIORITY_STORES)


def is_sub_store(store_key: str) -> bool:
    """サブ店舗かどうか"""
    base = store_key.rsplit('_', 1)[0]
    return any(s in store_key or s == base for s in SUB_STORES)


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='v2統合スクレイパー')
    parser.add_argument('--discover', action='store_true', help='台番号自動検出のみ')
    parser.add_argument('--tokyoghoul-only', action='store_true', help='東京喰種のみ')
    parser.add_argument('--daidata-only', action='store_true', help='daidataのみ（papimoスキップ）')
    parser.add_argument('--papimo-only', action='store_true', help='papimoのみ')
    parser.add_argument('--priority-only', action='store_true', help='主要店舗のみ（渋谷/新宿/秋葉原）')
    parser.add_argument('--sub-only', action='store_true', help='サブ店舗のみ（赤坂/上野/高田馬場/新大久保/新小岩）')
    parser.add_argument('--parallel', type=int, default=1, help='並列数')
    parser.add_argument('--store', type=str, help='特定店舗のみ')
    args = parser.parse_args()
    
    if args.discover:
        # 台番号検出 + stores.py自動更新
        from sync_stores import main as sync_stores_main
        sync_stores_main(do_update=True)
        return
    
    fetcher = V2Fetcher(headless=True, discover=False)
    start = time.time()
    all_results = {}
    
    # daidata取得
    if not args.papimo_only:
        stores = DAIDATA_STORES.copy()
        if args.tokyoghoul_only:
            stores = {k: v for k, v in stores.items() if 'tokyoghoul' in k}
        if args.priority_only:
            stores = {k: v for k, v in stores.items() if is_priority_store(k)}
        elif args.sub_only:
            stores = {k: v for k, v in stores.items() if is_sub_store(k)}
        if args.store:
            stores = {k: v for k, v in stores.items() if args.store in k}
        
        daidata_results = fetcher.fetch_all_daidata(stores, parallel=args.parallel)
        all_results.update(daidata_results)
    
    # papimo取得（island_akihabaraは主要店舗）
    if not args.daidata_only and not args.sub_only:
        papimo_results = fetcher.fetch_all_papimo()
        all_results.update(papimo_results)
    
    # 保存
    fetcher.save_availability(all_results)
    
    # historyファイルも更新（差枚・最大・連チャンを反映）
    try:
        import subprocess
        result = subprocess.run(
            ['python3', str(ROOT / 'scripts' / 'update_history_from_availability.py')],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            logger.info("✅ historyファイル更新完了")
        else:
            logger.warning(f"⚠️ historyファイル更新失敗: {result.stderr[:100]}")
    except Exception as e:
        logger.warning(f"⚠️ historyファイル更新エラー: {e}")
    
    elapsed = time.time() - start
    success = sum(1 for r in all_results.values() if not r.get('error'))
    logger.info(f"完了: {success}/{len(all_results)}店舗, {elapsed:.1f}秒")


if __name__ == '__main__':
    main()
