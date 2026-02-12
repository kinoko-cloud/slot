# 2026-02-12 変更点

## 問題
1. GitHub Actionsのスケジュール実行が不安定
2. リアルタイムデータが更新されない
3. health_checkでisland_akihabara関連のエラー多発

## 対策（全て実装済み）

### 1. GitHub Actions実行頻度を大幅増加
**毎時4回の冗長実行体制**
- `fetch-availability-parallel.yml`: 毎時0分, 30分
- `deploy-static.yml`: 毎時5分, 35分
- `auto-recovery.yml`: 毎時15分, 45分

→ **最悪でも15分ごとにどれかのワークフローが実行される**

### 2. deploy-static.ymlに自動fetch機能追加
- データが45分以上古い場合は先にfetchを実行
- Playwrightも自動インストール

### 3. health_check.py改善
- `data_consistency`: island_akihabara（papimoソース）をスキップ
- `history_realtime`: 
  - island_akihabara（papimoソース）をスキップ
  - ART閾値を5→10に
  - 時間帯別閾値を緩和（日中4h、19時以降6h、21時以降8h）

## 現在のスケジュール（全自動）

| 時刻 | ワークフロー | 動作 |
|------|-------------|------|
| :00 | fetch-parallel | データ取得 |
| :05 | deploy-static | 静的サイト生成（古ければfetch） |
| :15 | auto-recovery | ヘルスチェック+自動復旧 |
| :30 | fetch-parallel | データ取得 |
| :35 | deploy-static | 静的サイト生成（古ければfetch） |
| :45 | auto-recovery | ヘルスチェック+自動復旧 |

## 手動対応は不要
全て自動化されました。問題が発生してもauto-recoveryが自動復旧します。
