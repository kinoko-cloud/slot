#!/usr/bin/env python3
"""
天井・モード分析モジュール

天井システムを考慮した設定推定を行う:
- 天井到達での当選を識別
- 天井除外した真の初当たり確率を計算
- 上位AT後（天国モード）の台を検出
- リセット判定
"""

from datetime import datetime, timezone
JST = timezone(timedelta(hours=9))
from typing import Optional, List, Dict, Any

# 機種別天井パラメータ
CEILING_PARAMS = {
    'sbj': {
        'normal_ceiling': 999,      # 通常天井G数
        'reset_ceiling': 666,       # リセット時天井
        'ceiling_zone_start': 800,  # これ以上は天井ゾーン
        'reset_zone_end': 700,      # これ以下はリセット天井の可能性
        'renchain_threshold': 30,   # 連チャン判定閾値
        # 設定別初当たり確率（天井込み）
        'setting_probs': {
            6: 165, 5: 172, 4: 180, 3: 195, 2: 205, 1: 215
        },
        # 天井到達率の推定（設定別）
        'ceiling_rate': {
            6: 0.05, 5: 0.08, 4: 0.12, 3: 0.18, 2: 0.22, 1: 0.28
        },
    },
    'hokuto2': {
        'normal_ceiling_g': 750,     # 通常A天井の平均G数（1536あべし）
        'reset_ceiling_g': 630,      # リセット時天井G数（1280あべし）
        'mode_ceilings_g': {         # モード別天井G数（あべしから換算）
            'A': 750, 'B': 440, 'C': 280, 'heaven': 60
        },
        'ceiling_zone_start': 600,   # これ以上は天井ゾーン
        'heaven_zone_end': 80,       # これ以下は天国モードの可能性
        'upper_at_indicator': 5,     # 上位AT後のBB履歴G数
        'renchain_threshold': 50,    # 連チャン判定閾値
        # 設定別初当たり確率
        'setting_probs': {
            6: 273, 5: 283, 4: 299, 3: 336, 2: 357, 1: 366
        },
        # 天井到達率の推定（設定別、モードA時）
        'ceiling_rate': {
            6: 0.15, 5: 0.18, 4: 0.22, 3: 0.28, 2: 0.32, 1: 0.38
        },
    },
}


def analyze_hit_pattern(history: List[Dict], machine_key: str) -> Dict[str, Any]:
    """
    当たり履歴から天井パターンを分析
    
    Args:
        history: [{time, games, medals, ...}, ...]  当たり履歴
        machine_key: 'sbj' or 'hokuto2'
    
    Returns:
        {
            'total_hits': 総当たり回数,
            'ceiling_hits': 天井到達回数,
            'early_hits': 早い当たり回数,
            'ceiling_rate': 天井到達率,
            'adjusted_prob': 天井除外確率,
            'is_upper_at_after': 上位AT後フラグ（北斗2のみ）,
            'estimated_mode': 推定モード,
            'hit_distribution': 当たりG数分布,
        }
    """
    params = CEILING_PARAMS.get(machine_key, CEILING_PARAMS['sbj'])
    
    if not history:
        return {
            'total_hits': 0,
            'ceiling_hits': 0,
            'early_hits': 0,
            'ceiling_rate': 0,
            'adjusted_prob': 0,
            'is_upper_at_after': False,
            'estimated_mode': 'unknown',
            'hit_distribution': {},
        }
    
    # 当たり間G数を計算
    hit_games_list = []
    prev_time = None
    
    for hit in sorted(history, key=lambda x: x.get('time', '00:00')):
        # 当たり間G数（複数フィールド名に対応）
        # startフィールド: 初当たりG数（papimo等）
        # gamesフィールド: 初当たりG数（anaslo等）
        hit_games = hit.get('start') or hit.get('games', 0)
        
        if hit_games and hit_games > 0:
            hit_games_list.append(hit_games)
    
    if not hit_games_list:
        # G数データがない場合は時間から推定
        return _analyze_from_time(history, machine_key)
    
    # 天井判定
    ceiling_zone = params.get('ceiling_zone_start', 800)
    heaven_zone = params.get('heaven_zone_end', 80) if machine_key == 'hokuto2' else 0
    
    ceiling_hits = sum(1 for g in hit_games_list if g >= ceiling_zone)
    early_hits = sum(1 for g in hit_games_list if g <= heaven_zone) if heaven_zone else 0
    total_hits = len(hit_games_list)
    
    ceiling_rate = ceiling_hits / total_hits if total_hits > 0 else 0
    
    # 天井除外確率を計算
    # 天井以外の当たりの平均G数から確率を推定
    non_ceiling_games = [g for g in hit_games_list if g < ceiling_zone]
    if non_ceiling_games:
        avg_non_ceiling = sum(non_ceiling_games) / len(non_ceiling_games)
        adjusted_prob = avg_non_ceiling
    else:
        adjusted_prob = 0
    
    # 上位AT後判定（北斗2のみ）
    is_upper_at_after = False
    if machine_key == 'hokuto2':
        upper_indicator = params.get('upper_at_indicator', 5)
        # 直近の当たりが極端に早い場合は上位AT後
        if hit_games_list and hit_games_list[-1] <= upper_indicator:
            is_upper_at_after = True
        # または早い当たりが複数ある場合
        if early_hits >= 2:
            is_upper_at_after = True
    
    # モード推定
    estimated_mode = _estimate_mode(hit_games_list, machine_key)
    
    # 当たりG数分布
    distribution = {
        '0-100': sum(1 for g in hit_games_list if g <= 100),
        '101-300': sum(1 for g in hit_games_list if 100 < g <= 300),
        '301-500': sum(1 for g in hit_games_list if 300 < g <= 500),
        '501-700': sum(1 for g in hit_games_list if 500 < g <= 700),
        '701-999': sum(1 for g in hit_games_list if 700 < g <= 999),
        '1000+': sum(1 for g in hit_games_list if g > 999),
    }
    
    return {
        'total_hits': total_hits,
        'ceiling_hits': ceiling_hits,
        'early_hits': early_hits,
        'ceiling_rate': round(ceiling_rate, 3),
        'adjusted_prob': round(adjusted_prob, 1),
        'is_upper_at_after': is_upper_at_after,
        'estimated_mode': estimated_mode,
        'hit_distribution': distribution,
    }


