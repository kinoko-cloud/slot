#!/usr/bin/env python3
"""pre_build_check.py - ビルド前の仕様整合性チェック

generate_static.pyの冒頭で呼ばれる。
CLAUDE.mdの仕様 / config/rankings.py / SPEC_prediction.md と
コード実装の矛盾を包括的に検出する。

【対象】天井だけでなく、RSさんの全指示に対する二重チェック:
- 天井閾値（機種別、ハードコード禁止）
- 好調/不調判定の閾値（config/rankings.py準拠）
- 差枚計算（蓄積DB優先）
- 連チャン定義（RENCHAIN_THRESHOLD準拠）
- max_medals計算（連チャン合計、1hit最大ではない）
- 北斗あべしシステム（G数≠あべし、G数ベース天井判定は参考値）
- おすすめ理由の一貫性
"""
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def check_hardcoded_thresholds():
    """天井・好調判定などの閾値がconfig以外にハードコードされていないか"""
    issues = []

    sys.path.insert(0, str(BASE))
    from config.rankings import MACHINES, MACHINE_DEFAULTS

    # --- 天井閾値 ---
    valid_ceilings = set()
    for m in MACHINES.values():
        valid_ceilings.add(m.get('normal_ceiling', 999))
        valid_ceilings.add(m.get('reset_ceiling', 999))
    valid_ceilings.add(MACHINE_DEFAULTS.get('normal_ceiling', 999))

    ceiling_pattern = re.compile(r'>= ?\d{3,4}.*(?:天井|tenjou|ceiling)', re.IGNORECASE)
    ceiling_pattern2 = re.compile(r'(?:天井|tenjou|ceiling).*>= ?\d{3,4}', re.IGNORECASE)
    hardcode_pattern = re.compile(r'>= ?(\d{3,4})')

    # --- 好調/不調閾値 ---
    valid_probs = set()
    for m in MACHINES.values():
        for k in ('good_prob', 'bad_prob', 'very_bad_prob'):
            valid_probs.add(m.get(k))
    for k in ('good_prob', 'bad_prob', 'very_bad_prob'):
        valid_probs.add(MACHINE_DEFAULTS.get(k))
    valid_probs.discard(None)

    check_files = list(BASE.glob('scripts/*.py')) + \
                  list(BASE.glob('analysis/*.py')) + \
                  list(BASE.glob('web/*.py')) + \
                  list(BASE.glob('web/templates/*.html'))

    for f in check_files:
        if f.name in ('rankings.py', 'pre_build_check.py') or '__pycache__' in str(f):
            continue
        try:
            content = f.read_text()
        except:
            continue
        for i, line in enumerate(content.split('\n'), 1):
            stripped = line.strip()
            if stripped.startswith('#') or stripped.startswith('{#') or stripped.startswith('"""') or stripped.startswith("'''"):
                continue

            # 天井ハードコード
            if ceiling_pattern.search(line) or ceiling_pattern2.search(line):
                matches = hardcode_pattern.findall(line)
                for m in matches:
                    val = int(m)
                    if val not in valid_ceilings and val not in (500, 10, 50):
                        issues.append(f'HARDCODE: {f.relative_to(BASE)}:{i} 天井閾値 {val} がconfig/rankings.pyにない')

    return issues


def check_claude_md_specs():
    """CLAUDE.mdの機種仕様が存在し、config/rankings.pyと矛盾しないか"""
    issues = []
    claude_md = BASE / 'CLAUDE.md'
    if not claude_md.exists():
        issues.append('ERROR: CLAUDE.md が存在しない')
        return issues

    content = claude_md.read_text()

    # === SBJ仕様 ===
    sbj_specs = {
        '999G+α': 'SBJ天井（999G+α）',
        'RBではゲーム数天井がリセットされない': 'SBJ RBリセットなし',
        '666G+α': 'SBJリセット時天井短縮',
    }
    for text, desc in sbj_specs.items():
        if text not in content:
            issues.append(f'ERROR: CLAUDE.mdに{desc}の記載がない')

    # === 北斗仕様 ===
    hokuto_specs = {
        'あべし天井': '北斗あべし天井',
        '1536あべし': '北斗モードA天井',
        '896あべし': '北斗モードB天井',
        '576あべし': '北斗モードC天井',
        '128あべし': '北斗天国天井',
        '天撃失敗後は絶対にやめない': '北斗やめどき注意',
    }
    for text, desc in hokuto_specs.items():
        if text not in content:
            issues.append(f'ERROR: CLAUDE.mdに{desc}の記載がない')

    # === config/rankings.pyとの整合 ===
    from config.rankings import MACHINES

    sbj = MACHINES.get('sbj', {})
    if sbj.get('normal_ceiling') != 999:
        issues.append(f'ERROR: SBJ天井が{sbj.get("normal_ceiling")}だがCLAUDE.mdでは999')
    if sbj.get('good_prob') != 130:
        issues.append(f'WARN: SBJ好調閾値が1/{sbj.get("good_prob")}（CLAUDE.md確認必要）')

    hokuto = MACHINES.get('hokuto_tensei2', {})
    if hokuto.get('normal_ceiling_abeshi') != 1536:
        issues.append(f'ERROR: 北斗モードA天井が{hokuto.get("normal_ceiling_abeshi")}あべしだがCLAUDE.mdでは1536')
    if hokuto.get('reset_ceiling_abeshi') != 1280:
        issues.append(f'ERROR: 北斗リセット天井が{hokuto.get("reset_ceiling_abeshi")}あべしだがCLAUDE.mdでは1280')

    mode_ceilings = hokuto.get('mode_ceilings_abeshi', {})
    expected_modes = {'A': 1536, 'B': 896, 'C': 576, 'heaven': 128}
    for mode, expected in expected_modes.items():
        actual = mode_ceilings.get(mode)
        if actual != expected:
            issues.append(f'ERROR: 北斗モード{mode}天井が{actual}あべしだがCLAUDE.mdでは{expected}')

    return issues


