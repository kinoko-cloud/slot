"""差枚推定モジュール

機種別の通常消費レート(α)を使って差枚を推定する。
差枚 ≈ Σ medals(当たり出玉) - 累計G数 × α

αは「通常時の実質1G消費枚数」:
  IN(3枚/G) - 通常時OUT(ベース払出/G)

検証済み:
  北斗転生の章2: α=1.58 (daidataランキングと誤差<200枚)
  SBJ: α=1.0 (推定、要検証)
"""

# 機種別の通常消費レート
MACHINE_ALPHA = {
    'hokuto2': 1.58,  # 北斗転生の章2 (コイン持ち≈35G)
    'sbj': 1.0,              # スーパーブラックジャック (コイン持ち≈50G)
}


def estimate_diff_medals(medals_total: int, total_games: int, machine_key: str) -> int:
    """差枚を推定する
    
    Args:
        medals_total: 当たり出玉の合計
        total_games: 累計スタート（総回転数）
        machine_key: 機種キー
    
    Returns:
        推定差枚（正=勝ち、負=負け）
    """
    alpha = MACHINE_ALPHA.get(machine_key, 1.3)  # デフォルト1.3
    return round(medals_total - total_games * alpha)
