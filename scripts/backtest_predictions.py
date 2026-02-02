#!/usr/bin/env python3
"""
予測バックテストスクリプト

過去のデータを使って、N日までのデータでN+1日を予測し、
実際の結果と比較して精度を検証する。

シミュレート対象：
- 1/25までのデータで1/26を予測→的中したか？
- 1/26までのデータで1/27を予測→的中したか？
- ...
- 1/31までのデータで2/1を予測→的中したか？
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from collections import defaultdict

# プロジェクトルート追加
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.rankings import STORES, MACHINES

# ========================================
# 予測ロジック（シンプル版）
# ========================================

def calculate_trend_score(days_data: List[dict], lookback: int = 5) -> dict:
    """
    過去N日間のトレンドからスコアを計算
    
    スコア要素：
    1. 連続プラス/マイナス日数
    2. ART確率の推移（上昇/下降トレンド）
    3. 前日凹み→翌日狙い目パターン
    4. 直近の差枚傾向
    """
    if not days_data or len(days_data) < 2:
        return {'score': 50, 'rank': 'C', 'reasons': ['データ不足']}
    
    score = 50  # ベーススコア
    reasons = []
    
    recent = days_data[:lookback]
    
    # 1. 前日の結果（最重要）
    yesterday = recent[0] if recent else None
    if yesterday:
        diff = yesterday.get('diff_medals', 0) or 0
        prob = yesterday.get('prob', 200) or 200
        
        # 前日凹み → 翌日狙い目（最重要パターン）
        if diff < -2000:
            score += 20
            reasons.append(f'前日大凹み({diff:+,}枚)→反発期待')
        elif diff < -1000:
            score += 10
            reasons.append(f'前日凹み({diff:+,}枚)→反発期待')
        elif diff > 3000:
            score -= 10
            reasons.append(f'前日大勝({diff:+,}枚)→連勝警戒')
        
        # 前日のART確率
        if prob < 100:
            score += 15
            reasons.append(f'前日高確率(1/{prob:.0f})→高機械割域継続期待')
        elif prob < 130:
            score += 8
            reasons.append(f'前日好調(1/{prob:.0f})')
        elif prob > 200:
            score -= 5
            reasons.append(f'前日不調(1/{prob:.0f})')
    
    # 2. 連続傾向（3日以上同じ傾向は反発）
    consecutive_plus = 0
    consecutive_minus = 0
    for d in recent:
        diff = d.get('diff_medals', 0) or 0
        if diff > 0:
            consecutive_plus += 1
            consecutive_minus = 0
        elif diff < 0:
            consecutive_minus += 1
            consecutive_plus = 0
        else:
            break
        if consecutive_plus >= 3:
            score -= 5
            if '連勝後警戒' not in str(reasons):
                reasons.append(f'{consecutive_plus}連勝後→調整警戒')
            break
        if consecutive_minus >= 3:
            score += 10
            if '連敗後期待' not in str(reasons):
                reasons.append(f'{consecutive_minus}連敗後→反発期待')
            break
    
    # 3. 週間トレンド（確率の平均）
    probs = [d.get('prob', 200) or 200 for d in recent if d.get('prob')]
    if probs:
        avg_prob = sum(probs) / len(probs)
        if avg_prob < 120:
            score += 10
            reasons.append(f'週間高確率(平均1/{avg_prob:.0f})')
        elif avg_prob > 180:
            score -= 5
    
    # ランク決定
    if score >= 80:
        rank = 'S'
    elif score >= 65:
        rank = 'A'
    elif score >= 50:
        rank = 'B'
    elif score >= 35:
        rank = 'C'
    else:
        rank = 'D'
    
    return {
        'score': score,
        'rank': rank,
        'reasons': reasons,
    }


def predict_units_for_date(store_key: str, target_date: str, history_data: dict) -> List[dict]:
    """
    target_date（予測対象日）の前日までのデータで予測を生成
    
    Args:
        store_key: 店舗キー
        target_date: 予測対象日 (YYYY-MM-DD)
        history_data: {unit_id: {'days': [...]}, ...}
    
    Returns:
        ランキング順の推奨台リスト
    """
    target_dt = datetime.strptime(target_date, '%Y-%m-%d')
    cutoff_dt = target_dt - timedelta(days=1)
    cutoff_str = cutoff_dt.strftime('%Y-%m-%d')
    
    predictions = []
    
    for unit_id, unit_data in history_data.items():
        days = unit_data.get('days', [])
        
        # target_date より前のデータのみ使用
        available_days = [d for d in days if d.get('date', '') < target_date]
        
        if not available_days:
            continue
        
        # トレンドスコア計算
        trend = calculate_trend_score(available_days)
        
        predictions.append({
            'unit_id': unit_id,
            'score': trend['score'],
            'rank': trend['rank'],
            'reasons': trend['reasons'],
            'yesterday_diff': available_days[0].get('diff_medals', 0) if available_days else 0,
            'yesterday_prob': available_days[0].get('prob', 0) if available_days else 0,
        })
    
    # スコア順でソート
    predictions.sort(key=lambda x: x['score'], reverse=True)
    
    return predictions


def evaluate_prediction(prediction: dict, actual_result: dict) -> dict:
    """
    予測と実際の結果を比較して評価
    
    Args:
        prediction: 予測データ
        actual_result: 実際の結果（日のデータ）
    
    Returns:
        評価結果
    """
    actual_diff = actual_result.get('diff_medals', 0) or 0
    actual_prob = actual_result.get('prob', 200) or 200
    
    # 予測ランクと実際の結果
    pred_rank = prediction.get('rank', 'C')
    
    # 実際の結果ランク
    if actual_diff > 2000 or actual_prob < 100:
        actual_rank = 'S'
    elif actual_diff > 500 or actual_prob < 130:
        actual_rank = 'A'
    elif actual_diff > -500:
        actual_rank = 'B'
    elif actual_diff > -2000:
        actual_rank = 'C'
    else:
        actual_rank = 'D'
    
    # 的中判定
    # S/A予測 → 実際S/A/B = 成功（許容範囲）
    # S/A予測 → 実際C/D = 失敗（外れ）
    high_pred = pred_rank in ('S', 'A')
    high_actual = actual_rank in ('S', 'A', 'B')
    
    if high_pred and high_actual:
        result = 'HIT'  # 推奨台が好調
    elif high_pred and not high_actual:
        result = 'MISS'  # 推奨台が不調
    elif not high_pred and actual_rank in ('S', 'A'):
        result = 'SURPRISE'  # 非推奨台が好調（見逃し）
    else:
        result = 'OK'  # 非推奨台が非好調（正しい判断）
    
    return {
        'unit_id': prediction['unit_id'],
        'pred_rank': pred_rank,
        'pred_score': prediction.get('score', 0),
        'actual_rank': actual_rank,
        'actual_diff': actual_diff,
        'actual_prob': actual_prob,
        'result': result,
    }


def run_backtest(store_key: str, start_date: str, end_date: str, top_n: int = 3) -> dict:
    """
    バックテスト実行
    
    Args:
        store_key: 店舗キー
        start_date: 開始日（予測対象の最初の日）
        end_date: 終了日（予測対象の最後の日）
        top_n: 上位何台を推奨とするか
    
    Returns:
        バックテスト結果
    """
    history_dir = PROJECT_ROOT / 'data' / 'history' / store_key
    
    if not history_dir.exists():
        return {'error': f'History directory not found: {history_dir}'}
    
    # 全台のデータ読み込み
    history_data = {}
    for json_file in history_dir.glob('*.json'):
        try:
            with open(json_file) as f:
                data = json.load(f)
            unit_id = json_file.stem
            history_data[unit_id] = data
        except Exception as e:
            continue
    
    if not history_data:
        return {'error': 'No history data found'}
    
    # 日付範囲でバックテスト
    results_by_date = {}
    all_evaluations = []
    
    current_dt = datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')
    
    while current_dt <= end_dt:
        date_str = current_dt.strftime('%Y-%m-%d')
        
        # 予測生成
        predictions = predict_units_for_date(store_key, date_str, history_data)
        
        if not predictions:
            current_dt += timedelta(days=1)
            continue
        
        # 上位N台を推奨
        top_predictions = predictions[:top_n]
        
        # 実際の結果と比較
        day_results = {
            'date': date_str,
            'predictions': [],
            'hits': 0,
            'misses': 0,
            'surprises': 0,
        }
        
        for pred in top_predictions:
            unit_id = pred['unit_id']
            unit_data = history_data.get(unit_id, {})
            days = unit_data.get('days', [])
            
            # target_date の実際の結果を取得
            actual = None
            for d in days:
                if d.get('date') == date_str:
                    actual = d
                    break
            
            if actual:
                evaluation = evaluate_prediction(pred, actual)
                day_results['predictions'].append(evaluation)
                all_evaluations.append(evaluation)
                
                if evaluation['result'] == 'HIT':
                    day_results['hits'] += 1
                elif evaluation['result'] == 'MISS':
                    day_results['misses'] += 1
        
        # 見逃しチェック（非推奨だが実際は好調だった台）
        non_top_ids = set(p['unit_id'] for p in predictions[top_n:])
        for unit_id in non_top_ids:
            unit_data = history_data.get(unit_id, {})
            days = unit_data.get('days', [])
            actual = None
            for d in days:
                if d.get('date') == date_str:
                    actual = d
                    break
            if actual:
                diff = actual.get('diff_medals', 0) or 0
                prob = actual.get('prob', 200) or 200
                if diff > 2000 or prob < 100:
                    day_results['surprises'] += 1
        
        results_by_date[date_str] = day_results
        current_dt += timedelta(days=1)
    
    # 集計
    total_predictions = len(all_evaluations)
    total_hits = sum(1 for e in all_evaluations if e['result'] == 'HIT')
    total_misses = sum(1 for e in all_evaluations if e['result'] == 'MISS')
    
    hit_rate = (total_hits / total_predictions * 100) if total_predictions > 0 else 0
    
    return {
        'store_key': store_key,
        'start_date': start_date,
        'end_date': end_date,
        'top_n': top_n,
        'total_predictions': total_predictions,
        'total_hits': total_hits,
        'total_misses': total_misses,
        'hit_rate': hit_rate,
        'results_by_date': results_by_date,
    }


def print_backtest_report(result: dict):
    """バックテスト結果をレポート出力"""
    if 'error' in result:
        print(f"エラー: {result['error']}")
        return
    
    print("=" * 70)
    print(f"予測バックテスト結果: {result['store_key']}")
    print(f"期間: {result['start_date']} 〜 {result['end_date']}")
    print(f"推奨台数: 上位{result['top_n']}台")
    print("=" * 70)
    print()
    print(f"総予測数: {result['total_predictions']}")
    print(f"的中数: {result['total_hits']}")
    print(f"外れ数: {result['total_misses']}")
    print(f"的中率: {result['hit_rate']:.1f}%")
    print()
    print("-" * 70)
    print("日別結果:")
    print("-" * 70)
    
    for date_str, day_result in result['results_by_date'].items():
        hits = day_result['hits']
        misses = day_result['misses']
        surprises = day_result['surprises']
        total = hits + misses
        rate = (hits / total * 100) if total > 0 else 0
        
        status = "◎" if rate >= 66 else "○" if rate >= 33 else "×"
        print(f"  {date_str}: {status} 的中{hits}/{total} ({rate:.0f}%) 見逃し{surprises}台")
        
        for pred in day_result['predictions']:
            emoji = "✅" if pred['result'] == 'HIT' else "❌"
            print(f"    {emoji} 台{pred['unit_id']}: "
                  f"予測{pred['pred_rank']}(スコア{pred['pred_score']}) → "
                  f"実際{pred['actual_rank']}(差枚{pred['actual_diff']:+,}, 確率1/{pred['actual_prob']:.0f})")
    
    print()


def main():
    """メイン実行"""
    import argparse
    
    parser = argparse.ArgumentParser(description='予測バックテスト')
    parser.add_argument('--store', default='shibuya_espass_sbj', help='店舗キー')
    parser.add_argument('--start', default='2026-01-26', help='開始日')
    parser.add_argument('--end', default='2026-01-31', help='終了日')
    parser.add_argument('--top', type=int, default=3, help='推奨台数')
    parser.add_argument('--all-stores', action='store_true', help='全店舗でテスト')
    parser.add_argument('--json', action='store_true', help='JSON出力')
    
    args = parser.parse_args()
    
    if args.all_stores:
        stores = [
            'shibuya_espass_sbj',
            'akiba_espass_sbj',
            'seibu_shinjuku_espass_sbj',
            'island_akihabara_sbj',
        ]
    else:
        stores = [args.store]
    
    all_results = {}
    
    for store in stores:
        print(f"\n{'='*70}")
        print(f"店舗: {store}")
        print('='*70)
        
        result = run_backtest(store, args.start, args.end, args.top)
        all_results[store] = result
        
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_backtest_report(result)
    
    # サマリー
    if len(stores) > 1:
        print("\n" + "=" * 70)
        print("全店舗サマリー")
        print("=" * 70)
        total_pred = sum(r.get('total_predictions', 0) for r in all_results.values())
        total_hits = sum(r.get('total_hits', 0) for r in all_results.values())
        overall_rate = (total_hits / total_pred * 100) if total_pred > 0 else 0
        print(f"総合的中率: {overall_rate:.1f}% ({total_hits}/{total_pred})")


if __name__ == '__main__':
    main()
