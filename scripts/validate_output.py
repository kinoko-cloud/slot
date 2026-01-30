#!/usr/bin/env python3
"""
ビルド後HTML包括検証スクリプト

generate_static.py の最後に呼ばれ、全HTMLの表示項目を検証する。
ERRORがあれば非ゼロ終了。

検証項目:
 1. 時間帯モード (before_open / realtime / after_close) の正しさ
 2. 的中率の日付が正しいか
 3. TOP10カードの必須項目（おすすめ理由、直近データ、差枚推移グラフ）
 4. 差枚/最大枚数/連チャンの表示漏れ
 5. 的中結果(verify)ページの日付・内容
 6. 各セクションの存在チェック
 7. 展開詳細テーブルに連チャン列（N連）があるか
 8. 天井判定がaccumulated_gamesベース（RB跨ぎ累計）か
 9. おすすめ理由が矛盾していないか
10. max_medalsが連チャン合計値として妥当か
11. 直近データの展開パネルにprocessedデータが使われているか
12. CSSファイルが存在し、キャッシュバスト付きか
13. CSS未使用クラス警告（残骸蓄積防止）
"""
import re
import sys
import json
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = PROJECT_ROOT / 'docs'
HISTORY_DIR = PROJECT_ROOT / 'data' / 'history'
VERIFY_DIR = PROJECT_ROOT / 'data' / 'verify'
CSS_PATH = PROJECT_ROOT / 'web' / 'static' / 'style.css'
TEMPLATES_DIR = PROJECT_ROOT / 'web' / 'templates'

JST_OFFSET = 9  # JST = UTC+9

WEEKDAY_NAMES = ['月', '火', '水', '木', '金', '土', '日']


def _now_jst():
    """現在のJST時刻"""
    from datetime import timezone
    return datetime.now(timezone(timedelta(hours=9)))


def _expected_display_mode(now=None):
    """期待されるdisplay_mode"""
    if now is None:
        now = _now_jst()
    h = now.hour
    m = now.minute
    if h < 10:
        return 'before_open'
    elif h >= 23 or (h == 22 and m >= 50):
        return 'after_close'
    else:
        return 'realtime'


def _expected_verify_date(now=None):
    """的中率で使うべき日付 (YYYY-MM-DD)"""
    if now is None:
        now = _now_jst()
    if now.hour >= 23:
        # 23:00以降 → 今日の結果
        return now.strftime('%Y-%m-%d')
    else:
        # 0:00-22:59 → 昨日の結果
        return (now - timedelta(days=1)).strftime('%Y-%m-%d')


def _expected_recommend_date(now=None):
    """おすすめ台の対象日"""
    if now is None:
        now = _now_jst()
    if now.hour >= 22:
        return (now + timedelta(days=1)).strftime('%Y-%m-%d')
    elif now.hour < 10:
        return now.strftime('%Y-%m-%d')
    else:
        return now.strftime('%Y-%m-%d')


