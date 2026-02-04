# 新店舗追加ガイド

## 概要

新店舗を追加する際の手順。システムは以下の設計原則に基づいている：

1. **店舗定義は `config/rankings.py` に一元管理**
2. **蓄積DBは `data/history/{store_key}_{machine}/` に自動保存**
3. **全台系分析は蓄積DBを自動スキャン**（新店舗は自動で対象になる）
4. **予測ロジックは config/rankings.py を参照**（新店舗は自動で対象になる）

## 追加手順

### 1. config/rankings.py に店舗定義を追加

```python
# STORES辞書に追加
'new_store_sbj': {
    'name': '新店舗名',
    'short_name': '短縮名',
    'machine': 'sbj',  # or 'hokuto_tensei2'
    'units': ['1001', '1002', '1003', ...],  # 台番号リスト
    'site7_id': '12345',  # サイトセブンの店舗ID（任意）
    'papimo_url': 'https://...',  # パピモURL（任意）
},
```

### 2. データ収集スクリプトに追加

`scripts/daily_collect.py` の対象店舗リストに追加。

### 3. 初回データ収集

```bash
python scripts/daily_collect.py --store new_store_sbj
```

### 4. 自動で有効になるもの

- **予測**: 1日分のデータで予測対象に
- **全台系分析**: 3日分のデータで分析対象に
- **曜日パターン**: 7日分のデータで分析対象に

## ヘルパースクリプト

```bash
# 新店舗追加のテンプレート生成
python scripts/add_store.py \
    --name "マルハン新宿" \
    --key "maruhan_shinjuku" \
    --machine sbj \
    --units 1001-1020

# dry-runで確認
python scripts/add_store.py --dry-run ...
```

## 店舗キーの命名規則

```
{店舗名}_{機種}
例:
- island_akihabara_sbj
- shinjuku_espass_hokuto_tensei2
```

## 注意事項

- 店舗キーは一度決めたら変更しない（履歴データのパスに使用）
- 機種ごとに別の店舗キーを使う（同じ店でもSBJと北斗は別）
- 台番号は文字列で統一（'1001'、'2058'など）
