#!/usr/bin/env python3
"""
店舗・台番号のランキング設定
過去データ分析結果を基にした静的ランキング
"""

# 機種設定
MACHINES = {
    'tokyoghoul': {
        'name': 'Lスマスロ東京喰種',
        'short_name': '東京喰種',
        'display_name': '東京喰種',
        'icon': '🫀',
        'verify_keywords': ['東京喰種'],
        # AT初当たり確率: 設定1=1/394, 設定6=1/261
        # 機械割: 設定1=97.5%, 設定6=114.9%
        # 実データ中央値は蓄積後に調整
        'good_prob': 310,      # 設定4相当（1/310以下で好調）
        'bad_prob': 380,       # 設定1以上（1/380以上で不調）
        'very_bad_prob': 450,
        'typical_daily_games': 5000,
        # CZ間天井: 600G+α（CZ/AT当選）
        # AT間天井: 1200G+α（AT当選）
        # リセット後: CZ間天井が200G+αに短縮（大きな恩恵）
        'normal_ceiling': 1200,
        'reset_ceiling': 200,
        'reset_first_hit_bonus': True,
        'renchain_threshold': 65,
    },
}

# 新機種追加時のデフォルト閾値
MACHINE_DEFAULTS = {
    'good_prob': 200,
    'bad_prob': 250,
    'very_bad_prob': 350,
    'typical_daily_games': 5000,
    'normal_ceiling': 999,
    'reset_ceiling': 999,       # デフォルトはリセット恩恵なし（通常天井と同じ）
    'reset_first_hit_bonus': False,
    'renchain_threshold': 65,   # デフォルト連チャン閾値
}


def get_machine_threshold(machine_key: str, key: str):
    """機種の閾値を取得（未設定の場合はデフォルト）"""
    m = MACHINES.get(machine_key, {})
    return m.get(key, MACHINE_DEFAULTS.get(key, 0))

# 店舗設定（東京喰種のみ）
STORES = {
    'shinjuku_espass_tokyoghoul': {
        'name': 'エスパス日拓新宿歌舞伎町店',
        'short_name': 'エスパス歌舞伎町',
        'hall_id': '100949',
        'machine': 'tokyoghoul',
        'units': [
            '801', '802', '803', '804', '805', '806', '807', '808', '809', '810',
            '811', '812', '813', '814', '815', '830', '831', '832', '833', '834',
            '838', '839', '840', '841', '842', '843', '844', '845', '846', '847',
            '848', '849', '850', '851', '852', '853', '854', '855', '856', '857',
            '858', '859', '860',
        ],
        'data_source': 'daidata',
    },
    'akiba_espass_tokyoghoul': {
        'name': 'エスパス日拓秋葉原駅前店',
        'short_name': 'エスパス秋葉原',
        'hall_id': '100928',
        'machine': 'tokyoghoul',
        'units': [
            '4076', '4077', '4078', '4079', '4080', '4081', '4082', '4083', '4084', '4085', '4086',
            '4156', '4157', '4158', '4159', '4160', '4161', '4162', '4163', '4164',
            '4165', '4166', '4167', '4168', '4169', '4170', '4171', '4172',
        ],
        'data_source': 'daidata',
    },
    'seibu_shinjuku_espass_tokyoghoul': {
        'name': 'エスパス日拓西武新宿駅前店',
        'short_name': 'エスパス西武新宿',
        'hall_id': '100950',
        'machine': 'tokyoghoul',
        'units': [
            '3159', '3160', '3161', '3162', '3163', '3164', '3165', '3166', '3167', '3168',
            '3169', '3170', '3171', '3172', '3173', '3174', '3218',
        ],
        'data_source': 'daidata',
    },
    'shibuya_espass_tokyoghoul': {
        'name': 'エスパス日拓渋谷新館',
        'short_name': 'エスパス渋谷新館',
        'hall_id': '100860',
        'machine': 'tokyoghoul',
        'units': [
            '2075', '2076', '2077', '2078', '2079', '2080', '2081', '2082', '2083', '2084',
            '2085', '2086', '2087', '2088', '2089', '2090', '2091', '2092', '2093', '2094',
            '2095', '2096', '2097', '2098', '2099', '2100', '2101', '2102', '2103', '2104',
            '2105', '2106', '2107',
        ],
        'data_source': 'daidata',
    },
    'island_akihabara_tokyoghoul': {
        'name': 'アイランド秋葉原',
        'short_name': 'アイランド秋葉原',
        'hall_id': None,
        'machine': 'tokyoghoul',
        # 2026-07-06: papimo.jp実地確認で16台確認（machine_id 125030007）
        'units': [
            '162', '163', '165', '166', '167', '168', '170', '171',
            '172', '173', '175', '176', '177', '178', '180', '181',
        ],
        'data_source': 'papimo',
    },
}



