# Slot Recommendation Site - CLAUDE.md

## 🔄 セッション引き継ぎ

**「続き」や作業指示があったら：**
1. このファイル (`CLAUDE.md`) を読む
2. `memory/` の最新日付ファイルを読む（作業ログ）
3. `git log --oneline -5` で直近の変更を確認
4. `git remote -v` でリモート構成を確認
5. `.git/config` でアカウント設定を確認
6. `~/.openclaw/workspace/memory/` でWhatsApp側の作業ログを確認

**重要**: memoryファイルには一部しか記録されない。git設定やプロジェクト構成も必ず確認すること。

**作業終了時：**
- 重要な変更は `memory/YYYY-MM-DD.md` に書き出す
- 新しいルールや仕様はこのファイルに追記

---

## 📋 プロジェクト概要

スロット台のおすすめ表示サイト（静的HTML生成）

- **ディレクトリ**: `/home/riichi/works/slot`
- **ビルド**: `python3 scripts/generate_static.py`
- **出力**: `docs/index.html` 等
- **デプロイ**: GitHub Pages（push で自動）

---

## ⚠️ 絶対ルール

### 1. 「設定X」は使わない
- ❌ 「設定6」「設定1」等の表記は禁止
- ✅ 代わりに「機械割125.5%」「機械割97%」等を使う
- 理由: 設定は確認不可能、機械割は確率から推定可能

### 2. 確率は整数表示
- ❌ `1/227.818` 
- ✅ `1/227`
- テンプレートでは `|int` フィルタ、Pythonでは `int()` を使う

### 3. 連チャン表示は最後の行
- 「X連」は連チャンの**最後の当たり**（chain_pos == chain_len）に表示
- 降順表示（上が最新）なので、連チャン結果が上に見える

### 4. 好調/不調は確率ベース
- `consecutive_bad`（確率ベース）を使う
- `consecutive_minus`（差枚ベース）は「連続不調」判定に使わない
- 差枚がマイナスでも確率が良ければ「好調」

### 5. データの整合性
- art_count, rensa, diff_medals は同じソースから取得
- 古いデータ（stale）と新しいデータを混ぜない

---

## 🏗️ アーキテクチャ

```
analysis/
  recommender.py    # 推薦ロジック（recommend_units）
  analyzer.py       # 分析関数群
  
scripts/
  generate_static.py  # 静的HTML生成（メイン）
  post_build_check.py # ビルド後検証
  enrich_rec.py       # rec補完処理

web/templates/
  index.html         # メインページ
  verify.html        # 答え合わせページ
  recommend.html     # 店舗別ページ

data/
  daily/             # 日次スナップショット
  history/           # 蓄積履歴データ
```

---

## 🌐 環境構成

### GitHubアカウント（2つ）
| リモート | SSHエイリアス | 用途 |
|---------|--------------|------|
| origin | `github.com` | メイン（デプロイ） |
| secondary | `github-twiakaid` | 分散用（GitHub Actions無料枠分散） |

### SSHキー構成
| キーファイル | 対象アカウント | 用途 |
|-------------|---------------|------|
| `~/.ssh/id_ed25519` | origin（メイン） | `git@github.com` |
| `~/.ssh/id_ed25519_twiakaid` | secondary（twiakaid-hash） | `git@github-twiakaid` |

設定ファイル: `~/.ssh/config`

### CI/CD構成
- **GitHub Actions (origin)**: デプロイ、静的サイト生成
- **GitHub Actions (secondary)**: 軽いスクレイピング
- **Circle CI (secondary)**: Playwright重い処理（fetch-availability, daily-collect）

Circle CI設定: `.circleci/config.yml`
- `hourly-fetch`: 1時間ごと（10:00-23:00 JST）
- `daily-collection`: 毎日23:00 JST

### OpenClaw連携
- ワークスペース: `~/.openclaw/workspace/`
- slotリンク: `~/.openclaw/workspace/slot` → `/home/riichi/works/slot`
- WhatsApp作業ログ: `~/.openclaw/workspace/memory/YYYY-MM-DD.md`

---

## 🔧 よく使うコマンド

```bash
# ビルド
cd /home/riichi/works/slot
python3 scripts/generate_static.py

# テスト（特定店舗）
python3 -c "
import sys, os; sys.path.insert(0,'.'); os.environ['SLOT_BASE_DIR'] = os.getcwd()
from analysis.recommender import recommend_units
recs = recommend_units('shinjuku_espass_hokuto')
for r in sorted(recs, key=lambda x: -x.get('final_score',0))[:5]:
    print(r)
"

# コミット＆プッシュ
git add -A && git commit -m "説明" && git push
```

---

## 🎰 機種仕様

### SBJ（スマスロ北斗の拳）
- 天井: 999G+α（通常時）
- RBではゲーム数天井がリセットされない
- リセット時天井短縮: 666G+α
- 好調閾値: 1/130以下

### 北斗の拳 転生2
- あべし天井システム（G数≠あべし、G数ベース天井判定は参考値）
- モードA天井: 1536あべし
- モードB天井: 896あべし  
- モードC天井: 576あべし
- 天国天井: 128あべし
- 天撃失敗後は絶対にやめない（モード移行の可能性）
- 好調閾値: 1/120以下

---

## 📝 memory/ ファイル

日次の作業ログ。OpenClaw workspace側（`~/.openclaw/workspace/memory/`）にも保存。

---

*最終更新: 2026-02-02*

## 台変動の概念

### 台移動
- 機種の台番号が変わる → 過去データ紐づけ不可（シャッフル）
- 店の傾向・機種のクセは同じだが、台個別データは仕切り直し

### 減台
- 一部が歯抜けになる。残った台は継続
- なくなった台は他機種になる → その新機種を追加しないと意味がない

### 増台
- 取得対象が増える
- 台移動とセットの場合あり → 実質シャッフル

### 撤去
- 機種自体が全部なくなる → 取得先消滅

### 検出ロジック
- `scripts/verify_units.py` で実装済み
- configにある台番号だがデータが取れない → 減台・撤去の可能性
- configにない台番号でデータが取れた → 増台の可能性
- 全台のデータが取れない → 機種撤去 or サイト障害
- **検出後のRSさんへの通知が未実装**（要対応）

## オンデマンド取得（ページ閲覧時のリアルタイムデータ取得）

### 仕組み（実装済み）
```
閲覧者がページ開く
  → realtime.js が PythonAnywhere API を呼ぶ
  → /api/scrape/<store_key> でバックグラウンドスクレイピング開始
  → recommend.html でプログレスバー表示（「データ取得中」）
  → /api/scrape_status/<store_key> でポーリング（1秒間隔）
  → 完了 → 最新データでUI更新 or location.reload()
  → 以後 3-5分間隔で自動更新
```

### 関連ファイル
- `web/static/realtime.js` - クライアント側リアルタイム取得
- `web/app.py` - Flask APIサーバー（/api/scrape, /api/scrape_status）
- `scrapers/availability_checker.py` - データ取得ロジック

### 動作確認
- PythonAnywhere API: https://autogmail.pythonanywhere.com
- 確認コマンド: `curl "https://autogmail.pythonanywhere.com/api/scrape_status/shibuya_espass_sbj"`

### 注意
- WSLが停止していてもPythonAnywhereで動作する
- ただし蓄積データ（history）は別途GitHub Actionsで更新が必要
