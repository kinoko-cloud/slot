#!/bin/bash
# PythonAnywhere自動デプロイスクリプト
# 使い方: ./deploy.sh "コミットメッセージ"

set -e

# 設定
PA_USER="autogmail"
PA_DOMAIN="autogmail.pythonanywhere.com"
PA_TOKEN=$(cat .pythonanywhere_token)
DEPLOY_SECRET="slot_deploy_2026"

# コミットメッセージ
MSG="${1:-auto deploy}"

echo "=== デプロイ開始 ==="

# 1. Git push
echo "[1/3] Git push..."
git add -A
git commit -m "$MSG

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>" 2>/dev/null || echo "Nothing to commit"
git push origin main

# 2. PythonAnywhereでgit pull（Webアプリ経由）
echo "[2/3] PythonAnywhere git pull..."
PULL_RESULT=$(curl -s -X POST "https://$PA_DOMAIN/deploy" -d "secret=$DEPLOY_SECRET")
echo "$PULL_RESULT" | head -c 200

# 3. Webアプリをreload
echo ""
echo "[3/3] Webアプリ reload..."
RELOAD_RESULT=$(curl -s -X POST \
  -H "Authorization: Token $PA_TOKEN" \
  "https://www.pythonanywhere.com/api/v0/user/$PA_USER/webapps/$PA_DOMAIN/reload/")
echo "$RELOAD_RESULT"

echo ""
echo "=== デプロイ完了 ==="
echo "URL: https://$PA_DOMAIN"
