# 2026-02-16 statusフィールド追加修正

## 📋 問題

**POST-BUILD ERROR**:
```
ERROR: 営業時間中(22時)なのに遊技中が0台（総台数183）→ データ未更新の可能性
```

**原因**:
- availability.jsonに`status`フィールドが存在しない（全183台）
- post_build_check.pyが`status == 'playing'`の台数をカウント
- 全台が`status`なしなので、遊技中が0台と判定される

---

## 🔍 根本原因の分析

### 1. fetch_realtime()が`status`を返していない

**ファイル**: `scripts/scrapers_v2/daidata/scraper.py` (133-138行目)

**問題のコード**:
```python
data = {
    'unit_id': unit_id,
    'bb': 0, 'rb': 0, 'art': 0,
    'total_start': 0, 'final_start': 0,
    # ← statusがない！
    'fetched_at': now_jst().isoformat()
}
```

### 2. スキップ時のみ`status`が設定される

**ファイル**: `scripts/scrapers_v2/fetch_all.py` (174行目)

**問題**:
```python
# G数変化なし → 前回のデータを使用
result['units'][unit_id] = {
    'unit_id': unit_id,
    'total_start': games,
    'art': prev_data.get('art', 0),
    ...
    'status': 'empty' if unit_id in result['empty'] else 'playing',  # ← ここだけ
}
```

**現状の問題点**:
- 全台取得（`changed_units = set(expected_units)`）の場合、全台がスキップされない
- スキップされない台は`status`が設定されない

---

## ✅ 実施した修正

### 修正1: DaidataScraperに`status`追加

**ファイル**: `scripts/scrapers_v2/daidata/scraper.py`

**変更箇所1** (133-138行目):
```python
data = {
    'unit_id': unit_id,
    'bb': 0, 'rb': 0, 'art': 0,
    'total_start': 0, 'final_start': 0,
    'status': 'empty',  # ← デフォルトは空台
    'fetched_at': now_jst().isoformat()
}
```

**変更箇所2** (198-202行目):
```python
# ステータス判定: データがあれば遊技中
if data['art'] > 0 or data['bb'] > 0 or data['rb'] > 0 or data['total_start'] > 0:
    data['status'] = 'playing'

# デバッグ: total_start=0でART>0は異常
if data['total_start'] == 0 and data['art'] > 0:
    ...
```

### 修正2: PapimoScraperに`status`追加

**ファイル**: `scripts/scrapers_v2/papimo/scraper.py`

**変更箇所1** (149-153行目):
```python
data = {
    'unit_id': unit_id,
    'date': date_str,
    'status': 'empty',  # ← デフォルトは空台
}
```

**変更箇所2** (204-208行目):
```python
# ステータス判定: データがあれば遊技中
if art > 0 or data.get('bb', 0) > 0 or data.get('rb', 0) > 0 or total_start > 0:
    data['status'] = 'playing'

return data if data.get('total_start', 0) > 0 else None
```

### 修正3: 空データ検証でも`status`を保持

**ファイル**: `scripts/scrapers_v2/fetch_all.py` (148-157行目)

**変更後**:
```python
if prev_data and (prev_data.get('art', 0) > 0 or prev_data.get('total_start', 0) > 0):
    # 前回データがあれば、それを保持（ただしG数は更新）
    logger.warning(f"{store_key}/{unit_id}: 空データ検知、前回データを保持")
    # statusも保持（遊技中判定のため）
    status = 'playing' if (prev_data.get('art', 0) > 0 or prev_data.get('total_start', 0) > 0) else 'empty'
    result['units'][unit_id] = {
        **prev_data,
        'total_start': games,
        'status': status,  # ← statusを明示的に設定
        'cached': True,
        'stale_warning': True,
    }
    result['skipped_count'] += 1
    continue
```

---

## 🎯 期待される結果

### 1. availability.jsonに`status`が存在

**修正前**:
```json
{
  "unit_id": "2505",
  "art": 0,
  "total_start": 0
  // statusなし
}
```

**修正後**:
```json
{
  "unit_id": "2505",
  "art": 0,
  "total_start": 0,
  "status": "empty"  // ← 追加
}
```

### 2. 遊技中判定が正常に機能

- `art > 0` または `total_start > 0` → `status: "playing"`
- それ以外 → `status: "empty"`

### 3. POST-BUILD ERRORが解消

- 営業時間中に遊技中の台が正しくカウントされる
- エラーメッセージが表示されなくなる

---

## 📝 テスト方法

### 1. データ取得
```bash
timeout 900 python3 scripts/scrapers_v2/fetch_all.py
```

### 2. availability.json確認
```python
python3 -c "
import json
from pathlib import Path

avail = json.loads(Path('data/availability.json').read_text())

status_count = {}
for store_key, store_data in avail['stores'].items():
    for unit in store_data.get('units', []):
        status = unit.get('status', 'なし')
        status_count[status] = status_count.get(status, 0) + 1

print('status分布:')
for status, count in sorted(status_count.items()):
    print(f'  {status}: {count}台')
"
```

**期待結果**:
```
status分布:
  empty: 22台
  playing: 161台
```

### 3. HTML生成・検証
```bash
python3 scripts/generate_static.py
```

**期待結果**: POST-BUILD ERRORが出ない

---

## 📚 関連ファイル

- `scripts/scrapers_v2/daidata/scraper.py` - DaidataScraperのstatus追加
- `scripts/scrapers_v2/papimo/scraper.py` - PapimoScraperのstatus追加
- `scripts/scrapers_v2/fetch_all.py` - 空データ検証時のstatus保持
- `scripts/post_build_check.py` - 遊技中判定ロジック

---

**作業日時**: 2026-02-16 22:25-22:35
**修正ファイル数**: 3ファイル
