# 次にやるべきアクション

**作成日時**: 2026-02-12 22:15

---

## 🚨 最優先: リアルタイム反映問題

### 問題
ユーザーが「リアルタイム反映されてない」と報告

### データ確認済み
- ✅ availability.json: 21:09に更新済み
- ✅ metadata.json: 21:09のfetched_at含む
- ✅ 整合性: 両ファイル一致

### 考えられる原因
1. **Cloudflare Pagesのキャッシュ**（最も可能性高い）
2. ブラウザキャッシュ（ユーザー側）
3. GitHub Pagesのデプロイ遅延

### 解決方法（優先順）

#### 方法1: deploy-static.ymlを手動実行
```
1. https://github.com/kinoko-cloud/slot/actions/workflows/deploy-static.yml
2. 「Run workflow」をクリック
3. 約2分で完了
4. サイトを確認
```

#### 方法2: Cloudflare Pagesのキャッシュパージ
```
（Cloudflare使っている場合）
1. Cloudflareダッシュボード
2. Caching > Purge Cache
3. Purge Everything
```

#### 方法3: 強制再デプロイ
```bash
# 空コミットでトリガー
git commit --allow-empty -m "chore: force redeploy"
git push origin main
```

---

## ⏰ 監視: 22:30の自動復旧

### 何が起こるか
- 22:30に自動復旧ワークフローが実行される
- データが30分以上古い場合、自動fetch実行
- 現在64分経過 → fetch実行されるはず

### 確認方法
```bash
# 22:35頃に確認
git pull origin main
git log --oneline -3

# データ取得時刻を確認
python3 -c "import json; data = json.load(open('data/availability.json')); print('取得:', data.get('fetched_at'))"
```

### 期待される結果
- コミット: "auto: 自動復旧 22:3X"
- データ取得時刻: 22:30台

---

## 🔍 調査: 22:00未実行の原因

### 確認すべきこと
1. **GitHub Actionsログ**
   - https://github.com/kinoko-cloud/slot/actions
   - 22:00前後の実行履歴を確認

2. **cronスケジュール**
   ```yaml
   # fetch-availability-parallel.yml
   cron: '0 1-13 * * *'  # UTC 1-13 = JST 10-22時
   ```
   - JST 22時 = UTC 13時（範囲内のはず）
   - なぜ実行されなかったか？

3. **可能性**
   - GitHub Actionsの遅延
   - cronの解釈ミス（1-13は1,2,...,13を意味するが、13時ちょうどが含まれない？）
   - ワークフロー無効化されている？

### 対策案
もし22:00が実行されないことが判明したら：
```yaml
# cronを修正
cron: '0 1-13,13 * * *'  # 13時を明示的に含める
# または
cron: '0 1,2,3,4,5,6,7,8,9,10,11,12,13 * * *'  # 明示的に列挙
```

---

## 📝 WhatsAppから作業する際のテンプレート

### 状況確認
```
STATUS_NOW.mdを読んで、現在の状況を教えて
```

### データ確認
```
availability.jsonの最終取得時刻を確認して
```

### ワークフロー実行状況
```
最新5件のコミットを見せて
```

### 問題解決
```
リアルタイム反映されない問題を解決して。
deploy-static.ymlを手動実行する方法を教えて。
```

---

## 🎯 成功の指標

### リアルタイム反映問題が解決した状態
- ✅ サイトで21:09（または最新）のデータが表示される
- ✅ ユーザーが「更新された」と確認

### システムが正常な状態
- ✅ 22:30に自動復旧ワークフローが実行
- ✅ データが更新される
- ✅ 23:00までに最新データがサイトに反映

---

## 💡 ヒント

### WhatsAppから戻ってきた際
1. **STATUS_NOW.md**を読む
2. **git pull**して最新コミット確認
3. **availability.json**の取得時刻確認
4. **サイト**で実際のデータ確認

### 問題が解決していない場合
1. **NEXT_ACTIONS.md**（このファイル）の解決方法を試す
2. **GitHub Actions**でログ確認
3. **手動実行**でワークフローを動かす

---

**次回更新**: 22:30（自動復旧ワークフロー）
**確認時刻**: 22:35以降