def validate_all():
    """全HTMLを検証"""
    if not DOCS_DIR.exists():
        print('ERROR: docs/ ディレクトリが存在しない')
        return False

    now = _now_jst()
    expected_mode = _expected_display_mode(now)
    expected_verify_dt = _expected_verify_date(now)
    expected_rec_dt = _expected_recommend_date(now)

    all_issues = []

    # === index.html ===
    index_path = DOCS_DIR / 'index.html'
    if index_path.exists():
        content = index_path.read_text()
        all_issues.extend(_validate_index(content, expected_mode, expected_verify_dt, expected_rec_dt, now))
    else:
        all_issues.append('ERROR: index.html が存在しない')

    # === verify.html ===
    verify_path = DOCS_DIR / 'verify.html'
    if verify_path.exists():
        vcontent = verify_path.read_text()
        all_issues.extend(_validate_verify(vcontent, expected_verify_dt, now))
    else:
        all_issues.append('WARN: verify.html が存在しない')

    # === recommend pages ===
    for f in sorted(DOCS_DIR.glob('recommend/*.html')) if (DOCS_DIR / 'recommend').exists() else sorted(DOCS_DIR.glob('recommend_*.html')):
        rcontent = f.read_text()
        all_issues.extend(_validate_recommend(f, rcontent))

    # === 全ページ共通要素チェック（トップバー統一規格） ===
    all_html_files = list(DOCS_DIR.glob('*.html'))
    if (DOCS_DIR / 'recommend').exists():
        all_html_files.extend(DOCS_DIR.glob('recommend/*.html'))
    if (DOCS_DIR / 'ranking').exists():
        all_html_files.extend(DOCS_DIR.glob('ranking/*.html'))
    if (DOCS_DIR / 'machine').exists():
        all_html_files.extend(DOCS_DIR.glob('machine/*.html'))
    # historyはサンプル3件のみ（数百ファイルあるため）
    if (DOCS_DIR / 'history').exists():
        history_files = sorted(DOCS_DIR.glob('history/*.html'))
        all_html_files.extend(history_files[:3])

    for f in all_html_files:
        fname = f.name
        if fname.startswith('_'):
            continue
        content = f.read_text()
        # build-ver（ビルド時刻）の存在チェック
        if 'build-ver' not in content:
            all_issues.append(f'WARN: {f.relative_to(DOCS_DIR)}: build-ver（ビルド時刻）が見つからない')
        # common_jsの動的モード/時刻更新
        if 'updateModeBadge' not in content and 'updateCurrentTime' not in content:
            all_issues.append(f'WARN: {f.relative_to(DOCS_DIR)}: 動的モード切替JS（_common_js）が見つからない')

    # === 全ページ: CSS キャッシュバスト付きチェック ===
    all_issues.extend(_validate_css_cache_bust())

    # === リアルタイム機能の健全性チェック ===
    all_issues.extend(_validate_realtime())

    # === 過去指摘統合チェック ===
    all_issues.extend(_validate_past_feedback_checks())

    # === CSS未使用クラス警告 ===
    all_issues.extend(_validate_css_unused_classes())

    # 結果表示
    errors = [i for i in all_issues if i.startswith('ERROR')]
    warns = [i for i in all_issues if i.startswith('WARN')]
    infos = [i for i in all_issues if i.startswith('INFO')]

    if errors:
        print(f'\n❌ ERRORS ({len(errors)}):')
        for e in errors:
            print(f'  {e}')
    if warns:
        print(f'\n⚠️  WARNINGS ({len(warns)}):')
        for w in warns[:30]:
            print(f'  {w}')
        if len(warns) > 30:
            print(f'  ... and {len(warns) - 30} more')
    if infos:
        print(f'\nℹ️  INFO ({len(infos)}):')
        for i in infos[:10]:
            print(f'  {i}')
        if len(infos) > 10:
            print(f'  ... and {len(infos) - 10} more')

    if not all_issues:
        print('✅ 全検証パス')

    return len(errors) == 0


