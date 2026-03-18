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

## 現在の状態（2026-03-18）

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

### ⚠️ 現在の問題

#### 差枚データ一部欠損（非致命的）
- 一部台で `diff_medals=0` または `None` のまま
- 計算仕様: SBJ→`medals>0`のエントリで集計、hokuto2→`hit_num>0`のエントリで集計
- 影響: 差枚表示が出ないだけ（ランキング自体は正常）

### 🎯 次にやること

1. **翌日の自動更新を確認**
   - 12:28のリセット後、初回fast path → 2回目以降diff modeになるか確認
   - GitHub Actions ログ: `fetch-availability-v2.yml` の実行ログ

2. **データ品質モニタリング**
   - ゴーストエントリが再発しないか確認
   - `batch_update_history.py --check-only` で全台データ状況確認

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

### よくある問題の対処
| 症状 | 原因 | 対処 |
|------|------|------|
| 0ARTばかり | タイムアウト/daily_reset問題 | `fetch-availability-v2.yml`ログ確認 |
| データが古い | availability.json未更新 | 手動で`python3 scripts/scrapers_v2/fetch_all.py`実行 |
| ゴーストエントリ | art=0でgames=前日コピー | `batch_update_history.py`で削除 |
| 台データ取れない | 台変動の可能性 | `verify_units.py`で確認 → config更新 |

### 最新コミット（2026-03-18時点）
```
feb333c043 Merge remote-tracking branch 'origin/main'
dddab255fd fix: リセット後の初回fast path完了後は差分モードに切り替え（hit history取得を最適化）
```