def _analyze_from_time(history: List[Dict], machine_key: str) -> Dict[str, Any]:
    """時間データから分析（G数データがない場合のフォールバック）"""
    # 時間間隔から推定（30G/分として計算）
    params = CEILING_PARAMS.get(machine_key, CEILING_PARAMS['sbj'])
    
    sorted_history = sorted(history, key=lambda x: x.get('time', '00:00'))
    
    hit_games_list = []
    prev_minutes = 600  # 10:00開店
    
    for hit in sorted_history:
        time_str = hit.get('time', '')
        if not time_str:
            continue
        try:
            h, m = map(int, time_str.split(':'))
            curr_minutes = h * 60 + m
            gap = curr_minutes - prev_minutes
            estimated_games = gap * 30  # 30G/分
            if estimated_games > 0:
                hit_games_list.append(estimated_games)
            prev_minutes = curr_minutes
        except:
            continue
    
    if not hit_games_list:
        return {
            'total_hits': len(history),
            'ceiling_hits': 0,
            'early_hits': 0,
            'ceiling_rate': 0,
            'adjusted_prob': 0,
            'is_upper_at_after': False,
            'estimated_mode': 'unknown',
            'hit_distribution': {},
        }
    
    ceiling_zone = params.get('ceiling_zone_start', 800)
    ceiling_hits = sum(1 for g in hit_games_list if g >= ceiling_zone)
    total_hits = len(hit_games_list)
    ceiling_rate = ceiling_hits / total_hits if total_hits > 0 else 0
    
    return {
        'total_hits': total_hits,
        'ceiling_hits': ceiling_hits,
        'early_hits': 0,
        'ceiling_rate': round(ceiling_rate, 3),
        'adjusted_prob': 0,
        'is_upper_at_after': False,
        'estimated_mode': 'unknown',
        'hit_distribution': {},
    }


def _estimate_mode(hit_games_list: List[int], machine_key: str) -> str:
    """当たりG数分布からモードを推定"""
    if not hit_games_list:
        return 'unknown'
    
    avg_games = sum(hit_games_list) / len(hit_games_list)
    
    if machine_key == 'hokuto2':
        # 北斗2のモード推定
        if avg_games <= 100:
            return 'heaven'  # 天国モード
        elif avg_games <= 350:
            return 'C'  # モードC
        elif avg_games <= 500:
            return 'B'  # モードB
        else:
            return 'A'  # モードA
    else:
        # SBJ
        if avg_games <= 400:
            return 'good'  # 好調
        elif avg_games <= 600:
            return 'normal'  # 普通
        else:
            return 'bad'  # 不調（天井に頼りがち）