def _validate_index(content, expected_mode, expected_verify_dt, expected_rec_dt, now):
    """index.htmlの検証"""
    issues = []

    # 1. 時間帯モードチェック
    badges = re.findall(r'mode-badge[^>]*>([^<]+)', content)
    badge_text = badges[0].strip() if badges else ''
    if not badge_text:
        issues.append(f'WARN: mode-badgeが見つからない')

    # 2. 的中率ヒーローカードのチェック
    hero_cards = re.findall(r'hero-rate-card', content)
    if not hero_cards:
        verify_files = sorted(VERIFY_DIR.glob('verify_*_results.json')) if VERIFY_DIR.exists() else []
        if verify_files:
            issues.append('ERROR: 的中率ヒーローカードが0件（verifyデータは存在する）')
        else:
            issues.append('INFO: 的中率ヒーローカードなし（verifyデータなし）')
    else:
        # 的中率の日付チェック（強化版）
        verify_date_match = re.search(r'(\d+)月(\d+)日\([月火水木金土日]\)の予測結果', content)
        if verify_date_match:
            v_month = int(verify_date_match.group(1))
            v_day = int(verify_date_match.group(2))
            expected_dt = datetime.strptime(expected_verify_dt, '%Y-%m-%d')
            if v_month != expected_dt.month or v_day != expected_dt.day:
                issues.append(f'WARN: 的中率の日付が{v_month}/{v_day}だが、{expected_dt.month}/{expected_dt.day}であるべき')
        else:
            # 他の的中率日付パターンもチェック
            alt_date = re.search(r'(\d+)/(\d+)\([月火水木金土日]\)の(?:予測結果|的中結果)', content)
            if alt_date:
                v_month = int(alt_date.group(1))
                v_day = int(alt_date.group(2))
                expected_dt = datetime.strptime(expected_verify_dt, '%Y-%m-%d')
                if v_month != expected_dt.month or v_day != expected_dt.day:
                    issues.append(f'WARN: 的中率の日付が{v_month}/{v_day}だが、{expected_dt.month}/{expected_dt.day}であるべき')

    # 3. TOP10セクションのチェック（before_open/after_closeのみ）
    if expected_mode in ('before_open', 'after_close'):
        top3_section = re.search(r'id="top3-section"(.*?)(?=<section|</main>)', content, re.DOTALL)
        if top3_section:
            sec = top3_section.group(1)
            card_blocks = re.split(r'<a href=', sec)[1:]
            top10_blocks = card_blocks[:10]

            if not top10_blocks:
                issues.append('ERROR: TOP10セクションにカードが0件')

            for i, card in enumerate(top10_blocks):
                uid_m = re.search(r'unit-id-num-lg[^>]*>(\d+)', card)
                uid_str = uid_m.group(1) if uid_m else f'#{i+1}'
                card_issues = []

                # おすすめ理由
                if 'reason-line' not in card:
                    card_issues.append('おすすめ理由なし')

                # 直近データ行
                recent_rows = re.findall(r'recent-day-row', card)
                if not recent_rows:
                    card_issues.append('直近データ行なし')
                else:
                    expandable = re.findall(r'recent-day-detail', card)
                    if not expandable:
                        pass  # historyがそもそもない台は展開不可で正常

                # 差枚推移グラフ
                if 'sparkline' not in card:
                    card_issues.append('差枚推移グラフなし')
                else:
                    # グラフ日付が最新（前日）であることを確認
                    graph_date_m = re.search(r'(\d{4}-\d{2}-\d{2})\s*差枚推移', card)
                    if graph_date_m:
                        graph_date = graph_date_m.group(1)
                        expected_graph_date = expected_verify_dt.strftime('%Y-%m-%d') if expected_verify_dt else None
                        if expected_graph_date and graph_date < expected_graph_date:
                            card_issues.append(f'グラフ日付が古い({graph_date}、期待={expected_graph_date})')

                # 差枚表示
                if recent_rows and 'diff-medals-sm' not in card:
                    card_issues.append('差枚なし')

                if card_issues:
                    issues.append(f'WARN: TOP10 カード{uid_str}: {", ".join(card_issues)}')
        else:
            issues.append('ERROR: TOP10セクションが見つからない')

    # 4. 前日爆発台セクション（before_open/after_closeのみ）
    if expected_mode in ('before_open', 'after_close'):
        if 'explosion-section' not in content and 'yesterday-card' not in content:
            issues.append('WARN: 前日爆発台セクションが見つからない')

    # 5. recent-day-rowの差枚チェック
    total_rows = 0
    missing_diff = 0
    for m in re.finditer(r'<div class="recent-day-row">(.*?)</div>', content, re.DOTALL):
        line = m.group(1)
        art_m = re.search(r'ART (\d+)', line)
        if not art_m or int(art_m.group(1)) < 5:
            continue
        total_rows += 1
        if 'diff-medals-sm' not in line:
            missing_diff += 1
    if missing_diff > 0 and total_rows > 0:
        pct = missing_diff / total_rows * 100
        if pct > 50:
            issues.append(f'WARN: index.html 差枚なし行: {missing_diff}/{total_rows} ({pct:.0f}%)')
        else:
            issues.append(f'INFO: index.html 差枚なし行: {missing_diff}/{total_rows} ({pct:.0f}%, データなしの可能性)')

    return issues


