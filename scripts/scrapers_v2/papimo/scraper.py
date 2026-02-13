"""
scrapers_v2/papimo/scraper.py - papimoスクレイパー

機能:
- papimo.jpからのデータ取得（アイランド秋葉原用）
- 機種別ランキング/詳細データ取得
"""
import re
from typing import Dict, Any, List, Optional
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from common.base import BaseScraper, setup_logger, now_jst


class PapimoScraper(BaseScraper):
    """papimo.jp スクレイパー"""
    
    BASE_URL = "https://papimo.jp"
    
    # 店舗設定
    STORES = {
        'island_akihabara': {
            'url_path': '/akihabara',
            'name': 'アイランド秋葉原',
        }
    }
    
    # 機種設定
    MACHINES = {
        'sbj': {
            'search_name': 'Lスーパーブラックジャック',
            'aliases': ['ブラックジャック', 'SBJ'],
        },
        'hokuto': {
            'search_name': '北斗の拳',
            'aliases': ['北斗', 'ホクト'],
        },
        'hokuto2': {
            'search_name': '北斗の拳 転生の章',
            'aliases': ['転生', '北斗2'],
        },
    }
    
    def __init__(self, headless: bool = True):
        super().__init__(headless=headless, timeout=30000)
    
    def fetch_machine_data(self, store_key: str, machine_key: str) -> Dict[str, Any]:
        """機種別データ取得"""
        store = self.STORES.get(store_key)
        machine = self.MACHINES.get(machine_key)
        
        if not store or not machine:
            return {'error': 'invalid_store_or_machine'}
        
        url = f"{self.BASE_URL}{store['url_path']}"
        
        if not self.navigate(url):
            return {'error': 'navigation_failed'}
        
        self.wait(2000)
        
        # 機種検索
        search_name = machine['search_name']
        data = {
            'store_key': store_key,
            'machine_key': machine_key,
            'store_name': store['name'],
            'units': [],
            'fetched_at': now_jst().isoformat()
        }
        
        # 機種リンクを探す
        try:
            links = self.page.locator(f'a:has-text("{search_name}")').all()
            if links:
                links[0].click()
                self.wait(2000)
                data['units'] = self._parse_machine_page()
        except Exception as e:
            self.logger.error(f"Machine search failed: {e}")
            data['error'] = str(e)
        
        return data
    
    def _parse_machine_page(self) -> List[Dict]:
        """機種ページのパース"""
        units = []
        
        try:
            # テーブル行を取得
            rows = self.page.locator('table tr, .unit-row').all()
            
            for row in rows:
                try:
                    text = row.inner_text()
                    
                    # 台番号
                    unit_match = re.search(r'(\d{3,4})', text)
                    if not unit_match:
                        continue
                    
                    unit = {'unit_id': unit_match.group(1)}
                    
                    # G数/BB/RB/ART等
                    numbers = re.findall(r'\d+', text)
                    if len(numbers) >= 4:
                        unit['games'] = int(numbers[1]) if len(numbers) > 1 else 0
                        unit['bb'] = int(numbers[2]) if len(numbers) > 2 else 0
                        unit['rb'] = int(numbers[3]) if len(numbers) > 3 else 0
                    
                    # 差枚
                    diff_match = re.search(r'([+-]?\d{1,5})枚', text)
                    if diff_match:
                        unit['diff_medals'] = int(diff_match.group(1))
                    
                    units.append(unit)
                    
                except Exception as e:
                    self.logger.debug(f"Row parse error: {e}")
                    continue
                    
        except Exception as e:
            self.logger.error(f"Page parse failed: {e}")
        
        return units
    
    def fetch_unit_history(self, store_key: str, unit_id: str) -> List[Dict]:
        """台別履歴取得"""
        # TODO: papimoの台別履歴ページの構造に合わせて実装
        return []
    
    def fetch(self, store_key: str = 'island_akihabara', 
              machine_keys: List[str] = None) -> Dict[str, Any]:
        """
        一括取得
        
        Args:
            store_key: 店舗キー
            machine_keys: 機種キーリスト（Noneで全機種）
        """
        if machine_keys is None:
            machine_keys = list(self.MACHINES.keys())
        
        results = {
            'store_key': store_key,
            'machines': {},
            'fetched_at': now_jst().isoformat()
        }
        
        with self.browser_session():
            for machine_key in machine_keys:
                self.logger.info(f"Fetching {store_key}/{machine_key}")
                data = self.fetch_machine_data(store_key, machine_key)
                results['machines'][machine_key] = data
        
        return results


# 使用例
if __name__ == '__main__':
    scraper = PapimoScraper(headless=True)
    
    # アイランド秋葉原のSBJデータ
    result = scraper.fetch(
        store_key='island_akihabara',
        machine_keys=['sbj']
    )
    print(result)
