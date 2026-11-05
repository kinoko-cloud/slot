"""リトライデコレータ"""
import time
import functools
import logging

logger = logging.getLogger(__name__)

def retry(max_attempts=3, delay=2, backoff=2, exceptions=(Exception,)):
    """
    リトライデコレータ
    
    Args:
        max_attempts: 最大試行回数
        delay: 初回リトライまでの待機秒数
        backoff: 待機時間の倍率
        exceptions: リトライ対象の例外タプル
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            current_delay = delay
            
            while attempt < max_attempts:
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    attempt += 1
                    if attempt >= max_attempts:
                        logger.error(f"{func.__name__} failed after {max_attempts} attempts: {e}")
                        raise
                    
                    logger.warning(f"{func.__name__} attempt {attempt} failed: {e}. Retrying in {current_delay}s...")
                    time.sleep(current_delay)
                    current_delay *= backoff
            
            return None
        return wrapper
    return decorator