def _validate_verify(content, expected_verify_dt, now):
    """verify.htmlの検証"""
    issues = []

    expected_dt = datetime.strptime(expected_verify_dt, '%Y-%m-%d')
    expected_str = f'{expected_dt.month}/{expected_dt.day}'

    # 日付チェック
    date_matches = re.findall(r'(\d+)/(\d+)\([月火水木金土日]\)の(?:予測結果|的中結果)', content)
    if date_matches:
        found_expected = False
        for m, d in date_matches:
            if int(m) == expected_dt.month and int(d) == expected_dt.day:
                found_expected = True
        if not found_expected:
            actual = f'{date_matches[0][0]}/{date_matches[0][1]}'
            issues.append(f'WARN: verify.html の日付が{actual}だが、{expected_str}であるべき')
    else:
        issues.append('INFO: verify.html に日付表示なし')

    # 結果カードの存在
    result_rows = re.findall(r'vr-mark|vt-result|result-card|verify-card|topic-card', content)
    if not result_rows:
        issues.append('WARN: verify.html に結果データが見つからない')

    return issues


def _validate_recommend(filepath, content):
    """個別店舗ページの検証"""
    issues = []

    # prev-day-recの差枚チェック
    total_recs = 0
    missing = 0
    blocks = re.split(r'(?=<span class="prev-day-rec)', content)
    for block in blocks:
        if not block.startswith('<span class="prev-day-rec'):
            continue
        art_m = re.search(r'ART (\d+)', block[:500])
        if not art_m or int(art_m.group(1)) < 5:
            continue
        total_recs += 1
        if 'diff-medals-sm' not in block[:500]:
            missing += 1

    if missing > 0 and total_recs > 0:
        pct = missing / total_recs * 100
        if pct > 50:
            issues.append(f'WARN: {filepath.name} 差枚なし: {missing}/{total_recs} ({pct:.0f}%)')

    return issues


def _validate_realtime():
    """リアルタイム機能の健全性チェック"""
    issues = []

    # 1. availability.jsonの存在と鮮度
    avail_path = PROJECT_ROOT / 'data' / 'availability.json'
    if not avail_path.exists():
        issues.append('WARN: data/availability.json が存在しない')
    else:
        try:
            with open(avail_path) as f:
                avail_data = json.load(f)

            fetched_at = avail_data.get('fetched_at', '')
            if fetched_at:
                from datetime import timezone
                fetch_time = datetime.fromisoformat(fetched_at)
                now = datetime.now(timezone(timedelta(hours=9)))
                age_hours = (now - fetch_time).total_seconds() / 3600
                if age_hours > 24:
                    issues.append(f'WARN: availability.json が{age_hours:.0f}時間前のデータ（24h超）')
                elif age_hours > 1:
                    issues.append(f'INFO: availability.json が{age_hours:.1f}時間前のデータ')

            # 全9キーの存在チェック
            expected_keys = [
                'shibuya_espass_sbj', 'shinjuku_espass_sbj', 'akiba_espass_sbj',
                'seibu_shinjuku_espass_sbj', 'island_akihabara_sbj',
                'shibuya_espass_hokuto', 'shinjuku_espass_hokuto', 'akiba_espass_hokuto',
                'island_akihabara_hokuto',
            ]
            stores = avail_data.get('stores', {})
            missing_keys = [k for k in expected_keys if k not in stores]
            if missing_keys:
                issues.append(f'WARN: availability.json に不足キー: {missing_keys}')

            # 各店舗のunitsが空でないか
            empty_stores = []
            for k in expected_keys:
                store_data = stores.get(k, {})
                if not store_data.get('units'):
                    empty_stores.append(k)
            if empty_stores:
                issues.append(f'INFO: availability.json units空: {empty_stores}')

        except Exception as e:
            issues.append(f'WARN: availability.json パースエラー: {e}')

    # 2. realtime.jsがdocs/static/に含まれるか
    rt_js_path = DOCS_DIR / 'static' / 'realtime.js'
    if not rt_js_path.exists():
        issues.append('ERROR: docs/static/realtime.js が存在しない（リアルタイム更新不可）')

    # 3. recommend/*.htmlにdata-store-key属性があるか
    rec_dir = DOCS_DIR / 'recommend'
    if rec_dir.exists():
        for f in rec_dir.glob('*.html'):
            content = f.read_text()
            if 'data-store-key' not in content:
                issues.append(f'WARN: {f.name} に data-store-key 属性がない（リアルタイム更新不可）')

    return issues


