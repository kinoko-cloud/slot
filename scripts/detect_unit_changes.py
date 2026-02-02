#!/usr/bin/env python3
"""
台番号変更検出スクリプト

機能:
- config定義の台番号 vs 実際のデータを比較
- 取得できない台（減台/移動）を検出
- 新しい台番号（増台）を検出
- 変更があればWhatsApp通知 + config更新提案

実行: python scripts/detect_unit_changes.py [--fix]
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.rankings import STORES


def detect_changes(store_key: str, actual_units: list) -> dict:
    """config定義と実際の台番号を比較して変更を検出"""
    cfg = STORES.get(store_key, {})
    config_units = set(str(u) for u in cfg.get('units', []))
    actual_units = set(str(u) for u in actual_units)
    
    missing = config_units - actual_units  # configにあるが実際にはない
    new = actual_units - config_units      # configにないが実際にはある
    
    return {
        'store_key': store_key,
        'config_count': len(config_units),
        'actual_count': len(actual_units),
        'missing': sorted(missing),
        'new': sorted(new),
        'has_changes': bool(missing or new)
    }


def scan_daidata_units(hall_id: str) -> list:
    """DAIDATAから現在の台番号一覧を取得"""
    try:
        import requests
        from bs4 import BeautifulSoup
        
        url = f"https://daidata.goraggio.com/{hall_id}"
        resp = requests.get(url, timeout=30)
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        units = []
        for link in soup.select('a[href*="/detail?unit="]'):
            href = link.get('href', '')
            if 'unit=' in href:
                unit_id = href.split('unit=')[-1].split('&')[0]
                units.append(unit_id)
        
        return list(set(units))
    except Exception as e:
        print(f"  Error scanning DAIDATA: {e}")
        return []


def scan_papimo_units(hall_id: str) -> list:
    """PAPILOから現在の台番号一覧を取得"""
    try:
        from playwright.sync_api import sync_playwright
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"https://papimo.jp/hall/{hall_id}", timeout=30000)
            page.wait_for_timeout(2000)
            
            units = []
            for el in page.query_selector_all('[data-unit-id]'):
                unit_id = el.get_attribute('data-unit-id')
                if unit_id:
                    units.append(unit_id)
            
            browser.close()
            return list(set(units))
    except Exception as e:
        print(f"  Error scanning PAPILO: {e}")
        return []


def check_all_stores(fix: bool = False):
    """全店舗の台番号変更をチェック"""
    print(f"=== 台番号変更検出 ===")
    print(f"実行時刻: {datetime.now()}")
    print()
    
    changes = []
    
    for store_key, cfg in STORES.items():
        if not cfg.get('units'):
            continue
        
        hall_id = cfg.get('hall_id')
        data_source = cfg.get('data_source', 'daidata')
        
        if not hall_id:
            continue
        
        print(f"{store_key}...")
        
        # 実際の台番号を取得
        if data_source == 'papimo':
            actual_units = scan_papimo_units(hall_id)
        else:
            actual_units = scan_daidata_units(hall_id)
        
        if not actual_units:
            print(f"  スキップ（データ取得失敗）")
            continue
        
        result = detect_changes(store_key, actual_units)
        
        if result['has_changes']:
            changes.append(result)
            print(f"  ⚠️ 変更検出!")
            print(f"    config: {result['config_count']}台")
            print(f"    実際: {result['actual_count']}台")
            if result['missing']:
                print(f"    なくなった台: {result['missing']}")
            if result['new']:
                print(f"    新しい台: {result['new']}")
        else:
            print(f"  ✅ 変更なし ({result['config_count']}台)")
    
    print()
    
    if changes:
        print("=== 変更サマリー ===")
        for c in changes:
            print(f"{c['store_key']}:")
            if c['missing']:
                print(f"  減台/移動: {c['missing']}")
            if c['new']:
                print(f"  増台: {c['new']}")
        
        if fix:
            print()
            print("=== config更新 ===")
            # TODO: 自動更新実装
            print("自動更新は未実装。手動でconfig/rankings.pyを更新してください。")
    else:
        print("✅ 全店舗で変更なし")
    
    return changes


if __name__ == '__main__':
    fix = '--fix' in sys.argv
    check_all_stores(fix=fix)
