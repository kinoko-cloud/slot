#!/usr/bin/env python3
"""
GAS経由でdaidataのunit_listデータを取得してavailability.jsonを更新する

フロー:
  1. GASエンドポイントを呼び出してエスパス全店舗データを取得
  2. 既存のavailability.jsonとマージ（history/diff_medalsは保持）
  3. data/availability.jsonを更新

引数:
  --dry-run: ファイルを更新せず結果を表示
  --history: sync_realtime_to_history.pyも実行する（日次バッチ用）
"""
import json
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
import argparse

PROJECT_ROOT = Path(__file__).parent.parent
AVAILABILITY_JSON = PROJECT_ROOT / 'data' / 'availability.json'
JST = timezone(timedelta(hours=9))

GAS_URL = (
    'https://script.google.com/macros/s/'
    'AKfycbxHxySh9QZlooJ9wi5XG_XytFzBGPJl2W3--GBdKApfEdeEiw2B9BlCKMilKP2JuR0n'
    '/exec?action=scrape_daidata'
)


def fetch_from_gas(timeout=120):
    """GASエンドポイントからデータを取得してパース"""
    print(f"GASエンドポイントに接続中...")
    req = urllib.request.Request(
        GAS_URL,
        headers={'User-Agent': 'Mozilla/5.0 (compatible; slot-bot/1.0)'}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    return data


def load_existing_availability():
    """既存のavailability.jsonを読み込む"""
    if not AVAILABILITY_JSON.exists():
        return {}
    try:
        with open(AVAILABILITY_JSON, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"警告: availability.json読み込み失敗: {e}")
        return {}


def get_store_name(store_key, existing_stores):
    """既存データからストア名を取得（なければfallback）"""
    if store_key in existing_stores:
        return existing_stores[store_key].get('name', store_key)
    # fallback map
    names = {
        'shinjuku_espass_tokyoghoul': 'エスパス日拓新宿歌舞伎町店',
        'akiba_espass_tokyoghoul': 'エスパス日拓秋葉原駅前店',
        'seibu_shinjuku_espass_tokyoghoul': 'エスパス日拓西武新宿駅前店',
        'shibuya_espass_tokyoghoul': 'エスパス日拓渋谷新館',
    }
    return names.get(store_key, store_key)


def merge_availability(gas_data, existing):
    """GASデータと既存availability.jsonをマージ"""
    now = datetime.now(JST)
    today = now.strftime('%Y-%m-%d')
    fetched_at = gas_data.get('fetched_at', now.isoformat())

    existing_stores = existing.get('stores', {})

    new_stores = {}
    for store_key, store_data in gas_data.get('stores', {}).items():
        if store_data.get('error'):
            # エラーがあれば既存データをそのまま保持
            if store_key in existing_stores:
                new_stores[store_key] = existing_stores[store_key]
            continue

        gas_units = store_data.get('units', [])
        existing_units_map = {
            u['unit_id']: u
            for u in existing_stores.get(store_key, {}).get('units', [])
        }

        merged_units = []
        for gu in gas_units:
            uid = gu['unit_id']
            existing_unit = existing_units_map.get(uid, {})

            unit = {
                'unit_id': uid,
                'art': gu.get('art', 0),
                'bb': gu.get('bb', 0),
                'rb': gu.get('rb', 0),
                'total_start': gu.get('total_start', 0),
                'games': gu.get('total_start', 0),
                'final_start': gu.get('final_start', 0),
                'max_medals': gu.get('max_medals', 0),
                'availability': '遊技中' if gu.get('playing') else '空き',
                'date': today,
            }

            # 既存のhistoryとdiff_medalsを引き継ぐ
            if existing_unit.get('date') == today:
                if existing_unit.get('history'):
                    unit['history'] = existing_unit['history']
                if existing_unit.get('diff_medals') is not None:
                    unit['diff_medals'] = existing_unit['diff_medals']
                if existing_unit.get('today_history'):
                    unit['today_history'] = existing_unit['today_history']

            merged_units.append(unit)

        new_stores[store_key] = {
            'name': get_store_name(store_key, existing_stores),
            'units': merged_units,
        }

    # daidataに含まれない店舗（island_akihabaraなど）は既存データを保持
    for store_key, store_data in existing_stores.items():
        if store_key not in new_stores:
            new_stores[store_key] = store_data

    return {
        'last_updated': now.isoformat(),
        'fetched_at': fetched_at,
        'stores': new_stores,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='ファイルを更新しない')
    parser.add_argument('--history', action='store_true', help='sync_realtime_to_history.pyも実行')
    args = parser.parse_args()

    # GASからデータ取得
    try:
        gas_data = fetch_from_gas()
    except Exception as e:
        print(f"エラー: GAS取得失敗: {e}")
        sys.exit(1)

    # エラー確認
    errors = gas_data.get('errors', [])
    if errors:
        print(f"警告: {len(errors)}件のエラー:")
        for err in errors:
            print(f"  - {err}")

    stores = gas_data.get('stores', {})
    total_units = sum(len(s.get('units', [])) for s in stores.values() if not s.get('error'))
    print(f"取得完了: {len(stores)}店舗, {total_units}台")

    # 遊技中台数サマリー
    for sk, sd in sorted(stores.items()):
        if sd.get('error'):
            print(f"  ⚠️ {sk}: {sd['error']}")
        else:
            units = sd.get('units', [])
            playing = sum(1 for u in units if u.get('playing'))
            print(f"  ✅ {sk}: {len(units)}台 ({playing}遊技中)")

    # 既存データとマージ
    existing = load_existing_availability()
    merged = merge_availability(gas_data, existing)

    if args.dry_run:
        print("\n[DRY RUN] 更新内容プレビュー:")
        for sk, sd in merged['stores'].items():
            if 'units' in sd:
                print(f"  {sk}: {len(sd['units'])}台")
        return

    # ファイル保存
    with open(AVAILABILITY_JSON, 'w', encoding='utf-8') as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"\ndata/availability.json 更新完了 ({total_units}台)")

    # history同期（オプション）
    if args.history:
        import subprocess
        print("\nhistory同期を実行...")
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / 'scripts' / 'sync_realtime_to_history.py')],
            cwd=str(PROJECT_ROOT),
            capture_output=False
        )
        if result.returncode != 0:
            print(f"警告: history同期が終了コード {result.returncode} で終了")


if __name__ == '__main__':
    main()
