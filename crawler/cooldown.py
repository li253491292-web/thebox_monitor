"""
全局冷却控制 — 追踪上次运行时间，强制最小间隔，防止触发风控。
"""
import time
import datetime
import random
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from crawler.browser_context import load_config

COOLDOWN_FILE = PROJECT_ROOT / "data" / ".cooldown"


def get_config_cooldown():
    config = load_config()
    cc = config.get("crawl", {}).get("cooldown", {})
    return {
        "min_run_gap": cc.get("min_run_gap_seconds", 300),
        "post_gap": cc.get("post_gap_seconds", 30),
        "captcha_penalty": cc.get("captcha_penalty_seconds", 600),
        "comment_page_gap": cc.get("comment_page_gap_seconds", 15),
        "comment_batch_size": cc.get("comment_batch_size", 5),
        "comment_batch_delay": cc.get("comment_batch_delay_seconds", 2),
    }


def _read_state():
    if COOLDOWN_FILE.exists():
        try:
            return float(COOLDOWN_FILE.read_text().strip())
        except Exception:
            return 0
    return 0


def _write_state(ts):
    COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOLDOWN_FILE.write_text(str(ts))


def wait_if_needed():
    """检查是否需要冷却，如果需要则等待。返回实际等待秒数。"""
    cd = get_config_cooldown()
    min_gap = cd["min_run_gap"]
    last_run = _read_state()

    if last_run == 0:
        return 0

    elapsed = time.time() - last_run
    if elapsed < min_gap:
        wait = min_gap - elapsed
        print(f"[cooldown] 距上次运行 {elapsed:.0f}s, 需等待 {wait:.0f}s...")
        time.sleep(wait)
        return wait

    return 0


def mark_run_complete():
    """记录本次运行完成时间"""
    _write_state(time.time())


def mark_captcha_hit():
    """验证码触发时延长冷却"""
    cd = get_config_cooldown()
    penalty = cd["captcha_penalty"]
    future_ts = time.time() + penalty
    _write_state(future_ts)
    print(f"[cooldown] 验证码触发，下次运行需等待 {penalty}s")


def random_post_delay():
    """打开下一个帖子详情页前的延迟"""
    cd = get_config_cooldown()
    base = cd["post_gap"]
    delay = random.uniform(base * 0.7, base * 1.3)
    time.sleep(delay)
    return delay


def random_comment_batch_delay():
    """评论批次间延迟"""
    cd = get_config_cooldown()
    base = cd["comment_batch_delay"]
    delay = random.uniform(base, base * 2)
    time.sleep(delay)
    return delay


def random_comment_page_delay():
    """Delay before opening the next comment page."""
    cd = get_config_cooldown()
    base = cd["comment_page_gap"]
    delay = random.uniform(base * 0.7, base * 1.3)
    time.sleep(delay)
    return delay
