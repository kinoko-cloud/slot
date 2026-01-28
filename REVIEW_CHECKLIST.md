# レビューチェックリスト

修正時に必ず確認すること。

## 修正前
- [ ] 問題のあるページのスクショを撮る（CDN版）
- [ ] 同じ問題が他のテンプレート/ページにもないか横断チェック

## 修正後
- [ ] 全テンプレート(index/ranking/recommend/verify)で同じ修正が必要か確認
- [ ] CSS変更は全ページに影響するか確認
- [ ] JS変更はrealtime.jsとインラインスクリプト両方に必要か確認
- [ ] generate_static.py再生成 + check_css.py
- [ ] ローカルHTTPサーバーでスクショ確認
- [ ] push後、CDN反映（1-2分）を待ってから実際のURLで確認

## よくあるミス
- リンクの下線を一箇所だけ直して他を忘れる
- テンプレートAで直してテンプレートBで直さない
- JS変数の二重定義（realtime.js vs インラインスクリプト）
- display_modeの分岐漏れ（before_open/after_close/realtime）
- 静的HTML生成のタイミング依存（display_modeは生成時に固定）
