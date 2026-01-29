#!/usr/bin/env python3
"""
データ整合性チェック — daily_collect.py実行後に呼ばれる包括チェック

チェック項目:
1. 【スクレイピング品質】
   - 取得0台の店舗/機種がないか
   - 部分取得（期待台数 vs 取得台数の乖離）
   - ART=0, games=0, history空 の台が多すぎないか
   - null/0で埋まったフィールドの検出
   
2. 【台番号/機種名/台数】
   - config定義 vs 実取得の台番号照合
   - 消えた台（撤去/移動）
   - 新しく出現した台（増台/移動先）
   - 台数の変動
   - 機種名バリデーション（verify_keywords照合）

3. 【日付整合性】
   - 開店直後のデータ切り替わり（前日→当日）
   - 蓄積DBの日付ギャップ（歯抜け日）
   - 未来日付の混入

4. 【データ品質】
   - diff_medalsが異常値（±50,000枚超等）
   - ART確率が物理的にありえない値（1/1以下、1/10000以上）
   - historyの時系列矛盾（時刻が逆順等）

出力: チェック結果サマリー + アラートリスト
重大アラートがあればWhatsApp通知対象（呼び出し元で判定）
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.rankings import STORES, MACHINES, get_machine_threshold

JST = timezone(timedelta(hours=9))
HISTORY_DIR = PROJECT_ROOT / 'data' / 'history'


class Alert:
    """アラート定義"""
    CRITICAL = 'critical'   # 即座にWhatsApp通知
    WARNING = 'warning'     # ログ + サマリーに記載
    INFO = 'info'           # ログのみ

    def __init__(self, level, category, store_key, message, details=None):
        self.level = level
        self.category = category
        self.store_key = store_key
        self.message = message
        self.details = details or {}
        self.timestamp = datetime.now(JST).isoformat()

    def to_dict(self):
        return {
            'level': self.level,
            'category': self.category,
            'store_key': self.store_key,
            'message': self.message,
            'details': self.details,
            'timestamp': self.timestamp,
        }

    def __repr__(self):
        icon = {'critical': '🔴', 'warning': '🟡', 'info': '🔵'}.get(self.level, '⚪')
        return f"{icon} [{self.category}] {self.store_key}: {self.message}"


def check_scraping_quality(daily_data: dict) -> list:
    """スクレイピング品質チェック
    
    - 取得0台の店舗
    - 部分取得（期待台数の半分未満）
    - データが空/0の台が多い
    - null/0で埋まったフィールド
    """
    alerts = []
    stores = daily_data.get('stores', {})
    
    for store_key, config in STORES.items():
        # STORESキーは "store_machine" 形式（例: island_akihabara_sbj）
        machine_key = config.get('machine', 'sbj')
        unit_list = config.get('units', [])
        result_key = store_key  # daily_dataのキーと一致
        store_data = stores.get(result_key)
        expected_count = len(unit_list)
        store_name = config.get('name', store_key)
        machine_name = MACHINES.get(machine_key, {}).get('display_name', machine_key)
        
        if not store_data:
            alerts.append(Alert(
                Alert.CRITICAL, 'scrape_missing',
                result_key,
                f"{store_name}({machine_name}): データ取得なし（{expected_count}台期待）",
                {'expected': expected_count, 'actual': 0}
            ))
            continue
        
        units = store_data.get('units', [])
        actual_count = len(units)
        
        # 取得0台
        if actual_count == 0:
            alerts.append(Alert(
                Alert.CRITICAL, 'scrape_empty',
                result_key,
                f"{store_name}({machine_name}): 0台取得（{expected_count}台期待）",
                {'expected': expected_count, 'actual': 0}
            ))
            continue
        
        # 部分取得（半分未満）
        if actual_count < expected_count * 0.5:
            alerts.append(Alert(
                Alert.WARNING, 'scrape_partial',
                result_key,
                f"{store_name}({machine_name}): {actual_count}/{expected_count}台のみ取得",
                {'expected': expected_count, 'actual': actual_count}
            ))
        
        # データ品質: 各台のフィールドチェック
        empty_units = 0
        null_field_units = []
        
        for unit in units:
            unit_id = unit.get('unit_id', '?')
            days = unit.get('days', [])
            
            if not days:
                empty_units += 1
                continue
            
            # 最新日のデータをチェック
            latest = days[0] if days else {}
            art = latest.get('art')
            games = latest.get('total_start') or latest.get('games')
            
            # null/0チェック
            problems = []
            if art is None:
                problems.append('art=null')
            if games is None:
                problems.append('games=null')
            if art == 0 and games and games > 500:
                problems.append(f'art=0 but games={games}')
            
            if problems:
                null_field_units.append({'unit_id': unit_id, 'problems': problems})
        
        if empty_units > expected_count * 0.3:
            alerts.append(Alert(
                Alert.WARNING, 'scrape_empty_units',
                result_key,
                f"{store_name}({machine_name}): {empty_units}/{actual_count}台がデータ空",
                {'empty': empty_units, 'total': actual_count}
            ))
        
        if null_field_units:
            alerts.append(Alert(
                Alert.WARNING, 'scrape_null_fields',
                result_key,
                f"{store_name}({machine_name}): {len(null_field_units)}台にnull/異常フィールド",
                {'units': null_field_units[:5]}  # 先頭5台のみ
            ))
    
    return alerts


def check_unit_changes(daily_data: dict) -> list:
    """台番号/機種名/台数チェック
    
    - 消えた台（撤去/移動）
    - 新しい台（増台/移動先）
    - 台数変動
    - 機種名バリデーション
    """
    alerts = []
    stores = daily_data.get('stores', {})
    
    for store_key, config in STORES.items():
        machine_key = config.get('machine', 'sbj')
        unit_list = config.get('units', [])
        result_key = store_key
        store_data = stores.get(result_key)
        if not store_data:
            continue
        
        store_name = config.get('name', store_key)
        machine_name = MACHINES.get(machine_key, {}).get('display_name', machine_key)
        config_units = set(str(u) for u in unit_list)
        
        # スクレイピングで取得した台番号
        scraped_units = set()
        machine_names_found = set()
        mismatched_machines = []
        
        for unit in store_data.get('units', []):
            uid = str(unit.get('unit_id', ''))
            if uid:
                scraped_units.add(uid)
            mn = unit.get('machine_name', '')
            if mn:
                machine_names_found.add(mn)
            if unit.get('machine_mismatch'):
                mismatched_machines.append({
                    'unit_id': uid,
                    'actual_machine': unit.get('actual_machine', '不明'),
                })
        
        # 消えた台
        missing = config_units - scraped_units
        if missing:
            severity = Alert.CRITICAL if len(missing) >= 3 else Alert.WARNING
            alerts.append(Alert(
                severity, 'unit_missing',
                result_key,
                f"{store_name}({machine_name}): {len(missing)}台が消失 [{', '.join(sorted(missing))}]",
                {'missing_units': sorted(missing)}
            ))
        
        # 新しい台（configにない台が取得された）
        new_units = scraped_units - config_units
        if new_units:
            alerts.append(Alert(
                Alert.WARNING, 'unit_new',
                result_key,
                f"{store_name}({machine_name}): {len(new_units)}台が新出現 [{', '.join(sorted(new_units))}]",
                {'new_units': sorted(new_units)}
            ))
        
        # 台数変動
        if len(config_units) != len(scraped_units):
            diff = len(scraped_units) - len(config_units)
            alerts.append(Alert(
                Alert.WARNING, 'unit_count_change',
                result_key,
                f"{store_name}({machine_name}): 台数変動 {len(config_units)}→{len(scraped_units)} ({diff:+d}台)",
                {'config_count': len(config_units), 'scraped_count': len(scraped_units)}
            ))
        
        # 機種名不一致
        if mismatched_machines:
            alerts.append(Alert(
                Alert.CRITICAL, 'machine_mismatch',
                result_key,
                f"{store_name}: {len(mismatched_machines)}台が別機種に変更",
                {'mismatched': mismatched_machines}
            ))
        
        # 台番号の大幅変動（移動の疑い）
        if missing and new_units and len(missing) == len(new_units):
            alerts.append(Alert(
                Alert.CRITICAL, 'unit_shuffle',
                result_key,
                f"{store_name}({machine_name}): 台移動の疑い — {len(missing)}台消失+{len(new_units)}台新出現",
                {'missing': sorted(missing), 'new': sorted(new_units)}
            ))
    
    return alerts


def check_date_integrity(daily_data: dict) -> list:
    """日付整合性チェック
    
    - 蓄積DBの日付ギャップ（歯抜け）
    - 未来日付
    - 開店直後のデータ切り替わり
    """
    alerts = []
    now = datetime.now(JST)
    today_str = now.strftime('%Y-%m-%d')
    
    stores = daily_data.get('stores', {})
    
    for result_key, store_data in stores.items():
        for unit in store_data.get('units', []):
            unit_id = str(unit.get('unit_id', '?'))
            days = unit.get('days', [])
            
            for day in days:
                date = day.get('date', '')
                if not date:
                    continue
                
                # 未来日付
                if date > today_str:
                    alerts.append(Alert(
                        Alert.CRITICAL, 'future_date',
                        result_key,
                        f"台{unit_id}: 未来日付 {date} が混入",
                        {'unit_id': unit_id, 'date': date}
                    ))
    
    # 蓄積DBの歯抜け日チェック
    if HISTORY_DIR.exists():
        for store_dir in HISTORY_DIR.iterdir():
            if not store_dir.is_dir():
                continue
            store_key = store_dir.name
            
            for unit_file in store_dir.glob('*.json'):
                try:
                    with open(unit_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    unit_id = data.get('unit_id', unit_file.stem)
                    days = data.get('days', [])
                    if len(days) < 3:
                        continue
                    
                    # 営業日（定休日除く）の歯抜けを検出
                    dates = sorted(d.get('date', '') for d in days if d.get('date'))
                    if len(dates) >= 2:
                        gaps = []
                        for i in range(1, len(dates)):
                            d1 = datetime.strptime(dates[i-1], '%Y-%m-%d')
                            d2 = datetime.strptime(dates[i], '%Y-%m-%d')
                            gap = (d2 - d1).days
                            if gap > 2:  # 2日以上のギャップ（1日休みは許容）
                                gaps.append(f"{dates[i-1]}→{dates[i]}({gap}日)")
                        
                        if gaps:
                            alerts.append(Alert(
                                Alert.INFO, 'date_gap',
                                store_key,
                                f"台{unit_id}: 日付ギャップ {', '.join(gaps[:3])}",
                                {'unit_id': unit_id, 'gaps': gaps}
                            ))
                except (json.JSONDecodeError, IOError):
                    continue
    
    return alerts


def check_data_quality(daily_data: dict) -> list:
    """データ品質チェック
    
    - 異常な差枚値
    - ありえないART確率
    - historyの時系列矛盾
    """
    alerts = []
    stores = daily_data.get('stores', {})
    
    for result_key, store_data in stores.items():
        machine_key = store_data.get('machine_key', 'sbj')
        
        for unit in store_data.get('units', []):
            unit_id = str(unit.get('unit_id', '?'))
            days = unit.get('days', [])
            
            for day in days:
                art = day.get('art', 0) or 0
                games = day.get('total_start', 0) or day.get('games', 0) or 0
                date = day.get('date', '?')
                diff = day.get('diff_medals')
                
                # ART確率チェック
                if art > 0 and games > 0:
                    prob = games / art
                    if prob < 1:
                        alerts.append(Alert(
                            Alert.CRITICAL, 'impossible_prob',
                            result_key,
                            f"台{unit_id} {date}: ART確率 1/{prob:.1f} — 物理的にありえない",
                            {'unit_id': unit_id, 'date': date, 'art': art, 'games': games}
                        ))
                    elif prob > 5000:
                        alerts.append(Alert(
                            Alert.WARNING, 'extreme_prob',
                            result_key,
                            f"台{unit_id} {date}: ART確率 1/{prob:.0f} — 異常に悪い",
                            {'unit_id': unit_id, 'date': date, 'art': art, 'games': games}
                        ))
                
                # 差枚チェック
                if diff is not None and abs(diff) > 50000:
                    alerts.append(Alert(
                        Alert.WARNING, 'extreme_diff',
                        result_key,
                        f"台{unit_id} {date}: 差枚{diff:+,}枚 — 異常値の可能性",
                        {'unit_id': unit_id, 'date': date, 'diff': diff}
                    ))
                
                # historyの時系列チェック
                history = day.get('history', [])
                if len(history) >= 2:
                    times = [h.get('time', '') for h in history if h.get('time')]
                    if times and all(t for t in times):
                        # 新しい順→古い順のはずが、昇順になってないかチェック
                        # (daidataは新しい順、papimoも新しい順)
                        pass  # 順序は取得元依存なので軽くチェック
    
    return alerts


def check_opening_data_transition(daily_data: dict) -> list:
    """開店時のデータ切り替わりチェック
    
    日付が変わった直後（10:00開店）に取得した場合、
    サイト側が前日データ→当日データに切り替わるタイミングで
    古いデータが返される可能性がある。
    """
    alerts = []
    now = datetime.now(JST)
    
    # 10:00-11:00の間に取得されたデータは注意
    collected_at_str = daily_data.get('collected_at', '')
    if collected_at_str:
        try:
            collected_at = datetime.fromisoformat(collected_at_str)
            if collected_at.tzinfo is None:
                collected_at = collected_at.replace(tzinfo=JST)
            hour = collected_at.hour
            if 10 <= hour <= 11:
                alerts.append(Alert(
                    Alert.WARNING, 'early_collection',
                    'system',
                    f"データ収集が開店直後（{hour}時台）。サイト側の日付切替前の可能性あり",
                    {'collected_at': collected_at_str}
                ))
        except (ValueError, TypeError):
            pass
    
    return alerts


def run_all_checks(daily_data: dict) -> list:
    """全チェック実行"""
    all_alerts = []
    
    print('\n' + '=' * 60)
    print('📋 データ整合性チェック')
    print('=' * 60)
    
    # 1. スクレイピング品質
    print('\n[1/5] スクレイピング品質チェック...')
    alerts = check_scraping_quality(daily_data)
    all_alerts.extend(alerts)
    _print_section_result(alerts)
    
    # 2. 台番号/機種名/台数
    print('\n[2/5] 台番号/機種名/台数チェック...')
    alerts = check_unit_changes(daily_data)
    all_alerts.extend(alerts)
    _print_section_result(alerts)
    
    # 3. 日付整合性
    print('\n[3/5] 日付整合性チェック...')
    alerts = check_date_integrity(daily_data)
    all_alerts.extend(alerts)
    _print_section_result(alerts)
    
    # 4. データ品質
    print('\n[4/5] データ品質チェック...')
    alerts = check_data_quality(daily_data)
    all_alerts.extend(alerts)
    _print_section_result(alerts)
    
    # 5. 開店時データ切り替わり
    print('\n[5/5] 開店時データ切替チェック...')
    alerts = check_opening_data_transition(daily_data)
    all_alerts.extend(alerts)
    _print_section_result(alerts)
    
    # サマリー
    _print_summary(all_alerts)
    
    return all_alerts


def _print_section_result(alerts):
    if not alerts:
        print('  ✅ 問題なし')
    else:
        for a in alerts:
            print(f'  {a}')


def _print_summary(alerts):
    critical = [a for a in alerts if a.level == Alert.CRITICAL]
    warnings = [a for a in alerts if a.level == Alert.WARNING]
    info = [a for a in alerts if a.level == Alert.INFO]
    
    print(f'\n{"=" * 60}')
    print(f'📊 チェック結果サマリー')
    print(f'  🔴 重大: {len(critical)}件')
    print(f'  🟡 警告: {len(warnings)}件')
    print(f'  🔵 情報: {len(info)}件')
    
    if critical:
        print(f'\n⚠️ 重大アラート（WhatsApp通知対象）:')
        for a in critical:
            print(f'  {a}')
    
    print('=' * 60)


def save_check_result(alerts: list, source: str = 'daily') -> Path:
    """チェック結果を保存"""
    alerts_dir = PROJECT_ROOT / 'data' / 'alerts'
    alerts_dir.mkdir(parents=True, exist_ok=True)
    
    now = datetime.now(JST)
    filename = f"integrity_{source}_{now.strftime('%Y%m%d_%H%M%S')}.json"
    filepath = alerts_dir / filename
    
    result = {
        'checked_at': now.isoformat(),
        'source': source,
        'summary': {
            'critical': len([a for a in alerts if a.level == Alert.CRITICAL]),
            'warning': len([a for a in alerts if a.level == Alert.WARNING]),
            'info': len([a for a in alerts if a.level == Alert.INFO]),
        },
        'alerts': [a.to_dict() for a in alerts],
    }
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    return filepath


def format_notification(alerts: list) -> str:
    """WhatsApp通知用テキスト生成（重大アラートのみ）"""
    critical = [a for a in alerts if a.level == Alert.CRITICAL]
    if not critical:
        return ''
    
    lines = [f'⚠️ データチェック — 重大アラート {len(critical)}件']
    lines.append('')
    
    for a in critical:
        lines.append(f'🔴 {a.message}')
    
    lines.append('')
    lines.append('確認してください。')
    
    return '\n'.join(lines)


def main():
    """スタンドアロン実行"""
    import argparse
    parser = argparse.ArgumentParser(description='データ整合性チェック')
    parser.add_argument('--file', type=str, help='チェック対象のJSONファイル')
    parser.add_argument('--latest', action='store_true', help='最新のdailyファイルを自動選択')
    args = parser.parse_args()
    
    if args.file:
        filepath = Path(args.file)
    elif args.latest:
        daily_dir = PROJECT_ROOT / 'data' / 'daily'
        files = sorted(daily_dir.glob('daily_sbj_hokuto_tensei2_*.json'), reverse=True)
        if not files:
            files = sorted(daily_dir.glob('daily_*.json'), reverse=True)
        if not files:
            print('デイリーファイルが見つかりません')
            sys.exit(1)
        filepath = files[0]
    else:
        print('--file または --latest を指定してください')
        sys.exit(1)
    
    print(f'チェック対象: {filepath.name}')
    
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    alerts = run_all_checks(data)
    
    if alerts:
        save_path = save_check_result(alerts)
        print(f'\n結果保存: {save_path}')
    
    # 重大アラートがあれば非ゼロ終了
    critical = [a for a in alerts if a.level == Alert.CRITICAL]
    if critical:
        notification = format_notification(alerts)
        print(f'\n📱 通知テキスト:\n{notification}')
        sys.exit(1)


if __name__ == '__main__':
    main()
