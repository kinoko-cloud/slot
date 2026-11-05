#!/usr/bin/env python3
"""
店舗・機種設定

データソース:
- daidata: 台データオンライン (daidata.goraggio.com)
- papimo: PAPIMO-NET (papimo.jp)
"""

# =============================================================================
# store_key マッピング（一元管理）
# =============================================================================
# availability.jsonやrecommenderでは `_hokuto` を使うが、
# 蓄積DBでは `_hokuto2` として保存する。
# 全モジュールでこの関数を使うことで、マッピングの分散を防ぐ。

def resolve_history_store_key(store_key: str) -> str:
    """store_keyを蓄積DB用のキーに変換する
    
    Args:
        store_key: 元のstore_key (例: 'shinjuku_espass_hokuto2')
    
    Returns:
        蓄積DB用のstore_key (例: 'shinjuku_espass_hokuto2')
    """
    if '_hokuto' in store_key and '_hokuto2' not in store_key:
        return store_key.replace('_hokuto', '_hokuto2')
    return store_key


def get_machine_key_from_store(store_key: str) -> str:
    """store_keyから機種キーを取得する

    Args:
        store_key: 店舗キー

    Returns:
        機種キー（現行機種は config/rankings.py の STORES を参照。
        旧sbj/hokuto2店舗キーなど STORES に存在しないものは従来の推定ロジックにフォールバック）
    """
    try:
        from config.rankings import STORES
        if store_key in STORES:
            return STORES[store_key]['machine']
    except ImportError:
        pass
    if 'hokuto' in store_key:
        return 'hokuto2'
    return 'sbj'


# 機種情報
MACHINES = {
    'sbj': {
        'name': 'Lスーパーブラックジャック',
        'short_name': 'SBJ',
        'papimo_id': '225010000',
        'ceiling': 999,  # 通常天井
        'ceiling_reset': 666,  # リセット時天井
    },
    'hokuto2': {
        'name': 'L北斗の拳 転生の章2',
        'short_name': '北斗転生2',
        'papimo_id': '225110007',
        'ceiling_abeshi': 1536,  # モードA天井（あべし）
        'ceiling_reset_abeshi': 1280,  # リセット時最大あべし
    },
}

