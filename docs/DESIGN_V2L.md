# デザイン v2l - 固定版（2026-02-06）

## 概要
RSさん承認済みの最終デザイン。同様の見せ方が必要な場面で使用する。

## レイアウト構造

### 1行目（top3-stats-main）
```
ART 96  RB 4  合成1/86  累計8,311G
```
- ART: ラベル左、数字大（2.2em）、色変化（rainbow/gold/hot）
- RB: ラベル左、数字（1.6em）
- 合成: ラベル+数字（1.0em）
- 累計: ラベル+数字（0.95em）+G
- ベースライン揃え

### 2行目（top3-stats-sub）
```
スタート数 0G  ✨最大 4,051枚  差枚 -4,236枚
```
- スタート数: ラベル+数字（1.6em）+G
- 最大枚数: アイコン+ラベル+数字（1.6em）+枚
- 差枚: ラベル+数字（1.6em）+枚（plus/minus色分け）

### 3行目（top3-setting）
```
機械割122.2%  最大22連  ▼詳細
```
- 機械割: 数字+%（%小さめ0.7em）
- 最大連: ラベル+数字（1.6em）+連
- 詳細ボタン: 右端

### 台番号
```
1022番台
```
- 数字: 1.6em
- 「番台」表記

## CSS追加分
`web/static/style.css` の末尾に追加するCSS（v2l用）

## HTML構造変更点
- RB: `<span class="rb-label">RB</span> <span class="rb-num">4</span>`
- 累計1行目移動: `<span class="total-games-inline">...</span>`
- スタート数: `<span class="start-label">スタート数</span> <span class="start-num">0</span><span class="start-g">G</span>`
- 最大枚数: `<span class="max-label">最大</span> <span class="max-num">4,051</span><span class="max-unit">枚</span>`
- 差枚: `<span class="diff-label">差枚</span> <span class="diff-num">-4,236</span><span class="diff-unit">枚</span>`
- 最大連: `<span class="rensa-inline">...</span>`
- 詳細: 機械割の行に移動

## 注意事項
- モード変更で空き台表示など一部変更あり
- テンプレート更新時は既存コードとの整合性を確認
