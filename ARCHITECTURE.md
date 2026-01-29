# ARCHITECTURE.md — コードベース全体マップ

**⚠️ セッション開始時に必ず読むこと。RSさんはこの全体像を理解している前提で指示を出す。**

**最終更新: 2026-01-31**

---

## サービス概要

スロット台の予測・おすすめサービス。
- 閲覧者がサイトを開いた瞬間に最新データを取得→予測→表示
- 閉店後は確定データから翌日予測を表示

## 2つの動作モード

### 確定データモード（閉店後〜開店前）
- 蓄積DB（前日までの確定実績）から翌日のおすすめ台を表示
- generate_static.pyでビルドした静的HTML

### リアルタイムモード（営業中）★サービスの核心
- **閲覧者がサイトを開いた瞬間にリアルタイムでデータ取得→予測→表示**
- ローディング画面（プログレスバー、10-30秒）を表示しながらスクレイピング実行
- 取得完了後、最新の当たりデータ・回転数で「今この瞬間の狙い目」を予測

#### リアルタイム取得の仕組み（既存実装）
```
閲覧者がページ開く
  → realtime.js が PythonAnywhere API を呼ぶ
  → /api/scrape/<store_key> でバックグラウンドスクレイピング開始
  → recommend.html でプログレスバー表示（「データ取得中」）
  → /api/scrape_status/<store_key> でポーリング（1秒間隔）
  → 完了 → 最新データでUI更新 or location.reload()
  → 以後 3-5分間隔で自動更新
```

---

## ファイル構成

### データ層
| ファイル | 役割 |
|---------|------|
| `data/history/{store_key}/{unit_id}.json` | **蓄積DB** — 台ごとの全日履歴（最重要データ） |
| `data/daily/{store_key}/YYYY-MM-DD.json` | 日別スナップショット |
| `data/availability/{store_key}.json` | リアルタイム空き状況 |

### スクレイパー層
| ファイル | 役割 | 状態 |
|---------|------|------|
| `scrapers/daidata_detail_history.py` | daidata: 当たり履歴取得（Playwright） | ✅ 現役 |
| `scrapers/papimo.py` | papimo: 当たり履歴取得（requests） | ✅ 現役 |
| `scrapers/daidata_direct.py` | daidata: 基本データ取得 | ✅ 現役 |
| `scrapers/availability_checker.py` | リアルタイムデータ取得（availability.json / GAS） | ✅ 現役 |
| `scrapers/realtime_scraper.py` | リアルタイムスクレイピング（**全9店舗対応**） | ✅ 現役 |

### 分析・予測層
| ファイル | 役割 | 状態 |
|---------|------|------|
| `analysis/recommender.py` | **メイン予測エンジン** — スコアリング/ランク/おすすめ文生成 | ✅ 現役 |
| `analysis/history_accumulator.py` | 蓄積DB管理 — 日別データ蓄積/連チャン/差枚計算 | ✅ 現役 |
| `analysis/diff_medals_estimator.py` | 差枚推定（⚠️ 不正確、フォールバックのみ） | ✅ 現役（廃止検討中） |
| `analysis/pattern_detector.py` | パターン検出 | ✅ 現役 |
| `analysis/verdict.py` | 判定ロジック | ✅ 現役 |
| `analysis/feedback.py` | 的中フィードバック | ✅ 現役 |

### 設定層
| ファイル | 役割 |
|---------|------|
| `config/rankings.py` | **機種別閾値定義（唯一の定義元）** — good_prob, bad_prob等 |
| `config/stores.py` | 店舗定義 |

### Web層
| ファイル | 役割 |
|---------|------|
| `web/app.py` | **Flask APIサーバー** — 動的ルーティング + リアルタイムAPI |
| `web/static/realtime.js` | **クライアント側リアルタイム取得** — API呼び出し/UI更新 |
| `web/static/style.css` | スタイルシート |
| `web/templates/index.html` | トップページ（おすすめ/爆発台/SA候補） |
| `web/templates/recommend.html` | 店舗別推奨ページ（**ローディングUI付き**） |
| `web/templates/ranking.html` | 機種別ランキング |
| `web/templates/unit_history.html` | 台別履歴 |
| `web/templates/verify.html` | 的中率検証 |
| `web/templates/stores.html` | 店舗一覧 |
| `web/templates/_topbar.html` | 共通トップバー |
| `web/templates/_common_js.html` | 共通JS |