def estimate_true_setting(
    total_games: int,
    art_count: int,
    ceiling_hits: int,
    machine_key: str
) -> Dict[str, Any]:
    """
    天井を考慮した真の設定推定
    
    Args:
        total_games: 総G数
        art_count: 総ART回数
        ceiling_hits: 天井到達回数
        machine_key: 機種キー
    
    Returns:
        {
            'raw_prob': 生の確率,
            'adjusted_prob': 天井補正後確率,
            'estimated_setting': 推定設定,
            'confidence': 信頼度,
        }
    """
    params = CEILING_PARAMS.get(machine_key, CEILING_PARAMS['sbj'])
    setting_probs = params.get('setting_probs', {})
    
    if art_count <= 0 or total_games <= 0:
        return {
            'raw_prob': 0,
            'adjusted_prob': 0,
            'estimated_setting': 0,
            'confidence': 'none',
        }
    
    raw_prob = total_games / art_count
    
    # 天井補正
    # 天井での当選は設定関係なく発生するため、その分を補正
    non_ceiling_hits = art_count - ceiling_hits
    if non_ceiling_hits > 0:
        # 天井以外の当選での確率を推定
        # 天井G数（平均800G）× 天井回数を除外
        ceiling_games = ceiling_hits * params.get('ceiling_zone_start', 800)
        non_ceiling_games = total_games - ceiling_games
        if non_ceiling_games > 0:
            adjusted_prob = non_ceiling_games / non_ceiling_hits
        else:
            adjusted_prob = raw_prob
    else:
        # 全部天井の場合は判定不能
        adjusted_prob = raw_prob * 1.3  # 悪い方向に補正
    
    # 設定推定
    estimated_setting = 0
    for setting in [6, 5, 4, 3, 2, 1]:
        if adjusted_prob <= setting_probs.get(setting, 999):
            estimated_setting = setting
            break
    
    # 信頼度
    if art_count >= 30:
        confidence = 'high'
    elif art_count >= 15:
        confidence = 'medium'
    else:
        confidence = 'low'
    
    return {
        'raw_prob': round(raw_prob, 1),
        'adjusted_prob': round(adjusted_prob, 1),
        'estimated_setting': estimated_setting,
        'confidence': confidence,
    }


def detect_reset(first_hit_games: int, machine_key: str) -> Dict[str, Any]:
    """
    初当たりG数からリセット判定
    
    Args:
        first_hit_games: 朝イチの初当たりG数
        machine_key: 機種キー
    
    Returns:
        {
            'is_likely_reset': リセットの可能性,
            'reset_probability': リセット確率,
            'reason': 判定理由,
        }
    """
    params = CEILING_PARAMS.get(machine_key, CEILING_PARAMS['sbj'])
    
    if machine_key == 'sbj':
        reset_ceiling = params.get('reset_ceiling', 666)
        normal_ceiling = params.get('normal_ceiling', 999)
        
        if first_hit_games <= reset_ceiling:
            # リセット天井以内での当選
            # リセット時は666G天井なので、600G以降での当選はリセット濃厚
            if first_hit_games >= 550:
                return {
                    'is_likely_reset': True,
                    'reset_probability': 0.8,
                    'reason': f'リセット天井圏内({first_hit_games}G)での当選',
                }
            else:
                return {
                    'is_likely_reset': False,
                    'reset_probability': 0.3,
                    'reason': f'リセット天井前での当選({first_hit_games}G)',
                }
        else:
            # 666Gを超えているので据え置き
            return {
                'is_likely_reset': False,
                'reset_probability': 0.1,
                'reason': f'リセット天井超え({first_hit_games}G)は据え置き濃厚',
            }
    
    elif machine_key == 'hokuto2':
        reset_ceiling_g = params.get('reset_ceiling_g', 630)
        normal_ceiling_g = params.get('normal_ceiling_g', 750)
        
        if first_hit_games <= reset_ceiling_g:
            if first_hit_games >= 500:
                return {
                    'is_likely_reset': True,
                    'reset_probability': 0.7,
                    'reason': f'リセット天井圏内({first_hit_games}G)での当選',
                }
            else:
                return {
                    'is_likely_reset': False,
                    'reset_probability': 0.4,
                    'reason': f'早い当選({first_hit_games}G)はモード判定必要',
                }
        else:
            return {
                'is_likely_reset': False,
                'reset_probability': 0.2,
                'reason': f'リセット天井超え({first_hit_games}G)',
            }
    
    return {
        'is_likely_reset': False,
        'reset_probability': 0,
        'reason': '不明',
    }


