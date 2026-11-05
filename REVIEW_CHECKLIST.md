# レビューチェックリスト

修正時に必ず確認すること。過去の全指摘を統合。

---

## 🔴 修正前（必須）

- [ ] 問題のあるページのスクショを撮る（CDN版）
- [ ] 同じ問題が他のテンプレート/ページにもないか横断チェック
- [ ] **既存コードを確認してから着手**（ARCHITECTURE.md参照）
- [ ] 同じ仕組みが既に存在しないか検索する

## 🟢 修正後（必須）

- [ ] 全テンプレート(index/ranking/recommend/verify/unit_history)で同じ修正が必要か確認
- [ ] CSS変更は全ページに影響するか確認
- [ ] JS変更はrealtime.jsとインラインスクリプト両方に必要か確認
- [ ] `python3 scripts/generate_static.py` でビルド
- [ ] `python3 scripts/validate_output.py` でチェック通過
- [ ] push後、CDN反映（1-2分）を待ってから実際のURLで確認

---

## 📋 データ表示チェック（過去指摘統合）

### 連チャン表記がある
- [ ] 展開詳細テーブルに「連」列がある（chain_pos / N連）
- [ ] 直近データ行に連チャン数（rensa-sm）が表示される
- [ ] 全データパス（index/recommend/verify/ranking）で連チャン表示
- [ ] `_calc_history_stats()` は1hitの最大値ではなく**連チャン合計枚数**を返す

### 天井判定がRB跨ぎ累計
- [ ] 天井行（class=tenjou）に `acc-games` / 累計G数が表示される
- [ ] accumulated_gamesベース（RBを跨いで累計）で天井判定される
- [ ] 天井閾値は機種別（SBJ=936, 北斗=963 等）

### 差枚は蓄積DB優先
- [ ] `diff_medals` は蓄積DB（data/history/）を最優先
- [ ] `estimate_diff_medals()` はフォールバックのみ（符号すら逆になることがある）
- [ ] 差枚表示が**全カード**にある（ART 5回以上の日）
- [ ] 5つのデータパス全てで差枚が補完される:
  1. recommendページ
  2. indexのtop3/sa_recs
  3. indexのyesterday_top10
  4. indexのrecent_days
  5. rankingページ

### max_medalsは連チャン合計
- [ ] `max_medals` は1hitの最大枚数ではなく、**連チャン中の合計枚数**
- [ ] 蓄積DB再計算済み（max_medals < 500 なのに 20連以上 → バグ）

### おすすめ理由が矛盾しない
- [ ] 「連続好調」と「不調」が同一カードに同居していない
- [ ] consecutive_plus判定: diff > -2,000枚の場合のみprob良好で好調扱い許可
- [ ] `estimate_diff_medals()` 由来の誤った好調判定がない

### モードバッジが正しい
- [ ] before_open（開店前）/ realtime（営業中）/ after_close（閉店後）
- [ ] JSが動的に上書きするが、ビルド時の初期値も正しい
- [ ] staleリアルタイムデータを閉店後に使わない

### 的中率日付が正しい
- [ ] 23:00以降 → 今日の結果
- [ ] 0:00-22:59 → 昨日の結果
- [ ] verify.html と index.html の日付が一致

---

## 🎨 CSS チェック

- [ ] CSSに重複定義がない（同じセレクタが2箇所 → 1箇所に統合）
- [ ] 未使用クラスが増えていない（validate_output.pyが警告する）
- [ ] style.cssにキャッシュバスト（?v=）が付いている
- [ ] セクションコメントで論理的に整理されている

---

## ⚠️ よくあるミス

- リンクの下線を一箇所だけ直して他を忘れる
- テンプレートAで直してテンプレートBで直さない
- JS変数の二重定義（realtime.js vs インラインスクリプト）
- display_modeの分岐漏れ（before_open/after_close/realtime）
- 静的HTML生成のタイミング依存（display_modeは生成時に固定）
- **データ補完を1箇所ずつ追加して、他の4パスを忘れる**
- **estimate_diff_medals()を蓄積DBより先に参照する**
- **1hitの最大値をmax_medalsとして扱う**（連チャン合計が正解）
- **確率だけで好調判定する**（差枚大幅マイナスなら好調ではない）
- メモリだけ読んで既存コードを確認せずに着手する
- RSさんの指示を「新規の話」と決めつける（大抵は既存の延長）

---

## 🤖 自動チェック（validate_output.py）

以下は `python3 scripts/validate_output.py` で自動検証される:

1. 時間帯モードバッジの存在
2. 的中率ヒーローカードと日付の正しさ
3. TOP10カードの必須項目（理由/直近データ/グラフ/差枚）
4. 差枚表示の網羅性
5. verify.htmlの日付・結果データ
6. recommendページの差枚表示
7. realtime.jsの存在
8. availability.jsonの鮮度・キー
9. CSSキャッシュバスト
10. **展開詳細テーブルの連チャン列**
11. **天井行のRB跨ぎ累計G数**
12. **おすすめ理由の矛盾（好調+不調の同居）**
13. **max_medalsと連チャン数の整合性**
14. **展開パネルのprocessedデータ使用確認**
15. **CSS未使用クラスの警告**
