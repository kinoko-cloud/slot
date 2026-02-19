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
        'renchain_threshold': 30,     # 連チャン判定: AT間30G以内なら連チャン継続
                                       # SBJのATは1セット約30G消化。AT間1-10Gは即連（有利区間内）。
                                       # 30G以上空いたら通常に戻った可能性 → 新規初当たり扱い。
                                       # 実績: 閾値30Gで最大20連程度（現実的な範囲）
    },
    'hokuto2': {
        'name': 'L北斗の拳 転生の章2',
        'short_name': '北斗の拳 転生の章2',
        'display_name': '北斗転生2',
        'icon': '👊',
        # 「北斗」だけだと北斗無双等と混同する。「転生」で絞り込む
        'verify_keywords': ['北斗', '転生'],
        'good_prob': 120,       # ART確率1/120以下なら好調（実データ70%タイル）
        'bad_prob': 150,        # ART確率1/150以上なら不調（実データ88%タイル）
        'very_bad_prob': 200,   # 明確に低設定
        'typical_daily_games': 7000,  # 北斗は消化速度が速い
        # 天井パラメータ
        # ※北斗転生2の天井は「あべしpt」ベース（液晶表示）。G数ではない。
        #   データサイトからあべし数は取得不可。G数のみ。
        #   あべしとG数は比例しない。レア役で大量加算されるため、
        #   極端な例では10Gで天井到達もありうる。
        #   → G数ベースの天井判定は参考程度。ハマリ判定には使えるが天井狙いには不適。
        # モード別あべし天井:
        #   通常A: 1536あべし / 通常B: 896あべし / 通常C: 576あべし / 天国: 128あべし
        #   設定変更後: 最大1280あべし
        # コイン持ち: 50枚≒31.5G
        'normal_ceiling': 1100,       # 参考値（通常Aの実データ上の最大ハマリ付近。天井判定には不適）
        'normal_ceiling_abeshi': 1536, # 通常Aモードのあべし天井
        'mode_ceilings_abeshi': {     # モード別あべし天井
            'A': 1536, 'B': 896, 'C': 576, 'heaven': 128,
        },
        'reset_ceiling': 600,         # リセット時天井（G数換算。あべし1280≒G数600〜800程度）
        'reset_ceiling_abeshi': 1280, # リセット時あべし天井
        'reset_first_hit_bonus': True, # 朝イチ初当たりに恩恵あり
        'renchain_threshold': 50,     # 連チャン判定: AT間50G以内なら連チャン継続
                                       # 北斗はAT消化が速い。50G以内は連チャン、それ以上は新規初当たり。
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
    'renchain_threshold': 70,   # デフォルト連チャン閾値
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
        'units': ['3185', '3186', '3187'],
        'data_source': 'daidata',
    },
    'seibu_shinjuku_espass_hokuto2': {
        'name': 'エスパス日拓西武新宿駅前店',
        'short_name': 'エスパス西武新宿',
        'hall_id': '100950',
        'machine': 'hokuto2',
        'units': ['3139', '3140', '3141', '3142', '3143', '3144', '3145', '3146', '3147', '3148', '3149', '3150', '3151', '3165', '3166'],
        'data_source': 'daidata',
    },
    'akiba_espass_sbj': {
        'name': 'エスパス日拓秋葉原駅前店',
        'short_name': 'エスパス秋葉原',
        'hall_id': '100928',
        'machine': 'sbj',
        'units': ['2157', '2158', '2159', '2160'],
        'data_source': 'daidata',
    },
    # === 渋谷本館 (hall_id=100930) ===
    'shibuya_honkan_espass_sbj': {
        'name': 'エスパス日拓渋谷本館',
        'short_name': 'エスパス渋谷本館',
        'hall_id': '100930',
        'machine': 'sbj',
        'units': ['3095', '3096', '3097'],
        'data_source': 'daidata',
    },
    'shibuya_honkan_espass_hokuto2': {
        'name': 'エスパス日拓渋谷本館',
        'short_name': 'エスパス渋谷本館',
        'hall_id': '100930',
        'machine': 'hokuto2',
        'units': [str(i) for i in range(2013, 2020)] + [str(i) for i in range(2030, 2038)],  # 2013-2019, 2030-2037
        'data_source': 'daidata',
    },
    # === 北斗転生2 ===
    'shibuya_espass_hokuto2': {
        'name': 'エスパス日拓渋谷新館',
        'short_name': 'エスパス渋谷新館',
        'hall_id': '100860',
        'machine': 'hokuto2',
        'units': [str(i) for i in range(2046, 2068)] + [str(i) for i in range(2233, 2241)],  # 2046-2067, 2233-2240
        'data_source': 'daidata',
    },
    'shinjuku_espass_hokuto2': {
        'name': 'エスパス日拓新宿歌舞伎町店',
        'short_name': 'エスパス歌舞伎町',
        'hall_id': '100949',
        'machine': 'hokuto2',
        # 2026-02-19更新: 台変動を検知。125-128の4台のみ北斗転生2として稼働中。
        'units': ['125', '126', '127', '128'],
        'data_source': 'daidata',
    },
    'akiba_espass_hokuto2': {
        'name': 'エスパス日拓秋葉原駅前店',
        'short_name': 'エスパス秋葉原',
        'hall_id': '100928',
        'machine': 'hokuto2',
        'units': ['2011', '2012', '2013', '2014', '2015', '2016', '2017', '2018', '2019', '2056', '2057', '2058', '2059', '2060', '2061', '2062', '2063', '2064', '2065', '2066', '2067'],
        'data_source': 'daidata',
    },
    'island_akihabara_hokuto2': {
        'name': 'アイランド秋葉原',
        'short_name': 'アイランド秋葉原',
        'hall_id': None,
        'machine': 'hokuto2',
        'units': ['0811', '0812', '0813', '0815', '0816', '0817', '0818', '0820', '0821', '0822', '0823', '0825'],  # 0814,0824別機種 (2026-02-14)
        'data_source': 'papimo',
    },
    # === 追加店舗 (2026-02-14) ===
    'ueno_espass_sbj': {
        'name': 'エスパス日拓上野新館',
        'short_name': 'エスパス上野新館',
        'hall_id': '100196',
        'machine': 'sbj',
        'units': ['3110', '3111', '3112', '3113'],
        'data_source': 'daidata',
    },
    'ueno_honkan_espass_sbj': {
        'name': 'エスパス日拓上野本館',
        'short_name': 'エスパス上野本館',
        'hall_id': '100947',
        'machine': 'sbj',
        'units': ['3125', '3126', '3127'],
        'data_source': 'daidata',
    },
    'takadanobaba_espass_sbj': {
        'name': 'エスパス日拓高田馬場店',
        'short_name': 'エスパス高田馬場',
        'hall_id': '100915',
        'machine': 'sbj',
        'units': ['2060', '2061', '2062'],
        'data_source': 'daidata',
    },
    'akasaka_espass_sbj': {
        'name': 'エスパス日拓赤坂見附店',
        'short_name': 'エスパス赤坂見附',
        'hall_id': '100952',
        'machine': 'sbj',
        'units': ['2039', '2040', '2041'],
        'data_source': 'daidata',
    },
    'shinokubo_espass_sbj': {
        'name': 'エスパス日拓新大久保店',
        'short_name': 'エスパス新大久保',
        'hall_id': '100951',
        'machine': 'sbj',
        'units': ['3141', '3142', '3143', '3144'],
        'data_source': 'daidata',
    },
    'shinkoiwa_espass_sbj': {
        'name': 'エスパス日拓新小岩店',
        'short_name': 'エスパス新小岩',
        'hall_id': '100260',
        'machine': 'sbj',
        'units': ['485', '486'],
        'data_source': 'daidata',
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
        for suffix in ['_sbj', '_hokuto', '_hokuto2']:
            if store_key.endswith(suffix):
                alt_key = store_key[:-len(suffix)]
                store_rankings = RANKINGS.get(alt_key, {})
                if store_rankings:
                    break
    return store_rankings.get(unit_id, {'rank': 'C', 'score': 50, 'note': '未評価'})