# =====================================================
# 過去指摘統合チェック（RSさんの全指摘をここに集約）
# =====================================================

def _validate_past_feedback_checks():
    """過去の全指摘事項を自動チェックとして統合"""
    issues = []

    # --- チェック対象ファイル収集 ---
    target_files = {}
    index_path = DOCS_DIR / 'index.html'
    if index_path.exists():
        target_files['index'] = index_path.read_text()
    verify_path = DOCS_DIR / 'verify.html'
    if verify_path.exists():
        target_files['verify'] = verify_path.read_text()
    rec_dir = DOCS_DIR / 'recommend'
    if rec_dir.exists():
        for f in rec_dir.glob('*.html'):
            target_files[f'recommend/{f.name}'] = f.read_text()

    # --- 1. 展開詳細テーブルに連チャン列（N連）があるか ---
    issues.extend(_check_chain_column(target_files))

    # --- 2. 天井判定がaccumulated_gamesベース（RB跨ぎ累計）か ---
    issues.extend(_check_ceiling_accumulated(target_files))

    # --- 3. おすすめ理由が矛盾していないか ---
    issues.extend(_check_reason_contradiction(target_files))

    # --- 4. max_medalsが連チャン合計値として妥当か ---
    issues.extend(_check_max_medals_sanity(target_files))

    # --- 5. 直近データ展開パネルにprocessedデータ（連チャン情報）があるか ---
    issues.extend(_check_expanded_detail_processed(target_files))

    return issues


def _check_chain_column(target_files):
    """展開詳細テーブルに連チャン列（N連）があるか"""
    issues = []

    for name, content in target_files.items():
        # hit-detail-table / vd-history-table を検出
        tables = re.findall(
            r'<table[^>]*class="[^"]*(?:hit-detail-table|vd-history-table)[^"]*"[^>]*>(.*?)</table>',
            content, re.DOTALL
        )
        if not tables:
            continue

        tables_with_chain = 0
        tables_without_chain = 0
        for table in tables:
            # ヘッダに「連」列があるか
            if re.search(r'<th[^>]*>連</th>', table):
                tables_with_chain += 1
            else:
                tables_without_chain += 1

        if tables_without_chain > 0 and tables_with_chain == 0:
            issues.append(f'ERROR: {name}: 展開詳細テーブル{tables_without_chain}件に連チャン列（連）がない')
        elif tables_without_chain > 0:
            issues.append(f'WARN: {name}: 展開詳細テーブルの一部（{tables_without_chain}/{tables_with_chain + tables_without_chain}）に連チャン列がない')

    return issues


def _check_ceiling_accumulated(target_files):
    """天井判定がaccumulated_gamesベース（RB跨ぎ累計）か
    天井行（class=tenjou）に acc-games / 累計 が存在するかチェック"""
    issues = []

    for name, content in target_files.items():
        # 天井行を検出
        tenjou_rows = re.findall(r'<tr[^>]*class="[^"]*tenjou[^"]*"[^>]*>(.*?)</tr>', content, re.DOTALL)
        if not tenjou_rows:
            continue

        rows_with_acc = 0
        rows_without_acc = 0
        for row in tenjou_rows:
            if 'acc-games' in row or '累計' in row:
                rows_with_acc += 1
            else:
                rows_without_acc += 1

        if rows_without_acc > 0 and rows_with_acc == 0:
            issues.append(f'WARN: {name}: 天井行{rows_without_acc}件にRB跨ぎ累計G数（acc-games/累計）がない')
        elif rows_without_acc > 0:
            pct = rows_without_acc / (rows_with_acc + rows_without_acc) * 100
            if pct > 30:
                issues.append(f'INFO: {name}: 天井行の{pct:.0f}%にRB跨ぎ累計なし（{rows_without_acc}/{rows_with_acc + rows_without_acc}）')

    return issues


