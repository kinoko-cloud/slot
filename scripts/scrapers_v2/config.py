"""
scrapers_v2/config.py - スクレイパー設定

v1の設定（fetch_daidata_availability.py）を直接インポート
v2はスクレイピング部分の高速化のみ担当
"""
from pathlib import Path
import sys

# v1の設定をインポート
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from fetch_daidata_availability import DAIDATA_STORES
except ImportError:
    DAIDATA_STORES = {}

# 既存の設定をインポート
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
try:
    from config.stores import ESPASS_STORES, PAPIMO_STORES
except ImportError:
    ESPASS_STORES = {}
    PAPIMO_STORES = {}

# v1のDAIDATA_STORESからhall_mappingを構築
def _build_hall_mapping():
    """v1の設定からhall_id→store_keyマッピングを構築"""
    mapping = {}
    for store_key, cfg in DAIDATA_STORES.items():
        hall_id = cfg.get('hall_id')
        if hall_id:
            # store_keyから機種部分を除去（shibuya_espass_sbj → shibuya_espass）
            base_key = '_'.join(store_key.split('_')[:-1]) if any(store_key.endswith(f'_{m}') for m in ('sbj', 'yoshimune', 'toloveru')) else store_key
            if hall_id not in mapping:
                mapping[hall_id] = base_key
    return mapping

# daidata用設定（v1からインポート）
DAIDATA_CONFIG = {
    'stores': DAIDATA_STORES,  # v1の設定をそのまま使用
    'hall_mapping': _build_hall_mapping(),
}

# papimo用設定
PAPIMO_CONFIG = {
    'stores': {
        'island_akihabara': {
            'url': 'https://papimo.jp/akihabara',
            'name': 'アイランド秋葉原店',
        }
    },
    'machines': {
        'sbj': 'Lスーパーブラックジャック',
        'yoshimune': 'L真打吉宗',
        'toloveru': 'L ToLOVEるダークネスver.8.7',
    }
}

# 取得対象の店舗×機種
SCRAPE_TARGETS = {
    'daidata': {
        'shinjuku_espass': ['sbj', 'yoshimune', 'toloveru'],
        'shibuya_espass': ['yoshimune', 'toloveru'],
        'akiba_espass': ['sbj', 'yoshimune', 'toloveru'],
        'seibu_shinjuku_espass': ['sbj', 'yoshimune', 'toloveru'],
        # 'shibuya_honkan_espass': [],  # 2026-03-31 閉店
        'ueno_espass': ['sbj'],
        'ueno_honkan_espass': ['sbj'],
        'takadanobaba_espass': ['sbj'],
        'akasaka_espass': ['sbj'],
        'shinokubo_espass': ['sbj'],
        'shinkoiwa_espass': ['sbj'],
    },
    'papimo': {
        'island_akihabara': ['sbj', 'yoshimune', 'toloveru'],
    }
}

# 機種設定（config/rankings.py の MACHINES と同期）
MACHINE_CONFIG = {
    'sbj': {
        'name': 'Lスーパーブラックジャック',
        'threshold': 130,
        'ceiling': 999,
        'reset_ceiling': 600,
        'rb_resets_games': False,
    },
    'yoshimune': {
        'name': 'L真打吉宗',
        'threshold': 90,
        'ceiling': 1500,
        'reset_ceiling': 1000,
        'rb_resets_games': False,
    },
    'toloveru': {
        'name': 'L ToLOVEるダークネスver.8.7',
        'threshold': 290,
        'ceiling': 999,
        'reset_ceiling': 650,
        'rb_resets_games': False,
    },
}


def get_hall_id(store_key: str) -> str:
    """store_keyからhall_idを取得"""
    for hall_id, key in DAIDATA_CONFIG['hall_mapping'].items():
        if key == store_key:
            return hall_id
    return None

def get_store_key(hall_id: str) -> str:
    """hall_idからstore_keyを取得"""
    return DAIDATA_CONFIG['hall_mapping'].get(hall_id)

def is_good_condition(machine_key: str, art: int, total_start: int) -> bool:
    """好調判定（機種別閾値ベース）"""
    if art <= 0 or total_start <= 0:
        return False
    
    config = MACHINE_CONFIG.get(machine_key, {})
    threshold = config.get('threshold', 130)
    prob = total_start / art
    
    return prob <= threshold

def calc_prob(art: int, total_start: int) -> int:
    """確率計算（整数表示）"""
    if art <= 0:
        return 0
    return int(total_start / art)
