"""
scrapers_v2/common/unit_tracker.py - 台変動検出

【検出するケース】
- 台撤去: configにある台番号がスクレイピング結果にない
- 新台追加: スクレイピング結果にconfig未定義の台番号がある
- 台移動: 消えた台と新しい台が同時にある（シャッフルの可能性）
- 減台: 一部の台がなくなる
- 増台: 台数が増える

【対応方針（CLAUDE.md準拠）】
1. 古いデータ: 履歴として保持（削除しない）
2. 新しい台番号: 新規として追跡開始
3. 同一台追跡: しない（物理的な同一台かどうかは判断不可）
"""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Set, Any, Optional

JST = timezone(timedelta(hours=9))


class UnitTracker:
    """台番号追跡・変動検出"""
    
    def __init__(self, config_path: Optional[Path] = None, alerts_dir: Optional[Path] = None):
        self.config_path = config_path
        self.alerts_dir = alerts_dir or Path('data/alerts')
        self.alerts_dir.mkdir(parents=True, exist_ok=True)
    
    def detect_changes(self, 
                      store_key: str,
                      machine_key: str,
                      config_units: Set[str],
                      scraped_units: Set[str],
                      store_name: str = None) -> List[Dict[str, Any]]:
        """台変動を検出
        
        Args:
            store_key: 店舗キー
            machine_key: 機種キー
            config_units: 設定ファイルの台番号セット
            scraped_units: スクレイピングで取得した台番号セット
            store_name: 店舗名（表示用）
        
        Returns:
            アラートのリスト
        """
        alerts = []
        checked_at = datetime.now(JST).isoformat()
        store_name = store_name or store_key
        
        if not scraped_units:
            # データが取れなかった場合
            alerts.append({
                'type': 'scrape_failed',
                'severity': 'error',
                'store_key': store_key,
                'machine_key': machine_key,
                'message': f'{store_name}: データ取得失敗（スクレイピングエラーまたは撤去）',
                'checked_at': checked_at,
            })
            return alerts
        
        # 消えた台番号
        missing_units = config_units - scraped_units
        # 新しい台番号
        new_units = scraped_units - config_units
        
        # 1. 台撤去 or 台移動
        if missing_units:
            alerts.append({
                'type': 'unit_missing',
                'severity': 'warning',
                'store_key': store_key,
                'machine_key': machine_key,
                'units': sorted(missing_units),
                'message': f'{store_name}: 台番号 {", ".join(sorted(missing_units))} が見つかりません',
                'checked_at': checked_at,
            })
        
        # 2. 新台追加 or 台移動先
        if new_units:
            alerts.append({
                'type': 'unit_new',
                'severity': 'info',
                'store_key': store_key,
                'machine_key': machine_key,
                'units': sorted(new_units),
                'message': f'{store_name}: 新しい台番号 {", ".join(sorted(new_units))} を検出',
                'checked_at': checked_at,
            })
        
        # 3. 台数変動
        diff = len(scraped_units) - len(config_units)
        if diff != 0:
            direction = '増台' if diff > 0 else '減台'
            alerts.append({
                'type': 'unit_count_change',
                'severity': 'warning' if diff < 0 else 'info',
                'store_key': store_key,
                'machine_key': machine_key,
                'config_count': len(config_units),
                'actual_count': len(scraped_units),
                'diff': diff,
                'message': f'{store_name}: {direction}（{len(config_units)}台→{len(scraped_units)}台）',
                'checked_at': checked_at,
            })
        
        # 4. 台移動の可能性（消えた台と新しい台が同時にある）
        if missing_units and new_units:
            # 同数なら台番号変更（シャッフル）の可能性が高い
            if len(missing_units) == len(new_units):
                alert_type = 'unit_shuffle'
                msg = f'{store_name}: 台番号変更の可能性（{len(missing_units)}台がシャッフル）'
            else:
                alert_type = 'unit_move_suspected'
                msg = f'{store_name}: 台移動＋増減の可能性'
            
            alerts.append({
                'type': alert_type,
                'severity': 'warning',
                'store_key': store_key,
                'machine_key': machine_key,
                'missing': sorted(missing_units),
                'new': sorted(new_units),
                'message': msg,
                'checked_at': checked_at,
            })
        
        return alerts
    
    def save_alerts(self, alerts: List[Dict], filename: str = None) -> Path:
        """アラートを保存"""
        if not alerts:
            return None
        
        if filename is None:
            filename = f"alerts_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}.json"
        
        filepath = self.alerts_dir / filename
        with open(filepath, 'w') as f:
            json.dump(alerts, f, ensure_ascii=False, indent=2)
        
        return filepath
    
    def get_units_from_history(self, history_dir: Path, store_key: str, machine_key: str) -> Set[str]:
        """履歴ディレクトリから現在追跡中の台番号を取得"""
        full_key = f"{store_key}_{machine_key}"
        store_dir = history_dir / full_key
        
        if not store_dir.exists():
            return set()
        
        return {f.stem for f in store_dir.glob('*.json')}
    
    def auto_update_config(self, 
                          config_units: Set[str],
                          scraped_units: Set[str],
                          add_new: bool = True,
                          remove_missing: bool = False) -> Set[str]:
        """台番号リストを自動更新
        
        Args:
            config_units: 現在の設定
            scraped_units: スクレイピング結果
            add_new: 新しい台を追加するか
            remove_missing: 消えた台を削除するか（デフォルトFalse=履歴保持）
        
        Returns:
            更新後の台番号セット
        """
        updated = config_units.copy()
        
        if add_new:
            updated |= scraped_units
        
        if remove_missing:
            updated &= scraped_units
        
        return updated


def format_alert_message(alerts: List[Dict]) -> str:
    """アラートを人間が読めるメッセージに変換"""
    if not alerts:
        return "異常なし"
    
    lines = ["⚠️ 台変動検出:\n"]
    
    for alert in alerts:
        severity_icon = {
            'error': '🔴',
            'warning': '🟡',
            'info': '🔵',
        }.get(alert.get('severity', 'info'), '⚪')
        
        lines.append(f"{severity_icon} {alert['message']}")
    
    return "\n".join(lines)
