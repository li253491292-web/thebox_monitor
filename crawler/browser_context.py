import random
import time
from contextlib import contextmanager
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
CAPTCHA_KEYWORDS = ("验证码", "captcha", "人机验证", "安全验证", "滑动验证")


class CaptchaBlocked(RuntimeError):
    pass


def load_config():
    with (PROJECT_ROOT / "config.yaml").open("r", encoding="utf-8") as config_file:
        return yaml.safe_load(config_file) or {}


def resolve_project_path(path_value):
    path = Path(path_value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def page_wait_seconds(config):
    return config.get("crawl", {}).get("page_wait_seconds", 5)


def launch_browser(config=None):
    config = config or load_config()
    browser_config = config["browser"]
    user_data_dir = resolve_project_path(browser_config["user_data_dir"])

    print(f"[browser] Starting browser, user_data_dir={user_data_dir}")
    playwright = sync_playwright().start()
    try:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            executable_path=browser_config.get("executable_path"),
            headless=browser_config.get("headless", False),
            chromium_sandbox=browser_config.get("chromium_sandbox", True),
            slow_mo=browser_config.get("slow_mo", 300),
            viewport=browser_config.get("viewport", {"width": 1440, "height": 900}),
        )
    except Exception:
        playwright.stop()
        raise
    return playwright, context, context.pages[0] if context.pages else context.new_page()


@contextmanager
def browser_session(config=None):
    playwright, context, page = launch_browser(config)
    try:
        yield page
    finally:
        context.close()
        playwright.stop()
        print("[browser] Closed")


def safe_wait(page, seconds=3):
    time.sleep(seconds + random.uniform(0, seconds * 0.5))


def wait_for_page(page, config):
    safe_wait(page, page_wait_seconds(config))


def take_screenshot(page, name):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{name}.png"
    page.screenshot(path=str(path), full_page=False)
    print(f"[screenshot] Saved: {path}")
    return str(path)


def save_page_html(page, name):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{name}.html"
    path.write_text(page.content(), encoding="utf-8")
    print(f"[html] Saved: {path}")
    return str(path)


def capture_page_state(page, name):
    return take_screenshot(page, name), save_page_html(page, name)


def _page_has_captcha(page):
    page_text = page.content().lower()
    return next((keyword for keyword in CAPTCHA_KEYWORDS if keyword.lower() in page_text), None)


def detect_captcha(page):
    keyword = _page_has_captcha(page)
    if not keyword:
        return False
    print(f"[captcha] Detected keyword: {keyword}")
    take_screenshot(page, "captcha_detected")
    return True


def wait_for_captcha_resolved(page, check_interval=5, max_wait=300):
    check_interval = max(0.1, float(check_interval))
    max_wait = max(check_interval, float(max_wait))
    print(f"[captcha] Waiting up to {max_wait:.0f}s for manual completion")

    waited = 0.0
    while waited < max_wait:
        time.sleep(check_interval)
        waited += check_interval
        try:
            if not _page_has_captcha(page):
                print(f"[captcha] Resolved after {waited:.0f}s")
                return True
        except Exception:
            return False
        if int(waited) and int(waited) % 30 == 0:
            print(f"[captcha] Still waiting ({waited:.0f}s / {max_wait:.0f}s)")

    print(f"[captcha] Timed out after {max_wait:.0f}s")
    return False


def ensure_captcha_resolved(page, message):
    if detect_captcha(page) and not wait_for_captcha_resolved(page):
        raise CaptchaBlocked(message)


def read_text(container, selector, default="", limit=None):
    try:
        element = container.locator(selector).first
        if element.count() == 0:
            return default
        text = element.inner_text().strip()
        return text[:limit] if limit is not None else text
    except Exception:
        return default


def safe_error_text(error):
    return str(error).encode("ascii", "backslashreplace").decode("ascii")
