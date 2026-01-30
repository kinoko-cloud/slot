"""排他ロック付きデータ取得ラッパー
複数プロセスの同時実行を防止する"""
import fcntl
import sys
import os

LOCKFILE = '/tmp/slot_fetch.lock'

def acquire_lock():
    """排他ロック取得。既に実行中なら即終了"""
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
