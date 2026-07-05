#!/usr/bin/env python3
"""
静的サイト生成スクリプト

Cloudflare Pages用に静的HTMLを生成する
GitHub Actionsで定期実行し、生成したHTMLをデプロイ
"""

import glob
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# プロジェクトルート
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# === ビルド前の仕様整合性チェック ===
from scripts.pre_build_check import run_all as _pre_build_check
_pre_build_errors = _pre_build_check()
if _pre_build_errors > 0:
    print(f'\n🛑 仕様整合性エラー {_pre_build_errors}件 — CLAUDE.md / config/rankings.py を確認してください')
    sys.exit(1)

from jinja2 import Environment, FileSystemLoader
from analysis.verdict import get_result_level, get_verdict, is_hit as v_is_hit, RESULT_MARKS
from config.rankings import STORES, MACHINES, get_stores_by_machine, get_machine_info
from analysis.recommender import recommend_units, load_daily_data, generate_store_analysis, calculate_expected_profit, analyze_today_graph, calculate_at_intervals, get_machine_from_store_key
from analysis.analyzer import calculate_first_hits, mark_first_hits
from scrapers.availability_checker import get_availability, get_realtime_data
from scripts.verify_units import get_active_alerts, get_unit_status

JST = timezone(timedelta(hours=9))
WEEKDAY_NAMES = ['月', '火', '水', '木', '金', '土', '日']

# 出力ディレクトリ
OUTPUT_DIR = PROJECT_ROOT / 'docs'  # GitHub Pages互換

