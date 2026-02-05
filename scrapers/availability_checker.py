#!/usr/bin/env python3
"""
空き状況チェッカー
- GAS経由: papimo.jp (アイランド秋葉原)
- GitHub JSON or ローカル: daidata (エスパス系)
"""

import json
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict

JST = timezone(timedelta(hours=9))

# GAS WebアプリURL (papimo.jp用)
GAS_URL = "https://script.google.com/macros/s/AKfycbxPxFOrfhytabAS9R8xg_PJbFFXAWsTuBIciJMaYdil3BxlV0-XL3yPYSvQHxyuRO_7/exec"

# GitHub raw JSON URL (daidata用)
GITHUB_JSON_URL = "https://raw.githubusercontent.com/kinoko-cloud/slot/main/data/availability.json"

# ローカルJSONパス
LOCAL_JSON_PATH = Path(__file__).parent.parent / 'data' / 'availability.json'

# GASでサポートしている店舗 (papimo.jp) - GAS fallback用
GAS_STORES = ['island_akihabara_sbj']

# GitHubでサポートしている店舗 (availability.json経由 = daidata + papimo)
GITHUB_STORES = [
    'shibuya_espass_sbj', 'shinjuku_espass_sbj', 'akiba_espass_sbj',
    'seibu_shinjuku_espass_sbj', 'island_akihabara_sbj',
    # 北斗転生2
    'shibuya_espass_hokuto', 'shinjuku_espass_hokuto', 'akiba_espass_hokuto',
    'island_akihabara_hokuto',
    # _tensei2サフィックスのエイリアス
    'shibuya_espass_hokuto_tensei2', 'shinjuku_espass_hokuto_tensei2', 'akiba_espass_hokuto_tensei2',
    'island_akihabara_hokuto_tensei2', 'seibu_shinjuku_espass_hokuto_tensei2',
    'shibuya_honkan_espass_hokuto_tensei2',
]


