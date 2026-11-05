#!/usr/bin/env python3
"""
台データオンライン - ランキングページからデータ取得
差玉・累計スタート等をランキングTOP10から取得
"""

from playwright.sync_api import sync_playwright
import re
import json
from datetime import datetime, timedelta
from pathlib import Path


REMOVE_ADS_SCRIPT = """
() => {
    document.querySelectorAll('#gn_interstitial_outer_area, .yads_ad_item, [id*="google_ads"]').forEach(el => el.remove());
}
"""


def get_ranking_data(hall_id: str, date_str: str = None, hall_name: str = "") -> list:
    """ランキングページからTOP10のデータを取得
    
    Args:
        hall_id: ホールID
        date_str: 日付（YYYY-MM-DD形式）。Noneなら前日
        hall_name: ホール名（ログ用）
    
    Returns:
        [{rank, unit_id, machine_name, games, bb, rb, art, diff_medals, date, hall_id}]
    """
    if date_str is None:
        yesterday = datetime.now() - timedelta(days=1)
        date_str = yesterday.strftime('%Y-%m-%d')
    
    print(f"ランキング取得: {hall_name or hall_id} ({date_str})")
    
    results = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        try:
            # 規約同意
            page.goto(f"https://daidata.goraggio.com/{hall_id}/accept", timeout=30000)
            page.wait_for_timeout(1000)
            try:
                page.click('button:has-text("利用規約に同意する")', timeout=5000)
                page.wait_for_timeout(2000)
            except:
                pass
            
            # ランキングページ（スロット、差玉順）
            page.goto(f"https://daidata.goraggio.com/{hall_id}/ranking?ps=S&gb=good", timeout=30000)
            page.wait_for_timeout(3000)
            page.evaluate(REMOVE_ADS_SCRIPT)
            
            # selectで日付選択
            page.select_option('select[name="day"]', date_str)
            page.wait_for_timeout(5000)
            
            # テーブル行を取得
            # ヘッダー: 順位, 台番号, 機種名, 累計スタート, BB, RB, ART, 差玉, BB確率, RB確率, ART確率, 合成確率, スタート回数
            rows = page.evaluate("""() => {
                const trs = document.querySelectorAll('table tbody tr');
                return Array.from(trs).map(tr => tr.innerText);
            }""")
            
            for row in rows:
                parts = [p.strip() for p in row.split('\t') if p.strip()]
                if len(parts) >= 8:
                    try:
                        entry = {
                            'rank': int(parts[0]),
                            'unit_id': parts[1],
                            'machine_name': parts[2],
                            'games': int(parts[3]),
                            'bb': int(parts[4]),
                            'rb': int(parts[5]),
                            'art': int(parts[6]),
                            'diff_medals': int(parts[7]),
                            'date': date_str,
                            'hall_id': hall_id,
                        }
                        # 確率データもあれば取得
                        if len(parts) >= 13:
                            entry['last_start'] = int(parts[12]) if parts[12].isdigit() else 0
                        
                        results.append(entry)
                        print(f"  #{entry['rank']:>2} {entry['unit_id']:>5} {entry['machine_name'][:15]:15s} 差玉={entry['diff_medals']:>+7,d}")
                    except (ValueError, IndexError):
                        continue
        
        finally:
            browser.close()
    
    print(f"  取得: {len(results)}台")
    return results


def save_ranking_data(hall_id: str, date_str: str = None, hall_name: str = "") -> Path:
    """ランキングデータを取得して保存"""
    data = get_ranking_data(hall_id, date_str, hall_name)
    if not data:
        return None
    
    if date_str is None:
        date_str = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    date_compact = date_str.replace('-', '')
    save_dir = Path('data/ranking')
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / f'ranking_{hall_id}_{date_compact}.json'
    
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"  保存: {save_path}")
    return save_path


# 対象店舗
RANKING_HALLS = {
    '100860': 'エスパス渋谷新館',
    '100949': 'エスパス歌舞伎町',
    '100928': 'エスパス秋葉原',
    '100950': 'エスパス西武新宿',
}


def collect_all_rankings(date_str: str = None):
    """全店舗のランキングを取得"""
    results = {}
    for hall_id, name in RANKING_HALLS.items():
        try:
            path = save_ranking_data(hall_id, date_str, name)
            if path:
                results[hall_id] = str(path)
        except Exception as e:
            print(f"  ⚠ {name}: {e}")
    return results


if __name__ == "__main__":
    collect_all_rankings()
