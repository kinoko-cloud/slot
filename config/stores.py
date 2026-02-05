#!/usr/bin/env python3
"""
店舗・機種設定

データソース:
- daidata: 台データオンライン (daidata.goraggio.com)
- papimo: PAPIMO-NET (papimo.jp)
"""

# 機種情報
MACHINES = {
    'sbj': {
        'name': 'Lスーパーブラックジャック',
        'short_name': 'SBJ',
        'papimo_id': '225010000',
        'ceiling': 999,  # 通常天井
        'ceiling_reset': 666,  # リセット時天井
    },
    'hokuto_tensei2': {
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
        'name': '渋谷エスパス新館',
        'machines': {
            'sbj': ['3011', '3012', '3013'],
            'hokuto_tensei2': [
                '2046', '2047', '2048', '2049', '2050', '2051', '2052', '2053',
                '2054', '2055', '2056', '2057', '2058', '2059', '2060', '2061',
                '2062', '2063', '2064', '2065', '2066', '2067',
                '2233', '2234', '2235', '2236', '2237', '2238', '2239', '2240',
            ],
        },
    },
    'shinjuku_espass': {
        'hall_id': '100949',
        'name': '新宿エスパス歌舞伎町',
        'machines': {
            'sbj': ['682', '683', '684', '685'],
            'hokuto_tensei2': [
                '1', '2', '3', '4', '5', '6', '7', '8', '9', '10',
                '11', '12', '13', '14', '15', '16', '17', '18', '19', '20',
                '21', '22', '23', '24', '25', '26', '27', '28', '29', '30',
                '31', '32', '33', '34', '35', '36', '37',
                '125', '126', '127', '128',
            ],
        },
    },
    'akiba_espass': {
        'hall_id': '100928',
        'name': '秋葉原エスパス駅前',
        'machines': {
            'sbj': ['2158', '2159', '2160', '2161'],
            'hokuto_tensei2': [
                '2011', '2012', '2013', '2014', '2015', '2016', '2017', '2018', '2019',
                '2056', '2057', '2058', '2059', '2060', '2061', '2062', '2063',
                '2064', '2065', '2066', '2067', '2068',
            ],
        },
    },
    'maruhan_shinjuku': {
        'hall_id': '203505',
        'name': 'マルハン新宿東宝ビル',
        'machines': {
            'sbj': [],  # 要確認
            'hokuto_tensei2': [],
        },
    },
    'rakuen_shibuya': {
        'hall_id': '203478',
        'name': '楽園渋谷駅前',
        'machines': {
            'sbj': [],  # 要確認
            'hokuto_tensei2': [],
        },
    },
    'seibu_shinjuku_espass': {
        'hall_id': '100950',
        'name': '西武新宿駅前エスパス',
        'machines': {
            'sbj': ['4168'],  # 減台により4168のみ稼働（3185,3186,3187は撤去、過去データは残す）
            'hokuto_tensei2': ['3138', '3139', '3140', '3141', '3142', '3143', '3144', '3145', '3146', '3147', '3148', '3149', '3150', '3151', '3165', '3166'],
        },
    },
    'shibuya_honkan_espass': {
        'hall_id': '100930',
        'name': 'エスパス日拓渋谷本館',
        'machines': {
            'sbj': ['3095', '3096', '3097'],
            'hokuto_tensei2': [str(i) for i in range(2013, 2020)] + [str(i) for i in range(2030, 2038)],
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
            'hokuto_tensei2': [
                # 2026-02-02更新: 0731-0738,0750-0757 → 0811-0818,0820-0825 (16台→14台に減台)
                '0811', '0812', '0813', '0814', '0815', '0816', '0817', '0818',
                '0820', '0821', '0822', '0823', '0824', '0825',
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
    for store_key, data in get_all_units_by_machine('hokuto_tensei2').items():
        print(f"{data['name']}: {len(data['units'])}台 ({data['source']})")