def get_stores_by_machine(machine_key: str) -> dict:
    """指定機種がある店舗を取得"""
    result = {}
    # 旧形式のキーは除外
    old_keys = set()
    for store_key, store in STORES.items():
        if store_key in old_keys:
            continue
        if store.get('machine') == machine_key and store.get('units'):
            result[store_key] = store
    return result


def get_machine_info(machine_key: str) -> dict:
    """機種情報を取得"""
    return MACHINES.get(machine_key, {'name': machine_key, 'short_name': machine_key, 'icon': '🎰'})

# 静的ランキング（2026/1/26時点の分析結果）
# S: 高設定濃厚, A: 高設定可能性高, B: まずまず, C: 様子見, D: 非推奨
RANKINGS = {
    'island_akihabara': {
        '1023': {'rank': 'S', 'score': 80.7, 'note': '7日ART497回'},
        '1030': {'rank': 'S', 'score': 77.9, 'note': '7日ART416回'},
        '1025': {'rank': 'S', 'score': 77.1, 'note': '7日ART388回'},
        '1017': {'rank': 'S', 'score': 75.0, 'note': '7日ART488回'},
        '1016': {'rank': 'A', 'score': 74.3, 'note': ''},
        '1027': {'rank': 'A', 'score': 73.6, 'note': ''},
        '1026': {'rank': 'A', 'score': 72.9, 'note': ''},
        '1020': {'rank': 'A', 'score': 72.1, 'note': ''},
        '1021': {'rank': 'A', 'score': 71.4, 'note': ''},
        '1028': {'rank': 'A', 'score': 70.7, 'note': ''},
        '1018': {'rank': 'A', 'score': 70.0, 'note': ''},
        '1022': {'rank': 'B', 'score': 68.6, 'note': ''},
        '1031': {'rank': 'B', 'score': 66.4, 'note': ''},
        '1015': {'rank': 'B', 'score': 65.7, 'note': ''},
    },
}

# 台評価の閾値
SCORE_THRESHOLDS = {
    'S': 75,  # 高設定濃厚
    'A': 65,  # 高設定可能性高
    'B': 55,  # まずまず
    'C': 45,  # 様子見
    'D': 0,   # 非推奨
}

def get_rank(score: float) -> str:
    """スコアからランクを取得"""
    for rank, threshold in sorted(SCORE_THRESHOLDS.items(), key=lambda x: -x[1]):
        if score >= threshold:
            return rank
    return 'D'

_RANK_ORDER = ['D', 'C', 'B', 'A', 'S']

def rank_up(rank: str) -> str:
    """ランクを1段階上げる（S→S）"""
    idx = _RANK_ORDER.index(rank) if rank in _RANK_ORDER else 0
    return _RANK_ORDER[min(idx + 1, len(_RANK_ORDER) - 1)]

def rank_down(rank: str) -> str:
    """ランクを1段階下げる（D→D）"""
    idx = _RANK_ORDER.index(rank) if rank in _RANK_ORDER else 0
    return _RANK_ORDER[max(idx - 1, 0)]

def get_store_units(store_key: str) -> list:
    """店舗の台番号リストを取得"""
    store = STORES.get(store_key)
    if not store:
        return []
    return store.get('units', [])

def get_unit_ranking(store_key: str, unit_id: str) -> dict:
    """台のランキング情報を取得"""
    store_rankings = RANKINGS.get(store_key, {})
    if not store_rankings:
        # 機種サフィックスなしのキーでも検索
        for suffix in ['_tokyoghoul']:
            if store_key.endswith(suffix):
                alt_key = store_key[:-len(suffix)]
                store_rankings = RANKINGS.get(alt_key, {})
                if store_rankings:
                    break
    return store_rankings.get(unit_id, {'rank': 'C', 'score': 50, 'note': '未評価'})
