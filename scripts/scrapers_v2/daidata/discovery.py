"""
scrapers_v2/daidata/discovery.py - 台番号自動発見

店舗 → 機種一覧 → 台番号リスト の流れで動的に取得
ハードコードせずに台変動に自動対応

【取得フロー】
1. 店舗トップ: https://daidata.goraggio.com/{hall_id}
2. 機種検索: モデル名でフィルター
3. 台一覧取得: 台番号 + サマリー（BB/RB/ART/G数）
"""
import re
from typing import Dict, List, Any, Optional
from pathlib import Path
from urllib.parse import quote

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from common.base import BaseScraper, setup_logger, now_jst

# 広告削除スクリプト
REMOVE_ADS_SCRIPT = """
() => {
    document.querySelectorAll('#gn_interstitial_outer_area, .gn_interstitial_outer_area').forEach(el => el.remove());
    document.querySelectorAll('.yads_ad_item, [id*="google_ads"], [class*="ad-"]').forEach(el => el.remove());
}
"""

# 機種名（検索用）
MACHINE_NAMES = {
    'sbj': ['スーパーブラックジャック', 'ブラックジャック', 'SBJ'],
    'hokuto': ['北斗の拳', '北斗'],
    'hokuto2': ['北斗の拳 転生', '転生の章', '北斗転生'],
}


