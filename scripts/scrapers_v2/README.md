# scrapers_v2 - スクレイピングシステム v2

## 概要

データ取得システムのリファクタリング版。以下の問題を解決：

- 重複コードの排除
- 設定の一元管理
- テスト容易性の向上
- エラー処理の標準化

## 構成

```
scrapers_v2/
├── common/
│   └── base.py        # 共通基盤（BaseScraper, DataStore）
├── daidata/
│   └── scraper.py     # daidata.goraggio.com用
├── papimo/
│   └── scraper.py     # papimo.jp用
├── config.py          # 設定（店舗・機種定義）
└── README.md
```

## 使用例

### daidata - リアルタイムデータ

```python
from scrapers_v2.daidata.scraper import DaidataScraper

scraper = DaidataScraper(headless=True)
result = scraper.fetch(
    hall_id='100949',      # 新宿エスパス
    unit_ids=['682', '683'],
    mode='realtime'
)
```

### daidata - 詳細履歴

```python
result = scraper.fetch(
    hall_id='100949',
    unit_ids=['682'],
    mode='history'
)
```

### papimo

```python
from scrapers_v2.papimo.scraper import PapimoScraper

scraper = PapimoScraper(headless=True)
result = scraper.fetch(
    store_key='island_akihabara',
    machine_keys=['sbj']
)
```

## 設計方針

### BaseScraper

- Playwrightの初期化・終了を管理
- `browser_session()` コンテキストマネージャでリソース管理
- `navigate()` でリトライ付きページ遷移

### DataStore

- JSON読み書きの標準化
- 履歴データのマージ処理

### 設定

- `config.py` で店舗・機種を一元管理
- 既存の `config/stores.py` と連携

## TODO

- [ ] 統合スケジューラ作成
- [ ] エラー通知（Slack/Discord）
- [ ] データ検証・整合性チェック
- [ ] 並列取得対応
- [ ] テスト追加

## 移行計画

1. v2を開発・テスト（現行v1は残す）
2. v2で安定動作を確認
3. auto_update.shをv2に切り替え
4. v1を削除

## 既知の問題

- daidata: 広告オーバーレイが規約同意ボタンを覆う場合がある
- papimo: ページ構造が頻繁に変わる可能性
