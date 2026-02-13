"""
scrapers_v2/daidata/scraper.py - daidataスクレイパー

機能:
- リアルタイムデータ取得（BB/RB/ART/スタート）
- 詳細履歴取得（過去7日分の当たり履歴）
- 規約同意・広告削除の自動処理
"""
import re
from typing import Dict, Any, List, Optional
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from common.base import BaseScraper, DataStore, setup_logger, now_jst

# 広告削除スクリプト
REMOVE_ADS_SCRIPT = """
() => {
    document.querySelectorAll('#gn_interstitial_outer_area, .gn_interstitial_outer_area').forEach(el => el.remove());
    document.querySelectorAll('.yads_ad_item, [id*="google_ads"], [class*="ad-"]').forEach(el => el.remove());
    document.querySelectorAll('[class*="interstitial"], [id*="interstitial"]').forEach(el => el.remove());
}
"""


class DaidataScraper(BaseScraper):
    """daidata.goraggio.com スクレイパー"""
    
    BASE_URL = "https://daidata.goraggio.com"
    
    def __init__(self, headless: bool = True):
        super().__init__(headless=headless, timeout=20000)
        self._agreed_halls = set()  # 同意済みホール
    
    def _remove_ads(self):
        """広告削除"""
        try:
            self.page.evaluate(REMOVE_ADS_SCRIPT)
        except:
            pass
    
    def _accept_terms(self, hall_id: str) -> bool:
        """規約同意処理"""
        if hall_id in self._agreed_halls:
            return True
        
        try:
            # ボタン形式
            btn = self.page.locator('button:has-text("利用規約に同意する")')
            if btn.count() > 0:
                btn.first.click()
                self.wait(3000)
                self._agreed_halls.add(hall_id)
                return True
            
            # リンク形式
            link = self.page.locator('a:has-text("同意")')
            if link.count() > 0:
                link.first.click()
                self.wait(2000)
                self._agreed_halls.add(hall_id)
                return True
                
        except Exception as e:
            self.logger.debug(f"Terms accept: {e}")
        
        return False
    
    def _goto_with_terms(self, url: str, hall_id: str) -> bool:
        """ページ遷移＋規約同意"""
        if not self.navigate(url):
            return False
        
        self.wait(1500)
        self._remove_ads()
        self._accept_terms(hall_id)
        
        # 規約ページにリダイレクトされた場合、元のURLに戻る
        if 'terms' in self.page.url or '規約' in self.get_text()[:200]:
            self._accept_terms(hall_id)
            self.navigate(url)
            self.wait(1500)
            self._remove_ads()
        
        return True
    
    def fetch_realtime(self, hall_id: str, unit_id: str) -> Dict[str, Any]:
        """リアルタイムデータ取得（1台分）"""
        url = f"{self.BASE_URL}/{hall_id}/detail?unit={unit_id}"
        
        if not self._goto_with_terms(url, hall_id):
            return {'unit_id': unit_id, 'error': 'navigation_failed'}
        
        text = self.get_text()
        data = {
            'unit_id': unit_id,
            'bb': 0, 'rb': 0, 'art': 0,
            'total_start': 0, 'final_start': 0,
            'fetched_at': now_jst().isoformat()
        }
        
        # BB RB ART スタート回数
        match = re.search(r'BB\s+RB\s+ART\s+スタート回数\s*\n?\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)', text)
        if match:
            data['bb'] = int(match.group(1))
            data['rb'] = int(match.group(2))
            data['art'] = int(match.group(3))
            data['final_start'] = int(match.group(4))
        
        # 累計スタート
        total = re.search(r'累計スタート\s*\n?\s*(\d+)', text)
        if total:
            data['total_start'] = int(total.group(1))
        
        # 差枚
        diff = re.search(r'差枚\s*\n?\s*([+-]?\d+)', text)
        if diff:
            data['diff_medals'] = int(diff.group(1))
        
        return data
    
    def fetch_history(self, hall_id: str, unit_id: str, days: int = 7) -> List[Dict]:
        """詳細履歴取得（過去N日分）"""
        url = f"{self.BASE_URL}/{hall_id}/detail?unit={unit_id}"
        
        if not self._goto_with_terms(url, hall_id):
            return []
        
        # 日付リンク取得
        links = self.page.locator('a[href*="target_date"]').all()
        if not links:
            self.logger.debug(f"No history links for unit {unit_id}")
            return []
        
        results = []
        for link in links[:days]:
            try:
                href = link.get_attribute('href')
                date_match = re.search(r'target_date=(\d{4}-\d{2}-\d{2})', href)
                if not date_match:
                    continue
                
                date = date_match.group(1)
                detail_url = href if href.startswith('http') else f"{self.BASE_URL}{href}"
                
                if not self._goto_with_terms(detail_url, hall_id):
                    continue
                
                day_data = self._parse_day_detail(date)
                if day_data:
                    results.append(day_data)
                    
            except Exception as e:
                self.logger.debug(f"History fetch error: {e}")
        
        return results
    
    def _parse_day_detail(self, date: str) -> Optional[Dict]:
        """日別詳細ページのパース"""
        text = self.get_text()
        day = {'date': date, 'history': []}
        
        # BB/RB/ART
        for pattern, key in [
            (r'BB[：:]\s*(\d+)', 'bb'),
            (r'RB[：:]\s*(\d+)', 'rb'),
            (r'ART[：:]\s*(\d+)', 'art'),
        ]:
            match = re.search(pattern, text)
            if match:
                day[key] = int(match.group(1))
        
        # 履歴テーブル
        try:
            rows = self.page.locator('table tr').all()
            for row in rows[1:]:  # ヘッダースキップ
                cells = row.locator('td').all()
                if len(cells) >= 3:
                    item = {
                        'start': int(cells[0].inner_text().strip() or '0'),
                        'type': cells[1].inner_text().strip(),
                        'medals': int(cells[2].inner_text().strip() or '0'),
                    }
                    if len(cells) > 3:
                        item['time'] = cells[3].inner_text().strip()
                    day['history'].append(item)
        except:
            pass
        
        return day if day.get('history') or day.get('art', 0) > 0 else None
    
    def fetch(self, hall_id: str, unit_ids: List[str], mode: str = 'realtime') -> Dict[str, Any]:
        """
        一括取得
        
        Args:
            hall_id: ホールID
            unit_ids: 台番号リスト
            mode: 'realtime' or 'history'
        """
        results = {'hall_id': hall_id, 'units': [], 'fetched_at': now_jst().isoformat()}
        
        with self.browser_session():
            for unit_id in unit_ids:
                self.logger.info(f"Fetching {hall_id}/{unit_id} ({mode})")
                
                if mode == 'realtime':
                    data = self.fetch_realtime(hall_id, unit_id)
                else:
                    data = {
                        'unit_id': unit_id,
                        'days': self.fetch_history(hall_id, unit_id)
                    }
                
                results['units'].append(data)
        
        return results


# 使用例
if __name__ == '__main__':
    scraper = DaidataScraper(headless=True)
    
    # 新宿エスパス、台番号682のリアルタイムデータ
    result = scraper.fetch(
        hall_id='100949',
        unit_ids=['682'],
        mode='realtime'
    )
    print(result)