### スクリプト層
| ファイル | 役割 | 状態 |
|---------|------|------|
| `scripts/generate_static.py` | **静的サイトビルド** → docs/ に出力 | ✅ 現役 |
| `scripts/daily_collect.py` | **日次データ収集** — スクレイピング→蓄積DB更新→ビルド | ✅ 現役 |
| `scripts/fetch_daidata_availability.py` | **リアルタイムデータ取得**（Playwright、全9店舗） | ✅ 現役 |
| `scripts/auto_update.sh` | **15分cron自動更新**（ローカル用） | ✅ 現役 |
| `scripts/data_integrity_check.py` | データ品質チェック | ✅ 現役 |
| `scripts/validate_output.py` | ビルド後HTML検証 + リアルタイム健全性チェック | ✅ 現役 |
| `scripts/enrich_rec.py` | データ補完 | ✅ 現役 |
| `scripts/nightly_verify.py` | 夜間的中検証 | ✅ 現役 |

### アーカイブ（_archive/）
開発残骸。削除はしないが現在使用しない。
- `_archive/scrapers/`: daidata_scraper{,2,3}.py, daidata_sbj.py, test_*.py 等
- `_archive/analysis/`: compare_all.py, store_analyzer.py, realtime_predictor.py
- `_archive/scripts/`: backfill_papimo.py, fetch_island_history.py

### ドキュメント
| ファイル | 役割 |
|---------|------|
| `docs/SPEC_prediction.md` | **予測ロジック仕様書（マスター）** |
| `docs/DESIGN_evolution.md` | 分析ロジック進化設計書 |
| `CLAUDE.md` | AIアシスタント向けガイド |
| `REVIEW_CHECKLIST.md` | レビューチェックリスト |

### デプロイ
| 項目 | 値 | 状態 |
|------|-----|------|
| 静的ホスティング | Cloudflare Pages (`docs/`) | ✅ 稼働中 |
| APIサーバー | PythonAnywhere (`web/app.py`) — v14 | ✅ 稼働中 |
| データ取得（定期） | GitHub Actions 15分ごと (`fetch-availability.yml`) | ✅ 稼働中 |
| データ取得（ローカル） | WSL cron 15分ごと (`auto_update.sh`) | ✅ 稼働中（WSL起動時） |
| 自動デプロイ | push→`deploy.yml`→PythonAnywhere git pull+reload | ✅ 稼働中 |

### 健全性チェック
| ファイル | 用途 |
|---------|------|
| `INTEGRATION_CHECK.md` | リアルタイム機能の手動チェックリスト |
| `scripts/validate_output.py` | 自動検証（ビルド時 + リアルタイム健全性） |

---

## データフロー

### 閉店後（静的ビルド）
```
cron → daily_collect.py
  → daidata_detail_history.py / papimo.py（スクレイピング）
  → history_accumulator.py（蓄積DB更新）
  → data_integrity_check.py（品質チェック）
  → generate_static.py（静的HTML生成）
    → recommender.py（スコアリング/ランク/おすすめ文）
    → validate_output.py（HTML検証）
  → git push → Cloudflare Pages デプロイ
```

### 営業中（リアルタイム）
```
閲覧者がページ開く
  → realtime.js → PythonAnywhere API
  → /api/scrape/{store} → realtime_scraper.py
  → recommend.html ローディングUI表示
  → 完了 → recommender.py で最新データから再予測
  → UI更新
```

---

## 既知の問題・注意点

