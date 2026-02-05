#!/usr/bin/env python3
"""日次データ蓄積 — 毎日のdaily JSONから台ごとの履歴を蓄積する

data/history/{store_key}/{unit_id}.json に追記していく。
これにより、外部ソースの保持期間（7-14日）を超えた長期データが使える。
"""
import json
import os
from datetime import datetime
from pathlib import Path

HISTORY_DIR = Path(__file__).parent.parent / 'data' / 'history'

RENCHAIN_THRESHOLD = 100  # AT間100G以内なら連チャン


def _calc_history_stats(history: list) -> tuple:
    """当たり履歴から最大連チャン・最大獲得枚数（連チャン合計）を計算
    
    max_medals = 1回の連チャン中に獲得した合計枚数の最大値
    （個別hitの最大ではなく、連チャン塊としての合計）
    """
    if not history:
        return 0, 0

    sorted_hist = sorted(history, key=lambda x: x.get('time', '00:00'))

    # 連チャン計算 + 連チャン中の合計枚数
    chain_len = 0
    max_chain = 0
    accumulated_games = 0
    chain_medals = 0      # 現在の連チャン中の累計枚数
    max_medals = 0        # 連チャン合計枚数の最大値

    for i, hit in enumerate(sorted_hist):
        hit_type = hit.get('type', 'ART')
        start = hit.get('start', 0)
        medals = hit.get('medals', 0)
        accumulated_games += start

        if hit_type in ('ART', 'AT', 'BIG'):
            if i == 0 or accumulated_games > RENCHAIN_THRESHOLD:
                # 前の連チャンが終了 → 記録
                if chain_len > 0:
                    max_chain = max(max_chain, chain_len)
                    max_medals = max(max_medals, chain_medals)
                chain_len = 1
                chain_medals = medals
            else:
                chain_len += 1
                chain_medals += medals
            accumulated_games = 0

    # 最後の連チャンを記録
    if chain_len > 0:
        max_chain = max(max_chain, chain_len)
        max_medals = max(max_medals, chain_medals)

    return max_chain, max_medals


def accumulate_from_daily(daily_data: dict, machine_key: str = 'sbj'):
    """daily JSONデータから各台の履歴を蓄積する

    Args:
        daily_data: load_daily_data()の戻り値
        machine_key: 機種キー

    Returns:
        {'new_entries': int, 'updated_units': int}
    """
    stores = daily_data.get('stores', {})
    new_entries = 0
    updated_units = 0

    for store_key, store_data in stores.items():
        units = store_data.get('units', [])
        for unit_data in units:
            unit_id = str(unit_data.get('unit_id', ''))
            days = unit_data.get('days', [])
            if not unit_id or not days:
                continue

            added = _accumulate_unit(store_key, unit_id, days, machine_key)
            if added > 0:
                updated_units += 1
                new_entries += added

    return {'new_entries': new_entries, 'updated_units': updated_units}