# 店舗設定（台データオンライン）
DAIDATA_STORES = {
    'shibuya_espass': {
        'hall_id': '100860',
        'name': 'エスパス渋谷',
        'machines': {
            'sbj': ['3012', '3013', '3014'],
            'hokuto2': ['2052', '2053', '2054', '2055', '2056', '2057', '2058', '2059', '2060', '2061', '2062', '2063', '2064', '2065', '2066', '2067', '2068', '2069', '2070', '2071', '2072', '2073', '2074', '2246', '2247', '2248', '2249', '2250', '2251', '2252', '2253'],
        },
    },
    'shinjuku_espass': {
        'hall_id': '100949',
        'name': '新宿エスパス歌舞伎町',
        'machines': {
            'sbj': ['682', '683', '684', '685'],
            'hokuto2': ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12', '13', '14', '15', '16', '30', '31', '32', '33', '34', '35', '36', '37', '38', '39', '40', '41', '42', '43', '57', '58', '59', '60', '61', '62'],
        },
    },
    'akiba_espass': {
        'hall_id': '100928',
        'name': '秋葉原エスパス駅前',
        'machines': {
            'sbj': ['2157', '2158', '2159', '2160'],
            'hokuto2': ['2011', '2012', '2013', '2014', '2015', '2016', '2019', '2061', '2062', '2063', '2066', '2067'],
        },
    },
    'maruhan_shinjuku': {
        'hall_id': '203505',
        'name': 'マルハン新宿東宝ビル',
        'machines': {
            'sbj': [],  # 要確認
            'hokuto2': [],
        },
    },
    'rakuen_shibuya': {
        'hall_id': '203478',
        'name': '楽園渋谷駅前',
        'machines': {
            'sbj': [],  # 要確認
            'hokuto2': [],
        },
    },
    'seibu_shinjuku_espass': {
        'hall_id': '100950',
        'name': '西武新宿駅前エスパス',
        'machines': {
            'sbj': ['3201', '3202', '3203'],
            'hokuto2': ['3140', '3141', '3142', '3143', '3144', '3145', '3146', '3147'],
        },
    },
    # 2026-03-31 閉店 - 過去データは history/ に保持
    # 'shibuya_honkan_espass': {
    #     'hall_id': '100930',
    #     'name': 'エスパス日拓渋谷本館',
    #     'machines': {
    #         'sbj': ['3095', '3096', '3097'],
    #         'hokuto2': ['2013', '2014', '2015', '2016', '2017', '2018', '2019', '2030', '2031', '2032', '2033', '2034', '2035', '2036', '2037'],
    #     },
    # },
    'akasaka_espass': {
        'hall_id': '100952',
        'name': 'エスパス日拓赤坂見附駅前新館',
        'machines': {
            'sbj': ['2039', '2040', '2041'],
            'hokuto2': ['2107', '2108', '2109', '2110', '2111', '2112', '2113', '2114', '2115', '2116', '2117', '2118', '2119', '2120', '2121', '2122'],
        },
    },
    'ueno_espass': {
        'hall_id': '100196',
        'name': 'エスパス日拓上野新館',
        'machines': {
            'sbj': ['2070', '2071', '2072', '2280'],
            'hokuto2': ['2207', '2208', '2221', '2222', '2223', '2224', '2225', '2226', '2227', '2228', '2229', '2230', '2231', '2232', '2233', '2270', '2271', '2272'],
        },
    },
    'ueno_honkan_espass': {
        'hall_id': '100947',
        'name': 'エスパス日拓上野本館',
        'machines': {
            'sbj': ['3125', '3126', '3127'],
            'hokuto2': ['3001', '3002', '3003', '3004', '3005', '3006', '3007', '3008', '3009', '3010', '3011', '3012', '3013', '3014', '3184'],
        },
    },
    'takadanobaba_espass': {
        'hall_id': '100915',
        'name': 'エスパス日拓高田馬場本店',
        'machines': {
            'sbj': ['2067', '2068'],
            'hokuto2': ['2110', '2111', '2112', '2170', '2171', '2172', '2192'],
        },
    },
    'shinokubo_espass': {
        'hall_id': '100951',
        'name': 'エスパス日拓新大久保駅前店',
        'machines': {
            'sbj': ['2175', '2176', '2177', '2178'],
            'hokuto2': ['2125', '2126', '2127', '2128', '2129', '2130', '2131', '2132', '2133', '2193'],
        },
    },
    'shinkoiwa_espass': {
        'hall_id': '100260',
        'name': 'エスパス１３００新小岩北口駅前店',
        'machines': {
            'sbj': ['2049', '2050'],
            'hokuto2': ['2167', '2168', '2169', '2170', '2171', '2172', '2173', '2222', '2223', '2224', '2225', '2226', '2227', '2228', '2229', '2230', '2231', '2232', '2233', '2234', '2235', '2236'],
        },
    },
}

# 店舗設定（PAPIMO-NET）
PAPIMO_STORES = {
    'island_akihabara': {
        'hall_id': '00031715',
        'name': 'アイランド秋葉原店',
        'machines': {
            'sbj': [
                '1015', '1016', '1017', '1018', '1020', '1021', '1022', '1023',
                '1025', '1026', '1027', '1028', '1030', '1031',
            ],
            'hokuto2': [
                # 2026-03-30更新: 12台→5台に減台
                '0700', '0701', '0702', '0703', '0705',
            ],
        },
    },
}


def get_store_units(store_key: str, machine_key: str) -> list:
    """店舗と機種を指定して台番号リストを取得"""
    if store_key in DAIDATA_STORES:
        store = DAIDATA_STORES[store_key]
    elif store_key in PAPIMO_STORES:
        store = PAPIMO_STORES[store_key]
    else:
        return []

    return store.get('machines', {}).get(machine_key, [])


def get_all_units_by_machine(machine_key: str) -> dict:
    """機種を指定して全店舗の台番号を取得"""
    result = {}

    for store_key, store in DAIDATA_STORES.items():
        units = store.get('machines', {}).get(machine_key, [])
        if units:
            result[store_key] = {
                'source': 'daidata',
                'hall_id': store['hall_id'],
                'name': store['name'],
                'units': units,
            }

    for store_key, store in PAPIMO_STORES.items():
        units = store.get('machines', {}).get(machine_key, [])
        if units:
            result[store_key] = {
                'source': 'papimo',
                'hall_id': store['hall_id'],
                'name': store['name'],
                'units': units,
            }

    return result


if __name__ == '__main__':
    # テスト表示
    print("=== SBJ 全店舗 ===")
    for store_key, data in get_all_units_by_machine('sbj').items():
        print(f"{data['name']}: {len(data['units'])}台 ({data['source']})")

    print("\n=== 北斗転生2 全店舗 ===")
    for store_key, data in get_all_units_by_machine('hokuto2').items():
        print(f"{data['name']}: {len(data['units'])}台 ({data['source']})")
