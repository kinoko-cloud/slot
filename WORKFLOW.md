# 開発ワークフロー

このドキュメントは、開発作業の標準フローを定義しています。
Claudeと人間の両方が参照し、一貫した作業プロセスを維持します。

## 📚 関連ドキュメント

- `CLAUDE.md` - Claude専用の推測不可能な情報（絶対ルール、機種仕様等）
- `ARCHITECTURE.md` - コードベース全体マップ
- `WORKFLOW.md` - このファイル（開発フロー、コマンド集）

---

## 🔄 標準的な作業フロー

### 新機能追加・大きな変更

**段階的アプローチ**を採用し、計画と実装を分離します。

#### フェーズ1: 探索（Plan Mode推奨）

```bash
# Plan Modeで開始
/plan
```

**やること:**
1. 関連ファイルを探索（Glob, Grep）
2. 既存パターンを確認
3. 影響範囲を特定
4. 類似実装を探す

**成果物:** 影響範囲の把握

---

#### フェーズ2: 計画（Plan Mode）

**やること:**
1. 実装方針を文書化
2. 変更するファイルをリストアップ
3. テスト計画を立てる
4. リスクを特定

**成果物:** 実装プランドキュメント

**Plan Mode終了:**
```bash
/exitplan
```

---

#### フェーズ3: 実装（通常モード）

**やること:**
1. コード変更
2. テスト実行（`bash tests/run_all_tests.sh`）
3. 検証（期待される出力と比較）
4. 必要に応じて修正

**自己チェック:**
- `tests/expected_outputs/` のサンプルと比較
- エラーがあれば原因を調査して修正

---

#### フェーズ4: コミット

**やること:**
1. `git status` で変更確認
2. `git diff` で差分レビュー
3. `git add` で変更をステージング
4. `git commit` でコミット（Co-Authored-By: Claude）
5. `git push`

**コミットメッセージ:**
- 簡潔な要約（1-2行）
- 「何を」ではなく「なぜ」を記述
- Co-Authored-By を含める

---

### 小さな修正・バグ修正

小さな変更は Plan Mode をスキップ可能：

1. ファイルを直接編集
2. テスト実行
3. コミット

---

## 🧪 テスト・検証コマンド

### 全テスト実行

```bash
cd /home/riichi/works/slot
bash tests/run_all_tests.sh
```

**含まれるテスト:**
- データ品質チェック
- 静的ビルド
- HTML検証
- 予測ロジック

---

### 個別テスト

#### データ品質チェック
```bash
python3 scripts/data_integrity_check.py
```

#### 静的ビルド
```bash
python3 scripts/generate_static.py
```

#### HTML検証
```bash
python3 scripts/validate_output.py
```

#### 予測ロジックのサンプル実行
```bash
python3 -c "
import sys, os
sys.path.insert(0, '.')
os.environ['SLOT_BASE_DIR'] = os.getcwd()
from analysis.recommender import recommend_units
recs = recommend_units('shinjuku_espass_hokuto')
for r in sorted(recs, key=lambda x: -x.get('final_score',0))[:5]:
    print(f\"{r['unit_id']}: {r['rank']} (score={r['final_score']:.1f})\")
"
```

#### 台変動チェック
```bash
python3 scripts/verify_units.py
```

---

## 🛠️ よく使うコマンド

### ビルド・デプロイ

```bash
# ローカルビルド
python3 scripts/generate_static.py

# 検証
python3 scripts/validate_output.py

# コミット＆プッシュ（自動デプロイ）
git add -A
git commit -m "メッセージ

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
git push
```

---

### データ収集

```bash
# リアルタイムデータ取得（全店舗）
python3 scripts/fetch_daidata_availability.py

# 日次データ収集
python3 scripts/daily_collect.py
```

---

### Git操作

```bash
# 状態確認
git status
git log --oneline -5

# リモート確認
git remote -v

# 最新を取得
git pull

# ブランチ確認
git branch -a
```

---

## 📋 会話履歴管理

### `/clear` を使うタイミング

ベストプラクティス: 無関係なタスク間で `/clear` を実行

**例:**

```
タスクA（機能追加）完了
↓
/clear  ← ここで履歴をクリア
↓
タスクB（バグ修正）開始
```

**メリット:**
- コンテキストの混乱を防ぐ
- 関係ない情報を排除
- レスポンス速度向上

---

### サブエージェント活用

調査タスクはサブエージェントで分離：

```bash
# コードベース探索
Task tool (Explore agent)

# 特定の調査
Task tool (General-purpose agent)
```

---

## 🚨 失敗時の対応

### テスト失敗

1. エラーメッセージを確認
2. `tests/expected_outputs/` のサンプルと比較
3. 原因を特定
4. 修正
5. 再テスト

### ビルド失敗

1. ログを確認（`/tmp/test_build.log`）
2. エラー箇所を特定
3. 関連ファイルを確認
4. 修正
5. 再ビルド

### Git コンフリクト

1. `git status` で状態確認
2. コンフリクトファイルを確認
3. 手動マージ
4. テスト実行
5. コミット

---

## 🔄 セッション引き継ぎ

新しいセッション開始時の手順（CLAUDE.md参照）：

1. `CLAUDE.md` を読む
2. `memory/` の最新ファイルを読む
3. `git log --oneline -5` で直近の変更確認
4. `git status` で現在の状態確認

---

## 📝 作業ログ

重要な変更は `memory/YYYY-MM-DD.md` に記録：

- 実施した変更
- 学んだこと
- 次回の課題

---

**最終更新:** 2026-02-04
