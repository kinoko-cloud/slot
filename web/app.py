#!/usr/bin/env python3
"""
SBJ 台選びアシスタント - Webアプリ

iPhoneから店舗でアクセスして、推奨台を確認するためのWebアプリ
"""

import json
import sys
import time
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 日本時間
JST = timezone(timedelta(hours=9))

from flask import Flask, render_template, jsonify, request, redirect

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.rankings import STORES, RANKINGS, MACHINES, get_stores_by_machine, get_machine_info
from analysis.recommender import recommend_units, load_daily_data, generate_store_analysis
from scrapers.availability_checker import get_availability, get_realtime_data
from scripts.verify_units import get_active_alerts

app = Flask(__name__)

# キャッシュ無効化 + CORS対応
@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    # CORS: Cloudflare Pagesからのアクセスを許可
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

# デプロイ用シークレット
DEPLOY_SECRET = 'slot_deploy_2026'

# リアルタイムデータキャッシュ
REALTIME_CACHE = {}
SCRAPING_STATUS = {}
CACHE_DURATION = 300  # 5分キャッシュ（秒）

# バージョン確認用
APP_VERSION = '2026-01-31-v14-realtime-integration'

# Cloudflare Pagesへのリダイレクト設定
CLOUDFLARE_URL = 'https://slot-e8a.pages.dev'
REDIRECT_TO_CLOUDFLARE = True  # TrueでHTMLページをリダイレクト、APIは維持

# 営業時間設定
OPEN_HOUR = 10    # 開店時刻
CLOSE_HOUR = 23   # 閉店時刻
CLOSE_MINUTE = 50 # 集計開始時刻（22:50から集計中モード）

# 曜日名（日本語）
WEEKDAY_NAMES = ['月', '火', '水', '木', '金', '土', '日']


def get_display_mode():
    """現在時刻から表示モードを決定

    Returns:
        'realtime': 営業中モード（10:00〜22:49）
        'collecting': 集計中モード（22:50〜22:59）
        'result': 閉店後モード（23:00〜翌9:59）
    """
    now = datetime.now(JST)
    hour = now.hour
    minute = now.minute

    if hour >= CLOSE_HOUR or hour < OPEN_HOUR:
        # 23:00〜翌9:59: 閉店後モード
        return 'result'
    elif hour == (CLOSE_HOUR - 1) and minute >= CLOSE_MINUTE:
        # 22:50〜22:59: 集計中モード
        return 'collecting'
    else:
        # 10:00〜22:49: 営業中モード
        return 'realtime'


def get_result_date():
    """結果モード時に表示する日付を取得"""
    now = datetime.now(JST)
    if now.hour >= CLOSE_HOUR:
        # 23時以降は当日の結果
        return now
    else:
        # 0時〜10時は前日の結果
        return now - timedelta(days=1)


def format_date_with_weekday(dt):
    """日付を曜日付きでフォーマット（例: 1月27日(月)）"""
    weekday = WEEKDAY_NAMES[dt.weekday()]
    return f"{dt.month}月{dt.day}日({weekday})"


def is_business_hours():
    """現在営業時間内かどうか"""
    mode = get_display_mode()
    return mode == 'realtime'

@app.route('/version')
def version():
    return APP_VERSION

# 検索エンジンブロック用
@app.route('/robots.txt')
def robots():
    return """User-agent: *
Disallow: /
""", 200, {'Content-Type': 'text/plain'}


