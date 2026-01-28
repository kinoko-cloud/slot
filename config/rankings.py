#!/usr/bin/env python3
"""
店舗・台番号のランキング設定
過去データ分析結果を基にした静的ランキング
"""

# 機種設定
MACHINES = {
    'sbj': {
        'name': 'Lスーパーブラックジャック',
        'short_name': 'スーパーブラックジャック',
        'display_name': 'スーパーブラックジャック',
        'icon': '🃏',
        # 分析閾値（新機種追加時はここを設定するだけで全ロジックに反映）
        'good_prob': 130,      # ART確率がこれ以下なら好調
        'bad_prob': 150,       # ART確率がこれ以上なら不調判定
        'very_bad_prob': 200,  # 明確に低設定
        'typical_daily_games': 6500,  # 1日あたりの一般的な消化G数
        # 天井パラメータ（設定変更/リセット時の天井短縮恩恵）
        # ※液晶表示G数とスタート数（実G数）に差あり
        #   液晶999G+α ≒ スタート数約800G（データサイトはスタート数）
        'normal_ceiling': 800,        # 通常天井（スタート数ベース。液晶表示では999G+α）
        'normal_ceiling_lcd': 999,    # 液晶表示上の天井G数
        'reset_ceiling': 600,         # リセット時天井（朝イチ天井、スタート数ベース）
        'reset_first_hit_bonus': True, # 朝イチ初当たりに恩恵あり
    },
    'hokuto_tensei2': {
        'name': 'L北斗の拳 転生の章2',
        'short_name': '北斗の拳 転生の章2',
        'display_name': '北斗転生2',
        'icon': '👊',
        'good_prob': 120,       # ART確率1/120以下なら好調（実データ70%タイル）
        'bad_prob': 150,        # ART確率1/150以上なら不調（実データ88%タイル）
        'very_bad_prob': 200,   # 明確に低設定
        'typical_daily_games': 7000,  # 北斗は消化速度が速い
        # 天井パラメータ
        'normal_ceiling': 1500,       # 通常天井（スマスロ系で天井が深い）
        'reset_ceiling': 600,         # リセット時天井（朝イチ天井）
        'reset_first_hit_bonus': True, # 朝イチ初当たりに恩恵あり
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
}


def get_machine_threshold(machine_key: str, key: str):
    """機種の閾値を取得（未設定の場合はデフォルト）"""
    m = MACHINES.get(machine_key, {})
    return m.get(key, MACHINE_DEFAULTS.get(key, 0))

# 店舗設定（機種ごと）
STORES = {
    # === SBJ ===
    'island_akihabara_sbj': {
        'name': 'アイランド秋葉原',
        'short_name': 'アイランド秋葉原',
        'hall_id': None,
        'machine': 'sbj',
        'units': [
            '1015', '1016', '1017', '1018', '1020', '1021', '1022', '1023',
            '1025', '1026', '1027', '1028', '1030', '1031'
        ],
        'data_source': 'papimo',
    },
    'shibuya_espass_sbj': {
        'name': 'エスパス日拓渋谷新館',
        'short_name': 'エスパス渋谷新館',
        'hall_id': '100860',
        'machine': 'sbj',
        'units': ['3011', '3012', '3013'],
        'data_source': 'daidata',
    },
    'shinjuku_espass_sbj': {
        'name': 'エスパス日拓新宿歌舞伎町店',
        'short_name': 'エスパス歌舞伎町',
        'hall_id': '100949',
        'machine': 'sbj',
        'units': ['682', '683', '684', '685'],
        'data_source': 'daidata',
    },
    'seibu_shinjuku_espass_sbj': {
        'name': 'エスパス日拓西武新宿駅前店',
        'short_name': 'エスパス西武新宿',
        'hall_id': '100950',
        'machine': 'sbj',
        'units': ['3185', '3186', '3187'],  # 4000番台は全て低貸のため除外
        'data_source': 'daidata',
    },
    'akiba_espass_sbj': {
        'name': 'エスパス日拓秋葉原駅前店',
        'short_name': 'エスパス秋葉原',
        'hall_id': '100928',
        'machine': 'sbj',
        'units': ['2158', '2159', '2160', '2161'],
        'data_source': 'daidata',
    },
    # === 北斗転生2 ===
    'shibuya_espass_hokuto': {
        'name': 'エスパス日拓渋谷新館',
        'short_name': 'エスパス渋谷新館',
        'hall_id': '100860',
        'machine': 'hokuto_tensei2',
        'units': [str(i) for i in range(2046, 2068)] + [str(i) for i in range(2233, 2241)],  # 2046-2067, 2233-2240
        'data_source': 'daidata',
    },
    'shinjuku_espass_hokuto': {
        'name': 'エスパス日拓新宿歌舞伎町店',
        'short_name': 'エスパス歌舞伎町',
        'hall_id': '100949',
        'machine': 'hokuto_tensei2',
        'units': [str(i) for i in range(1, 38)] + [str(i) for i in range(125, 129)],  # 1-37, 125-128
        'data_source': 'daidata',
    },
    'akiba_espass_hokuto': {
        'name': 'エスパス日拓秋葉原駅前店',
        'short_name': 'エスパス秋葉原',
        'hall_id': '100928',
        'machine': 'hokuto_tensei2',
        'units': [str(i) for i in range(2011, 2020)] + [str(i) for i in range(2056, 2069)],  # 2011-2019, 2056-2068
        'data_source': 'daidata',
    },
    'island_akihabara_hokuto': {
        'name': 'アイランド秋葉原',
        'short_name': 'アイランド秋葉原',
        'hall_id': None,
        'machine': 'hokuto_tensei2',
        'units': [f'{i:04d}' for i in range(731, 739)] + [f'{i:04d}' for i in range(750, 758)],  # 0731-0738, 0750-0757
        'data_source': 'papimo',
    },
}

# 旧形式との互換性
STORES['island_akihabara'] = STORES['island_akihabara_sbj']
STORES['shibuya_espass'] = STORES['shibuya_espass_sbj']


def get_stores_by_machine(machine_key: str) -> dict:
    """指定機種がある店舗を取得"""
    result = {}
    # 旧形式のキーは除外
    old_keys = {'island_akihabara', 'shibuya_espass', 'shinjuku_espass'}
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
    'shibuya_espass': {
        '3012': {'rank': 'A', 'score': 70.0, 'note': '7日ART208回'},
        '3011': {'rank': 'A', 'score': 69.3, 'note': '7日ART198回'},
        '3013': {'rank': 'B', 'score': 62.1, 'note': '7日ART192回'},
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
        for suffix in ['_sbj', '_hokuto', '_hokuto_tensei2']:
            if store_key.endswith(suffix):
                alt_key = store_key[:-len(suffix)]
                store_rankings = RANKINGS.get(alt_key, {})
                if store_rankings:
                    break
    return store_rankings.get(unit_id, {'rank': 'C', 'score': 50, 'note': '未評価'})
