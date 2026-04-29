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
        # 機種名バリデーション用キーワード（スクレイピング時にページ上の機種名と照合）
        # 新機種追加時は必ず設定すること（同シリーズの他機種と区別できるキーワード）
        'verify_keywords': ['ブラックジャック'],
        # 分析閾値（新機種追加時はここを設定するだけで全ロジックに反映）
        'good_prob': 130,      # ART確率がこれ以下なら好調
        'bad_prob': 150,       # ART確率がこれ以上なら不調判定
        'very_bad_prob': 200,  # 明確に低設定
        'typical_daily_games': 6500,  # 1日あたりの一般的な消化G数
        # 天井パラメータ（設定変更/リセット時の天井短縮恩恵）
        # ART間999G+αで天井。RBはG数リセットしない（回数のみカウント）
        # ※液晶G数は通常時に順押し（左→中→右）した場合のみカウント。
        #   変則押しすると液晶カウントされないがデータ上は1G消費。
        #   そのためデータ上は999+α（変則押し分）になることがある。
        'normal_ceiling': 999,        # 通常天井（液晶G数ベース。データ上は+αの誤差あり）
        'reset_ceiling': 600,         # リセット時天井（朝イチ天井）
        'reset_first_hit_bonus': True, # 朝イチ初当たりに恩恵あり
        'renchain_threshold': 65,     # 連チャン判定: AT間70G以内なら連チャン継続（デフォルト統一）
        # === 推奨条件（2026-02-20 データ分析結果） ===
        'ceiling_target_games': 500,  # 天井狙い閾値: RB込み500G以上
        'explosion_condition': {      # 爆発期待条件
            'prob_threshold': 130,    # 確率1/130以下
            'diff_threshold': -2000,  # 差枚-2000以下
        },
        'big_renchain': 30,           # 大連チャン定義: 30連
        'warning_renchain_count': 2,  # 警戒ライン: 同日30連×2回
    },
    'yoshimune': {
        'name': 'L真打吉宗',
        'short_name': '真打吉宗',
        'display_name': '真打吉宗',
        'icon': '⚔️',
        'verify_keywords': ['吉宗'],
        # ART確率（papimoのart=AT内ゲーム当選回数）: 設定6≒1/56, 設定1≒1/191
        # 実データ中央値1/128、good判定は設定4-5相当の1/90以下
        'good_prob': 90,
        'bad_prob': 150,
        'very_bad_prob': 220,
        'typical_daily_games': 5000,
        # AT間1500G天井、リセット時1000G天井
        'normal_ceiling': 1500,
        'reset_ceiling': 1000,
        'reset_first_hit_bonus': False,
        'renchain_threshold': 65,
    },
    'toloveru': {
        'name': 'L ToLOVEるダークネスver.8.7',
        'short_name': 'ToLOVEるDARKNESS',
        'display_name': 'ToLOVEるDARKNESS',
        'icon': '💕',
        'verify_keywords': ['ToLOVE', 'トラブル'],
        # ART確率（papimoのart=AT初当たり相当）: 設定5-6≒1/290前後
        # 実データ中央値1/330、good判定は設定5相当の1/290以下
        'good_prob': 290,
        'bad_prob': 380,
        'very_bad_prob': 460,
        'typical_daily_games': 5000,
        # ST間999G天井、リセット時650G天井
        'normal_ceiling': 999,
        'reset_ceiling': 650,
        'reset_first_hit_bonus': False,
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

# 店舗設定（機種ごと）
STORES = {
    # === SBJ ===
    'island_akihabara_sbj': {
        'name': 'アイランド秋葉原',
        'short_name': 'アイランド秋葉原',
        'hall_id': None,
        'machine': 'sbj',
        'units': [
            '1018', '1020', '1021', '1022', '1023',
            '1025', '1026', '1027', '1028', '1030', '1031'
        ],  # 2026-04-29: 1015-1017 撤去 (14台→11台)
        'data_source': 'papimo',
    },
    # shibuya_espass_sbj: 2026-03-12確認 SBJ撤退済み（3011も台変動）→ ランキング対象から除外

    'shinjuku_espass_sbj': {
        'name': 'エスパス日拓新宿歌舞伎町店',
        'short_name': 'エスパス歌舞伎町',
        'hall_id': '100949',
        'machine': 'sbj',
        'units': [],  # 2026-04-29: 669-672に移動。2026-04-30: 予測精度低下のため全機種除外
        'data_source': 'daidata',
    },
    'seibu_shinjuku_espass_sbj': {
        'name': 'エスパス日拓西武新宿駅前店',
        'short_name': 'エスパス西武新宿',
        'hall_id': '100950',
        'machine': 'sbj',
        'units': [],  # 3185,3186,3187全て除外: L化物語に台変動(2026-03-02確認)
        'data_source': 'daidata',
    },
    'akiba_espass_sbj': {
        'name': 'エスパス日拓秋葉原駅前店',
        'short_name': 'エスパス秋葉原',
        'hall_id': '100928',
        'machine': 'sbj',
        'units': [],  # 2026-04-29: 2070-2072に移動・減台。2026-04-30: 予測精度低下のため全機種除外
        'data_source': 'daidata',
    },
    # === 真打吉宗 ===
    'island_akihabara_yoshimune': {
        'name': 'アイランド秋葉原',
        'short_name': 'アイランド秋葉原',
        'hall_id': None,
        'machine': 'yoshimune',
        'units': [
            '637', '638', '650', '651', '652', '653', '655', '656', '657', '658',
        ],
        'data_source': 'papimo',
    },
    'shinjuku_espass_yoshimune': {
        'name': 'エスパス日拓新宿歌舞伎町店',
        'short_name': 'エスパス歌舞伎町',
        'hall_id': '100949',
        'machine': 'yoshimune',
        'units': [],  # 2026-04-30: 予測精度低下のため除外
        'data_source': 'daidata',
    },
    'akiba_espass_yoshimune': {
        'name': 'エスパス日拓秋葉原駅前店',
        'short_name': 'エスパス秋葉原',
        'hall_id': '100928',
        'machine': 'yoshimune',
        'units': [],  # 2026-04-30: 予測精度低下のため除外
        'data_source': 'daidata',
    },
    'seibu_shinjuku_espass_yoshimune': {
        'name': 'エスパス日拓西武新宿駅前店',
        'short_name': 'エスパス西武新宿',
        'hall_id': '100950',
        'machine': 'yoshimune',
        'units': ['3111','3112','3113','3114','3115'],
        'data_source': 'daidata',
    },
    'shibuya_espass_yoshimune': {
        'name': 'エスパス日拓渋谷新館',
        'short_name': 'エスパス渋谷新館',
        'hall_id': '100860',
        'machine': 'yoshimune',
        'units': ['3047','3048','3049','3050','3051','3052','3053','3090'],
        'data_source': 'daidata',
    },
    # === ToLOVEるDARKNESS ===
    'island_akihabara_toloveru': {
        'name': 'アイランド秋葉原',
        'short_name': 'アイランド秋葉原',
        'hall_id': None,
        'machine': 'toloveru',
        'units': [
            '1227', '1228', '1230', '1231', '1232', '1233', '1235', '1236', '1237', '1238',
            '1250', '1251', '1252', '1253', '1255', '1256', '1257', '1258',
            '1260', '1261', '1262', '1263', '1265', '1266', '1267', '1268',
            '1270', '1271', '1272', '1273', '1275', '1276', '1277', '1278',
            '1280', '1281', '1282', '1283', '1285', '1286', '1287', '1288',
        ],
        'data_source': 'papimo',
    },
    'shinjuku_espass_toloveru': {
        'name': 'エスパス日拓新宿歌舞伎町店',
        'short_name': 'エスパス歌舞伎町',
        'hall_id': '100949',
        'machine': 'toloveru',
        'units': [],  # 2026-04-30: 予測精度低下のため除外
        'data_source': 'daidata',
    },
    'akiba_espass_toloveru': {
        'name': 'エスパス日拓秋葉原駅前店',
        'short_name': 'エスパス秋葉原',
        'hall_id': '100928',
        'machine': 'toloveru',
        'units': [],  # 2026-04-30: 予測精度低下のため除外
        'data_source': 'daidata',
    },
    'seibu_shinjuku_espass_toloveru': {
        'name': 'エスパス日拓西武新宿駅前店',
        'short_name': 'エスパス西武新宿',
        'hall_id': '100950',
        'machine': 'toloveru',
        'units': ['3195','3196','3197'],
        'data_source': 'daidata',
    },
    'shibuya_espass_toloveru': {
        'name': 'エスパス日拓渋谷新館',
        'short_name': 'エスパス渋谷新館',
        'hall_id': '100860',
        'machine': 'toloveru',
        'units': ['3010','3011','3012'],
        'data_source': 'daidata',
    },
}

# 旧形式との互換性
STORES['island_akihabara'] = STORES['island_akihabara_sbj']
# STORES['shibuya_espass'] = STORES['shibuya_espass_sbj']  # 2026-03-12: SBJ撤退のためコメントアウト



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
        for suffix in ['_sbj', '_yoshimune', '_toloveru']:
            if store_key.endswith(suffix):
                alt_key = store_key[:-len(suffix)]
                store_rankings = RANKINGS.get(alt_key, {})
                if store_rankings:
                    break
    return store_rankings.get(unit_id, {'rank': 'C', 'score': 50, 'note': '未評価'})
