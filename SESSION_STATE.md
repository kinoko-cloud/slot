# Slot - セッション状態管理（常に最新）

## ⚠️ Claudeへの指示（最重要）

**トリガーワード**: 「slot」「スロット」「続き」「再開」

**ユーザーがトリガーワードを言ったら：**
1. このファイルを黙って読む
2. `git log --oneline -3` で最新コミットを確認
3. 「続きを進めます。」（1行のみ）
4. すぐに作業開始

**作業終了時のルール**:
ユーザーが「終了」「やめる」「離れる」「また」と言ったら：
1. このファイルを現状に更新する
2. 「SESSION_STATE.mdを更新しました。次回「slot」と言えば再開できます。」

---

## 現在の状態（2026-04-21）

### ✅ 完了した重要修正

#### 毎日の更新停止問題（根本解決 2026-03-11〜3/12）
- **原因**: `games_cache.py` が毎日0時にキャッシュを無効化 → 全台フェッチ → タイムアウト
- **修正**: `daily_reset` を `save_availability()` で引き継ぐ + `_is_games_cache_fresh()` で2回目以降を差分モードに切り替え
- **結果**: 初回fast path（タイムアウトなし）→ 2回目以降diff mode（hit history取得）

#### ゴーストエントリ問題（2026-03-18解決）
- **原因**: タイムアウト後にart=0のデータが保存され、翌日も同じgamesなら前日コピーとして蓄積
- **修正**: 3箇所に防止ロジック追加（`history_accumulator.py`, `sync_realtime_to_history.py`, `update_history_from_availability.py`）
- **過去分680件を削除済み**

#### もっと見るのID重複バグ（2026-03-18解決）
- **原因**: `document.getElementById('more-recs-rt')` が旧HTMLで2回生成されていたため最初の要素しか制御できなかった
- **修正**: `nextElementSibling` 方式に変更 → IDに依存しない相対DOM参照

#### 台変動対応（3/2, 3/4, 3/17確認済み）
- `config/rankings.py` + `scripts/fetch_daidata_availability.py` 両方更新済み
- 詳細は `memory/MEMORY.md` の2026-03-12セクション参照

### ✅ 2026-04-21 完了: 機種変更対応

#### hokuto2を全面削除・真打吉宗・ToLOVEるを追加
- **config/rankings.py**: MACHINES/STORESからhokuto2削除。yoshitsune・toloveru追加。アイランド秋葉原エントリ追加
- **scripts/fetch_daidata_availability.py**: PAPIMO_STORESにisland_akihabara_yoshitsune・island_akihabara_toloveru追加
- **scripts/scrapers_v2/papimo/scraper.py**: hokuto2→yoshitsune/toloveru更新
- **scripts/scrapers_v2/config.py**: SCRAPE_TARGETS・MACHINE_CONFIG更新
- **scripts/generate_static.py**: machine_links・_get_machine_key更新
- **渋谷本館エスパスは閉店済み** → configから削除完了

#### アイランド秋葉原 papimo台番号（2026-04-21確認）
- 真打吉宗(226030000): 0637,0638,0650〜0653,0655〜0658（10台）
- ToLOVEるDARKNESS(224040005): 1227〜1288系（42台）
- SBJ(225010000): 1015〜1031系（14台）変更なし

### ✅ GitHub Actions 一時停止（2026-04-17）
- 全スケジュールワークフローを一時停止済み
- 再開時: `gh workflow enable <name>` で復旧

### ✅ 2026-04-21 完了: 予測閾値・仕様設定

- **config/rankings.py**: yoshitsune/toloveruの閾値・天井を実データ基準に最終設定
  - yoshitsune: good=1/90, bad=1/150, very_bad=1/220, ceiling=1500G, reset=1000G
  - toloveru: good=1/290, bad=1/380, very_bad=1/460, ceiling=999G, reset=650G
- **scrapers_v2/config.py**: MACHINE_CONFIGの閾値・天井を同期
- **validate_output.py**: expected_keysをyoshitsune/toloveruに更新
- **CLAUDE.md**: 機種仕様セクションに真打吉宗・ToLOVEる追記、hokuto2を「撤去済み参考情報」に変更
- **docs/**: 真打吉宗・ToLOVEるの全ページ（ranking/recommend/history/machine）を新規生成

### 🎯 次にやること
1. **ワークフローを再開するか確認・対応**
   - SBJ + 真打吉宗 + ToLOVEるの3機種でワークフロー再開可能
   - `gh workflow enable fetch-availability-v2.yml` で再開
   - yoshitsune/toloveru対応済み（fetch_all.py, scrapers_v2/config.py）
2. **デザイン実験の差し戻し** 
   - ユーザーから質問があったが内容未確認
   - 必要なら `docs/test/frontend_design_preview.html` を確認

---

## 重要情報

### システム構成
- **データ取得**: GitHub Actions（`fetch-availability-v2.yml`毎時、`nightly-update.yml`毎日23時）
- **サイト**: Cloudflare Pages（`docs/`ディレクトリから自動デプロイ）
- **リポジトリ**: `kinoko-cloud/slot`

### 主要ファイル
| ファイル | 役割 |
|---------|------|
| `scripts/scrapers_v2/fetch_all.py` | データ取得メイン（最重要） |
| `scripts/generate_static.py` | 静的サイト生成 |
| `analysis/history_accumulator.py` | 履歴蓄積ロジック |
| `config/rankings.py` | 店舗・台番号設定 |
| `data/availability.json` | リアルタイムデータ（毎時更新） |
| `scripts/fetch_daidata_availability.py` | DAIDATA_STORES + PAPIMO_STORES定義 |

### 店舗・機種構成（2026-04-21現在）
| 店舗 | SBJ | 真打吉宗 | ToLOVEる | データ源 |
|------|-----|---------|---------|---------|
| アイランド秋葉原 | ✅ | ✅ | ✅ | papimo |
| エスパス新宿 | ✅ | ✅ | ✅ | daidata |
| エスパス秋葉原 | ✅ | ✅ | ✅ | daidata |
| エスパス西武新宿 | ✅(空) | ✅ | ✅ | daidata |
| エスパス渋谷新館 | ✗(撤退) | ✅ | ✅ | daidata |

### よくある問題の対処
| 症状 | 原因 | 対処 |
|------|------|------|
| 0ARTばかり | タイムアウト/daily_reset問題 | `fetch-availability-v2.yml`ログ確認 |
| データが古い | availability.json未更新 | 手動で`python3 scripts/scrapers_v2/fetch_all.py`実行 |
| ゴーストエントリ | art=0でgames=前日コピー | `batch_update_history.py`で削除 |
| 台データ取れない | 台変動の可能性 | `verify_units.py`で確認 → config更新 |
