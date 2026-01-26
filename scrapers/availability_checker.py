#!/usr/bin/env python3
"""
空き状況チェッカー
- GAS経由: papimo.jp (アイランド秋葉原)
- GitHub JSON: daidata (エスパス系)
"""

import requests
from typing import Dict

# GAS WebアプリURL (papimo.jp用)
GAS_URL = "https://script.google.com/macros/s/AKfycbxPxFOrfhytabAS9R8xg_PJbFFXAWsTuBIciJMaYdil3BxlV0-XL3yPYSvQHxyuRO_7/exec"

# GitHub raw JSON URL (daidata用)
GITHUB_JSON_URL = "https://raw.githubusercontent.com/kinoko-cloud/slot/main/data/availability.json"

# GASでサポートしている店舗 (papimo.jp)
GAS_STORES = ['island_akihabara_sbj']

# GitHubでサポートしている店舗 (daidata)
GITHUB_STORES = ['shibuya_espass_sbj', 'shinjuku_espass_sbj', 'akihabara_espass_sbj', 'seibu_shinjuku_espass_sbj']


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


def get_availability_from_github() -> Dict[str, Dict]:
    """
    GitHubから空き状況JSONを取得 (daidata)
    """
    try:
        response = requests.get(GITHUB_JSON_URL, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get('stores', {})
    except Exception as e:
        print(f"Error fetching from GitHub: {e}")
        return {}


def get_availability(store_key: str) -> Dict[str, str]:
    """
    店舗の空き状況を取得

    Returns:
        {台番号: '空き' or '遊技中'}
    """
    store_data = {}

    # GAS (papimo.jp) から取得
    if store_key in GAS_STORES:
        try:
            data = get_availability_from_gas()
            store_data = data.get(store_key, {})
        except Exception as e:
            print(f"Error getting from GAS: {e}")

    # GitHub (daidata) から取得
    elif store_key in GITHUB_STORES:
        try:
            data = get_availability_from_github()
            store_data = data.get(store_key, {})
        except Exception as e:
            print(f"Error getting from GitHub: {e}")

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


if __name__ == "__main__":
    print("=== 空き状況チェック (GAS経由) ===\n")

    data = get_availability_from_gas()
    for store_key, store_data in data.items():
        print(f"【{store_key}】")
        empty = store_data.get('empty', [])
        playing = store_data.get('playing', [])
        print(f"  空き ({len(empty)}台): {', '.join(empty) if empty else 'なし'}")
        print(f"  遊技中 ({len(playing)}台): {', '.join(playing) if playing else 'なし'}")
