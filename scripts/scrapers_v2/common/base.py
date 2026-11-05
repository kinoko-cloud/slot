"""
scrapers_v2/common/base.py - スクレイピング共通基盤

設計方針:
- 各データソース（daidata, papimo）で共通の処理を集約
- Playwright管理、リトライ、ログ、データ保存を標準化
- 設定は外部から注入（テスト容易性）
"""
import json
import logging
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

JST = timezone(timedelta(hours=9))

# ロガー設定
def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(name)s] %(levelname)s: %(message)s',
            datefmt='%H:%M:%S'
        ))
        logger.addHandler(handler)
    return logger


class BaseScraper(ABC):
    """スクレイパー基底クラス"""
    
    # Cookie保存先（同意状態を永続化）
    STORAGE_DIR = Path(__file__).parent.parent.parent.parent / 'data' / '.browser_state'
    
    def __init__(self, headless: bool = True, timeout: int = 60000, persist_state: bool = True):
        self.headless = headless
        self.timeout = timeout
        self.persist_state = persist_state
        self.logger = setup_logger(self.__class__.__name__)
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
    
    def _get_storage_path(self, site: str = 'default') -> Path:
        """サイト別のストレージパスを取得"""
        self.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        return self.STORAGE_DIR / f'{site}_state.json'
    
    @contextmanager
    def browser_session(self, site: str = 'daidata'):
        """ブラウザセッションのコンテキストマネージャ（Cookie永続化対応）"""
        storage_path = self._get_storage_path(site)
        
        with sync_playwright() as p:
            self._browser = p.chromium.launch(headless=self.headless)
            
            # 保存済みのCookie/ストレージがあれば読み込む
            if self.persist_state and storage_path.exists():
                try:
                    self._context = self._browser.new_context(storage_state=str(storage_path))
                    self.logger.debug(f"Loaded browser state from {storage_path}")
                except Exception as e:
                    self.logger.warning(f"Failed to load state: {e}")
                    self._context = self._browser.new_context()
            else:
                self._context = self._browser.new_context()
            
            self._page = self._context.new_page()
            try:
                yield self._page
            finally:
                # セッション終了時にCookie/ストレージを保存
                if self.persist_state:
                    try:
                        self._context.storage_state(path=str(storage_path))
                        self.logger.debug(f"Saved browser state to {storage_path}")
                    except Exception as e:
                        self.logger.warning(f"Failed to save state: {e}")
                
                self._browser.close()
                self._browser = None
                self._context = None
                self._page = None
    
    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser session not started. Use browser_session() context.")
        return self._page
    
    def navigate(self, url: str, wait_until: str = 'domcontentloaded') -> bool:
        """ページ遷移（リトライ付き）"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.page.goto(url, timeout=self.timeout, wait_until=wait_until)
                return True
            except Exception as e:
                self.logger.warning(f"Navigate failed (attempt {attempt+1}): {e}")
                if attempt == max_retries - 1:
                    return False
        return False
    
    def wait(self, ms: int = 1000):
        """待機"""
        self.page.wait_for_timeout(ms)
    
    def get_text(self) -> str:
        """ページ全体のテキスト取得"""
        try:
            return self.page.inner_text('body', timeout=self.timeout)
        except:
            return ""
    
    @abstractmethod
    def fetch(self, **kwargs) -> Dict[str, Any]:
        """データ取得（サブクラスで実装）"""
        pass


class DataStore:
    """データ保存・読み込みユーティリティ"""
    
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
    
    def load_json(self, path: Path) -> Optional[Dict]:
        """JSON読み込み"""
        if path.exists():
            try:
                with open(path) as f:
                    return json.load(f)
            except:
                return None
        return None
    
    def save_json(self, path: Path, data: Dict, indent: int = 2):
        """JSON保存"""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
    
    def merge_history(self, path: Path, new_days: List[Dict]) -> int:
        """履歴データのマージ（重複排除）"""
        existing = self.load_json(path) or {'days': []}
        existing_dates = {d['date'] for d in existing.get('days', [])}
        
        added = 0
        for day in new_days:
            if day.get('date') and day['date'] not in existing_dates:
                existing['days'].append(day)
                added += 1
        
        if added > 0:
            existing['days'].sort(key=lambda x: x['date'], reverse=True)
            existing['last_updated'] = datetime.now(JST).isoformat()
            self.save_json(path, existing)
        
        return added


def now_jst() -> datetime:
    """現在時刻（JST）"""
    return datetime.now(JST)


def today_str() -> str:
    """今日の日付文字列"""
    return now_jst().strftime('%Y-%m-%d')
