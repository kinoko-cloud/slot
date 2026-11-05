#!/usr/bin/env python3
"""
台番号検証スクリプト

夜間バッチ（daily_collect.py）の後に実行し、
設定ファイルの台番号リストとスクレイピングで実際に見つかった台番号を照合する。

検知するケース:
- 台撤去: config定義にある台番号がスクレイピング結果に存在しない
- 新台追加: スクレイピング結果にconfig未定義の台番号がある
- 台数変動: 設置台数が増減した
- 機種名不一致: スクレイピングで取れた機種名と期待値が異なる

出力: data/alerts/ にJSON形式でアラートを保存
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.rankings import STORES, MACHINES

JST = timezone(timedelta(hours=9))
ALERTS_DIR = PROJECT_ROOT / 'data' / 'alerts'


def verify_units_from_daily(daily_data: dict) -> list:
    """デイリー収集結果から台番号を検証する

    Args:
        daily_data: daily_collect.pyの出力結果（stores辞書を含む）

    Returns:
        アラートのリスト
    """
    alerts = []
    checked_at = datetime.now(JST).isoformat()

    stores_data = daily_data.get('stores', {})

    for result_key, store_data in stores_data.items():
        # result_keyは "shibuya_espass_sbj" のような形式
        # config/rankings.pyのSTORESキーと照合
        store_config = STORES.get(result_key)
        if not store_config:
            # 旧形式キーの場合は対応するSTORESキーを探す
            store_config = _find_store_config(result_key, store_data.get('machine_key'))
            if not store_config:
                continue

        config_units = set(store_config.get('units', []))
        machine_key = store_config.get('machine', '')
        machine_name = MACHINES.get(machine_key, {}).get('name', '')
        store_name = store_config.get('name', result_key)

        # スクレイピングで実際に取得した台番号
        scraped_units = set()
        scraped_machine_names = set()
        for unit in store_data.get('units', []):
            unit_id = str(unit.get('unit_id', ''))
            if unit_id:
                scraped_units.add(unit_id)
            m_name = unit.get('machine_name', '')
            if m_name:
                scraped_machine_names.add(m_name)

        if not scraped_units:
            # データが取れなかった場合はスキップ（スクレイピングエラーの可能性）
            continue

        # 1. 消えた台番号（台撤去 or 台移動）
        missing_units = config_units - scraped_units
        if missing_units:
            alerts.append({
                'type': 'unit_missing',
                'severity': 'warning',
                'store_key': result_key,
                'store_name': store_name,
                'machine_key': machine_key,
                'units': sorted(missing_units),
                'message': f'{store_name}: 台番号 {", ".join(sorted(missing_units))} が見つかりません（台撤去または台移動の可能性）',
                'checked_at': checked_at,
            })

        # 2. 新しい台番号（台追加 or 台移動先）
        new_units = scraped_units - config_units
        if new_units:
            alerts.append({
                'type': 'unit_new',
                'severity': 'info',
                'store_key': result_key,
                'store_name': store_name,
                'machine_key': machine_key,
                'units': sorted(new_units),
                'message': f'{store_name}: 新しい台番号 {", ".join(sorted(new_units))} を検出（新台追加または台移動の可能性）',
                'checked_at': checked_at,
            })

        # 3. 台数変動
        if len(scraped_units) != len(config_units):
            diff = len(scraped_units) - len(config_units)
            direction = '増加' if diff > 0 else '減少'
            alerts.append({
                'type': 'unit_count_change',
                'severity': 'warning' if diff < 0 else 'info',
                'store_key': result_key,
                'store_name': store_name,
                'machine_key': machine_key,
                'config_count': len(config_units),
                'actual_count': len(scraped_units),
                'diff': diff,
                'message': f'{store_name}: 台数{direction}（設定{len(config_units)}台 → 実際{len(scraped_units)}台）',
                'checked_at': checked_at,
            })

        # 4. 同時に消えた台と新しい台がある場合→台移動の可能性
        if missing_units and new_units:
            alerts.append({
                'type': 'unit_move_suspected',
                'severity': 'warning',
                'store_key': result_key,
                'store_name': store_name,
                'machine_key': machine_key,
                'missing': sorted(missing_units),
                'new': sorted(new_units),
                'message': f'{store_name}: 台移動の可能性 — 消失: {", ".join(sorted(missing_units))} / 新規: {", ".join(sorted(new_units))}',
                'checked_at': checked_at,
            })

    return alerts


def verify_units_from_availability(availability_data: dict) -> list:
    """availability.json（リアルタイムデータ）から台番号を検証する

    リアルタイム取得時にも簡易チェックできるようにする。

    Args:
        availability_data: availability.jsonの内容

    Returns:
        アラートのリスト
    """
    alerts = []
    checked_at = datetime.now(JST).isoformat()

    stores_data = availability_data.get('stores', {})

    for store_key, store_data in stores_data.items():
        store_config = STORES.get(store_key)
        if not store_config:
            continue

        config_units = set(store_config.get('units', []))
        store_name = store_config.get('name', store_key)

        # availability.jsonから台番号を取得
        scraped_units = set()
        for unit in store_data.get('units', []):
            unit_id = str(unit.get('unit_id', ''))
            if unit_id:
                scraped_units.add(unit_id)

        # empty/playing リストからも取得
        for u in store_data.get('empty', []):
            scraped_units.add(str(u))
        for u in store_data.get('playing', []):
            scraped_units.add(str(u))

        if not scraped_units:
            continue

        missing = config_units - scraped_units
        new = scraped_units - config_units

        if missing:
            alerts.append({
                'type': 'unit_missing',
                'severity': 'warning',
                'store_key': store_key,
                'store_name': store_name,
                'units': sorted(missing),
                'message': f'{store_name}: 台番号 {", ".join(sorted(missing))} が見つかりません',
                'checked_at': checked_at,
            })

        if new:
            alerts.append({
                'type': 'unit_new',
                'severity': 'info',
                'store_key': store_key,
                'store_name': store_name,
                'units': sorted(new),
                'message': f'{store_name}: 新しい台番号 {", ".join(sorted(new))} を検出',
                'checked_at': checked_at,
            })

    return alerts


def save_alerts(alerts: list, source: str = 'daily') -> Path:
    """アラートをJSONファイルに保存する

    Args:
        alerts: アラートのリスト
        source: アラート元（'daily' or 'availability'）

    Returns:
        保存先パス
    """
    ALERTS_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now(JST)
    filename = f'alerts_{source}_{now.strftime("%Y%m%d_%H%M")}.json'
    output_path = ALERTS_DIR / filename

    alert_data = {
        'generated_at': now.isoformat(),
        'source': source,
        'alert_count': len(alerts),
        'alerts': alerts,
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(alert_data, f, ensure_ascii=False, indent=2)

    return output_path


def load_latest_alerts() -> dict:
    """最新のアラートファイルを読み込む

    Returns:
        アラートデータ（なければ空辞書）
    """
    if not ALERTS_DIR.exists():
        return {}

    alert_files = sorted(ALERTS_DIR.glob('alerts_*.json'), reverse=True)
    if not alert_files:
        return {}

    with open(alert_files[0], 'r', encoding='utf-8') as f:
        return json.load(f)


def get_active_alerts() -> list:
    """現在有効なアラートを取得する

    24時間以内のwarning以上のアラートを返す。

    Returns:
        有効なアラートリスト
    """
    latest = load_latest_alerts()
    if not latest:
        return []

    # 24時間以内のアラートのみ
    generated_at = latest.get('generated_at', '')
    if generated_at:
        try:
            gen_time = datetime.fromisoformat(generated_at)
            now = datetime.now(JST)
            if (now - gen_time).total_seconds() > 86400:
                return []
        except:
            pass

    return [a for a in latest.get('alerts', []) if a.get('severity') in ('warning', 'critical')]


def get_unit_status(store_key: str, unit_id: str) -> dict:
    """特定の台のアラート状態を取得する

    Returns:
        {'status': 'normal'/'missing'/'new'/'moved', 'message': str}
    """
    active = get_active_alerts()

    for alert in active:
        if alert.get('store_key') != store_key:
            continue

        if alert.get('type') == 'unit_move_suspected':
            if unit_id in alert.get('missing', []):
                return {'status': 'moved', 'message': '台移動（消失）'}
            if unit_id in alert.get('new', []):
                return {'status': 'moved', 'message': '台移動（新設）'}

        if alert.get('type') == 'unit_missing' and unit_id in alert.get('units', []):
            return {'status': 'missing', 'message': '台撤去の可能性'}

        if alert.get('type') == 'unit_new' and unit_id in alert.get('units', []):
            return {'status': 'new', 'message': '新台'}

    return {'status': 'normal', 'message': ''}


def _find_store_config(result_key: str, machine_key: str = None):
    """daily_collectの結果キーからSTORES設定を探す"""
    # まず直接マッチ
    if result_key in STORES:
        return STORES[result_key]

    # result_keyが "shibuya_espass_sbj" の場合はそのまま
    # "shibuya_espass" + machine_key = "sbj" → "shibuya_espass_sbj"
    if machine_key:
        combined = f"{result_key}_{machine_key}"
        if combined in STORES:
            return STORES[combined]

    return None


def print_report(alerts: list):
    """アラートをコンソールに出力"""
    if not alerts:
        print('✓ 台番号の異常なし')
        return

    print(f'\n{"="*50}')
    print(f'台番号検証結果: {len(alerts)}件のアラート')
    print(f'{"="*50}')

    for alert in alerts:
        severity = alert.get('severity', 'info')
        icon = '🔴' if severity == 'critical' else '🟡' if severity == 'warning' else '🔵'
        print(f'\n{icon} [{alert["type"]}] {alert["message"]}')

    print()


def verify_data_integrity(availability_data: dict) -> list:
    """availability.jsonのデータ品質を検証する

    チェック項目:
    - 全期待店舗がデータに含まれているか
    - 各台のART/total_startが0でないか（スクレイピング失敗検知）
    - 必須フィールドが欠損していないか
    - データの鮮度

    Returns:
        問題のリスト
    """
    issues = []
    checked_at = datetime.now(JST).isoformat()

    stores_data = availability_data.get('stores', {})

    # 期待される店舗キー（availability.jsonに含まれるべき）
    EXPECTED_STORES = {
        'island_akihabara_sbj': {'name': 'アイランド秋葉原', 'min_units': 14},
        'shibuya_espass_sbj': {'name': '渋谷エスパス新館', 'min_units': 3},
        'shinjuku_espass_sbj': {'name': '新宿エスパス歌舞伎町', 'min_units': 4},
        'akiba_espass_sbj': {'name': '秋葉原エスパス駅前', 'min_units': 4},
        'seibu_shinjuku_espass_sbj': {'name': '西武新宿駅前エスパス', 'min_units': 7},
    }

    # 1. 店舗の存在チェック
    for store_key, expected in EXPECTED_STORES.items():
        if store_key not in stores_data:
            issues.append({
                'type': 'store_missing',
                'severity': 'critical',
                'store_key': store_key,
                'store_name': expected['name'],
                'message': f'{expected["name"]}: availability.jsonにデータなし',
                'checked_at': checked_at,
            })
            continue

        store = stores_data[store_key]
        units = store.get('units', [])

        # 2. 台数チェック
        if len(units) < expected['min_units']:
            issues.append({
                'type': 'units_insufficient',
                'severity': 'warning',
                'store_key': store_key,
                'store_name': expected['name'],
                'expected': expected['min_units'],
                'actual': len(units),
                'message': f'{expected["name"]}: 台数不足（期待{expected["min_units"]}台、実際{len(units)}台）',
                'checked_at': checked_at,
            })

        # 3. 全台ART=0チェック（スクレイピング失敗の兆候）
        art_values = [u.get('art', 0) for u in units]
        total_start_values = [u.get('total_start', 0) for u in units]

        # 営業中で全台ART=0は異常（閉店後は正常）
        now = datetime.now(JST)
        is_business_hours = 10 <= now.hour < 23

        if is_business_hours and now.hour >= 12:
            # 12時以降で全台ART 0は異常
            if units and all(a == 0 for a in art_values):
                issues.append({
                    'type': 'all_art_zero',
                    'severity': 'critical',
                    'store_key': store_key,
                    'store_name': expected['name'],
                    'unit_count': len(units),
                    'message': f'{expected["name"]}: 全{len(units)}台のART=0（データ取得失敗の可能性）',
                    'checked_at': checked_at,
                })

        # 4. 必須フィールド欠損チェック
        required_fields = ['unit_id', 'art']
        for unit in units:
            missing_fields = [f for f in required_fields if f not in unit]
            if missing_fields:
                issues.append({
                    'type': 'field_missing',
                    'severity': 'warning',
                    'store_key': store_key,
                    'store_name': expected['name'],
                    'unit_id': unit.get('unit_id', '?'),
                    'missing_fields': missing_fields,
                    'message': f'{expected["name"]} {unit.get("unit_id", "?")}番: フィールド欠損 {", ".join(missing_fields)}',
                    'checked_at': checked_at,
                })

        # 5. 個別台のデータ異常チェック
        for unit in units:
            uid = unit.get('unit_id', '?')
            art = unit.get('art', 0)
            total = unit.get('total_start', 0)

            # total_startが大きいのにART=0は異常
            if total > 2000 and art == 0:
                issues.append({
                    'type': 'art_zero_with_games',
                    'severity': 'warning',
                    'store_key': store_key,
                    'store_name': expected['name'],
                    'unit_id': uid,
                    'total_start': total,
                    'message': f'{expected["name"]} {uid}番: {total:,}G消化済みなのにART=0',
                    'checked_at': checked_at,
                })

    # 6. データ鮮度チェック
    fetched_at = availability_data.get('fetched_at', '')
    if fetched_at:
        try:
            fetch_time = datetime.fromisoformat(fetched_at)
            now = datetime.now(JST)
            age_minutes = (now - fetch_time).total_seconds() / 60

            if age_minutes > 60:
                issues.append({
                    'type': 'data_stale',
                    'severity': 'warning',
                    'age_minutes': int(age_minutes),
                    'fetched_at': fetched_at,
                    'message': f'データが{int(age_minutes)}分前のもの（60分超過）',
                    'checked_at': checked_at,
                })
        except Exception:
            pass

    return issues


def print_integrity_report(issues: list):
    """データ整合性レポートをコンソール出力"""
    if not issues:
        print('✓ データ整合性: 問題なし')
        return

    critical = [i for i in issues if i.get('severity') == 'critical']
    warnings = [i for i in issues if i.get('severity') == 'warning']

    print(f'\n{"="*50}')
    print(f'データ整合性チェック: {len(issues)}件の問題')
    if critical:
        print(f'  🔴 重大: {len(critical)}件')
    if warnings:
        print(f'  🟡 警告: {len(warnings)}件')
    print(f'{"="*50}')

    for issue in issues:
        severity = issue.get('severity', 'info')
        icon = '🔴' if severity == 'critical' else '🟡' if severity == 'warning' else '🔵'
        print(f'{icon} [{issue["type"]}] {issue["message"]}')

    print()
    return len(critical) > 0


def main():
    """スタンドアロン実行: availability.jsonから検証"""
    import argparse
    parser = argparse.ArgumentParser(description='台番号検証')
    parser.add_argument('--source', choices=['availability', 'daily', 'integrity'], default='availability',
                        help='検証データソース')
    parser.add_argument('--daily-file', type=str, help='デイリーファイルパス（--source daily時）')
    args = parser.parse_args()

    avail_path = PROJECT_ROOT / 'data' / 'availability.json'

    if args.source == 'integrity':
        if not avail_path.exists():
            print('availability.json が見つかりません')
            return

        with open(avail_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        issues = verify_data_integrity(data)
        has_critical = print_integrity_report(issues)

        if has_critical:
            print('⚠ 重大な問題が見つかりました')
            sys.exit(1)
        return

    if args.source == 'availability':
        if not avail_path.exists():
            print('availability.json が見つかりません')
            return

        with open(avail_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        alerts = verify_units_from_availability(data)

    elif args.source == 'daily':
        if args.daily_file:
            daily_path = Path(args.daily_file)
        else:
            # 最新のデイリーファイルを探す
            daily_dir = PROJECT_ROOT / 'data' / 'daily'
            daily_files = sorted(daily_dir.glob('daily_*.json'), reverse=True)
            if not daily_files:
                print('デイリーファイルが見つかりません')
                return
            daily_path = daily_files[0]

        with open(daily_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        alerts = verify_units_from_daily(data)

    print_report(alerts)

    if alerts:
        save_path = save_alerts(alerts, source=args.source)
        print(f'アラート保存: {save_path}')


if __name__ == '__main__':
    main()
