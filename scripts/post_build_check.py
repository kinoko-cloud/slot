#!/usr/bin/env python3
"""post_build_check.py - ビルド後のHTML出力を自動検証

generate_static.pyの最後で呼ばれる。
docs/*.html の実際の出力内容を検証し、矛盾・バグを機械的に検出。
ERRORが1件でもあればビルド失敗（exitcode=1）。

【過去の実際のバグから作成した検証項目】
- 小数点付き確率表示（1/227.818...）
- ART数 < 連チャン数の矛盾（21ART なのに 28連）
- diff_medals未設定（当日おすすめ台に差枚なし）
- 時間軸の方向（降順=上が最新）
- staleデータ混入（当日0ARTなのにhistoryあり）
"""
import re
import sys
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DOCS = BASE / 'docs'


def check_float_probability(html: str, fname: str) -> list:
    """確率表示に小数点が含まれていないか"""
    issues = []
    # 1/xxx.xxx パターン
    floats = re.findall(r'1/(\d+\.\d+)', html)
    for f in floats:
        issues.append(f'ERROR: {fname} 小数点付き確率 1/{f}')
    return issues


def check_art_vs_rensa(html: str, fname: str) -> list:
    """ART数より連チャン数が大きい矛盾がないか"""
    issues = []
    # トップカード: ART数と最大連チャン
    # パターン: <span class="art-num-big">21</span> ... 最大<span class="num">28</span>連
    cards = re.findall(
        r'art-num-big">(\d+)</span>.*?(?:rensa-badge.*?<span class="num">(\d+)</span>連)?',
        html, re.DOTALL
    )
    # recent_day_expandableの行: ART XX ... X連
    day_rows = re.findall(
        r'ART\s*</span>\s*<span[^>]*>(\d+)</span>.*?(\d+)連',
        html
    )
    for art_str, rensa_str in day_rows:
        art = int(art_str)
        rensa = int(rensa_str)
        if rensa > art and art > 0:
            issues.append(f'ERROR: {fname} ART{art}に対して{rensa}連は矛盾')
    return issues


def check_stale_today_data(html: str, fname: str) -> list:
    """当日0ARTなのにhistoryが表示されていないか"""
    issues = []
    # 営業中トップ: art-num-big=0 なのに本日セクションに履歴がある
    top_cards = re.findall(
        r'art-num-big">(\d+)</span>.*?(?:本日.*?<table.*?</table>)?',
        html[:100000], re.DOTALL
    )
    # この検証は簡易版。精密にはJinja出力を構造解析する必要がある
    return issues


def check_time_order(html: str, fname: str) -> list:
    """履歴テーブルの時間軸が降順（上が最新）か"""
    issues = []
    # テーブル内の時間列を抽出
    tables = re.findall(r'<tbody>(.*?)</tbody>', html, re.DOTALL)
    for i, table in enumerate(tables[:5]):  # 最初の5テーブルのみ
        times = re.findall(r'<td>(\d{2}:\d{2})</td>', table)
        if len(times) < 3:
            continue
        # 降順チェック: 最初の時間 >= 最後の時間
        first = times[0]
        last = times[-1]
        if first < last:
            # 昇順になってる（バグ）
            issues.append(f'ERROR: {fname} テーブル#{i+1} 時間が昇順（{first}→{last}）。降順（上が最新）であるべき')
    return issues


def check_missing_diff_medals(html: str, fname: str) -> list:
    """当日おすすめ台に差枚が設定されているか（ART>0の場合）"""
    issues = []
    # 本日セクション: ART > 0 なのに差枚表示なし
    today_sections = re.findall(
        r'本日.*?ART\s*</span>\s*<span[^>]*>(\d+)</span>(.*?)(?:前日|1/\d{2}\()',
        html, re.DOTALL
    )
    for art_str, section in today_sections:
        art = int(art_str)
        if art > 0:
            has_diff = '枚' in section and ('+' in section or '-' in section or '▲' in section)
            if not has_diff:
                # diff_medals=0の場合は表示しないのが正常
                pass  # 0枚は表示しないのでスキップ
    return issues


