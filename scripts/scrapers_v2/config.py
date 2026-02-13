"""
scrapers_v2/config.py - スクレイパー設定

店舗・機種の定義を一元管理
既存のconfig/stores.pyと連携

【CLAUDE.md記載の仕様】
- SBJ: 天井999G+α、RBリセットなし、好調閾値1/130
- 北斗2: あべしシステム、好調閾値1/120
- 確率は整数表示
- 設定Xは使わない（機械割で表現）
"""
from pathlib import Path
import sys

# 既存の設定をインポート
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
try:
    from config.stores import ESPASS_STORES, PAPIMO_STORES
except ImportError:
    ESPASS_STORES = {}
    PAPIMO_STORES = {}

# daidata用設定
DAIDATA_CONFIG = {
    # 店舗ID → store_key のマッピング
    'hall_mapping': {
        '100949': 'shinjuku_espass',      # 新宿エスパス歌舞伎町
        '100860': 'shibuya_espass',       # 渋谷エスパス新館
        '100928': 'akiba_espass',         # 秋葉原エスパス駅前
        '100950': 'seibu_shinjuku_espass', # 西武新宿駅前エスパス
        '100196': 'ueno_espass',          # エスパス上野新館
        '100947': 'ueno_honkan_espass',   # エスパス上野本館
        '100915': 'takadanobaba_espass',  # エスパス高田馬場
        '100952': 'akasaka_espass',       # エスパス赤坂見附
        '100951': 'shinokubo_espass',     # エスパス新大久保
        '100260': 'shinkoiwa_espass',     # エスパス新小岩
        '100856': 'shibuya_honkan_espass', # 渋谷エスパス本館
    },
    
    # 機種名（URLエンコード済み）
    'machine_encoding': {
        'sbj': 'L%EF%BD%BD%EF%BD%B0%EF%BE%8A%EF%BE%9F%EF%BD%B0%EF%BE%8C%EF%BE%9E%EF%BE%97%EF%BD%AF%EF%BD%B8%EF%BD%BC%EF%BE%9E%EF%BD%AC%EF%BD%AF%EF%BD%B8',
        'hokuto': '%E5%8C%97%E6%96%97%E3%81%AE%E6%8B%B3',
        'hokuto2': '%E5%8C%97%E6%96%97%E3%81%AE%E6%8B%B3%20%E8%BB%A2%E7%94%9F%E3%81%AE%E7%AB%A0',
    },
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
        'hokuto': '北斗の拳',
        'hokuto2': '北斗の拳 転生の章',
    }
}

# 取得対象の店舗×機種
SCRAPE_TARGETS = {
    'daidata': {
        'shinjuku_espass': ['sbj', 'hokuto', 'hokuto2'],
        'shibuya_espass': ['sbj', 'hokuto', 'hokuto2'],
        'akiba_espass': ['sbj', 'hokuto', 'hokuto2'],
        'seibu_shinjuku_espass': ['sbj', 'hokuto', 'hokuto2'],
        'shibuya_honkan_espass': ['sbj', 'hokuto', 'hokuto2'],
        'ueno_espass': ['sbj'],
        'ueno_honkan_espass': ['sbj'],
        'takadanobaba_espass': ['sbj'],
        'akasaka_espass': ['sbj'],
        'shinokubo_espass': ['sbj'],
        'shinkoiwa_espass': ['sbj'],
    },
    'papimo': {
        'island_akihabara': ['sbj', 'hokuto', 'hokuto2'],
    }
}

# 機種設定（CLAUDE.md準拠）
MACHINE_CONFIG = {
    'sbj': {
        'name': 'Lスーパーブラックジャック',
        'threshold': 130,        # 好調閾値: 1/130以下
        'ceiling': 999,          # 天井: 999G+α
        'reset_ceiling': 666,    # リセット時天井: 666G+α
        'rb_resets_games': False, # RBでG数リセットされない
    },
    'hokuto': {
        'name': 'L北斗の拳 転生の章',
        'threshold': 120,        # 好調閾値: 1/120以下
        'ceiling_type': 'abeshi', # あべしシステム
        'ceiling_a': 1536,       # モードA天井
        'ceiling_b': 896,        # モードB天井
        'ceiling_c': 576,        # モードC天井
        'ceiling_heaven': 128,   # 天国天井
    },
    'hokuto2': {
        'name': 'L北斗の拳 転生の章2',
        'threshold': 120,
        'ceiling_type': 'abeshi',
        'ceiling_a': 1536,
        'ceiling_b': 896,
        'ceiling_c': 576,
        'ceiling_heaven': 128,
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
