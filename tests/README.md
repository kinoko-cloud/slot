# テスト・検証ディレクトリ

このディレクトリには、Claudeが自己チェックできるテスト環境を整備しています。

## ディレクトリ構成

```
tests/
  expected_outputs/     - 期待される出力サンプル
  run_all_tests.sh      - 全テスト実行スクリプト
  README.md            - このファイル
```

## 期待される出力サンプル

Claudeが作業結果を自己検証できるよう、以下のサンプルを用意：

- `static_build_success.txt` - 静的ビルド成功時の出力
- `validation_pass.json` - validate_output.py の成功パターン
- `prediction_format.json` - recommender.py の予測結果フォーマット
- `data_integrity_ok.json` - data_integrity_check.py の正常パターン

## 使い方

### 全テスト実行
```bash
cd /home/riichi/works/slot
bash tests/run_all_tests.sh
```

### 個別テスト
```bash
# 静的ビルドテスト
python3 scripts/generate_static.py && python3 scripts/validate_output.py

# データ品質チェック
python3 scripts/data_integrity_check.py

# 予測ロジックのサンプル実行
python3 -c "from analysis.recommender import recommend_units; print(recommend_units('shinjuku_espass_hokuto')[:1])"
```

## ベストプラクティス

Claudeがこのディレクトリを使って自己チェックする方法：

1. **作業前**: expected_outputs/ のサンプルを確認
2. **作業後**: 実際の出力とサンプルを比較
3. **検証**: 差異があれば原因を調査

これにより、Claudeは人間の確認なしに作業結果を検証できます。