def accumulate_from_availability(avail_data: dict, target_date: str = None):
    """availability.jsonからtoday_historyを蓄積する

    当日のtoday_historyを蓄積DBに保存。
    閉店後に実行すると、その日のhistoryが保存される。

    Args:
        avail_data: availability.jsonの内容
        target_date: 保存先の日付（YYYY-MM-DD形式）。Noneの場合はfetched_atから取得

    Returns:
        {'new_entries': int, 'updated_units': int}
    """
    from datetime import datetime
    import pytz
    JST = pytz.timezone('Asia/Tokyo')
    
    # 日付を決定
    if not target_date:
        fetched_at = avail_data.get('fetched_at', '')
        if fetched_at:
            try:
                dt = datetime.fromisoformat(fetched_at.replace('Z', '+00:00'))
                target_date = dt.astimezone(JST).strftime('%Y-%m-%d')
            except Exception:
                target_date = datetime.now(JST).strftime('%Y-%m-%d')
        else:
            target_date = datetime.now(JST).strftime('%Y-%m-%d')
    
    stores = avail_data.get('stores', {})
    new_entries = 0
    updated_units = 0
    
    for store_key, store_data in stores.items():
        units = store_data.get('units', [])
        
        # store_keyのマッピング（_hokuto → _hokuto_tensei2）
        # availability.jsonでは_hokutoだが、蓄積DBでは_hokuto_tensei2を使う
        acc_store_key = store_key
        if '_hokuto' in store_key and '_hokuto_tensei2' not in store_key:
            acc_store_key = store_key.replace('_hokuto', '_hokuto_tensei2')
        
        # store_keyから機種キーを推測
        machine_key = 'hokuto_tensei2' if 'hokuto' in store_key else 'sbj'
        
        for unit_data in units:
            unit_id = str(unit_data.get('unit_id', ''))
            today_history = unit_data.get('today_history', [])
            
            if not unit_id:
                continue
            
            # 日別データを作成
            art = unit_data.get('art', 0)
            games = unit_data.get('total_start', 0)
            
            # historyがあればスキップしない（art=0でもhistoryがあれば蓄積価値あり）
            if art == 0 and (games is None or games == 0) and not today_history:
                continue
            
            day_entry = {
                'date': target_date,
                'art': art,
                'rb': unit_data.get('rb', 0),
                'total_start': games,
                'games': games,
                'history': today_history,
                'max_medals': unit_data.get('max_medals', 0),
                'max_rensa': unit_data.get('today_max_rensa', 0),
            }
            
            added = _accumulate_unit(acc_store_key, unit_id, [day_entry], machine_key)
            if added > 0:
                updated_units += 1
                new_entries += added
    
    return {'new_entries': new_entries, 'updated_units': updated_units}


def _accumulate_unit(store_key: str, unit_id: str, days: list, machine_key: str) -> int:
    """1台分のデータを蓄積する"""
    store_dir = HISTORY_DIR / store_key
    store_dir.mkdir(parents=True, exist_ok=True)

    file_path = store_dir / f'{unit_id}.json'

    # 既存データ読み込み
    existing = {'store_key': store_key, 'unit_id': unit_id, 'machine_key': machine_key, 'days': []}
    if file_path.exists():
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    # 既存日付のマップ（更新用）
    existing_days_map = {d.get('date'): d for d in existing.get('days', []) if d.get('date')}

    # 新規日付の追加 + 既存日付のhistory更新
    added = 0
    updated = 0
    good_prob = 130 if machine_key == 'sbj' else 330

    for day in days:
        date = day.get('date', '')
        if not date:
            continue
        
        # 既存データがあり、historyが空で、新データにhistoryがある場合は更新
        if date in existing_days_map:
            existing_day = existing_days_map[date]
            new_history = day.get('history', [])
            if new_history and not existing_day.get('history'):
                existing_day['history'] = new_history
                # max_rensa, max_medalsも更新
                max_rensa, max_medals = _calc_history_stats(new_history)
                if max_rensa > 0:
                    existing_day['max_rensa'] = max_rensa
                if max_medals > 0:
                    existing_day['max_medals'] = max_medals
                updated += 1
            continue

        art = day.get('art', 0)
        games = day.get('total_start', 0) or day.get('games', 0)

        # スクレイピング失敗/空データのフィルタ:
        # art=0 かつ games=0 のデータは「取得失敗」と判断して蓄積しない
        # （本当にART 0回なら games>0 のはず＝稼働してるが当たってない）
        if art == 0 and (games is None or games == 0):
            continue

        prob = games / art if art > 0 and games > 0 else 0

        # diff_medalsの取得（複数キー対応）
        diff_medals = day.get('diff_medals') or day.get('diff') or day.get('sashi') or 0
        
        entry = {
            'date': date,
            'art': art,
            'rb': day.get('rb', 0),
            'total_start': games,
            'games': games,
            'prob': round(prob, 1) if prob > 0 else 0,
            'diff_medals': diff_medals if diff_medals else None,
            # 試行回数が少ない日は信頼性が低いため好調判定しない
            # SBJ: ART 20回以上、北斗: ART 10回以上
            'is_good': prob > 0 and prob <= good_prob and art >= (20 if machine_key == 'sbj' else 10),
        }

        # 当たり履歴があれば最大連チャン・最大枚数を計算
        history = day.get('history', [])
        if history:
            entry['history'] = history
            max_rensa, max_medals = _calc_history_stats(history)
            if max_rensa > 0:
                entry['max_rensa'] = max_rensa
            if max_medals > 0:
                entry['max_medals'] = max_medals

        existing['days'].append(entry)
        added += 1

    if added > 0 or updated > 0:
        # 日付順ソート（古い順）
        existing['days'].sort(key=lambda x: x.get('date', ''))
        existing['last_updated'] = datetime.now().isoformat()

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(existing, f, ensure_ascii=False, indent=1)

    return added + updated


