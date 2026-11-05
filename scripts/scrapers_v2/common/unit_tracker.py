"""
unit_tracker.py - 台データ変化追跡

一覧ページのG数（スタート回数）をキャッシュして、変化がある台だけ詳細取得
G数が変化していない = 誰も回していない = 詳細取得不要
"""
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Set, Optional

JST = timezone(timedelta(hours=9))
CACHE_DIR = Path(__file__).parent.parent.parent.parent / 'data' / '.unit_cache'


def get_cache_path(store_key: str) -> Path:
    """キャッシュファイルパス"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f'{store_key}.json'


def load_cache(store_key: str) -> Dict[str, int]:
    """前回のG数キャッシュを読み込む"""
    path = get_cache_path(store_key)
    if path.exists():
        try:
            with open(path) as f:
                data = json.load(f)
                return data.get('units', {})
        except:
            pass
    return {}


def save_cache(store_key: str, units: Dict[str, int]):
    """G数キャッシュを保存"""
    path = get_cache_path(store_key)
    with open(path, 'w') as f:
        json.dump({
            'store_key': store_key,
            'updated_at': datetime.now(JST).isoformat(),
            'units': units,
        }, f, ensure_ascii=False, indent=2)


def get_changed_units(store_key: str, current_units: Dict[str, int]) -> Set[str]:
    """
    G数（スタート回数）が変化した台のIDを返す
    
    Args:
        store_key: 店舗キー
        current_units: {unit_id: total_start} の現在値
    
    Returns:
        変化があった台IDのセット（詳細取得が必要な台）
    """
    cached = load_cache(store_key)
    changed = set()
    
    for unit_id, games in current_units.items():
        cached_games = cached.get(unit_id)
        # G数が変化した or 新規台 → 詳細取得必要
        if cached_games is None or cached_games != games:
            changed.add(unit_id)
    
    # キャッシュを更新
    save_cache(store_key, current_units)
    
    return changed


def get_unchanged_units(store_key: str, current_units: Dict[str, int]) -> Set[str]:
    """
    G数が変化していない台のIDを返す（詳細取得をスキップできる台）
    """
    cached = load_cache(store_key)
    unchanged = set()
    
    for unit_id, games in current_units.items():
        cached_games = cached.get(unit_id)
        if cached_games is not None and cached_games == games:
            unchanged.add(unit_id)
    
    return unchanged
