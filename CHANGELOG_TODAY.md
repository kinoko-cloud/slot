# 2026-02-12 変更点

## 問題
GitHub Actionsのスケジュール実行が不安定で、営業時間中にリアルタイムデータが更新されない

## 対策

### 1. deploy-static.yml 改善
- 営業時間中（10-23時JST）でデータが2時間以上古い場合は自動fetch
- 実行頻度: JST 9-22時、毎時0分

```yaml
schedule:
  - cron: '0 0-13 * * *'  # UTC 0-13 = JST 9-22時
```

### 2. Cloudflare Worker追加（新規）
**ファイル:** `workers/trigger-workflow.js`, `workers/wrangler.toml`

- GitHub Actionsのバックアップトリガー
- 実行: 毎時15分・45分（JST 9:15-22:45）
- データが90分以上古い → fetch-availability.ymlをトリガー
- データが新しい → deploy-static.ymlをトリガー

### 3. scripts/trigger_github_workflow.py 追加
- PythonAnywhereのスケジュールタスクから実行可能
- 同じロジック（データ古い→fetch、新しい→deploy）

## セットアップ必要

### Cloudflare Worker
```bash
cd /home/riichi/works/slot/workers
npm install -g wrangler
wrangler login
wrangler secret put GITHUB_PAT  # GitHubで作成したPATを入力
wrangler deploy
```

### GitHub PAT作成
1. https://github.com/settings/tokens/new
2. Note: slot-workflow-trigger
3. Scopes: ✅ workflow
4. Generate token → コピー

## 冗長性
- **GitHub Actions**: 毎時0分（メイン）
- **Cloudflare Worker**: 毎時15分・45分（バックアップ）

どちらかが失敗しても、もう一方がカバー。