def load_unit_history(store_key: str, unit_id: str) -> dict:
    """蓄積済みの台履歴を読み込む

    store_keyが完全一致しない場合、サフィックス違いも試す
    例: island_akihabara_hokuto → island_akihabara_hokuto_tensei2
    """
    # 完全一致
    file_path = HISTORY_DIR / store_key / f'{unit_id}.json'
    if file_path.exists():
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    # store_keyが部分一致するディレクトリを探す
    if HISTORY_DIR.exists():
        for d in HISTORY_DIR.iterdir():
            if d.is_dir() and (d.name.startswith(store_key) or store_key.startswith(d.name)):
                fp = d / f'{unit_id}.json'
                if fp.exists():
                    try:
                        with open(fp, 'r', encoding='utf-8') as f:
                            return json.load(f)
                    except (json.JSONDecodeError, IOError):
                        pass

    return {'store_key': store_key, 'unit_id': unit_id, 'days': []}


def get_analysis_phase(unit_history: dict) -> int:
    """蓄積日数に応じた分析フェーズを返す

    Phase 1: 1-7日（基本分析）
    Phase 2: 8-14日（設定変更周期・交互パターン）
    Phase 3: 15-21日（週間パターン・据え置き率）
    Phase 4: 22-30日（月間トレンド・回帰分析）
    Phase 5: 31日+（完全統計）
    """
    days = len(unit_history.get('days', []))
    if days >= 31:
        return 5
    if days >= 22:
        return 4
    if days >= 15:
        return 3
    if days >= 8:
        return 2
    return 1


def analyze_setting_change_cycle(unit_history: dict, machine_key: str = 'sbj') -> dict:
    """設定変更周期分析 — 不調N日後に好調になる確率

    Returns:
        {
            'bad_to_good': {1: {'total': 5, 'good': 4, 'rate': 0.8}, 2: {...}, ...},
            'good_to_good': {1: {'total': 6, 'good': 5, 'rate': 0.83}, ...},
            'avg_cycle': 3.2,  # 好調→不調→好調の平均周期日数
            'alternating_score': 0.7,  # 交互パターンスコア (0-1)
        }
    """
    days = unit_history.get('days', [])
    if len(days) < 3:
        return {}

    good_prob = 130 if machine_key == 'sbj' else 330

    # 日付順（古い→新しい）にソート済みのはず
    sorted_days = sorted(days, key=lambda x: x.get('date', ''))

    # 好調/不調のシーケンスを作成
    sequence = []
    for d in sorted_days:
        prob = d.get('prob') or 0
        if prob and prob > 0:
            sequence.append(prob <= good_prob)  # True=好調, False=不調

    if len(sequence) < 3:
        return {}

    # 不調N日連続後→翌日好調の確率
    bad_to_good = {}
    for max_streak in range(1, min(8, len(sequence))):
        total = 0
        good = 0
        for i in range(max_streak, len(sequence)):
            # i-max_streak ~ i-1 が全部不調か？
            all_bad = all(not sequence[j] for j in range(i - max_streak, i))
            # さらに、max_streak+1日前が好調か（連続不調の開始点）
            if max_streak < i and not all(not sequence[j] for j in range(i - max_streak - 1, i)):
                # ちょうどmax_streak日連続不調
                pass
            if all_bad:
                # i-max_streak-1日目が好調（=ちょうどN日不調の開始）
                if max_streak == i or (i > max_streak and sequence[i - max_streak - 1]):
                    total += 1
                    if sequence[i]:
                        good += 1
        if total > 0:
            bad_to_good[max_streak] = {
                'total': total, 'good': good,
                'rate': round(good / total, 2)
            }

    # 好調N日連続後→翌日も好調の確率
    good_to_good = {}
    for max_streak in range(1, min(8, len(sequence))):
        total = 0
        good = 0
        for i in range(max_streak, len(sequence)):
            all_good = all(sequence[j] for j in range(i - max_streak, i))
            if all_good:
                if max_streak == i or (i > max_streak and not sequence[i - max_streak - 1]):
                    total += 1
                    if sequence[i]:
                        good += 1
        if total > 0:
            good_to_good[max_streak] = {
                'total': total, 'good': good,
                'rate': round(good / total, 2)
            }

    # 交互パターンスコア
    alternations = 0
    for i in range(1, len(sequence)):
        if sequence[i] != sequence[i - 1]:
            alternations += 1
    alternating_score = round(alternations / (len(sequence) - 1), 2) if len(sequence) > 1 else 0

    # 好調→不調→好調の平均周期
    good_indices = [i for i, s in enumerate(sequence) if s]
    if len(good_indices) >= 2:
        gaps = [good_indices[i+1] - good_indices[i] for i in range(len(good_indices)-1)]
        avg_cycle = round(sum(gaps) / len(gaps), 1)
    else:
        avg_cycle = 0

    return {
        'bad_to_good': bad_to_good,
        'good_to_good': good_to_good,
        'alternating_score': alternating_score,
        'avg_cycle': avg_cycle,
        'total_days': len(sequence),
        'good_days': sum(1 for s in sequence if s),
        'phase': get_analysis_phase(unit_history),
    }


