#!/usr/bin/env python3
"""
傾向分析・保存スクリプト

傾向データを3階層で管理：
1. 全店舗共通（グローバル傾向）
2. 機種ごと（全店共通）
3. 店舗×機種ごと

保存先：
- data/trends/global.json
- data/trends/machines/{machine_key}.json
- data/trends/stores/{store_key}_{machine_key}.json
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from collections import defaultdict

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.rankings import MACHINES, STORES

TRENDS_DIR = PROJECT_ROOT / 'data' / 'trends'

# 曜日名
WEEKDAYS = ['月', '火', '水', '木', '金', '土', '日']

# 機種キー検出用パターン（新機種追加時はここに追加するか、config/rankings.pyのMACHINESを参照）
def detect_machine_key(store_dir_name: str) -> tuple:
    """
    ディレクトリ名から機種キーとベース店舗名を検出
    
    新機種追加時：config/rankings.pyのMACHINESに追加すれば自動対応
    """
    # config/rankings.pyのMACHINESから動的に検出
    for machine_key in MACHINES.keys():
        if f'_{machine_key}' in store_dir_name:
            base_store = store_dir_name.replace(f'_{machine_key}', '')
            return base_store, machine_key
    
    # フォールバック：旧形式対応
    if 'hokuto' in store_dir_name and 'tensei' in store_dir_name:
        return store_dir_name.replace('_hokuto_tensei2', ''), 'hokuto_tensei2'
    elif 'sbj' in store_dir_name:
        return store_dir_name.replace('_sbj', ''), 'sbj'
    
    # 不明な場合はそのまま
    return store_dir_name, 'unknown'


def ensure_dirs():
    """ディレクトリ作成"""
    (TRENDS_DIR / 'machines').mkdir(parents=True, exist_ok=True)
    (TRENDS_DIR / 'stores').mkdir(parents=True, exist_ok=True)


def load_all_history() -> Dict[str, Dict]:
    """
    全店舗の履歴データを読み込み
    
    新店舗・新機種は自動検出される
    
    Returns:
        {
            store_key: {
                machine_key: {
                    unit_id: {'days': [...]}
                }
            }
        }
    """
    history_base = PROJECT_ROOT / 'data' / 'history'
    all_data = defaultdict(lambda: defaultdict(dict))
    
    for store_dir in history_base.iterdir():
        if not store_dir.is_dir():
            continue
        
        store_key = store_dir.name
        
        # 機種を動的に判定
        base_store, machine_key = detect_machine_key(store_key)
        
        for json_file in store_dir.glob('*.json'):
            try:
                with open(json_file) as f:
                    data = json.load(f)
                unit_id = json_file.stem
                all_data[base_store][machine_key][unit_id] = data
            except Exception:
                continue
    
    return dict(all_data)


def analyze_day_patterns(days: List[dict]) -> Dict:
    """
    日別パターンを分析
    
    Returns:
        各種パターンの統計
    """
    patterns = {
        'dip_recovery': {'total': 0, 'success': 0},  # 前日凹み→翌日反発
        'streak_reversal': {'total': 0, 'success': 0},  # 連勝/連敗後の反転
        'weekday_performance': {i: {'total': 0, 'good': 0} for i in range(7)},
        'prob_zones': {
            'excellent': {'total': 0, 'next_good': 0},  # 1/100未満
            'high': {'total': 0, 'next_good': 0},       # 1/100-130
            'mid': {'total': 0, 'next_good': 0},        # 1/130-180
            'low': {'total': 0, 'next_good': 0},        # 1/180以上
        },
    }
    
    if len(days) < 2:
        return patterns
    
    # 日付順にソート（古い順）
    sorted_days = sorted(days, key=lambda d: d.get('date', ''))
    
    for i in range(len(sorted_days) - 1):
        today = sorted_days[i]
        tomorrow = sorted_days[i + 1]
        
        today_diff = today.get('diff_medals', 0) or 0
        today_prob = today.get('prob', 200) or 200
        tomorrow_diff = tomorrow.get('diff_medals', 0) or 0
        tomorrow_prob = tomorrow.get('prob', 200) or 200
        
        # 曜日パフォーマンス
        try:
            date_obj = datetime.strptime(tomorrow.get('date', ''), '%Y-%m-%d')
            weekday = date_obj.weekday()
            patterns['weekday_performance'][weekday]['total'] += 1
            if tomorrow_diff > 0 or tomorrow_prob < 130:
                patterns['weekday_performance'][weekday]['good'] += 1
        except:
            pass
        
        # 前日凹み→翌日反発パターン
        if today_diff < -2000:
            patterns['dip_recovery']['total'] += 1
            if tomorrow_diff > 0 or tomorrow_prob < 130:
                patterns['dip_recovery']['success'] += 1
        
        # 確率帯別翌日傾向
        if today_prob < 100:
            zone = 'excellent'
        elif today_prob < 130:
            zone = 'high'
        elif today_prob < 180:
            zone = 'mid'
        else:
            zone = 'low'
        
        patterns['prob_zones'][zone]['total'] += 1
        if tomorrow_diff > 0 or tomorrow_prob < 130:
            patterns['prob_zones'][zone]['next_good'] += 1
    
    return patterns


def merge_patterns(base: Dict, new: Dict) -> Dict:
    """パターン統計をマージ"""
    if not base:
        return new
    
    result = {}
    
    for key in ['dip_recovery', 'streak_reversal']:
        result[key] = {
            'total': base.get(key, {}).get('total', 0) + new.get(key, {}).get('total', 0),
            'success': base.get(key, {}).get('success', 0) + new.get(key, {}).get('success', 0),
        }
    
    # 曜日パフォーマンス
    result['weekday_performance'] = {}
    for i in range(7):
        base_wd = base.get('weekday_performance', {}).get(i, {})
        new_wd = new.get('weekday_performance', {}).get(i, {})
        result['weekday_performance'][i] = {
            'total': base_wd.get('total', 0) + new_wd.get('total', 0),
            'good': base_wd.get('good', 0) + new_wd.get('good', 0),
        }
    
    # 確率帯
    result['prob_zones'] = {}
    for zone in ['excellent', 'high', 'mid', 'low']:
        base_z = base.get('prob_zones', {}).get(zone, {})
        new_z = new.get('prob_zones', {}).get(zone, {})
        result['prob_zones'][zone] = {
            'total': base_z.get('total', 0) + new_z.get('total', 0),
            'next_good': base_z.get('next_good', 0) + new_z.get('next_good', 0),
        }
    
    return result


def calculate_rates(patterns: Dict, include_store_specific: bool = False) -> Dict:
    """パターンから確率を計算
    
    Args:
        patterns: パターン統計
        include_store_specific: 店舗固有の傾向（曜日別等）を含めるか
            - グローバル/機種別: False（含めない）
            - 店舗×機種: True（含める）
    """
    result = {
        'dip_recovery_rate': 0,
        'prob_zone_rates': {},
    }
    
    # 凹み→反発確率（全レベルで有効）
    dr = patterns.get('dip_recovery', {})
    if dr.get('total', 0) > 0:
        result['dip_recovery_rate'] = dr['success'] / dr['total'] * 100
    
    # 確率帯別（全レベルで有効）
    for zone in ['excellent', 'high', 'mid', 'low']:
        pz = patterns.get('prob_zones', {}).get(zone, {})
        if pz.get('total', 0) > 0:
            result['prob_zone_rates'][zone] = pz['next_good'] / pz['total'] * 100
    
    # 店舗固有の傾向（曜日別等）は店舗×機種レベルのみ
    if include_store_specific:
        result['weekday_rates'] = {}
        for i in range(7):
            wd = patterns.get('weekday_performance', {}).get(i, {})
            if wd.get('total', 0) > 0:
                result['weekday_rates'][WEEKDAYS[i]] = wd['good'] / wd['total'] * 100
    
    return result


def analyze_and_save():
    """全データを分析して傾向を保存"""
    ensure_dirs()
    
    print("=" * 70)
    print("傾向分析開始")
    print("=" * 70)
    
    all_data = load_all_history()
    
    # 各レベルのパターン集計
    global_patterns = {}
    machine_patterns = defaultdict(dict)
    store_machine_patterns = {}
    
    for store_key, machines in all_data.items():
        for machine_key, units in machines.items():
            store_machine_key = f"{store_key}_{machine_key}"
            store_patterns = {}
            
            for unit_id, unit_data in units.items():
                days = unit_data.get('days', [])
                patterns = analyze_day_patterns(days)
                
                # 各レベルにマージ
                global_patterns = merge_patterns(global_patterns, patterns)
                machine_patterns[machine_key] = merge_patterns(
                    machine_patterns[machine_key], patterns
                )
                store_patterns = merge_patterns(store_patterns, patterns)
            
            store_machine_patterns[store_machine_key] = store_patterns
    
    # 確率計算
    # グローバル・機種別: 曜日別などの店舗固有傾向は含めない
    global_rates = calculate_rates(global_patterns, include_store_specific=False)
    
    machine_rates = {}
    for machine_key, patterns in machine_patterns.items():
        machine_rates[machine_key] = calculate_rates(patterns, include_store_specific=False)
    
    # 店舗×機種: 曜日別などの店舗固有傾向を含める
    store_machine_rates = {}
    for key, patterns in store_machine_patterns.items():
        store_machine_rates[key] = calculate_rates(patterns, include_store_specific=True)
    
    # 保存
    now = datetime.now().isoformat()
    
    # グローバル傾向
    global_data = {
        'updated_at': now,
        'patterns': global_patterns,
        'rates': global_rates,
    }
    with open(TRENDS_DIR / 'global.json', 'w', encoding='utf-8') as f:
        json.dump(global_data, f, ensure_ascii=False, indent=2)
    print(f"✓ グローバル傾向保存: {TRENDS_DIR / 'global.json'}")
    
    # 機種別傾向
    for machine_key, patterns in machine_patterns.items():
        machine_data = {
            'machine_key': machine_key,
            'updated_at': now,
            'patterns': patterns,
            'rates': machine_rates.get(machine_key, {}),
        }
        path = TRENDS_DIR / 'machines' / f'{machine_key}.json'
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(machine_data, f, ensure_ascii=False, indent=2)
        print(f"✓ 機種傾向保存: {path}")
    
    # 店舗×機種傾向
    for key, patterns in store_machine_patterns.items():
        store_data = {
            'store_machine_key': key,
            'updated_at': now,
            'patterns': patterns,
            'rates': store_machine_rates.get(key, {}),
        }
        path = TRENDS_DIR / 'stores' / f'{key}.json'
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(store_data, f, ensure_ascii=False, indent=2)
    print(f"✓ 店舗×機種傾向保存: {len(store_machine_patterns)}件")
    
    # レポート出力
    print()
    print("=" * 70)
    print("傾向分析結果")
    print("=" * 70)
    
    print("\n【グローバル傾向（全店舗・全機種）】")
    print(f"  前日凹み→翌日反発率: {global_rates.get('dip_recovery_rate', 0):.1f}%")
    print("  前日確率帯→翌日好調率:")
    for zone, rate in global_rates.get('prob_zone_rates', {}).items():
        zone_label = {'excellent': '1/100未満', 'high': '1/100-130', 'mid': '1/130-180', 'low': '1/180以上'}
        print(f"    {zone_label.get(zone, zone):12}: {rate:5.1f}%")
    
    for machine_key, rates in machine_rates.items():
        print(f"\n【{machine_key}（全店共通）】")
        print(f"  前日凹み→翌日反発率: {rates.get('dip_recovery_rate', 0):.1f}%")
        print("  前日確率帯→翌日好調率:")
        for zone, rate in rates.get('prob_zone_rates', {}).items():
            zone_label = {'excellent': '1/100未満', 'high': '1/100-130', 'mid': '1/130-180', 'low': '1/180以上'}
            print(f"    {zone_label.get(zone, zone):12}: {rate:5.1f}%")
    
    # 店舗×機種の曜日傾向サマリー（上位のみ表示）
    print("\n【店舗×機種 曜日傾向（抜粋）】")
    for key, rates in list(store_machine_rates.items())[:3]:
        weekday_rates = rates.get('weekday_rates', {})
        if weekday_rates:
            best_day = max(weekday_rates.items(), key=lambda x: x[1])
            worst_day = min(weekday_rates.items(), key=lambda x: x[1])
            print(f"  {key}: 最良={best_day[0]}({best_day[1]:.0f}%) 最悪={worst_day[0]}({worst_day[1]:.0f}%)")
    
    print()
    return {
        'global': global_data,
        'machines': {k: machine_rates[k] for k in machine_patterns},
        'stores': store_machine_rates,
    }


if __name__ == '__main__':
    analyze_and_save()