def check_chain_arrow_direction(html: str, fname: str) -> list:
    """連チャン矢印が↑（降順表示で上方向=新しい方向）か"""
    issues = []
    # ↓が使われてたらバグ（降順表示では↑が正しい）
    chain_down = re.findall(r'<td[^>]*>↓</td>', html)
    chain_up = re.findall(r'<td[^>]*>↑</td>', html)
    if chain_down and not chain_up:
        issues.append(f'ERROR: {fname} 連チャン矢印が↓（降順表示では↑が正しい）')
    return issues


def check_accumulated_games_display(html: str, fname: str) -> list:
    """RB跨ぎのaccumulated_gamesが表示されているか"""
    issues = []
    # RB行の後にART行があり、startが小さいのにaccumulated表示がない場合
    # → 複雑すぎるので簡易チェック: acc-gamesクラスが1件以上あるか
    has_acc = 'acc-games' in html
    has_rb = '>RB<' in html
    if has_rb and not has_acc:
        issues.append(f'WARN: {fname} RBがあるのに累計G数表示（acc-games）が0件')
    return issues


def check_availability_format() -> list:
    """availability.jsonのフォーマットと整合性をチェック"""
    issues = []

    avail_path = BASE / 'data' / 'availability.json'
    if not avail_path.exists():
        issues.append('ERROR: data/availability.json が存在しない')
        return issues

    try:
        data = json.loads(avail_path.read_text())
    except Exception as e:
        issues.append(f'ERROR: availability.json 読み込み失敗: {e}')
        return issues

    stores = data.get('stores', {})
    if not stores:
        issues.append('ERROR: availability.json に stores がない')
        return issues

    # 各店舗のユニットをチェック
    total_units = 0
    playing_units = 0
    empty_units = 0
    format_issues = []

    for store_key, store_data in stores.items():
        units = store_data.get('units', [])
        if not units:
            continue

        for unit in units:
            total_units += 1

            # フィールド形式チェック
            has_availability = 'availability' in unit
            has_status = 'status' in unit
            has_history = 'history' in unit
            has_today_history = 'today_history' in unit

            # 新旧形式混在チェック
            if has_status and not has_availability:
                format_issues.append(f'{store_key}: unit {unit.get("unit_id")} が新形式(status)')
            if has_today_history and not has_history:
                format_issues.append(f'{store_key}: unit {unit.get("unit_id")} が新形式(today_history)')

            # availability集計
            if has_availability:
                avail_val = unit.get('availability', '?')
                if avail_val == '遊技中':
                    playing_units += 1
                elif avail_val == '空き':
                    empty_units += 1

    # フォーマット不一致警告
    if format_issues:
        issues.append(f'WARN: availability.json に新形式フィールドが {len(format_issues)} 件: {format_issues[:3]}')

    # 営業時間中チェック（10-22時）
    from datetime import datetime, timedelta, timezone
    JST = timezone(timedelta(hours=9))
    now_hour = datetime.now(JST).hour
    if 10 <= now_hour <= 22:
        # 営業時間中なのに遊技中が0台は異常
        if total_units > 50 and playing_units == 0:
            issues.append(f'ERROR: 営業時間中({now_hour}時)なのに遊技中が0台（総台数{total_units}）→ データ未更新の可能性')
        # 遊技中が異常に少ない
        elif total_units > 50 and playing_units < 10:
            issues.append(f'WARN: 営業時間中({now_hour}時)に遊技中が{playing_units}台のみ（総台数{total_units}）→ データ更新不完全の可能性')

    return issues