def is_target_for_realtime(
    current_games: int,
    current_art: int,
    prob: float,
    diff_medals: int,
    max_rensa: int,
    max_hamari: int,
    machine_key: str,
    history: List[Dict] = None,
) -> Dict[str, Any]:
    """
    リアルタイムで狙うべき台かどうか判定
    
    Returns:
        {
            'is_target': 狙い目か,
            'priority': 優先度(1-5),
            'reasons': 理由リスト,
            'warnings': 警告リスト,
            'expected_profit': 期待収支,
        }
    """
    params = CEILING_PARAMS.get(machine_key, CEILING_PARAMS['sbj'])
    reasons = []
    warnings = []
    priority = 0
    
    # 履歴分析
    hit_analysis = analyze_hit_pattern(history or [], machine_key)
    
    # 1. 上位AT後判定（北斗2）
    if machine_key == 'hokuto2' and hit_analysis.get('is_upper_at_after'):
        reasons.append('上位AT後（128あべし天国）')
        priority += 3
    
    # 2. 天井到達率チェック
    ceiling_rate = hit_analysis.get('ceiling_rate', 0)
    if ceiling_rate <= 0.1:
        reasons.append(f'天井到達率低い({ceiling_rate*100:.0f}%)')
        priority += 2
    elif ceiling_rate >= 0.4:
        warnings.append(f'天井到達率高い({ceiling_rate*100:.0f}%)')
        priority -= 1
    
    # 3. 天井補正後の確率チェック
    if current_art > 0:
        setting_info = estimate_true_setting(
            current_games, current_art,
            hit_analysis.get('ceiling_hits', 0),
            machine_key
        )
        adjusted_prob = setting_info.get('adjusted_prob', 999)
        estimated_setting = setting_info.get('estimated_setting', 0)
        
        if estimated_setting >= 4:
            reasons.append(f'推定設定{estimated_setting}（補正確率1/{adjusted_prob:.0f}）')
            priority += 2
        elif estimated_setting <= 2:
            warnings.append(f'推定設定{estimated_setting}（補正確率1/{adjusted_prob:.0f}）')
            priority -= 2
    
    # 4. 差枚チェック（凹み台狙い）
    if diff_medals <= -2000:
        reasons.append(f'差枚凹み({diff_medals:+}枚)')
        priority += 1
    elif diff_medals >= 3000:
        warnings.append(f'既に出ている({diff_medals:+}枚)')
        priority -= 1
    
    # 5. 連チャン率チェック
    if max_rensa >= 10:
        reasons.append(f'大連チャン実績あり({max_rensa}連)')
        priority += 1
    elif current_art >= 10 and max_rensa <= 3:
        warnings.append(f'連チャン率低い(最大{max_rensa}連)')
        priority -= 1
    
    # 6. 最大ハマりチェック
    ceiling_zone = params.get('ceiling_zone_start', 800)
    if max_hamari < ceiling_zone:
        reasons.append(f'大ハマりなし(最大{max_hamari}G)')
        priority += 1
    elif max_hamari >= ceiling_zone * 1.5:
        warnings.append(f'大ハマりあり({max_hamari}G)')
    
    # 判定
    is_target = priority >= 2
    
    # 期待収支計算
    remaining_hours = max(0, 23 - datetime.now().hour)
    if priority >= 3:
        expected_profit = remaining_hours * 1500  # 高設定期待
    elif priority >= 1:
        expected_profit = remaining_hours * 500   # 中間期待
    else:
        expected_profit = remaining_hours * -500  # マイナス期待
    
    return {
        'is_target': is_target,
        'priority': max(1, min(5, priority + 2)),  # 1-5にスケール
        'reasons': reasons,
        'warnings': warnings,
        'expected_profit': expected_profit,
        'hit_analysis': hit_analysis,
    }


# テスト用
if __name__ == '__main__':
    # サンプルデータでテスト
    sample_history = [
        {'time': '10:30', 'games': 250},
        {'time': '11:15', 'games': 180},
        {'time': '12:00', 'games': 320},
        {'time': '13:30', 'games': 850},  # 天井
        {'time': '14:15', 'games': 200},
    ]
    
    result = analyze_hit_pattern(sample_history, 'sbj')
    print("=== SBJ分析結果 ===")
    for k, v in result.items():
        print(f"  {k}: {v}")
    
    print("\n=== 真の設定推定 ===")
    setting = estimate_true_setting(5000, 20, 3, 'sbj')
    for k, v in setting.items():
        print(f"  {k}: {v}")
    
    print("\n=== リアルタイム判定 ===")
    target = is_target_for_realtime(
        current_games=4000,
        current_art=25,
        prob=160,
        diff_medals=-2500,
        max_rensa=8,
        max_hamari=650,
        machine_key='sbj',
        history=sample_history,
    )
    for k, v in target.items():
        print(f"  {k}: {v}")