def analyze_weekday_pattern(unit_history: dict, machine_key: str = 'sbj') -> dict:
    """曜日別好調率（Phase 3+）"""
    days = unit_history.get('days', [])
    good_prob = 130 if machine_key == 'sbj' else 330

    weekday_stats = {i: {'total': 0, 'good': 0} for i in range(7)}

    for d in days:
        date_str = d.get('date', '')
        prob = d.get('prob', 0)
        if not date_str or not prob or prob <= 0:
            continue
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            wd = dt.weekday()
            weekday_stats[wd]['total'] += 1
            if prob <= good_prob:
                weekday_stats[wd]['good'] += 1
        except ValueError:
            continue

    result = {}
    weekday_names = ['月', '火', '水', '木', '金', '土', '日']
    for wd, stats in weekday_stats.items():
        if stats['total'] > 0:
            result[weekday_names[wd]] = {
                'total': stats['total'],
                'good': stats['good'],
                'rate': round(stats['good'] / stats['total'], 2),
            }

    return result


if __name__ == '__main__':
    """直接実行で蓄積処理を実行"""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from analysis.recommender import load_daily_data
    from config.rankings import MACHINES

    total_new = 0
    total_units = 0

    for machine_key in MACHINES:
        daily = load_daily_data(machine_key=machine_key)
        if daily:
            result = accumulate_from_daily(daily, machine_key)
            total_new += result['new_entries']
            total_units += result['updated_units']
            print(f"{machine_key}: {result['new_entries']}件追加 ({result['updated_units']}台)")

    print(f"\n合計: {total_new}件追加 ({total_units}台)")

    # サンプル分析
    sample_dirs = list(HISTORY_DIR.iterdir())
    if sample_dirs:
        store_dir = sample_dirs[0]
        files = list(store_dir.glob('*.json'))
        if files:
            hist = load_unit_history(store_dir.name, files[0].stem)
            phase = get_analysis_phase(hist)
            print(f"\nサンプル: {store_dir.name}/{files[0].stem}")
            print(f"  蓄積日数: {len(hist.get('days', []))} → Phase {phase}")

            cycle = analyze_setting_change_cycle(hist, 'sbj')
            if cycle:
                print(f"  交互スコア: {cycle.get('alternating_score', 0)}")
                print(f"  平均周期: {cycle.get('avg_cycle', 0)}日")
                btg = cycle.get('bad_to_good', {})
                for n, stats in btg.items():
                    print(f"  {n}日不調→翌日好調: {stats['good']}/{stats['total']}回 ({stats['rate']:.0%})")
