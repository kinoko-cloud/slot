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
from analysis.recommender import recommend_units, load_daily_data
from scrapers.availability_checker import get_availability, get_realtime_data

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
    return '#'


def generate_index(env):
    """トップページを生成"""
    print("Generating index.html...")

    template = env.get_template('index.html')

    now = datetime.now(JST)
    display_mode = get_display_mode()
    is_open = is_business_hours()
    today_weekday = WEEKDAY_NAMES[now.weekday()]
    today_date = now.strftime('%Y/%m/%d')
    today_date_formatted = format_date_with_weekday(now)

    # 店舗曜日傾向
    store_day_ratings = {
        'island_akihabara_sbj': {
            'name': 'アイランド秋葉原',
            'short_name': 'アイランド秋葉原',
            'day_ratings': {'月': 4, '火': 3, '水': 5, '木': 3, '金': 3, '土': 1, '日': 4},
            'best_note': '水曜が最強、日月も狙い目',
            'worst_note': '土曜は避けるべき',
            'overall_rating': 4,
        },
        'shibuya_espass_sbj': {
            'name': 'エスパス日拓渋谷新館',
            'short_name': 'エスパス渋谷新館',
            'day_ratings': {'月': 3, '火': 4, '水': 4, '木': 5, '金': 3, '土': 3, '日': 1},
            'best_note': '木曜が最強、火水も狙い目',
            'worst_note': '日曜は避けるべき',
            'overall_rating': 3,
        },
        'shinjuku_espass_sbj': {
            'name': 'エスパス日拓新宿歌舞伎町店',
            'short_name': 'エスパス歌舞伎町',
            'day_ratings': {'月': 2, '火': 3, '水': 3, '木': 3, '金': 4, '土': 5, '日': 3},
            'best_note': '土曜が最強、金曜も狙い目',
            'worst_note': '月曜は控えめ',
            'overall_rating': 3,
        },
        'akihabara_espass_sbj': {
            'name': 'エスパス日拓秋葉原駅前店',
            'short_name': 'エスパス秋葉原',
            'day_ratings': {'月': 2, '火': 3, '水': 3, '木': 3, '金': 4, '土': 5, '日': 4},
            'best_note': '土日が狙い目、金曜も可',
            'worst_note': '月曜は控えめ',
            'overall_rating': 3,
        },
        'seibu_shinjuku_espass_sbj': {
            'name': 'エスパス日拓西武新宿駅前店',
            'short_name': 'エスパス西武新宿',
            'day_ratings': {'月': 2, '火': 2, '水': 3, '木': 3, '金': 4, '土': 4, '日': 3},
            'best_note': '金土が狙い目',
            'worst_note': '月火は控えめ',
            'overall_rating': 2,
        },
    }

    # 機種一覧とトップ台を収集
    machines = []
    top3_all = []
    yesterday_top10 = []

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

                recs = recommend_units(store_key, availability=availability)
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

                    max_medals = rec.get('max_medals', 0)
                    if max_medals > 3000 or rec.get('yesterday_diff', 0) > 500:
                        yesterday_top10.append({
                            'unit_id': rec['unit_id'],
                            'store_name': store.get('short_name', store['name']),
                            'store_key': store_key,
                            'machine_icon': machine['icon'],
                            'machine_name': machine.get('display_name', machine['short_name']),
                            'yesterday_diff': rec.get('yesterday_diff', 0),
                            'avg_art_7days': rec.get('avg_art_7days', 0),
                            'yesterday_art': rec.get('yesterday_art', 0),
                            'max_medals': max_medals,
                            'availability': availability.get(rec['unit_id'], ''),
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
    top3 = top3_all[:5]

    yesterday_top10.sort(key=lambda x: -x['max_medals'])
    yesterday_top10 = yesterday_top10[:10]

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
        })
    today_store_ranking.sort(key=lambda x: -x['today_rating'])

    today_recommended_stores = [s for s in today_store_ranking if s['today_rating'] >= 4]
    today_avoid_stores = [s for s in today_store_ranking if s['today_rating'] <= 2]

    result_date_str = None
    if display_mode in ('result', 'collecting'):
        if now.hour >= 23:
            result_date = now
        else:
            result_date = now - timedelta(days=1)
        result_date_str = format_date_with_weekday(result_date)

    html = template.render(
        machines=machines,
        top3=top3,
        yesterday_top10=yesterday_top10,
        today_weekday=today_weekday,
        today_date=today_date,
        today_date_formatted=today_date_formatted,
        store_recommendations={},
        today_recommended_stores=today_recommended_stores,
        today_store_ranking=today_store_ranking,
        today_avoid_stores=today_avoid_stores,
        store_day_ratings=store_day_ratings,
        display_mode=display_mode,
        result_date_str=result_date_str,
        is_open=is_open,
    )

    output_path = OUTPUT_DIR / 'index.html'
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

    for store_key, store in STORES.items():
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

        recommendations = recommend_units(store_key, realtime_data, availability)

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
        )

        output_path = output_subdir / f'{store_key}.html'
        output_path.write_text(html, encoding='utf-8')

    print(f"  -> {output_subdir}/")


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


def main():
    print("=" * 50)
    print("静的サイト生成開始")
    print(f"出力先: {OUTPUT_DIR}")
    print("=" * 50)
    print()

    # 出力ディレクトリを作成
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Jinja2環境をセットアップ
    env = setup_jinja()

    # 各ページを生成
    generate_index(env)
    generate_recommend_pages(env)
    copy_static_files()
    generate_metadata()

    print()
    print("=" * 50)
    print("静的サイト生成完了!")
    print("=" * 50)


if __name__ == '__main__':
    main()
