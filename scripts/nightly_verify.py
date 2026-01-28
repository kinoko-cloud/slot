#!/usr/bin/env python3
"""
夜間答え合わせスクリプト
予測ランク vs 実際のART確率を比較し、的中率を算出する
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.rankings import STORES, RANKINGS, MACHINES, get_stores_by_machine
from analysis.recommender import recommend_units, MACHINE_SPECS

JST = timezone(timedelta(hours=9))

def load_availability():
    """availability.jsonを読み込む"""
    path = PROJECT_ROOT / 'data' / 'availability.json'
    if not path.exists():
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_actual_data(availability, store_key):
    """availability.jsonから店舗の実績データを取得"""
    stores = availability.get('stores', {})
    return stores.get(store_key, {})

def evaluate_prediction(predicted_rank, actual_prob, machine_key='sbj'):
    """予測ランク vs 実際のART確率で的中判定
    
    的中条件:
    - S/A予測 → 実際のART確率が1/130以下（SBJ）or 1/330以下（北斗）= 高設定域
    - C/D予測 → 実際のART確率が1/180以上（SBJ）or 1/366以上（北斗）= 低設定域
    """
    if machine_key == 'sbj':
        high_threshold = 130  # これ以下なら高設定
        mid_threshold = 180   # これ以上なら低設定
    else:
        high_threshold = 330
        mid_threshold = 366
    
    if actual_prob <= 0:
        return 'no_data'
    
    if predicted_rank in ('S', 'A'):
        if actual_prob <= high_threshold:
            return 'hit'  # 高設定予測 → 実際に高設定域
        elif actual_prob <= mid_threshold:
            return 'partial'  # 高設定予測 → 中間域（惜しい）
        else:
            return 'miss'  # 高設定予測 → 低設定域（外れ）
    elif predicted_rank in ('C', 'D'):
        if actual_prob >= mid_threshold:
            return 'hit'  # 低設定予測 → 実際に低設定域
        elif actual_prob >= high_threshold:
            return 'partial'  # 低設定予測 → 中間域
        else:
            return 'miss'  # 低設定予測 → 実際は高設定域
    else:  # B
        # B予測は中間なので判定甘め
        if high_threshold < actual_prob < mid_threshold + 50:
            return 'hit'
        else:
            return 'partial'


def run_verification():
    """全店舗の答え合わせを実行"""
    availability = load_availability()
    if not availability:
        return "availability.jsonが見つかりません。"
    
    fetched_at = availability.get('fetched_at', '')
    
    # SBJ店舗のみ対象（ランキングデータがあるもの）
    # 全ストアを対象に（SBJ + 北斗）
    target_stores = [sk for sk in STORES if sk not in ('island_akihabara', 'shibuya_espass', 'shinjuku_espass')]
    
    all_results = []
    store_summaries = []
    
    for store_key in target_stores:
        store = STORES.get(store_key)
        if not store:
            continue
        
        store_name = store.get('short_name', store.get('name', store_key))
        machine_key = store.get('machine', 'sbj')
        
        # 予測を取得
        predictions = recommend_units(store_key)
        
        # 実績データを取得
        actual_store = get_actual_data(availability, store_key)
        actual_units = {str(u.get('unit_id')): u for u in actual_store.get('units', [])}
        
        hits = 0
        partials = 0
        misses = 0
        no_data = 0
        sa_count = 0  # S/A予測数
        sa_hits = 0  # S/A予測的中数
        
        unit_results = []
        
        for pred in predictions:
            uid = pred['unit_id']
            pred_rank = pred['final_rank']
            pred_score = pred['final_score']
            
            actual = actual_units.get(uid, {})
            art = actual.get('art', 0)
            total = actual.get('total_start', 0)
            actual_prob = total / art if art > 0 else 0
            
            result = evaluate_prediction(pred_rank, actual_prob, machine_key)
            
            unit_result = {
                'unit_id': uid,
                'store_key': store_key,
                'store_name': store_name,
                'pred_rank': pred_rank,
                'pred_score': pred_score,
                'art': art,
                'total_start': total,
                'actual_prob': actual_prob,
                'result': result,
                'reasons': pred.get('reasons', []),
            }
            unit_results.append(unit_result)
            all_results.append(unit_result)
            
            if result == 'hit':
                hits += 1
            elif result == 'partial':
                partials += 1
            elif result == 'miss':
                misses += 1
            else:
                no_data += 1
            
            if pred_rank in ('S', 'A'):
                sa_count += 1
                if result == 'hit':
                    sa_hits += 1
        
        total_judged = hits + partials + misses
        hit_rate = hits / total_judged * 100 if total_judged > 0 else 0
        sa_hit_rate = sa_hits / sa_count * 100 if sa_count > 0 else 0
        
        store_summaries.append({
            'store_name': store_name,
            'store_key': store_key,
            'total': len(predictions),
            'hits': hits,
            'partials': partials,
            'misses': misses,
            'no_data': no_data,
            'hit_rate': hit_rate,
            'sa_count': sa_count,
            'sa_hits': sa_hits,
            'sa_hit_rate': sa_hit_rate,
            'unit_results': unit_results,
        })
    
    # 全体集計
    total_hits = sum(s['hits'] for s in store_summaries)
    total_partials = sum(s['partials'] for s in store_summaries)
    total_misses = sum(s['misses'] for s in store_summaries)
    total_judged = total_hits + total_partials + total_misses
    total_sa = sum(s['sa_count'] for s in store_summaries)
    total_sa_hits = sum(s['sa_hits'] for s in store_summaries)
    
    overall_hit_rate = total_hits / total_judged * 100 if total_judged > 0 else 0
    overall_sa_rate = total_sa_hits / total_sa * 100 if total_sa > 0 else 0
    
    # 外れた台の分析
    missed_units = [r for r in all_results if r['result'] == 'miss']
    missed_sa = [r for r in missed_units if r['pred_rank'] in ('S', 'A')]
    
    # レポート生成
    report = generate_report(
        fetched_at, store_summaries, all_results,
        total_hits, total_partials, total_misses, total_judged,
        total_sa, total_sa_hits, overall_hit_rate, overall_sa_rate,
        missed_sa
    )
    
    return report


def generate_report(fetched_at, store_summaries, all_results,
                    total_hits, total_partials, total_misses, total_judged,
                    total_sa, total_sa_hits, overall_hit_rate, overall_sa_rate,
                    missed_sa):
    """WhatsApp向けレポート生成"""
    
    now = datetime.now(JST)
    date_str = now.strftime('%m/%d(%a)')
    
    lines = []
    lines.append(f"🎰 {date_str} 答え合わせ")
    lines.append("")
    
    # 全体サマリー
    lines.append(f"📊 *全体結果*")
    lines.append(f"的中: {total_hits}/{total_judged} ({overall_hit_rate:.0f}%)")
    lines.append(f"S/A予測的中: {total_sa_hits}/{total_sa} ({overall_sa_rate:.0f}%)")
    lines.append(f"惜しい: {total_partials} / 外れ: {total_misses}")
    lines.append("")
    
    # 店舗別
    lines.append("📍 *店舗別*")
    for s in store_summaries:
        if s['total'] == 0:
            continue
        # 店舗名を短縮
        name = s['store_name']
        emoji = "✅" if s['sa_hit_rate'] >= 50 else "⚠️" if s['sa_hit_rate'] >= 30 else "❌"
        lines.append(f"{emoji} {name}")
        
        # S/A予測台の結果を表示
        sa_units = [u for u in s['unit_results'] if u['pred_rank'] in ('S', 'A')]
        other_units = [u for u in s['unit_results'] if u['pred_rank'] not in ('S', 'A')]
        
        if sa_units:
            for u in sa_units:
                prob_str = f"1/{u['actual_prob']:.0f}" if u['actual_prob'] > 0 else "未稼働"
                mark = "◎" if u['result'] == 'hit' else "△" if u['result'] == 'partial' else "✗"
                lines.append(f"  {mark} {u['unit_id']}番 [{u['pred_rank']}] → {prob_str} (ART{u['art']}回/{u['total_start']}G)")
        
        # B以下で実は高設定だった台（サプライズ）
        surprises = [u for u in other_units if u['actual_prob'] > 0 and u['actual_prob'] <= 130]
        if surprises:
            for u in surprises:
                lines.append(f"  💡 {u['unit_id']}番 [{u['pred_rank']}] → 1/{u['actual_prob']:.0f} (予想外の高設定)")
        
        lines.append("")
    
    # 外れた台の分析
    if missed_sa:
        lines.append("🔍 *S/A外れ分析*")
        for u in missed_sa:
            prob_str = f"1/{u['actual_prob']:.0f}" if u['actual_prob'] > 0 else "?"
            lines.append(f"  {u['store_name']} {u['unit_id']}番 [{u['pred_rank']}] → {prob_str}")
            # 外れ理由を推測
            if u['actual_prob'] > 250:
                lines.append(f"    → 設定1域。前日データに騙された可能性")
            elif u['actual_prob'] > 180:
                lines.append(f"    → 低設定域。設定変更が入った可能性")
            
            if u.get('reasons'):
                lines.append(f"    予測根拠: {u['reasons'][0]}")
        lines.append("")
    
    # 教訓
    # 最も的中率の高い/低い店舗
    valid_stores = [s for s in store_summaries if s['sa_count'] > 0]
    if valid_stores:
        best = max(valid_stores, key=lambda s: s['sa_hit_rate'])
        worst = min(valid_stores, key=lambda s: s['sa_hit_rate'])
        lines.append("💡 *所感*")
        if best['sa_hit_rate'] > 0:
            lines.append(f"好調: {best['store_name']} (S/A的中 {best['sa_hits']}/{best['sa_count']})")
        if worst['sa_hit_rate'] < 50 and worst != best:
            lines.append(f"苦戦: {worst['store_name']} (S/A的中 {worst['sa_hits']}/{worst['sa_count']})")
    
    return "\n".join(lines)


if __name__ == '__main__':
    report = run_verification()
    print(report)