class DaidataDiscovery(BaseScraper):
    """daidata 台番号自動発見"""
    
    BASE_URL = "https://daidata.goraggio.com"
    
    def __init__(self, headless: bool = True):
        super().__init__(headless=headless, timeout=20000)
        self._agreed_halls = set()
    
    def fetch(self, **kwargs):
        """BaseScraper抽象メソッド実装"""
        hall_id = kwargs.get('hall_id')
        machine_key = kwargs.get('machine_key')
        if machine_key:
            return self.discover_units(hall_id, machine_key)
        return self.discover_all(hall_id)
    
    def _remove_ads(self):
        try:
            self.page.evaluate(REMOVE_ADS_SCRIPT)
        except:
            pass
    
    def _accept_terms(self, hall_id: str) -> bool:
        """規約同意"""
        if hall_id in self._agreed_halls:
            return True
        
        try:
            btn = self.page.locator('button:has-text("利用規約に同意する")')
            if btn.count() > 0:
                btn.first.click()
                self.wait(3000)
                self._agreed_halls.add(hall_id)
                return True
        except:
            pass
        return False
    
    def discover_machines(self, hall_id: str) -> List[Dict[str, Any]]:
        """店舗内の全機種リストを取得"""
        url = f"{self.BASE_URL}/{hall_id}"
        
        if not self.navigate(url):
            return []
        
        self.wait(2000)
        self._remove_ads()
        self._accept_terms(hall_id)
        
        machines = []
        
        try:
            # 機種リンクを探す
            links = self.page.locator('a[href*="/model/"]').all()
            
            for link in links:
                try:
                    href = link.get_attribute('href')
                    text = link.inner_text().strip()
                    
                    if href and text:
                        machines.append({
                            'name': text,
                            'url': href if href.startswith('http') else f"{self.BASE_URL}{href}",
                        })
                except:
                    continue
                    
        except Exception as e:
            self.logger.error(f"Machine discovery failed: {e}")
        
        return machines
    
    def discover_units(self, hall_id: str, machine_key: str = None, 
                      machine_name: str = None) -> Dict[str, Any]:
        """
        機種の台番号一覧を取得
        
        フロー:
        1. 店舗トップ→機種別で探す→機種一覧
        2. 機種一覧URLを取得
        3. 台一覧ページで台番号を抽出
        
        Args:
            hall_id: ホールID
            machine_key: 機種キー (sbj/hokuto/hokuto2)
            machine_name: 機種名（直接指定）
        """
        result = {
            'hall_id': hall_id,
            'machine_key': machine_key,
            'units': [],
            'fetched_at': now_jst().isoformat()
        }
        
        # Step 1: 同意処理
        if not self.navigate(f"{self.BASE_URL}/{hall_id}/detail?unit=1"):
            result['error'] = 'navigation_failed'
            return result
        
        self.wait(2000)
        self._accept_terms(hall_id)
        
        # Step 2: 機種別一覧ページへ
        if not self.navigate(f"{self.BASE_URL}/{hall_id}"):
            return result
        
        self.wait(2000)
        
        # 「機種別で探す」（スロットセクション）をクリック
        try:
            model_links = self.page.locator('a:has-text("機種別で探す")').all()
            if len(model_links) >= 2:
                model_links[1].click()  # スロットは2番目
                self.wait(3000)
        except Exception as e:
            self.logger.error(f"Failed to navigate to model list: {e}")
            return result
        
        # Step 3: 機種リンクを探す
        search_names = MACHINE_NAMES.get(machine_key, [machine_name] if machine_name else [])
        unit_list_url = None
        
        for name in search_names:
            try:
                links = self.page.locator('a').all()
                for link in links:
                    txt = link.inner_text()
                    href = link.get_attribute('href') or ''
                    if name in txt and 'unit_list' in href:
                        unit_list_url = href if href.startswith('http') else f"{self.BASE_URL}{href}"
                        result['machine_name'] = name
                        break
                if unit_list_url:
                    break
            except:
                continue
        
        if not unit_list_url:
            result['error'] = 'machine_not_found'
            return result
        
        # Step 4: 台一覧ページへ
        if not self.navigate(unit_list_url):
            return result
        
        self.wait(2000)
        result['units'] = self._parse_unit_list()
        
        return result
    
    def _parse_unit_list(self) -> List[Dict]:
        """台一覧ページをパース"""
        units = []
        text = self.get_text()
        
        # パターン1: テーブル形式
        try:
            rows = self.page.locator('table tr, .unit-row, [class*="machine-item"]').all()
            
            for row in rows:
                try:
                    row_text = row.inner_text()
                    
                    # 台番号を探す（3-4桁の数字）
                    unit_match = re.search(r'(?:No\.?|台番号|#)?(\d{3,4})', row_text)
                    if not unit_match:
                        continue
                    
                    unit = {'unit_id': unit_match.group(1)}
                    
                    # BB/RB/ART/G数を探す
                    numbers = re.findall(r'\d+', row_text)
                    # 最初の数字は台番号なのでスキップして、BB/RB/ART/G数を取得
                    
                    # BB RB ART パターン
                    bb_match = re.search(r'BB[:\s]*(\d+)', row_text)
                    rb_match = re.search(r'RB[:\s]*(\d+)', row_text)
                    art_match = re.search(r'ART[:\s]*(\d+)', row_text)
                    games_match = re.search(r'(?:G数|ゲーム|スタート)[:\s]*([\d,]+)', row_text)
                    
                    if bb_match:
                        unit['bb'] = int(bb_match.group(1))
                    if rb_match:
                        unit['rb'] = int(rb_match.group(1))
                    if art_match:
                        unit['art'] = int(art_match.group(1))
                    if games_match:
                        unit['games'] = int(games_match.group(1).replace(',', ''))
                    
                    units.append(unit)
                    
                except Exception as e:
                    continue
                    
        except Exception as e:
            self.logger.debug(f"Table parse failed: {e}")
        
        # パターン2: リンク形式（詳細ページへのリンクから台番号を抽出）
        if not units:
            try:
                links = self.page.locator('a[href*="detail?unit="]').all()
                
                for link in links:
                    try:
                        href = link.get_attribute('href')
                        unit_match = re.search(r'unit=(\d+)', href)
                        if unit_match:
                            units.append({'unit_id': unit_match.group(1)})
                    except:
                        continue
                        
            except Exception as e:
                self.logger.debug(f"Link parse failed: {e}")
        
        # 重複除去
        seen = set()
        unique_units = []
        for unit in units:
            uid = unit['unit_id']
            if uid not in seen:
                seen.add(uid)
                unique_units.append(unit)
        
        return unique_units
    
    def discover_all(self, hall_id: str) -> Dict[str, Any]:
        """店舗内の全機種・全台番号を取得"""
        result = {
            'hall_id': hall_id,
            'machines': {},
            'fetched_at': now_jst().isoformat()
        }
        
        with self.browser_session():
            for machine_key in ['sbj', 'hokuto', 'hokuto2']:
                self.logger.info(f"Discovering {hall_id}/{machine_key}")
                data = self.discover_units(hall_id, machine_key)
                
                if data.get('units'):
                    result['machines'][machine_key] = data
                    self.logger.info(f"  Found {len(data['units'])} units")
        
        return result


# CLI
if __name__ == '__main__':
    import sys
    import json
    
    discovery = DaidataDiscovery(headless=True)
    
    # 引数: hall_id [machine_key]
    hall_id = sys.argv[1] if len(sys.argv) > 1 else '100949'  # デフォルト: 新宿エスパス
    machine_key = sys.argv[2] if len(sys.argv) > 2 else None
    
    if machine_key:
        # 特定機種
        with discovery.browser_session():
            result = discovery.discover_units(hall_id, machine_key)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        # 全機種
        result = discovery.discover_all(hall_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
