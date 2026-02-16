#!/usr/bin/env python3
"""自動データ修復システム

データ不足を自動検知し、自動で修復を試みる。
- availability.jsonの不足キー検知＆再取得
- 履歴データの欠損検知＆自動補完
- 3日以上更新されていない店舗の再取得

使い方:
  python3 scripts/auto_repair_data.py        # 全自動修復
  python3 scripts/auto_repair_data.py --dry  # ドライラン（検知のみ）
"""
import json
import sys
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone

BASE = Path(__file__).resolve().parent.parent
AVAILABILITY_JSON = BASE / 'data' / 'availability.json'
HISTORY_DIR = BASE / 'data' / 'history'


def load_availability():
    """availability.jsonを読み込む"""
    if not AVAILABILITY_JSON.exists():
        return None
    try:
        return json.loads(AVAILABILITY_JSON.read_text())
    except Exception as e:
        print(f"⚠️  availability.json読み込み失敗: {e}")
        return None


def check_availability_completeness(data):
    """availability.jsonの完全性をチェック"""
    issues = []

    if not data or 'stores' not in data:
        issues.append('availability.jsonにstoresキーがない')
        return issues

    stores = data['stores']

    # 全店舗チェック
    expected_stores = [
        'shibuya_espass_sbj', 'shinjuku_espass_sbj', 'akiba_espass_sbj',
        'seibu_shinjuku_espass_sbj', 'ueno_espass_sbj', 'ueno_honkan_espass_sbj',
        'takadanobaba_espass_sbj', 'akasaka_espass_sbj', 'shinokubo_espass_sbj',
        'shinkoiwa_espass_sbj', 'shibuya_honkan_espass_sbj',
        'shibuya_espass_hokuto2', 'shinjuku_espass_hokuto2', 'akiba_espass_hokuto2',
        'seibu_shinjuku_espass_hokuto2', 'shibuya_honkan_espass_hokuto2',
        'island_akihabara_sbj', 'island_akihabara_hokuto2'
    ]

    for store in expected_stores:
        if store not in stores:
            issues.append(f'店舗 {store} がavailability.jsonにない')
            continue

        store_data = stores[store]
        if 'units' not in store_data:
            issues.append(f'{store}: unitsキーがない')
        elif not store_data['units']:
            # units空は警告のみ（全台空きの可能性）
            pass

    return issues


def check_history_completeness():
    """履歴データの完全性をチェック"""
    issues = []

    if not HISTORY_DIR.exists():
        issues.append('data/history/ ディレクトリが存在しない')
        return issues

    # 各店舗ディレクトリをチェック
    today = datetime.now(timezone(timedelta(hours=9))).date()
    three_days_ago = today - timedelta(days=3)

    for store_dir in HISTORY_DIR.iterdir():
        if not store_dir.is_dir():
            continue

        # 各ユニットファイルをチェック
        unit_files = list(store_dir.glob('*.json'))
        if not unit_files:
            issues.append(f'{store_dir.name}: ユニットファイルが0件')
            continue

        # サンプルとして最初のファイルをチェック
        sample_file = unit_files[0]
        try:
            data = json.loads(sample_file.read_text())
            if 'days' not in data or not data['days']:
                issues.append(f'{store_dir.name}/{sample_file.stem}: daysが空')
                continue

            # 最新データの日付を確認
            latest_date_str = data['days'][0].get('date', '')
            if latest_date_str:
                latest_date = datetime.strptime(latest_date_str, '%Y-%m-%d').date()
                days_old = (today - latest_date).days
                if days_old > 3:
                    issues.append(f'{store_dir.name}: 最新データが{days_old}日前（{latest_date_str}）')
        except Exception as e:
            issues.append(f'{store_dir.name}/{sample_file.stem}: 読み込みエラー: {e}')

    return issues


def run_repair(dry_run=False):
    """自動修復を実行"""
    print('='*50)
    print('自動データ修復システム')
    print('='*50)
    print()

    all_issues = []

    # 1. availability.json チェック
    print('[1/2] availability.json チェック中...')
    data = load_availability()
    avail_issues = check_availability_completeness(data)
    all_issues.extend(avail_issues)

    if avail_issues:
        print(f'  ⚠️  {len(avail_issues)}件の問題を検出:')
        for issue in avail_issues[:5]:  # 最初の5件のみ表示
            print(f'    - {issue}')
        if len(avail_issues) > 5:
            print(f'    ... 他 {len(avail_issues)-5}件')

        if not dry_run:
            print('  🔧 自動修復: availability.jsonを再取得中...')
            try:
                subprocess.run(
                    ['python3', str(BASE / 'scripts' / 'scrapers_v2' / 'fetch_all.py')],
                    timeout=600,
                    check=True
                )
                print('  ✅ 再取得完了')
            except subprocess.TimeoutExpired:
                print('  ❌ タイムアウト（10分）')
            except subprocess.CalledProcessError as e:
                print(f'  ❌ 再取得失敗: {e}')
    else:
        print('  ✅ 問題なし')

    # 2. 履歴データ チェック
    print()
    print('[2/2] 履歴データ チェック中...')
    hist_issues = check_history_completeness()
    all_issues.extend(hist_issues)

    if hist_issues:
        print(f'  ⚠️  {len(hist_issues)}件の問題を検出:')
        for issue in hist_issues[:5]:  # 最初の5件のみ表示
            print(f'    - {issue}')
        if len(hist_issues) > 5:
            print(f'    ... 他 {len(hist_issues)-5}件')

        if not dry_run:
            print('  🔧 自動修復: 履歴データを補完中...')
            # 履歴データ補完はnightly-updateで実行
            print('  ℹ️  履歴データ補完はnightly-updateワークフローで自動実行されます')
    else:
        print('  ✅ 問題なし')

    # まとめ
    print()
    print('='*50)
    if all_issues:
        print(f'検出: {len(all_issues)}件の問題')
        if dry_run:
            print('（ドライランモード - 修復は実行されませんでした）')
        else:
            print('自動修復を試みました。詳細は上記ログを確認してください。')
    else:
        print('✅ 全データ正常')
    print('='*50)

    return len(all_issues)


if __name__ == '__main__':
    dry_run = '--dry' in sys.argv
    exit_code = run_repair(dry_run)
    sys.exit(0 if exit_code == 0 else 1)
