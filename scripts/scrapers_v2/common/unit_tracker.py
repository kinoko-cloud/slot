"""
unit_tracker.py - 台データ変化追跡

一覧ページのART数をキャッシュして、変化がある台だけ詳細取得
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
    """前回のART数キャッシュを読み込む"""
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
    """ART数キャッシュを保存"""
    path = get_cache_path(store_key)
    with open(path, 'w') as f:
        json.dump({
            'store_key': store_key,
            'updated_at': datetime.now(JST).isoformat(),
            'units': units,
        }, f, ensure_ascii=False, indent=2)


def get_changed_units(store_key: str, current_units: Dict[str, int]) -> Set[str]:
    """
    ART数が変化した台のIDを返す
    
    Args:
        store_key: 店舗キー
        current_units: {unit_id: art_count} の現在値
    
    Returns:
        変化があった台IDのセット
    """
    cached = load_cache(store_key)
    changed = set()
    
    for unit_id, art_count in current_units.items():
        cached_art = cached.get(unit_id)
        if cached_art is None or cached_art != art_count:
            changed.add(unit_id)
    
    # キャッシュを更新
    save_cache(store_key, current_units)
    
    return changed


def get_unchanged_units(store_key: str, current_units: Dict[str, int]) -> Set[str]:
    """
    ART数が変化していない台のIDを返す（詳細取得をスキップできる台）
    """
    cached = load_cache(store_key)
    unchanged = set()
    
    for unit_id, art_count in current_units.items():
        cached_art = cached.get(unit_id)
        if cached_art is not None and cached_art == art_count:
            unchanged.add(unit_id)
    
    return unchanged