# デプロイ用エンドポイント
@app.route('/deploy', methods=['POST'])
def deploy():
    """git pull を実行してアプリを更新"""
    import subprocess
    import os

    secret = request.form.get('secret') or request.args.get('secret')
    if secret != DEPLOY_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        # git fetch + reset で強制同期（ローカル変更は破棄）
        subprocess.run(
            ['git', 'fetch', 'origin', 'main'],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30
        )
        result = subprocess.run(
            ['git', 'reset', '--hard', 'origin/main'],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30
        )

        # PythonAnywhere: WSGIファイルをtouchしてリロード
        wsgi_paths = [
            '/var/www/autogmail_pythonanywhere_com_wsgi.py',
            '/home/autogmail/autogmail.pythonanywhere.com/wsgi.py',
        ]
        touched = False
        for wsgi_path in wsgi_paths:
            if os.path.exists(wsgi_path):
                os.utime(wsgi_path, None)
                touched = True
                break

        return jsonify({
            'status': 'success',
            'output': result.stdout,
            'error': result.stderr,
            'returncode': result.returncode,
            'wsgi_touched': touched,
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/')
def index():
    """メインページ - 機種選択 + トップ5 + 店舗おすすめ曜日 + 前日トップ10"""
    # Cloudflare Pagesへリダイレクト
    if REDIRECT_TO_CLOUDFLARE:
        return redirect(CLOUDFLARE_URL)

    machines = []
    top3_all = []
    yesterday_top10 = []

    # 表示モードを判定
    display_mode = get_display_mode()

    # 店舗別曜日傾向（物理店舗ベース）
    # 各曜日の評価: 5=最強, 4=強い, 3=普通, 2=やや弱い, 1=避けるべき
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

    # 旧形式との互換性のためstore_recommendationsも作成
    store_recommendations = {}
    for key, info in store_day_ratings.items():
        best_days = [day for day, rating in info['day_ratings'].items() if rating >= 4]
        store_recommendations[key] = {
            'name': info['name'],
            'short_name': info['short_name'],
            'best_days': best_days,
            'note': info['best_note'],
            'rating': info['overall_rating'],
        }

    # 今日の日付と曜日
    now = datetime.now(JST)
    weekday_names = ['月', '火', '水', '木', '金', '土', '日']
    today_weekday = weekday_names[now.weekday()]
    today_date = now.strftime('%Y/%m/%d')

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

        # 各機種のトップ台を集める
        for store_key, store in stores.items():
            try:
                # 空き状況も取得
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

                recs = recommend_units(store_key, realtime_data=realtime, availability=availability)
                for rec in recs[:3]:  # 各店舗上位3台
                    rec['store_name'] = store.get('short_name', store['name'])
                    rec['store_key'] = store_key
                    rec['machine_key'] = key
                    rec['machine_icon'] = machine['icon']
                    rec['machine_name'] = machine.get('display_name', machine['short_name'])
                    # 空き状況
                    rec['availability'] = availability.get(rec['unit_id'], '')

                    # S/A評価台を狙い目に追加（空き・稼働中両方）
                    # ただし「様子見」推奨は除外
                    reasons_text = ' '.join(rec.get('reasons', []))
                    if rec['final_rank'] in ('S', 'A') and '様子見' not in reasons_text:
                        top3_all.append(rec)

                    # 前日トップ10用のデータを収集（最大枚数が多い台）
                    max_medals = rec.get('max_medals', 0)
                    y_art = rec.get('yesterday_art', 0)
                    y_games = rec.get('yesterday_games', 0)
                    if max_medals > 2000 or rec.get('yesterday_diff', 0) > 500:  # 差枚ベースなので閾値調整
                        yesterday_top10.append({
                            'unit_id': rec['unit_id'],
                            'store_name': store.get('short_name', store['name']),
                            'store_key': store_key,
                            'machine_icon': machine['icon'],
                            'machine_name': machine.get('display_name', machine['short_name']),
                            'yesterday_diff': rec.get('yesterday_diff', 0),
                            'avg_art_7days': rec.get('avg_art_7days', 0),
                            'yesterday_art': y_art,
                            'yesterday_rb': rec.get('yesterday_rb', 0),
                            'yesterday_games': y_games,
                            'yesterday_max_rensa': rec.get('yesterday_max_rensa', 0),
                            'yesterday_prob': y_games / y_art if y_art > 0 and y_games > 0 else 0,
                            'yesterday_max_medals': rec.get('yesterday_max_medals', 0),
                            'day_before_art': rec.get('day_before_art', 0),
                            'max_medals': max_medals,
                            'availability': availability.get(rec['unit_id'], ''),
                        })
            except:
                pass

    # スコア順でソートして上位5つ（空き台を優先）
    def top3_sort_key(r):
        score = r['final_score']
        if r.get('availability') == '空き':
            score += 10  # 空き台優先
        return -score

    top3_all.sort(key=top3_sort_key)
    top3 = top3_all[:3]

    # 前日トップ10（最大枚数順）
    yesterday_top10.sort(key=lambda x: -x['max_medals'])
    yesterday_top10 = yesterday_top10[:10]

    # 今日の曜日で店舗をランキング（評価の高い順）
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
    # 今日の評価でソート（高い順）
    today_store_ranking.sort(key=lambda x: -x['today_rating'])

    # 今日おすすめの店舗（評価4以上）
    today_recommended_stores = [s for s in today_store_ranking if s['today_rating'] >= 4]

    # 今日避けるべき店舗（評価1-2）
    today_avoid_stores = [s for s in today_store_ranking if s['today_rating'] <= 2]

    # 結果モードの場合、対象日付を取得
    result_date = None
    result_date_str = None
    if display_mode in ('result', 'collecting'):
        result_date = get_result_date()
        result_date_str = format_date_with_weekday(result_date)

    # 営業時間内かどうか
    is_open = is_business_hours()

    return render_template('index.html',
                           machines=machines,
                           top3=top3,
                           yesterday_top10=yesterday_top10,
                           today_weekday=today_weekday,
                           today_date=today_date,
                           today_date_formatted=format_date_with_weekday(now),
                           now_time=now.strftime('%H:%M'),
                           now_short=now.strftime('%m%d_%H:%M'),
                           store_recommendations=store_recommendations,
                           today_recommended_stores=today_recommended_stores,
                           today_store_ranking=today_store_ranking,
                           today_avoid_stores=today_avoid_stores,
                           store_day_ratings=store_day_ratings,
                           display_mode=display_mode,
                           result_date_str=result_date_str,
                           is_open=is_open)


@app.route('/machine/<machine_key>')
def machine_stores(machine_key: str):
    """機種別店舗一覧"""
    machine = get_machine_info(machine_key)
    stores = get_stores_by_machine(machine_key)
    if not stores:
        return "機種が見つかりません", 404

    store_list = [
        {'key': key, 'name': store['name'], 'unit_count': len(store['units'])}
        for key, store in stores.items()
    ]
    return render_template('stores.html', machine=machine, machine_key=machine_key, stores=store_list)


@app.route('/ranking/<machine_key>')
def ranking(machine_key: str):
    """機種別 全店舗総合ランキング"""
    machine = get_machine_info(machine_key)
    stores = get_stores_by_machine(machine_key)
    if not stores:
        return "機種が見つかりません", 404

    all_recommendations = []

    for store_key, store in stores.items():
        # 空き状況を取得
        availability = {}
        try:
            availability = get_availability(store_key)
        except Exception as e:
            print(f"Availability check failed for {store_key}: {e}")

        recommendations = recommend_units(store_key, availability=availability)
        for rec in recommendations:
            rec['store_name'] = store['name']
            rec['store_key'] = store_key
            all_recommendations.append(rec)

    # スコア順でソート（稼働中は下げる）
    def sort_key(r):
        score = r['final_score']
        if r['is_running']:
            score -= 30
        return -score

    all_recommendations.sort(key=sort_key)

    # ランク別に分類
    top_recs = [r for r in all_recommendations if r['final_rank'] in ('S', 'A') and not r['is_running']][:10]
    other_recs = [r for r in all_recommendations if r not in top_recs][:20]

    return render_template('ranking.html',
                           machine=machine,
                           machine_key=machine_key,
                           top_recs=top_recs,
                           other_recs=other_recs,
                           total_count=len(all_recommendations))


@app.route('/recommend/<store_key>')
def recommend(store_key: str):
    """推奨台表示ページ"""
    # Cloudflare Pagesへリダイレクト
    if REDIRECT_TO_CLOUDFLARE:
        return redirect(f"{CLOUDFLARE_URL}/recommend/{store_key}.html")

    store = STORES.get(store_key)
    if not store:
        return "店舗が見つかりません", 404

    # 機種情報を取得
    machine_key = store.get('machine', 'tokyoghoul')
    machine = get_machine_info(machine_key)

    # キャッシュがあれば使用
    realtime_data = None
    cache_info = None
    now_jst = datetime.now(JST)

    if store_key in REALTIME_CACHE:
        cache = REALTIME_CACHE[store_key]
        # キャッシュの時刻をJSTに変換して比較
        cache_time = cache['fetched_at']
        if cache_time.tzinfo is None:
            cache_time = cache_time.replace(tzinfo=JST)
        cache_age = (now_jst - cache_time).total_seconds()
        if cache_age < 600:  # 10分以内はキャッシュ使用
            realtime_data = cache['data']
            cache_info = {
                'fetched_at': cache_time.strftime('%H:%M'),
                'age_seconds': int(cache_age),
                'source': cache.get('source', 'unknown'),
            }

    # キャッシュがない場合はリアルタイムデータを取得（GitHub or GAS）
    if not realtime_data:
        rt_data = get_realtime_data(store_key)
        if rt_data and (rt_data.get('units') or rt_data.get('source')):
            realtime_data = rt_data
            # fetched_atをパース
            fetched_at_str = rt_data.get('fetched_at', '')
            source = rt_data.get('source', 'unknown')

            if fetched_at_str:
                try:
                    # ISO形式の文字列をパース
                    fetched_time = datetime.fromisoformat(fetched_at_str.replace('Z', '+00:00'))
                    fetched_time_jst = fetched_time.astimezone(JST)
                except:
                    fetched_time_jst = now_jst
            else:
                fetched_time_jst = now_jst

            # キャッシュに保存
            REALTIME_CACHE[store_key] = {
                'data': rt_data,
                'fetched_at': fetched_time_jst,
                'source': source,
            }
            cache_info = {
                'fetched_at': fetched_time_jst.strftime('%H:%M'),
                'age_seconds': int((now_jst - fetched_time_jst).total_seconds()),
                'source': source,
            }

    # リアルタイム空き状況を取得
    availability = {}
    availability_info = None
    try:
        availability = get_availability(store_key)
        if availability:
            availability_info = {
                'fetched_at': datetime.now(JST).strftime('%H:%M'),
                'empty_count': sum(1 for v in availability.values() if v == '空き'),
                'playing_count': sum(1 for v in availability.values() if v == '遊技中'),
            }
    except Exception as e:
        print(f"Availability check failed: {e}")

    recommendations = recommend_units(store_key, realtime_data, availability)

    # ランク別に分類（S/Aランクかつ非稼働を優先、なければ上位3台を表示）
    sa_recs = [r for r in recommendations if r['final_rank'] in ('S', 'A') and not r['is_running']]
    if sa_recs:
        top_recs = sa_recs
    else:
        # S/Aがなくても上位3台は表示（暫定おすすめ）
        top_recs = [r for r in recommendations if not r['is_running']][:3]

    other_recs = [r for r in recommendations if r not in top_recs]

    updated_at = cache_info['fetched_at'] if cache_info else datetime.now(JST).strftime('%H:%M')

    # 営業時間内かどうか
    is_open = is_business_hours()
    display_mode = get_display_mode()

    # 店舗分析（この店舗の機種全体傾向）
    daily_data = load_daily_data(machine_key=machine_key)
    store_analysis = generate_store_analysis(store_key, daily_data)

    # 台番号アラート
    store_alerts = [a for a in get_active_alerts() if a.get('store_key') == store_key]

    return render_template('recommend.html',
                           store=store,
                           store_key=store_key,
                           machine=machine,
                           machine_key=machine_key,
                           top_recs=top_recs,
                           other_recs=other_recs,
                           updated_at=updated_at,
                           cache_info=cache_info,
                           availability_info=availability_info,
                           is_open=is_open,
                           display_mode=display_mode,
                           store_analysis=store_analysis,
                           unit_alerts=store_alerts)


@app.route('/rules')
def rules():
    """法則コーナー - 店舗・機種の傾向と攻略情報"""
    # 店舗別ルール
    store_rules = {
        'island_akihabara_sbj': {
            'name': 'アイランド秋葉原',
            'day_ratings': {'月': 4, '火': 3, '水': 5, '木': 3, '金': 3, '土': 1, '日': 4},
            'best_note': '水曜が最強日、日月も狙い目',
            'worst_note': '土曜は避けるべき',
            'overall_rating': 4,
            'patterns': [
                '水曜に高設定投入の傾向が強い',
                '2日連続マイナス後の上げパターンあり',
                '角台（1015, 1031）は据え置き傾向',
            ],
        },
        'shibuya_espass_sbj': {
            'name': 'エスパス渋谷新館',
            'day_ratings': {'月': 3, '火': 4, '水': 4, '木': 5, '金': 3, '土': 3, '日': 1},
            'best_note': '木曜が最強日、火水も狙い目',
            'worst_note': '日曜は避けるべき',
            'overall_rating': 3,
            'patterns': [
                '木曜の設定投入が顕著',
                '3台中1台は高設定の傾向',
                '連日プラスの台は据え置き率高い',
            ],
        },
        'shinjuku_espass_sbj': {
            'name': 'エスパス歌舞伎町',
            'day_ratings': {'月': 2, '火': 3, '水': 3, '木': 3, '金': 4, '土': 5, '日': 3},
            'best_note': '土曜が最強日、金曜も狙い目',
            'worst_note': '月曜は控えめ',
            'overall_rating': 3,
            'patterns': [
                '週末型の設定投入パターン',
                '平日は控えめな傾向',
            ],
        },
        'akiba_espass_sbj': {
            'name': 'エスパス秋葉原駅前',
            'day_ratings': {'月': 2, '火': 3, '水': 3, '木': 3, '金': 4, '土': 5, '日': 4},
            'best_note': '土日が狙い目、金曜も可',
            'worst_note': '月曜は控えめ',
            'overall_rating': 3,
            'patterns': [
                '週末重視の傾向',
                '土日は複数台に期待',
            ],
        },
        'seibu_shinjuku_espass_sbj': {
            'name': 'エスパス西武新宿',
            'day_ratings': {'月': 2, '火': 2, '水': 3, '木': 3, '金': 4, '土': 4, '日': 3},
            'best_note': '金土が狙い目',
            'worst_note': '月火は控えめ',
            'overall_rating': 2,
            'patterns': [
                '週末型だが控えめ',
                '平日は低設定傾向',
            ],
        },
    }

    # 機種別ルール
    machine_rules = {
        'sbj': {
            'name': 'スーパーブラックジャック',
            'icon': '🃏',
            'setting6_prob': 181.3,
            'setting1_prob': 241.7,
            'tenjou': '999G+α（リセット時666G）',
            'tips': [
                'ART確率1/100以下なら高設定濃厚',
                '10連以上の爆発があれば信頼度UP',
                'ミミズ展開（平坦）からの爆発に期待',
                '天井直撃が2回以上あれば低設定警戒',
            ],
            'reset_info': '天井999G→666Gに短縮、スイカ天井も優遇（30%で30回以下）',
        },
        'hokuto2': {
            'name': '北斗の拳 転生の章2',
            'icon': '👊',
            'setting6_prob': 273.1,
            'setting1_prob': 366.0,
            'tenjou': 'モード依存（A:1536/B:896/C:576あべし）',
            'tips': [
                'AT確率1/290以下なら高設定域',
                '天撃失敗後は天国モード濃厚→即やめ厳禁',
                'あべしUI赤色も天国濃厚',
                '193〜256あべしは全モード共通チャンスゾーン',
            ],
            'reset_info': 'リセット時は最大1280あべしに短縮',
        },
    }

    # 一般的な立ち回りTips
    general_tips = [
        {
            'title': '朝イチの狙い方',
            'text': '前日凹み台（連続マイナス）はリセット狙い。天井短縮の恩恵がある機種は特に有効。',
        },
        {
            'title': '夕方からの立ち回り',
            'text': '当日好調台を確認。ART確率が良く、まだ伸びしろがある台を狙う。',
        },
        {
            'title': '設定判別のタイミング',
            'text': '3000G以上回ってから判断。それ以下は引き次第でブレる。',
        },
        {
            'title': 'やめどき',
            'text': '天井到達後、または連チャン終了後の100G以内に判断。ダラダラ打たない。',
        },
        {
            'title': 'モミモミ台の扱い',
            'text': '大連荘なく淡々と当たる台は、爆発前の溜め期間の可能性。粘る価値あり。',
        },
    ]

    return render_template('rules.html',
                           store_rules=store_rules,
                           machine_rules=machine_rules,
                           general_tips=general_tips)


@app.route('/history/<store_key>/<unit_id>')
def unit_history(store_key: str, unit_id: str):
    """台別の当たり履歴を表示"""
    store = STORES.get(store_key)
    if not store:
        return "店舗が見つかりません", 404

    machine_key = store.get('machine', 'tokyoghoul')
    machine = get_machine_info(machine_key)

    # 日別データを読み込み
    daily_data = load_daily_data(machine_key=machine_key)

    history = []
    summary = None
    analysis = None
    history_date = None

    if daily_data:
        # データ内の店舗キーで検索
        store_data = None
        for key_to_try in [store_key, f'{store_key}_sbj']:
            store_data = daily_data.get('stores', {}).get(key_to_try, {})
            if store_data:
                break

        if store_data:
            for unit in store_data.get('units', []):
                if unit.get('unit_id') == unit_id:
                    # 最新日のデータを取得
                    days = unit.get('days', [])
                    if days:
                        # 日付順でソート（新しい順）
                        sorted_days = sorted(days, key=lambda x: x.get('date', ''), reverse=True)
                        latest_day = sorted_days[0]
                        history_date = latest_day.get('date', '')

                        # 履歴データを整形
                        raw_history = latest_day.get('history', [])
                        tenjou_count = 0
                        max_rensa = latest_day.get('max_rensa', 0)  # dayから取得
                        valleys = []

                        for i, h in enumerate(raw_history):
                            start = h.get('start', 0) or h.get('games_between', 0)
                            rensa = h.get('rensa', 1)
                            is_tenjou = start >= 999

                            if is_tenjou:
                                tenjou_count += 1
                            if start > 0:
                                valleys.append(start)

                            history.append({
                                'time': h.get('time', ''),
                                'start': start,
                                'type': h.get('type', 'ART'),
                                'rensa': rensa,
                                'medals': h.get('medals', 0) or h.get('diff', 0),
                                'is_tenjou': is_tenjou,
                            })

                        # サマリー計算
                        total_art = latest_day.get('art', 0)
                        total_games = latest_day.get('total_start', 0)
                        max_medals = latest_day.get('max_medals', 0)
                        art_prob = total_games / total_art if total_art > 0 else 0
                        avg_valley = sum(valleys) / len(valleys) if valleys else 0

                        summary = {
                            'total_art': total_art,
                            'total_games': total_games,
                            'art_prob': art_prob,
                            'max_medals': max_medals,
                            'max_rensa': max_rensa,
                            'tenjou_count': tenjou_count,
                            'avg_valley': avg_valley,
                        }

                        # グラフ分析
                        if total_art >= 10:
                            if tenjou_count == 0 and avg_valley < 100:
                                analysis = {
                                    'pattern_name': '超安定型',
                                    'description': '天井到達なし、平均ハマりも浅い。高設定濃厚。',
                                    'recommendation': '継続推奨。閉店まで打ち切りたい。',
                                }
                            elif max_rensa >= 10:
                                analysis = {
                                    'pattern_name': '爆発型',
                                    'description': f'{max_rensa}連の大爆発あり。出玉感のある台。',
                                    'recommendation': '高設定でも低設定でもありえる。他の指標と合わせて判断。',
                                }
                            elif tenjou_count >= 2:
                                analysis = {
                                    'pattern_name': '天井依存型',
                                    'description': f'天井到達{tenjou_count}回。引きが悪いか低設定。',
                                    'recommendation': '様子見推奨。他に空き台があれば移動検討。',
                                }
                            elif avg_valley > 150:
                                analysis = {
                                    'pattern_name': '重い展開',
                                    'description': f'平均{avg_valley:.0f}Gと重め。苦しい展開。',
                                    'recommendation': '低設定の可能性。撤退も視野に。',
                                }
                            else:
                                analysis = {
                                    'pattern_name': '標準型',
                                    'description': '特に際立った特徴なし。',
                                    'recommendation': 'ART確率で判断。1/130以下なら継続。',
                                }
                    break

    return render_template('history.html',
                           store=store,
                           store_key=store_key,
                           unit_id=unit_id,
                           machine=machine,
                           history=history,
                           summary=summary,
                           analysis=analysis,
                           history_date=history_date)


@app.route('/api/status/<store_key>')
def api_status(store_key: str):
    """API: 台状況をJSON形式で返す"""
    store = STORES.get(store_key)
    if not store:
        return jsonify({'error': 'Store not found'}), 404

    # リアルタイムデータを取得
    realtime_data = None
    if store_key in REALTIME_CACHE:
        cache = REALTIME_CACHE[store_key]
        cache_age = (datetime.now(JST) - cache['fetched_at']).total_seconds()
        if cache_age < 600:
            realtime_data = cache['data']

    # キャッシュがない場合はGitHubから取得
    if not realtime_data:
        realtime_data = get_realtime_data(store_key)

    # 空き状況も取得
    availability = {}
    try:
        availability = get_availability(store_key)
    except:
        pass

    recommendations = recommend_units(store_key, realtime_data, availability)

    return jsonify({
        'store': store['name'],
        'updated_at': datetime.now(JST).isoformat(),
        'units': recommendations,
    })


@app.route('/api/refresh/<store_key>')
def api_refresh(store_key: str):
    """API: リアルタイムデータを取得して更新"""
    store = STORES.get(store_key)
    if not store:
        return jsonify({'error': 'Store not found'}), 404

    # キャッシュがあればそれを使用
    if store_key in REALTIME_CACHE:
        cache = REALTIME_CACHE[store_key]
        cache_age = (datetime.now(JST) - cache['fetched_at']).total_seconds()
        if cache_age < 300:  # 5分以内はキャッシュ使用
            recommendations = recommend_units(store_key, cache['data'])
            return jsonify({
                'store': store['name'],
                'updated_at': cache['fetched_at'].isoformat(),
                'cache_age_seconds': int(cache_age),
                'units': recommendations,
            })

    # キャッシュなしの場合は既存データで推奨
    recommendations = recommend_units(store_key)

    return jsonify({
        'store': store['name'],
        'updated_at': datetime.now(JST).isoformat(),
        'units': recommendations,
        'note': 'Using historical data. Click "Get Latest" to fetch real-time data.',
    })


def run_scraping(store_key: str, max_retries: int = 3):
    """バックグラウンドでデータを取得（GitHub JSON優先、リトライ＆フォールバック対応）"""
    import logging
    import time
    
    SCRAPING_STATUS[store_key] = {'status': 'running', 'started_at': datetime.now(JST)}
    last_error = None
    
    for attempt in range(max_retries):
        try:
            # まずGitHubからリアルタイムデータを試す
            realtime_data = get_realtime_data(store_key)

            if realtime_data and realtime_data.get('units'):
                REALTIME_CACHE[store_key] = {
                    'data': realtime_data,
                    'fetched_at': datetime.now(JST),
                    'source': 'github',
                }
                SCRAPING_STATUS[store_key] = {'status': 'completed', 'completed_at': datetime.now(JST), 'source': 'github'}
                return

            # GitHubにデータがない場合は直接スクレイピングを試みる
            from scrapers.realtime_scraper import scrape_realtime
            results = scrape_realtime(store_key)

            if store_key in results:
                REALTIME_CACHE[store_key] = {
                    'data': results[store_key],
                    'fetched_at': datetime.now(JST),
                    'source': 'direct',
                }
                SCRAPING_STATUS[store_key] = {'status': 'completed', 'completed_at': datetime.now(JST), 'source': 'direct'}
                return
            else:
                last_error = 'No data returned'
                
        except Exception as e:
            last_error = str(e)
            logging.error(f"[run_scraping] {store_key} attempt {attempt + 1}/{max_retries} failed: {e}")
            
        # リトライ前に待機（最後の試行では待たない）
        if attempt < max_retries - 1:
            time.sleep(2)
    
    # 全リトライ失敗 → フォールバック（蓄積データを使用）
    logging.error(f"[run_scraping] {store_key} all retries failed, using fallback")
    try:
        fallback_data = load_fallback_history(store_key)
        if fallback_data:
            REALTIME_CACHE[store_key] = {
                'data': fallback_data,
                'fetched_at': datetime.now(JST),
                'source': 'fallback',
            }
            SCRAPING_STATUS[store_key] = {
                'status': 'completed',
                'completed_at': datetime.now(JST),
                'source': 'fallback',
                'warning': f'Using cached data due to fetch error: {last_error}',
            }
            return
    except Exception as fallback_error:
        logging.error(f"[run_scraping] {store_key} fallback also failed: {fallback_error}")
    
    SCRAPING_STATUS[store_key] = {'status': 'error', 'error': last_error or 'Unknown error'}


def load_fallback_history(store_key: str) -> dict:
    """蓄積データ（data/history/）からフォールバックデータを読み込む"""
    import json
    from pathlib import Path
    
    history_dir = Path(f'data/history/{store_key}')
    if not history_dir.exists():
        return None
    
    units = []
    for json_file in history_dir.glob('*.json'):
        try:
            with open(json_file) as f:
                data = json.load(f)
            unit_id = json_file.stem
            # 最新日のデータを取得
            if data.get('days'):
                latest = data['days'][0]
                units.append({
                    'unit_id': unit_id,
                    'art': latest.get('art', 0),
                    'rb': latest.get('rb', 0),
                    'total_start': latest.get('total_games', 0),
                    'history': latest.get('history', []),
                    'max_medals': latest.get('max_medals', 0),
                    'max_rensa': latest.get('max_rensa', 0),
                    'is_fallback': True,
                })
        except Exception:
            continue
    
    if not units:
        return None
    
    return {
        'store_key': store_key,
        'units': units,
        'is_fallback': True,
    }


@app.route('/api/debug/<store_key>')
def api_debug(store_key: str):
    """API: デバッグ情報を表示"""
    store = STORES.get(store_key)
    if not store:
        return jsonify({'error': 'Store not found'}), 404

    # キャッシュ情報
    cache = REALTIME_CACHE.get(store_key)
    cache_info = None
    if cache:
        cache_data = cache.get('data', {})
        cache_info = {
            'fetched_at': cache.get('fetched_at').isoformat() if cache.get('fetched_at') else None,
            'store_name': cache_data.get('store_name'),
            'units_count': len(cache_data.get('units', [])),
            'units_preview': [{'unit_id': u.get('unit_id'), 'art': u.get('art'), 'total_start': u.get('total_start')} for u in cache_data.get('units', [])[:5]],
            'debug': cache_data.get('debug'),
        }

    # スクレイピング状態
    status = SCRAPING_STATUS.get(store_key)

    return jsonify({
        'store': store['name'],
        'cache': cache_info,
        'scraping_status': status,
        'app_version': APP_VERSION,
    })


@app.route('/api/scrape/<store_key>')
def api_scrape(store_key: str):
    """API: リアルタイムスクレイピングを開始"""
    store = STORES.get(store_key)
    if not store:
        return jsonify({'error': 'Store not found'}), 404

    # 既に実行中かチェック
    if store_key in SCRAPING_STATUS:
        status = SCRAPING_STATUS[store_key]
        if status.get('status') == 'running':
            elapsed = (datetime.now(JST) - status['started_at']).total_seconds()
            return jsonify({
                'status': 'running',
                'elapsed_seconds': int(elapsed),
                'message': 'Scraping in progress...',
            })

    # バックグラウンドでスクレイピング開始
    thread = threading.Thread(target=run_scraping, args=(store_key,))
    thread.daemon = True
    thread.start()

    return jsonify({
        'status': 'started',
        'message': 'Scraping started. Please wait...',
    })


@app.route('/api/scrape_status/<store_key>')
def api_scrape_status(store_key: str):
    """API: スクレイピング状況を確認"""
    store = STORES.get(store_key)
    if not store:
        return jsonify({'error': 'Store not found'}), 404

    status = SCRAPING_STATUS.get(store_key, {'status': 'idle'})

    if status.get('status') == 'completed':
        # 完了していたら推奨データも返す
        cache = REALTIME_CACHE.get(store_key)
        if cache:
            recommendations = recommend_units(store_key, cache['data'])
            return jsonify({
                'status': 'completed',
                'fetched_at': cache['fetched_at'].isoformat(),
                'units': recommendations,
            })

    return jsonify(status)


# ========================================
# Cloudflare Pages用 JSON API (v2)
# ========================================

@app.route('/api/v2/index')
def api_v2_index():
    """API v2: トップページ用データをJSON形式で返す（5分キャッシュ）"""
    cache_key = 'api_v2_index'
    current_time = time.time()

    # キャッシュチェック（5分以内なら返す）
    if cache_key in REALTIME_CACHE:
        cached_data, cached_time = REALTIME_CACHE[cache_key]
        age_seconds = current_time - cached_time
        if age_seconds < CACHE_DURATION:
            # キャッシュヒット（年齢情報を追加）
            cached_data['cache_hit'] = True
            cached_data['cache_age_seconds'] = int(age_seconds)
            return jsonify(cached_data)

    # キャッシュミス → リアルタイムデータ取得
    now = datetime.now(JST)
    display_mode = get_display_mode()
    is_open = is_business_hours()
    today_weekday = WEEKDAY_NAMES[now.weekday()]

    # 店舗曜日傾向
    store_day_ratings = {
        'island_akihabara_sbj': {
            'name': 'アイランド秋葉原',
            'day_ratings': {'月': 4, '火': 3, '水': 5, '木': 3, '金': 3, '土': 1, '日': 4},
        },
        'shibuya_espass_sbj': {
            'name': 'エスパス渋谷新館',
            'day_ratings': {'月': 3, '火': 4, '水': 4, '木': 5, '金': 3, '土': 3, '日': 1},
        },
        'shinjuku_espass_sbj': {
            'name': 'エスパス歌舞伎町',
            'day_ratings': {'月': 2, '火': 3, '水': 3, '木': 3, '金': 4, '土': 5, '日': 3},
        },
        'akiba_espass_sbj': {
            'name': 'エスパス秋葉原',
            'day_ratings': {'月': 2, '火': 3, '水': 3, '木': 3, '金': 4, '土': 5, '日': 4},
        },
        'seibu_shinjuku_espass_sbj': {
            'name': 'エスパス西武新宿',
            'day_ratings': {'月': 2, '火': 2, '水': 3, '木': 3, '金': 4, '土': 4, '日': 3},
        },
    }

    # トップ台を収集
    top3_all = []
    for key, machine in MACHINES.items():
        stores = get_stores_by_machine(key)
        for store_key, store in stores.items():
            try:
                availability = {}
                try:
                    availability = get_availability(store_key)
                except:
                    pass

                realtime_data = get_realtime_data(store_key)
                recs = recommend_units(store_key, realtime_data, availability)

                for rec in recs[:3]:
                    rec['store_name'] = store.get('short_name', store['name'])
                    rec['store_key'] = store_key
                    rec['machine_key'] = key
                    rec['machine_icon'] = machine['icon']
                    rec['machine_name'] = machine.get('display_name', machine['short_name'])
                    rec['availability'] = availability.get(rec['unit_id'], '')

                    reasons_text = ' '.join(rec.get('reasons', []))
                    if rec['final_rank'] in ('S', 'A') and '様子見' not in reasons_text:
                        top3_all.append(rec)
            except Exception as e:
                print(f"API v2 error for {store_key}: {e}")

    # ソート
    def top3_sort_key(r):
        score = r['final_score']
        if r.get('availability') == '空き':
            score += 10
        return -score

    top3_all.sort(key=top3_sort_key)
    top3 = top3_all[:3]

    # 今日の曜日ランキング
    today_store_ranking = []
    for store_key, info in store_day_ratings.items():
        today_rating = info['day_ratings'].get(today_weekday, 3)
        today_store_ranking.append({
            'store_key': store_key,
            'name': info['name'],
            'today_rating': today_rating,
        })
    today_store_ranking.sort(key=lambda x: -x['today_rating'])

    # レスポンスデータ作成
    response_data = {
        'updated_at': now.isoformat(),
        'display_mode': display_mode,
        'is_open': is_open,
        'today_weekday': today_weekday,
        'today_date': format_date_with_weekday(now),
        'top3': top3,
        'today_store_ranking': today_store_ranking,
        'cache_hit': False,
        'cache_age_seconds': 0,
    }

    # キャッシュに保存（5分間）
    REALTIME_CACHE[cache_key] = (response_data, current_time)

    return jsonify(response_data)


@app.route('/api/v2/recommend/<store_key>')
def api_v2_recommend(store_key: str):
    """API v2: 店舗別推奨台データをJSON形式で返す"""
    store = STORES.get(store_key)
    if not store:
        return jsonify({'error': 'Store not found'}), 404

    now = datetime.now(JST)
    display_mode = get_display_mode()
    is_open = is_business_hours()

    machine_key = store.get('machine', 'tokyoghoul')
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
                    cache_info = {
                        'fetched_at': fetched_time_jst.strftime('%H:%M'),
                        'age_seconds': int((now - fetched_time_jst).total_seconds()),
                        'source': rt_data.get('source', 'unknown'),
                    }
                except:
                    pass
    except:
        pass

    recommendations = recommend_units(store_key, realtime_data, availability)

    # 分類
    sa_recs = [r for r in recommendations if r['final_rank'] in ('S', 'A') and not r['is_running']]
    if sa_recs:
        top_recs = sa_recs
    else:
        top_recs = [r for r in recommendations if not r['is_running']][:3]

    other_recs = [r for r in recommendations if r not in top_recs]

    return jsonify({
        'updated_at': now.isoformat(),
        'display_mode': display_mode,
        'is_open': is_open,
        'store': {
            'key': store_key,
            'name': store['name'],
            'short_name': store.get('short_name', store['name']),
        },
        'machine': {
            'key': machine_key,
            'name': machine['name'],
            'icon': machine['icon'],
        },
        'cache_info': cache_info,
        'top_recs': top_recs,
        'other_recs': other_recs,
    })


# テンプレートにランク色を提供
@app.context_processor
def utility_processor():
    def rank_color(rank):
        colors = {
            'S': '#ff6b6b',  # 赤
            'A': '#ffa502',  # オレンジ
            'B': '#2ed573',  # 緑
            'C': '#70a1ff',  # 青
            'D': '#747d8c',  # グレー
        }
        return colors.get(rank, '#747d8c')

    def rank_stars(rank):
        stars = {
            'S': 3,
            'A': 2,
            'B': 1,
            'C': 0,
            'D': 0,
        }
        return stars.get(rank, 0)

    def signed_number(value):
        """符号付きカンマ区切り数値フォーマット"""
        try:
            num = int(value)
            if num >= 0:
                return f'+{num:,}'
            else:
                return f'{num:,}'
        except (ValueError, TypeError):
            return str(value)

    def medals_badge(value):
        """最大獲得枚数に応じたバッジを返す"""
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
            else:
                return None
        except (ValueError, TypeError):
            return None

    def short_date(date_str):
        """日付を短い形式に変換 (例: 2026-02-18 → 2/18)"""
        if not date_str:
            return ''
        try:
            from datetime import datetime
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            return f'{dt.month}/{dt.day}'
        except:
            return date_str

    return dict(rank_color=rank_color, rank_stars=rank_stars, signed_number=signed_number, medals_badge=medals_badge, short_date=short_date)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='SBJ 台選びアシスタント')
    parser.add_argument('--host', default='0.0.0.0', help='ホスト (default: 0.0.0.0)')
    parser.add_argument('--port', '-p', type=int, default=5000, help='ポート (default: 5000)')
    parser.add_argument('--debug', '-d', action='store_true', help='デバッグモード')
    args = parser.parse_args()

    print(f"""
====================================
  SBJ 台選びアシスタント
====================================
  URL: http://localhost:{args.port}

  ngrokでトンネル作成:
    ngrok http {args.port}

  登録店舗:
""")
    for key, store in STORES.items():
        if store['units']:
            print(f"    - {store['name']} ({len(store['units'])}台)")
    print()

    app.run(host=args.host, port=args.port, debug=args.debug)
