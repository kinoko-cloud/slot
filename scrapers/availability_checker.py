#!/usr/bin/env python3
"""
空き状況チェッカー
GAS経由でpapimo.jpから空き/遊技中を取得
"""

import requests
from typing import Dict

# GAS WebアプリURL
GAS_URL = "https://script.google.com/macros/s/AKfycbxPxFOrfhytabAS9R8xg_PJbFFXAWsTuBIciJMaYdil3BxlV0-XL3yPYSvQHxyuRO_7/exec"

# GASでサポートしている店舗
GAS_STORES = ['island_akihabara_sbj']


def get_availability_from_gas() -> Dict[str, Dict]:
    """
    GASから全店舗の空き状況を取得
    """
    try:
        response = requests.get(GAS_URL, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching from GAS: {e}")
        return {}


def get_availability(store_key: str) -> Dict[str, str]:
    """
    店舗の空き状況を取得

    Returns:
        {台番号: '空き' or '遊技中'}
    """
    if store_key not in GAS_STORES:
        return {}

    try:
        data = get_availability_from_gas()
        store_data = data.get(store_key, {})

        if 'error' in store_data:
            print(f"GAS error: {store_data['error']}")
            return {}

        result = {}
        for u in store_data.get('empty', []):
            result[u] = '空き'
        for u in store_data.get('playing', []):
            result[u] = '遊技中'
        return result

    except Exception as e:
        print(f"Error getting availability: {e}")
        return {}


if __name__ == "__main__":
    print("=== 空き状況チェック (GAS経由) ===\n")

    data = get_availability_from_gas()
    for store_key, store_data in data.items():
        print(f"【{store_key}】")
        empty = store_data.get('empty', [])
        playing = store_data.get('playing', [])
        print(f"  空き ({len(empty)}台): {', '.join(empty) if empty else 'なし'}")
        print(f"  遊技中 ({len(playing)}台): {', '.join(playing) if playing else 'なし'}")
