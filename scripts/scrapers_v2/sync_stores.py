#!/usr/bin/env python3
"""
sync_stores.py - stores.pyの台番号を自動更新

daidataから実際の台番号を取得し、stores.pyとの差分を検出・更新する。

Usage:
    python scripts/scrapers_v2/sync_stores.py           # 差分チェックのみ
    python scripts/scrapers_v2/sync_stores.py --update  # stores.pyを更新
"""
import sys
import re
import json
from pathlib import Path
from typing import Dict, List, Any

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / 'scripts' / 'scrapers_v2'))

from daidata.scraper import DaidataScraper
from common.base import setup_logger

logger = setup_logger('sync_stores')

# daidata店舗設定（hall_idのマスター）
DAIDATA_HALL_IDS = {
    'shibuya_espass': '100860',
    'shinjuku_espass': '100949',
    'akiba_espass': '100928',
    'seibu_shinjuku_espass': '100950',
    'shibuya_honkan_espass': '100930',
    'akasaka_espass': '100952',
    'ueno_espass': '100196',
    'ueno_honkan_espass': '100947',
    'takadanobaba_espass': '100915',
    'shinokubo_espass': '100951',
    'shinkoiwa_espass': '100260',
}

# 機種検索用のURL
MACHINE_SEARCH = {
    'sbj': '%E3%82%B9%E3%83%BC%E3%83%91%E3%83%BC%E3%83%96%E3%83%A9%E3%83%83%E3%82%AF%E3%82%B8%E3%83%A3%E3%83%83%E3%82%AF',
    'hokuto2': '%E8%BB%A2%E7%94%9F%E3%81%AE%E7%AB%A0',
}


def accept_terms_for_hall(scraper: DaidataScraper, hall_id: str):
    """詳細ページで規約同意（一覧ページの前に必要）"""
    detail_url = f"https://daidata.goraggio.com/{hall_id}/detail?unit=1"
    scraper.navigate(detail_url)
    scraper.wait(3000)
    
    try:
        btn = scraper.page.locator('button:has-text("利用規約に同意する")')
        if btn.count() > 0:
            btn.first.click()
            scraper.wait(3000)
            logger.info(f"  規約同意完了: {hall_id}")
            return True
    except:
        pass
    return False


def fetch_units_from_daidata(scraper: DaidataScraper, hall_id: str, machine_key: str) -> List[str]:
    """daidataから台番号リストを取得"""
    machine_name = MACHINE_SEARCH.get(machine_key)
    if not machine_name:
        return []
    
    url = f"https://daidata.goraggio.com/{hall_id}/list?mode=modelNameSearch&machine_name={machine_name}"
    
    # 一覧ページへ
    scraper.navigate(url)
    scraper.wait(2000)
    
    # 機種リンクをクリック
    try:
        if machine_key == 'sbj':
            link = scraper.page.locator('a:has-text("ﾌﾞﾗｯｸ")').first
        else:
            link = scraper.page.locator('a:has-text("転生")').first
        if link.count() > 0:
            link.click()
            scraper.wait(2000)
    except Exception as e:
        logger.debug(f"Link click failed: {e}")
    
    text = scraper.get_text()
    
    # 台番号を抽出
    units = []
    for line in text.split('\n'):
        line = line.strip()
        # 3-4桁の数字のみの行
        if re.match(r'^\d{1,4}$', line):
            units.append(line)
        # または「台番号 BB RB ART」形式
        match = re.match(r'^(\d{1,4})\s+\d+\s+\d+\s+\d+', line)
        if match:
            units.append(match.group(1))
    
    return sorted(set(units), key=lambda x: int(x))


def load_stores_py() -> str:
    """stores.pyの内容を読み込む"""
    stores_path = ROOT / 'config' / 'stores.py'
    with open(stores_path, 'r') as f:
        return f.read()


def save_stores_py(content: str):
    """stores.pyを保存"""
    stores_path = ROOT / 'config' / 'stores.py'
    with open(stores_path, 'w') as f:
        f.write(content)


