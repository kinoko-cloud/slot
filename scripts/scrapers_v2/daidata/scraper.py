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
            # ボタン形式（複数パターン対応）
            selectors = [
                'button:has-text("利用規約に同意する")',
                'input[type="submit"][value*="同意"]',
                'button:has-text("同意する")',
                'a:has-text("利用規約に同意する")',
                'a:has-text("同意する")',
                '.agree-button',
                '#agree-btn',
            ]
            
            for selector in selectors:
                try:
                    elem = self.page.locator(selector)
                    if elem.count() > 0:
                        elem.first.click()
                        self.wait(3000)
                        self._agreed_halls.add(hall_id)
                        self.logger.info(f"Terms accepted for hall {hall_id} via {selector}")
                        return True
                except:
                    continue
            
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
        current_url = self.page.url
        page_text = self.get_text()[:500]
        if 'terms' in current_url or 'accept' in current_url or '規約' in page_text or '同意' in page_text:
            self.logger.info(f"Detected terms page: {current_url}")
            self._accept_terms(hall_id)
            self.wait(2000)
            # 同意後に元のURLに遷移
            if not self.navigate(url):
                return False
            self.wait(1500)
            self._remove_ads()
            # 再度リダイレクトされた場合のリトライ
            if 'accept' in self.page.url:
                self._accept_terms(hall_id)
                self.wait(2000)
                self.navigate(url)
                self.wait(1500)
        
        return True
    
    def fetch_realtime(self, hall_id: str, unit_id: str) -> Dict[str, Any]:
        """リアルタイムデータ取得（1台分）"""
        url = f"{self.BASE_URL}/{hall_id}/detail?unit={unit_id}"
        
        if not self._goto_with_terms(url, hall_id):
            return {'unit_id': unit_id, 'error': 'navigation_failed'}
        
        # 「リスト表示に切り替える」をクリック（グラフ表示からリスト表示に切り替え）
        try:
            link = self.page.locator('text=リスト表示に切り替える')
            if link.count() > 0:
                link.click()
                self.wait(2000)
        except Exception as e:
            self.logger.debug(f"リスト表示切り替え: {e}")
        
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
        
        # 今日の履歴をテキストから正規表現でパース
        # パターン: 大当たり スタート 出玉 種別 時間
        # 例: 0 32 37 ART 22:36
        today_history = []
        try:
            pattern = r'(\d+)\s+(\d+)\s+(\d+)\s+(ART|RB|BB)\s+(\d{1,2}:\d{2})'
            matches = re.findall(pattern, text)
            for m in matches:
                today_history.append({
                    'hit_num': int(m[0]),
                    'start': int(m[1]),
                    'medals': int(m[2]),
                    'type': m[3],
                    'time': m[4],
                })
            # 時刻順にソート（古い順）
            today_history.sort(key=lambda x: x['time'])
        except Exception as e:
            self.logger.debug(f"Failed to get today_history: {e}")
        
        if today_history:
            data['today_history'] = today_history
        
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
        
        # BB/RB/ART/スタート
        for pattern, key in [
            (r'BB[：:]\s*(\d+)', 'bb'),
            (r'RB[：:]\s*(\d+)', 'rb'),
            (r'ART[：:]\s*(\d+)', 'art'),
            (r'(?:スタート|総回転|G数)[：:]\s*(\d+)', 'games'),
            (r'(\d+)\s*(?:G|回転)', 'games'),  # フォールバック
        ]:
            match = re.search(pattern, text)
            if match and key not in day:  # 重複防止
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
        
        # gamesがない場合、履歴からstartの合計を計算
        if not day.get('games') and day.get('history'):
            total_start = sum(h.get('start', 0) for h in day['history'])
            if total_start > 0:
                day['games'] = total_start
                day['total_start'] = total_start
        
        return day if day.get('history') or day.get('art', 0) > 0 or day.get('games', 0) > 0 else None
    
    def fetch_list_games(self, hall_id: str, model_encoded: str) -> Dict[str, int]:
        """
        一覧ページから全台のG数（スタート回数）を一括取得
        
        Returns:
            {unit_id: total_start} の辞書
        """
        url = f"{self.BASE_URL}/{hall_id}/unit_list?model={model_encoded}&ballPrice=21.70&ps=S"
        
        if not self._goto_with_terms(url, hall_id):
            return {}
        
        self.wait(2000)
        
        results = {}
        try:
            text = self.get_text()
            for line in text.split('\n'):
                # パターン: 台番号 BB RB ART スタート回数
                # 例: 682 5 3 42 1234
                match = re.match(r'^\s*(\d+)\s+\d+\s+\d+\s+\d+\s+(\d+)', line)
                if match:
                    unit_id = match.group(1)
                    games = int(match.group(2))
                    results[unit_id] = games
        except Exception as e:
            self.logger.error(f"fetch_list_games error: {e}")
        
        return results
    
    def fetch_list_with_availability(self, hall_id: str, model_encoded: str, expected_units: List[str] = None) -> Dict[str, Any]:
        """
        一覧ページから全台のG数 + 空き/遊技中を一括取得
        
        Returns:
            {
                'games': {unit_id: total_start},
                'playing': [unit_ids...],  # 遊技中
                'empty': [unit_ids...],    # 空き
            }
        """
        url = f"{self.BASE_URL}/{hall_id}/unit_list?model={model_encoded}&ballPrice=21.70&ps=S"
        
        if not self._goto_with_terms(url, hall_id):
            return {'games': {}, 'playing': [], 'empty': []}
        
        self.wait(2000)
        
        games = {}
        playing = []
        empty = []
        
        try:
            html = self.page.content()
            
            # HTMLから台番号と遊技状態を抽出
            # パターン: <tr>内で icon-user があれば遊技中
            for unit_id in (expected_units or []):
                # 台番号を含む行を検索
                pattern = rf'<tr[^>]*>.*?<td[^>]*>(.*?)</td>\s*<td[^>]*>\s*<a[^>]*>\s*{unit_id}\s*</a>'
                match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
                
                if match:
                    first_td = match.group(1)
                    if 'icon-user' in first_td:
                        playing.append(unit_id)
                    else:
                        empty.append(unit_id)
                else:
                    empty.append(unit_id)  # 見つからない場合は空きとみなす
            
            # G数も取得
            text = self.get_text()
            for line in text.split('\n'):
                match = re.match(r'^\s*(\d+)\s+\d+\s+\d+\s+\d+\s+(\d+)', line)
                if match:
                    unit_id = match.group(1)
                    g = int(match.group(2))
                    games[unit_id] = g
                    
        except Exception as e:
            self.logger.error(f"fetch_list_with_availability error: {e}")
        
        return {
            'games': games,
            'playing': sorted(playing),
            'empty': sorted(empty),
            'total': len(expected_units) if expected_units else len(games),
        }
    
    def fetch_selective(self, hall_id: str, model_encoded: str, store_key: str) -> Dict[str, Any]:
        """
        G数が変化した台だけ詳細取得（高速化版）
        
        1. 一覧ページでG数を一括取得
        2. 前回キャッシュと比較（G数が同じ=誰も回してない=スキップ）
        3. 変化した台だけ詳細取得
        """
        from common.unit_tracker import get_changed_units
        
        results = {'hall_id': hall_id, 'units': [], 'fetched_at': now_jst().isoformat()}
        
        with self.browser_session():
            # 一覧からG数を取得
            current_games = self.fetch_list_games(hall_id, model_encoded)
            self.logger.info(f"一覧取得: {len(current_games)}台")
            
            if not current_games:
                return results
            
            # G数が変化した台を特定
            changed_units = get_changed_units(store_key, current_games)
            self.logger.info(f"G数変化: {len(changed_units)}台 / {len(current_games)}台")
            
            # 変化した台だけ詳細取得
            for unit_id in changed_units:
                data = self.fetch_realtime(hall_id, unit_id)
                results['units'].append(data)
            
            # 変化なし台は基本データのみ（前回のキャッシュを使用）
            for unit_id, games in current_games.items():
                if unit_id not in changed_units:
                    results['units'].append({
                        'unit_id': unit_id,
                        'total_start': games,
                        'cached': True,
                    })
        
        return results
    
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
