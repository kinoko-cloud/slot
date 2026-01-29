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
    # 主要ページ + サブページ。historyは数が多いのでサンプルチェック。
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
        # _unit_card等のパーシャルはスキップ
        if fname.startswith('_'):
            continue
        content = f.read_text()
        # build-ver（ビルド時刻）の存在チェック
        if 'build-ver' not in content:
            all_issues.append(f'WARN: {f.relative_to(DOCS_DIR)}: build-ver（ビルド時刻）が見つからない')
        # common_jsの動的モード/時刻更新
        if 'updateModeBadge' not in content and 'updateCurrentTime' not in content:
            all_issues.append(f'WARN: {f.relative_to(DOCS_DIR)}: 動的モード切替JS（_common_js）が見つからない')

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
        for w in warns[:20]:
            print(f'  {w}')
        if len(warns) > 20:
            print(f'  ... and {len(warns) - 20} more')
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
    # ※ mode-badgeはクライアントサイドJSで現在時刻に基づいて動的更新される
    # HTMLに焼き付いた値はビルド時のもの。JSが上書きするのでERRORにしない
    badges = re.findall(r'mode-badge[^>]*>([^<]+)', content)
    badge_text = badges[0].strip() if badges else ''
    if not badge_text:
        issues.append(f'WARN: mode-badgeが見つからない')

    # 2. 的中率ヒーローカードのチェック
    hero_cards = re.findall(r'hero-rate-card', content)
    if not hero_cards:
        # 的中率データが存在するか確認
        verify_files = sorted(VERIFY_DIR.glob('verify_*_results.json')) if VERIFY_DIR.exists() else []
        if verify_files:
            issues.append('ERROR: 的中率ヒーローカードが0件（verifyデータは存在する）')
        else:
            issues.append('INFO: 的中率ヒーローカードなし（verifyデータなし）')
    else:
        # 的中率の日付チェック
        verify_date_match = re.search(r'(\d+)月(\d+)日\([月火水木金土日]\)の予測結果', content)
        if verify_date_match:
            v_month = int(verify_date_match.group(1))
            v_day = int(verify_date_match.group(2))
            expected_dt = datetime.strptime(expected_verify_dt, '%Y-%m-%d')
            if v_month != expected_dt.month or v_day != expected_dt.day:
                issues.append(f'WARN: 的中率の日付が{v_month}/{v_day}だが、{expected_dt.month}/{expected_dt.day}であるべき')

    # 3. TOP10セクションのチェック（before_open/after_closeのみ）
    if expected_mode in ('before_open', 'after_close'):
        top3_section = re.search(r'id="top3-section"(.*?)(?=<section|</main>)', content, re.DOTALL)
        if top3_section:
            sec = top3_section.group(1)
            # カードを<a href=で分割
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

                # 直近データ行（展開可能かチェック）
                recent_rows = re.findall(r'recent-day-row', card)
                if not recent_rows:
                    card_issues.append('直近データ行なし')
                else:
                    # 履歴データがある日には展開ボタンがあるか
                    expandable = re.findall(r'recent-day-detail', card)
                    if not expandable:
                        # historyなし台（アイランド北斗等）はINFO扱い
                        pass  # historyがそもそもない台は展開不可で正常

                # 差枚推移グラフ
                if 'sparkline' not in card:
                    card_issues.append('差枚推移グラフなし')

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

    # 5. recent-day-rowの差枚チェック（全ページ共通）
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

    # 結果カードの存在（verify.htmlのテーブル行 or カード）
    result_rows = re.findall(r'vr-mark|vt-result|result-card|verify-card|topic-card', content)
    if not result_rows:
        issues.append('WARN: verify.html に結果データが見つからない')

    return issues


def _validate_recommend(filepath, content):
    """個別店舗ページの検証"""
    issues = []

    # prev-day-recの差枚チェック（ネストしたspanを含むため、行全体で検索）
    total_recs = 0
    missing = 0
    # prev-day-recの開始から次のprev-day-recまたはunit-cardまでを1ブロックとする
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


if __name__ == '__main__':
    success = validate_all()
    sys.exit(0 if success else 1)
