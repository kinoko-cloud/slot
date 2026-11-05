#!/bin/bash
# 全テスト実行スクリプト
# Claudeがこれを実行して自己チェックできる

set -e  # エラーで停止

# カラー出力
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "============================================"
echo "  全テスト実行"
echo "============================================"
echo ""

# プロジェクトルートに移動
cd "$(dirname "$0")/.."

# テスト結果カウンター
PASSED=0
FAILED=0

# テスト1: データ品質チェック
echo "🔍 テスト1: データ品質チェック"
if python3 scripts/data_integrity_check.py > /tmp/test_integrity.log 2>&1; then
    echo -e "${GREEN}✓ パス${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ 失敗${NC}"
    cat /tmp/test_integrity.log
    FAILED=$((FAILED + 1))
fi
echo ""

# テスト2: 静的ビルド
echo "🔨 テスト2: 静的ビルド"
if python3 scripts/generate_static.py > /tmp/test_build.log 2>&1; then
    echo -e "${GREEN}✓ パス${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ 失敗${NC}"
    cat /tmp/test_build.log
    FAILED=$((FAILED + 1))
fi
echo ""

# テスト3: HTML検証
echo "✅ テスト3: HTML検証"
if python3 scripts/validate_output.py > /tmp/test_validate.log 2>&1; then
    echo -e "${GREEN}✓ パス${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ 失敗${NC}"
    cat /tmp/test_validate.log
    FAILED=$((FAILED + 1))
fi
echo ""

# テスト4: 予測ロジックのサンプル実行
echo "🧠 テスト4: 予測ロジック"
if python3 -c "
import sys, os
sys.path.insert(0, '.')
os.environ['SLOT_BASE_DIR'] = os.getcwd()
from analysis.recommender import recommend_units
recs = recommend_units('shinjuku_espass_hokuto')
assert len(recs) > 0, '予測結果が空'
assert 'rank' in recs[0], 'ランクフィールドが存在しない'
print('✓ 予測ロジック正常')
" > /tmp/test_prediction.log 2>&1; then
    echo -e "${GREEN}✓ パス${NC}"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}✗ 失敗${NC}"
    cat /tmp/test_prediction.log
    FAILED=$((FAILED + 1))
fi
echo ""

# 結果サマリ
echo "============================================"
echo "  結果サマリ"
echo "============================================"
echo -e "パス: ${GREEN}${PASSED}${NC}"
echo -e "失敗: ${RED}${FAILED}${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ 全テストパス！${NC}"
    exit 0
else
    echo -e "${RED}✗ 一部のテストが失敗しました${NC}"
    exit 1
fi
