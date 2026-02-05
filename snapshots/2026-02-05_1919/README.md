# スナップショット: 2026-02-05 19:19

## 概要
TOP10おすすめ台のレイアウト・デザイン

## 特徴
- TOP3: 大きなカード、スパークライングラフ付き
- 4-10位: コンパクトカード
- 11位以降: 「もっと見る」セクション
- 前日・前々日データ表示あり
- ランク別色分け（S/A/B/C）

## ファイル
- index.html: Jinja2テンプレート
- style.css: スタイルシート

## 復元方法
```bash
cp snapshots/2026-02-05_1919/index.html web/templates/
cp snapshots/2026-02-05_1919/style.css web/static/
python3 scripts/generate_static.py
```
