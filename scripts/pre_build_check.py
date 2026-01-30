#!/usr/bin/env python3
"""pre_build_check.py - ビルド前の仕様整合性チェック

generate_static.pyの冒頭で呼ばれる。
CLAUDE.mdの仕様とコード内のハードコード値の矛盾を検出。
"""
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

def check_hardcoded_thresholds():
    """天井閾値などがconfig/rankings.py以外にハードコードされていないか"""
    issues = []
    
    # config/rankings.pyから正規の値を取得
    sys.path.insert(0, str(BASE))
    from config.rankings import MACHINES, MACHINE_DEFAULTS
    
    valid_ceilings = set()
    for m in MACHINES.values():
        valid_ceilings.add(m.get('normal_ceiling', 999))
        valid_ceilings.add(m.get('reset_ceiling', 999))
    valid_ceilings.add(MACHINE_DEFAULTS.get('normal_ceiling', 999))
    
    # 天井閾値のハードコードパターン（>= NNN で天井判定してる箇所）
    ceiling_pattern = re.compile(r'>= ?\d{3,4}.*(?:天井|tenjou|ceiling)', re.IGNORECASE)
    ceiling_pattern2 = re.compile(r'(?:天井|tenjou|ceiling).*>= ?\d{3,4}', re.IGNORECASE)
    hardcode_pattern = re.compile(r'>= ?(\d{3,4})')
    
    # チェック対象ファイル（config/rankings.py自体は除外）
    check_files = list(BASE.glob('scripts/*.py')) + \
                  list(BASE.glob('analysis/*.py')) + \
                  list(BASE.glob('web/*.py')) + \
                  list(BASE.glob('web/templates/*.html'))
    
    for f in check_files:
        if 'rankings.py' in f.name or '__pycache__' in str(f):
            continue
        try:
            content = f.read_text()
        except:
            continue
        for i, line in enumerate(content.split('\n'), 1):
            # コメント行は許可（説明用）
            stripped = line.strip()
            if stripped.startswith('#') or stripped.startswith('{#'):
                continue
            # >= NNN のパターンで天井関連の文脈
            if ceiling_pattern.search(line) or ceiling_pattern2.search(line):
                matches = hardcode_pattern.findall(line)
                for m in matches:
                    val = int(m)
                    if val not in valid_ceilings and val not in (500, 10, 50):  # deep/shallowは別
                        issues.append(f'HARDCODE: {f.relative_to(BASE)}:{i} 天井閾値 {val} がconfig/rankings.pyにない')
    
    return issues


def check_claude_md_specs():
    """CLAUDE.mdの機種仕様セクションが存在し、コードと矛盾しないか"""
    issues = []
    claude_md = BASE / 'CLAUDE.md'
    if not claude_md.exists():
        issues.append('ERROR: CLAUDE.md が存在しない')
        return issues
    
    content = claude_md.read_text()
    
    # SBJ仕様が存在するか
    if '999G+α' not in content:
        issues.append('ERROR: CLAUDE.mdにSBJ天井（999G+α）の記載がない')
    if 'RBではゲーム数天井がリセットされない' not in content:
        issues.append('ERROR: CLAUDE.mdにSBJ RBリセットなしの記載がない')
    
    # 北斗仕様が存在するか
    if 'あべし天井' not in content:
        issues.append('ERROR: CLAUDE.mdに北斗あべし天井の記載がない')
    
    # config/rankings.pyの値がCLAUDE.mdと矛盾しないか
    sys.path.insert(0, str(BASE))
    from config.rankings import MACHINES
    
    sbj = MACHINES.get('sbj', {})
    if sbj.get('normal_ceiling') != 999:
        issues.append(f'ERROR: SBJ天井が{sbj.get("normal_ceiling")}だがCLAUDE.mdでは999')
    
    hokuto = MACHINES.get('hokuto_tensei2', {})
    if hokuto.get('normal_ceiling', 0) < 1000:
        issues.append(f'WARN: 北斗天井が{hokuto.get("normal_ceiling")}G（あべしシステムのG数参考値として低すぎる可能性）')
    
    return issues


def run_all():
    """全チェック実行"""
    all_issues = []
    all_issues.extend(check_hardcoded_thresholds())
    all_issues.extend(check_claude_md_specs())
    
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
        print('✅ pre-build check: 仕様整合OK')
    
    return len(errors)  # エラーがあればビルド中断


if __name__ == '__main__':
    sys.exit(run_all())
