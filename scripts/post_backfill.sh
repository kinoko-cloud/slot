#!/bin/bash
set -e
cd /home/riichi/works/slot

echo "=== 静的サイト再生成 ==="
python3 scripts/generate_static.py

echo "=== CSSチェック ==="
python3 scripts/check_css.py

echo "=== git push ==="
git add -A
git commit -m "data: パピモ過去14日分バックフィル完了 (アイランド秋葉原 SBJ+北斗)

- days_back=2→14に変更
- 全28台の1/15〜1/27データを取得
- 既存6-7日→13日分に倍増"
git push

echo "=== 完了 ==="