def parse_stores_py(content: str) -> Dict[str, Dict[str, List[str]]]:
    """stores.pyから現在の台番号設定をパース"""
    result = {}
    
    # DAIDATA_STORES内の各店舗を探す
    for store_key, hall_id in DAIDATA_HALL_IDS.items():
        result[store_key] = {'sbj': [], 'hokuto2': []}
        
        # 店舗ブロックを探す
        pattern = rf"'{store_key}':\s*\{{\s*[^}}]*?'machines':\s*\{{([^}}]+)\}}"
        match = re.search(pattern, content, re.DOTALL)
        if not match:
            continue
        
        machines_block = match.group(1)
        
        # sbj
        sbj_match = re.search(r"'sbj':\s*\[([^\]]*)\]", machines_block)
        if sbj_match:
            units = re.findall(r"'(\d+)'", sbj_match.group(1))
            result[store_key]['sbj'] = units
        
        # hokuto2
        hokuto_match = re.search(r"'hokuto2':\s*\[([^\]]*)\]", machines_block)
        if hokuto_match:
            units_str = hokuto_match.group(1)
            # str(i) for i in range(...) の場合もあるので、単純な文字列リストとして抽出
            units = re.findall(r"'(\d+)'", units_str)
            result[store_key]['hokuto2'] = units
    
    return result


def update_stores_py(content: str, store_key: str, machine_key: str, new_units: List[str]) -> str:
    """stores.pyの特定店舗・機種の台番号を更新"""
    
    # 台番号リストを文字列に変換
    units_str = ', '.join(f"'{u}'" for u in new_units)
    
    # パターン: 'sbj': [...] を探して置換
    pattern = rf"('{store_key}':\s*\{{\s*[^}}]*?'machines':\s*\{{[^}}]*?'{machine_key}':\s*)\[[^\]]*\]"
    
    def replacer(m):
        return f"{m.group(1)}[{units_str}]"
    
    new_content = re.sub(pattern, replacer, content, flags=re.DOTALL)
    
    return new_content


def main(do_update: bool = False):
    """メイン処理"""
    print("=" * 60)
    print("stores.py 台番号同期チェック")
    print("=" * 60)
    
    # 現在のstores.pyを読み込み
    content = load_stores_py()
    current_config = parse_stores_py(content)
    
    # daidataから最新の台番号を取得
    scraper = DaidataScraper(headless=True)
    diffs = []
    
    with scraper.browser_session('sync_stores'):
        for store_key, hall_id in DAIDATA_HALL_IDS.items():
            print(f"\n--- {store_key} ({hall_id}) ---")
            
            # 最初に規約同意
            accept_terms_for_hall(scraper, hall_id)
            
            for machine_key in ['sbj', 'hokuto2']:
                current_units = set(current_config.get(store_key, {}).get(machine_key, []))
                
                # daidataから取得
                daidata_units = fetch_units_from_daidata(scraper, hall_id, machine_key)
                daidata_set = set(daidata_units)
                
                if not daidata_units:
                    print(f"  {machine_key}: 取得失敗またはなし")
                    continue
                
                # 差分チェック
                added = daidata_set - current_units
                removed = current_units - daidata_set
                
                if added or removed:
                    print(f"  {machine_key}: ⚠️ 差分あり")
                    if added:
                        print(f"    追加: {sorted(added, key=int)}")
                    if removed:
                        print(f"    削除: {sorted(removed, key=int)}")
                    
                    diffs.append({
                        'store_key': store_key,
                        'machine_key': machine_key,
                        'new_units': daidata_units,
                        'added': list(added),
                        'removed': list(removed),
                    })
                else:
                    print(f"  {machine_key}: ✅ 一致 ({len(daidata_units)}台)")
    
    # 差分があれば更新
    if diffs:
        print(f"\n{'=' * 60}")
        print(f"差分: {len(diffs)}件")
        
        if do_update:
            print("stores.pyを更新します...")
            
            for diff in diffs:
                content = update_stores_py(
                    content,
                    diff['store_key'],
                    diff['machine_key'],
                    diff['new_units']
                )
            
            save_stores_py(content)
            print("✅ 更新完了!")
        else:
            print("更新するには --update オプションを指定してください")
    else:
        print(f"\n✅ 全て一致しています")


if __name__ == '__main__':
    do_update = '--update' in sys.argv
    main(do_update)
