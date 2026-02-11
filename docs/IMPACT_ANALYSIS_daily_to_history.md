# 完全影響範囲調査: daily/ 依存削除 → history/ 移行

**作成日**: 2026-02-11
**目的**: data/daily/ への依存を削除し、data/history/ からの直接読み込みに移行する変更の影響範囲を完全に文書化

---

## 📋 目次

1. [現状の問題](#現状の問題)
2. [現在のデータフロー](#現在のデータフロー)
3. [提案する新しいデータフロー（案A）](#提案する新しいデータフロー案a)
4. [影響を受けるコンポーネント](#影響を受けるコンポーネント)
5. [変更が必要なファイル一覧](#変更が必要なファイル一覧)
6. [外部サービスへの影響](#外部サービスへの影響)
7. [テンプレートへの影響](#テンプレートへの影響)
8. [ロールバック手順](#ロールバック手順)
9. [実装フェーズ](#実装フェーズ)

---

## 現状の問題

### 症状
- **毎日、おすすめ台が古いデータを表示する**
- 例: 2026-02-11 時点で、1月26日のデータが「昨日」として表示される

### 根本原因
1. **`data/daily/` が 2026-01-27 以降更新されていない**
   - 最新ファイル: `daily_akiba_espass_sbj_20260127.json` (1月28日 06:45)
   - 内容: 1月26日までの7日分のデータ

2. **GitHub Actions `daily_collect.yml` が動いていない**
   - スケジュール: 毎日 23:00 JST
   - 実行されていない（コミット履歴に "Daily data collection" が見つからない）

3. **システム設計が逆転している**
   - `data/history/` が10分〜1時間ごとに更新（本来は1日1回の確定データであるべき）
   - `data/daily/` が週1回程度の更新（本来はリアルタイムまたは毎日更新であるべき）

---

## 現在のデータフロー

### データソース
```
scrapers/daidata_detail_history.py
  ↓ (週1回程度、GitHub Actions経由)
data/daily/daily_{store}_{date}.json
  - 複数日分のスナップショット（7日分程度）
  - 各日の history 配列を含む

scrapers/fetch_daidata_availability.py
  ↓ (10分〜1時間ごと)
data/availability.json
  - リアルタイムデータ（today_history含む）
  ↓ (sync_realtime_to_history.py - 10分〜1時間ごと)
data/history/{store}/{unit_id}.json
  - 蓄積データ（全日付のhistory含む）
```

### 使用箇所

#### **営業前（閉店後〜開店前）**
- **予測**: `daily` + `history` の店舗傾向
- **表示**: `daily` の前日データ
  - `yesterday_art`, `yesterday_games`, `yesterday_prob`
  - `day_before_art`, `day_before_games`
  - `three_days_ago_art`, `three_days_ago_games`
  - `yesterday_history`, `day_before_history`, `three_days_ago_history`

#### **営業中（10:00〜22:49）**
- **予測**: `daily` + `history` の店舗傾向（営業前と同じ）
- **当日表示**: `availability.json` の `today_history`
  - ART回数、ゲーム数、確率（リアルタイム）
  - nullの場合は0表示（エラーではない）
- **過去3日表示**: `daily` から取得
  - `yesterday_history`, `day_before_history`, `three_days_ago_history`

### 問題点
- `daily` が更新されないため、「昨日」が2週間前になっている
- `history` は更新されているが、recommender.py は `daily` からしか yesterday を取得しない
- **設計として逆転**: history が頻繁更新、daily が停滞

---

## 提案する新しいデータフロー（案A）

### データソース
```
scrapers/fetch_daidata_availability.py
  ↓ (10分〜1時間ごと、営業時間中のみ)
data/availability.json
  - リアルタイムデータ（当日のみ）
  - today_history のみ保持

scripts/nightly_update.sh (新規作成)
  ↓ (毎日 23:00 JST、1日1回)
data/history/{store}/{unit_id}.json
  - 確定データ（全日付のhistory含む）
  - availability.json から当日確定データを取り込み

data/daily/ ← 廃止
```

### 変更のポイント
1. **`data/daily/` を完全廃止**
   - スナップショット不要（history が全データを持つ）
   - daily_collect.py 廃止
   - daily_collect.yml 廃止

2. **`data/history/` を1日1回の確定データに変更**
   - sync_realtime_to_history.py を auto_update.sh から削除
   - 新しい nightly_update.sh で23:00に1回だけ実行

3. **recommender.py を history/ から直接読み込むように変更**
   - `load_daily_data()` を `load_history_data()` に変更
   - analyze_trend() が history/ から yesterday/day_before を取得

4. **テンプレートは変更不要**
   - yesterday_art, day_before_art などの変数名はそのまま
   - データの供給元が daily → history に変わるだけ

---

## 影響を受けるコンポーネント

### 1. データ取得・更新スクリプト (7ファイル)
| ファイル | 現状 | 変更内容 |
|---------|------|---------|
| `scripts/daily_collect.py` | 毎日実行（動いていない） | **廃止** |
| `scripts/fetch_daidata_availability.py` | 10分〜1時間ごと | **変更なし**（リアルタイム取得継続） |
| `scripts/sync_realtime_to_history.py` | 10分〜1時間ごと実行 | **実行頻度変更**: 1日1回（23:00のみ） |
| `scripts/auto_update.sh` | sync_realtime_to_history.py呼び出し | **削除**: sync_realtime_to_history.py の呼び出しを削除 |
| `scripts/nightly_update.sh` | **新規作成** | 23:00に実行、history更新+静的サイト再生成 |
| `.github/workflows/daily_collect.yml` | 23:00実行（動いていない） | **廃止** |
| `.github/workflows/daily-verify.yml` | 23:30実行 | **変更なし**（既に generate_verify.py が history を使用） |

### 2. 分析・レコメンドロジック (6ファイル)
| ファイル | 使用箇所 | 変更内容 |
|---------|---------|---------|
| `analysis/recommender.py` | L298: `load_daily_data()` 定義<br>L2783: `load_daily_data()` 呼び出し | **大幅変更**: `load_history_data()` に変更、history/ から直接読み込み |
| `scripts/generate_static.py` | L30: import<br>L1318, L2196, L2748: 呼び出し<br>L2750: `accumulate_from_daily()` | **変更**: `load_history_data()` に変更<br>`accumulate_from_daily()` 不要（既にhistoryに蓄積済み） |
| `web/app.py` | L24: import<br>L566, L725: 呼び出し | **変更**: `load_history_data()` に変更 |
| `analysis/history_accumulator.py` | L66: `accumulate_from_daily()` 定義<br>L463-465: 使用 | **実質廃止**: historyが常に最新なので accumulate 不要<br>後方互換のため関数は残す（中身は空処理） |
| `scripts/generate_verify.py` | L35-41: `load_daily_data` monkeypatch | **変更**: `load_history_data` に変更 |
| `scripts/backtest.py` | L254-258: `load_daily_data` monkeypatch | **変更**: `load_history_data` に変更 |

### 3. テンプレート (12ファイル)
| ファイル | 使用変数 | 変更の必要性 |
|---------|---------|------------|
| `web/templates/_macros.html` | `yesterday_art`, `yesterday_prob`, `yesterday_date`, `yesterday_history`<br>`day_before_*`, `three_days_ago_*` | **変更不要**（変数名同じ） |
| `web/templates/_unit_card.html` | 同上 | **変更不要** |
| `web/templates/index.html` | 同上 + `history_date`, `today_history` | **変更不要** |
| `web/templates/recommend.html` | 同上 + `daily_summary` | **変更不要** |
| `web/templates/ranking.html` | 同上 | **変更不要** |
| `web/templates/unit_history.html` | `history`, `history_sorted` | **変更不要** |
| `web/templates/verify.html` | `history`, `history_summary` | **変更不要** |
| `web/templates/history.html` | `history`, `history_date` | **変更不要** |
| 他4ファイル | - | **変更不要** |

**重要**: テンプレートは変数名が変わらないため、修正不要。recommender.py が history から yesterday_* を生成すればOK。

---

## 変更が必要なファイル一覧

### Phase 1: データ読み込みロジックの変更（最重要）
1. **`analysis/recommender.py`** (2箇所)
   - [ ] L298: `load_daily_data()` → `load_history_data()` に関数名変更
   - [ ] L300-350: 関数の中身を書き換え
     ```python
     # 変更前: data/daily/*.json を読み込み
     # 変更後: data/history/{store}/{unit_id}.json を全て読み込み
     ```
   - [ ] L2783: 呼び出し側も `load_history_data()` に変更
   - [ ] L2819-2833: unit_history 取得ロジックを history データ構造に合わせて変更

2. **`analysis/recommender.py` - `analyze_trend()` 関数** (L900-1050)
   - [ ] L979-981: `daily_results[0]` から yesterday を取得 → history の最新日から取得に変更
   - [ ] L995-1010: `sorted_days[0]` から yesterday_history 取得 → history データ構造に合わせる
   - [ ] L1013-1029: `sorted_days[1]` から day_before 取得 → 同上
   - [ ] L1032-1039: `sorted_days[2]` から three_days_ago 取得 → 同上

### Phase 2: 静的サイト生成の変更
3. **`scripts/generate_static.py`** (6箇所)
   - [ ] L30: `from analysis.recommender import recommend_units, load_history_data` に変更
   - [ ] L1318: `load_history_data(machine_key=machine_key)` に変更
   - [ ] L2196: 同上
   - [ ] L2745: `from analysis.history_accumulator import accumulate_from_daily` → import削除（不要）
   - [ ] L2748: `load_history_data(machine_key=mk)` に変更
   - [ ] L2750: `accumulate_from_daily()` 呼び出し削除（不要）

### Phase 3: Webアプリの変更
4. **`web/app.py`** (3箇所)
   - [ ] L24: `from analysis.recommender import recommend_units, load_history_data` に変更
   - [ ] L566: `load_history_data(machine_key=machine_key)` に変更
   - [ ] L725: 同上

### Phase 4: テストツールの変更
5. **`scripts/generate_verify.py`** (3箇所)
   - [ ] L35: `original_load = recommender.load_history_data` に変更
   - [ ] L41: `recommender.load_history_data = patched_load` に変更
   - [ ] L89: `recommender.load_history_data = original_load` に変更

6. **`scripts/backtest.py`** (6箇所)
   - [ ] L254-258: monkeypatch を `load_history_data` に変更

### Phase 5: 自動更新スクリプトの変更
7. **`scripts/auto_update.sh`**
   - [ ] L97-103: sync_realtime_to_history.py の呼び出しを**削除**
   - [ ] L115: `git add` から `data/history/` を削除（nightly_update.sh に移動）

8. **`scripts/nightly_update.sh`** (新規作成)
   ```bash
   #!/bin/bash
   # 毎日23:00に実行される夜間更新スクリプト

   # 1. availability.json から history/ への確定データ移行
   python3 scripts/sync_realtime_to_history.py

   # 2. 静的サイト再生成
   python3 scripts/generate_static.py

   # 3. コミット＆プッシュ
   git add data/history/ docs/
   git commit -m "auto: 北斗更新 $(date +'%H:%M')"
   git push origin main
   ```

9. **`crontab`** (ローカルサーバー)
   - [ ] 新規追加: `0 23 * * * /home/riichi/works/slot/scripts/nightly_update.sh`

### Phase 6: 廃止するファイル
10. **`scripts/daily_collect.py`**
    - [ ] ファイルを `_archive/` に移動（完全削除はしない）

11. **`.github/workflows/daily_collect.yml`**
    - [ ] ファイルを `_archive/workflows/` に移動

12. **`data/daily/` ディレクトリ**
    - [ ] `_archive/data/daily_backup_20260211/` にバックアップして削除
    - [ ] `.gitignore` に `data/daily/` を追加（今後作られないように）

### Phase 7: 後方互換性の維持
13. **`analysis/history_accumulator.py`**
    - [ ] L66: `accumulate_from_daily()` 関数を残すが、中身を空処理に
      ```python
      def accumulate_from_daily(daily_data: dict, machine_key: str = 'sbj'):
          """
          後方互換性のために残すが、historyが常に最新なので何もしない
          """
          print("WARN: accumulate_from_daily() は廃止されました。historyは常に最新です。")
          return {}
      ```

---

## 外部サービスへの影響

### GitHub Actions

#### 1. `daily_collect.yml`
- **現状**: 毎日 23:00 JST に実行（動いていない）
- **影響**: **廃止** → `_archive/workflows/` に移動
- **理由**: daily/ 自体を廃止するため不要

#### 2. `fetch-availability.yml`
- **現状**: JST 10:00-23:00 の毎時00分に実行
- **影響**: **変更なし**
- **理由**: availability.json のリアルタイム取得は継続

#### 3. `daily-verify.yml`
- **現状**: 毎日 23:30 JST に実行
- **影響**: **変更なし**
- **理由**: generate_verify.py が自動的に load_history_data() を使うようになる

### Google Apps Script (GAS)

#### `gas/availability.gs`
- **現状**: papimo.jp から空き状況を取得
- **影響**: **変更なし**
- **理由**: availability（空き/遊技中）のみ取得、daily/historyとは無関係

### ローカル cron

#### 現在の設定
```bash
*/10 10-22 * * * /home/riichi/works/slot/scripts/auto_update.sh
```

#### 変更後
```bash
# リアルタイム更新（10分おき、営業時間中）
*/10 10-22 * * * /home/riichi/works/slot/scripts/auto_update.sh

# 夜間確定データ更新（23:00に1回）
0 23 * * * /home/riichi/works/slot/scripts/nightly_update.sh
```

---

## テンプレートへの影響

### 結論: **全テンプレートで変更不要**

#### 理由
- テンプレートが使用する変数名は変わらない
  - `yesterday_art`, `yesterday_games`, `yesterday_prob`, `yesterday_date`
  - `day_before_art`, `day_before_games`, `day_before_date`
  - `three_days_ago_art`, `three_days_ago_games`, `three_days_ago_date`
  - `yesterday_history`, `day_before_history`, `three_days_ago_history`

- これらは全て `recommender.py` の `recommend_units()` が生成
- データソースが daily → history に変わるだけで、出力形式は同じ

### 使用状況の詳細

| テンプレート | 使用変数 | 備考 |
|-------------|---------|------|
| _macros.html | yesterday_*, day_before_*, three_days_ago_* | マクロで使用（変更不要） |
| _unit_card.html | 同上 | カード表示（変更不要） |
| index.html | 同上 + history_date, today_history | トップページ（変更不要） |
| recommend.html | 同上 + daily_summary | おすすめページ（変更不要） |
| ranking.html | 同上 | ランキング（変更不要） |
| unit_history.html | history, history_sorted | 台別履歴（変更不要） |
| verify.html | history, history_summary | 答え合わせ（変更不要） |
| history.html | history, history_date | 履歴詳細（変更不要） |

---

## ロールバック手順

### 緊急時の即時ロールバック（5分以内）

```bash
# 1. 変更前のコミットに戻る
cd /home/riichi/works/slot
git log --oneline -10  # 変更前のコミットハッシュを確認
git revert HEAD  # 最新コミットを打ち消し
git push origin main

# 2. daily データを復元
cp -r _archive/data/daily_backup_20260211/* data/daily/

# 3. 古い recommender.py に戻す
git checkout <変更前コミット> analysis/recommender.py
git add analysis/recommender.py
git commit -m "emergency: revert to load_daily_data()"
git push origin main

# 4. 静的サイト再生成
python scripts/generate_static.py
git add docs/
git commit -m "emergency: regenerate static site"
git push origin main
```

### 段階的ロールバック（問題の切り分け）

#### Step 1: recommender.py だけ戻す
```bash
git checkout <変更前コミット> analysis/recommender.py
python scripts/generate_static.py
```
→ おすすめページが正常に表示されるか確認

#### Step 2: auto_update.sh を戻す
```bash
git checkout <変更前コミット> scripts/auto_update.sh
# sync_realtime_to_history.py が10分ごとに実行される状態に戻る
```

#### Step 3: daily/ データを復元
```bash
cp -r _archive/data/daily_backup_20260211/* data/daily/
```

### バックアップの作成（変更前に必須）

```bash
# 変更前にタグを打つ
git tag -a before-daily-to-history -m "変更前バックアップ (2026-02-11)"
git push origin before-daily-to-history

# daily/ のバックアップ
mkdir -p _archive/data/daily_backup_20260211
cp -r data/daily/* _archive/data/daily_backup_20260211/

# recommender.py のバックアップ
cp analysis/recommender.py _archive/recommender_before_change_20260211.py
```

---

## 実装フェーズ

### フェーズ 0: 準備（変更前）
- [ ] このドキュメントをレビュー・承認
- [ ] バックアップ作成
  - [ ] Git tag: `before-daily-to-history`
  - [ ] `data/daily/` → `_archive/data/daily_backup_20260211/`
  - [ ] `analysis/recommender.py` → `_archive/recommender_before_change_20260211.py`
- [ ] テスト環境で動作確認

### フェーズ 1: データ読み込みロジック変更（最重要）
- [ ] `analysis/recommender.py` の変更
  - [ ] `load_daily_data()` → `load_history_data()` 関数作成
  - [ ] `analyze_trend()` を history データ構造に対応
  - [ ] 単体テスト実行
- [ ] ローカルで generate_static.py を実行して動作確認
- [ ] コミット: `feat: load_history_data() を追加`

### フェーズ 2: 他ファイルの変更
- [ ] `scripts/generate_static.py` の変更
- [ ] `web/app.py` の変更
- [ ] `scripts/generate_verify.py` の変更
- [ ] `scripts/backtest.py` の変更
- [ ] コミット: `feat: load_history_data() への移行完了`

### フェーズ 3: 自動更新スクリプト変更
- [ ] `scripts/nightly_update.sh` 作成
- [ ] `scripts/auto_update.sh` から sync_realtime_to_history.py 削除
- [ ] ローカル crontab に nightly_update.sh 追加
- [ ] コミット: `feat: nightly_update.sh 追加、auto_update修正`

### フェーズ 4: 動作確認（1日間）
- [ ] 23:00 に nightly_update.sh が実行されるか確認
- [ ] 翌朝、おすすめページで「昨日」が正しい日付になっているか確認
- [ ] history/ が23:00に更新されているか確認

### フェーズ 5: 廃止処理
- [ ] `scripts/daily_collect.py` → `_archive/` に移動
- [ ] `.github/workflows/daily_collect.yml` → `_archive/workflows/` に移動
- [ ] `data/daily/` → `_archive/data/daily_backup_20260211/` に移動
- [ ] `.gitignore` に `data/daily/` 追加
- [ ] コミット: `chore: daily関連ファイルを廃止`

### フェーズ 6: 最終確認
- [ ] 全ページで yesterday, day_before, three_days_ago が正しく表示されるか確認
- [ ] おすすめロジックが正常に動作しているか確認
- [ ] 1週間運用して問題ないか監視

---

## 備考

### データ構造の違い

#### daily/ の構造（現在、廃止予定）
```json
{
  "collected_at": "2026-01-28 06:45:00",
  "stores": {
    "akiba_espass_sbj": {
      "units": [
        {
          "unit_id": "2158",
          "days": [
            {
              "date": "2026-01-26",
              "art": 24,
              "bb": 0,
              "rb": 5,
              "total_start": 2840,
              "diff_medals": -1350,
              "history": [...]
            },
            ...
          ]
        }
      ]
    }
  }
}
```

#### history/ の構造（移行後のメイン）
```json
{
  "unit_id": "2158",
  "store": "akiba_espass_sbj",
  "machine": "sbj",
  "days": [
    {
      "date": "2026-02-10",
      "art": 24,
      "bb": 0,
      "rb": 5,
      "total_start": 2840,
      "diff_medals": -1350,
      "history": [...]
    },
    ...
  ],
  "last_updated": "2026-02-11 11:52:00"
}
```

### 利点
- **シンプル**: 3層構造（availability, daily, history）→ 2層構造（availability, history）
- **正確**: history が1日1回の確定データになる
- **保守性**: daily/ の更新が止まっても影響なし
- **一貫性**: history が「履歴の唯一の真実」になる

### リスク
- **移行期間**: yesterday が一時的に取得できない可能性
  - 対策: history/ に最低3日分のデータが揃っていることを確認してから実施
- **バグ混入**: load_history_data() の実装ミス
  - 対策: 十分なテストと段階的リリース

---

**最終更新**: 2026-02-11
**作成者**: Claude Sonnet 4.5
**レビュー**: 未実施
**承認**: 未実施
