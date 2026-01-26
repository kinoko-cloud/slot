#!/usr/bin/env python3
"""
空き状況チェッカー
requestsで取得可能なサイトから空き/遊技中を取得
"""

import requests
import re
from typing import Dict, List, Tuple

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15'
}

# 店舗設定
PAPIMO_STORES = {
    'island_akihabara_sbj': {
        'hall_id': '00031715',
        'machine_id': '225010000',
    },
    # 北斗転生2は機種IDを調べて追加
}


def check_papimo_availability(hall_id: str, machine_id: str) -> Tuple[List[str], List[str]]:
    """
    papimo.jpから空き/遊技中を取得

    Returns:
        (空き台リスト, 遊技中台リスト)
    """
    url = f"https://papimo.jp/h/{hall_id}/hit/index_sort/{machine_id}/1-20-1274324"

    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        html = response.text

        # 遊技中の台を取得（badge-workクラス）
        playing = re.findall(r'<span class="badge-work">遊技中</span>(\d{4})', html)

        # 全台番号を取得
        all_units = list(set(re.findall(r'/hit/view/(\d{4})', html)))

        # 空き = 全台 - 遊技中
        empty = [u for u in all_units if u not in playing]

        return sorted(empty), sorted(playing)

    except Exception as e:
        print(f"Error fetching papimo: {e}")
        return [], []


def get_availability(store_key: str) -> Dict[str, str]:
    """
    店舗の空き状況を取得

    Returns:
        {台番号: '空き' or '遊技中'}
    """
    if store_key in PAPIMO_STORES:
        config = PAPIMO_STORES[store_key]
        empty, playing = check_papimo_availability(config['hall_id'], config['machine_id'])

        result = {}
        for u in empty:
            result[u] = '空き'
        for u in playing:
            result[u] = '遊技中'
        return result

    # daidata系はPlaywrightが必要なので空を返す
    return {}


def get_all_availability() -> Dict[str, Dict[str, str]]:
    """
    全店舗の空き状況を取得

    Returns:
        {店舗キー: {台番号: 状態}}
    """
    results = {}

    for store_key in PAPIMO_STORES:
        availability = get_availability(store_key)
        if availability:
            results[store_key] = availability

    return results


if __name__ == "__main__":
    print("=== 空き状況チェック ===\n")

    # アイランド秋葉原 SBJ
    print("【アイランド秋葉原 スーパーブラックジャック】")
    empty, playing = check_papimo_availability('00031715', '225010000')

    print(f"空き台 ({len(empty)}台): {', '.join(empty) if empty else 'なし'}")
    print(f"遊技中 ({len(playing)}台): {', '.join(playing) if playing else 'なし'}")