def _check_reason_contradiction(target_files):
    """おすすめ理由が矛盾していないか
    「連続好調」と「不調」系が同一カード内で共存していたら異常
    
    ただし以下は矛盾ではない（意図的なロジック）:
    - 「X日連続不調 → 設定変更の可能性」= 過去が悪いから今日変わる期待（ポジティブ文脈）
    - 「好調台(1/XX)が途中放棄」= 今日の観察結果
    - 「前日は…不調」= 過去の状況説明
    - 「ほぼ毎日好調になる台」= 長期傾向
    """
    issues = []

    # 真にポジティブな理由（現在or予測が好調）
    positive_patterns = ['連続好調', '好調継続', '日連続プラス']
    # 真にネガティブな理由（予測としてネガティブ）
    negative_patterns = ['要注意台', '低設定が入りやすい']

    # 以下は文脈的に矛盾しないので除外:
    # - 「X日連続不調 → 設定変更の可能性」: 不調→改善期待（ポジティブ文脈）
    # - 「好調台(1/XX)が途中放棄」: 今日の実データ
    # - 「前日は…不調」: 過去データ説明
    # - 「やや不調」: 前日参考情報

    for name, content in target_files.items():
        if 'recommend/' not in name and name != 'index':
            continue

        card_blocks = re.split(r'(?=<(?:div|details|a)[^>]*class="[^"]*(?:unit-card|v2-reason|sa-rec-card)[^"]*")', content)

        for block in card_blocks:
            if len(block) < 50:
                continue

            reason_texts = re.findall(r'reason-line[^>]*>([^<]+)', block)
            reason_texts += re.findall(r'v2-reason[^>]*>([^<]+)', block)
            reason_texts += re.findall(r'sa-rec-reason[^>]*>([^<]+)', block)
            all_reasons = ' '.join(reason_texts)

            has_positive = any(p in all_reasons for p in positive_patterns)
            has_negative = any(p in all_reasons for p in negative_patterns)

            if has_positive and has_negative:
                uid_m = re.search(r'(?:unit-id-num-lg|v2-unit-id|sa-rec-unit)[^>]*>(\d+)', block)
                uid = uid_m.group(1) if uid_m else '?'
                issues.append(f'ERROR: {name}: 台{uid} おすすめ理由が矛盾（好調+不調が同居）: {all_reasons[:80]}')

    return issues


def _check_max_medals_sanity(target_files):
    """max_medalsが連チャン合計値として妥当か
    1hitの最大値（<1000枚）が50連以上で表示されていたら異常
    → max_medalsが表示されていて、かつテーブル内の個別hit枚数が全て<1000枚なのに
       max_medals値が1000未満で最大連チャン50+と表示されていたら整合性エラー"""
    issues = []

    for name, content in target_files.items():
        # max_medals値を検出（パターン: 最大XXX枚, max-medals-sm, max-medals-top等）
        max_medals_matches = re.findall(r'最大(\d[\d,]*)枚', content)
        rensa_matches = re.findall(r'(\d+)連', content)

        if not max_medals_matches or not rensa_matches:
            continue

        # max_medalsの最大値と最大連チャン数をチェック
        for mm_str in max_medals_matches:
            mm_val = int(mm_str.replace(',', ''))
            # 50連以上が存在するのにmax_medalsが1000未満 → 旧ロジック（1hit最大値）の可能性
            if mm_val < 1000:
                # この近辺に50連以上の記述がないかチェック（同一カード内）
                # 粒度が粗いので INFO レベル
                pass  # 個別のmax_medalsは正常に1000未満のケースも多い

        # ただし全体的な傾向として、蓄積DBの max_medals が1hit値のままでないか、
        # 任意のカードで max_medals < 500 かつ rensa > 20 があれば警告
        # これは _calc_history_stats() のバグ再発検知
        card_blocks = re.split(r'(?=<(?:div|details|a)[^>]*class="[^"]*(?:unit-card|yesterday-card|v2)[^"]*")', content)
        for block in card_blocks:
            if len(block) < 50:
                continue
            mm_in_block = re.findall(r'最大(\d[\d,]*)枚', block)
            rensa_in_block = re.findall(r'(\d+)連', block)
            if not mm_in_block or not rensa_in_block:
                continue
            max_mm = max(int(v.replace(',', '')) for v in mm_in_block)
            max_rensa = max(int(v) for v in rensa_in_block)

            if max_mm < 500 and max_rensa >= 20:
                uid_m = re.search(r'(?:unit-id-num-lg|v2-unit-id|unit-id-sm)[^>]*>(\d+)', block)
                uid = uid_m.group(1) if uid_m else '?'
                issues.append(f'WARN: {name}: 台{uid} max_medals={max_mm}枚なのに{max_rensa}連チャン → 1hit最大値のバグの可能性')

    return issues


