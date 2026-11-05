#!/usr/bin/env python3
"""HTMLで使われてるCSSクラスがstyle.cssに定義されてるかチェック"""
import re, glob, sys, os

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

with open('web/static/style.css') as f:
    css = f.read()

css_classes = set(re.findall(r'\.([\w][\w-]*)', css))

# Jinja動的クラス（プレフィックス）— 実行時に値が付く
DYNAMIC_PREFIXES = {'rank-', 'setting-', 'verdict-', 'alert-'}

html_classes = set()
for html_file in glob.glob('web/templates/*.html'):
    with open(html_file) as f:
        html = f.read()
    clean = re.sub(r'\{[%{#].*?[%}#]\}', ' ', html, flags=re.DOTALL)
    for cls_attr in re.findall(r'class="([^"]*)"', clean):
        for c in cls_attr.split():
            if re.match(r'^[a-z][a-z0-9-]*$', c) and len(c) > 2:
                if not any(c == dp.rstrip('-') or c.startswith(dp) for dp in DYNAMIC_PREFIXES):
                    html_classes.add(c)

missing = html_classes - css_classes
# legend-itemsなど外部ライブラリ系を除外
IGNORE = {'legend-items'}
missing -= IGNORE

if missing:
    print(f"⚠️ CSS未定義クラス ({len(missing)}件):")
    for c in sorted(missing):
        print(f"  .{c}")
    sys.exit(1)
else:
    print("✅ CSS未定義クラスなし")