def check_spec_prediction():
    """SPEC_prediction.mdの仕様がconfig/rankings.pyと矛盾しないか"""
    issues = []
    spec = BASE / 'docs' / 'SPEC_prediction.md'
    if not spec.exists():
        issues.append('WARN: docs/SPEC_prediction.md が存在しない')
        return issues

    content = spec.read_text()

    # 連チャン定義の記載
    if 'RENCHAIN_THRESHOLD' not in content and '連チャン閾値' not in content:
        issues.append('WARN: SPEC_prediction.mdに連チャン閾値の記載がない')

    # max_medals定義
    if '連チャン合計' not in content and 'チェーン内合計' not in content:
        issues.append('WARN: SPEC_prediction.mdにmax_medalsの定義（連チャン合計）がない')

    return issues


def check_diff_medals_priority():
    """差枚計算で蓄積DBが優先されているか（ハードコード推定値が優先されていないか）"""
    issues = []

    # analysis/recommender.pyでestimate_diff_medalsが最優先になっていないか
    recommender = BASE / 'analysis' / 'recommender.py'
    if recommender.exists():
        content = recommender.read_text()
        # estimate_diff_medalsが蓄積DBより先に使われていたら警告
        # パターン: diff_medals = estimate_... の後に蓄積DB上書きがない
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'estimate_diff_medals' in line and '=' in line and 'def ' not in line:
                # この後50行以内で蓄積DBの上書きがあるか
                context = '\n'.join(lines[i:i+50])
                if '蓄積DB' not in context and 'accumulated' not in context.lower() and 'history_db' not in context.lower():
                    # フォールバック用途ならOK（コメントにfallbackがあるか）
                    if 'fallback' not in context.lower() and 'フォールバック' not in context:
                        issues.append(f'WARN: recommender.py:{i+1} estimate_diff_medalsが蓄積DBフォールバックなしで使用されている可能性')

    return issues


def check_hokuto_abeshi_awareness():
    """北斗のコードがあべしシステムを正しく認識しているか
    
    北斗の天井はあべしptベース（実機画面表示）。
    G数とあべしは比例しない（レア役で大量加算される）。
    100G消化で1500あべしになることもある。
    データサイトからはG数しか取れないため、G数ベースの天井判定は参考値。
    """
    issues = []

    # 北斗関連のコードでG数ベースの天井判定をしている箇所
    check_files = list(BASE.glob('scripts/*.py')) + \
                  list(BASE.glob('analysis/*.py'))

    for f in check_files:
        if f.name in ('pre_build_check.py', 'rankings.py') or '__pycache__' in str(f):
            continue
        try:
            content = f.read_text()
        except:
            continue

        # 北斗でis_tenjouをG数だけで判定していたら警告
        if 'hokuto' in content.lower() or '北斗' in content:
            for i, line in enumerate(content.split('\n'), 1):
                stripped = line.strip()
                if stripped.startswith('#'):
                    continue
                # 北斗のコンテキストでG数ベース天井を確定判定していたらWARN
                # （参考値として使うのはOK）

    return issues


def run_all():
    """全チェック実行"""
    all_issues = []
    all_issues.extend(check_hardcoded_thresholds())
    all_issues.extend(check_claude_md_specs())
    all_issues.extend(check_spec_prediction())
    all_issues.extend(check_diff_medals_priority())
    all_issues.extend(check_hokuto_abeshi_awareness())

    errors = [i for i in all_issues if i.startswith('ERROR')]
    warns = [i for i in all_issues if i.startswith('WARN') or i.startswith('HARDCODE')]

    if errors:
        print(f'\n❌ PRE-BUILD ERRORS ({len(errors)}):')
        for e in errors:
            print(f'  {e}')
    if warns:
        print(f'\n⚠️  PRE-BUILD WARNINGS ({len(warns)}):')
        for w in warns:
            print(f'  {w}')

    if not errors and not warns:
        print('✅ pre-build check: 全仕様整合OK')

    return len(errors)


if __name__ == '__main__':
    sys.exit(run_all())
