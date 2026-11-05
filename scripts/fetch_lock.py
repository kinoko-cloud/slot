"""排他ロック付きデータ取得ラッパー
複数プロセスの同時実行を防止する"""
import fcntl
import sys
import os
import time

LOCKFILE = '/tmp/slot_fetch.lock'
LOCK_TIMEOUT_SECONDS = 3600  # 1時間でタイムアウト

def _check_stale_lock():
    """古いロックファイルをチェックして削除"""
    if not os.path.exists(LOCKFILE):
        return
    
    try:
        mtime = os.path.getmtime(LOCKFILE)
        age = time.time() - mtime
        if age > LOCK_TIMEOUT_SECONDS:
            # 1時間以上古いロックは削除
            with open(LOCKFILE, 'r') as f:
                old_pid = f.read().strip()
            print(f'⚠️ 古いロックファイル検出（{int(age)}秒前, PID={old_pid}）→ 削除')
            os.remove(LOCKFILE)
    except Exception as e:
        print(f'ロックファイルチェックエラー: {e}')

def acquire_lock():
    """排他ロック取得。既に実行中なら即終了"""
    _check_stale_lock()  # まず古いロックをチェック
    
    fp = open(LOCKFILE, 'w')
    try:
        fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fp.write(str(os.getpid()))
        fp.flush()
        return fp
    except BlockingIOError:
        print(f'別プロセスが実行中（lockfile: {LOCKFILE}）。スキップ。')
        sys.exit(0)

def release_lock(fp):
    fcntl.flock(fp, fcntl.LOCK_UN)
    fp.close()
