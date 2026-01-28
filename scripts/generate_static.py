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
from analysis.analyzer import calculate_first_hits, mark_first_hits
from scrapers.availability_checker import get_availability, get_realtime_data
from scripts.verify_units import get_active_alerts, get_unit_status

JST = timezone(timedelta(hours=9))
WEEKDAY_NAMES = ['月', '火', '水', '木', '金', '土', '日']

# 出力ディレクトリ
OUTPUT_DIR = PROJECT_ROOT / 'docs'  # GitHub Pages互換


def get_display_mode():
    """現在時刻から表示モードを決定
    - before_open: 0:00-9:59（営業前）
    - after_close: 22:50-23:59（閉店後）
    - realtime: 10:00-22:49（営業中）
    """
    now = datetime.now(JST)
    hour = now.hour
    minute = now.minute

    if hour < 10:
        return 'before_open'
    elif hour >= 23 or (hour == 22 and minute >= 50):
        return 'after_close'
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
    env.globals['build_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def generate_sparkline(history, width=120, height=40, diff_medals=None):
        """当たり履歴から差枚推移のSVGスパークラインを生成

        Args:
            history: 当たり履歴リスト
            diff_medals: 既知の最終差枚（正規化に使用）
        """
        if not history or len(history) < 2:
            return ''
        # hit_num降順（大きい=古い）でソート
        sorted_hist = sorted(history, key=lambda x: (-x.get('hit_num', 0), x.get('time', '00:00')))
        # 各当たりのメダル獲得数で相対推移を計算
        # medals: ボーナス/AT獲得枚数、start: 当たり間の消化G数
        cumulative = [0]
        total = 0
        for h in sorted_hist:
            medals = h.get('medals', 0)
            start = h.get('start', 0)
            total -= start * 3  # 当たり間の投入
            total += medals      # 獲得
            cumulative.append(total)

        # 既知の差枚があれば正規化（推移の形は保ち、最終値を合わせる）
        if diff_medals is not None and total != 0:
            scale = diff_medals / total
            cumulative = [v * scale for v in cumulative]
        elif diff_medals is not None and total == 0:
            # 累積0だが差枚がある場合、推移が描けないのでスキップ
            pass
        if len(cumulative) < 2:
            return ''
        min_v = min(cumulative)
        max_v = max(cumulative)
        v_range = max_v - min_v if max_v != min_v else 1
        # SVGポイント生成
        points = []
        for i, v in enumerate(cumulative):
            x = i / (len(cumulative) - 1) * width
            y = height - ((v - min_v) / v_range * (height - 4)) - 2
            points.append(f'{x:.1f},{y:.1f}')
        polyline = ' '.join(points)
        # ゼロライン
        zero_y = height - ((0 - min_v) / v_range * (height - 4)) - 2
        # 色: 最終値がプラスなら緑、マイナスなら赤（正規化後の値で判定）
        final_val = cumulative[-1] if cumulative else total
        color = '#2ed573' if final_val >= 0 else '#ff6b6b'
        return f'<svg class="sparkline" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><line x1="0" y1="{zero_y:.1f}" x2="{width}" y2="{zero_y:.1f}" stroke="#555" stroke-width="0.5" stroke-dasharray="2,2"/><polyline points="{polyline}" fill="none" stroke="{color}" stroke-width="1.5"/></svg>'
    env.globals['sparkline'] = generate_sparkline

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
        'akiba_espass': {
            'name': 'エスパス日拓秋葉原駅前店',
            'short_name': 'エスパス秋葉原',
            'day_ratings': {'月': 2, '火': 3, '水': 3, '木': 3, '金': 4, '土': 5, '日': 4},
            'best_note': '土日が狙い目、金曜も可',
            'worst_note': '月曜は控えめ',
            'overall_rating': 3,
            'machine_links': [
                {'store_key': 'akiba_espass_sbj', 'icon': '🃏', 'short_name': 'SBJ'},
                {'store_key': 'akiba_espass_hokuto', 'icon': '👊', 'short_name': '北斗転生2'},
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
                    # 差枚計算はrecommend_units内で統合済み
                    # 初当たり回数を計算（TOP3表示用）
                    _y_hist = rec.get('yesterday_history', [])
                    if _y_hist:
                        rec['first_hit_count'] = calculate_first_hits(_y_hist)['first_hit_count']
                    else:
                        rec['first_hit_count'] = 0

                # TOP3候補（上位3台/店舗）
                for rec in recs[:3]:
                    top3_all.append(rec)

                # 前日の爆発台（全台から収集、yesterday_art > 0）
                for rec in recs:
                    y_art = rec.get('yesterday_art', 0)
                    if y_art and y_art > 0:
                        y_games = rec.get('yesterday_games', 0)
                        y_prob = y_games / y_art if y_art > 0 and y_games > 0 else 0
                        # 差枚計算（medalsベース優先 → フォールバックで機械割ベース）
                        y_diff_medals = 0
                        y_setting = ''
                        y_setting_num = 0
                        if y_art > 0 and y_games > 0:
                            # 設定推定（表示用）
                            y_profit = calculate_expected_profit(y_games, y_art, key)
                            y_si = y_profit.get('setting_info', {})
                            y_setting = y_si.get('estimated_setting', '')
                            y_setting_num = y_si.get('setting_num', 0)
                            # 差枚: historyのmedals合計から実測ベースで推定
                            try:
                                from analysis.history_accumulator import load_unit_history
                                from analysis.diff_medals_estimator import estimate_diff_medals
                                acc = load_unit_history(store_key, rec['unit_id'])
                                y_date = rec.get('yesterday_date', '')
                                for ad in acc.get('days', []):
                                    if ad.get('date') == y_date:
                                        ad_hist = ad.get('history', [])
                                        ad_games = ad.get('games', ad.get('total_start', 0))
                                        if ad_hist and ad_games > 0:
                                            medals_total = sum(h.get('medals', 0) for h in ad_hist)
                                            y_diff_medals = estimate_diff_medals(medals_total, ad_games, key)
                                        break
                            except Exception:
                                pass
                            # フォールバック: medalsが取れなければ機械割ベース
                            if y_diff_medals == 0:
                                y_diff_medals = y_profit.get('current_estimate', 0)
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
                                    from analysis.analyzer import calculate_max_chain_medals
                                    y_max_medals = calculate_max_chain_medals(hist)
                            except:
                                pass
                        # 蓄積DBからも補完
                        if not y_max_rensa or not y_max_medals:
                            try:
                                from analysis.history_accumulator import load_unit_history
                                from analysis.analyzer import calculate_max_chain_medals as _calc_chain
                                acc_hist = load_unit_history(store_key, rec['unit_id'])
                                y_date = rec.get('yesterday_date', '')
                                for ad in acc_hist.get('days', []):
                                    if ad.get('date') == y_date or (not y_date and ad == acc_hist['days'][-1]):
                                        if not y_max_rensa:
                                            y_max_rensa = ad.get('max_rensa', 0)
                                        if not y_max_medals:
                                            # historyがあれば連チャン累計で再計算
                                            ad_hist = ad.get('history', [])
                                            if ad_hist:
                                                y_max_medals = _calc_chain(ad_hist)
                                            else:
                                                y_max_medals = ad.get('max_medals', 0)
                                        break
                            except:
                                pass
                        # 前日の予想ランクを取得
                        predicted_rank = rec.get('rank', 'C')
                        predicted_score = rec.get('score', 50)
                        was_predicted_good = predicted_rank in ('S', 'A')
                        # 実際の結果（好調だったか）
                        good_threshold = 130 if key == 'sbj' else 330
                        was_actually_good = y_prob > 0 and y_prob <= good_threshold
                        # 的中判定
                        if was_predicted_good and was_actually_good:
                            prediction_result = 'hit'    # 予想◎→結果◎
                        elif was_predicted_good and not was_actually_good:
                            prediction_result = 'miss'   # 予想◎→結果✗
                        elif not was_predicted_good and was_actually_good:
                            prediction_result = 'missed'  # 見逃し（予想外の好調）
                        else:
                            prediction_result = 'correct'  # 予想通り不調

                        # 初当たり計算 & 履歴マーキング
                        y_hist_raw = rec.get('yesterday_history', [])
                        t_hist_raw = rec.get('today_history', [])
                        y_first_hits = calculate_first_hits(y_hist_raw)
                        y_first_hit_count = y_first_hits['first_hit_count']
                        y_hist_marked = mark_first_hits(y_hist_raw)
                        t_hist_marked = mark_first_hits(t_hist_raw)

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
                            'yesterday_diff_medals': y_diff_medals,
                            'estimated_setting': y_setting,
                            'setting_num': y_setting_num,
                            'predicted_rank': predicted_rank,
                            'predicted_score': predicted_score,
                            'prediction_result': prediction_result,
                            'yesterday_history': y_hist_marked,
                            'today_history': t_hist_marked,
                            'recent_days': rec.get('recent_days', []),
                            'first_hit_count': y_first_hit_count,
                            # 前々日・3日前データ
                            'day_before_art': rec.get('day_before_art', 0),
                            'day_before_rb': rec.get('day_before_rb', 0),
                            'day_before_games': rec.get('day_before_games', 0),
                            'day_before_date': rec.get('day_before_date', ''),
                            'day_before_diff_medals': rec.get('day_before_diff_medals', 0),
                            'day_before_max_rensa': rec.get('day_before_max_rensa', 0),
                            'day_before_max_medals': rec.get('day_before_max_medals', 0),
                            'three_days_ago_art': rec.get('three_days_ago_art', 0),
                            'three_days_ago_rb': rec.get('three_days_ago_rb', 0),
                            'three_days_ago_games': rec.get('three_days_ago_games', 0),
                            'three_days_ago_date': rec.get('three_days_ago_date', ''),
                            'three_days_ago_diff_medals': rec.get('three_days_ago_diff_medals', 0),
                            'three_days_ago_max_rensa': rec.get('three_days_ago_max_rensa', 0),
                            'three_days_ago_max_medals': rec.get('three_days_ago_max_medals', 0),
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
                        # 初当たり計算
                        t_hist_raw2 = rec.get('today_history', [])
                        t_first_hits = calculate_first_hits(t_hist_raw2)
                        t_first_hit_count = t_first_hits['first_hit_count']
                        t_hist_marked2 = mark_first_hits(t_hist_raw2)

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
                            'today_history': t_hist_marked2,
                            'first_hit_count': t_first_hit_count,
                        })
            except Exception as e:
                print(f"Error processing {store_key}: {e}")

    # ソート
    # TOP3: 各機種の最強台を1台ずつ + 残り枠は差枚順
    # 機種関係なく「前日最も稼いだS/A台」= 高設定の据え置き期待
    top3_candidates = [r for r in top3_all if r.get('final_rank') in ('S', 'A')]
    # スコア順（信頼度・試行回数を考慮した総合スコア）
    top3_candidates.sort(key=lambda r: -r.get('final_score', 0))

    # 各機種から1台ずつ確保 + 重複台排除
    top3 = []
    seen_machines = set()
    seen_units = set()  # 同じ台番の重複排除
    for r in top3_candidates:
        mk = r.get('machine_key', '')
        uid = str(r.get('unit_id', ''))
        if mk not in seen_machines and uid not in seen_units:
            top3.append(r)
            seen_machines.add(mk)
            seen_units.add(uid)
        if len(top3) >= len(MACHINES):
            break
    # 残り枠をスコア順で埋める
    for r in top3_candidates:
        uid = str(r.get('unit_id', ''))
        if uid not in seen_units:
            top3.append(r)
            seen_units.add(uid)
        if len(top3) >= 3:
            break
    if not top3:
        top3 = top3_candidates[:3]

    # 前日の爆発台: 最大連チャン枚数でソート
    # 差枚だと「万枚出して飲まれた台」が低く出る。
    # max_chain（1回の連チャン区間の累計枚数）なら爆発の瞬間を正しく評価。
    yesterday_top10.sort(key=lambda x: (-x.get('yesterday_max_medals', 0), -x.get('diff_medals', 0)))
    yesterday_top10 = yesterday_top10[:10]

    # 本日の爆発台: 最大連チャン枚数でソート
    today_top10.sort(key=lambda x: (-x.get('max_medals', 0), -x.get('diff_medals', 0)))
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
    if display_mode in ('before_open', 'after_close'):
        if now.hour >= 23:
            result_date = now
            date_prefix = '本日'
        elif now.hour < 10:
            result_date = now - timedelta(days=1)
            date_prefix = ''  # 日付だけで十分（「昨日」は冗長）
        else:
            result_date = now - timedelta(days=1)
            date_prefix = ''
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

    # 全店舗おすすめリンク
    recommend_links = []
    for store_key, info in store_day_ratings.items():
        for ml in info.get('machine_links', []):
            link_store_key = ml.get('store_key', store_key)
            _mk = STORES.get(link_store_key, {}).get('machine', 'sbj')
            _machine_display = MACHINES.get(_mk, {}).get('display_name', ml.get('short_name', ''))
            recommend_links.append({
                'store_key': link_store_key,
                'name': info['short_name'],
                'icon': ml.get('icon', ''),
                'machine_name': _machine_display,
            })
    # 今日の評価順
    recommend_links.sort(key=lambda x: next(
        (-s['today_rating'] for s in all_stores if any(
            ml.get('store_key') == x['store_key'] for ml in s.get('machine_links', [])
        )), 0
    ))

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
        recommend_links=recommend_links,
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
                # 差枚計算はrecommend_units内で統合済み
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

        # 差枚計算はrecommend_units内で統合済み

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


def _try_load_backtest_results():
    """最新のバックテスト結果を読み込む"""
    import glob
    results_files = sorted(glob.glob(str(PROJECT_ROOT / 'data' / 'verify' / 'verify_*_results.json')))
    if not results_files:
        return None
    latest = results_files[-1]
    try:
        data = json.loads(Path(latest).read_text())
        if data.get('total_sa', 0) > 0:
            print(f"  バックテスト結果を使用: {Path(latest).name}")
            return data
    except:
        pass
    return None


def _generate_verify_from_backtest(env, results):
    """バックテスト結果からverifyページを生成"""
    from analysis.feedback import analyze_prediction_errors
    
    STORE_TO_MACHINE = {}
    for sk, sv in STORES.items():
        STORE_TO_MACHINE[sk] = sv.get('machine_key', 'sbj')
    
    machine_groups = {}
    for store_key, store_data in results.get('stores', {}).items():
        mk = STORE_TO_MACHINE.get(store_key, 'sbj')
        if mk not in machine_groups:
            machine_groups[mk] = {'stores': []}
        
        units = results.get('units', {}).get(store_key, [])
        formatted_units = []
        for u in sorted(units, key=lambda x: -x.get('predicted_score', 0)):
            rank = u.get('predicted_rank', 'C')
            is_sa = rank in ('S', 'A')
            is_good = u.get('actual_is_good', False)
            prob = u.get('actual_prob', 0)
            games = u.get('actual_games', 0)
            
            if is_sa and is_good and prob > 0 and prob <= 100:
                verdict, verdict_class = '◎', 'perfect'
            elif is_sa and is_good:
                verdict, verdict_class = '○', 'hit'
            elif is_sa and not is_good:
                verdict, verdict_class = '✕', 'miss'
            elif not is_sa and is_good:
                verdict, verdict_class = '★', 'surprise'
            elif games < 500:
                verdict, verdict_class = '-', 'nodata'
            else:
                verdict, verdict_class = '△', 'neutral'
            
            formatted_units.append({
                'unit_id': u.get('unit_id', ''),
                'pre_open_rank': rank,
                'pre_open_score': u.get('predicted_score', 50),
                'predicted_rank': rank,
                'predicted_score': u.get('predicted_score', 50),
                'actual_art': u.get('actual_art', 0),
                'actual_prob': prob,
                'actual_games': games,
                'verdict': verdict,
                'verdict_class': verdict_class,
            })
        
        machine_groups[mk]['stores'].append({
            'name': store_data.get('name', store_key),
            'units': formatted_units,
            'sa_total': store_data.get('sa_total', 0),
            'sa_hit': store_data.get('sa_hit', 0),
            'sa_rate': store_data.get('rate', 0),
        })
    
    verify_data = {}
    for mk, mg in machine_groups.items():
        m = MACHINES.get(mk, {})
        verify_data[mk] = {
            'name': m.get('short_name', mk),
            'icon': m.get('icon', '🎰'),
            'stores': mg['stores'],
        }
    
    accuracy = results.get('overall_rate', 0)
    total_sa = results.get('total_sa', 0)
    total_hit = results.get('total_hit', 0)
    total_surprise = results.get('total_surprise', 0)
    
    machine_accuracy = []
    for mk, md in verify_data.items():
        m_predicted = sum(s['sa_total'] for s in md['stores'])
        m_actual = sum(s['sa_hit'] for s in md['stores'])
        m_surprise = sum(sum(1 for u in s['units'] if u['verdict_class'] == 'surprise') for s in md['stores'])
        m_all = sum(len(s['units']) for s in md['stores'])
        m_rate = (m_actual / m_predicted * 100) if m_predicted > 0 else 0
        machine_accuracy.append({
            'name': md['name'], 'icon': md['icon'],
            'total': m_predicted, 'hit': m_actual,
            'surprise': m_surprise, 'all_units': m_all,
            'total_good': m_actual + m_surprise, 'rate': m_rate,
        })
    
    hypotheses = []
    for mk, md in verify_data.items():
        for sd in md['stores']:
            try:
                analysis = analyze_prediction_errors(sd['units'], '', mk)
                if analysis.get('hypotheses'):
                    hypotheses.extend(analysis['hypotheses'])
            except:
                pass
    
    # 日付情報（読みやすいフォーマット）
    weekdays = ['月','火','水','木','金','土','日']
    def _fmt_date(date_str):
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            return f'{dt.month}/{dt.day}({weekdays[dt.weekday()]})'
        except:
            return date_str
    
    pred_date = results.get('prediction_date', '')
    actual_date = results.get('date', '')
    
    template = env.get_template('verify.html')
    html = template.render(
        verify_data=verify_data,
        accuracy=accuracy,
        total_predicted_good=total_sa,
        total_actual_good=total_hit,
        total_surprise=total_surprise,
        machine_accuracy=machine_accuracy,
        hypotheses=hypotheses[:6],
        version=f'backtest_{actual_date}',
        result_date_str=f'{_fmt_date(actual_date)}の実績',
        predict_base=f'{_fmt_date(pred_date)}までの蓄積データ + 推移パターンロジック',
    )
    
    output_path = OUTPUT_DIR / 'verify.html'
    output_path.write_text(html, encoding='utf-8')
    print(f"  -> {output_path} (バックテスト: 的中率{accuracy:.0f}%)")


def generate_verify_page(env):
    """答え合わせページを生成 - 予測 vs 実績の比較
    
    バックテスト結果(data/verify/verify_*_results.json)があれば
    最新のものを使って生成する。なければ通常の予測vs前日実績で生成。
    """
    print("Generating verify page...")
    
    # バックテスト結果があればそちらを使う
    backtest_result = _try_load_backtest_results()
    if backtest_result:
        _generate_verify_from_backtest(env, backtest_result)
        return

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

            # フィードバック保存（答え合わせ結果を次回予測に反映）
            try:
                from analysis.feedback import analyze_prediction_errors, save_feedback
                for sd in stores_data:
                    store_name = sd.get('name', '')
                    # store_keyを逆引き
                    _sk = ''
                    for _skey, _sval in get_stores_by_machine(machine_key).items():
                        if _sval.get('name', '') == store_name:
                            _sk = _skey
                            break
                    if _sk and sd.get('units'):
                        analysis = analyze_prediction_errors(sd['units'], _sk, machine_key)
                        if analysis['hits'] + analysis['misses'] + analysis['surprises'] > 0:
                            save_feedback(analysis)
            except Exception as e:
                print(f"  ⚠ フィードバック保存エラー: {e}")

    # 的中率計算
    accuracy = 0
    if total_predicted_good > 0:
        accuracy = (total_actual_good / total_predicted_good) * 100

    # 機種別の的中率（開店前予測ベース）
    machine_accuracy = []
    for machine_key, machine_data in verify_data.items():
        m_all = 0
        m_predicted = 0
        m_actual = 0
        m_surprise = 0
        for store in machine_data.get('stores', []):
            for unit in store.get('units', []):
                m_all += 1
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
            'all_units': m_all,
            'total': m_predicted,
            'hit': m_actual,
            'rate': rate,
            'surprise': m_surprise,
            'total_good': m_actual + m_surprise,
        })

    # 全台数を計算
    total_all_units = 0
    for mk, md in verify_data.items():
        for s in md.get('stores', []):
            total_all_units += len(s.get('units', []))

    # 全台中の好調台数
    total_good_all = total_actual_good + total_surprise

    # 日付情報
    now = datetime.now(JST)
    reason_data_label, reason_prev_label = get_reason_date_labels()
    # 実績データの日付（閉店後は前日、営業中は当日）
    if is_business_hours():
        result_date_str = format_date_with_weekday(now)
        predict_base = format_date_with_weekday(now - timedelta(days=1))
    else:
        result_date_str = format_date_with_weekday(now - timedelta(days=1))
        predict_base = format_date_with_weekday(now - timedelta(days=2))

    # 仮説生成
    hypotheses = []
    try:
        from analysis.feedback import generate_hypotheses, load_feedback_history
        import glob
        all_fbs = []
        for fp in sorted(glob.glob('data/feedback/*_2026-*.json')):
            try:
                with open(fp) as fh:
                    all_fbs.append(json.load(fh))
            except Exception:
                pass
        # 今日のフィードバックのみで仮説生成
        today_str = now.strftime('%Y-%m-%d')
        today_fbs = [fb for fb in all_fbs if fb.get('date') == today_str]
        if today_fbs:
            hypotheses = generate_hypotheses(today_fbs)
    except Exception as e:
        print(f"  ⚠ 仮説生成エラー: {e}")

    html = template.render(
        verify_data=verify_data,
        accuracy=accuracy,
        predicted_good=total_predicted_good,
        actual_good=total_actual_good,
        surprise_good=total_surprise,
        machine_accuracy=machine_accuracy,
        total_all_units=total_all_units,
        total_good_all=total_good_all,
        result_date_str=result_date_str,
        predict_base=predict_base,
        hypotheses=hypotheses,
    )

    output_path = OUTPUT_DIR / 'verify.html'
    output_path.write_text(html, encoding='utf-8')
    print(f"  -> {output_path}")


