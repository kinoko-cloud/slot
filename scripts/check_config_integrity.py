#!/usr/bin/env python3
"""
設定ファイル整合性チェック

rankings.pyとfetch_daidata_availability.pyの台番号設定が一致しているか確認
ヘルスチェックや台番号更新前に実行して、不整合を事前に検出する

使い方:
  python scripts/check_config_integrity.py
"""
import json
import sys
from pathlib import Path

# パス設定
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'config'))

def check_integrity():
    """設定ファイルの整合性をチェック"""
    from rankings import STORES
    
    # fetch_daidata_availability.pyの設定を読み込み
    fetch_path = ROOT / 'scripts' / 'fetch_daidata_availability.py'
    with open(fetch_path) as f:
        content = f.read()
    
    # DAIDATA_STORES抽出
    daidata_start = content.find('DAIDATA_STORES = {')
    daidata_end = content.find('\n}', daidata_start) + 2
    local_vars = {}
    exec(content[daidata_start:daidata_end], {}, local_vars)
    DAIDATA_STORES = local_vars['DAIDATA_STORES']
    
    # PAPIMO_STORES抽出
    papimo_start = content.find('PAPIMO_STORES = {')
    papimo_end = content.find('\n}', papimo_start) + 2
    exec(content[papimo_start:papimo_end], {}, local_vars)
    PAPIMO_STORES = local_vars['PAPIMO_STORES']
    
    errors = []
    warnings = []
    
    # 1. rankings vs fetch 整合性
    for store_key, config in STORES.items():
        if store_key in ['island_akihabara', 'shibuya_espass']:
            continue
        
        r_units = set(config.get('units', []))
        
        if store_key in DAIDATA_STORES:
            f_units = set(DAIDATA_STORES[store_key].get('units', []))
        elif store_key in PAPIMO_STORES:
            f_units = set(PAPIMO_STORES[store_key].get('units', []))
        else:
            warnings.append(f"⚠️ {store_key}: rankings.pyにあるがfetch設定なし")
            continue
        
        if r_units != f_units:
            diff1 = r_units - f_units
            diff2 = f_units - r_units
            msg = f"❌ {store_key}: 台番号不一致"
            if diff1:
                msg += f"\n   rankingsのみ: {sorted(diff1)}"
            if diff2:
                msg += f"\n   fetchのみ: {sorted(diff2)}"
            errors.append(msg)
    
    # 2. fetch設定にあるがrankingsにない
    all_fetch = set(DAIDATA_STORES.keys()) | set(PAPIMO_STORES.keys())
    all_rankings = set(STORES.keys()) - {'island_akihabara', 'shibuya_espass'}
    
    missing_in_rankings = all_fetch - all_rankings
    if missing_in_rankings:
        errors.append(f"❌ fetch設定にあるがrankings.pyにない: {sorted(missing_in_rankings)}")
    
    # 3. 台番号の型チェック
    for store_key, config in STORES.items():
        units = config.get('units', [])
        for u in units:
            if not isinstance(u, str):
                errors.append(f"❌ {store_key}: 台番号が文字列でない ({u}: {type(u).__name__})")
                break
    
    # 4. 重複チェック
    for store_key, config in STORES.items():
        units = config.get('units', [])
        if len(units) != len(set(units)):
            errors.append(f"❌ {store_key}: 台番号に重複あり")
    
    return errors, warnings


def main():
    print("=== 設定ファイル整合性チェック ===\n")
    
    errors, warnings = check_integrity()
    
    if warnings:
        print("【警告】")
        for w in warnings:
            print(f"  {w}")
        print()
    
    if errors:
        print("【エラー】")
        for e in errors:
            print(f"  {e}")
        print(f"\n❌ {len(errors)}件のエラー検出")
        return 1
    
    print("✅ 整合性OK")
    return 0


if __name__ == '__main__':
    sys.exit(main())
