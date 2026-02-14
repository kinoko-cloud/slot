# 店舗プロファイル

店舗ごとの高設定投入傾向を記録。
台番号が変わっても、店の癖は引き継ぐ。

## ファイル構造

```json
{
  "store_key": "shinjuku_espass_hokuto2",
  "name": "エスパス新宿歌舞伎町",
  "tendencies": {
    "position_preference": "corner",  // corner, center, random
    "weekday_pattern": {
      "monday": "weak",
      "friday": "strong",
      "saturday": "strong"
    },
    "event_days": ["5", "15", "25"],
    "notes": "角台に高設定を入れる傾向あり"
  },
  "updated_at": "2026-02-14"
}
```
