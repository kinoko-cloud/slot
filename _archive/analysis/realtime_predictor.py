#!/usr/bin/env python3
"""
SBJ リアルタイム予測システム

営業中の台選びをサポート:
1. 過去データに基づく台の実力評価
2. 当日の稼働状況からの推測
3. 未稼働・低稼働台の隠れ高設定発見
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# 営業時間
OPEN_TIME = "10:00"
CLOSE_TIME = "22:40"


def time_to_minutes(time_str: str) -> int:
    """時間を分に変換（10:00起点）"""
    h, m = map(int, time_str.split(':'))
    return (h - 10) * 60 + m


def get_current_time_minutes() -> int:
    """現在時刻を分で取得"""
    now = datetime.now()
    return (now.hour - 10) * 60 + now.minute


def load_historical_data(hall_name: str) -> list:
    """過去データを読み込み"""
    data_dir = Path('data/raw')
    all_units = []

    # 最新のデータファイルを探す
    for pattern in ['sbj_all_history_*.json', 'papimo_island_sbj_*.json']:
        files = sorted(data_dir.glob(pattern), reverse=True)
        if files:
            with open(files[0], 'r', encoding='utf-8') as f:
                data = json.load(f)
                for unit in data:
                    if hall_name in unit.get('hall_name', ''):
                        all_units.append(unit)

    return all_units


def calculate_unit_strength(unit_data: dict) -> dict:
    """台の実力を計算（過去データベース）"""
    days = unit_data.get('days', [])
    if not days:
        return {'strength': 0, 'confidence': 0}

    # 過去の成績を集計
    total_art = 0
    total_games = 0
    good_days = 0  # ART確率1/200以下の日

    for day in days:
        art = day.get('art', 0)
        games = day.get('total_start', 0)
        total_art += art
        total_games += games

        if games > 0 and art > 0:
            prob = games / art
            if prob <= 200:
                good_days += 1

    # 実力スコア
    if total_games > 0 and total_art > 0:
        avg_prob = total_games / total_art
        strength = max(0, 100 - (avg_prob - 100) / 2)  # 1/100で100点、1/200で50点
    else:
        strength = 0

    return {
        'strength': strength,
        'total_art': total_art,
        'total_games': total_games,
        'avg_prob': total_games / total_art if total_art > 0 else 0,
        'good_days': good_days,
        'total_days': len(days),
        'confidence': min(100, len(days) * 10),  # データ量による信頼度
    }


def analyze_current_day(current_data: dict, historical_strength: dict) -> dict:
    """当日データを分析して予測"""
    result = {
        'unit_id': current_data.get('unit_id'),
        'status': 'unknown',
        'recommendation': '',
        'reasons': [],
        'priority': 0,  # 優先度（高いほど狙い目）
    }

    current_games = current_data.get('total_start', 0)
    current_art = current_data.get('art', 0)
    history = current_data.get('history', [])
    current_minutes = get_current_time_minutes()

    # 1. 稼働状況の判定
    if current_games == 0:
        result['status'] = 'unused'
        result['reasons'].append('未稼働')
    elif current_games < 500:
        result['status'] = 'low_activity'
        result['reasons'].append(f'低稼働 ({current_games}G)')
    elif current_games < 2000:
        result['status'] = 'moderate'
        result['reasons'].append(f'中程度稼働 ({current_games}G)')
    else:
        result['status'] = 'high_activity'
        result['reasons'].append(f'高稼働 ({current_games}G)')

    # 2. 当日ART確率
    if current_games > 0 and current_art > 0:
        current_prob = current_games / current_art
        result['current_prob'] = current_prob

        if current_prob <= 150:
            result['reasons'].append(f'★ 本日好調 (1/{current_prob:.0f})')
            result['priority'] += 30
        elif current_prob <= 200:
            result['reasons'].append(f'本日良好 (1/{current_prob:.0f})')
            result['priority'] += 15
        elif current_prob >= 300:
            result['reasons'].append(f'本日不調 (1/{current_prob:.0f})')
            result['priority'] -= 10

    # 3. 当たり時間のパターン分析
    if history:
        hit_times = [time_to_minutes(h.get('time', '10:00')) for h in history]
        first_hit = min(hit_times)
        last_hit = max(hit_times)

        # 早い時間から当たっている = 朝から狙われている可能性
        if first_hit <= 60:  # 11時前
            result['reasons'].append(f'朝から稼働 (初当たり {10 + first_hit // 60}:{first_hit % 60:02d})')
            # 朝から好調なら優先度アップ、ただし既に取られている可能性も
            if current_prob and current_prob <= 200:
                result['priority'] += 10
                result['reasons'].append('→ 高設定を先行者が確保中の可能性')

        # 最近当たったか
        if current_minutes - last_hit <= 30:
            result['reasons'].append('直近30分以内に当たり')
            result['priority'] += 5

    # 4. 過去実績との組み合わせ
    strength = historical_strength.get('strength', 0)
    result['historical_strength'] = strength

    if result['status'] == 'unused':
        # 未稼働台の評価
        if strength >= 70:
            result['recommendation'] = '★ 狙い目: 実績良好台が空いている'
            result['priority'] += 40
        elif strength >= 50:
            result['recommendation'] = '△ 様子見: まずまずの台が空き'
            result['priority'] += 20
        else:
            result['recommendation'] = '× 見送り: 実績が低い'
            result['priority'] -= 10

    elif result['status'] == 'low_activity':
        # 低稼働台の評価
        if strength >= 70:
            if current_art > 0 and current_prob <= 200:
                result['recommendation'] = '★★ 狙い目: 実績良好 + 本日も好調'
                result['priority'] += 50
            else:
                result['recommendation'] = '★ 狙い目: 実績良好台が低稼働'
                result['priority'] += 30
        elif strength >= 50:
            result['recommendation'] = '△ 様子見'
            result['priority'] += 10

    elif result['status'] == 'high_activity':
        # 高稼働台の評価
        if current_art > 0 and current_prob <= 150:
            result['recommendation'] = '◎ 好調継続中（空いたら狙い）'
            result['priority'] += 20
        else:
            result['recommendation'] = '→ 他の台を優先'
            result['priority'] -= 5

    return result


def generate_realtime_report(hall_name: str, current_day_data: list) -> str:
    """リアルタイム予測レポートを生成"""
    report = []
    now = datetime.now()
    current_minutes = get_current_time_minutes()

    report.append("=" * 70)
    report.append(f"SBJ リアルタイム予測 - {hall_name}")
    report.append(f"更新時刻: {now.strftime('%Y-%m-%d %H:%M')}")
    report.append(f"営業経過: {current_minutes // 60}時間{current_minutes % 60}分")
    report.append("=" * 70)

    # 過去データを読み込み
    historical = load_historical_data(hall_name)
    historical_dict = {u['unit_id']: u for u in historical}

    # 各台を分析
    predictions = []
    for current in current_day_data:
        unit_id = current.get('unit_id')
        hist = historical_dict.get(unit_id, {})
        strength = calculate_unit_strength(hist)
        prediction = analyze_current_day(current, strength)
        predictions.append(prediction)

    # 優先度順にソート
    predictions.sort(key=lambda x: x['priority'], reverse=True)

    # 狙い目台
    report.append("")
    report.append("【狙い目ランキング】")
    report.append("")

    for i, pred in enumerate(predictions[:5], 1):
        unit_id = pred['unit_id']
        priority = pred['priority']
        rec = pred.get('recommendation', '')
        status = pred['status']

        report.append(f"{i}. 台{unit_id} (優先度: {priority})")
        report.append(f"   状態: {status}")
        if rec:
            report.append(f"   {rec}")
        for reason in pred.get('reasons', [])[:3]:
            report.append(f"   - {reason}")
        report.append("")

    # 要注意台（高稼働で好調）
    report.append("-" * 70)
    report.append("【高稼働好調台】（空いたら狙い）")
    hot_machines = [p for p in predictions if p['status'] == 'high_activity' and p.get('current_prob', 999) <= 180]
    if hot_machines:
        for pred in hot_machines[:3]:
            prob = pred.get('current_prob', 0)
            report.append(f"  台{pred['unit_id']}: 1/{prob:.0f} - 好調継続中")
    else:
        report.append("  なし")

    # 未稼働台一覧
    report.append("")
    report.append("-" * 70)
    report.append("【未稼働・低稼働台】")
    unused = [p for p in predictions if p['status'] in ['unused', 'low_activity']]
    if unused:
        for pred in unused:
            strength = pred.get('historical_strength', 0)
            report.append(f"  台{pred['unit_id']}: 実績スコア {strength:.0f}")
    else:
        report.append("  なし（全台稼働中）")

    report.append("")
    report.append("=" * 70)

    return "\n".join(report)


def main():
    """テスト実行"""
    # サンプル: 当日データを読み込み（実際はリアルタイムスクレイピング）
    print("リアルタイム予測システム テスト")
    print("=" * 50)

    # 既存データを当日データとして使用（テスト用）
    data_path = Path('data/raw/papimo_island_sbj_20260126_1237.json')
    if data_path.exists():
        with open(data_path, 'r', encoding='utf-8') as f:
            all_units = json.load(f)

        # 最新日のデータを当日データとして抽出
        current_day_data = []
        for unit in all_units:
            days = unit.get('days', [])
            if days:
                latest = days[0]  # 最新日
                current_day_data.append({
                    'unit_id': unit['unit_id'],
                    'total_start': latest.get('total_start', 0),
                    'art': latest.get('art', 0),
                    'history': latest.get('history', []),
                })

        report = generate_realtime_report('アイランド秋葉原', current_day_data)
        print(report)

        # レポート保存
        report_path = Path('data/raw') / f'realtime_prediction_{datetime.now().strftime("%Y%m%d_%H%M")}.txt'
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"\n✓ 保存: {report_path}")


if __name__ == "__main__":
    main()
