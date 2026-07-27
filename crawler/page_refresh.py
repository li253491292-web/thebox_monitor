import random
import time

from crawler.browser_context import ensure_captcha_resolved, safe_wait
from crawler.crawl_navigation import select_latest_post_sort


def refresh_page_for_next_round(page, config, round_number, delay_seconds):
    """Refresh the community page between deep-crawl rounds."""
    jitter = random.uniform(0, max(0.0, delay_seconds * 0.5))
    delay = delay_seconds + jitter
    print(f"\n[refresh] round {round_number}: waiting {delay:.1f}s before page refresh")
    time.sleep(delay)

    page.reload(wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    safe_wait(page, config["crawl"].get("page_wait_seconds", 5))
    page.evaluate("window.scrollTo(0, 0)")
    safe_wait(page, 1.5)

    ensure_captcha_resolved(page, "Refresh captcha wait timed out")

    latest_sort_selected = select_latest_post_sort(page, config)
    sort_status = "latest sort selected" if latest_sort_selected else "platform default sort retained"
    print(f"[refresh] round {round_number}: page refreshed, {sort_status}, and reset to top")

