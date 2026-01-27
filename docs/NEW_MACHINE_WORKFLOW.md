# 新機種追加ワークフロー

新しいスロット機種をシステムに追加する際のチェックリストと手順。

## 1. 機種パラメータ設定 (`config/rankings.py`)

`MACHINES` ディクショナリに新機種を追加する。

### 必須パラメータ

| パラメータ | 説明 | 例（SBJ） |
|-----------|------|----------|
| `name` | 正式名称 | `'Lスーパーブラックジャック'` |
| `short_name` | 短縮名 | `'スーパーブラックジャック'` |
| `display_name` | 表示名 | `'スーパーブラックジャック'` |
| `icon` | 絵文字アイコン | `'🃏'` |
| `good_prob` | 好調判定ART確率閾値 | `130` |
| `bad_prob` | 不調判定ART確率閾値 | `150` |
| `very_bad_prob` | 明確に低設定の確率閾値 | `200` |
| `typical_daily_games` | 1日あたりの一般的な消化G数 | `6500` |

### 天井パラメータ（朝イチ恩恵）

| パラメータ | 説明 | 例（SBJ） | 例（北斗転生2） |
|-----------|------|----------|---------------|
| `normal_ceiling` | 通常天井（G数） | `800` | `1500` |
| `reset_ceiling` | リセット時天井（朝イチ天井） | `600` | `600` |
| `reset_first_hit_bonus` | 朝イチ初当たりに恩恵あり | `True` | `True` |

※ `reset_ceiling < normal_ceiling` の場合、朝イチ（設定変更/リセット後）に天井が短縮される恩恵がある。
※ 正確な天井値は機種情報サイト（ちょんぼりすた等）で確認すること。

### テンプレート

```python
'new_machine_key': {
    'name': 'L新機種名',
    'short_name': '新機種名',
    'display_name': '新機種',
    'icon': '🎰',
    'good_prob': 200,       # ← 機種の確率体系に合わせて設定
    'bad_prob': 250,
    'very_bad_prob': 350,
    'typical_daily_games': 5000,
    'normal_ceiling': 999,
    'reset_ceiling': 999,   # ← リセット恩恵がない場合は通常天井と同じ
    'reset_first_hit_bonus': False,
},
```

## 2. 店舗設定 (`config/rankings.py`)

`STORES` ディクショナリに店舗×機種のエントリを追加。

```python
'store_name_machine_key': {
    'name': '店舗正式名',
    'short_name': '店舗略称',
    'hall_id': '100XXX',        # daidataのhall_id（パピモの場合はNone）
    'machine': 'new_machine_key',
    'units': ['1001', '1002', '1003'],  # 台番号リスト
    'data_source': 'daidata',   # 'daidata' or 'papimo'
},
```

## 3. スクレイパー対応

### daidata系店舗
- `scrapers/daidata_scraper.py` — 既存ロジックで対応（hall_idベース）
- 機種名の照合: スクレイパーがdaidataの機種名を認識できるか確認

### papimo系店舗
- `scrapers/papimo_scraper.py` — 台番号レンジの設定が必要な場合あり
- パピモは機種名ではなく台番号で管理

### 確認コマンド
```bash
# daidata
python -m scrapers.daidata_scraper --store store_name_machine_key --test

# papimo
python -m scrapers.papimo_scraper --store store_name_machine_key --test
```

## 4. 分析パラメータ

### `analysis/analyzer.py`
- `SBJ_ART_PROB` / 設定別ART確率 — 新機種用の確率テーブルが必要な場合は追加
- `SBJ_CEILING` — 天井値。機種横断的な天井はここを参照
- `RENCHAIN_THRESHOLD` — 連チャン判定閾値（70G）は機種共通

### `analysis/recommender.py`
- `calculate_expected_profit()` — 機種別の差枚計算ロジックを確認
- 新機種の出玉率テーブルが必要な場合は追加

## 5. テンプレート・表示

- `web/templates/index.html` — 機種アイコンと表示名は `MACHINES` から自動取得
- 機種固有の表示要素がある場合のみテンプレート修正が必要

## 6. テスト手順

```bash
# 1. Pythonシンタックスチェック
python -c "from config.rankings import MACHINES; print(MACHINES.keys())"

# 2. CSS未定義クラスチェック
python scripts/check_css.py

# 3. 静的サイト生成テスト
python scripts/generate_static.py

# 4. ローカルプレビュー
python -m http.server 8080 -d docs

# 5. データスクレイピングテスト
python -m scrapers.daidata_scraper --store <store_key> --test
```

## 7. デプロイ

```bash
git add -A
git commit -m "feat: 新機種 <機種名> 追加"
git push origin main
```

GitHub Actions が自動でデプロイを実行。

## チェックリスト

- [ ] `config/rankings.py` — MACHINES に機種パラメータ追加
- [ ] `config/rankings.py` — STORES に店舗×機種エントリ追加
- [ ] 天井パラメータ（normal_ceiling, reset_ceiling, reset_first_hit_bonus）設定
- [ ] スクレイパーで台番号データが取得できることを確認
- [ ] `check_css.py` でCSS未定義なし
- [ ] `generate_static.py` で生成成功
- [ ] ローカルプレビューで表示確認
- [ ] git commit & push
