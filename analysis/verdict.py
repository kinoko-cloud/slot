"""的中判定ロジック

結果レベル（◎◯△✕）と判定テキストの共通定義。
verify/generate_static両方から使う。

結果レベルは確率＋差枚の2軸で判定：
  ◎ = 確率が非常に良い AND 差枚プラス大
  ◯ = 確率が好調域 AND 差枚プラス
  △ = 中間（確率はまぁまぁ or 確率良いが差枚マイナス）
  ✕ = 確率悪い OR 差枚大幅マイナス

判定テキストは予測ランク×結果レベルの全組み合わせで定義：
  S/A予測で◎◯結果 → 的中系
  S/A予測で△✕結果 → ハズレ系
  B予測で△結果 → 的中（予想通り微妙）
  B予測で✕結果 → ハズレ（予想より悪い）
  C/D予測で△✕結果 → 的中（予想通り不調）
  C/D予測で◎◯結果 → 大ハズレ（見逃し）
"""

# 機種別の結果判定閾値
RESULT_THRESHOLDS = {
    'sbj': {
        'excellent_prob': 100,   # ◎の確率閾値
        'good_prob': 130,        # ◯の確率閾値
        'bad_prob': 160,         # ✕の確率閾値
        'excellent_diff': 1500,  # ◎の差枚閾値
        'good_diff': 500,        # ◯の差枚閾値
        'bad_diff': -1500,       # ✕の差枚閾値
        'excellent_max': 2000,   # ◎の最大枚数閾値
        'good_max': 1000,        # ◯の最大枚数閾値
    },
    'hokuto_tensei2': {
        'excellent_prob': 80,
        'good_prob': 100,
        'bad_prob': 150,
        'excellent_diff': 3000,
        'good_diff': 1000,
        'bad_diff': -3000,
        'excellent_max': 5000,
        'good_max': 3000,
    },
}


def get_result_level(prob: float, diff_medals: int, machine_key: str,
                     max_medals: int = 0) -> str:
    """結果レベルを判定する

    Args:
        prob: ART確率（1/X のX。0=データなし）
        diff_medals: 差枚（正=勝ち、負=負け。0=データなし）
        machine_key: 機種キー
        max_medals: 最大枚数（差枚なし時の代替指標。0=データなし）

    Returns:
        'excellent'(◎), 'good'(◯), 'normal'(△), 'bad'(✕), 'nodata'(-)
    """
    if prob <= 0:
        return 'nodata'

    th = RESULT_THRESHOLDS.get(machine_key, RESULT_THRESHOLDS['sbj'])
    has_diff = diff_medals != 0
    has_max = max_medals > 0

    # 出玉指標: 差枚優先、なければ最大枚数で代替
    def _output_check(excellent_th, good_th):
        """出玉が閾値を満たすか判定"""
        if has_diff:
            return diff_medals >= excellent_th, diff_medals >= good_th
        if has_max:
            return max_medals >= th['excellent_max'], max_medals >= th['good_max']
        # どちらもなし → 確率のみで判定（出玉条件はパス扱い）
        return True, True

    # ✕判定（確率悪い OR 差枚大幅マイナス）
    if prob >= th['bad_prob']:
        return 'bad'
    if has_diff and diff_medals <= th['bad_diff']:
        return 'bad'

    # ◎判定（確率非常に良い AND 出玉◎域）
    if prob <= th['excellent_prob']:
        is_exc, is_good = _output_check(th['excellent_diff'], th['good_diff'])
        if is_exc:
            return 'excellent'
        if is_good:
            return 'good'
        # 確率は◎域だが出玉が弱い → △
        return 'normal'

    # ◯判定（確率好調域 AND 出玉◯域）
    if prob <= th['good_prob']:
        _, is_good = _output_check(th['excellent_diff'], th['good_diff'])
        if is_good:
            return 'good'
        # 確率は好調域だが出玉が弱い → △
        return 'normal'

    # それ以外 = △
    return 'normal'


# 予測ランク × 結果レベル → (判定テキスト, CSSクラス)
_VERDICT_TABLE = {
    # S予測
    ('S', 'excellent'): ('大的中！', 'perfect'),
    ('S', 'good'):      ('的中', 'hit'),
    ('S', 'normal'):    ('ハズレ', 'miss'),
    ('S', 'bad'):       ('大ハズレ', 'miss'),

    # A予測
    ('A', 'excellent'): ('的中！', 'hit'),
    ('A', 'good'):      ('的中', 'hit'),
    ('A', 'normal'):    ('ハズレ', 'miss'),
    ('A', 'bad'):       ('大ハズレ', 'miss'),

    # B予測
    ('B', 'excellent'): ('大ハズレ', 'surprise'),
    ('B', 'good'):      ('ハズレ', 'surprise'),
    ('B', 'normal'):    ('的中', 'hit'),
    ('B', 'bad'):       ('ハズレ', 'miss'),

    # C予測
    ('C', 'excellent'): ('大ハズレ', 'surprise'),
    ('C', 'good'):      ('大ハズレ', 'surprise'),
    ('C', 'normal'):    ('的中', 'hit'),
    ('C', 'bad'):       ('的中', 'hit'),

    # D予測
    ('D', 'excellent'): ('大ハズレ', 'surprise'),
    ('D', 'good'):      ('大ハズレ', 'surprise'),
    ('D', 'normal'):    ('的中', 'hit'),
    ('D', 'bad'):       ('的中', 'hit'),
}


def get_verdict(pred_rank: str, result_level: str) -> tuple:
    """判定テキストとCSSクラスを返す

    Args:
        pred_rank: 予測ランク（S/A/B/C/D）
        result_level: 結果レベル（excellent/good/normal/bad/nodata）

    Returns:
        (判定テキスト, CSSクラス)
    """
    if result_level == 'nodata':
        return ('—', 'nodata')

    return _VERDICT_TABLE.get((pred_rank, result_level), ('—', 'nodata'))


def is_hit(pred_rank: str, result_level: str) -> bool:
    """的中判定（的中率計算用）"""
    _, css = get_verdict(pred_rank, result_level)
    return css in ('perfect', 'hit')


# 結果レベル → 表示マーク
RESULT_MARKS = {
    'excellent': ('◎', 'excellent'),
    'good': ('◯', 'good'),
    'normal': ('△', 'neutral'),
    'bad': ('✕', 'bad'),
    'nodata': ('-', 'nodata'),
}
