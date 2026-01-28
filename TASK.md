# TASK.md — 作業指示

**更新日時:** 2026-01-28 17:30 JST
**指示元:** RSさん（WhatsApp経由）

---

## 最優先: HANDOFF_20260128.md を読む

まず `/home/riichi/works/slot/HANDOFF_20260128.md` を読んでください。
今日のRSさんとの全議論・指示・分析結果が書いてあります。

---

## 作業指示

### 1. store_pattern.py の効果検証
- バックテスト（1/27）を再実行して、pattern_bonus追加前後で的中率・カバー率・F1がどう変わったか確認
- 結果をWhatsApp側（Ren）に報告

### 2. スコア分散の改善
- 北斗は74台中63台がスコア50-65に集中 → 差がつかない
- pattern_bonusで改善されたか確認
- 改善不足なら、スコアリングの重み調整を検討

### 3. 据え置き率のスコア直接反映
- store_pattern.pyのcalculate_pattern_bonusで据え置き率は含まれてるが、
  前日好調台 × 据え置き率の重み付けが十分か確認
- 例: アイランド北斗（据え置き率20%）→ 前日好調台にはボーナスほぼなし
- 例: エスパス秋葉原北斗（据え置き率67%）→ 前日好調台にしっかりボーナス

### 4. 的中率定義の統一
- 全台ベース: 的中 = (S/A予測→好調) + (B以下予測→不調)
- generate_static.pyは修正済み
- backtest.pyのsummarize_resultsもこの定義に合わせる

---

## 作業ルール
- 変更時は「なぜそうしたか」の背景をコミットメッセージに記載
- recommender.py / generate_static.py / store_pattern.py を変更したらビルド確認
- `python3 scripts/generate_static.py` でビルド通ること
- git commit & push

---

## 完了後
このファイルの該当タスクに ✅ を付けて、結果サマリーを追記してください。