def _check_expanded_detail_processed(target_files):
    """直近データの展開パネルにprocessedデータ（chain_pos/連）が使われているか
    hit-detail-table内に chain_pos / 連 の列が存在するかチェック"""
    issues = []

    for name, content in target_files.items():
        if name == 'verify':
            continue  # verifyは別チェック

        # 展開パネル（hit-detail-panel or recent-day-detail）を検出
        panels = re.findall(
            r'<div[^>]*class="[^"]*(?:hit-detail-panel|recent-day-detail)[^"]*"[^>]*>(.*?)</div>',
            content, re.DOTALL
        )
        if not panels:
            continue

        panels_with_chain = 0
        panels_without_chain = 0
        for panel in panels:
            if '<th' in panel:  # テーブルがある
                if re.search(r'<th[^>]*>連</th>', panel) or 'chain' in panel:
                    panels_with_chain += 1
                else:
                    panels_without_chain += 1

        if panels_without_chain > 0 and panels_with_chain == 0:
            issues.append(f'WARN: {name}: 展開パネル{panels_without_chain}件に連チャン情報（連/chain_pos）がない → rawデータが使われている可能性')
        elif panels_without_chain > 0 and (panels_with_chain + panels_without_chain) > 3:
            pct = panels_without_chain / (panels_with_chain + panels_without_chain) * 100
            if pct > 30:
                issues.append(f'INFO: {name}: 展開パネルの{pct:.0f}%に連チャン列なし')

    return issues


# =====================================================
# CSS検証
# =====================================================

def _validate_css_cache_bust():
    """CSSファイルが存在し、キャッシュバスト付きか"""
    issues = []

    # docs/static/style.css の存在
    css_docs = DOCS_DIR / 'static' / 'style.css'
    if not css_docs.exists():
        issues.append('ERROR: docs/static/style.css が存在しない')
        return issues

    # 主要HTML（index, recommend, verify）でstyle.css?v=が使われているか
    for html_file in [DOCS_DIR / 'index.html', DOCS_DIR / 'verify.html']:
        if not html_file.exists():
            continue
        content = html_file.read_text()
        if 'style.css?v=' not in content:
            issues.append(f'WARN: {html_file.name}: style.cssにキャッシュバスト（?v=）がない')

    rec_dir = DOCS_DIR / 'recommend'
    if rec_dir.exists():
        for f in rec_dir.glob('*.html'):
            content = f.read_text()
            if 'style.css?v=' not in content:
                issues.append(f'WARN: recommend/{f.name}: style.cssにキャッシュバスト（?v=）がない')
                break  # 1件あれば十分

    return issues