def get_availability_from_gas() -> Dict[str, Dict]:
    """
    GASから全店舗の空き状況を取得 (papimo.jp)
    """
    try:
        response = requests.get(GAS_URL, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching from GAS: {e}")
        return {}


def get_availability_from_local() -> Dict[str, Dict]:
    """
    ローカルファイルから空き状況を取得
    """
    try:
        if LOCAL_JSON_PATH.exists():
            with open(LOCAL_JSON_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data
    except Exception as e:
        print(f"Error reading local file: {e}")
    return {}


def get_availability_from_github() -> Dict[str, Dict]:
    """
    GitHubから空き状況JSONを取得 (daidata)
    """
    try:
        response = requests.get(GITHUB_JSON_URL, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data
    except Exception as e:
        print(f"Error fetching from GitHub: {e}")
        return {}


def get_daidata_availability() -> Dict[str, Dict]:
    """
    daidata空き状況を取得（ローカル優先、なければGitHub）
    """
    # ローカルファイルを確認
    local_data = get_availability_from_local()

    if local_data:
        # ローカルデータの鮮度をチェック（30分以内なら使う）
        fetched_at = local_data.get('fetched_at', '')
        if fetched_at:
            try:
                fetch_time = datetime.fromisoformat(fetched_at)
                now = datetime.now(JST)
                age_minutes = (now - fetch_time).total_seconds() / 60

                if age_minutes <= 30:
                    print(f"Using local data (fetched {int(age_minutes)} minutes ago)")
                    return local_data
                else:
                    print(f"Local data is stale ({int(age_minutes)} minutes old)")
            except Exception as e:
                print(f"Error parsing local data timestamp: {e}")

    # ローカルがない/古い場合はGitHubから
    github_data = get_availability_from_github()

    if github_data:
        return github_data

    # GitHubも失敗したらローカルを返す（古くても）
    if local_data:
        print("Using stale local data as fallback")
        return local_data

    return {}


def get_availability(store_key: str) -> Dict[str, str]:
    """
    店舗の空き状況を取得

    Returns:
        {台番号: '空き' or '遊技中'}
    """
    store_data = {}

    # availability.json（ローカル優先、GitHub fallback）から取得
    # store_keyのエイリアス対応（_tensei2サフィックスを除去）
    data_store_key = store_key.replace('_tensei2', '') if '_tensei2' in store_key else store_key
    if store_key in GITHUB_STORES:
        try:
            data = get_daidata_availability()
            store_data = data.get('stores', {}).get(data_store_key, {}) or data.get('stores', {}).get(store_key, {})
        except Exception as e:
            print(f"Error getting availability from JSON: {e}")

    # GAS fallback（JSONにデータがない場合）
    if not store_data and store_key in GAS_STORES:
        try:
            data = get_availability_from_gas()
            store_data = data.get(store_key, {})
        except Exception as e:
            print(f"Error getting from GAS: {e}")

    if not store_data:
        return {}

    if 'error' in store_data:
        print(f"Availability error: {store_data['error']}")
        return {}

    result = {}
    for u in store_data.get('empty', []):
        result[u] = '空き'
    for u in store_data.get('playing', []):
        result[u] = '遊技中'
    return result


def get_all_availability() -> Dict[str, Dict[str, str]]:
    """
    全店舗の空き状況を取得
    """
    result = {}

    # GAS店舗
    gas_data = get_availability_from_gas()
    for store_key in GAS_STORES:
        if store_key in gas_data:
            store_data = gas_data[store_key]
            result[store_key] = {}
            for u in store_data.get('empty', []):
                result[store_key][u] = '空き'
            for u in store_data.get('playing', []):
                result[store_key][u] = '遊技中'

    # daidata店舗
    daidata = get_daidata_availability()
    stores_data = daidata.get('stores', {})
    for store_key in GITHUB_STORES:
        if store_key in stores_data:
            store_data = stores_data[store_key]
            result[store_key] = {}
            for u in store_data.get('empty', []):
                result[store_key][u] = '空き'
            for u in store_data.get('playing', []):
                result[store_key][u] = '遊技中'

    return result


def get_realtime_data(store_key: str) -> Dict:
    """
    リアルタイムデータ(ART, スタート数等)を取得

    Returns:
        {
            'store_name': str,
            'fetched_at': str,  # ISO形式のJST時刻
            'units': [
                {'unit_id': str, 'art': int, 'bb': int, 'rb': int, 'total_start': int, ...},
                ...
            ],
            'source': str,  # 'github' or 'gas'
        }
    """
    # availability.json対応店舗（daidata + papimo）
    # store_keyのエイリアス対応（_tensei2サフィックスを除去）
    data_store_key = store_key.replace('_tensei2', '') if '_tensei2' in store_key else store_key
    if store_key in GITHUB_STORES:
        try:
            data = get_daidata_availability()
            store_data = data.get('stores', {}).get(data_store_key, {}) or data.get('stores', {}).get(store_key, {})

            if store_data and store_data.get('units'):
                return {
                    'store_name': store_data.get('name', ''),
                    'fetched_at': data.get('fetched_at', ''),
                    'units': store_data.get('units', []),
                    'source': 'github',
                }
        except Exception as e:
            print(f"Error getting realtime data from availability.json: {e}")

    # GAS fallback（availability.jsonにデータがない場合）
    if store_key in GAS_STORES:
        try:
            data = get_availability_from_gas()
            store_data = data.get(store_key, {})

            if not store_data:
                return {}

            # GASからのデータを整形
            units = []
            for u in store_data.get('empty', []):
                units.append({'unit_id': u, 'availability': '空き'})
            for u in store_data.get('playing', []):
                units.append({'unit_id': u, 'availability': '遊技中'})

            # GASからの詳細データがあれば使用
            if 'units' in store_data:
                units = store_data['units']

            return {
                'store_name': store_data.get('name', ''),
                'fetched_at': data.get('fetched_at', datetime.now(JST).isoformat()),
                'units': units,
                'source': 'gas',
            }
        except Exception as e:
            print(f"Error getting realtime data from GAS: {e}")

    return {}


def get_all_realtime_data() -> Dict[str, Dict]:
    """
    全daidata店舗のリアルタイムデータを取得
    """
    result = {}

    try:
        data = get_daidata_availability()
        stores_data = data.get('stores', {})
        fetched_at = data.get('fetched_at', '')

        for store_key in GITHUB_STORES:
            if store_key in stores_data:
                store_data = stores_data[store_key]
                result[store_key] = {
                    'store_name': store_data.get('name', ''),
                    'fetched_at': fetched_at,
                    'units': store_data.get('units', []),
                }
    except Exception as e:
        print(f"Error getting all realtime data: {e}")

    return result


if __name__ == "__main__":
    print("=== 空き状況チェック ===\n")

    all_avail = get_all_availability()

    for store_key, units in all_avail.items():
        empty = [u for u, status in units.items() if status == '空き']
        playing = [u for u, status in units.items() if status == '遊技中']
        print(f"【{store_key}】")
        print(f"  空き ({len(empty)}台): {', '.join(empty) if empty else 'なし'}")
        print(f"  遊技中 ({len(playing)}台): {', '.join(playing) if playing else 'なし'}")
        print()