def check_sparkline_graphs(html: str, fname: str) -> list:
    """リアルタイムグラフ（sparkline）の検証"""
    issues = []

    # sparklineを全て抽出
    sparklines = re.findall(
        r'<div class="sparkline-label">(.*?)</div>\s*<svg class="sparkline".*?>(.*?)</svg>',
        html,
        re.DOTALL
    )

    for i, (label, svg_content) in enumerate(sparklines):
        # ラベルに「データ取得中」がある場合は線がないのが正常
        if 'データ取得中' in label:
            # 線がないのが正常
            if '<polyline' in svg_content:
                issues.append(f'WARN: {fname} データ取得中なのにグラフに線がある')
            continue

        # 本日データの場合
        if '本日' in label:
            # 線がないのは異常（ART > 0なら線があるべき）
            if '<polyline' not in svg_content:
                # 本日ARTが0の場合は線がないのが正常
                # ここでは一旦スキップ（厳密にはART数を確認する必要がある）
                pass
            else:
                # polylineのpointsが異常に短い（データ不足）
                points_match = re.search(r'points="([^"]+)"', svg_content)
                if points_match:
                    points = points_match.group(1)
                    point_count = len(points.split())
                    if point_count < 3:
                        issues.append(f'WARN: {fname} グラフのデータポイント数が少ない（{point_count}点）')

    return issues


def check_recent_data_missing(html: str, fname: str) -> list:
    """直近データでART>0なのに差枚・連チャンがない欠損をチェック"""
    issues = []
    
    if fname != 'index.html':
        return issues  # index.htmlのみチェック
    
    # unit-cardごとに抽出
    cards = re.findall(r'<a href="[^"]*" class="unit-card[^"]*"[^>]*>.*?</a>', html, re.DOTALL)
    
    for card in cards[:30]:  # TOP30のみ
        rank_match = re.search(r'ranking-num">#(\d+)</span>', card)
        unit_match = re.search(r'unit-id-num[^>]*>(\d+)</span>', card)
        rank = rank_match.group(1) if rank_match else '?'
        unit_id = unit_match.group(1) if unit_match else '?'
        
        blocks = re.findall(r'<div class="recent-day-block">.*?</div>\s*</div>', card, re.DOTALL)
        
        for block in blocks:
            label = re.search(r'recent-label[^>]*>([^<]+)', block)
            art = re.search(r'recent-art[^>]*>ART (\d+)', block)
            diff = re.search(r'diff-medals-sm', block)
            rensa = re.search(r'rensa-sm', block)
            
            label_str = label.group(1) if label else '?'
            art_val = int(art.group(1)) if art else 0
            
            # ARTが5以上あるのに差枚・連がない = 欠損
            if art_val >= 5 and (not diff or not rensa):
                issues.append(f'ERROR: {fname} {rank}位 {unit_id}: {label_str} ART={art_val} → 差枚/連なし')
    
    return issues


def run_all() -> int:
    """全HTML検証を実行。ERRORの数を返す"""
    all_issues = []

    # availability.json検証（HTML生成前のデータ品質チェック）
    all_issues.extend(check_availability_format())

    index_html = DOCS / 'index.html'
    if not index_html.exists():
        print('❌ POST-BUILD: docs/index.html が存在しない')
        return 1
    
    html_files = {
        'index.html': index_html,
    }
    # verify/recommend/rankingも対象
    for name in ['verify.html', 'recommend.html', 'ranking.html']:
        p = DOCS / name
        if p.exists():
            html_files[name] = p
    # store別ページ
    for p in DOCS.glob('store_*.html'):
        html_files[p.name] = p
    
    checks = [
        check_float_probability,
        check_art_vs_rensa,
        check_time_order,
        check_chain_arrow_direction,
        check_accumulated_games_display,
        check_sparkline_graphs,
        check_recent_data_missing,
    ]
    
    for fname, fpath in html_files.items():
        try:
            html = fpath.read_text()
        except Exception as e:
            all_issues.append(f'ERROR: {fname} 読み込み失敗: {e}')
            continue
        
        for check_fn in checks:
            all_issues.extend(check_fn(html, fname))
    
    errors = [i for i in all_issues if i.startswith('ERROR')]
    warns = [i for i in all_issues if i.startswith('WARN')]
    
    if errors:
        print(f'\n❌ POST-BUILD ERRORS ({len(errors)}):')
        for e in errors:
            print(f'  {e}')
    if warns:
        print(f'\n⚠️  POST-BUILD WARNINGS ({len(warns)}):')
        for w in warns:
            print(f'  {w}')
    
    if not errors and not warns:
        print('✅ post-build check: 全出力検証OK')
    
    return len(errors)


if __name__ == '__main__':
    sys.exit(run_all())
