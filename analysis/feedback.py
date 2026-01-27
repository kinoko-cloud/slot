"""
答え合わせフィードバックシステム

予測 vs 実績を分析し、次回予測の精度向上に活用する。
- 外れパターンの分類と原因分析
- 見逃しパターンの分類と原因分析
- 台・店舗・曜日ごとの補正係数を算出
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

FEEDBACK_DIR = Path('data/feedback')
FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)

WEEKDAY_NAMES = ['月', '火', '水', '木', '金', '土', '日']


def analyze_prediction_errors(verify_results: list, store_key: str, machine_key: str) -> dict:
    """予測誤差を分析し、補正情報を返す

    Args:
        verify_results: [{unit_id, predicted_rank, predicted_score, actual_art, actual_prob, actual_games, ...}]
        store_key: 店舗キー
        machine_key: 機種キー

    Returns:
        分析結果dict
    """
    good_threshold = 130 if machine_key == 'sbj' else 330

    misses = []      # S/A予測 → 実際不調
    surprises = []   # B以下予測 → 実際好調
    hits = []        # S/A予測 → 実際好調

    for r in verify_results:
        rank = r.get('pre_open_rank', r.get('predicted_rank', 'C'))
        prob = r.get('actual_prob', 0)
        art = r.get('actual_art', 0)
        games = r.get('actual_games', 0)

        is_predicted_good = rank in ('S', 'A')
        is_actual_good = prob > 0 and prob <= good_threshold
        is_actual_bad = prob >= 200 or (games >= 1000 and art == 0)

        entry = {
            'unit_id': r.get('unit_id'),
            'predicted_rank': rank,
            'predicted_score': r.get('predicted_score', r.get('pre_open_score', 50)),
            'actual_prob': prob,
            'actual_art': art,
            'actual_games': games,
        }

        if is_predicted_good and is_actual_bad:
            misses.append(entry)
        elif is_predicted_good and is_actual_good:
            hits.append(entry)
        elif not is_predicted_good and is_actual_good:
            surprises.append(entry)

    # パターン分析
    analysis = {
        'store_key': store_key,
        'machine_key': machine_key,
        'date': datetime.now().strftime('%Y-%m-%d'),
        'weekday': WEEKDAY_NAMES[datetime.now().weekday()],
        'total_units': len(verify_results),
        'hits': len(hits),
        'misses': len(misses),
        'surprises': len(surprises),
        'miss_details': _analyze_misses(misses),
        'surprise_details': _analyze_surprises(surprises),
    }

    return analysis


def _analyze_misses(misses: list) -> list:
    """外れた台の原因を分析"""
    details = []
    for m in misses:
        reasons = []

        # 予測スコアが高すぎた
        if m['predicted_score'] >= 80:
            reasons.append('高スコア過信')

        # ゲーム数が少ない（早い段階でやめた可能性）
        if m['actual_games'] < 1000:
            reasons.append(f'低稼働({m["actual_games"]}G)')

        # ART確率が設定1域
        if m['actual_prob'] >= 200:
            reasons.append(f'設定1域(1/{m["actual_prob"]:.0f})')

        details.append({
            'unit_id': m['unit_id'],
            'score': m['predicted_score'],
            'actual_prob': m['actual_prob'],
            'reasons': reasons,
        })

    return details


def _analyze_surprises(surprises: list) -> list:
    """見逃した台の原因を分析"""
    details = []
    for s in surprises:
        reasons = []

        # スコアが低かったのに好調
        if s['predicted_score'] < 40:
            reasons.append(f'低スコア台({s["predicted_score"]:.0f}点)が好調')

        # 高確率で好調
        if s['actual_prob'] <= 80:
            reasons.append(f'実は高設定域(1/{s["actual_prob"]:.0f})')

        details.append({
            'unit_id': s['unit_id'],
            'rank': s['predicted_rank'],
            'score': s['predicted_score'],
            'actual_prob': s['actual_prob'],
            'reasons': reasons,
        })

    return details


def save_feedback(analysis: dict):
    """フィードバック結果を保存"""
    date_str = analysis['date']
    store_key = analysis['store_key']

    feedback_file = FEEDBACK_DIR / f'{store_key}_{date_str}.json'
    with open(feedback_file, 'w', encoding='utf-8') as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)


def load_feedback_history(store_key: str, days: int = 30) -> list:
    """過去のフィードバック履歴を読み込む"""
    results = []
    for fp in sorted(FEEDBACK_DIR.glob(f'{store_key}_*.json')):
        try:
            with open(fp) as f:
                data = json.load(f)
            results.append(data)
        except (json.JSONDecodeError, IOError):
            continue

    return results[-days:]


def calculate_correction_factors(store_key: str, machine_key: str) -> dict:
    """過去のフィードバックから補正係数を算出

    Returns:
        {
            'weekday_corrections': {'月': -2, '火': 0, ...},
            'unit_corrections': {'3011': +3, '3012': -2, ...},
            'confidence': float  # 0-1のデータ信頼度
        }
    """
    history = load_feedback_history(store_key)
    if not history:
        return {'weekday_corrections': {}, 'unit_corrections': {}, 'confidence': 0}

    # 曜日別の外れ率
    weekday_miss_rates = {}
    for fb in history:
        wd = fb.get('weekday', '')
        total = fb.get('total_units', 0)
        misses = fb.get('misses', 0)
        surprises = fb.get('surprises', 0)
        if total > 0:
            miss_rate = misses / total
            surprise_rate = surprises / total
            if wd not in weekday_miss_rates:
                weekday_miss_rates[wd] = []
            weekday_miss_rates[wd].append({
                'miss_rate': miss_rate,
                'surprise_rate': surprise_rate,
            })

    # 曜日補正: 外れが多い曜日 → スコアを下げる
    weekday_corrections = {}
    for wd, rates in weekday_miss_rates.items():
        avg_miss = sum(r['miss_rate'] for r in rates) / len(rates)
        avg_surprise = sum(r['surprise_rate'] for r in rates) / len(rates)
        # 外れ率が高い → マイナス補正、見逃し率が高い → プラス補正
        correction = int((avg_surprise - avg_miss) * 20)
        correction = max(-10, min(10, correction))
        weekday_corrections[wd] = correction

    # 台番号別の外れ率
    unit_miss_count = {}
    unit_surprise_count = {}
    unit_total_count = {}

    for fb in history:
        for detail in fb.get('miss_details', []):
            uid = str(detail.get('unit_id', ''))
            unit_miss_count[uid] = unit_miss_count.get(uid, 0) + 1
            unit_total_count[uid] = unit_total_count.get(uid, 0) + 1
        for detail in fb.get('surprise_details', []):
            uid = str(detail.get('unit_id', ''))
            unit_surprise_count[uid] = unit_surprise_count.get(uid, 0) + 1
            unit_total_count[uid] = unit_total_count.get(uid, 0) + 1

    # 台補正
    unit_corrections = {}
    for uid in unit_total_count:
        misses = unit_miss_count.get(uid, 0)
        surprises = unit_surprise_count.get(uid, 0)
        # 外れが多い台 → マイナス、見逃しが多い台 → プラス
        correction = (surprises - misses) * 3
        correction = max(-10, min(10, correction))
        if correction != 0:
            unit_corrections[uid] = correction

    confidence = min(1.0, len(history) / 7)  # 7日分でconfidence=1.0

    return {
        'weekday_corrections': weekday_corrections,
        'unit_corrections': unit_corrections,
        'confidence': confidence,
    }
