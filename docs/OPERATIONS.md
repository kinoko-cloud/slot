# スロットサイト 運用ガイド

## CI/CD構成

```
GitHub Actions (kinoko-cloud)
  ├─ DAIDATA（エスパス各店）
  │   - 渋谷エスパス
  │   - 新宿エスパス
  │   - 秋葉原エスパス
  │   - 西武新宿エスパス
  │
  └─ PAPIMO（アイランド系）
      - アイランド秋葉原
```

**注意:** Circle CIはアカウントBANのため使用停止

## データ取得フロー

```
┌─────────────────────────────────────────────────────────────┐
│                    正常時のフロー                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  GitHub Actions (origin)                                    │
│  ├─ Fetch Availability: 毎時00分 (10:00-23:00 JST)         │
│  ├─ Daily Verify: 毎日 14:30 JST                           │
│  └─ Daily Data Collection: 毎日 14:00 JST                  │
│                                                             │
│  → 失敗時: ヘルスチェックが検知 → 自己修復 or アラート     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## フォールバック戦略

### レベル1: 自動リトライ
- GitHub Actions内で2回リトライ
- 失敗してもビルドは続行（警告のみ）

### レベル2: ヘルスチェック自己修復
- 10:00, 14:00, 18:00, 22:00 に自動チェック
- 異常検知時:
  - ロックファイル削除
  - リアルタイムデータ再取得
  - 蓄積データ取得（バックグラウンド）
  - サイト再ビルド

### レベル3: 緊急モード（手動）
```bash
# 3並列で高速取得
python scripts/fetch_emergency.py --workers 3
```

### レベル4: 代替環境（未実装）
- Circle CI (secondary) で実行
- ローカルcronで定期実行

## アラート通知

### WhatsApp通知条件
1. リアルタイムデータが24時間以上古い
2. 4店舗以上の蓄積データが古い
3. GitHub Actionsが失敗

### 通知先
- +819030684797 (RSさん)

## ヘルスチェック項目

| 項目 | 正常条件 | チェック間隔 |
|------|---------|-------------|
| availability.json | 2時間以内 | 4時間ごと |
| 蓄積データ | 前日データあり | 4時間ごと |
| GitHub Actions | 失敗なし | 4時間ごと |

## 手動コマンド

### 状態確認
```bash
# ヘルスチェック実行
python scripts/health_check.py

# 詳細付き
python scripts/health_check.py --repair
```

### データ取得
```bash
# リアルタイムデータ
python scripts/fetch_daidata_availability.py

# 全店舗蓄積データ（通常）
python scripts/fetch_all_missing.py

# 緊急モード（並列）
python scripts/fetch_emergency.py --workers 3
```

### サイト再ビルド
```bash
python scripts/generate_static.py
```

## トラブルシューティング

### GitHub Actionsが失敗し続ける場合
1. エラーログ確認: https://github.com/kinoko-cloud/slot/actions
2. ローカルで同じスクリプトを実行してエラー再現
3. 修正してプッシュ
4. 必要なら緊急モードでデータ取得

### データが古いまま更新されない場合
1. `python scripts/health_check.py` で状態確認
2. ロックファイル確認: `ls -la /tmp/slot_fetch.lock`
3. 手動でデータ取得: `python scripts/fetch_emergency.py`

### リアルタイムデータだけ古い場合
1. `python scripts/fetch_daidata_availability.py` を手動実行
2. daidataサイトが落ちていないか確認
