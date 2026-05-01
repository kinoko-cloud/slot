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

## 現在の状態（2026-05-01）

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
- **config/rankings.py**: MACHINES/STORESからhokuto2削除。yoshimune・toloveru追加。アイランド秋葉原エントリ追加
- **scripts/fetch_daidata_availability.py**: PAPIMO_STORESにisland_akihabara_yoshimune・island_akihabara_toloveru追加
- **scripts/scrapers_v2/papimo/scraper.py**: hokuto2→yoshimune/toloveru更新
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
- **注意**: daidataはCloudFront WAFがGitHub Actions（AWS IP）をブロックするため、ローカルcronで代替

### ✅ 2026-04-21 完了: 予測閾値・仕様設定

- **config/rankings.py**: yoshimune/toloveruの閾値・天井を実データ基準に最終設定
  - yoshimune: good=1/90, bad=1/150, very_bad=1/220, ceiling=1500G, reset=1000G
  - toloveru: good=1/290, bad=1/380, very_bad=1/460, ceiling=999G, reset=650G
- **scrapers_v2/config.py**: MACHINE_CONFIGの閾値・天井を同期
- **validate_output.py**: expected_keysをyoshimune/toloveruに更新
- **CLAUDE.md**: 機種仕様セクションに真打吉宗・ToLOVEる追記、hokuto2を「撤去済み参考情報」に変更
- **docs/**: 真打吉宗・ToLOVEるの全ページ（ranking/recommend/history/machine）を新規生成

### ✅ 2026-04-27 完了: daidataエスパス全店舗の取得修復

#### 根本原因: CloudFront WAFがGitHub Actions（AWS IP）をブロック
- **症状**: 5週間以上、daidataエスパス全店舗でデータ取得失敗
- **原因**: `daidata.goraggio.com` のAWS CloudFront WAFがGitHub Actions（AWS IP）からのアクセスをブロック
- **解決策**: ローカルWSL（日本IP）からcronで実行する `scripts/local_daidata_cron.sh`
- **crontab設定**: `30 1-14 * * * bash /home/riichi/works/slot/scripts/local_daidata_cron.sh >> /tmp/slot_local_cron.log 2>&1`

#### daidataスクレイパー改善（`scripts/scrapers_v2/daidata/scraper.py`）
- `_accept_terms`: unit_listページにいる場合は規約処理スキップ（8-10秒節約）
- `fetch_list_with_availability`: `wait_for_selector('table tr td', timeout=30000)` + networkidle fallback
- DIAG logging追加（空テーブル時にURL/TEXTをログ出力）

#### 2026-04-27 取得成功確認（初回）
- **yoshimune**: shinjuku14/akiba10/seibu5/shibuya8台（計37台）498秒
- **toloveru**: shinjuku3/akiba30/shibuya3台（計36台）528秒
- **SBJ**: shinjuku4/akiba3台
- data/history/に全yoshimune/toloveru espassファイル初回生成完了

#### cronスクリプトのタイムアウト設定
- SBJ: 240s（実測~60s）
- yoshimune: 600s（実測498s）
- toloveru: 600s（実測528s）
- 次回以降はgames_cacheヒットで大幅短縮される見込み

### ✅ 2026-05-01 完了: CloudFront WAF検証・deploy-static.yml修正

#### VPN Gate検証結果（GitHub Actions上で実施）
- **結論**: CloudFront WAFは住宅系IP以外すべてをブロック
  - ローカルPC（Sony Network Communications、住宅系）→ 302 ✅
  - GitHub Actions（Azure IP）→ 403 ❌
  - VPN Gate接続後（AS36599 SoftEther Research、学術IP）→ 403 ❌ （VPN接続は成功、でもブロック）
  - 公開プロキシ（各種データセンターIP）→ 403 ❌
- **対応**: 無料プロキシでの回避は不可能。住宅系IPのみが通過できる。

#### deploy-static.yml修正
- daidataフェッチ試みを全削除（どうせ403で失敗→古いHTMLを生成してしまっていた）
- HTML生成のみに特化。データ取得は別途担当：
  - **daidata (エスパス)**: ローカルcron専任（住宅系IP必須）
  - **papimo (アイランド秋葉原)**: papimo-fetch.yml

#### local_daidata_cron.sh改善
- `git push --force` を廃止（他のコミットを上書きする問題があった）
- JSONスマートマージ（rebase失敗時: data/とdocs/のJSONファイルを intelligentにマージ）

### 🎯 次にやること
1. **papimo-fetch.ymlを再開（任意）**
   - アイランド秋葉原のデータ取得をGitHub Actions経由で再開可能
   - `gh workflow enable papimo-fetch.yml`
2. **crontabが正常動作しているか確認**
   - `/tmp/slot_local_cron.log` でログ確認
   - data/availability.jsonのfetched_atが毎時30分頃に更新されているか確認
3. **デザイン実験の差し戻し** 
   - 必要なら `docs/test/frontend_design_preview.html` を確認

---

## 重要情報

### システム構成（2026-05-01更新）
- **データ取得 (daidata/エスパス)**: ローカルcron専任 `scripts/local_daidata_cron.sh`（住宅系JP IP必須）
- **データ取得 (papimo/アイランド)**: GitHub Actions `papimo-fetch.yml`（一時停止中）
- **HTML生成**: GitHub Actions `deploy-static.yml`（データ取得は一切しない、HTML生成のみ）
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
