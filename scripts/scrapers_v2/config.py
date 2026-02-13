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
            base_key = '_'.join(store_key.split('_')[:-1]) if '_sbj' in store_key or '_hokuto2' in store_key else store_key
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
        'hokuto2': 'L北斗の拳 転生の章2',
    }
}

# 取得対象の店舗×機種
SCRAPE_TARGETS = {
    'daidata': {
        'shinjuku_espass': ['sbj', 'hokuto2'],
        'shibuya_espass': ['sbj', 'hokuto2'],
        'akiba_espass': ['sbj', 'hokuto2'],
        'seibu_shinjuku_espass': ['sbj', 'hokuto2'],
        'shibuya_honkan_espass': ['sbj', 'hokuto2'],
        'ueno_espass': ['sbj'],
        'ueno_honkan_espass': ['sbj'],
        'takadanobaba_espass': ['sbj'],
        'akasaka_espass': ['sbj'],
        'shinokubo_espass': ['sbj'],
        'shinkoiwa_espass': ['sbj'],
    },
    'papimo': {
        'island_akihabara': ['sbj', 'hokuto2'],
    }
}

# 機種設定（config/rankings.py の MACHINES と同期）
MACHINE_CONFIG = {
    'sbj': {
        'name': 'Lスーパーブラックジャック',
        'threshold': 130,        # 好調閾値: 1/130以下
        'ceiling': 999,          # 天井: 999G+α
        'reset_ceiling': 600,    # リセット時天井: 600G
        'rb_resets_games': False, # RBでG数リセットされない
    },
    'hokuto2': {
        'name': 'L北斗の拳 転生の章2',
        'threshold': 120,        # 好調閾値: 1/120以下
        'ceiling_type': 'abeshi', # あべしシステム（G数≠あべし）
        'ceiling_a': 1536,       # モードA天井
        'ceiling_b': 896,        # モードB天井
        'ceiling_c': 576,        # モードC天井
        'ceiling_heaven': 128,   # 天国天井
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