def _validate_css_unused_classes():
    """CSSで定義されているがHTMLで使われていないクラスを警告
    今後の残骸蓄積を防止するための監視チェック"""
    issues = []

    if not CSS_PATH.exists():
        return issues

    css_content = CSS_PATH.read_text()

    # CSSクラスセレクタを抽出
    skip_words = {
        'hover', 'active', 'focus', 'first', 'last', 'nth', 'webkit', 'moz',
        'child', 'even', 'odd', 'before', 'after', 'not', 'root', 'empty',
        'checked', 'disabled', 'placeholder', 'selection', 'marker',
    }
    css_classes = set()
    for m in re.finditer(r'\.([a-zA-Z_][\w-]*)', css_content):
        cls = m.group(1)
        if cls.lower() not in skip_words:
            css_classes.add(cls)

    # テンプレート + JS + 生成済みdocsからクラスを収集
    html_classes = set()

    # テンプレート（Jinja含む）
    for f in TEMPLATES_DIR.glob('*.html'):
        content = f.read_text()
        for m in re.finditer(r'class="([^"]+)"', content):
            val = m.group(1)
            # Jinja式を除去して静的部分を取得
            val_clean = re.sub(r'\{\{.*?\}\}', 'X', val)
            val_clean = re.sub(r'\{%.*?%\}', '', val_clean)
            for c in val_clean.split():
                c = c.strip().strip('-')
                if c and re.match(r'^[a-zA-Z]', c):
                    html_classes.add(c)

        # Jinja動的クラス（rank-{{ }}, setting-{{ }}, verdict-{{ }} 等）
        for m in re.finditer(r'([\w-]+)-\{\{', content):
            prefix = m.group(1)
            known_suffixes = {
                'rank': ['s', 'a', 'b', 'c', 'd'],
                'setting': [str(i) for i in range(7)],
                'verdict': ['perfect', 'hit', 'miss', 'surprise', 'neutral', 'nodata'],
                'chain': ['2', '3', '5', '8'],
                'medals': ['10k', '5k', '3k', '2k', '1k'],
            }
            if prefix in known_suffixes:
                for s in known_suffixes[prefix]:
                    html_classes.add(f'{prefix}-{s}')

    # JS
    js_path = PROJECT_ROOT / 'web' / 'static' / 'realtime.js'
    if js_path.exists():
        js_content = js_path.read_text()
        for m in re.finditer(r'classList\.(?:add|remove|toggle|contains)\(["\']([^"\']+)', js_content):
            html_classes.add(m.group(1))
        for m in re.finditer(r'class="([^"]+)"', js_content):
            for c in m.group(1).split():
                html_classes.add(c.strip())

    # docs/ サンプル
    sample_html_files = list((DOCS_DIR).glob('*.html'))
    rec_dir = DOCS_DIR / 'recommend'
    if rec_dir.exists():
        sample_html_files.extend(list(rec_dir.glob('*.html'))[:3])
    history_dir = DOCS_DIR / 'history'
    if history_dir.exists():
        sample_html_files.extend(list(history_dir.glob('*.html'))[:3])

    for f in sample_html_files:
        try:
            content = f.read_text()
            for m in re.finditer(r'class="([^"]+)"', content):
                for c in m.group(1).split():
                    html_classes.add(c.strip())
        except Exception:
            pass

    # 一般的に動的に付与されるクラス名（JS/CSS状態クラス）はスキップ
    dynamic_classes = {
        'hidden', 'open', 'active', 'expanded', 'loading', 'success', 'error',
        'stale', 'warning', 'available', 'playing', 'closed', 'running',
        'tentative', 'hot', 'deep', 'shallow', 'rensa', 'past', 'today',
        'good', 'bad', 'ok', 'excellent', 'normal', 'weak', 'best', 'dim',
        'highlight', 'plus', 'minus', 'small', 'compact',
        'big', 'medium', 'at', 'bb', 'art', 'rb', 'reg', 'tenjou',
        'no-data', 'has-detail', 'improving', 'flat',
        'high', 'mid', 'low', 'cold', 'cool', 'ice', 'warm', 'hot',
        'nodata', 'miss', 'hit', 'neutral', 'missed',
        'rainbow',
    }
    html_classes.update(dynamic_classes)

    unused = css_classes - html_classes
    if len(unused) > 50:
        issues.append(f'INFO: CSS未使用クラスが{len(unused)}件（残骸蓄積注意。主要なもの: {", ".join(sorted(unused)[:10])}...）')
    elif len(unused) > 20:
        issues.append(f'INFO: CSS未使用クラスが{len(unused)}件: {", ".join(sorted(unused)[:15])}...')

    return issues


if __name__ == '__main__':
    success = validate_all()
    sys.exit(0 if success else 1)
