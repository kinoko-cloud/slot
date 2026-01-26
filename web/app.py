#!/usr/bin/env python3
"""
SBJ 台選びアシスタント - Webアプリ

iPhoneから店舗でアクセスして、推奨台を確認するためのWebアプリ
"""

import json
import sys
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 日本時間
JST = timezone(timedelta(hours=9))

from flask import Flask, render_template, jsonify, request

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.rankings import STORES, RANKINGS, MACHINES, get_stores_by_machine, get_machine_info
from analysis.recommender import recommend_units, load_daily_data
from scrapers.availability_checker import get_availability

app = Flask(__name__)

# キャッシュ無効化
@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# デプロイ用シークレット
DEPLOY_SECRET = 'slot_deploy_2026'

# リアルタイムデータキャッシュ
REALTIME_CACHE = {}
SCRAPING_STATUS = {}

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

    secret = request.form.get('secret') or request.args.get('secret')
    if secret != DEPLOY_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        # git pull を実行
        result = subprocess.run(
            ['git', 'pull', 'origin', 'main'],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30
        )
        return jsonify({
            'status': 'success',
            'output': result.stdout,
            'error': result.stderr,
            'returncode': result.returncode
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/')
def index():
    """メインページ - 機種選択 + トップ3 + 店舗おすすめ曜日 + 前日トップ3"""
    machines = []
    top3_all = []
    yesterday_top3 = []

    # 店舗別おすすめ曜日（過去データからの傾向）
    store_recommendations = {
        'island_akihabara_sbj': {
            'name': 'アイランド秋葉原店',
            'best_days': ['土', '日'],
            'note': '週末に高設定投入傾向',
        },
        'shibuya_espass_sbj': {
            'name': 'エスパス日拓渋谷新館',
            'best_days': ['金', '土'],
            'note': '金曜夜から狙い目',
        },
        'shinjuku_espass_sbj': {
            'name': 'エスパス日拓新宿歌舞伎町店',
            'best_days': ['土'],
            'note': '土曜日がアツい',
        },
    }

    # 今日の曜日
    weekday_names = ['月', '火', '水', '木', '金', '土', '日']
    today_weekday = weekday_names[datetime.now(JST).weekday()]

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
                recs = recommend_units(store_key)
                for rec in recs[:2]:  # 各店舗上位2台
                    if not rec['is_running'] and rec['final_rank'] in ('S', 'A'):
                        rec['store_name'] = store['name']
                        rec['store_key'] = store_key
                        rec['machine_key'] = key
                        rec['machine_icon'] = machine['icon']
                        rec['machine_name'] = machine['short_name']
                        top3_all.append(rec)

                    # 前日トップ3用のデータを収集（昨日の差枚が大きい台）
                    if rec.get('yesterday_diff', 0) > 1000:
                        yesterday_top3.append({
                            'unit_id': rec['unit_id'],
                            'store_name': store['name'],
                            'store_key': store_key,
                            'machine_icon': machine['icon'],
                            'yesterday_diff': rec['yesterday_diff'],
                            'avg_art_7days': rec.get('avg_art_7days', 0),
                        })
            except:
                pass

    # スコア順でソートして上位3つ
    top3_all.sort(key=lambda x: -x['final_score'])
    top3 = top3_all[:3]

    # 前日トップ3（差枚順）
    yesterday_top3.sort(key=lambda x: -x['yesterday_diff'])
    yesterday_top3 = yesterday_top3[:3]

    # 今日おすすめの店舗
    today_recommended_stores = []
    for store_key, info in store_recommendations.items():
        if today_weekday in info['best_days']:
            today_recommended_stores.append(info)

    return render_template('index.html',
                           machines=machines,
                           top3=top3,
                           yesterday_top3=yesterday_top3,
                           today_weekday=today_weekday,
                           store_recommendations=store_recommendations,
                           today_recommended_stores=today_recommended_stores)


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
    store = STORES.get(store_key)
    if not store:
        return "店舗が見つかりません", 404

    # 機種情報を取得
    machine_key = store.get('machine', 'sbj')
    machine = get_machine_info(machine_key)

    # キャッシュがあれば使用
    realtime_data = None
    cache_info = None
    if store_key in REALTIME_CACHE:
        cache = REALTIME_CACHE[store_key]
        cache_age = (datetime.now() - cache['fetched_at']).total_seconds()
        if cache_age < 600:  # 10分以内はキャッシュ使用
            realtime_data = cache['data']
            cache_info = {
                'fetched_at': cache['fetched_at'].strftime('%H:%M'),
                'age_seconds': int(cache_age),
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

    # ランク別に分類
    top_recs = [r for r in recommendations if r['final_rank'] in ('S', 'A') and not r['is_running']]
    other_recs = [r for r in recommendations if r not in top_recs]

    updated_at = cache_info['fetched_at'] if cache_info else datetime.now(JST).strftime('%H:%M')

    return render_template('recommend.html',
                           store=store,
                           store_key=store_key,
                           machine=machine,
                           machine_key=machine_key,
                           top_recs=top_recs,
                           other_recs=other_recs,
                           updated_at=updated_at,
                           cache_info=cache_info,
                           availability_info=availability_info)


@app.route('/api/status/<store_key>')
def api_status(store_key: str):
    """API: 台状況をJSON形式で返す"""
    store = STORES.get(store_key)
    if not store:
        return jsonify({'error': 'Store not found'}), 404

    recommendations = recommend_units(store_key)

    return jsonify({
        'store': store['name'],
        'updated_at': datetime.now().isoformat(),
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
        cache_age = (datetime.now() - cache['fetched_at']).total_seconds()
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
        'updated_at': datetime.now().isoformat(),
        'units': recommendations,
        'note': 'Using historical data. Click "Get Latest" to fetch real-time data.',
    })


def run_scraping(store_key: str):
    """バックグラウンドでスクレイピングを実行"""
    SCRAPING_STATUS[store_key] = {'status': 'running', 'started_at': datetime.now()}
    try:
        from scrapers.realtime_scraper import scrape_realtime
        results = scrape_realtime(store_key)

        if store_key in results:
            REALTIME_CACHE[store_key] = {
                'data': results[store_key],
                'fetched_at': datetime.now(),
            }
            SCRAPING_STATUS[store_key] = {'status': 'completed', 'completed_at': datetime.now()}
        else:
            SCRAPING_STATUS[store_key] = {'status': 'error', 'error': 'No data returned'}
    except Exception as e:
        SCRAPING_STATUS[store_key] = {'status': 'error', 'error': str(e)}


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
            elapsed = (datetime.now() - status['started_at']).total_seconds()
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

    return dict(rank_color=rank_color, rank_stars=rank_stars, signed_number=signed_number)


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
