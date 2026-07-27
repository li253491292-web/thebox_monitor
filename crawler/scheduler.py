r"""
每日定时采集调度器 — 固定时段自动运行完整流程。

流程（每轮）:
    1. 帖子采集 (crawl_posts.py)
    2. New-post sentiment analysis (analysis/sentiment.py)
    3. High-risk full-text and comment crawl (crawl_risk_content.py)
    4. Publication-time normalization and report generation

用法:
    python crawler\scheduler.py           # 前台运行
    python crawler\scheduler.py --once    # 立即执行一轮
"""
import sys
import time
import datetime
import json
import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from crawler.cooldown import wait_if_needed, mark_run_complete, mark_captcha_hit
from crawler.browser_context import load_config
from crawler.database import mark_runs_timed_out_since

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

RUN_INTERVAL_SECONDS = 2 * 60 * 60
LOCK_PATH = LOG_DIR / "scheduler.lock"


def _is_process_running(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _acquire_scheduler_lock():
    for _ in range(2):
        try:
            descriptor = os.open(LOCK_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            try:
                owner = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                owner = {}
            owner_pid = owner.get("pid")
            if _is_process_running(owner_pid):
                print(f"[scheduler] already running (pid={owner_pid}); skip this cycle")
                return False
            try:
                LOCK_PATH.unlink()
                print("[scheduler] removed stale run lock")
            except FileNotFoundError:
                pass
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8") as lock_file:
                json.dump({"pid": os.getpid(), "started_at": datetime.datetime.now().isoformat()}, lock_file)
            return True
    raise RuntimeError(f"Unable to acquire scheduler lock: {LOCK_PATH}")


def _release_scheduler_lock():
    try:
        LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


def _run_script(script_name, label, args=None):
    start = datetime.datetime.now()
    print(f"  [{label}] 开始...")
    script = str(PROJECT_ROOT / script_name)
    try:
        result = subprocess.run(
            [sys.executable, script, *(args or [])],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=int(load_config().get("scheduler", {}).get("script_timeout_seconds", 1200)),
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = (datetime.datetime.now() - start).total_seconds()
        print(f"  [{label}] TIMEOUT ({elapsed:.0f}s)")
        if label == "posts":
            marked = mark_runs_timed_out_since(start)
            print(f"  [{label}] marked {marked} crawl run(s) as timeout")
        log_path = LOG_DIR / f"scheduler_{start.strftime('%Y%m%d_%H%M%S')}_{label}.log"
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        log_path.write_text(str(stdout) + "\n" + str(stderr), encoding="utf-8", errors="replace")
        return 124
    elapsed = (datetime.datetime.now() - start).total_seconds()
    status = "OK" if result.returncode == 0 else f"FAIL({result.returncode})"
    print(f"  [{label}] {status} ({elapsed:.0f}s)")

    def _print_console(value, limit):
        value = value.strip()[:limit]
        try:
            print(f"    {value}")
        except UnicodeEncodeError:
            print(f"    {value.encode('ascii', 'backslashreplace').decode('ascii')}")

    if result.stdout:
        for line in result.stdout.strip().split("\n")[-3:]:
            _print_console(line, 100)
    if result.stderr:
        stderr = result.stderr.strip()
        if stderr:
            try:
                print(f"  [{label}] stderr: {stderr[:200]}")
            except UnicodeEncodeError:
                print(f"  [{label}] stderr: {stderr[:200].encode('ascii', 'backslashreplace').decode('ascii')}")

    log_path = LOG_DIR / f"scheduler_{start.strftime('%Y%m%d_%H%M%S')}_{label}.log"
    log_path.write_text(result.stdout + "\n" + result.stderr, encoding="utf-8", errors="replace")

    return result.returncode


def _run_full_cycle():
    print(f"\n{'='*50}")
    print(f"[scheduler] start full crawl cycle {datetime.datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*50}")

    wait_if_needed()

    post_code = _run_script("crawler/crawl_posts.py", "posts", ["--deep"])
    if post_code != 0:
        if post_code == 2:
            mark_captcha_hit()
        print("[scheduler] posts failed; skip remaining steps")
        return post_code

    time.sleep(2)
    publish_time_code = _run_script("analysis/publish_time.py", "publish_time")
    if publish_time_code != 0:
        print("[scheduler] publish-time normalization failed; skip remaining steps")
        return publish_time_code

    failed_code = 0
    sentiment_code = _run_script("analysis/sentiment.py", "sentiment")
    if sentiment_code != 0:
        print("[scheduler] sentiment failed; skip high-risk detail and comment crawl")
        return sentiment_code

    high_risk_enabled = bool(load_config().get("crawl", {}).get("high_risk", {}).get("enabled", True))
    if high_risk_enabled:
        risk_content_code = _run_script("crawler/crawl_risk_content.py", "risk_content")
        if risk_content_code != 0:
            print("[scheduler] risk content failed; continue with alerts, reports, and notifications")
    else:
        print("[scheduler] high-risk detail and comment crawl disabled by config")

    alert_code = _run_script("analysis/alert_rules.py", "alerts")
    if alert_code != 0:
        print("[scheduler] alerts failed; skip report publication")
        return alert_code

    failed_code = 0
    for script_name, label in (
        ("reports/generate_markdown.py", "report"),
        ("reports/generate_html.py", "html"),
        ("reports/generate_excel.py", "export"),
        ("crawler/popo_notifier.py", "notify"),
    ):
        code = _run_script(script_name, label)
        if code != 0 and failed_code == 0:
            failed_code = code

    if failed_code != 0:
        print("[scheduler] cycle finished with failures")
        return failed_code

    mark_run_complete()
    print("[scheduler] cycle complete\n")
    return 0


def run_full_cycle():
    if not _acquire_scheduler_lock():
        return 0
    try:
        return _run_full_cycle()
    finally:
        _release_scheduler_lock()

def run_loop():
    print("[scheduler] running posts, analysis, reports, and notifications")
    print("[scheduler] press Ctrl+C to stop")
    while True:
        try:
            run_full_cycle()
        except KeyboardInterrupt:
            print("\n[scheduler] ??")
            break
        next_time = datetime.datetime.now() + datetime.timedelta(seconds=RUN_INTERVAL_SECONDS)
        print(f"[scheduler] next cycle: {next_time.strftime('%Y-%m-%d %H:%M:%S')} (every 2 hours)")
        try:
            time.sleep(RUN_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            print("\n[scheduler] ??")
            break

def main():
    print("=" * 50)
    print("小黑盒舆情监控 — 定时调度器 v2")
    print("=" * 50)

    if "--once" in sys.argv or "--now" in sys.argv:
        return run_full_cycle()
    else:
        print("[scheduler] 前台运行，Ctrl+C 停止")
        run_loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