1. **estimate_diff_medals()は信用できない** — 符号すら逆になることがある。蓄積DBのdiff_medals最優先
2. **config/rankings.pyが閾値の唯一の定義元** — 他でハードコードしない
3. **PythonAnywhere API**: 稼働中。deploy.ymlで自動デプロイ（push→git pull→reload）
4. **静的サイト(docs/)とFlask動的サイト(web/app.py)が共存** — 閉店後は静的、営業中はAPI経由の動的更新
5. **WSL再起動後はcronデーモンが停止する** — `sudo service cron start` が必要
6. **リアルタイム機能の放置検知** — `INTEGRATION_CHECK.md` で定期確認、`validate_output.py` で自動チェック

---

---

## 設計意図・経緯（Why）

### なぜリアルタイムAPI方式か
- Cloudflare Pages（静的ホスティング）をフロントにしているため、動的処理はAPI分離が必要
- PythonAnywhere無料プランではdaidata等にアクセス不可（ホワイトリスト外）
- → **GitHub Actions（10分ごと）でPlaywright実行 → availability.json → PythonAnywhereがJSON読み込み** という構成
- GAS経由のアイランドデータ + GitHub Actions経由のエスパスデータ = 2系統

### なぜローディング画面（10-30秒）か
- スクレイピングに時間がかかる（Playwright起動+ページ描画+データ取得）
- 閲覧者にリアルタイム感を伝えつつ、バックグラウンドでAPIが処理完了を待つ
- ポーリング方式: startScraping → checkScrapingStatus(1秒間隔) → 完了でUI更新

### なぜ静的サイトと動的APIが共存するか
- **閉店後**: 全データが確定しているので静的HTMLで十分（高速・無料）
- **営業中**: 刻々と変わるデータを反映するためAPIが必要
- generate_static.py（閉店後用）+ web/app.py（営業中用）で分離

### なぜ蓄積DB（data/history/）を作ったか
- daidataは直近7日分しか保持しない。papimoは14日
- 長期分析（月間パターン、設定変更周期）には全日データが必要
- → ローカルに台ごとのJSON蓄積。daily_collect.pyで毎日追記

### なぜestimate_diff_medals()があるか
- 蓄積DB登場前は当たり履歴のmedals合計から差枚を推定するしかなかった
- 蓄積DBにdiff_medalsが入った今は不正確なフォールバックでしかない
- **将来的に廃止検討**（蓄積DBのdiff_medals充足率が上がれば）

### なぜS/A枠制限があるか
- 全台Sランクにしたら意味がない（パチンコ店は利益を出す必要がある）
- 店の好調率（全台中の高設定率）を推定し、S/A枠をそれに連動させる
- RSさんの考え: 店舗は**島単位**で利益管理。設定は1-6段階（好調/不調の二値は粗い）

### なぜ機種別閾値があるか（config/rankings.py）
- SBJと北斗転生2では確率体系が全く違う（SBJ: ART 1/130-1/240、北斗: AT 1/270-1/366）
- good_prob/bad_probは機種の仕様に基づく数値（設定差のある境界）
- **CLAUDE.mdの設定差テーブルが根拠**

### プロジェクトの出発点と目標
- RSさんがパチスロの良台を効率的に選ぶためのツール
- 最初の3店舗・1機種（SBJ）で練習 → 現在は5店舗・2機種に拡大
- **目標: データ蓄積に応じて自動的に分析精度を上げ、1ヶ月後に的中率100%に近づく**
- 段階的進化: Phase 1（7日）→ Phase 5（31日+）で分析が深化（docs/DESIGN_evolution.md）

### PythonAnywhere vs ローカル
- PythonAnywhere: 無料プランのFlask Webアプリ。APIエンドポイント提供
- ローカル(WSL): Playwright実行、日次バッチ、静的ビルド
- GitHub Actions: 定期スクレイピング（10分ごと）+ 自動デプロイ

---

## 変更履歴

| 日付 | 内容 |
|------|------|
| 2026-01-30 | 初版作成（RSさん指摘：既存コードを忘れて新規で作る問題への対策） |
| 2026-01-30 | Why（設計意図・経緯）セクション追加 |
| 2026-01-31 | リアルタイム機能統合 — 全9店舗対応、稼働状態を明記、アーカイブ整理 |
