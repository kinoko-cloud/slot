#!/usr/bin/env python3
"""
台番号自動更新スクリプト

1. 全店舗の台番号を自動検出（discovery）
2. 変更があった店舗の設定ファイルを自動更新
3. 変更内容をログ出力

使い方:
  python scripts/scrapers_v2/auto_update_units.py          # 全店舗チェック
  python scripts/scrapers_v2/auto_update_units.py --apply  # 変更を適用
"""
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from daidata.discovery import DaidataDiscovery
from common.base import setup_logger, now_jst

# 設定ファイルのパス
CONFIG_FILES = {
    'fetch_daidata_availability': Path(__file__).parent.parent / 'fetch_daidata_availability.py',
    'rankings': Path(__file__).parent.parent.parent / 'config' / 'rankings.py',
}

logger = setup_logger('auto_update_units')


def load_current_config() -> Dict[str, Dict]:
    """現在の設定を読み込む"""
    # fetch_daidata_availability.pyからDAIDATA_STORESを取得
    config_path = CONFIG_FILES['fetch_daidata_availability']
    
    # 動的インポート
    import importlib.util
    spec = importlib.util.spec_from_file_location("fetch_config", config_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    
    return getattr(module, 'DAIDATA_STORES', {})


def discover_all_units() -> Dict[str, Dict]:
    """全店舗の台番号を検出"""
    from fetch_daidata_availability import DAIDATA_STORES
    
    discovery = DaidataDiscovery(headless=True)
    results = {}
    
    logger.info("=== 台番号自動検出 ===")
    
    with discovery.browser_session():
        for store_key, config in DAIDATA_STORES.items():
            hall_id = config['hall_id']
            machine_key = 'sbj' if 'sbj' in store_key else 'hokuto2'
            expected = config.get('units', [])
            
            result = discovery.discover_units(hall_id, machine_key)
            detected = [u['unit_id'] for u in result.get('units', [])]
            machine_name = result.get('machine_name', '')
            
            # 変更検出
            has_change = set(expected) != set(detected)
            
            results[store_key] = {
                'hall_id': hall_id,
                'machine_key': machine_key,
                'machine_name': machine_name,
                'expected': expected,
                'detected': detected,
                'has_change': has_change,
            }
            
            if has_change:
                logger.warning(f"⚠️ {store_key}: 台番号変更検出")
                logger.warning(f"   機種名: {machine_name}")
                logger.warning(f"   設定: {len(expected)}台 → 検出: {len(detected)}台")
            else:
                logger.info(f"✓ {store_key}: OK ({len(detected)}台) - {machine_name}")
    
    return results


def update_config_file(path: Path, store_key: str, new_units: List[str]) -> bool:
    """設定ファイルの台番号を更新"""
    content = path.read_text()
    
    # 'store_key': { ... 'units': [...], ... } のパターンを探す
    # 複数行にまたがる可能性があるので、正規表現で対応
    
    # パターン1: 'units': ['xxx', 'yyy', ...]
    pattern = rf"('{store_key}':\s*\{{[^}}]*'units':\s*)\[[^\]]*\]"
    
    # 新しいunitsリスト
    new_units_str = str(new_units)
    
    # 置換
    new_content, count = re.subn(pattern, rf"\1{new_units_str}", content, flags=re.DOTALL)
    
    if count > 0:
        path.write_text(new_content)
        return True
    
    return False


def apply_changes(results: Dict[str, Dict]) -> int:
    """変更を設定ファイルに適用"""
    updated = 0
    
    for store_key, data in results.items():
        if not data['has_change']:
            continue
        
        detected = data['detected']
        
        # 両方の設定ファイルを更新
        for name, path in CONFIG_FILES.items():
            if path.exists():
                if update_config_file(path, store_key, detected):
                    logger.info(f"  ✓ {name}: {store_key} 更新完了")
                    updated += 1
                else:
                    logger.warning(f"  ⚠️ {name}: {store_key} 更新失敗（パターン不一致）")
    
    return updated


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='台番号自動更新')
    parser.add_argument('--apply', action='store_true', help='変更を設定ファイルに適用')
    parser.add_argument('--store', type=str, help='特定店舗のみチェック')
    args = parser.parse_args()
    
    # 全店舗検出
    results = discover_all_units()
    
    # 変更があった店舗をまとめ
    changes = {k: v for k, v in results.items() if v['has_change']}
    
    if not changes:
        logger.info("\n✅ 全店舗の台番号OK")
        return 0
    
    logger.info(f"\n⚠️ {len(changes)}店舗で台番号変更を検出:")
    for store_key, data in changes.items():
        logger.info(f"  {store_key}: {data['machine_name']}")
        logger.info(f"    設定: {data['expected']}")
        logger.info(f"    検出: {data['detected']}")
    
    if args.apply:
        logger.info("\n設定ファイルを更新中...")
        updated = apply_changes(results)
        logger.info(f"✓ {updated}件更新完了")
    else:
        logger.info("\n--apply オプションで設定ファイルを更新できます")
    
    return len(changes)


if __name__ == '__main__':
    sys.exit(main())
