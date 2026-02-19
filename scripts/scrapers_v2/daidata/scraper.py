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
        """規約同意処理（v3方式: 毎回チェック）"""
        # v3効率化: ホール管理せず毎回ボタンをチェック
        try:
            # メインのボタン（最も一般的）
            agree_btn = self.page.locator('button:has-text("利用規約に同意する")')
            if agree_btn.count() > 0 and agree_btn.first.is_visible():
                agree_btn.first.click()
                self.wait(2000)
                self._remove_ads()
                self.logger.info(f"Terms accepted for hall {hall_id}")
                return True
            
            # その他のパターン
            selectors = [
                'input[type="submit"][value*="同意"]',
                'button:has-text("同意する")',
                'a:has-text("利用規約に同意する")',
                'a:has-text("同意する")',
            ]
            
            for selector in selectors:
                try:
                    elem = self.page.locator(selector)
                    if elem.count() > 0 and elem.first.is_visible():
                        elem.first.click()
                        self.wait(2000)
                        self._remove_ads()
                        self.logger.info(f"Terms accepted for hall {hall_id} via {selector}")
                        return True
                except:
                    continue
                
        except Exception as e:
            self.logger.debug(f"Terms accept check: {e}")
        
        return False
    
    def _goto_with_terms(self, url: str, hall_id: str) -> bool:
        """ページ遷移＋規約同意（v3方式: シンプル化）"""
        if not self.navigate(url):
            return False
        
        self.wait(2500)  # v3と同じ待機時間
        self._remove_ads()
        
        # 規約同意（表示されていたらクリック）
        self._accept_terms(hall_id)
        
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

        # マッチしない場合（規約ページが残っている可能性）→ リトライ
        if 'BB' not in text or 'スタート' not in text:
            self.logger.debug(f"Unit {unit_id}: Page may be terms page, retrying...")
            self._accept_terms(hall_id)
            self.wait(1500)
            self.page.goto(url, timeout=20000, wait_until='domcontentloaded')
            self.wait(2000)
            # リスト表示に再度切り替え
            try:
                link = self.page.locator('text=リスト表示に切り替える')
                if link.count() > 0:
                    link.click()
                    self.wait(2000)
            except:
                pass
            text = self.get_text()

        # 機種種別チェック（台変動検知）
        # ページ先頭20行以内に'Slot'または'Pachinko'が表示される
        fetched_at = now_jst().isoformat()
        if 'お探しの台は見つかりませんでした' in text:
            self.logger.info(f"Unit {unit_id}: 台が見つからない（撤去または台番号変更）")
            return {'unit_id': unit_id, 'not_found': True, 'error': 'not_found',
                    'bb': 0, 'rb': 0, 'art': 0, 'total_start': 0, 'final_start': 0,
                    'status': 'empty', 'fetched_at': fetched_at}
        page_kind = None
        for line in text.split('\n')[:20]:
            stripped = line.strip()
            if stripped in ('Slot', 'Pachinko'):
                page_kind = stripped
                break
        if page_kind == 'Pachinko':
            self.logger.warning(f"Unit {unit_id}: パチンコ台（台変動）- スキップ")
            return {'unit_id': unit_id, 'machine_mismatch': True, 'error': 'machine_mismatch',
                    'bb': 0, 'rb': 0, 'art': 0, 'total_start': 0, 'final_start': 0,
                    'status': 'empty', 'fetched_at': fetched_at}

        data = {
            'unit_id': unit_id,
            'bb': 0, 'rb': 0, 'art': 0,
            'total_start': 0, 'final_start': 0,
            'status': 'empty',  # デフォルトは空台
            'fetched_at': now_jst().isoformat()
        }
        
        # ページから日付を取得（「本日の大当たり履歴詳細（2月19日）」または「2026.02.19」）
        date_match = re.search(r'本日の大当たり履歴詳細（(\d+)月(\d+)日）', text)
        if date_match:
            month = int(date_match.group(1))
            day = int(date_match.group(2))
            year = now_jst().year
            # 12月に1月のデータを見ている場合は翌年
            if now_jst().month == 12 and month == 1:
                year += 1
            data['date'] = f"{year}-{month:02d}-{day:02d}"
        else:
            # フォールバック: 「2026.02.19 23:53現在」パターン
            alt_date = re.search(r'(\d{4})\.(\d{2})\.(\d{2})\s+\d{1,2}:\d{2}現在', text)
            if alt_date:
                data['date'] = f"{alt_date.group(1)}-{alt_date.group(2)}-{alt_date.group(3)}"
        
        # BB RB ART スタート回数（複数パターン対応）
        match = re.search(r'BB\s+RB\s+ART\s+スタート(?:回数)?\s*\n?\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)', text)
        if match:
            data['bb'] = int(match.group(1))
            data['rb'] = int(match.group(2))
            data['art'] = int(match.group(3))
            data['final_start'] = int(match.group(4))
        else:
            # フォールバック: 各項目を個別に取得
            bb_match = re.search(r'BB\s*\n?\s*(\d+)', text)
            rb_match = re.search(r'RB\s*\n?\s*(\d+)', text)
            art_match = re.search(r'ART\s*\n?\s*(\d+)', text)
            start_match = re.search(r'スタート(?:回数)?\s*\n?\s*(\d+)', text)
            if bb_match:
                data['bb'] = int(bb_match.group(1))
            if rb_match:
                data['rb'] = int(rb_match.group(1))
            if art_match:
                data['art'] = int(art_match.group(1))
            if start_match:
                data['final_start'] = int(start_match.group(1))
        
        # 累計スタート（複数パターン対応）
        total = re.search(r'累計スタート\s*\n?\s*(\d+)', text)
        if total:
            data['total_start'] = int(total.group(1))
        else:
            # 別パターン: 「累計スタート」ではなく「累計」の場合
            alt_total = re.search(r'累計\s*\n?\s*(\d+)\s*G', text)
            if alt_total:
                data['total_start'] = int(alt_total.group(1))
            else:
                # さらに別パターン
                alt_total2 = re.search(r'(\d+)\s*G\s*累計', text)
                if alt_total2:
                    data['total_start'] = int(alt_total2.group(1))
        
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
            # total_startが取得できなかった場合、履歴から推定
            if data['total_start'] == 0 and len(today_history) > 0:
                # 最後の当たりのスタート数を使用
                last_start = max(h.get('start', 0) for h in today_history)
                if last_start > 0:
                    data['total_start'] = last_start
                    self.logger.info(f"Unit {unit_id}: total_start estimated from history: {last_start}")
        
        # ステータス判定: データがあれば遊技中
        if data['art'] > 0 or data['bb'] > 0 or data['rb'] > 0 or data['total_start'] > 0:
            data['status'] = 'playing'

        # デバッグ: total_start=0でART>0は異常
        if data['total_start'] == 0 and data['art'] > 0:
            self.logger.warning(f"Unit {unit_id}: total_start=0 but art={data['art']} - data may be incomplete")
            # ページテキストの一部をログに出力（デバッグ用）
            self.logger.debug(f"Page text sample: {text[:1000]}")

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
        
        # 履歴テーブル（大当たり, スタート, 出玉, 種別, 時間）
        try:
            rows = self.page.locator('table tr').all()
            for row in rows[1:]:  # ヘッダースキップ
                cells = row.locator('td').all()
                if len(cells) >= 5:
                    # カラム順: 大当たり(0), スタート(1), 出玉(2), 種別(3), 時間(4)
                    start_text = cells[1].inner_text().strip()
                    medals_text = cells[2].inner_text().strip()
                    type_text = cells[3].inner_text().strip()
                    time_text = cells[4].inner_text().strip()
                    
                    # 数値変換（空文字対策）
                    try:
                        start = int(start_text) if start_text.isdigit() else 0
                    except:
                        start = 0
                    try:
                        medals = int(medals_text) if medals_text.isdigit() else 0
                    except:
                        medals = 0
                    
                    if type_text in ['ART', 'BB', 'RB']:  # 有効なタイプのみ
                        item = {
                            'start': start,
                            'medals': medals,
                            'type': type_text,
                            'time': time_text,
                        }
                        day['history'].append(item)
        except Exception as e:
            self.logger.debug(f"History parse error: {e}")
        
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
            
            # G数・ART等・スタート回数を取得
            arts = {}
            starts = {}  # スタート回数（現在のハマり）
            text = self.get_text()
            for line in text.split('\n'):
                # 台番号 累計G BB RB ART ... スタート回数（最後の数字）
                # 例: 682 111 0 0 0 15 1/0.0 1/0.0 0.0 0.0 143 111
                match = re.match(r'^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)', line)
                if match:
                    unit_id = match.group(1)
                    games[unit_id] = int(match.group(2))
                    # BB=group(3), RB=group(4), ART=group(5)
                    arts[unit_id] = int(match.group(5))
                    # 行末の数字がスタート回数（複数パターン対応）
                    all_nums = re.findall(r'\d+', line)
                    if len(all_nums) >= 6:
                        # 通常パターン: 最後から2番目または最後の数字
                        # 行末が「143 111」のような場合、143がスタート回数
                        last_two = [int(all_nums[-2]), int(all_nums[-1])]
                        # 累計Gと同じ値が行末にある場合、その前の数字がスタート
                        total_g = int(match.group(2))
                        if last_two[1] == total_g and last_two[0] != total_g:
                            starts[unit_id] = last_two[0]
                        else:
                            starts[unit_id] = last_two[1]
                    elif len(all_nums) >= 2:
                        starts[unit_id] = int(all_nums[-1])
                    
        except Exception as e:
            self.logger.error(f"fetch_list_with_availability error: {e}")
        
        return {
            'games': games,
            'arts': arts,
            'starts': starts,  # スタート回数（現在のハマり）
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
