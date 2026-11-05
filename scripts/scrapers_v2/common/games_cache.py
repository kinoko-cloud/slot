"""
scrapers_v2/common/games_cache.py - G数キャッシュ（差分取得用）

前回取得時のG数を保存し、変化した台だけ詳細取得するために使用
"""
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

JST = timezone(timedelta(hours=9))

# キャッシュファイルのパス
CACHE_DIR = Path(__file__).parent.parent.parent.parent / 'data' / '.games_cache'


def _ensure_cache_dir():
    """キャッシュディレクトリを作成"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _get_cache_path(store_key: str) -> Path:
    """キャッシュファイルのパスを取得"""
    return CACHE_DIR / f"{store_key}.json"


def load_cache(store_key: str) -> Dict[str, int]:
    """
    前回のG数キャッシュを読み込む

    Returns:
        {unit_id: games} の辞書。日付に関係なく前回値を返す（日跨ぎ比較に使用）
    """
    path = _get_cache_path(store_key)
    if not path.exists():
        return {}

    try:
        with open(path) as f:
            data = json.load(f)
        return data.get('games', {})
    except:
        return {}


def save_cache(store_key: str, games: Dict[str, int]):
    """
    G数キャッシュを保存
    
    Args:
        store_key: 店舗キー
        games: {unit_id: games} の辞書
    """
    _ensure_cache_dir()
    path = _get_cache_path(store_key)
    
    data = {
        'store_key': store_key,
        'games': games,
        'updated_at': datetime.now(JST).isoformat(),
    }
    
    with open(path, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_changed_units(store_key: str, current_games: Dict[str, int]) -> List[str]:
    """
    G数が変化した台のリストを取得
    
    Args:
        store_key: 店舗キー
        current_games: 現在の {unit_id: games} 辞書
    
    Returns:
        G数が変化した台のunit_idリスト
    """
    prev_games = load_cache(store_key)
    
    changed = []
    for unit_id, games in current_games.items():
        # G数が0の台はスキップ（未稼働 or 開店直後）
        if games == 0:
            continue
        prev = prev_games.get(unit_id)

        # 前回データなし or G数変化あり
        if prev is None or prev != games:
            changed.append(unit_id)

    # キャッシュ更新
    save_cache(store_key, current_games)

    return changed


def get_unchanged_units(store_key: str, current_games: Dict[str, int]) -> List[str]:
    """
    G数が変化していない台のリストを取得
    
    Args:
        store_key: 店舗キー
        current_games: 現在の {unit_id: games} 辞書
    
    Returns:
        G数が変化していない台のunit_idリスト
    """
    prev_games = load_cache(store_key)
    
    unchanged = []
    for unit_id, games in current_games.items():
        prev = prev_games.get(unit_id)
        
        # 前回と同じG数
        if prev is not None and prev == games:
            unchanged.append(unit_id)
    
    return unchanged
