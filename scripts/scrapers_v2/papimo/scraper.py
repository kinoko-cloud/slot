"""
scrapers_v2/papimo/scraper.py - papimoスクレイパー v2

既存scrapers/papimo.pyのリファクタリング版
- 共通基盤(BaseScraper)を使用
- 設定を外部化
- エラー処理を標準化
"""
import re
from typing import Dict, Any, List, Optional
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from common.base import BaseScraper, DataStore, setup_logger, now_jst, today_str

# 店舗設定
# 2026-07-06: sbj/yoshimune/toloveruは取得停止（東京喰種専業に移行）。
# 東京喰種16台はpapimo.jp実地確認で確認済み（machine_id 125030007）
PAPIMO_STORES = {
    'island_akihabara': {
        'hall_id': '00031715',
        'hall_name': 'アイランド秋葉原店',
        'machines': {
            'tokyoghoul': {
                'machine_id': '125030007',
                'machine_name': 'Lスマスロ東京喰種',
                'units': ['162', '163', '165', '166', '167', '168', '170', '171',
                          '172', '173', '175', '176', '177', '178', '180', '181'],
                'expected_keywords': ['東京喰種'],
            },
        }
    }
}


class PapimoScraper(BaseScraper):
    """papimo.jp スクレイパー"""
    
    BASE_URL = "https://papimo.jp"
    
    def __init__(self, headless: bool = True):
        super().__init__(headless=headless, timeout=30000)
    
    def _parse_number(self, s: str) -> int:
        """カンマ区切りの数値を解析"""
        try:
            return int(s.replace(',', ''))
        except:
            return 0
    
    def _validate_machine(self, expected_keywords: List[str]) -> bool:
        """機種名バリデーション"""
        if not expected_keywords:
            return True
        
        try:
            text = self.get_text()
            for line in text.split('\n')[:30]:
                line = line.strip()
                if len(line) > 3:
                    for kw in expected_keywords:
                        if kw in line:
                            return True
        except:
            pass
        
        return False
    
    def _click_more_buttons(self):
        """「もっと見る」ボタンをクリックして全履歴表示"""
        max_clicks = 20
        for _ in range(max_clicks):
            try:
                btn = self.page.query_selector('text=もっと見る')
                if btn and btn.is_visible():
                    btn.click()
                    self.wait(500)
                else:
                    break
            except:
                break
    
    def fetch_unit_history(self, hall_id: str, unit_id: str, 
                          days_back: int = 14, 
                          expected_keywords: List[str] = None) -> Dict[str, Any]:
        """1台分の履歴を取得"""
        url = f"{self.BASE_URL}/h/{hall_id}/hit/view/{unit_id}"
        
        result = {
            'unit_id': unit_id,
            'days': [],
            'fetched_at': now_jst().isoformat()
        }
        
        if not self.navigate(url):
            result['error'] = 'navigation_failed'
            return result
        
        self.wait(2000)
        
        # 機種バリデーション
        if expected_keywords and not self._validate_machine(expected_keywords):
            self.logger.warning(f"Machine mismatch for unit {unit_id}")
            result['machine_mismatch'] = True
            return result
        
        # 利用可能な日付を取得
        available_dates = self.page.evaluate('''() => {
            const select = document.querySelector('#display-date');
            if (!select) return [];
            return Array.from(select.options).map(o => o.value);
        }''')
        
        if not available_dates:
            self.logger.debug(f"No date selector for unit {unit_id}")
            return result
        
        # 日付ごとにデータ取得
        for date_value in available_dates[:days_back]:
            date_str = f"{date_value[:4]}-{date_value[4:6]}-{date_value[6:8]}"
            
            try:
                self.page.select_option('#display-date', date_value)
                self.wait(1500)
                self._click_more_buttons()
                
                day_data = self._parse_day_data(unit_id, date_str)
                if day_data and day_data.get('total_start', 0) > 0:
                    result['days'].append(day_data)
                    self.logger.debug(f"  {date_str}: ART={day_data.get('art', 0)}")
                    
            except Exception as e:
                self.logger.debug(f"  {date_str}: error - {e}")
        
        return result
    
    def _parse_day_data(self, unit_id: str, date_str: str) -> Optional[Dict]:
        """1日分のデータをパース"""
        text = self.get_text()
        
        data = {
            'unit_id': unit_id,
            'date': date_str,
            'status': 'empty',  # デフォルトは空台
        }
        
        # BB/RB/ART回数
        patterns = [
            (r'BB回数\s*(\d+)', 'bb'),
            (r'RB回数\s*(\d+)', 'rb'),
            (r'ART回数\s*(\d+)', 'art'),
            (r'総スタート\s*([\d,]+)', 'total_start'),
            (r'最終スタート\s*([\d,]+)', 'final_start'),
            (r'ARTゲーム数\s*([\d,]+)', 'art_games'),
            (r'最大出メダル\s*([\d,]+)', 'max_medals'),
            (r'合成確率\s*1/([\d,]+)', 'combined_prob'),
        ]
        
        for pattern, key in patterns:
            match = re.search(pattern, text)
            if match:
                data[key] = self._parse_number(match.group(1))
        
        # 当たり履歴
        history = []
        history_pattern = re.findall(
            r'(\d{1,2}:\d{2})\s+([\d,]+)\s+([\d,]+)\s*\n?\s*(ART|BB|RB|AT|REG)',
            text,
            re.MULTILINE
        )
        
        for i, match in enumerate(history_pattern):
            history.append({
                'hit_num': i + 1,
                'time': match[0],
                'start': self._parse_number(match[1]),
                'medals': self._parse_number(match[2]),
                'type': match[3],
            })
        
        if history:
            data['history'] = history
            art_starts = [h['start'] for h in history if h['type'] == 'ART']
            if art_starts:
                data['avg_art_start'] = sum(art_starts) / len(art_starts)
                data['max_art_start'] = max(art_starts)
        
        # 確率計算
        art = data.get('art', 0)
        total_start = data.get('total_start', 0)
        if art > 0 and total_start > 0:
            data['prob'] = round(total_start / art, 1)
            data['is_good_sbj'] = data['prob'] <= 130
            data['is_good_hokuto'] = data['prob'] <= 330

        # final_startが取得できなかった場合、履歴から計算
        if 'final_start' not in data and history and total_start > 0:
            history_total = sum(h['start'] for h in history)
            calculated_final = total_start - history_total
            if calculated_final >= 0:
                data['final_start'] = calculated_final
                self.logger.debug(f"  {unit_id}: final_start calculated: {calculated_final}")

        # ステータス判定: データがあれば遊技中
        if art > 0 or data.get('bb', 0) > 0 or data.get('rb', 0) > 0 or total_start > 0:
            data['status'] = 'playing'

        return data if data.get('total_start', 0) > 0 else None
    
    def discover_units(self, hall_id: str, machine_id: str) -> List[str]:
        """一覧ページから現在の台番号を自動取得"""
        url = f"{self.BASE_URL}/h/{hall_id}/hit/index_sort/{machine_id}/1-20-1290529/83/1/0/0"
        
        if not self.navigate(url):
            return []
        
        self.wait(2000)
        
        text = self.get_text()
        matches = re.findall(r'No\.(\d{4})', text)
        units = sorted(set(matches)) if matches else []
        
        self.logger.info(f"Discovered {len(units)} units: {units}")
        return units
    
    def fetch(self, store_key: str = 'island_akihabara',
              machine_key: str = 'tokyoghoul',
              days_back: int = 7) -> Dict[str, Any]:
        """
        一括取得

        Args:
            store_key: 店舗キー
            machine_key: 機種キー
            days_back: 取得日数
        """
        store = PAPIMO_STORES.get(store_key)
        if not store:
            return {'error': f'Unknown store: {store_key}'}
        
        machine = store['machines'].get(machine_key)
        if not machine:
            return {'error': f'Unknown machine: {machine_key}'}
        
        hall_id = store['hall_id']
        units = machine['units']
        expected_keywords = machine.get('expected_keywords', [])
        
        results = {
            'store_key': store_key,
            'machine_key': machine_key,
            'hall_id': hall_id,
            'hall_name': store['hall_name'],
            'machine_name': machine['machine_name'],
            'units': [],
            'fetched_at': now_jst().isoformat()
        }
        
        self.logger.info(f"Fetching {store_key}/{machine_key}: {len(units)} units, {days_back} days")
        
        with self.browser_session():
            for i, unit_id in enumerate(units, 1):
                self.logger.info(f"[{i}/{len(units)}] Unit {unit_id}")
                
                unit_data = self.fetch_unit_history(
                    hall_id, unit_id, days_back, expected_keywords
                )
                results['units'].append(unit_data)
        
        return results


# CLI
if __name__ == '__main__':
    import sys
    
    scraper = PapimoScraper(headless=True)
    
    # 引数パース
    machine = sys.argv[1] if len(sys.argv) > 1 else 'tokyoghoul'
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    
    if machine == 'discover':
        # 台番号探索モード
        with scraper.browser_session():
            units = scraper.discover_units('00031715', '225110007')
            print(f"発見: {units}")
    else:
        # データ取得
        result = scraper.fetch(
            store_key='island_akihabara',
            machine_key=machine,
            days_back=days
        )
        
        # サマリー表示
        print(f"\n{'='*60}")
        print(f"取得結果: {result['hall_name']} {result['machine_name']}")
        print(f"{'='*60}")
        
        for unit in result['units']:
            unit_id = unit.get('unit_id')
            days_data = unit.get('days', [])
            total_art = sum(d.get('art', 0) for d in days_data)
            total_games = sum(d.get('total_start', 0) for d in days_data)
            print(f"台{unit_id}: {len(days_data)}日, ART={total_art}, G数={total_games:,}")
