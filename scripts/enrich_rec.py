"""
全recのデータ補完を一箇所で行う共通モジュール。
generate_static.pyから呼ばれる。

補完対象:
- yesterday_diff_medals / yesterday_max_rensa / yesterday_max_medals
- day_before_diff_medals / day_before_max_rensa / day_before_max_medals
- three_days_ago_diff_medals / three_days_ago_max_rensa / three_days_ago_max_medals
- recent_days[].diff_medals / max_rensa / max_medals
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def enrich_recs(recs):
    """
    全recの蓄積DB補完を一括で行う。
    generate_static.pyで全recを生成した後に1回だけ呼ぶ。
    """
    from analysis.history_accumulator import load_unit_history, _calc_history_stats

    # キャッシュ: (store_key, unit_id) -> days_by_date
    _cache = {}

    for rec in recs:
        store_key = rec.get('store_key', '')
        unit_id = str(rec.get('unit_id', ''))
        if not store_key or not unit_id:
            continue

        cache_key = (store_key, unit_id)
        if cache_key not in _cache:
            try:
                acc = load_unit_history(store_key, unit_id)
                if acc and acc.get('days'):
                    _cache[cache_key] = {d['date']: d for d in acc['days'] if d.get('date')}
                else:
                    _cache[cache_key] = {}
            except Exception:
                _cache[cache_key] = {}

        days_by_date = _cache[cache_key]
        if not days_by_date:
            continue

        # 1. 前日/前々日/3日前の補完
        _enrich_day_prefix(rec, days_by_date, 'yesterday_', 'yesterday_date')
        _enrich_day_prefix(rec, days_by_date, 'day_before_', 'day_before_date')
        _enrich_day_prefix(rec, days_by_date, 'three_days_ago_', 'three_days_ago_date')

        # 2. recent_daysを蓄積DBから常に補完（recommenderからのデータが不完全な場合があるため）
        from datetime import datetime, timedelta, timezone
        JST = timezone(timedelta(hours=9))
        existing_days = {d.get('date'): d for d in rec.get('recent_days', []) if d.get('date')}
        recent_days = []
        for i in range(1, 8):
            d = (datetime.now(JST) - timedelta(days=i)).strftime('%Y-%m-%d')
            # 蓄積DBを優先、なければ既存から
            acc_data = days_by_date.get(d)
            exist_data = existing_days.get(d)
            
            # 蓄積DBがあればそれを使う、なければ既存データ
            if acc_data:
                day_data = acc_data
            elif exist_data:
                day_data = exist_data
            else:
                day_data = None
            
            # art>0かつgames>0がある日のみ表示（不完全データはスキップ）
            if not day_data:
                continue
            day_art = day_data.get('art', 0)
            day_games = day_data.get('games', 0) or day_data.get('total_start', 0)
            if day_art > 0 and day_games > 0:
                # 蓄積DBと既存データをマージ（既存データにdiff_medalsがあればそれを使う）
                diff_medals = day_data.get('diff_medals')
                max_rensa = day_data.get('max_rensa')
                max_medals = day_data.get('max_medals')
                history = day_data.get('history', [])
                art = day_data.get('art', 0)
                games = day_data.get('games', 0) or day_data.get('total_start', 0)
                prob = day_data.get('prob', 0)
                
                # games=0だがart>0の場合、historyから再計算
                if art > 0 and games == 0 and history:
                    # gamesはhistoryの先頭のtotal_startまたはstartの累計から推定
                    # 最も簡単な方法: artとprobの仕様値から逆算（概算）
                    # または、historyの累計Gを計算
                    total_g = sum(h.get('start', 0) for h in history)
                    if total_g > 0:
                        games = total_g
                        prob = games / art if art > 0 else 0
                
                # max_rensaがない場合、historyから計算
                if not max_rensa and history:
                    max_rensa, _ = _calc_history_stats(history)
                
                # max_medals: historyから連チャン合計枚数を計算し、保存値と大きい方を使用
                # 保存値 > calc の場合: Papimoサマリの正確な値（7000+等）を優先
                # 保存値 < calc の場合: historyから正しく再計算（1hit最大値バグを修正）
                if history:
                    _, calc_m = _calc_history_stats(history)
                    if calc_m > 0:
                        max_medals = max(max_medals or 0, calc_m)
                
                # diff_medalsがない場合、historyから計算
                if diff_medals is None and history and games > 0:
                    total_medals = sum(h.get('medals', 0) for h in history)
                    invested = games * 3  # SBJは1G=3枚
                    diff_medals = total_medals - invested
                
                # 既存データがあれば補完（蓄積DBにデータがない/0の場合）
                if exist_data:
                    diff_medals = exist_data.get('diff_medals') if diff_medals is None else diff_medals
                    max_rensa = exist_data.get('max_rensa') if not max_rensa else max_rensa
                    max_medals = exist_data.get('max_medals') if not max_medals else max_medals
                    history = exist_data.get('history', []) if not history else history
                
                recent_days.append({
                    'date': d,
                    'art': art,
                    'games': games,
                    'prob': prob,
                    'diff_medals': diff_medals,
                    'max_rensa': max_rensa,
                    'max_medals': max_medals,
                    'history': history,
                })
        rec['recent_days'] = recent_days


def _enrich_day_prefix(rec, days_by_date, prefix, date_key):
    """rec[prefix + 'diff_medals'] 等を蓄積DBから補完"""
    from datetime import datetime, timedelta
    
    target_date = rec.get(date_key, '') or ''
    
    # 日付が空または無効な場合、蓄積DBから正しい日付を設定
    if not target_date or len(target_date) < 10:
        # prefixから何日前かを判定
        if prefix == 'yesterday_':
            days_ago = 1
        elif prefix == 'day_before_':
            days_ago = 2
        elif prefix == 'three_days_ago_':
            days_ago = 3
        else:
            return
        
        target_date = (datetime.now(JST) - timedelta(days=days_ago)).strftime('%Y-%m-%d')
        day_data = days_by_date.get(target_date)
        # art>0またはgames>0があれば日付を設定（空の日でも日付表示のため常に設定）
        rec[date_key] = target_date
        if day_data:
            rec[f'{prefix}art'] = day_data.get('art', 0)
            rec[f'{prefix}rb'] = day_data.get('rb', 0)
            rec[f'{prefix}games'] = day_data.get('games') or day_data.get('total_start', 0)
    
    day_data = days_by_date.get(target_date)
    if not day_data:
        return

    # historyの補完（蓄積DBから）
    if not rec.get(f'{prefix}history') or len(rec.get(f'{prefix}history', [])) == 0:
        db_history = day_data.get('history', [])
        if db_history:
            rec[f'{prefix}history'] = db_history
    
    # max_rensa/max_medalsの補完（蓄積DBを常に優先）
    hist = rec.get(f'{prefix}history', []) or day_data.get('history', [])
    db_rensa = day_data.get('max_rensa')
    if db_rensa:
        rec[f'{prefix}max_rensa'] = db_rensa
    elif not rec.get(f'{prefix}max_rensa') and hist:
        # 蓄積DBにない場合のみhistoryから計算
        from analysis.history_accumulator import _calc_history_stats
        max_rensa, max_medals = _calc_history_stats(hist)
        if max_rensa > 0:
            rec[f'{prefix}max_rensa'] = max_rensa
        if max_medals > 0:
            rec[f'{prefix}max_medals'] = max_medals
    
    # max_medalsはhistoryから連チャン合計を計算（1hit最大値より正確）
    if hist and not rec.get(f'{prefix}max_medals'):
        from analysis.history_accumulator import _calc_history_stats
        _, calc_max = _calc_history_stats(hist)
        if calc_max > 0:
            rec[f'{prefix}max_medals'] = calc_max
    # 蓄積DBの値はフォールバック（histから計算できない場合のみ）
    if not rec.get(f'{prefix}max_medals'):
        db_max = day_data.get('max_medals')
        if db_max:
            rec[f'{prefix}max_medals'] = db_max

    # gamesの補完（historyから計算）
    if not rec.get(f'{prefix}games') or rec.get(f'{prefix}games') == 0:
        db_games = day_data.get('games') or day_data.get('total_start')
        if db_games and db_games > 0:
            rec[f'{prefix}games'] = int(db_games)
        else:
            # historyからstart合計を計算
            hist = rec.get(f'{prefix}history', []) or day_data.get('history', [])
            if hist:
                total_start = sum(h.get('start', 0) for h in hist)
                if total_start > 0:
                    rec[f'{prefix}games'] = total_start

    # diff_medalsは蓄積DBを常に優先（recommenderの推定値より正確）
    db_diff = day_data.get('diff_medals')
    if db_diff is not None:
        rec[f'{prefix}diff_medals'] = int(db_diff)
    elif not rec.get(f'{prefix}diff_medals'):
        # 蓄積DBにない場合のみhistoryから計算
        hist = rec.get(f'{prefix}history', []) or day_data.get('history', [])
        games = rec.get(f'{prefix}games', 0) or day_data.get('games', 0) or day_data.get('total_start', 0)
        if hist and games > 0:
            total_medals = sum(h.get('medals', 0) for h in hist)
            invested = games * 3
            diff = total_medals - invested
            if diff != 0:
                rec[f'{prefix}diff_medals'] = diff
        elif hist and games == 0:
            try:
                from analysis.diff_medals_estimator import estimate_diff_medals
                medals_total = sum(h.get('medals', 0) for h in hist)
                machine_key = rec.get('machine_key', 'sbj')
                estimated = estimate_diff_medals(medals_total, games, machine_key)
                if estimated != 0:
                    rec[f'{prefix}diff_medals'] = int(estimated)
            except Exception:
                pass

    if not rec.get(f'{prefix}max_rensa'):
        db_rensa = day_data.get('max_rensa')
        if db_rensa:
            rec[f'{prefix}max_rensa'] = db_rensa

    if not rec.get(f'{prefix}max_medals'):
        db_max = day_data.get('max_medals')
        if db_max:
            rec[f'{prefix}max_medals'] = db_max
            # DEBUG
            if target_date == '2026-02-13' and prefix == 'three_days_ago_':
                import sys
                print(f"[DEBUG enrich 237-240] overwriting with db_max={db_max}", file=sys.stderr)


def _enrich_day_dict(day_dict, day_data):
    """recent_daysの各日データを蓄積DBから補完"""
    if not day_dict.get('diff_medals'):
        db_diff = day_data.get('diff_medals')
        if db_diff is not None and db_diff != 0:
            day_dict['diff_medals'] = int(db_diff)

    if not day_dict.get('max_rensa'):
        db_rensa = day_data.get('max_rensa')
        if db_rensa:
            day_dict['max_rensa'] = db_rensa

    if not day_dict.get('max_medals'):
        # historyから連チャン合計を計算（1hit最大値より正確）
        hist = day_dict.get('history', []) or day_data.get('history', [])
        if hist:
            from analysis.history_accumulator import _calc_history_stats
            _, calc_max = _calc_history_stats(hist)
            if calc_max > 0:
                day_dict['max_medals'] = calc_max
        # フォールバック: historyがない場合のみDBの値を使用
        if not day_dict.get('max_medals'):
            db_max = day_data.get('max_medals')
            if db_max:
                day_dict['max_medals'] = db_max
