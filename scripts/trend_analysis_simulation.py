#!/usr/bin/env python3
"""
店舗×機種の傾向分析＋過去全日シミュレーション

目的:
- 店舗×機種ごとの傾向（どの台番号が高設定になりやすいか等）を学習
- データ初日から毎日シミュレーションして精度向上を確認
- 学習した傾向を予測に反映

傾向の観点:
1. 台番号別の好調率（特定台が高設定に入りやすい）
2. 曜日パターン（土日に設定入りやすい等）
3. 連続好調パターン（前日好調→当日も好調になりやすい）
4. 位置パターン（端台・角台の傾向）
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Tuple, Optional
import statistics

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config.rankings import MACHINES

HISTORY_DIR = ROOT / 'data' / 'history'
GOOD_PROB_THRESHOLD = {
    'sbj': 130,      # 1/130以下が好調
    'hokuto2': 120,  # 1/120以下が好調
}


def load_all_history() -> Dict[str, Dict[str, List[Dict]]]:
    """全履歴データを読み込む
    
    Returns:
        {store_key: {unit_id: [day_data, ...]}}
    """
    all_data = {}
    
    for store_dir in HISTORY_DIR.iterdir():
        if not store_dir.is_dir():
            continue
        
        store_key = store_dir.name
        all_data[store_key] = {}
        
        for hist_file in store_dir.glob('*.json'):
            unit_id = hist_file.stem
            with open(hist_file) as f:
                data = json.load(f)
            
            # 日付順にソート
            days = sorted(data.get('days', []), key=lambda x: x.get('date', ''))
            all_data[store_key][unit_id] = days
    
    return all_data


def get_machine_key(store_key: str) -> str:
    """store_keyから機種キーを取得"""
    if 'sbj' in store_key:
        return 'sbj'
    elif 'hokuto' in store_key:
        return 'hokuto2'
    return 'unknown'


def calc_art_prob(day_data: Dict) -> Optional[float]:
    """ART確率を計算（1/XXの分母を返す）"""
    art = day_data.get('art', 0)
    games = day_data.get('games', 0)
    
    if art <= 0 or games <= 0:
        return None
    
    return games / art


def is_good_day(day_data: Dict, machine_key: str) -> bool:
    """好調日かどうか判定"""
    prob = calc_art_prob(day_data)
    if prob is None:
        return False
    
    threshold = GOOD_PROB_THRESHOLD.get(machine_key, 130)
    return prob <= threshold


def analyze_trends(all_data: Dict, cutoff_date: str) -> Dict:
    """cutoff_date以前のデータで傾向を分析
    
    Args:
        all_data: 全履歴データ
        cutoff_date: この日付以前のデータで学習
    
    Returns:
        傾向データ {store_key: {unit_id: trend_info, ...}, ...}
    """
    trends = {}
    
    for store_key, units in all_data.items():
        machine_key = get_machine_key(store_key)
        trends[store_key] = {}
        
        for unit_id, days in units.items():
            # cutoff_date以前のデータのみ使用
            past_days = [d for d in days if d.get('date', '') < cutoff_date]
            
            if len(past_days) < 3:  # 最低3日のデータが必要
                continue
            
            # 好調率を計算
            good_count = sum(1 for d in past_days if is_good_day(d, machine_key))
            total_count = len(past_days)
            good_rate = good_count / total_count if total_count > 0 else 0
            
            # 曜日別好調率
            weekday_good = defaultdict(lambda: {'good': 0, 'total': 0})
            for d in past_days:
                date_str = d.get('date', '')
                if date_str:
                    try:
                        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                        wd = date_obj.weekday()  # 0=月, 6=日
                        weekday_good[wd]['total'] += 1
                        if is_good_day(d, machine_key):
                            weekday_good[wd]['good'] += 1
                    except:
                        pass
            
            weekday_rates = {}
            for wd, counts in weekday_good.items():
                if counts['total'] > 0:
                    weekday_rates[wd] = counts['good'] / counts['total']
            
            # 連続好調パターン（前日好調→当日好調の確率）
            consecutive_good = 0
            consecutive_total = 0
            for i in range(1, len(past_days)):
                prev = past_days[i-1]
                curr = past_days[i]
                if is_good_day(prev, machine_key):
                    consecutive_total += 1
                    if is_good_day(curr, machine_key):
                        consecutive_good += 1
            
            consecutive_rate = consecutive_good / consecutive_total if consecutive_total > 0 else 0
            
            # 平均確率
            probs = [calc_art_prob(d) for d in past_days if calc_art_prob(d) is not None]
            avg_prob = statistics.mean(probs) if probs else 999
            
            trends[store_key][unit_id] = {
                'good_rate': good_rate,
                'weekday_rates': weekday_rates,
                'consecutive_rate': consecutive_rate,
                'avg_prob': avg_prob,
                'sample_size': total_count,
            }
    
    return trends


def predict_with_trends(trends: Dict, store_key: str, unit_id: str, 
                        target_date: str, prev_day_good: bool = False) -> float:
    """傾向を使ってスコアを計算
    
    Returns:
        0-100のスコア（高いほど好調予測）
    """
    if store_key not in trends or unit_id not in trends[store_key]:
        return 50  # データなし→中立
    
    trend = trends[store_key][unit_id]
    
    # 基本スコア: 好調率
    base_score = trend['good_rate'] * 100
    
    # 曜日補正
    try:
        date_obj = datetime.strptime(target_date, '%Y-%m-%d')
        wd = date_obj.weekday()
        wd_rate = trend['weekday_rates'].get(wd, trend['good_rate'])
        weekday_bonus = (wd_rate - trend['good_rate']) * 50
    except:
        weekday_bonus = 0
    
    # 連続好調補正
    consecutive_bonus = 0
    if prev_day_good and trend['consecutive_rate'] > 0.3:
        consecutive_bonus = (trend['consecutive_rate'] - 0.3) * 30
    
    # サンプルサイズ補正（少ないデータは信頼性低）
    if trend['sample_size'] < 5:
        confidence = 0.5
    elif trend['sample_size'] < 10:
        confidence = 0.7
    else:
        confidence = 1.0
    
    score = (base_score + weekday_bonus + consecutive_bonus) * confidence
    return max(0, min(100, score))


def simulate_day(all_data: Dict, trends: Dict, target_date: str) -> Dict:
    """1日分のシミュレーション
    
    Returns:
        {'hits': int, 'misses': int, 'predictions': [...]}
    """
    results = {
        'date': target_date,
        'hits': 0,
        'misses': 0,
        'total_predictions': 0,
        'total_good_actual': 0,
        'predictions': [],
    }
    
    for store_key, units in all_data.items():
        machine_key = get_machine_key(store_key)
        
        for unit_id, days in units.items():
            # target_dateのデータを探す
            target_day = None
            prev_day = None
            for i, d in enumerate(days):
                if d.get('date') == target_date:
                    target_day = d
                    if i > 0:
                        prev_day = days[i-1]
                    break
            
            if target_day is None:
                continue
            
            # 実際の結果
            actual_good = is_good_day(target_day, machine_key)
            if actual_good:
                results['total_good_actual'] += 1
            
            # 予測
            prev_day_good = is_good_day(prev_day, machine_key) if prev_day else False
            score = predict_with_trends(trends, store_key, unit_id, target_date, prev_day_good)
            
            # スコア70以上をS/A予測とする
            predicted_good = score >= 70
            
            if predicted_good:
                results['total_predictions'] += 1
                if actual_good:
                    results['hits'] += 1
                else:
                    results['misses'] += 1
            
            results['predictions'].append({
                'store': store_key,
                'unit': unit_id,
                'score': score,
                'predicted': predicted_good,
                'actual': actual_good,
            })
    
    return results


def run_full_simulation(all_data: Dict) -> List[Dict]:
    """全期間シミュレーション"""
    
    # 全日付を収集
    all_dates = set()
    for store_key, units in all_data.items():
        for unit_id, days in units.items():
            for d in days:
                date = d.get('date')
                if date:
                    all_dates.add(date)
    
    sorted_dates = sorted(all_dates)
    
    # 最初の7日間は学習期間、8日目以降をシミュレーション
    if len(sorted_dates) < 8:
        print("データが足りません（最低8日必要）")
        return []
    
    results = []
    cumulative_hits = 0
    cumulative_predictions = 0
    
    print(f"シミュレーション期間: {sorted_dates[7]} ~ {sorted_dates[-1]}")
    print(f"総日数: {len(sorted_dates) - 7}日\n")
    
    for i, target_date in enumerate(sorted_dates[7:], start=8):
        # この日より前のデータで傾向を学習
        trends = analyze_trends(all_data, target_date)
        
        # シミュレーション
        day_result = simulate_day(all_data, trends, target_date)
        
        cumulative_hits += day_result['hits']
        cumulative_predictions += day_result['total_predictions']
        
        precision = day_result['hits'] / day_result['total_predictions'] * 100 if day_result['total_predictions'] > 0 else 0
        cumulative_precision = cumulative_hits / cumulative_predictions * 100 if cumulative_predictions > 0 else 0
        
        day_result['precision'] = precision
        day_result['cumulative_precision'] = cumulative_precision
        
        results.append(day_result)
        
        # 進捗表示
        if (i - 7) % 5 == 0 or i == len(sorted_dates):
            print(f"{target_date}: 的中{day_result['hits']}/{day_result['total_predictions']} ({precision:.1f}%) | 累計 {cumulative_precision:.1f}%")
    
    return results


def main():
    print("=" * 60)
    print("店舗×機種 傾向分析＋過去全日シミュレーション")
    print("=" * 60)
    
    print("\n1. データ読み込み中...")
    all_data = load_all_history()
    
    total_units = sum(len(units) for units in all_data.values())
    print(f"   {len(all_data)}店舗, {total_units}台")
    
    print("\n2. シミュレーション実行中...")
    results = run_full_simulation(all_data)
    
    if not results:
        return
    
    print("\n" + "=" * 60)
    print("結果サマリ")
    print("=" * 60)
    
    # 週ごとの精度推移
    weekly_results = defaultdict(lambda: {'hits': 0, 'preds': 0})
    for r in results:
        try:
            date_obj = datetime.strptime(r['date'], '%Y-%m-%d')
            week = date_obj.isocalendar()[1]
            weekly_results[week]['hits'] += r['hits']
            weekly_results[week]['preds'] += r['total_predictions']
        except:
            pass
    
    print("\n週ごとの精度推移:")
    for week, data in sorted(weekly_results.items()):
        prec = data['hits'] / data['preds'] * 100 if data['preds'] > 0 else 0
        print(f"  W{week}: {data['hits']}/{data['preds']} ({prec:.1f}%)")
    
    # 最終精度
    final = results[-1]
    print(f"\n最終累計精度: {final['cumulative_precision']:.1f}%")
    
    # 結果を保存
    output_path = ROOT / 'data' / 'analysis' / 'trend_simulation_results.json'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    summary = {
        'simulation_date': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'total_days': len(results),
        'final_precision': final['cumulative_precision'],
        'weekly_results': {str(k): v for k, v in weekly_results.items()},
        'daily_results': results,
    }
    
    with open(output_path, 'w') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    print(f"\n結果保存: {output_path}")


if __name__ == '__main__':
    main()
