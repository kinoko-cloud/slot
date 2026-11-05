# HANDOFF.md - 作業引き継ぎ

**最終更新:** 2026-02-19 08:55 JST

---

## 現在の状態

### 本日実施した変更（2026-02-19）

#### 1. GitHub Actions スケジュール改善
- **30分間隔の交互実行**: メイン(0分) / セカンダリ(30分)
- **営業時間**: 10:00-23:00対応

#### 2. 店舗の主要/サブ分割
**主要店舗（30分間隔）:** 渋谷系、新宿系、秋葉原系（6店舗）
**サブ店舗（22:50/00:10）:** 赤坂、上野、高田馬場、新大久保、新小岩（6店舗）

#### 3. v3効率化をメインスクレイパーに適用
- 規約同意の毎回チェック
- 実行時間短縮

### 新規オプション（fetch_all.py）
```bash
# 主要店舗のみ（メイン/セカンダリworkflowで使用）
python scripts/scrapers_v2/fetch_all.py --priority-only --sbj-only
python scripts/scrapers_v2/fetch_all.py --priority-only --hokuto-only

# サブ店舗のみ（サブworkflowで使用）
python scripts/scrapers_v2/fetch_all.py --sub-only --sbj-only
python scripts/scrapers_v2/fetch_all.py --sub-only --hokuto-only
```

---

## 未修正バグ

### 店舗個別ページの表示バグ
**URL例:** `https://slot-e8a.pages.dev/recommend/island_akihabara_sbj`

1. **日付重複表示**: 同じ日が2行表示
2. **前日/前々日が同じ日付**: バグ
3. **確率の微妙な違い**: 1/119 vs 1/118

**修正対象:** `analysis/recommender.py` の日付処理

---

## 10時以降の確認ポイント

1. discovery（台番号チェック）が動くか
2. 主要店舗のデータ取得が正常か
3. 表示が正しく切り替わるか
4. v3効率化の効果（実行時間短縮）

---

## ファイル構成

### Workflows
- `.github/workflows/fetch-availability-v2.yml` - メイン（毎時0分）
- `.github/workflows/fetch-availability-secondary.yml` - セカンダリ（毎時30分）
- `.github/workflows/fetch-availability-sub.yml` - サブ（22:50/00:10）

### スクレイパー
- `scripts/scrapers_v2/fetch_all.py` - v2統合スクレイパー
- `scripts/scrapers_v2/daidata/scraper.py` - daidataスクレイパー
- `scripts/fill_missing_history_v3.py` - 欠落履歴補完（手動用）

### 店舗定義
- `PRIORITY_STORES` / `SUB_STORES` → `scripts/scrapers_v2/fetch_all.py`

---

## 次のタスク

1. [ ] 10時以降の動作確認
2. [ ] 表示バグの修正（トップページ基準に統一）
3. [ ] 実行時間の計測・最適化
