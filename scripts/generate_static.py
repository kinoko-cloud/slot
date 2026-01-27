#!/usr/bin/env python3
"""
静的サイト生成スクリプト

Cloudflare Pages用に静的HTMLを生成する
GitHub Actionsで定期実行し、生成したHTMLをデプロイ
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# プロジェクトルート
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from jinja2 import Environment, FileSystemLoader
from config.rankings import STORES, MACHINES, get_stores_by_machine, get_machine_info
from analysis.recommender import recommend_units, load_daily_data, generate_store_analysis, calculate_expected_profit, analyze_today_graph, calculate_at_intervals
from scrapers.availability_checker import get_availability, get_realtime_data
from scripts.verify_units import get_active_alerts, get_unit_status

JST = timezone(timedelta(hours=9))
WEEKDAY_NAMES = ['月', '火', '水', '木', '金', '土', '日']

# 出力ディレクトリ
OUTPUT_DIR = PROJECT_ROOT / 'docs'  # GitHub Pages互換


def get_display_mode():
    """現在時刻から表示モードを決定"""
    now = datetime.now(JST)
    hour = now.hour
    minute = now.minute

    if hour >= 23 or hour < 10:
        return 'result'
    elif hour == 22 and minute >= 50:
        return 'collecting'
    else:
        return 'realtime'


def format_date_with_weekday(dt):
    """日付を曜日付きでフォーマット"""
    weekday = WEEKDAY_NAMES[dt.weekday()]
    return f"{dt.month}月{dt.day}日({weekday})"


def is_business_hours():
    """営業時間内かどうか"""
    return get_display_mode() == 'realtime'


def rank_color(rank):
    """ランク色を返す"""
    colors = {
        'S': '#ff6b6b',
        'A': '#ffa502',
        'B': '#2ed573',
        'C': '#70a1ff',
        'D': '#747d8c',
    }
    return colors.get(rank, '#747d8c')


def signed_number(value):
    """符号付きカンマ区切り数値"""
    try:
        num = int(value)
        if num >= 0:
            return f'+{num:,}'
        else:
            return f'{num:,}'
    except (ValueError, TypeError):
        return str(value)


def medals_badge(value):
    """最大獲得枚数バッジ"""
    try:
        num = int(value)
        if num >= 10000:
            return {'class': 'medals-10k', 'icon': '🔥', 'label': '1万枚OVER'}
        elif num >= 5000:
            return {'class': 'medals-5k', 'icon': '💰', 'label': '5千枚OVER'}
        elif num >= 3000:
            return {'class': 'medals-3k', 'icon': '✨', 'label': '3千枚OVER'}
        elif num >= 2000:
            return {'class': 'medals-2k', 'icon': '⭐', 'label': '2千枚OVER'}
        elif num >= 1000:
            return {'class': 'medals-1k', 'icon': '👍', 'label': '1千枚OVER'}
        return None
    except (ValueError, TypeError):
        return None


def setup_jinja():
    """Jinja2環境をセットアップ"""
    template_dir = PROJECT_ROOT / 'web' / 'templates'
    env = Environment(loader=FileSystemLoader(str(template_dir)))

    # カスタムフィルタ・関数を追加
    env.globals['rank_color'] = rank_color
    env.globals['signed_number'] = signed_number
    env.globals['medals_badge'] = medals_badge
    env.globals['url_for'] = lambda endpoint, **kwargs: generate_url(endpoint, **kwargs)

    def pad_unit_id(uid):
        """台番号を4桁ゼロパディング（1→0001, 23→0023, 0752→0752）"""
        s = str(uid)
        if s.isdigit() and len(s) < 4:
            return s.zfill(4)
        return s
    env.filters['pad_id'] = pad_unit_id
    env.globals['pad_id'] = pad_unit_id

    def format_short_date(date_str):
        """'2026-01-26' → '1/26(月)'"""
        if not date_str:
            return ''
        try:
            dt = datetime.strptime(str(date_str), '%Y-%m-%d')
            return f"{dt.month}/{dt.day}({WEEKDAY_NAMES[dt.weekday()]})"
        except:
            return str(date_str)
    env.globals['short_date'] = format_short_date

    return env


def generate_url(endpoint, **kwargs):
    """静的サイト用のURL生成（絶対パス）"""
    if endpoint == 'index':
        return '/index.html'
    elif endpoint == 'static':
        return f"/static/{kwargs.get('filename', '')}"
    elif endpoint == 'recommend':
        return f"/recommend/{kwargs.get('store_key', '')}.html"
    elif endpoint == 'machine_stores':
        return f"/machine/{kwargs.get('machine_key', '')}.html"
    elif endpoint == 'ranking':
        return f"/ranking/{kwargs.get('machine_key', '')}.html"
    elif endpoint == 'rules':
        return '/rules.html'
    elif endpoint == 'unit_history':
        return f"/history/{kwargs.get('store_key', '')}_{kwargs.get('unit_id', '')}.html"
    elif endpoint == 'api_status':
        return f"https://autogmail.pythonanywhere.com/api/status/{kwargs.get('store_key', '')}"
    elif endpoint == 'verify':
        return '/verify.html'
    return '#'


def generate_index(env):
    """トップページを生成"""
    print("Generating index.html...")

    template = env.get_template('index.html')

    now = datetime.now(JST)
    display_mode = get_display_mode()
    is_open = is_business_hours()
    # 曜日傾向は常に「次に開店する日」の曜日
    # 22:45〜23:59は翌日、0:00〜09:59はその日（既に日付が変わっている）
    if is_open:
        today_weekday = WEEKDAY_NAMES[now.weekday()]
    elif now.hour >= 22:
        # 22:45〜23:59 → 翌日の曜日
        tomorrow_dt = now + timedelta(days=1)
        today_weekday = WEEKDAY_NAMES[tomorrow_dt.weekday()]
    else:
        # 0:00〜09:59 → 今日の曜日（日付は既に変わっている）
        today_weekday = WEEKDAY_NAMES[now.weekday()]
    today_date = now.strftime('%Y/%m/%d')
    today_date_formatted = format_date_with_weekday(now)

    # 理由文の日付ラベル
    reason_data_label, reason_prev_label = get_reason_date_labels()

    # 店舗曜日傾向（物理店舗ベース）
    store_day_ratings = {
        'island_akihabara': {
            'name': 'アイランド秋葉原',
            'short_name': 'アイランド秋葉原',
            'day_ratings': {'月': 4, '火': 3, '水': 5, '木': 3, '金': 3, '土': 1, '日': 4},
            'best_note': '水曜が最強、日月も狙い目',
            'worst_note': '土曜は避けるべき',
            'overall_rating': 4,
            'machine_links': [
                {'store_key': 'island_akihabara_sbj', 'icon': '🃏', 'short_name': 'SBJ'},
                {'store_key': 'island_akihabara_hokuto', 'icon': '👊', 'short_name': '北斗転生2'},
            ],
        },
        'shibuya_espass': {
            'name': 'エスパス日拓渋谷新館',
            'short_name': 'エスパス渋谷新館',
            'day_ratings': {'月': 3, '火': 4, '水': 4, '木': 5, '金': 3, '土': 3, '日': 1},
            'best_note': '木曜が最強、火水も狙い目',
            'worst_note': '日曜は避けるべき',
            'overall_rating': 3,
            'machine_links': [
                {'store_key': 'shibuya_espass_sbj', 'icon': '🃏', 'short_name': 'SBJ'},
                {'store_key': 'shibuya_espass_hokuto', 'icon': '👊', 'short_name': '北斗転生2'},
            ],
        },
        'shinjuku_espass': {
            'name': 'エスパス日拓新宿歌舞伎町店',
            'short_name': 'エスパス歌舞伎町',
            'day_ratings': {'月': 2, '火': 3, '水': 3, '木': 3, '金': 4, '土': 5, '日': 3},
            'best_note': '土曜が最強、金曜も狙い目',
            'worst_note': '月曜は控えめ',
            'overall_rating': 3,
            'machine_links': [
                {'store_key': 'shinjuku_espass_sbj', 'icon': '🃏', 'short_name': 'SBJ'},
                {'store_key': 'shinjuku_espass_hokuto', 'icon': '👊', 'short_name': '北斗転生2'},
            ],
        },
        'akihabara_espass': {
            'name': 'エスパス日拓秋葉原駅前店',
            'short_name': 'エスパス秋葉原',
            'day_ratings': {'月': 2, '火': 3, '水': 3, '木': 3, '金': 4, '土': 5, '日': 4},
            'best_note': '土日が狙い目、金曜も可',
            'worst_note': '月曜は控えめ',
            'overall_rating': 3,
            'machine_links': [
                {'store_key': 'akihabara_espass_sbj', 'icon': '🃏', 'short_name': 'SBJ'},
                {'store_key': 'akihabara_espass_hokuto', 'icon': '👊', 'short_name': '北斗転生2'},
            ],
        },
        'seibu_shinjuku_espass': {
            'name': 'エスパス日拓西武新宿駅前店',
            'short_name': 'エスパス西武新宿',
            'day_ratings': {'月': 2, '火': 2, '水': 3, '木': 3, '金': 4, '土': 4, '日': 3},
            'best_note': '金土が狙い目',
            'worst_note': '月火は控えめ',
            'overall_rating': 2,
            'machine_links': [
                {'store_key': 'seibu_shinjuku_espass_sbj', 'icon': '🃏', 'short_name': 'SBJ'},
            ],
        },
    }

    # 機種一覧とトップ台を収集
    machines = []
    top3_all = []
    yesterday_top10 = []
    today_top10 = []

    for key, machine in MACHINES.items():
        stores = get_stores_by_machine(key)
        total_units = sum(len(s['units']) for s in stores.values())
        machines.append({
            'key': key,
            'name': machine['name'],
            'short_name': machine['short_name'],
            'icon': machine['icon'],
            'store_count': len(stores),
            'unit_count': total_units,
        })

        for store_key, store in stores.items():
            try:
                availability = {}
                try:
                    availability = get_availability(store_key)
                except:
                    pass

                # リアルタイムデータ取得（本日のART/RB等）
                realtime = None
                try:
                    realtime = get_realtime_data(store_key)
                except:
                    pass

                recs = recommend_units(store_key, realtime_data=realtime, availability=availability,
                                      data_date_label=reason_data_label, prev_date_label=reason_prev_label)

                # 全recsにメタデータを付与
                for rec in recs:
                    rec['store_name'] = store.get('short_name', store['name'])
                    rec['store_key'] = store_key
                    rec['machine_key'] = key
                    rec['machine_icon'] = machine['icon']
                    rec['machine_name'] = machine.get('display_name', machine['short_name'])
                    if 'availability' not in rec or rec['availability'] is None:
                        rec['availability'] = availability.get(rec['unit_id'], '')
                    # 前日・前々日の差枚計算
                    y_art = rec.get('yesterday_art', 0)
                    y_games = rec.get('yesterday_games', 0)
                    if y_art and y_art > 0 and y_games and y_games > 0:
                        y_p = calculate_expected_profit(y_games, y_art, key)
                        rec['yesterday_diff_medals'] = y_p.get('current_estimate', 0)
                    db_art = rec.get('day_before_art', 0)
                    db_games = rec.get('day_before_games', 0)
                    if db_art and db_art > 0 and db_games and db_games > 0:
                        db_p = calculate_expected_profit(db_games, db_art, key)
                        rec['day_before_diff_medals'] = db_p.get('current_estimate', 0)
                    td_art = rec.get('three_days_ago_art', 0)
                    td_games = rec.get('three_days_ago_games', 0)
                    if td_art and td_art > 0 and td_games and td_games > 0:
                        td_p = calculate_expected_profit(td_games, td_art, key)
                        rec['three_days_ago_diff_medals'] = td_p.get('current_estimate', 0)

                # TOP3候補（上位3台/店舗）
                for rec in recs[:3]:
                    top3_all.append(rec)

                # 前日の爆発台（全台から収集、yesterday_art > 0）
                for rec in recs:
                    y_art = rec.get('yesterday_art', 0)
                    if y_art and y_art > 0:
                        y_games = rec.get('yesterday_games', 0)
                        y_prob = y_games / y_art if y_art > 0 and y_games > 0 else 0
                        # 差枚計算
                        y_diff_medals = 0
                        y_setting = ''
                        y_setting_num = 0
                        if y_art > 0 and y_games > 0:
                            y_profit = calculate_expected_profit(y_games, y_art, key)
                            y_diff_medals = y_profit.get('current_estimate', 0)
                            y_si = y_profit.get('setting_info', {})
                            y_setting = y_si.get('estimated_setting', '')
                            y_setting_num = y_si.get('setting_num', 0)
                        # 連チャン・天井・最大メダルを計算
                        y_max_rensa = rec.get('yesterday_max_rensa', 0) or rec.get('today_max_rensa', 0)
                        y_max_medals = rec.get('yesterday_max_medals', 0)
                        y_ceilings = 0
                        hist = rec.get('today_history', [])
                        if hist:
                            try:
                                graph = analyze_today_graph(hist)
                                y_max_rensa = max(y_max_rensa, graph.get('max_rensa', 0))
                                intervals = calculate_at_intervals(hist)
                                y_ceilings = sum(1 for g in intervals if g >= 999)
                                if not y_max_medals:
                                    y_max_medals = max((h.get('medals', 0) for h in hist), default=0)
                            except:
                                pass
                        # 蓄積DBからも補完
                        if not y_max_rensa or not y_max_medals:
                            try:
                                from analysis.history_accumulator import load_unit_history
                                acc_hist = load_unit_history(store_key, rec['unit_id'])
                                y_date = rec.get('yesterday_date', '')
                                for ad in acc_hist.get('days', []):
                                    if ad.get('date') == y_date or (not y_date and ad == acc_hist['days'][-1]):
                                        if not y_max_rensa:
                                            y_max_rensa = ad.get('max_rensa', 0)
                                        if not y_max_medals:
                                            y_max_medals = ad.get('max_medals', 0)
                                        break
                            except:
                                pass
                        yesterday_top10.append({
                            'unit_id': rec['unit_id'],
                            'store_name': rec['store_name'],
                            'store_key': store_key,
                            'machine_icon': machine['icon'],
                            'machine_name': machine.get('display_name', machine['short_name']),
                            'yesterday_art': y_art,
                            'yesterday_rb': rec.get('yesterday_rb', 0),
                            'yesterday_games': y_games,
                            'yesterday_max_rensa': y_max_rensa,
                            'yesterday_max_medals': y_max_medals,
                            'yesterday_ceilings': y_ceilings,
                            'yesterday_prob': y_prob,
                            'diff_medals': y_diff_medals,
                            'estimated_setting': y_setting,
                            'setting_num': y_setting_num,
                        })

                # 本日の爆発台（全台から収集、art_count > 0）
                for rec in recs:
                    t_art = rec.get('art_count', 0)
                    t_medals = rec.get('max_medals', 0)
                    t_games = rec.get('total_games', 0)
                    if t_art > 0 or t_medals > 0:
                        # 差枚計算
                        diff_medals = 0
                        if t_art > 0 and t_games > 0:
                            profit = calculate_expected_profit(t_games, t_art, key)
                            diff_medals = profit.get('current_estimate', 0)
                        today_top10.append({
                            'unit_id': rec['unit_id'],
                            'store_name': rec['store_name'],
                            'store_key': store_key,
                            'machine_icon': machine['icon'],
                            'machine_name': machine.get('display_name', machine['short_name']),
                            'art_count': t_art,
                            'rb_count': rec.get('rb_count', 0),
                            'total_games': t_games,
                            'max_medals': t_medals,
                            'art_prob': rec.get('art_prob', 0),
                            'availability': rec.get('availability', ''),
                            'estimated_setting': rec.get('estimated_setting', ''),
                            'setting_num': rec.get('setting_num', 0),
                            'payout_estimate': rec.get('payout_estimate', ''),
                            'today_max_rensa': rec.get('today_max_rensa', 0),
                            'diff_medals': diff_medals,
                        })
            except Exception as e:
                print(f"Error processing {store_key}: {e}")

    # ソート
    def top3_sort_key(r):
        score = r['final_score']
        if r.get('availability') == '空き':
            score += 10
        return -score

    top3_all.sort(key=top3_sort_key)
    top3 = top3_all[:3]

    # 前日の爆発台: 差枚でソート
    yesterday_top10.sort(key=lambda x: (-x.get('diff_medals', 0), -x['yesterday_art']))
    yesterday_top10 = yesterday_top10[:10]

    # 本日の爆発台: 差枚でソート（推定差枚の多い順）
    today_top10.sort(key=lambda x: (-x.get('diff_medals', 0), -x['max_medals']))
    today_top10 = today_top10[:10]

    # 曜日ランキング
    today_store_ranking = []
    for store_key, info in store_day_ratings.items():
        today_rating = info['day_ratings'].get(today_weekday, 3)
        today_store_ranking.append({
            'store_key': store_key,
            'name': info['name'],
            'short_name': info['short_name'],
            'today_rating': today_rating,
            'best_note': info['best_note'],
            'worst_note': info['worst_note'],
            'overall_rating': info['overall_rating'],
            'day_ratings': info['day_ratings'],
            'machine_links': info.get('machine_links', []),
        })
    today_store_ranking.sort(key=lambda x: -x['today_rating'])

    today_recommended_stores = [s for s in today_store_ranking if s['today_rating'] >= 4]
    today_avoid_stores = [s for s in today_store_ranking if s['today_rating'] <= 2]

    result_date_str = None
    date_prefix = ''  # 「昨日」or「本日」
    if display_mode in ('result', 'collecting'):
        if now.hour >= 23:
            result_date = now
            date_prefix = '本日'
        elif now.hour < 10:
            result_date = now - timedelta(days=1)
            date_prefix = '昨日'
        else:
            result_date = now - timedelta(days=1)
            date_prefix = '昨日'
        result_date_str = format_date_with_weekday(result_date)
    elif is_open:
        date_prefix = '本日'

    # 次の営業日（おすすめ台の対象日）
    if now.hour >= 22:
        # 22:45〜23:59 → 翌日
        next_day_dt = now + timedelta(days=1)
        next_day_prefix = '明日'
    elif now.hour < 10:
        # 0:00〜9:59 → 今日
        next_day_dt = now
        next_day_prefix = '本日'
    else:
        # 営業中 → 今日
        next_day_dt = now
        next_day_prefix = '本日'
    next_day_str = format_date_with_weekday(next_day_dt)

    # 全店舗一覧（店舗導線用）
    all_stores = []
    for store_key, info in store_day_ratings.items():
        today_rating = info['day_ratings'].get(today_weekday, 3)
        all_stores.append({
            'store_key': store_key,
            'name': info['name'],
            'short_name': info['short_name'],
            'today_rating': today_rating,
            'overall_rating': info['overall_rating'],
            'machine_links': info.get('machine_links', []),
        })
    # 今日の評価順でソート
    all_stores.sort(key=lambda x: (-x['today_rating'], -x['overall_rating']))

    night_mode = is_night_mode()
    tomorrow = now + timedelta(days=1)
    tomorrow_str = format_date_with_weekday(tomorrow)
    yesterday = now - timedelta(days=1)
    yesterday_str = format_date_with_weekday(yesterday)

    # 「本日」「前日」を日付付きに
    # データの日付はavailability.jsonのfetched_atから取得
    try:
        from scrapers.availability_checker import get_daidata_availability
        avail_data = get_daidata_availability()
        fetched_at = avail_data.get('fetched_at', '')
        if fetched_at:
            data_dt = datetime.fromisoformat(fetched_at)
            data_date_str = format_date_with_weekday(data_dt)
            prev_date_str = format_date_with_weekday(data_dt - timedelta(days=1))
        else:
            data_date_str = format_date_with_weekday(now)
            prev_date_str = format_date_with_weekday(yesterday)
    except:
        data_date_str = format_date_with_weekday(now)
        prev_date_str = format_date_with_weekday(yesterday)

    # 機種別的中率（ヒーロー表示用: 高い順に2つ）
    accuracy_hero = []
    for machine_key, machine in MACHINES.items():
        stores = get_stores_by_machine(machine_key)
        m_total = 0
        m_hit = 0
        store_results = []  # 店舗別の結果
        for store_key, store in stores.items():
            s_total = 0
            s_hit = 0
            try:
                pre_recs = recommend_units(store_key)  # 過去データのみ
                rt = get_realtime_data(store_key)
                rt_recs = recommend_units(store_key, realtime_data=rt)
                rt_map = {}
                for r in rt_recs:
                    rt_map[str(r.get('unit_id', ''))] = r
                for r in pre_recs:
                    uid = str(r.get('unit_id', ''))
                    if r.get('final_rank', 'C') in ('S', 'A'):
                        m_total += 1
                        s_total += 1
                        rt_r = rt_map.get(uid, {})
                        art = rt_r.get('art_count', 0)
                        games = rt_r.get('total_games', 0)
                        if art > 0 and games / art <= 130:
                            m_hit += 1
                            s_hit += 1
            except:
                pass
            if s_total > 0:
                s_rate = s_hit / s_total * 100
                short_name = store.get('name', store_key).replace('エスパス日拓', '').replace('店', '')
                store_results.append({'name': short_name, 'rate': s_rate, 'hit': s_hit, 'total': s_total})

        # 的中率が高い店舗を表示（100%の店は名前、それ以外は率）
        store_results.sort(key=lambda x: -x['rate'])
        top_parts = []
        for sr in store_results[:3]:
            if sr['rate'] >= 100:
                top_parts.append(f"{sr['name']}全的中")
            elif sr['rate'] >= 50:
                top_parts.append(f"{sr['name']}{sr['hit']}/{sr['total']}")
        top_stores = ' / '.join(top_parts) if top_parts else ''

        rate = (m_hit / m_total * 100) if m_total > 0 else 0
        accuracy_hero.append({
            'name': machine['short_name'],
            'icon': machine['icon'],
            'rate': rate,
            'hit': m_hit,
            'total': m_total,
            'top_stores': top_stores,
        })
    # 高い順にソート
    accuracy_hero.sort(key=lambda x: -x['rate'])

    html = template.render(
        machines=machines,
        top3=top3,
        yesterday_top10=yesterday_top10,
        today_top10=today_top10,
        today_weekday=today_weekday,
        today_date=today_date,
        today_date_formatted=today_date_formatted,
        now_time=now.strftime('%H:%M'),
        now_short=now.strftime('%m%d_%H:%M'),
        store_recommendations={},
        today_recommended_stores=today_recommended_stores,
        today_store_ranking=today_store_ranking,
        today_avoid_stores=today_avoid_stores,
        store_day_ratings=store_day_ratings,
        display_mode=display_mode,
        result_date_str=result_date_str,
        is_open=is_open,
        all_stores=all_stores,
        night_mode=night_mode,
        tomorrow_str=tomorrow_str,
        yesterday_str=yesterday_str,
        data_date_str=data_date_str,
        prev_date_str=prev_date_str,
        accuracy_hero=accuracy_hero,
        date_prefix=date_prefix,
        next_day_prefix=next_day_prefix,
        next_day_str=next_day_str,
    )

    output_path = OUTPUT_DIR / 'index.html'
    output_path.write_text(html, encoding='utf-8')
    print(f"  -> {output_path}")


def generate_machine_pages(env):
    """機種別店舗一覧ページを生成"""
    print("Generating machine pages...")

    template = env.get_template('stores.html')
    output_subdir = OUTPUT_DIR / 'machine'
    output_subdir.mkdir(parents=True, exist_ok=True)

    for machine_key, machine in MACHINES.items():
        stores = get_stores_by_machine(machine_key)
        store_list = [
            {'key': key, 'name': store['name'], 'unit_count': len(store['units'])}
            for key, store in stores.items()
        ]

        html = template.render(
            machine=machine,
            machine_key=machine_key,
            stores=store_list,
        )

        output_path = output_subdir / f'{machine_key}.html'
        output_path.write_text(html, encoding='utf-8')
        print(f"  -> {output_path}")


def get_reason_date_labels():
    """理由文の日付ラベルを取得（閉店後のみ日付に置換）"""
    if is_business_hours():
        return None, None
    try:
        from scrapers.availability_checker import get_daidata_availability
        avail_data = get_daidata_availability()
        fetched_at = avail_data.get('fetched_at', '')
        if fetched_at:
            data_dt = datetime.fromisoformat(fetched_at)
            data_label = f"{data_dt.month}/{data_dt.day}({WEEKDAY_NAMES[data_dt.weekday()]})"
            prev_dt = data_dt - timedelta(days=1)
            prev_label = f"{prev_dt.month}/{prev_dt.day}({WEEKDAY_NAMES[prev_dt.weekday()]})"
            return data_label, prev_label
    except:
        pass
    return None, None


def is_night_mode():
    """22:45以降は翌日予想モードに切り替え"""
    now = datetime.now(JST)
    return now.hour > 22 or (now.hour == 22 and now.minute >= 45)


def generate_ranking_pages(env):
    """機種別総合ランキングページを生成"""
    print("Generating ranking pages...")

    template = env.get_template('ranking.html')
    output_subdir = OUTPUT_DIR / 'ranking'
    output_subdir.mkdir(parents=True, exist_ok=True)

    night_mode = is_night_mode()
    now = datetime.now(JST)
    tomorrow = now + timedelta(days=1)
    tomorrow_str = format_date_with_weekday(tomorrow)
    yesterday = now - timedelta(days=1)
    data_date_str = format_date_with_weekday(now)
    prev_date_str = format_date_with_weekday(yesterday)
    reason_data_label, reason_prev_label = get_reason_date_labels()

    for machine_key, machine in MACHINES.items():
        stores = get_stores_by_machine(machine_key)
        all_recommendations = []

        for store_key, store in stores.items():
            availability = {}
            try:
                availability = get_availability(store_key)
            except:
                pass

            # リアルタイムデータも取得（設定推測やmax_medals等に必要）
            realtime = None
            try:
                realtime = get_realtime_data(store_key)
            except:
                pass

            recommendations = recommend_units(store_key, realtime_data=realtime, availability=availability,
                                              data_date_label=reason_data_label, prev_date_label=reason_prev_label)
            for rec in recommendations:
                rec['store_name'] = store.get('short_name', store['name'])
                rec['store_key'] = store_key
                all_recommendations.append(rec)

        # スコア順でソート
        def sort_key(r):
            score = r['final_score']
            if r['is_running']:
                score -= 30
            return -score

        all_recommendations.sort(key=sort_key)
        top_recs = [r for r in all_recommendations if r['final_rank'] in ('S', 'A') and not r['is_running']][:10]
        other_recs = [r for r in all_recommendations if r not in top_recs][:20]

        html = template.render(
            machine=machine,
            machine_key=machine_key,
            top_recs=top_recs,
            other_recs=other_recs,
            total_count=len(all_recommendations),
            night_mode=night_mode,
            tomorrow_str=tomorrow_str,
            data_date_str=data_date_str,
            prev_date_str=prev_date_str,
            now_short=now.strftime('%m%d_%H:%M'),
        )

        output_path = output_subdir / f'{machine_key}.html'
        output_path.write_text(html, encoding='utf-8')
        print(f"  -> {output_path}")


def generate_recommend_pages(env):
    """各店舗の推奨ページを生成"""
    print("Generating recommend pages...")

    template = env.get_template('recommend.html')
    output_subdir = OUTPUT_DIR / 'recommend'
    output_subdir.mkdir(parents=True, exist_ok=True)

    is_open = is_business_hours()
    display_mode = get_display_mode()
    reason_data_label, reason_prev_label = get_reason_date_labels()

    # 旧形式キーをスキップ
    old_keys = {'island_akihabara', 'shibuya_espass', 'shinjuku_espass'}

    for store_key, store in STORES.items():
        if store_key in old_keys:
            continue
        print(f"  Processing {store_key}...")

        machine_key = store.get('machine', 'sbj')
        machine = get_machine_info(machine_key)

        # 空き状況とリアルタイムデータを取得
        availability = {}
        realtime_data = None
        cache_info = None

        try:
            availability = get_availability(store_key)
        except:
            pass

        try:
            rt_data = get_realtime_data(store_key)
            if rt_data and rt_data.get('units'):
                realtime_data = rt_data
                fetched_at_str = rt_data.get('fetched_at', '')
                if fetched_at_str:
                    try:
                        fetched_time = datetime.fromisoformat(fetched_at_str.replace('Z', '+00:00'))
                        fetched_time_jst = fetched_time.astimezone(JST)
                        now_jst = datetime.now(JST)
                        cache_info = {
                            'fetched_at': fetched_time_jst.strftime('%H:%M'),
                            'age_seconds': int((now_jst - fetched_time_jst).total_seconds()),
                            'source': rt_data.get('source', 'unknown'),
                        }
                    except:
                        pass
        except:
            pass

        recommendations = recommend_units(store_key, realtime_data, availability,
                                          data_date_label=reason_data_label, prev_date_label=reason_prev_label)

        # 分類
        sa_recs = [r for r in recommendations if r['final_rank'] in ('S', 'A') and not r['is_running']]
        if sa_recs:
            top_recs = sa_recs
        else:
            top_recs = [r for r in recommendations if not r['is_running']][:3]

        other_recs = [r for r in recommendations if r not in top_recs]

        availability_info = None
        if availability:
            availability_info = {
                'fetched_at': datetime.now(JST).strftime('%H:%M'),
                'empty_count': sum(1 for v in availability.values() if v == '空き'),
                'playing_count': sum(1 for v in availability.values() if v == '遊技中'),
            }

        # 店舗分析（recommend_unitsの計算結果からランク分布を生成）
        daily_data = load_daily_data(machine_key=machine_key)
        store_analysis = generate_store_analysis(store_key, daily_data)

        # ランク分布をrecommend_unitsの結果で上書き（相対評価の結果を正確に反映）
        all_recs_for_analysis = top_recs + other_recs
        if all_recs_for_analysis:
            from collections import Counter
            rank_counts = Counter(r['final_rank'] for r in all_recs_for_analysis)
            rank_parts = []
            for rank in ['S', 'A', 'B', 'C', 'D']:
                count = rank_counts.get(rank, 0)
                if count > 0:
                    rank_parts.append(f"{rank}:{count}台")
            store_analysis['rank_dist'] = " / ".join(rank_parts)
            high_count = rank_counts.get('S', 0) + rank_counts.get('A', 0)
            total = len(all_recs_for_analysis)
            store_analysis['high_count'] = high_count
            store_analysis['total_units'] = total
            high_ratio = high_count / total * 100 if total > 0 else 0
            if high_ratio >= 70:
                store_analysis['overall'] = f"高設定台が非常に多い（全{total}台中{high_count}台がA以上）"
            elif high_ratio >= 50:
                store_analysis['overall'] = f"高設定台が多い（全{total}台中{high_count}台がA以上）"
            elif high_ratio >= 30:
                store_analysis['overall'] = f"高設定台あり（全{total}台中{high_count}台がA以上）"
            else:
                store_analysis['overall'] = f"高設定台が少ない（全{total}台中{high_count}台がA以上）"

        # 台番号アラート
        store_alerts = [a for a in get_active_alerts() if a.get('store_key') == store_key]

        html = template.render(
            store=store,
            store_key=store_key,
            machine=machine,
            machine_key=machine_key,
            top_recs=top_recs,
            other_recs=other_recs,
            updated_at=datetime.now(JST).strftime('%H:%M'),
            cache_info=cache_info,
            availability_info=availability_info,
            is_open=is_open,
            display_mode=display_mode,
            store_analysis=store_analysis,
            unit_alerts=store_alerts,
        )

        output_path = output_subdir / f'{store_key}.html'
        output_path.write_text(html, encoding='utf-8')

    print(f"  -> {output_subdir}/")


def _process_history_for_verify(history):
    """当たり履歴を答え合わせ表示用に加工する

    - 時間順にソート
    - チェーン（連チャン）を計算
    - 深いハマり・浅い当たりのフラグを付与
    """
    from analysis.analyzer import is_big_hit, RENCHAIN_THRESHOLD

    if not history:
        return [], {}

    # 時間順にソート
    sorted_hist = sorted(history, key=lambda x: x.get('time', '00:00'))

    # チェーン計算: AT間のG数を蓄積し、閾値以下なら連チャン
    processed = []
    chain_id = 0
    chain_hits = []  # 現在のチェーン内のヒット
    accumulated_games = 0  # RBを跨いだAT間G数

    for i, hit in enumerate(sorted_hist):
        start = hit.get('start', 0)
        hit_type = hit.get('type', 'ART')
        medals = hit.get('medals', 0)
        time_str = hit.get('time', '')

        accumulated_games += start

        entry = {
            'index': i + 1,
            'time': time_str,
            'start': start,
            'type': hit_type,
            'medals': medals,
            'is_deep': start >= 500,
            'is_shallow': start <= 10 and i > 0,
            'is_tenjou': start >= 800,
        }

        if is_big_hit(hit_type):
            if i == 0 or accumulated_games > RENCHAIN_THRESHOLD:
                # 新しいチェーン開始
                if chain_hits:
                    chain_len = len(chain_hits)
                    for ch in chain_hits:
                        ch['chain_len'] = chain_len
                chain_id += 1
                chain_hits = [entry]
            else:
                # 連チャン継続
                chain_hits.append(entry)

            entry['chain_id'] = chain_id
            entry['is_hot_chain'] = False  # 後で更新
            accumulated_games = 0  # AT間リセット
        else:
            # RB: チェーンに含めない（AT間は継続）
            entry['chain_id'] = 0
            entry['chain_len'] = 0
            entry['is_hot_chain'] = False

        processed.append(entry)

    # 最後のチェーンを処理
    if chain_hits:
        chain_len = len(chain_hits)
        for idx, ch in enumerate(chain_hits):
            ch['chain_len'] = chain_len
            ch['chain_pos'] = idx + 1  # 1連目, 2連目, ...

    # 全てのチェーンにchain_posを付与（最後のチェーン以外は既にループ内で処理済み）
    # → 上のループ内で処理する必要がある。修正:
    # chain_hitsの処理を再走査して全チェーンにchain_posを付与
    current_chain_id = 0
    pos = 0
    for entry in processed:
        cid = entry.get('chain_id', 0)
        if cid > 0:
            if cid != current_chain_id:
                current_chain_id = cid
                pos = 1
            else:
                pos += 1
            entry['chain_pos'] = pos
        else:
            entry['chain_pos'] = 0

    # ホットチェーン(5連以上)にフラグ付与
    for entry in processed:
        if entry.get('chain_len', 0) >= 5:
            entry['is_hot_chain'] = True

    # サマリー計算
    starts = [h.get('start', 0) for h in sorted_hist]
    big_hit_starts = []
    acc = 0
    for hit in sorted_hist:
        acc += hit.get('start', 0)
        if is_big_hit(hit.get('type', '')):
            big_hit_starts.append(acc)
            acc = 0

    total_games = sum(starts)
    total_hits = len(sorted_hist)
    total_medals = sum(h.get('medals', 0) for h in sorted_hist)

    # AT間ベースの谷
    valleys = big_hit_starts if big_hit_starts else starts
    max_valley = max(valleys) if valleys else 0
    avg_valley = int(sum(valleys) / len(valleys)) if valleys else 0
    tenjou_count = sum(1 for v in valleys if v >= 800)

    # 最大チェーン
    chain_lengths = [e.get('chain_len', 0) for e in processed if e.get('chain_id', 0) > 0]
    # 各チェーンの長さをユニークに取得
    seen_chains = {}
    for e in processed:
        cid = e.get('chain_id', 0)
        clen = e.get('chain_len', 0)
        if cid > 0 and cid not in seen_chains:
            seen_chains[cid] = clen
    max_chain = max(seen_chains.values()) if seen_chains else 0

    summary = {
        'total_games': total_games,
        'total_hits': total_hits,
        'total_medals': total_medals,
        'max_valley': max_valley,
        'avg_valley': avg_valley,
        'tenjou_count': tenjou_count,
        'max_chain': max_chain,
    }

    return processed, summary


def generate_verify_page(env):
    """答え合わせページを生成 - 予測 vs 実績の比較"""
    print("Generating verify page...")

    template = env.get_template('verify.html')
    old_keys = {'island_akihabara', 'shibuya_espass', 'shinjuku_espass'}

    verify_data = {}
    total_predicted_good = 0  # 予測S/A台数
    total_actual_good = 0     # 予測S/Aのうち実際に好調だった台数
    total_surprise = 0        # 予測B以下だが実際に好調だった台数

    for machine_key, machine in MACHINES.items():
        stores_data = []
        stores = get_stores_by_machine(machine_key)

        # 日別データを読み込み（当たり履歴取得用）
        daily_data = load_daily_data(machine_key=machine_key)
        daily_stores = daily_data.get('stores', {}) if daily_data else {}

        for store_key, store in stores.items():
            if store_key in old_keys:
                continue

            # 2種類の予測を取得
            # (1) 開店前予測: 過去データのみ（リアルタイムなし）
            # (2) リアルタイム予測: 当日データ込み
            availability = {}
            realtime = None
            try:
                availability = get_availability(store_key)
                realtime = get_realtime_data(store_key)
            except:
                pass

            # 開店前予測（過去データのみ）
            pre_open_recs = recommend_units(store_key, availability=availability)
            pre_open_map = {}
            for r in pre_open_recs:
                pre_open_map[str(r.get('unit_id', ''))] = {
                    'rank': r.get('final_rank', 'C'),
                    'score': r.get('final_score', 50),
                }

            # リアルタイム予測（当日データ込み）
            recommendations = recommend_units(store_key, realtime_data=realtime, availability=availability)
            units_data = []

            # この店舗の日別データからユニットマップを作成
            store_daily = daily_stores.get(store_key, {})
            daily_units_map = {}
            for u in store_daily.get('units', []):
                daily_units_map[str(u.get('unit_id', ''))] = u

            for rec in recommendations:
                # リアルタイム予測（当日データ込み）
                predicted_rank = rec.get('final_rank', 'C')
                predicted_score = rec.get('final_score', 50)

                # 閉店後は前日データを実績として使う
                actual_art = rec.get('art_count', 0)
                actual_games = rec.get('total_games', 0)
                actual_prob = rec.get('art_prob', 0)
                if actual_art == 0 and not is_business_hours():
                    actual_art = rec.get('yesterday_art', 0)
                    actual_games = rec.get('yesterday_games', 0)
                    if actual_art > 0 and actual_games > 0:
                        actual_prob = actual_games / actual_art
                    else:
                        actual_prob = 0

                # 開店前予測（過去データのみ）
                uid = str(rec.get('unit_id', ''))
                pre_open = pre_open_map.get(uid, {})
                pre_open_rank = pre_open.get('rank', 'C')
                pre_open_score = pre_open.get('score', 50)

                # 判定
                is_predicted_good = predicted_rank in ('S', 'A')
                is_actual_good = actual_prob > 0 and actual_prob <= 130
                is_actual_excellent = actual_prob > 0 and actual_prob <= 100
                is_actual_bad = actual_prob >= 200 or (actual_games >= 1000 and actual_art == 0)

                if is_predicted_good:
                    total_predicted_good += 1
                    if is_actual_excellent:
                        verdict = '\u25CE'  # ◎
                        verdict_class = 'perfect'
                        total_actual_good += 1
                    elif is_actual_good:
                        verdict = '\u25CB'  # ○
                        verdict_class = 'hit'
                        total_actual_good += 1
                    elif is_actual_bad:
                        verdict = '\u2715'  # ✕
                        verdict_class = 'miss'
                    else:
                        verdict = '\u25B3'  # △
                        verdict_class = 'neutral'
                elif not is_predicted_good and is_actual_good:
                    verdict = '\u2605'  # ★ 発掘
                    verdict_class = 'surprise'
                    total_surprise += 1
                elif actual_games < 500:
                    verdict = '-'
                    verdict_class = 'nodata'
                else:
                    verdict = '\u25B3'  # △
                    verdict_class = 'neutral'

                # 当たり履歴を取得
                unit_daily = daily_units_map.get(str(rec.get('unit_id', '')), {})
                days = unit_daily.get('days', [])
                today_history_raw = []
                history_date = ''
                if days:
                    # 最新の日付データを使用
                    today_data = days[0]
                    today_history_raw = today_data.get('history', [])
                    history_date = today_data.get('date', '')

                processed_history, history_summary = _process_history_for_verify(today_history_raw)

                units_data.append({
                    'unit_id': rec.get('unit_id', ''),
                    'predicted_rank': predicted_rank,
                    'predicted_score': predicted_score,
                    'pre_open_rank': pre_open_rank,
                    'pre_open_score': pre_open_score,
                    'actual_art': actual_art,
                    'actual_prob': actual_prob,
                    'actual_games': actual_games,
                    'verdict': verdict,
                    'verdict_class': verdict_class,
                    'history': processed_history,
                    'history_summary': history_summary,
                    'history_date': history_date,
                })

            if units_data:
                # 店舗別的中率（開店前予測ベース）
                store_sa_total = sum(1 for u in units_data if u['pre_open_rank'] in ('S', 'A'))
                store_sa_hit = sum(1 for u in units_data if u['pre_open_rank'] in ('S', 'A') and u.get('actual_prob', 0) > 0 and u['actual_prob'] <= 130)
                store_sa_rate = (store_sa_hit / store_sa_total * 100) if store_sa_total > 0 else 0
                stores_data.append({
                    'name': store.get('name', store_key),
                    'units': units_data,
                    'sa_total': store_sa_total,
                    'sa_hit': store_sa_hit,
                    'sa_rate': store_sa_rate,
                })

        if stores_data:
            verify_data[machine_key] = {
                'name': machine['short_name'],
                'icon': machine['icon'],
                'stores': stores_data,
            }

    # 的中率計算
    accuracy = 0
    if total_predicted_good > 0:
        accuracy = (total_actual_good / total_predicted_good) * 100

    # 機種別の的中率（開店前予測ベース）
    machine_accuracy = []
    for machine_key, machine_data in verify_data.items():
        m_predicted = 0
        m_actual = 0
        m_surprise = 0
        for store in machine_data.get('stores', []):
            for unit in store.get('units', []):
                is_sa = unit['pre_open_rank'] in ('S', 'A')
                prob = unit.get('actual_prob', 0)
                is_good = prob > 0 and prob <= 130
                if is_sa:
                    m_predicted += 1
                    if is_good:
                        m_actual += 1
                elif not is_sa and is_good:
                    m_surprise += 1
        rate = (m_actual / m_predicted * 100) if m_predicted > 0 else 0
        machine_accuracy.append({
            'name': machine_data['name'],
            'icon': machine_data['icon'],
            'total': m_predicted,
            'hit': m_actual,
            'rate': rate,
            'surprise': m_surprise,
        })

    html = template.render(
        verify_data=verify_data,
        accuracy=accuracy,
        predicted_good=total_predicted_good,
        actual_good=total_actual_good,
        surprise_good=total_surprise,
        machine_accuracy=machine_accuracy,
    )

    output_path = OUTPUT_DIR / 'verify.html'
    output_path.write_text(html, encoding='utf-8')
    print(f"  -> {output_path}")


def copy_static_files():
    """静的ファイルをコピー"""
    print("Copying static files...")

    import shutil

    static_src = PROJECT_ROOT / 'web' / 'static'
    static_dst = OUTPUT_DIR / 'static'

    if static_dst.exists():
        shutil.rmtree(static_dst)

    shutil.copytree(static_src, static_dst)
    print(f"  -> {static_dst}/")


def generate_metadata():
    """メタデータファイルを生成"""
    print("Generating metadata...")

    metadata = {
        'generated_at': datetime.now(JST).isoformat(),
        'version': '2026-01-27-static',
    }

    output_path = OUTPUT_DIR / 'metadata.json'
    output_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"  -> {output_path}")


def run_unit_verification():
    """台番号検証を実行し、アラートがあれば保存"""
    print("Running unit verification...")
    try:
        from scripts.verify_units import verify_units_from_availability, save_alerts, print_report
        avail_path = PROJECT_ROOT / 'data' / 'availability.json'
        if avail_path.exists():
            with open(avail_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            alerts = verify_units_from_availability(data)
            print_report(alerts)
            if alerts:
                save_alerts(alerts, source='availability')
        else:
            print("  availability.json not found, skipping")
    except Exception as e:
        print(f"  Verification error: {e}")


def run_data_integrity_check():
    """データ整合性チェック（全店舗のART/フィールド欠損等）"""
    print("Running data integrity check...")
    try:
        from scripts.verify_units import verify_data_integrity, print_integrity_report
        avail_path = PROJECT_ROOT / 'data' / 'availability.json'
        if avail_path.exists():
            with open(avail_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            issues = verify_data_integrity(data)
            print_integrity_report(issues)
        else:
            print("  availability.json not found, skipping")
    except Exception as e:
        print(f"  Integrity check error: {e}")


def main():
    print("=" * 50)
    print("静的サイト生成開始")
    print(f"出力先: {OUTPUT_DIR}")
    print("=" * 50)
    print()

    # 台番号検証（アラート生成）
    run_unit_verification()

    # データ整合性チェック
    run_data_integrity_check()

    # 日次データ蓄積（history DB更新）
    try:
        from analysis.history_accumulator import accumulate_from_daily
        for mk in MACHINES:
            daily = load_daily_data(machine_key=mk)
            if daily:
                result = accumulate_from_daily(daily, mk)
                if result['new_entries'] > 0:
                    print(f"  📦 {mk}: {result['new_entries']}件蓄積 ({result['updated_units']}台)")
    except Exception as e:
        print(f"  ⚠ 蓄積エラー: {e}")
    print()

    # 出力ディレクトリを作成
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Jinja2環境をセットアップ
    env = setup_jinja()

    # 各ページを生成
    generate_index(env)
    generate_machine_pages(env)
    generate_ranking_pages(env)
    generate_recommend_pages(env)
    generate_verify_page(env)
    copy_static_files()
    generate_metadata()

    print()
    print("=" * 50)
    print("静的サイト生成完了!")
    print("=" * 50)


if __name__ == '__main__':
    main()
