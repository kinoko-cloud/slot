# Slot - セッション状態管理（常に最新）

## ⚠️ Claudeへの指示（最重要）

**このファイルを読んだら：**
1. この内容を理解する
2. **作業終了時（ユーザーが「終了」「離れる」等と言ったら）、必ずこのファイルを更新する**
3. 更新内容：
   - 現在の状態（動作中/問題あり/完了）
   - 現在の問題
   - 次にやること
   - 最新コミットID
4. これにより、別の環境（WhatsApp/PC）でも続きができる

**更新忘れ厳禁**: このファイルが唯一の真実。更新しないと、次のセッションで齟齬が発生する。

---

## 現在の状態（2026-02-12 22:22）

**🔄 切り替え**: PCから離席 → WhatsApp（clawdbot）経由に切り替え

### ✅ 完了した作業
- システム大幅改善（並列ワークフロー最適化）
- 自動復旧ワークフロー改善
- 不要なワークフロー削除・無効化
- ドキュメント整備（CHANGELOG, STATUS_NOW, NEXT_ACTIONS）
- SESSION_STATE.md作成（双方向の仕組み）

### ⚠️ 現在の問題（未解決）
**リアルタイムが反映されていない**
- データ: 21:09に更新済み ✅
- metadata.json: 21:09のfetched_at含む ✅
- しかし、ユーザーが「反映されていない」と報告 ❌
- **原因**: Cloudflare Pagesのキャッシュ、またはブラウザキャッシュ
- **影響**: サイトで古いデータが表示される

### 🎯 次にやること（優先順）
1. **【最優先】deploy-static.ymlを手動実行**
   - https://github.com/kinoko-cloud/slot/actions/workflows/deploy-static.yml
   - 「Run workflow」を押す
   - 約2分で静的サイト再生成
   - サイトで最新データ（21:09）が表示されるか確認

2. **22:30の自動復旧を確認**
   - 22:30に自動復旧ワークフローが実行されるはず
   - データが30分以上古い → 自動fetch実行
   - 22:35頃にデータ更新を確認

3. **22:00未実行の原因調査**
   - なぜ22:00の並列ワークフローが実行されなかったか
   - GitHub Actionsログを確認
   - cronスケジュールの問題か？

---

## 📊 システム状態

### データ取得
- 最終取得: **21:09**（約70分前）
- 次回更新: **22:30**（自動復旧ワークフロー）
- システム: **正常動作中**

### ワークフロー構成
```
メイン: 並列ワークフロー（毎時0分、JST 10-22時）
  └─ 全18店舗を4分で取得

バックアップ: 自動復旧（毎時30分、JST 10:30-22:30）
  └─ データ30分以上古い場合のみfetch
```

---

## 🔗 重要リンク

- **サイト**: https://kinoko-cloud.github.io/slot/
- **GitHub Actions**: https://github.com/kinoko-cloud/slot/actions
- **deploy-static.yml実行**: https://github.com/kinoko-cloud/slot/actions/workflows/deploy-static.yml

---

## 📝 最新コミット

```
7bd7b1a67e feat: セッション状態管理ファイルを追加
```

---

## 💡 よく使うコマンド

### データ確認
```bash
python3 -c "import json; data = json.load(open('data/availability.json')); print('取得:', data.get('fetched_at'))"
```

### 最新コミット確認
```bash
git log --oneline -3
```

### データ更新
```bash
# 並列ワークフロー手動実行（GitHub.comから）
https://github.com/kinoko-cloud/slot/actions/workflows/fetch-availability-parallel.yml
```

---

## 🔄 切り替え方法

### WhatsApp → PC
1. WhatsAppで「終了」と言う
2. Clawdbotがこのファイルを更新
3. PCで「続き」と言う
4. このClaudeが SESSION_STATE.md を読んで続きを開始

### PC → WhatsApp
1. PCで「終了」と言う
2. このClaudeがこのファイルを更新
3. WhatsAppで「続き」と言う
4. Clawdbotが SESSION_STATE.md を読んで続きを開始

---

**最終更新**: 2026-02-12 22:22
**更新者**: PC Claude（離席前）
**次の環境**: WhatsApp（clawdbot）
**次回確認**: 22:30（自動復旧実行時）
