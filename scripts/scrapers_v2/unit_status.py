"""
台状態管理モジュール

台が一時的に取得できない場合と、本当に撤去された場合を区別する。

状態:
- active: 正常（取得可能）
- pending: 保留中（一時的に取得できない、確認中）
- stopped: 停止（撤去確定、取得しない）

フロー:
1. 台一覧から消えた → pending状態にする（即削除しない）
2. pending状態で複数回失敗 → 翌日営業開始後に再確認
3. 翌日もなければ → stopped状態に
"""
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import pytz

JST = pytz.timezone('Asia/Tokyo')

# 設定
PENDING_THRESHOLD_HOURS = 2  # pending状態になってからの最低待機時間
NEXT_DAY_CHECK_HOUR = 11  # 翌日確認する時刻（営業開始後）
MIN_FAIL_COUNT = 3  # 停止判定に必要な最低失敗回数

STATUS_FILE = Path(__file__).parent.parent.parent / 'data' / 'unit_status.json'


def _load_status() -> Dict[str, Any]:
    """状態ファイルを読み込む"""
    if STATUS_FILE.exists():
        try:
            with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {'units': {}}


def _save_status(data: Dict[str, Any]):
    """状態ファイルを保存"""
    data['_updated'] = datetime.now(JST).isoformat()
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _get_unit_key(store_key: str, unit_id: str) -> str:
    """台の一意キーを生成"""
    return f"{store_key}/{unit_id}"


def get_unit_status(store_key: str, unit_id: str) -> str:
    """台の状態を取得（active/pending/stopped）"""
    data = _load_status()
    key = _get_unit_key(store_key, unit_id)
    unit_data = data.get('units', {}).get(key, {})
    return unit_data.get('status', 'active')


def is_unit_active(store_key: str, unit_id: str) -> bool:
    """台がアクティブか（取得対象か）"""
    status = get_unit_status(store_key, unit_id)
    return status != 'stopped'


def mark_unit_missing(store_key: str, unit_id: str, reason: str = 'not_in_list') -> str:
    """
    台が見つからなかった場合の処理
    
    Returns:
        新しい状態（pending/stopped）
    """
    data = _load_status()
    key = _get_unit_key(store_key, unit_id)
    now = datetime.now(JST)
    
    if key not in data.get('units', {}):
        data.setdefault('units', {})[key] = {}
    
    unit_data = data['units'][key]
    current_status = unit_data.get('status', 'active')
    
    if current_status == 'stopped':
        # すでに停止済み
        return 'stopped'
    
    if current_status == 'active':
        # 初めて見つからなかった → pending
        unit_data['status'] = 'pending'
        unit_data['first_missing'] = now.isoformat()
        unit_data['fail_count'] = 1
        unit_data['reason'] = reason
        _save_status(data)
        return 'pending'
    
    # pending状態 → 失敗カウント増加
    unit_data['fail_count'] = unit_data.get('fail_count', 0) + 1
    unit_data['last_missing'] = now.isoformat()
    
    # 停止判定: 翌日の営業開始後 + 最低失敗回数
    first_missing = datetime.fromisoformat(unit_data['first_missing'])
    next_day_check = first_missing.replace(hour=NEXT_DAY_CHECK_HOUR, minute=0, second=0)
    if first_missing.hour >= NEXT_DAY_CHECK_HOUR:
        next_day_check += timedelta(days=1)
    
    if now >= next_day_check and unit_data['fail_count'] >= MIN_FAIL_COUNT:
        # 翌日営業開始後 + 最低回数失敗 → 停止
        unit_data['status'] = 'stopped'
        unit_data['stopped_at'] = now.isoformat()
        _save_status(data)
        return 'stopped'
    
    _save_status(data)
    return 'pending'


def mark_unit_found(store_key: str, unit_id: str):
    """
    台が見つかった場合の処理（pending/stoppedからactiveに戻す）
    """
    data = _load_status()
    key = _get_unit_key(store_key, unit_id)
    
    if key in data.get('units', {}):
        unit_data = data['units'][key]
        if unit_data.get('status') in ('pending', 'stopped'):
            unit_data['status'] = 'active'
            unit_data['recovered_at'] = datetime.now(JST).isoformat()
            unit_data['fail_count'] = 0
            _save_status(data)


def get_stopped_units(store_key: str = None) -> List[Dict[str, Any]]:
    """停止中の台一覧を取得"""
    data = _load_status()
    stopped = []
    for key, unit_data in data.get('units', {}).items():
        if unit_data.get('status') == 'stopped':
            sk, uid = key.split('/', 1)
            if store_key is None or sk == store_key:
                stopped.append({
                    'store_key': sk,
                    'unit_id': uid,
                    **unit_data
                })
    return stopped


def get_pending_units(store_key: str = None) -> List[Dict[str, Any]]:
    """保留中の台一覧を取得"""
    data = _load_status()
    pending = []
    for key, unit_data in data.get('units', {}).items():
        if unit_data.get('status') == 'pending':
            sk, uid = key.split('/', 1)
            if store_key is None or sk == store_key:
                pending.append({
                    'store_key': sk,
                    'unit_id': uid,
                    **unit_data
                })
    return pending


def cleanup_old_entries(days: int = 30):
    """古いエントリを削除（stopped状態で指定日数以上経過）"""
    data = _load_status()
    now = datetime.now(JST)
    threshold = now - timedelta(days=days)
    
    to_delete = []
    for key, unit_data in data.get('units', {}).items():
        if unit_data.get('status') == 'stopped':
            stopped_at = unit_data.get('stopped_at')
            if stopped_at:
                stopped_dt = datetime.fromisoformat(stopped_at)
                if stopped_dt < threshold:
                    to_delete.append(key)
    
    for key in to_delete:
        del data['units'][key]
    
    if to_delete:
        _save_status(data)
    
    return len(to_delete)


# テスト用
if __name__ == '__main__':
    # テスト
    print("=== 台状態管理テスト ===")
    
    # 初期状態
    print(f"3011 status: {get_unit_status('shibuya_espass_sbj', '3011')}")
    
    # 見つからなかった場合
    result = mark_unit_missing('shibuya_espass_sbj', '3011', 'machine_mismatch')
    print(f"After missing: {result}")
    
    # 再度見つからなかった場合
    result = mark_unit_missing('shibuya_espass_sbj', '3011', 'machine_mismatch')
    print(f"After 2nd missing: {result}")
    
    # 保留中一覧
    print(f"Pending units: {get_pending_units()}")
