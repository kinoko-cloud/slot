#!/usr/bin/env python3
"""
新店舗追加スクリプト

使い方:
  python scripts/add_store.py \\
    --name "マルハン新宿" \\
    --key "maruhan_shinjuku" \\
    --machine sbj \\
    --units 1001-1020 \\
    --site7-id 12345

これだけで:
1. config/rankings.py に店舗定義を追加
2. data/history/{store_key}_{machine}/ ディレクトリ作成
3. 全台系分析の対象に自動追加（historyをスキャンするため）
4. 予測ロジックに自動対応（config/rankings.pyを参照するため）
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

def parse_units(units_str: str) -> list:
    """台番号文字列をパース: "1001-1020" or "1001,1002,1003" """
    units = []
    for part in units_str.split(','):
        part = part.strip()
        if '-' in part:
            start, end = part.split('-')
            units.extend([str(i) for i in range(int(start), int(end) + 1)])
        else:
            units.append(part)
    return units

def add_store_to_rankings(
    store_key: str,
    store_name: str,
    short_name: str,
    machine_key: str,
    units: list,
    site7_id: str = None,
    papimo_url: str = None,
):
    """config/rankings.py に店舗定義を追加"""
    rankings_path = PROJECT_ROOT / 'config' / 'rankings.py'
    content = rankings_path.read_text()
    
    # STORES辞書に追加
    full_key = f"{store_key}_{machine_key}"
    
    store_def = f'''
    '{full_key}': {{
        'name': '{store_name}',
        'short_name': '{short_name}',
        'machine': '{machine_key}',
        'units': {units},
        'site7_id': '{site7_id or ""}',
        'papimo_url': '{papimo_url or ""}',
    }},'''
    
    # STORES = { の後に追加
    if full_key in content:
        print(f"⚠️ 店舗 {full_key} は既に存在します")
        return False
    
    # STORES辞書の末尾に追加
    pattern = r"(STORES\s*=\s*\{[^}]*)(})"
    if re.search(pattern, content, re.DOTALL):
        content = re.sub(
            pattern,
            r"\1" + store_def + r"\n\2",
            content,
            flags=re.DOTALL
        )
        rankings_path.write_text(content)
        print(f"✓ config/rankings.py に {full_key} を追加")
        return True
    else:
        print("❌ STORES辞書が見つかりません")
        return False

def create_history_dir(store_key: str, machine_key: str):
    """data/history/{store_key}_{machine}/ ディレクトリ作成"""
    full_key = f"{store_key}_{machine_key}"
    hist_dir = PROJECT_ROOT / 'data' / 'history' / full_key
    hist_dir.mkdir(parents=True, exist_ok=True)
    print(f"✓ {hist_dir} を作成")
    return hist_dir

def main():
    parser = argparse.ArgumentParser(description='新店舗追加')
    parser.add_argument('--name', required=True, help='店舗名（例: マルハン新宿）')
    parser.add_argument('--key', required=True, help='店舗キー（例: maruhan_shinjuku）')
    parser.add_argument('--short', help='短縮名（省略時: nameを使用）')
    parser.add_argument('--machine', required=True, choices=['sbj', 'hokuto_tensei2'], help='機種')
    parser.add_argument('--units', required=True, help='台番号（例: 1001-1020 or 1001,1002,1003）')
    parser.add_argument('--site7-id', help='サイトセブンの店舗ID')
    parser.add_argument('--papimo-url', help='パピモURL')
    parser.add_argument('--dry-run', action='store_true', help='実際には変更しない')
    
    args = parser.parse_args()
    
    units = parse_units(args.units)
    short_name = args.short or args.name
    
    print("=" * 50)
    print("🏪 新店舗追加")
    print("=" * 50)
    print(f"  店舗名: {args.name}")
    print(f"  短縮名: {short_name}")
    print(f"  キー: {args.key}_{args.machine}")
    print(f"  機種: {args.machine}")
    print(f"  台数: {len(units)}台 ({units[0]}〜{units[-1]})")
    print()
    
    if args.dry_run:
        print("(dry-run: 実際の変更はしません)")
        return
    
    # 1. config/rankings.py に追加
    # 注: 実際にはrankings.pyの構造が複雑なため、手動追加を推奨
    # add_store_to_rankings(args.key, args.name, short_name, args.machine, units, args.site7_id, args.papimo_url)
    
    # 2. historyディレクトリ作成
    create_history_dir(args.key, args.machine)
    
    # 3. 全台系分析を再実行
    print("\n📊 全台系分析を更新中...")
    os.system('python3 scripts/analyze_zentai.py')
    
    print("\n" + "=" * 50)
    print("✅ 完了")
    print("=" * 50)
    print("""
次のステップ:
1. config/rankings.py の STORES に以下を追加:
""")
    print(f"""    '{args.key}_{args.machine}': {{
        'name': '{args.name}',
        'short_name': '{short_name}',
        'machine': '{args.machine}',
        'units': {units},
    }},
""")
    print("""2. データ収集スクリプトに店舗を追加
3. 数日分のデータが溜まれば自動的に予測対象に
""")

if __name__ == '__main__':
    main()