# 隠し店舗（サイトに表示しない、データ収集のみ）
HIDDEN_STORES = set()
_hidden_config_path = PROJECT_ROOT / 'config' / 'hidden_stores.json'
if _hidden_config_path.exists():
    try:
        with open(_hidden_config_path) as f:
            _hidden_config = json.load(f)
            HIDDEN_STORES = set(_hidden_config.get('hidden_store_keys', []))
        print(f"隠し店舗: {len(HIDDEN_STORES)}件")
    except Exception as e:
        print(f"隠し店舗設定読込エラー: {e}")


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
    env.globals['build_time'] = datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')
    env.globals['cache_bust'] = datetime.now(JST).strftime('%Y%m%d%H%M%S')

    def generate_sparkline(history, width=120, height=40, diff_medals=None):
        """当たり履歴から差枚推移のSVGスパークラインを生成

        Args:
            history: 当たり履歴リスト
            diff_medals: 既知の最終差枚（正規化に使用）
        """
        if not history or len(history) < 2:
            return ''
        # hit_num降順（大きい=古い）でソート
        # ただし、hit_numが全て0または未設定の場合はリスト順序をそのまま使用
        hit_nums = [h.get('hit_num', 0) for h in history]
        if len(set(hit_nums)) <= 1:
            # 全て同じ値（0を含む）なので元の順序を使用
            sorted_hist = list(history)
        else:
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
        return f'<svg class="sparkline" viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet"><line x1="0" y1="{zero_y:.1f}" x2="{width}" y2="{zero_y:.1f}" stroke="#555" stroke-width="0.5" stroke-dasharray="2,2"/><polyline points="{polyline}" fill="none" stroke="{color}" stroke-width="1.5"/></svg>'
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
        'shibuya_espass': {
            'name': 'エスパス日拓渋谷新館',
            'short_name': 'エスパス渋谷新館',
            'day_ratings': {'月': 3, '火': 4, '水': 4, '木': 5, '金': 3, '土': 3, '日': 1},
            'best_note': '木曜が最強、火水も狙い目',
            'worst_note': '日曜は避けるべき',
            'overall_rating': 3,
            'machine_links': [
                {'store_key': 'shibuya_espass_tokyoghoul', 'icon': '🫀', 'short_name': '東京喰種'},
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
                {'store_key': 'shinjuku_espass_tokyoghoul', 'icon': '🫀', 'short_name': '東京喰種'},
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
                {'store_key': 'akiba_espass_tokyoghoul', 'icon': '🫀', 'short_name': '東京喰種'},
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
                {'store_key': 'seibu_shinjuku_espass_tokyoghoul', 'icon': '🫀', 'short_name': '東京喰種'},
            ],
        },
    }

    # 前日の答え合わせデータ（予測ランクの参照用）
    verify_lookup = {}  # {store_key: {unit_id: {predicted_rank, predicted_score, ...}}}
    try:
        import datetime as _dt_mod
        _yesterday_str = (_dt_mod.datetime.now(JST) - _dt_mod.timedelta(days=1)).strftime('%Y%m%d')
        _verify_path = Path(f'data/verify/verify_{_yesterday_str}.json')
        if _verify_path.exists():
            import json as _json_mod
            _verify_data = _json_mod.loads(_verify_path.read_text())
            for _vsk, _vunits in _verify_data.items():
                if isinstance(_vunits, list):
                    verify_lookup[_vsk] = {str(u['unit_id']): u for u in _vunits}
            print(f"  verify データ読込: {len(verify_lookup)}店舗分")
    except Exception as e:
        print(f"  verify データ読込エラー（続行）: {e}")

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
                # 営業中のみ使用。開店前/閉店後はstaleデータを使わない
                realtime = None
                if is_open:
                    try:
                        realtime = get_realtime_data(store_key)
                    except:
                        pass

                recs = recommend_units(store_key, realtime_data=realtime, availability=availability,
                                      data_date_label=reason_data_label, prev_date_label=reason_prev_label)
                
                # availability.jsonから直接today_historyを取得してセット
                # （recommenderが設定しない場合のフォールバック）
                # v1形式では 'history' として保存されるため両方参照
                if realtime and 'units' in realtime:
                    for r in recs:
                        if not r.get('today_history'):
                            for ru in realtime.get('units', []):
                                if str(ru.get('unit_id')) == str(r.get('unit_id')):
                                    rt_hist = ru.get('today_history') or ru.get('history', [])
                                    if rt_hist:
                                        r['today_history'] = rt_hist
                                    break
                
                # recommenderが返すtoday関連データの整合性チェック
                # today_historyからart/rensa/diff_medalsを再計算して一貫性を保つ
                from analysis.analyzer import calculate_max_rensa as _calc_rensa
                for r in recs:
                    _art = r.get('art_count', 0)
                    _games = r.get('total_games', 0)
                    _rensa = r.get('today_max_rensa', 0)
                    _hist = r.get('today_history', [])
                    
                    # today_historyの整合性チェック
                    # recommenderのart_count（daidataソース）とtoday_history（JSONソース）が
                    # 異なる日のデータを参照してる場合がある
                    if _hist:
                        _hist_art = sum(1 for h in _hist if h.get('type', '') in ('ART', 'AT', 'BB'))
                        _hist_rb = sum(1 for h in _hist if h.get('type', '') == 'RB')
                        _mk = r.get('machine_key', '') or get_machine_from_store_key(store_key)
                        _hist_rensa = _calc_rensa(_hist, machine_key=_mk)
                        
                        # art_countとhistory ART数が近ければ同じ日のデータとみなす
                        # （データ取得タイミングのズレで数回の差が出る）
                        if _art > 0 and abs(_art - _hist_art) <= 5:
                            # ほぼ一致 → historyからrensaを再計算
                            r['today_max_rensa'] = _hist_rensa
                        elif _art > 0 and _hist_art > 0 and _art >= _hist_art:
                            # art_countの方が多い → データ取得後に増えただけ、historyは有効
                            r['today_max_rensa'] = _hist_rensa
                        elif _art > 0 and _hist_art > _art * 2:
                            # historyの方が大幅に多い（2倍以上） → 別日のstale
                            r['today_history'] = []
                            r['today_max_rensa'] = 0
                        elif _art == 0 and _hist_art > 0:
                            # art_count未設定だがhistoryあり → historyベースで表示
                            r['art_count'] = _hist_art
                            r['rb_count'] = _hist_rb
                            r['today_max_rensa'] = _hist_rensa
                        # else: 両方0 → 何もしない
                    
                    # historyがない場合のクリーンアップ
                    elif _art == 0:
                        r['today_max_rensa'] = 0
                        r['diff_medals'] = 0
                        # 「本日」を含む理由テキストもクリーンアップ
                        for key in ('today_reasons', 'reasons'):
                            if key in r and r[key]:
                                r[key] = [reason for reason in r[key] 
                                          if '本日' not in reason]
                    
                    # 稼働開始後（ART > 0）は昨日ベースの理由をクリア
                    if _art > 0:
                        stale_patterns = ['途中放棄', '最終', 'やめ', '狙い余地']
                        for key in ('today_reasons', 'reasons'):
                            if key in r and r[key]:
                                r[key] = [reason for reason in r[key] 
                                          if not any(p in reason for p in stale_patterns)]

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

                # TOP3候補（上位3台/店舗）- 隠し店舗は除外
                if store_key not in HIDDEN_STORES:
                    for rec in recs[:3]:
                        top3_all.append(rec)

                # 前日の爆発台（全台から収集、yesterday_art > 0）- 隠し店舗は除外
                if store_key in HIDDEN_STORES:
                    continue  # 隠し店舗はスキップ
                for rec in recs:
                    y_art = rec.get('yesterday_art', 0)
                    if y_art and y_art > 0:
                        y_games = rec.get('yesterday_games', 0)
                        y_prob = int(y_games / y_art) if y_art > 0 and y_games > 0 else 0
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
                        # 前日の予想ランクを取得（verifyデータ優先、なければ現在のランク）
                        unit_str = str(rec['unit_id'])
                        _vunit = verify_lookup.get(store_key, {}).get(unit_str, {})
                        predicted_rank = _vunit.get('predicted_rank', rec.get('final_rank', 'C'))
                        predicted_score = _vunit.get('predicted_score', rec.get('final_score', 50))
                        was_predicted_good = predicted_rank in ('S', 'A')
                        # 的中判定（verdict.py共通ロジック）
                        _y_diff = rec.get('yesterday_diff_medals', rec.get('diff_medals', 0))
                        _y_max = rec.get('yesterday_max_medals', rec.get('max_medals', 0))
                        _y_rl = get_result_level(y_prob, _y_diff, key, max_medals=_y_max)
                        _y_vtext, _y_vcls = get_verdict(predicted_rank, _y_rl)
                        if v_is_hit(predicted_rank, _y_rl):
                            prediction_result = 'hit'
                        elif _y_vcls == 'surprise':
                            prediction_result = 'missed'
                        elif _y_vcls == 'miss':
                            prediction_result = 'miss'
                        else:
                            prediction_result = 'correct'

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
                            'payout_estimate': y_si.get('payout_estimate', 100.0) if y_si else 100.0,
                            'predicted_rank': predicted_rank,
                            'predicted_score': predicted_score,
                            'prediction_result': prediction_result,
                            'yesterday_history': y_hist_marked,
                            'today_history': t_hist_marked,
                            'recent_days': rec.get('recent_days', []),
                            'first_hit_count': y_first_hit_count,
                            # 前々日・3日前データ（ART=0の場合はdiff_medals/max_medalsを0に）
                            'day_before_art': rec.get('day_before_art', 0),
                            'day_before_rb': rec.get('day_before_rb', 0),
                            'day_before_games': rec.get('day_before_games', 0),
                            'day_before_date': rec.get('day_before_date', ''),
                            'day_before_diff_medals': rec.get('day_before_diff_medals', 0) if rec.get('day_before_art', 0) > 0 else 0,
                            'day_before_max_rensa': rec.get('day_before_max_rensa', 0),
                            'day_before_max_medals': rec.get('day_before_max_medals', 0) if rec.get('day_before_art', 0) > 0 else 0,
                            'three_days_ago_art': rec.get('three_days_ago_art', 0),
                            'three_days_ago_rb': rec.get('three_days_ago_rb', 0),
                            'three_days_ago_games': rec.get('three_days_ago_games', 0),
                            'three_days_ago_date': rec.get('three_days_ago_date', ''),
                            # ART=0の場合はdiff_medals/max_medalsを0に（不正な値を防止）
                            'three_days_ago_diff_medals': rec.get('three_days_ago_diff_medals', 0) if rec.get('three_days_ago_art', 0) > 0 else 0,
                            'three_days_ago_max_rensa': rec.get('three_days_ago_max_rensa', 0),
                            'three_days_ago_max_medals': rec.get('three_days_ago_max_medals', 0) if rec.get('three_days_ago_art', 0) > 0 else 0,
                        })

                # 本日の爆発台（全台から収集、art_count > 0）
                for rec in recs:
                    t_art = rec.get('art_count', 0)
                    t_games = rec.get('total_games', 0)
                    if t_art > 0:
                        # 初当たり計算（先にhistoryを取得）
                        t_hist_raw2 = rec.get('today_history', [])
                        # 差枚: DAIDATAの実データを使用（理論値は使わない）
                        diff_medals = 0
                        if realtime and 'units' in realtime:
                            for ru in realtime.get('units', []):
                                if str(ru.get('unit_id')) == str(rec.get('unit_id')):
                                    diff_medals = ru.get('diff_medals', 0) or 0
                                    break
                        # max_medals: historyから連チャン合計枚数を計算（最大連チャン中の獲得枚数合計）
                        from analysis.history_accumulator import _calc_history_stats as _calc_stats
                        _, t_medals = _calc_stats(t_hist_raw2)
                        if not t_medals:
                            t_medals = rec.get('max_medals', 0)
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
    # 営業中モードでは、本日のリアルタイムデータがある台のみを対象とする（昨日のデータを誤表示しない）
    # 営業時間外は昨日のデータがあればOK
    if is_open:
        top3_candidates = [r for r in top3_all if r.get('final_rank') in ('S', 'A') and (r.get('art_count', 0) > 0 or r.get('total_games', 0) > 0)]
    else:
        # 営業時間外: 昨日のデータがあればOK
        top3_candidates = [r for r in top3_all if r.get('final_rank') in ('S', 'A') and (r.get('art_count', 0) > 0 or r.get('total_games', 0) > 0 or r.get('yesterday_art', 0) > 0 or r.get('yesterday_games', 0) > 0)]
    # スコア順（信頼度・試行回数を考慮した総合スコア）
    top3_candidates.sort(key=lambda r: -r.get('final_score', 0))

    # 各機種から1台ずつ確保 + 重複台排除（最大30台）
    top3 = []
    seen_machines = set()
    seen_units = set()
    for r in top3_candidates:
        mk = r.get('machine_key', '')
        uid = str(r.get('unit_id', ''))
        if mk not in seen_machines and uid not in seen_units:
            top3.append(r)
            seen_machines.add(mk)
            seen_units.add(uid)
        if len(top3) >= len(MACHINES):
            break
    # 残り枠をスコア順で埋める（30台まで）
    for r in top3_candidates:
        uid = str(r.get('unit_id', ''))
        if uid not in seen_units:
            top3.append(r)
            seen_units.add(uid)
        if len(top3) >= 30:
            break
    if not top3:
        top3 = top3_candidates[:30]

    # TOP3 + 全S/A候補 + 爆発台: 蓄積DBから前日/前々日/3日前 + recent_daysを一括補完
    # 全recの蓄積DB補完を一括実行（enrich_rec.py: 1箇所で全パスを処理）
    from scripts.enrich_rec import enrich_recs
    all_recs_to_enrich = list({id(r): r for r in top3 + top3_candidates + yesterday_top10 + today_top10}.values())
    enrich_recs(all_recs_to_enrich)

    # 閉店後/当日データなしの場合、payout_estimateをyesterdayデータから再計算
    for rec in top3 + top3_candidates + yesterday_top10 + today_top10:
        if rec.get('payout_estimate', 100.0) == 100.0 or rec.get('art_count', 0) == 0:
            y_art = rec.get('yesterday_art', 0)
            y_games = rec.get('yesterday_games', 0)
            if y_art > 0 and y_games > 0:
                _mk = _get_machine_key(rec.get('store_key', ''))
                y_profit = calculate_expected_profit(y_games, y_art, _mk)
                y_si = y_profit.get('setting_info', {})
                rec['payout_estimate'] = y_si.get('payout_estimate', 100.0)
                rec['setting_num'] = y_si.get('setting_num', 0)
                rec['estimated_setting'] = y_si.get('estimated_setting', '')
    
    # 日付を固定化（前日、2日前、3日前、...7日前）
    # データの日付が正しくなければクリア
    fixed_dates = [(now - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(1, 8)]
    fixed_yesterday = fixed_dates[0]
    fixed_day_before = fixed_dates[1]
    fixed_three_days = fixed_dates[2]
    
    # 古すぎるデータをクリア（7日以上前のデータは除外）
    cutoff_date = (datetime.now(JST) - timedelta(days=7)).strftime('%Y-%m-%d')

    # all_sa_recsもループに含める（#11以降のrecsの日付も設定するため）
    all_recs_for_date_fix = list({id(r): r for r in top3 + top3_candidates + yesterday_top10 + today_top10}.values())
    
    for rec in all_recs_for_date_fix:
        # 日付を強制的に固定化（recommenderが「最新データ」を返す問題の対策）
        # 常に蓄積DBから正しい日付のデータを取得する
        store_key = rec.get('store_key', '')
        unit_id = str(rec.get('unit_id', ''))
        
        # 日付は常に設定（データがなくても「データなし」表示のため）
        rec['yesterday_date'] = fixed_yesterday
        rec['day_before_date'] = fixed_day_before
        rec['three_days_ago_date'] = fixed_three_days
        
        # gamesも初期化（データがない場合のデフォルト）
        if 'day_before_games' not in rec:
            rec['day_before_games'] = 0
        if 'three_days_ago_games' not in rec:
            rec['three_days_ago_games'] = 0
        
        if store_key and unit_id:
            # yesterday_dateが昨日でない場合、蓄積DBから正しいデータを取得
            try:
                from analysis.history_accumulator import load_unit_history
                acc = load_unit_history(store_key, unit_id)
                if acc and acc.get('days'):
                    days_by_date = {d['date']: d for d in acc['days'] if d.get('date')}
                    
                    # 昨日のデータ
                    if fixed_yesterday in days_by_date:
                        yd = days_by_date[fixed_yesterday]
                        yd_art = yd.get('art', 0)
                        yd_games = yd.get('games', 0) or yd.get('total_start', 0)
                        yd_hist = yd.get('history', [])
                        rec['yesterday_art'] = yd_art
                        rec['yesterday_rb'] = yd.get('rb', 0)
                        rec['yesterday_games'] = yd_games
                        # diff_medalsは蓄積DB優先、なければhistoryから計算
                        yd_diff = yd.get('diff_medals')
                        if yd_diff is None and yd_hist and yd_games > 0:
                            from analysis.diff_medals_estimator import estimate_diff_medals as _est_yd
                            total_medals = sum(h.get('medals', 0) for h in yd_hist)
                            _mk_yd = rec.get('machine_key') or _get_machine_key(rec.get('store_key')) or 'tokyoghoul'
                            yd_diff = _est_yd(total_medals, yd_games, _mk_yd)
                        rec['yesterday_diff_medals'] = yd_diff if yd_art > 0 else 0
                        rec['yesterday_max_rensa'] = yd.get('max_rensa', 0)
                        if yd_art > 0 and yd_hist:
                            from analysis.history_accumulator import _calc_history_stats as _calc_stats
                            _, y_max_m = _calc_stats(yd_hist)
                            rec['yesterday_max_medals'] = max(yd.get('max_medals', 0), y_max_m) if y_max_m > 0 else yd.get('max_medals', 0)
                        else:
                            rec['yesterday_max_medals'] = 0
                        rec['yesterday_history'] = yd_hist
                    else:
                        # 前日データなし: 古いデータをクリア（recommenderからの古いデータを上書き）
                        rec['yesterday_art'] = 0
                        rec['yesterday_rb'] = 0
                        rec['yesterday_games'] = 0
                        rec['yesterday_diff_medals'] = 0
                        rec['yesterday_max_rensa'] = 0
                        rec['yesterday_max_medals'] = 0
                        rec['yesterday_history'] = []
                        rec['yesterday_date'] = ''  # 日付もクリア（テンプレートで「データなし」を表示しない）
                        rec['_no_yesterday_data'] = True  # フィルタリング用フラグ
                    
                    # 前々日のデータ
                    if fixed_day_before in days_by_date:
                        dbd = days_by_date[fixed_day_before]
                        dbd_art = dbd.get('art', 0)
                        dbd_games = dbd.get('games', 0) or dbd.get('total_start', 0)
                        dbd_hist = dbd.get('history', [])
                        rec['day_before_art'] = dbd_art
                        rec['day_before_rb'] = dbd.get('rb', 0)
                        rec['day_before_games'] = dbd_games
                        # diff_medalsは蓄積DB優先、なければhistoryから計算
                        dbd_diff = dbd.get('diff_medals')
                        if dbd_diff is None and dbd_hist and dbd_games > 0:
                            from analysis.diff_medals_estimator import estimate_diff_medals as _est_dbd
                            total_medals = sum(h.get('medals', 0) for h in dbd_hist)
                            _mk_dbd = rec.get('machine_key') or _get_machine_key(rec.get('store_key')) or 'tokyoghoul'
                            dbd_diff = _est_dbd(total_medals, dbd_games, _mk_dbd)
                        rec['day_before_diff_medals'] = dbd_diff if dbd_art > 0 else 0
                        rec['day_before_max_rensa'] = dbd.get('max_rensa', 0)
                        if dbd_art > 0 and dbd_hist:
                            from analysis.history_accumulator import _calc_history_stats as _calc_stats
                            _, db_max_m = _calc_stats(dbd_hist)
                            rec['day_before_max_medals'] = max(dbd.get('max_medals', 0), db_max_m) if db_max_m > 0 else dbd.get('max_medals', 0)
                        else:
                            rec['day_before_max_medals'] = 0
                        rec['day_before_history'] = dbd_hist
                    else:
                        # 前々日データなし: 古いデータをクリア
                        rec['day_before_art'] = 0
                        rec['day_before_rb'] = 0
                        rec['day_before_games'] = 0
                        rec['day_before_diff_medals'] = 0
                        rec['day_before_max_rensa'] = 0
                        rec['day_before_max_medals'] = 0
                        rec['day_before_history'] = []
                        rec['day_before_date'] = ''  # 日付もクリア
                    
                    # 3日前のデータ
                    if fixed_three_days in days_by_date:
                        tdd = days_by_date[fixed_three_days]
                        tdd_art = tdd.get('art', 0)
                        tdd_games = tdd.get('games', 0) or tdd.get('total_start', 0)
                        tdd_hist = tdd.get('history', [])
                        rec['three_days_ago_art'] = tdd_art
                        rec['three_days_ago_rb'] = tdd.get('rb', 0)
                        rec['three_days_ago_games'] = tdd_games
                        # diff_medalsは蓄積DB優先、なければhistoryから計算
                        tdd_diff = tdd.get('diff_medals')
                        if tdd_diff is None and tdd_hist and tdd_games > 0:
                            from analysis.diff_medals_estimator import estimate_diff_medals as _est_tdd
                            total_medals = sum(h.get('medals', 0) for h in tdd_hist)
                            _mk_tdd = rec.get('machine_key') or _get_machine_key(rec.get('store_key')) or 'tokyoghoul'
                            tdd_diff = _est_tdd(total_medals, tdd_games, _mk_tdd)
                        rec['three_days_ago_diff_medals'] = tdd_diff if tdd_art > 0 else 0
                        rec['three_days_ago_max_rensa'] = tdd.get('max_rensa', 0)
                        if tdd_art > 0 and tdd_hist:
                            from analysis.history_accumulator import _calc_history_stats as _calc_stats
                            _, tdd_max_m = _calc_stats(tdd_hist)
                            rec['three_days_ago_max_medals'] = max(tdd.get('max_medals', 0), tdd_max_m) if tdd_max_m > 0 else tdd.get('max_medals', 0)
                        else:
                            rec['three_days_ago_max_medals'] = 0
                        rec['three_days_ago_history'] = tdd_hist
                    else:
                        # 3日前データなし: 古いデータをクリア
                        rec['three_days_ago_art'] = 0
                        rec['three_days_ago_rb'] = 0
                        rec['three_days_ago_games'] = 0
                        rec['three_days_ago_diff_medals'] = 0
                        rec['three_days_ago_max_rensa'] = 0
                        rec['three_days_ago_max_medals'] = 0
                        rec['three_days_ago_history'] = []
                        rec['three_days_ago_date'] = ''  # 日付もクリア
            except Exception:
                pass
        
        # 前日データのチェック（7日以上前は除外）
        y_date = rec.get('yesterday_date', '')
        if y_date and y_date < cutoff_date:
            rec['yesterday_art'] = 0
            rec['yesterday_rb'] = 0
            rec['yesterday_games'] = 0
            rec['yesterday_diff_medals'] = 0
            rec['yesterday_max_rensa'] = 0
            rec['yesterday_max_medals'] = 0
            rec['yesterday_history'] = []
            rec['yesterday_date'] = ''

        # 2日前データのチェック（7日以上前は除外）
        db_date = rec.get('day_before_date', '')
        if db_date and db_date < cutoff_date:
            rec['day_before_art'] = 0
            rec['day_before_rb'] = 0
            rec['day_before_games'] = 0
            rec['day_before_diff_medals'] = 0
            rec['day_before_max_rensa'] = 0
            rec['day_before_max_medals'] = 0
            rec['day_before_history'] = []
            rec['day_before_date'] = ''

        # 3日前データのチェック（7日以上前は除外）
        td_date = rec.get('three_days_ago_date', '')
        if td_date and td_date < cutoff_date:
            rec['three_days_ago_art'] = 0
            rec['three_days_ago_rb'] = 0
            rec['three_days_ago_games'] = 0
            rec['three_days_ago_diff_medals'] = 0
            rec['three_days_ago_max_rensa'] = 0
            rec['three_days_ago_max_medals'] = 0
            rec['three_days_ago_history'] = []
            rec['three_days_ago_date'] = fixed_three_days
        
        # recent_daysの補完はenrich_rec.pyで実施済み（重複処理を削除）
        if rec.get('recent_days'):
            new_recent_days = []
            for i, fixed_date in enumerate(fixed_dates[:7]):
                # 該当日付のデータを探す
                matching_day = None
                for day in rec.get('recent_days', []):
                    if day.get('date') == fixed_date:
                        matching_day = day
                        break
                # games>0（稼働あり）の日を表示
                # art=0でもgames>0なら「稼働したがART未当選」として正常データ
                if matching_day and matching_day.get('games', 0) > 0:
                    new_recent_days.append(matching_day)
            rec['recent_days'] = new_recent_days

    # TOP3 + 全S/A候補 + 爆発台の過去3日分+当日の当たり履歴を加工
    for rec in top3 + top3_candidates + yesterday_top10 + today_top10:
        _mk = _get_machine_key(rec.get('store_key', ''))
        for hist_key in ('yesterday_history', 'day_before_history', 'three_days_ago_history', 'today_history'):
            raw_hist = rec.get(hist_key, [])
            proc_key = f'{hist_key}_processed'
            summ_key = f'{hist_key}_summary'
            if proc_key not in rec:  # 重複加工防止
                if raw_hist:
                    processed, summary = _process_history_for_verify(raw_hist, machine_key=_mk)
                    rec[proc_key] = processed
                    rec[summ_key] = summary
                    # today_historyは加工済みで上書き（テンプレートで直接使用）
                    if hist_key == 'today_history':
                        rec['today_history'] = processed
                else:
                    rec[proc_key] = []
                    rec[summ_key] = {}

        # recent_daysの各日のhistoryも加工（連チャン表記・天井判定用）
        for day in rec.get('recent_days', []):
            raw_hist = day.get('history', [])
            if raw_hist and 'history_processed' not in day:
                processed, summary = _process_history_for_verify(raw_hist, machine_key=_mk)
                day['history_processed'] = processed
                day['history_summary'] = summary
            
            # historyから diff_medals / max_medals / max_rensa を計算（データがない場合）
            if raw_hist and not day.get('diff_medals'):
                total_medals = sum(h.get('medals', 0) for h in raw_hist)
                games = day.get('games', 0) or day.get('total_start', 0)
                if games > 0:
                    from analysis.diff_medals_estimator import estimate_diff_medals
                    day['diff_medals'] = estimate_diff_medals(total_medals, games, _mk)
            if raw_hist:
                # historyから連チャン合計枚数を計算し、保存値と大きい方を使用
                from analysis.history_accumulator import _calc_history_stats as _calc_stats
                _, calc_max = _calc_stats(raw_hist)
                if calc_max > 0:
                    day['max_medals'] = max(day.get('max_medals', 0), calc_max)
            if raw_hist and not day.get('max_rensa'):
                # 最大連チャンを計算
                max_rensa = 0
                for h in raw_hist:
                    rensa_str = h.get('rensa', '')
                    if rensa_str and '連' in str(rensa_str):
                        try:
                            rensa_num = int(str(rensa_str).replace('連', ''))
                            max_rensa = max(max_rensa, rensa_num)
                        except:
                            pass
                day['max_rensa'] = max_rensa

    # 前日の爆発台: 最大連チャン枚数でソート
    # 差枚だと「万枚出して飲まれた台」が低く出る。
    # max_chain（1回の連チャン区間の累計枚数）なら爆発の瞬間を正しく評価。
    # 前日の爆発台も差枚優先
    
    # ★ 前日データがない台を除外（古いデータの誤表示防止）
    # yesterday_gamesが0 = 前日のデータ取得失敗 or 稼働なし
    yesterday_top10 = [r for r in yesterday_top10 if r.get('yesterday_games', 0) > 0]
    
    yesterday_top10.sort(key=lambda x: (-(x.get('yesterday_diff_medals') or x.get('diff_medals') or 0), -(x.get('yesterday_max_medals') or 0)))
    yesterday_top10 = yesterday_top10[:10]
    
    # ★ 営業時間外の場合、おすすめ台TOP10からも前日データがない台を除外
    # （営業中は当日データがあれば表示したいので除外しない）
    if not is_open:
        top3 = [r for r in top3 if r.get('yesterday_games', 0) > 0 or r.get('total_games', 0) > 0]
        top3_candidates = [r for r in top3_candidates if r.get('yesterday_games', 0) > 0 or r.get('total_games', 0) > 0]

    # 本日の爆発台: 最大連チャン枚数でソート
    # 爆発台は差枚優先（朝から座ってたらいくら勝てたか）
    today_top10.sort(key=lambda x: (-x.get('diff_medals', 0), -x.get('max_medals', 0)))
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
    recommend_links.sort(key=lambda x: next(
        (-s['today_rating'] for s in all_stores if any(
            ml.get('store_key') == x['store_key'] for ml in s.get('machine_links', [])
        )), 0
    ))

    # 店舗ナビ（店名で重複排除、最初の機種リンクを使う）
    seen_stores = set()
    store_nav_links = []
    for info in sorted(store_day_ratings.values(), key=lambda x: -x.get('today_rating', 0)):
        sname = info['short_name']
        if sname in seen_stores:
            continue
        seen_stores.add(sname)
        mls = info.get('machine_links', [])
        if mls:
            store_nav_links.append({
                'name': sname,
                'store_key': mls[0].get('store_key', ''),
                'machine_links': [{'store_key': ml.get('store_key', ''), 'icon': ml.get('icon', ''), 'machine_name': MACHINES.get(STORES.get(ml.get('store_key', ''), {}).get('machine', 'sbj'), {}).get('short_name', '')} for ml in mls],
            })

    night_mode = is_night_mode()
    tomorrow = now + timedelta(days=1)
    tomorrow_str = format_date_with_weekday(tomorrow)
    yesterday = now - timedelta(days=1)
    yesterday_str = format_date_with_weekday(yesterday)

    # 「本日」「前日」を日付付きに
    # 営業中: 本日の日付を表示（リアルタイムデータ）
    # 開店前/閉店後: 前日の日付を表示（蓄積データ）
    if is_open:
        data_date_str = format_date_with_weekday(now)  # 本日
        prev_date_str = format_date_with_weekday(yesterday)  # 前日
    else:
        data_date_str = format_date_with_weekday(yesterday)  # 前日
        prev_date_str = format_date_with_weekday(yesterday - timedelta(days=1))  # 前々日

    # 機種別的中率（ヒーロー表示用: verifyデータから取得）
    accuracy_hero = []
    verify_data = _get_latest_valid_verify()
    if verify_data and verify_data.get('units'):
        # store_key → machine_key のマッピングを事前に構築
        _store_to_machine = {}
        for _mk in MACHINES:
            for _sk in get_stores_by_machine(_mk):
                _store_to_machine[_sk] = _mk

        # 機種別に集計
        machine_stats = {}  # {machine_key: {total, hit, stores: {store_key: {total, hit}}}}
        for store_key, units in verify_data['units'].items():
            mk = _store_to_machine.get(store_key)
            if not mk:
                continue
            if mk not in machine_stats:
                machine_stats[mk] = {'total': 0, 'hit': 0, 'stores': {}}
            ms = machine_stats[mk]
            if store_key not in ms['stores']:
                ms['stores'][store_key] = {'total': 0, 'hit': 0}
            ss = ms['stores'][store_key]
            for u in units:
                if u.get('predicted_rank') in ('S', 'A') and u.get('actual_prob', 0) > 0:
                    ms['total'] += 1
                    ss['total'] += 1
                    # 的中判定: verdict_class/result_level/prediction_resultの順で確認
                    vc = u.get('verdict_class', '')
                    rl = u.get('result_level', '')
                    pr = u.get('prediction_result', '')
                    if vc == 'hit' or pr in ('hit', 'excellent') or rl == 'good':
                        ms['hit'] += 1
                        ss['hit'] += 1
                    elif not vc and not pr and not rl:
                        # フォールバック: 機種別good_prob閾値で判定
                        _good = get_machine_threshold(mk, 'good_prob') or 130
                        if u.get('actual_prob', 999) <= _good:
                            ms['hit'] += 1
                            ss['hit'] += 1

        for machine_key, machine in MACHINES.items():
            ms = machine_stats.get(machine_key, {'total': 0, 'hit': 0, 'stores': {}})
            rate = (ms['hit'] / ms['total'] * 100) if ms['total'] > 0 else 0

            # 店舗別の結果
            store_results = []
            for sk, ss in ms['stores'].items():
                if ss['total'] > 0:
                    s_rate = ss['hit'] / ss['total'] * 100
                    stores_config = get_stores_by_machine(machine_key)
                    store_info = stores_config.get(sk, {})
                    short_name = store_info.get('name', sk).replace('エスパス日拓', '').replace('店', '')
                    store_results.append({'name': short_name, 'rate': s_rate, 'hit': ss['hit'], 'total': ss['total']})
            store_results.sort(key=lambda x: -x['rate'])
            top_parts = []
            for sr in store_results[:3]:
                if sr['rate'] >= 100:
                    top_parts.append(f"{sr['name']}全的中")
                elif sr['rate'] >= 50:
                    top_parts.append(f"{sr['name']}{sr['hit']}/{sr['total']}")
            top_stores = ' / '.join(top_parts) if top_parts else ''

            accuracy_hero.append({
                'name': machine['short_name'],
                'icon': machine['icon'],
                'rate': rate,
                'hit': ms['hit'],
                'total': ms['total'],
                'top_stores': top_stores,
            })
    # 高い順にソート
    accuracy_hero.sort(key=lambda x: -x['rate'])

    # フィルター用: S/A台をスコア順で最大20台（TOP3以外も含む）
    all_sa_recs = []
    seen_sa = set()
    for r in top3:
        key = f"{r.get('store_key')}_{r.get('unit_id')}"
        seen_sa.add(key)
    for r in top3_candidates:
        key = f"{r.get('store_key')}_{r.get('unit_id')}"
        if key not in seen_sa:
            all_sa_recs.append(r)
            seen_sa.add(key)
        if len(all_sa_recs) >= 17:  # TOP3 + 17 = 20台
            break

    # フィルター用の機種・店舗リスト
    filter_machines = []
    filter_machine_keys = set()
    filter_stores_by_name = {}  # 表示名でグルーピング（同じ店舗のSBJ/北斗を1つに）
    for r in top3 + all_sa_recs:
        mk = r.get('machine_key', '')
        mn = r.get('machine_name', '')
        sk = r.get('store_key', '')
        sn = r.get('store_name', '')
        if mk and mk not in filter_machine_keys:
            filter_machines.append({'key': mk, 'name': mn, 'icon': r.get('machine_icon', '🎰')})
            filter_machine_keys.add(mk)
        if sn and sn not in filter_stores_by_name:
            filter_stores_by_name[sn] = []
        if sn and sk:
            if sk not in [x['key'] for x in filter_stores_by_name.get(sn, [])]:
                filter_stores_by_name[sn].append({'key': sk, 'name': sn})
    filter_stores = [{'name': name, 'store_keys': [x['key'] for x in stores]} for name, stores in filter_stores_by_name.items()]

    # データ取得時刻を取得 + 古いデータ警告
    data_fetched_at = ''
    is_data_stale = False
    data_stale_reason = ''
    try:
        from scrapers.availability_checker import get_daidata_availability
        avail_data = get_daidata_availability()
        fetched_at_str = avail_data.get('fetched_at', '')
        if fetched_at_str:
            fetched_dt = datetime.fromisoformat(fetched_at_str)
            data_fetched_at = f"{fetched_dt.month}/{fetched_dt.day} {fetched_dt.strftime('%H:%M')}"
            
            # 古いデータチェック
            now_jst = datetime.now(JST)
            fetched_date = fetched_dt.strftime('%Y-%m-%d')
            today_date_check = now_jst.strftime('%Y-%m-%d')
            age_minutes = (now_jst - fetched_dt).total_seconds() / 60
            
            # 営業時間中（10:00-23:00）のみチェック
            if 10 <= now_jst.hour < 23:
                if fetched_date != today_date_check:
                    is_data_stale = True
                    data_stale_reason = f'データが{fetched_dt.month}/{fetched_dt.day}のものです'
                elif age_minutes > 60:
                    is_data_stale = True
                    data_stale_reason = f'データが{int(age_minutes)}分前のものです'
    except:
        pass

    # API JSON生成（realtime.jsのPythonAnywhere障害時フォールバック用）
    try:
        _api_dir = OUTPUT_DIR / 'api' / 'v2'
        _api_dir.mkdir(parents=True, exist_ok=True)
        _index_api = {
            'updated_at': datetime.now(JST).isoformat(),
            'display_mode': display_mode,
            'is_open': is_open,
            'top3': [
                {
                    'store_key': r.get('store_key', ''),
                    'store_name': r.get('store_name', ''),
                    'machine_icon': r.get('machine_icon', '🎰'),
                    'unit_id': r.get('unit_id', ''),
                    'final_rank': r.get('final_rank', 'C'),
                    'availability': r.get('availability', ''),
                    'today_art': r.get('art_count', 0),
                    'max_medals': r.get('max_medals', 0),
                    'reasons': r.get('reasons', []),
                }
                for r in top3
            ],
        }
        (_api_dir / 'index.json').write_text(json.dumps(_index_api, ensure_ascii=False), encoding='utf-8')
    except Exception as _e:
        print(f"  [warn] API JSON(index)生成失敗: {_e}")

    html = template.render(
        machines=machines,
        top3=top3,
        all_sa_recs=all_sa_recs,
        filter_stores=filter_stores,
        filter_machines=filter_machines,
        yesterday_top10=yesterday_top10,
        today_top10=today_top10,
        today_weekday=today_weekday,
        today_date=today_date,
        today_date_formatted=today_date_formatted,
        now_time=now.strftime('%H:%M'),
        data_fetched_at=data_fetched_at,
        is_data_stale=is_data_stale,
        data_stale_reason=data_stale_reason,
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
        verify_date_str=_get_verify_date_str(),
        verify_accuracy=_get_verify_accuracy(),
        verify_rate=_get_verify_accuracy_prob_based()[0],
        verify_hit=_get_verify_accuracy_prob_based()[1],
        verify_total=_get_verify_accuracy_prob_based()[2],
        verify_examples=_get_verify_examples(),
        verify_highlights=_get_verify_highlights(),
        verify_categories=_get_verify_by_category(),
        date_prefix=date_prefix,
        next_day_prefix=next_day_prefix,
        next_day_str=next_day_str,
        recommend_links=recommend_links,
        store_nav_links=store_nav_links,
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

        now = datetime.now(JST)
        html = template.render(
            machine=machine,
            machine_key=machine_key,
            stores=store_list,
            now_short=now.strftime('%m%d_%H:%M'),
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


def get_next_day_prefix():
    """次の営業日の表記（本日/明日）を返す"""
    now = datetime.now(JST)
    if now.hour >= 22 and now.minute >= 45:
        # 22:45〜23:59 → 「明日」
        return '明日'
    else:
        # 0:00〜22:44 → 「本日」
        return '本日'


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
            # 隠し店舗はランキングに含めない
            if store_key in HIDDEN_STORES:
                continue

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
        # 蓄積DB補完（共通関数）
        from scripts.enrich_rec import enrich_recs as _enrich
        _enrich(all_recommendations)
        
        top_recs = [r for r in all_recommendations if r['final_rank'] in ('S', 'A') and not r['is_running']][:10]
        other_recs = [r for r in all_recommendations if r not in top_recs][:20]

        next_day_prefix = get_next_day_prefix()
        html = template.render(
            machine=machine,
            machine_key=machine_key,
            top_recs=top_recs,
            other_recs=other_recs,
            total_count=len(all_recommendations),
            night_mode=night_mode,
            next_day_prefix=next_day_prefix,
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
                store_analysis['overall'] = f"好調台が非常に多い（全{total}台中{high_count}台がA以上）"
            elif high_ratio >= 50:
                store_analysis['overall'] = f"好調台が多い（全{total}台中{high_count}台がA以上）"
            elif high_ratio >= 30:
                store_analysis['overall'] = f"好調台あり（全{total}台中{high_count}台がA以上）"
            else:
                store_analysis['overall'] = f"好調台が少ない（全{total}台中{high_count}台がA以上）"

        # 蓄積DB補完（共通関数）
        from scripts.enrich_rec import enrich_recs as _enrich_recs
        _enrich_recs(recommendations)

        # 日付固定化：正しい日付のデータのみ使用
        now_date = datetime.now(JST)
        fixed_dates = [(now_date - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(1, 8)]
        fixed_yesterday = fixed_dates[0]
        fixed_day_before = fixed_dates[1]
        fixed_three_days = fixed_dates[2]

        # 古すぎるデータをクリア（7日以上前のデータは除外）
        cutoff_date = (datetime.now(JST) - timedelta(days=7)).strftime('%Y-%m-%d')

        for rec in recommendations:
            # 前日データのチェック（7日以上前は除外）
            y_date = rec.get('yesterday_date', '')
            if y_date and y_date < cutoff_date:
                rec['yesterday_art'] = 0
                rec['yesterday_rb'] = 0
                rec['yesterday_games'] = 0
                rec['yesterday_diff_medals'] = 0
                rec['yesterday_max_rensa'] = 0
                rec['yesterday_max_medals'] = 0
                rec['yesterday_history'] = []
                rec['yesterday_date'] = ''

            # 前々日データのチェック（7日以上前は除外）
            db_date = rec.get('day_before_date', '')
            if db_date and db_date < cutoff_date:
                rec['day_before_art'] = 0
                rec['day_before_rb'] = 0
                rec['day_before_games'] = 0
                rec['day_before_diff_medals'] = 0
                rec['day_before_max_rensa'] = 0
                rec['day_before_max_medals'] = 0
                rec['day_before_history'] = []
                rec['day_before_date'] = ''

            # 3日前データのチェック（7日以上前は除外）
            td_date = rec.get('three_days_ago_date', '')
            if td_date and td_date < cutoff_date:
                rec['three_days_ago_art'] = 0
                rec['three_days_ago_rb'] = 0
                rec['three_days_ago_games'] = 0
                rec['three_days_ago_diff_medals'] = 0
                rec['three_days_ago_max_rensa'] = 0
                rec['three_days_ago_max_medals'] = 0
                rec['three_days_ago_history'] = []
                rec['three_days_ago_date'] = ''

            # recent_daysも日付固定化（art=0でもgames>0があれば表示）
            if rec.get('recent_days'):
                new_recent_days = []
                for i, fixed_date in enumerate(fixed_dates[:7]):
                    matching_day = None
                    for day in rec.get('recent_days', []):
                        if day.get('date') == fixed_date:
                            matching_day = day
                            break
                    # games>0（稼働あり）の日を表示
                    # art=0でもgames>0なら「稼働したがART未当選」として正常データ
                    if matching_day and matching_day.get('games', 0) > 0:
                        new_recent_days.append(matching_day)
                rec['recent_days'] = new_recent_days

        # 各台の過去3日分の当たり履歴を答え合わせ形式に加工
        _rec_mk = _get_machine_key(store_key)
        for rec in recommendations:
            for hist_key in ('yesterday_history', 'day_before_history', 'three_days_ago_history'):
                raw_hist = rec.get(hist_key, [])
                if raw_hist:
                    processed, summary = _process_history_for_verify(raw_hist, machine_key=_rec_mk)
                    rec[f'{hist_key}_processed'] = processed
                    rec[f'{hist_key}_summary'] = summary
                else:
                    rec[f'{hist_key}_processed'] = []
                    rec[f'{hist_key}_summary'] = {}

        # 台番号アラート
        store_alerts = [a for a in get_active_alerts() if a.get('store_key') == store_key]

        # データ日付ラベル（蓄積DBの最新日付を取得）
        data_date_str = None
        try:
            from analysis.history_accumulator import load_unit_history
            _units = store.get('units', [])
            if _units:
                _hist = load_unit_history(store_key, str(_units[0]))
                if _hist and _hist.get('days'):
                    _latest = max(d.get('date', '') for d in _hist['days'])
                    if _latest:
                        _dt = datetime.strptime(_latest, '%Y-%m-%d')
                        data_date_str = f"{_dt.month}/{_dt.day}({WEEKDAY_NAMES[_dt.weekday()]})"
        except Exception:
            pass

        # API JSON生成（realtime.jsのフォールバック用）
        try:
            _api_rec_dir = OUTPUT_DIR / 'api' / 'v2' / 'recommend'
            _api_rec_dir.mkdir(parents=True, exist_ok=True)
            def _to_api_rec(r):
                return {
                    'unit_id': r.get('unit_id', ''),
                    'final_rank': r.get('final_rank', 'C'),
                    'availability': r.get('availability', ''),
                    'today_art': r.get('art_count', 0),
                    'today_games': r.get('total_games', 0),
                    'current_games': r.get('current_start', r.get('total_games', 0)),
                    'max_medals': r.get('max_medals', 0),
                    'reasons': r.get('reasons', []),
                    'is_running': r.get('is_running', False),
                }
            _rec_api = {
                'updated_at': datetime.now(JST).isoformat(),
                'cache_info': cache_info or {'fetched_at': datetime.now(JST).strftime('%H:%M')},
                'top_recs': [_to_api_rec(r) for r in top_recs],
                'other_recs': [_to_api_rec(r) for r in other_recs],
            }
            (_api_rec_dir / f'{store_key}.json').write_text(json.dumps(_rec_api, ensure_ascii=False), encoding='utf-8')
        except Exception as _e:
            print(f"  [warn] API JSON({store_key})生成失敗: {_e}")

        now = datetime.now(JST)
        html = template.render(
            store=store,
            store_key=store_key,
            machine=machine,
            machine_key=machine_key,
            top_recs=top_recs,
            other_recs=other_recs,
            updated_at=now.strftime('%H:%M'),
            now_short=now.strftime('%m%d_%H:%M'),
            cache_info=cache_info,
            availability_info=availability_info,
            is_open=is_open,
            display_mode=display_mode,
            store_analysis=store_analysis,
            unit_alerts=store_alerts,
            data_date_str=data_date_str,
        )

        output_path = output_subdir / f'{store_key}.html'
        output_path.write_text(html, encoding='utf-8')

    print(f"  -> {output_subdir}/")


def _get_machine_key(store_key):
    """store_keyから機種キーを取得"""
    if not store_key:
        return None
    if store_key.endswith('_sbj') or '_sbj_' in store_key:
        return 'sbj'
    if store_key.endswith('_yoshimune') or '_yoshimune_' in store_key:
        return 'yoshimune'
    if store_key.endswith('_toloveru') or '_toloveru_' in store_key:
        return 'toloveru'
    if 'sbj' in store_key:
        return 'sbj'
    if 'yoshimune' in store_key:
        return 'yoshimune'
    if 'toloveru' in store_key:
        return 'toloveru'
    return None


def _process_history_for_verify(history, machine_key=None):
    """当たり履歴を答え合わせ表示用に加工する

    - 時間順にソート
    - チェーン（連チャン）を計算
    - 深いハマり・浅い当たりのフラグを付与
    - 天井判定は機種別（config/rankings.pyのnormal_ceilingを参照）
    
    Args:
        history: 当たり履歴リスト
        machine_key: 機種キー（'sbj', 'hokuto2'等）。天井閾値の決定に使用
    """
    from analysis.analyzer import is_big_hit, RENCHAIN_THRESHOLD
    from config.rankings import MACHINES, MACHINE_DEFAULTS, get_machine_threshold

    if not history:
        return [], {}

    # 連チャン閾値: 機種別にconfig/rankings.pyから取得
    renchain_th = get_machine_threshold(machine_key, 'renchain_threshold') if machine_key else RENCHAIN_THRESHOLD
    if not renchain_th:
        renchain_th = RENCHAIN_THRESHOLD

    # チェーン計算は時間昇順で行う（連チャン判定のため）
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

        # 天井判定: 機種別にconfig/rankings.pyのnormal_ceilingを参照
        # SBJ: 999G+α（RBではリセットされない。表示G数と内部G数にズレあり）
        # 北斗: あべしシステム（G数ベースの天井判定は参考値。normal_ceiling=1100）
        from config.rankings import MACHINES, MACHINE_DEFAULTS
        machine_config = MACHINES.get(machine_key, MACHINE_DEFAULTS) if machine_key else MACHINE_DEFAULTS
        TENJOU_THRESHOLD = machine_config.get('normal_ceiling', 999)
        entry = {
            'index': i + 1,
            'time': time_str,
            'start': start,
            'type': hit_type,
            'medals': medals,
            'is_deep': accumulated_games >= 500 if not is_big_hit(hit_type) else start >= 500,
            'is_shallow': start <= 10 and i > 0,
            'is_tenjou': accumulated_games >= TENJOU_THRESHOLD if is_big_hit(hit_type) else False,
            'accumulated_games': accumulated_games,  # RBを跨いだ累計G数
        }

        if is_big_hit(hit_type):
            if i == 0 or accumulated_games > renchain_th:
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
    tenjou_count = sum(1 for v in valleys if v >= TENJOU_THRESHOLD)

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

    # 表示用に降順（最新が上）に並び替え + indexを振り直す
    processed.reverse()
    for i, entry in enumerate(processed):
        entry['index'] = i + 1

    return processed, summary


def _try_load_backtest_results():
    """有効な実績データがある最新のバックテスト結果を読み込む（当日は未確定なのでスキップ）"""
    import glob
    from datetime import datetime, timedelta
    today_str = datetime.now(JST).strftime('%Y%m%d')
    yesterday_str = (datetime.now(JST) - timedelta(days=1)).strftime('%Y%m%d')
    results_files = sorted(glob.glob(str(PROJECT_ROOT / 'data' / 'verify' / 'verify_*_results.json')), reverse=True)
    for f in results_files:
        fname = Path(f).name
        # 当日はスキップ
        if today_str in fname:
            continue
        # 昨日のデータのみ使用（古いデータは使わない）
        if yesterday_str not in fname:
            continue
        try:
            data = json.loads(Path(f).read_text())
            # S/A予測台のうちactual_prob>0がある店舗が全体の半数以上あれば有効
            # （一部店舗だけデータがある場合は蓄積DBからリアルタイム補完を使う）
            stores_with_valid = 0
            total_stores = 0
            for sk, units in data.get('units', {}).items():
                total_stores += 1
                has_prob = any(u.get('actual_prob', 0) > 0 for u in units if u.get('predicted_rank') in ('S', 'A'))
                if has_prob:
                    stores_with_valid += 1
            # 半数以上の店舗に実績データがある場合のみ使用
            if total_stores > 0 and stores_with_valid >= total_stores * 0.5:
                print(f"  バックテスト結果を使用: {fname} ({stores_with_valid}/{total_stores}店舗)")
                return data
            else:
                print(f"  バックテスト結果スキップ（データ不足）: {fname} ({stores_with_valid}/{total_stores}店舗)")
        except:
            pass
    return None


def _get_latest_valid_verify():
    """有効な実績データがあるverifyファイルを返す（nodataのみ・当日はスキップ）"""
    from datetime import datetime
    today_str = datetime.now(JST).strftime('%Y%m%d')
    files = sorted(glob.glob('data/verify/verify_*_results.json'), reverse=True)
    for f in files:
        # 当日のverifyは未確定なのでスキップ
        fname = Path(f).name
        if today_str in fname:
            continue
        try:
            data = json.load(open(f))
            # S/A予測台のうちactual_prob>0が1台でもあれば有効
            has_data = False
            for sk, units in data.get('units', {}).items():
                for u in units:
                    if u.get('predicted_rank') in ('S', 'A') and u.get('actual_prob', 0) > 0:
                        has_data = True
                        break
                if has_data:
                    break
            if has_data:
                return data
        except:
            pass
    return None


def _get_verify_date_str():
    """的中率の日付を取得（常に前日を表示）"""
    # データの有無に関係なく、常に「前日」を表示
    yesterday = datetime.now(JST) - timedelta(days=1)
    weekdays = ['月','火','水','木','金','土','日']
    return f'{yesterday.month}/{yesterday.day}({weekdays[yesterday.weekday()]})'


def _get_verify_accuracy_prob_based():
    """確率ベースの的中率を返す（S/A予測で確率1/130以下を的中とする）"""
    data = _get_latest_valid_verify()
    if not data:
        return 0, 0, 0
    try:
        total_sa = 0
        total_hit = 0
        for sk, units in data.get('units', {}).items():
            for u in units:
                prob = u.get('actual_prob', 0)
                games = u.get('actual_games', 0)
                if prob <= 0 or games < 500:
                    continue
                if u.get('predicted_rank') in ('S', 'A'):
                    total_sa += 1
                    if prob <= 130:  # 確率1/130以下なら的中
                        total_hit += 1
        if total_sa > 0:
            rate = int(total_hit / total_sa * 100)
            return rate, total_hit, total_sa
    except:
        pass
    return 0, 0, 0


def _get_verify_examples():
    """的中した台の具体例を返す（最大3件）"""
    data = _get_latest_valid_verify()
    if not data:
        return []
    try:
        hits = []
        for sk, units in data.get('units', {}).items():
            store_info = data.get('stores', {}).get(sk, {})
            store_name = store_info.get('name', sk)
            # 店名を短縮
            store_name = store_name.replace('エスパス日拓', '').replace('新宿歌舞伎町店', '歌舞伎町').replace('渋谷新館', '渋谷新館').replace('秋葉原駅前店', '秋葉原')
            
            for u in units:
                prob = u.get('actual_prob', 0)
                games = u.get('actual_games', 0)
                if prob <= 0 or games < 500:
                    continue
                if u.get('predicted_rank') in ('S', 'A') and prob <= 130:
                    hits.append({
                        'store': store_name,
                        'unit': u.get('unit_id', ''),
                        'rank': u.get('predicted_rank', ''),
                        'prob': int(prob),
                    })
        
        # 確率の良い順に3件
        hits.sort(key=lambda x: x['prob'])
        return hits[:3]
    except:
        return []


def _get_verify_accuracy():
    """verifyページと完全に同じ的中率を返す（verify結果JSONから直接読む）"""
    data = _get_latest_valid_verify()
    if not data:
        return 0
    try:
        # generate_verify.pyで計算済みのoverall_rateを使う
        rate = data.get('overall_rate', 0)
        if rate > 0:
            return int(rate)
        # フォールバック: verifyページ生成時と同じ計算
        # nodata除外 + games>=500 フィルタ
        total_sa = 0
        total_hit = 0
        for sk, units in data.get('units', {}).items():
            for u in units:
                prob = u.get('actual_prob', 0)
                games = u.get('actual_games', 0)
                if prob <= 0 or games < 500:
                    continue
                if u.get('predicted_rank') in ('S', 'A'):
                    total_sa += 1
                    if u.get('verdict_class') in ('perfect', 'hit'):
                        total_hit += 1
        if total_sa > 0:
            return int(total_hit / total_sa * 100)
    except:
        pass
    return 0


def _get_verify_by_category():
    """店舗×機種別の的中率を返す（トップページ表示用、上位2件）"""
    data = _get_latest_valid_verify()
    if not data:
        return []
    try:
        by_store = {}
        for sk, units in data.get('units', {}).items():
            mk = data['stores'][sk].get('machine_key', 'sbj')
            sn = data['stores'][sk].get('name', sk)
            mk_short = 'SBJ' if mk == 'sbj' else '北斗転生2'
            mk_icon = '🎰' if mk == 'sbj' else '⚔️'
            key = f"{sn}|{mk_short}"
            if key not in by_store:
                by_store[key] = {'sa': 0, 'hit': 0, 'icon': mk_icon, 'store': sn, 'mk': mk_short}
            for u in units:
                prob = u.get('actual_prob', 0)
                games = u.get('actual_games', 0)
                if prob <= 0 or games < 500:
                    continue
                if u.get('predicted_rank') in ('S', 'A'):
                    by_store[key]['sa'] += 1
                    if u.get('verdict_class') in ('perfect', 'hit'):
                        by_store[key]['hit'] += 1
        
        # S/A台が2台以上ある店から的中率の高い順に2つ（同率は台数多い方優先）
        candidates = [d for d in by_store.values() if d['sa'] >= 2]
        candidates.sort(key=lambda x: (-x['hit'] / x['sa'], -x['sa']))
        
        result = []
        for d in candidates[:2]:
            rate = int(d['hit'] / d['sa'] * 100)
            result.append({
                'mk_name': f"{d['icon']}{d['mk']}",
                'store_name': d['store'],
                'rate': rate,
                'hit': d['hit'],
                'total': d['sa'],
            })
        return result
    except:
        return []


def _get_verify_highlights():
    """バックテスト結果から特筆事項を取得（最大2件）"""
    highlights = []
    data = _get_latest_valid_verify()
    if not data:
        return highlights
    try:
        
        # 大的中（S/A予測 × 確率1/100以下 × 差枚+3000以上）を探す
        big_hits = []
        normal_hits = []
        for sk, units in data.get('units', {}).items():
            for u in units:
                rank = u.get('predicted_rank', 'C')
                prob = u.get('actual_prob', 999)
                diff = (u.get('diff_medals') or 0)
                if rank in ('S', 'A') and u.get('verdict_class') in ('perfect', 'hit'):
                    if prob <= 100 and diff >= 3000:
                        big_hits.append({
                            'unit_id': u.get('unit_id'),
                            'prob': prob,
                            'diff': diff,
                            'store': sk
                        })
                    elif diff >= 5000:
                        normal_hits.append({
                            'unit_id': u.get('unit_id'),
                            'diff': diff,
                            'store': sk
                        })
        
        # 大的中があれば最優先
        if big_hits:
            best = max(big_hits, key=lambda x: x['diff'])
            highlights.append(f"🎯 大的中！差枚+{best['diff']:,}枚")
        
        # 的中台数
        total_hit = sum(1 for sk, units in data.get('units', {}).items() 
                       for u in units 
                       if u.get('predicted_rank') in ('S', 'A') and u.get('verdict_class') in ('perfect', 'hit'))
        if total_hit > 0:
            highlights.append(f"📊 おすすめ台 {total_hit}台が的中")
        
        # 全店舗的中などがあれば追加
        if not highlights and normal_hits:
            best = max(normal_hits, key=lambda x: x['diff'])
            highlights.append(f"✨ 差枚+{best['diff']:,}枚の台を予測")
            
    except Exception as e:
        pass
    
    return highlights[:2]  # 最大2件


def _is_unit_hit(u):
    """予想と結果が一致=的中（verdict.pyベース）"""
    # verdict_class が既に計算済みの場合はそれを使う
    vc = u.get('verdict_class')
    if vc:
        return vc in ('perfect', 'hit')
    # フォールバック: result_levelから判定
    rank = u.get('pre_open_rank', u.get('predicted_rank', 'C'))
    rl = u.get('result_level', 'nodata')
    return v_is_hit(rank, rl)


def _generate_verify_from_backtest(env, results):
    """バックテスト結果からverifyページを生成"""
    from analysis.feedback import analyze_prediction_errors
    
    STORE_TO_MACHINE = {}
    for sk, sv in STORES.items():
        STORE_TO_MACHINE[sk] = sv.get('machine', sv.get('machine_key', 'sbj'))
    
    # availability.jsonからdiff_medals/max_medals/games/artを取得
    # availability.jsonは当日最終データ（historyからdiff推定可能）
    avail_lookup = {}
    avail_date = None
    try:
        from analysis.diff_medals_estimator import estimate_diff_medals
        avail_data = json.load(open(str(PROJECT_ROOT / 'data' / 'availability.json')))
        # availability.jsonの日付を取得
        fetched_at = avail_data.get('fetched_at', '')
        if fetched_at:
            avail_date = fetched_at[:10]  # YYYY-MM-DD部分
        for sk, sdata in avail_data.get('stores', {}).items():
            mk = STORE_TO_MACHINE.get(sk, 'sbj')
            for u in sdata.get('units', []):
                uid = str(u.get('unit_id', ''))
                hist = u.get('today_history', [])
                medals_total = sum(h.get('medals', 0) for h in hist)
                games = u.get('total_start', 0) or u.get('games', 0)
                art = u.get('art', 0)
                max_medals = u.get('max_medals', 0)
                diff = estimate_diff_medals(medals_total, games, mk) if games > 0 else 0
                info = {'max_medals': max_medals, 'diff_medals': diff, 'games': games, 'art': art, 'history': hist}
                avail_lookup[(sk, uid)] = info
                # エイリアス: akihabara↔akiba の差異対応
                if 'akihabara' in sk:
                    avail_lookup[(sk.replace('akihabara', 'akiba'), uid)] = info
                elif 'akiba' in sk:
                    avail_lookup[(sk.replace('akiba', 'akihabara'), uid)] = info
    except Exception as e:
        print(f"  ⚠ availability.json読み込みエラー: {e}")
    
    machine_groups = {}
    for store_key, store_data in results.get('stores', {}).items():
        mk = STORE_TO_MACHINE.get(store_key, 'sbj')
        if mk not in machine_groups:
            machine_groups[mk] = {'stores': []}
        
        units = results.get('units', {}).get(store_key, [])
        formatted_units = []
        for u in sorted(units, key=lambda x: -x.get('predicted_score', 0)):
            rank = u.get('predicted_rank', 'C')
            prob = u.get('actual_prob', 0)
            games = u.get('actual_games', 0)
            actual_art = u.get('actual_art', 0)
            
            uid = str(u.get('unit_id', ''))
            avail_info = avail_lookup.get((store_key, uid), {})
            # バックテスト結果のデータを優先（availability.jsonは当日データなので混在させない）
            max_medals = u.get('max_medals', 0)
            diff_medals = (u.get('diff_medals') or 0)
            # 蓄積DBから実績データ・差枚・最大枚数を補完
            try:
                from analysis.history_accumulator import load_unit_history
                from analysis.diff_medals_estimator import estimate_diff_medals as _est_diff
                _hd = load_unit_history(store_key, uid)
                if _hd:
                    pred_date = results.get('prediction_date', '')
                    if pred_date:
                        from datetime import datetime as _dt2, timedelta as _td2
                        _adate = (_dt2.strptime(pred_date, '%Y-%m-%d') + _td2(days=1)).strftime('%Y-%m-%d')
                        for _dd in _hd.get('days', []):
                            if _dd.get('date') == _adate:
                                # 実績データを蓄積DBから補完（バックテスト結果にない場合）
                                if games == 0:
                                    games = _dd.get('games', _dd.get('total_start', 0)) or 0
                                if actual_art == 0:
                                    actual_art = _dd.get('art', 0) or 0
                                if prob == 0 and games > 0 and actual_art > 0:
                                    prob = games / actual_art
                                if max_medals == 0:
                                    max_medals = _dd.get('max_medals', 0)
                                # diff_medals: 履歴から再計算（蓄積DBの値は信頼性が低い）
                                _hist = _dd.get('history', [])
                                _games = _dd.get('games', _dd.get('total_start', 0)) or games
                                if _hist and _games > 0:
                                    _medals_total = sum(h.get('medals', 0) for h in _hist)
                                    diff_medals = _est_diff(_medals_total, _games, mk)
                                break
            except:
                pass
            
            # 蓄積DBで取得できなかった場合、availability.jsonからフォールバック
            # （availability.jsonの日付がverify対象日と一致する場合のみ）
            pred_date = results.get('prediction_date', '')
            if pred_date:
                from datetime import datetime as _dt3, timedelta as _td3
                _target_date = (_dt3.strptime(pred_date, '%Y-%m-%d') + _td3(days=1)).strftime('%Y-%m-%d')
                if avail_date == _target_date and games == 0:
                    ai = avail_lookup.get((store_key, uid), {})
                    if ai.get('games', 0) > 0:
                        games = ai['games']
                        actual_art = ai.get('art', 0) or actual_art
                        if actual_art > 0 and games > 0:
                            prob = games / actual_art
                        diff_medals = ai.get('diff_medals', 0) or diff_medals
                        max_medals = ai.get('max_medals', 0) or max_medals
            
            # verdict.py共通ロジックで判定
            if games < 500 or prob <= 0:
                result_level = 'nodata'
                result_mark, result_mark_class = '-', 'nodata'
                verdict_text, verdict_class = '—', 'nodata'
            else:
                result_level = get_result_level(prob, diff_medals, mk, max_medals=max_medals)
                result_mark, result_mark_class = RESULT_MARKS.get(result_level, ('-', 'nodata'))
                verdict_text, verdict_class = get_verdict(rank, result_level)
            
            # 蓄積DBから当たり履歴を取得
            raw_hist = []
            try:
                from analysis.history_accumulator import load_unit_history
                hist_data = load_unit_history(store_key, uid)
                if hist_data and hist_data.get('days'):
                    # actual_date = prediction_date + 1日
                    pred_date = results.get('prediction_date', '')
                    if pred_date:
                        from datetime import datetime as _dt, timedelta as _td
                        actual_date = (_dt.strptime(pred_date, '%Y-%m-%d') + _td(days=1)).strftime('%Y-%m-%d')
                    else:
                        actual_date = results.get('actual_date', '')
                    for d in hist_data['days']:
                        if d.get('date') == actual_date:
                            raw_hist = d.get('history', [])
                            break
            except:
                pass
            processed_history, history_summary = _process_history_for_verify(raw_hist, machine_key=_get_machine_key(store_key))
            
            formatted_units.append({
                'unit_id': u.get('unit_id', ''),
                'pre_open_rank': rank,
                'pre_open_score': u.get('predicted_score', 50),
                'predicted_rank': rank,
                'predicted_score': u.get('predicted_score', 50),
                'actual_art': actual_art,
                'actual_prob': prob,
                'actual_games': games,
                'max_medals': max_medals,
                'diff_medals': diff_medals,
                'result_level': result_level,
                'result_mark': result_mark,
                'result_mark_class': result_mark_class,
                'verdict_text': verdict_text,
                'verdict_class': verdict_class,
                'history': processed_history,
                'history_summary': history_summary,
            })
        
        # S/A予測台ベースの的中率
        valid_units = [u for u in formatted_units if u['verdict_class'] != 'nodata']
        sa_valid = [u for u in valid_units if u['predicted_rank'] in ('S', 'A')]
        sa_hit = sum(1 for u in sa_valid if _is_unit_hit(u))
        
        machine_groups[mk]['stores'].append({
            'name': store_data.get('name', store_key),
            'units': formatted_units,
            'sa_total': len(sa_valid),
            'sa_hit': sa_hit,
            'sa_rate': (sa_hit / len(sa_valid) * 100) if sa_valid else 0,
        })
    
    verify_data = {}
    for mk, mg in machine_groups.items():
        m = MACHINES.get(mk, {})
        verify_data[mk] = {
            'name': m.get('short_name', mk),
            'icon': m.get('icon', '🎰'),
            'stores': mg['stores'],
        }
    
    # nodata台を除外した正確な的中率を再計算
    total_sa = sum(s['sa_total'] for mg in machine_groups.values() for s in mg['stores'])
    total_hit = sum(s['sa_hit'] for mg in machine_groups.values() for s in mg['stores'])
    total_surprise = sum(
        sum(1 for u in s['units'] if u['verdict_class'] == 'surprise')
        for mg in machine_groups.values() for s in mg['stores']
    )
    accuracy = (total_hit / total_sa * 100) if total_sa > 0 else 0
    
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
    
    # 店×機種別の的中率ヘッダー（最上部テキスト表示用）
    store_accuracy_header = []
    for mk, md in verify_data.items():
        for si, sd in enumerate(md['stores']):
            _units = sd.get('units', [])
            _valid = [u for u in _units if u.get('actual_prob', 0) > 0 and u.get('actual_games', 0) >= 500]
            _sa = [u for u in _valid if u.get('predicted_rank') in ('S', 'A')]
            if len(_sa) >= 2:
                _hit = sum(1 for u in _sa if u.get('verdict_class') in ('perfect', 'hit'))
                _rate = int(_hit / len(_sa) * 100)
                store_accuracy_header.append({
                    'rate': _rate,
                    'machine_name': md['name'],
                    'store_name': sd.get('store_name', sd.get('name', '')),
                    'hit': _hit,
                    'total': len(_sa),
                })
    store_accuracy_header.sort(key=lambda x: (-x['rate'], -x['total']))
    # 良い結果のみ表示（80%以上）
    store_accuracy_header = [s for s in store_accuracy_header if s['rate'] >= 80]

    perfect_stores = []
    for mk, md in verify_data.items():
        for si, sd in enumerate(md['stores']):
            units = sd.get('units', [])
            valid_units = [u for u in units if u.get('actual_prob', 0) > 0 and u.get('actual_games', 0) >= 500]
            if not valid_units:
                continue
            hit_count = sum(1 for u in valid_units if _is_unit_hit(u))
            total = len(valid_units)
            rate = hit_count / total * 100 if total > 0 else 0
            store_id = f"store-{mk}-{si}"
            if rate >= 80 and total >= 3:
                perfect_stores.append({
                    'store_name': sd.get('store_name', sd.get('name', '')),
                    'machine_name': md['name'],
                    'machine_icon': md['icon'],
                    'hit_count': hit_count,
                    'total_units': total,
                    'rate': rate,
                    'store_id': store_id,
                })
    # 的中率降順→台数順、最大5件
    perfect_stores.sort(key=lambda x: (-x['rate'], -x['total_units']))
    perfect_stores = perfect_stores[:5]

    # トピック自動生成
    topics = []
    for mk, md in verify_data.items():
        machine_name = md['name']
        for sd in md['stores']:
            store_name = sd.get('store_name', sd.get('name', ''))
            units = sd.get('units', [])
            valid = [u for u in units if u.get('actual_prob', 0) > 0 and u.get('actual_games', 0) >= 500]
            if not valid:
                continue
            
            def _unit_stats(u):
                parts = []
                art = u.get('actual_art', 0)
                if art > 0:
                    parts.append(f'<span class="td-art">ART {art}回</span>')
                prob = u.get('actual_prob', 0)
                if prob > 0:
                    parts.append(f'<span class="td-prob">1/{prob:.0f}</span>')
                diff = (u.get('diff_medals') or 0)
                if diff:
                    cls = 'plus' if diff > 0 else 'minus'
                    parts.append(f'<span class="td-diff {cls}">差枚{diff:+,}</span>')
                mx = u.get('max_medals', 0)
                if mx > 0:
                    parts.append(f'<span class="td-max">最大{mx:,}枚</span>')
                return ' / '.join(parts)
            
            sa_units = [u for u in valid if u.get('pre_open_rank', u.get('predicted_rank', 'C')) in ('S', 'A')]
            
            # 1. 大的中（S/A予測 × 確率1/100以下 × 差枚+3,000以上）
            for u in sa_units:
                diff = u.get('diff_medals') or 0
                mx = u.get('max_medals') or 0
                if u.get('actual_prob', 999) <= 100 and diff >= 3000:
                    topics.append({
                        'icon': '💥',
                        'type': 'explosion',
                        'machine': machine_name,
                        'title': f'大的中！{store_name} {u["unit_id"]}番',
                        'detail': _unit_stats(u),
                        'sort_diff': diff,
                        'sort_max': mx,
                    })
            
            # 2. 的中（S/A予測 × 差枚+5,000以上）— 大的中と重複しない台
            explosion_ids = {u.get('unit_id') for u in sa_units if u.get('actual_prob', 999) <= 100 and (u.get('diff_medals') or 0) >= 3000}
            for u in sa_units:
                diff = (u.get('diff_medals') or 0)
                mx = u.get('max_medals', 0)
                uid = u.get('unit_id')
                if diff >= 5000 and uid not in explosion_ids:
                    topics.append({
                        'icon': '🎯',
                        'type': 'hit',
                        'machine': machine_name,
                        'title': f'的中！{store_name} {u["unit_id"]}番',
                        'detail': _unit_stats(u),
                        'sort_diff': diff,
                        'sort_max': mx,
                    })
    
    # 差枚順 → 最大枚数順でソート、最大10件
    topics.sort(key=lambda x: (-x.get('sort_diff', 0), -x.get('sort_max', 0)))
    topics = topics[:10]
    
    # 日付情報（読みやすいフォーマット）
    weekdays = ['月','火','水','木','金','土','日']
    def _fmt_date(date_str):
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            return f'{dt.month}/{dt.day}({weekdays[dt.weekday()]})'
        except:
            return date_str
    
    actual_date = results.get('date', '')        # 実績日（1/28）
    pred_base_date = results.get('prediction_date', '')  # 予測に使ったデータの最新日（1/27）
    generated_at = results.get('generated_at', '')
    
    # 予測の説明: 「1/27までのデータで予測」
    predict_time_info = f'{_fmt_date(pred_base_date)}までのデータで予測'
    
    # 全台数計算
    _total_all_units = sum(len(s['units']) for mg in machine_groups.values() for s in mg['stores'])
    _total_good_all = total_hit + total_surprise

    template = env.get_template('verify.html')
    now = datetime.now(JST)
    html = template.render(
        verify_data=verify_data,
        accuracy=accuracy,
        predicted_good=total_sa,
        actual_good=total_hit,
        surprise_good=total_surprise,
        total_all_units=_total_all_units,
        total_good_all=_total_good_all,
        machine_accuracy=machine_accuracy,
        topics=topics,
        perfect_stores=perfect_stores,
        store_accuracy_header=store_accuracy_header,
        version=f'backtest_{actual_date}',
        result_date_str=f'{_fmt_date(actual_date)}の実績',
        predict_base=predict_time_info,
        now_short=now.strftime('%m%d_%H:%M'),
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
            
            # verify用: 蓄積DBからdiff_medals等を補完
            from scripts.enrich_rec import enrich_recs as _verify_enrich
            _verify_enrich(recommendations)
            
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
                        actual_prob = int(actual_games / actual_art)
                    else:
                        actual_prob = 0

                # 開店前予測（過去データのみ）
                uid = str(rec.get('unit_id', ''))
                pre_open = pre_open_map.get(uid, {})
                pre_open_rank = pre_open.get('rank', 'C')
                pre_open_score = pre_open.get('score', 50)

                # 結果判定（verdict.py共通ロジック）
                is_predicted_good = predicted_rank in ('S', 'A')
                # diff_medals: 履歴から再計算（蓄積DBの値は信頼性が低い場合がある）
                diff_medals = 0
                max_medals_val = 0
                
                # 蓄積DBから履歴を読み、medals_totalから差枚を計算
                from datetime import datetime, timedelta
                from analysis.history_accumulator import load_unit_history
                from analysis.diff_medals_estimator import estimate_diff_medals
                _verify_date = (datetime.now(JST) - timedelta(days=1)).strftime('%Y-%m-%d')
                _uhist = load_unit_history(store_key, uid)
                if _uhist:
                    for _dd in _uhist.get('days', []):
                        if _dd.get('date') == _verify_date:
                            _hist = _dd.get('history', [])
                            _games = _dd.get('games', _dd.get('total_start', 0)) or actual_games
                            if _hist and _games > 0:
                                # 履歴のmedals合計から差枚を再計算
                                medals_total = sum(h.get('medals', 0) for h in _hist)
                                diff_medals = estimate_diff_medals(medals_total, _games, machine_key)
                            # max_medalsは蓄積DBの値を使う
                            max_medals_val = _dd.get('max_medals', 0) or 0
                            break
                
                # 蓄積DBで取得できなければrecから
                if diff_medals == 0:
                    diff_medals = rec.get('yesterday_diff_medals', 0) or rec.get('diff_medals', 0)
                if max_medals_val == 0:
                    max_medals_val = rec.get('yesterday_max_medals', 0) or rec.get('max_medals', 0)
                result_level = get_result_level(actual_prob, diff_medals, machine_key, max_medals=max_medals_val)
                result_mark, result_mark_class = RESULT_MARKS.get(result_level, ('-', 'nodata'))
                verdict_text, verdict_class = get_verdict(pre_open_rank, result_level)
                
                if actual_games < 500 and actual_art == 0:
                    result_level = 'nodata'
                    result_mark, result_mark_class = '-', 'nodata'
                    verdict_text, verdict_class = '—', 'nodata'
                
                if is_predicted_good:
                    total_predicted_good += 1
                    if v_is_hit(pre_open_rank, result_level):
                        total_actual_good += 1
                elif verdict_class == 'surprise':
                    total_surprise += 1

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

                processed_history, history_summary = _process_history_for_verify(today_history_raw, machine_key=machine_key)

                units_data.append({
                    'unit_id': rec.get('unit_id', ''),
                    'predicted_rank': predicted_rank,
                    'predicted_score': predicted_score,
                    'pre_open_rank': pre_open_rank,
                    'pre_open_score': pre_open_score,
                    'actual_art': actual_art,
                    'actual_prob': actual_prob,
                    'actual_games': actual_games,
                    'diff_medals': diff_medals,
                    'max_medals': rec.get('max_medals', 0),
                    'result_level': result_level,
                    'result_mark': result_mark,
                    'result_mark_class': result_mark_class,
                    'verdict_text': verdict_text,
                    'verdict_class': verdict_class,
                    'history': processed_history,
                    'history_summary': history_summary,
                    'history_date': history_date,
                })

            if units_data:
                # 店舗別的中率（開店前予測ベース）
                store_sa_total = sum(1 for u in units_data if u['pre_open_rank'] in ('S', 'A'))
                store_sa_hit = sum(1 for u in units_data if u['pre_open_rank'] in ('S', 'A') and v_is_hit(u['pre_open_rank'], u.get('result_level', 'nodata')))
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
                if is_sa:
                    m_predicted += 1
                    if _is_unit_hit(unit):
                        m_actual += 1
                elif unit.get('verdict_class') == 'surprise':
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
    generated_time = now.strftime('%Y/%m/%d %H:%M')
    # 実績データの日付（閉店後は前日、営業中は当日）
    if is_business_hours():
        result_date_str = format_date_with_weekday(now) + 'の実績'
        predict_base = f'{format_date_with_weekday(now - timedelta(days=1))}までのデータで予測（{generated_time}生成）'
    else:
        result_date_str = format_date_with_weekday(now - timedelta(days=1)) + 'の実績'
        predict_base = f'{format_date_with_weekday(now - timedelta(days=2))}までのデータで予測（{generated_time}生成）'

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

    # 的中ハイライト（全台ベース）
    perfect_stores = []
    for mk, md in verify_data.items():
        for si, sd in enumerate(md['stores']):
            units = sd.get('units', [])
            valid_units = [u for u in units if u.get('actual_prob', 0) > 0 and u.get('actual_games', 0) >= 500]
            if not valid_units:
                continue
            hit_count = sum(1 for u in valid_units if _is_unit_hit(u))
            total = len(valid_units)
            rate = hit_count / total * 100 if total > 0 else 0
            store_id = f"store-{mk}-{si}"
            if rate >= 80 and total >= 3:
                perfect_stores.append({
                    'store_name': sd.get('store_name', sd.get('name', '')),
                    'machine_name': md['name'],
                    'machine_icon': md['icon'],
                    'hit_count': hit_count,
                    'total_units': total,
                    'rate': rate,
                    'store_id': store_id,
                })
    perfect_stores.sort(key=lambda x: (-x['rate'], -x['total_units']))
    perfect_stores = perfect_stores[:5]

    # 店×機種別の的中率ヘッダー
    store_accuracy_header = []
    for mk, md in verify_data.items():
        for si, sd in enumerate(md['stores']):
            units = sd.get('units', [])
            _valid = [u for u in units if u.get('actual_prob', 0) > 0 and u.get('actual_games', 0) >= 500]
            _sa = [u for u in _valid if u.get('pre_open_rank', u.get('predicted_rank', '')) in ('S', 'A')]
            if len(_sa) >= 2:
                _hit = sum(1 for u in _sa if _is_unit_hit(u))
                _rate = int(_hit / len(_sa) * 100)
                store_accuracy_header.append({
                    'rate': _rate,
                    'machine_name': md['name'],
                    'store_name': sd.get('store_name', sd.get('name', '')),
                    'hit': _hit,
                    'total': len(_sa),
                })
    store_accuracy_header.sort(key=lambda x: (-x['rate'], -x['total']))

    now = datetime.now(JST)
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
        topics=[],
        perfect_stores=perfect_stores,
        store_accuracy_header=store_accuracy_header,
        now_short=now.strftime('%m%d_%H:%M'),
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
                now = datetime.now(JST)
                html = template.render(
                    store=store,
                    store_key=store_key,
                    unit_id=unit_id_str,
                    machine=machine,
                    machine_key=machine_key,
                    days=[],
                    total_summary=None,
                    now_short=now.strftime('%m%d_%H:%M'),
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
                max_rensa = d.get('max_rensa', 0) or 0
                history = d.get('history', [])
                # historyがあれば機種別閾値で再計算（蓄積DBのmax_rensaは旧閾値の可能性）
                if history:
                    from analysis.analyzer import calculate_max_rensa, calculate_max_chain_medals
                    max_rensa = calculate_max_rensa(history, machine_key=machine_key)
                    max_medals = calculate_max_chain_medals(history, machine_key=machine_key)
                else:
                    max_medals = d.get('max_medals', 0) or 0
                _day_rl = get_result_level(prob, d.get('diff_medals', 0), machine_key, max_medals=max_medals)
                is_good = _day_rl in ('excellent', 'good')

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

                # 当たり履歴を時刻降順にソート（最新が上）
                history_sorted = []
                if history:
                    history_sorted = sorted(history, key=lambda x: x.get('time', '00:00'), reverse=True)

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
            avg_prob = int(total_games / total_art) if total_art > 0 else 0
            good_rate = round(good_count / total_days * 100) if total_days > 0 else 0

            total_summary = {
                'total_days': total_days,
                'good_days': good_count,
                'good_rate': good_rate,
                'avg_prob': round(avg_prob, 1) if avg_prob > 0 else 0,
                'total_diff_medals': total_diff,
            }

            now = datetime.now(JST)
            html = template.render(
                store=store,
                store_key=store_key,
                unit_id=unit_id_str,
                machine=machine,
                machine_key=machine_key,
                days=template_days,
                total_summary=total_summary,
                now_short=now.strftime('%m%d_%H:%M'),
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

    # availability.jsonからfetched_atを読み込む
    avail_path = PROJECT_ROOT / 'data' / 'availability.json'
    fetched_at = None
    if avail_path.exists():
        try:
            with open(avail_path, 'r', encoding='utf-8') as f:
                avail_data = json.load(f)
                fetched_at = avail_data.get('fetched_at')
        except Exception as e:
            print(f"  ⚠️ Failed to read fetched_at from availability.json: {e}")

    metadata = {
        'generated_at': datetime.now(JST).isoformat(),
        'version': '2026-01-27-static',
        'fetched_at': fetched_at,
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
        from analysis.history_accumulator import accumulate_from_daily, accumulate_from_availability
        # 1. daily JSONからの蓄積（従来）
        for mk in MACHINES:
            daily = load_daily_data(machine_key=mk)
            if daily:
                result = accumulate_from_daily(daily, mk)
                if result['new_entries'] > 0:
                    print(f"  📦 {mk}: {result['new_entries']}件蓄積 ({result['updated_units']}台)")
        
        # 2. availability.jsonからの蓄積（today_history → 蓄積DB）
        try:
            avail_path = Path('data/availability.json')
            if avail_path.exists():
                import json
                with open(avail_path, 'r', encoding='utf-8') as f:
                    avail_data = json.load(f)
                result = accumulate_from_availability(avail_data)
                if result['new_entries'] > 0:
                    print(f"  📦 availability: {result['new_entries']}件蓄積 ({result['updated_units']}台)")
        except Exception as e:
            print(f"  ⚠ availability蓄積エラー: {e}")
    except Exception as e:
        print(f"  ⚠ 蓄積エラー: {e}")

    # パターンデータ記録（蓄積済みhistoryからパターン分析用データを生成）
    try:
        from analysis.pattern_detector import record_from_history
        import os
        for store_dir in os.listdir('data/history'):
            if not os.path.isdir(f'data/history/{store_dir}'):
                continue
            mk = _get_machine_key(store_dir)
            if not mk:
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

    # ビルド後検証（既存）
    print("\n--- ビルド後検証 ---")
    
    # 3日分データ検証（TOP10の台に3日分データが揃っているか）
    from scripts.enrich_rec import enrich_recs
    from analysis.recommender import recommend_units
    
    data_check_errors = 0
    print("\n📊 TOP10 3日分データ検証:")
    for sk in ['shinjuku_espass_sbj', 'shinjuku_espass_yoshimune', 'shinjuku_espass_toloveru',
               'shibuya_espass_yoshimune', 'shibuya_espass_toloveru']:
        try:
            recs = recommend_units(sk)[:10]
            machine_key = _get_machine_key(sk) or 'tokyoghoul'
            for r in recs:
                r['store_key'] = sk
                r['machine_key'] = machine_key
            enrich_recs(recs)
            
            for r in recs[:5]:  # TOP5だけ検証
                uid = r.get('unit_id')
                y_date = r.get('yesterday_date', '')
                db_date = r.get('day_before_date', '')
                td_date = r.get('three_days_ago_date', '')
                y_hist = len(r.get('yesterday_history', []))
                db_hist = len(r.get('day_before_history', []))
                td_hist = len(r.get('three_days_ago_history', []))
                
                issues = []
                if not y_date:
                    issues.append("前日なし")
                if not db_date:
                    issues.append("前々日なし")
                if not td_date:
                    issues.append("3日前なし")
                if y_hist == 0 and r.get('yesterday_art', 0) == 0:
                    issues.append("前日データ空")
                
                if issues:
                    data_check_errors += 1
                    print(f"  ⚠️ {sk}/{uid}: {', '.join(issues)}")
        except Exception as e:
            print(f"  ERROR {sk}: {e}")
    
    if data_check_errors == 0:
        print("  ✅ 全店舗TOP5で3日分データ確認OK")
    else:
        print(f"  ❌ {data_check_errors}件のデータ欠損")
    
    from scripts.validate_output import validate_all
    if not validate_all():
        print("⚠️ validate_output: ERRORが検出されました")
    
    # post_build_check（HTML出力の整合性チェック）
    from scripts.post_build_check import run_all as _post_build_check
    post_errors = _post_build_check()
    if post_errors > 0:
        print(f"\n⚠️ POST-BUILD CHECK: {post_errors}件のERROR（警告のみ、ビルドは続行）")
    print("--- 検証完了 ---")


if __name__ == '__main__':
    main()