def generate_history_pages(env):
    """各台の詳細履歴ページを生成"""
    print("Generating history pages...")

    template = env.get_template('unit_history.html')
    output_subdir = OUTPUT_DIR / 'history'
    output_subdir.mkdir(parents=True, exist_ok=True)

    from analysis.history_accumulator import load_unit_history

    old_keys = {'island_akihabara', 'shibuya_espass', 'shinjuku_espass'}
    page_count = 0

    for store_key, store in STORES.items():
        if store_key in old_keys:
            continue

        machine_key = store.get('machine', 'sbj')
        machine = get_machine_info(machine_key)
        units = store.get('units', [])

        for unit_id in units:
            unit_id_str = str(unit_id)

            # 蓄積データ読み込み
            acc_hist = load_unit_history(store_key, unit_id_str)
            acc_days = acc_hist.get('days', [])

            if not acc_days:
                # データが無い台もページだけは作成（空表示）
                html = template.render(
                    store=store,
                    store_key=store_key,
                    unit_id=unit_id_str,
                    machine=machine,
                    machine_key=machine_key,
                    days=[],
                    total_summary=None,
                )
                output_path = output_subdir / f'{store_key}_{unit_id_str}.html'
                output_path.write_text(html, encoding='utf-8')
                page_count += 1
                continue

            # 日付を新しい順にソート
            sorted_days = sorted(acc_days, key=lambda x: x.get('date', ''), reverse=True)

            # 各日にデータを整形
            template_days = []
            total_art = 0
            total_games = 0
            good_count = 0
            total_diff = 0

            for d in sorted_days:
                date_str = d.get('date', '')
                art = d.get('art', 0) or 0
                rb = d.get('rb', 0) or 0
                games = d.get('games', 0) or 0
                prob = d.get('prob', 0) or 0
                is_good = d.get('is_good', False)
                max_rensa = d.get('max_rensa', 0) or 0
                history = d.get('history', [])
                # 最大枚数: historyがあれば連チャン区間累計で再計算
                if history:
                    from analysis.analyzer import calculate_max_chain_medals
                    max_medals = calculate_max_chain_medals(history)
                else:
                    max_medals = d.get('max_medals', 0) or 0

                # 差枚計算
                diff_medals = 0
                if art > 0 and games > 0:
                    try:
                        profit = calculate_expected_profit(games, art, machine_key)
                        diff_medals = profit.get('current_estimate', 0)
                    except Exception:
                        pass

                # 日付表示フォーマット
                date_display = date_str
                try:
                    dt = datetime.strptime(date_str, '%Y-%m-%d')
                    wd = WEEKDAY_NAMES[dt.weekday()]
                    date_display = f"{dt.month}/{dt.day}({wd})"
                except Exception:
                    pass

                # 当たり履歴を時刻順にソート（古い時刻→新しい時刻）
                history_sorted = []
                if history:
                    history_sorted = sorted(history, key=lambda x: x.get('time', '00:00'))

                template_days.append({
                    'date': date_str,
                    'date_display': date_display,
                    'art': art,
                    'rb': rb,
                    'games': games,
                    'prob': prob,
                    'is_good': is_good,
                    'max_rensa': max_rensa,
                    'max_medals': max_medals,
                    'diff_medals': diff_medals,
                    'history': history,
                    'history_sorted': history_sorted,
                })

                # 全期間サマリー用
                total_art += art
                total_games += games
                if is_good:
                    good_count += 1
                total_diff += diff_medals

            # 全期間サマリー
            total_days = len(sorted_days)
            avg_prob = total_games / total_art if total_art > 0 else 0
            good_rate = round(good_count / total_days * 100) if total_days > 0 else 0

            total_summary = {
                'total_days': total_days,
                'good_days': good_count,
                'good_rate': good_rate,
                'avg_prob': round(avg_prob, 1) if avg_prob > 0 else 0,
                'total_diff_medals': total_diff,
            }

            html = template.render(
                store=store,
                store_key=store_key,
                unit_id=unit_id_str,
                machine=machine,
                machine_key=machine_key,
                days=template_days,
                total_summary=total_summary,
            )

            output_path = output_subdir / f'{store_key}_{unit_id_str}.html'
            output_path.write_text(html, encoding='utf-8')
            page_count += 1

    print(f"  -> {output_subdir}/ ({page_count} pages)")


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

    # パターンデータ記録（蓄積済みhistoryからパターン分析用データを生成）
    try:
        from analysis.pattern_detector import record_from_history
        import os
        for store_dir in os.listdir('data/history'):
            if not os.path.isdir(f'data/history/{store_dir}'):
                continue
            if '_sbj' in store_dir:
                mk = 'sbj'
            elif '_hokuto' in store_dir:
                mk = 'hokuto_tensei2'
            else:
                continue
            n = record_from_history(store_dir, mk)
            if n > 0:
                print(f"  📊 パターン記録: {store_dir} ({n}件)")
    except Exception as e:
        print(f"  ⚠ パターン記録エラー: {e}")
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
    generate_history_pages(env)
    copy_static_files()
    generate_metadata()

    print()
    print("=" * 50)
    print("静的サイト生成完了!")
    print("=" * 50)


if __name__ == '__main__':
    main()
