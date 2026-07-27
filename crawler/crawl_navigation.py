from crawler.browser_context import (
    CaptchaBlocked,
    capture_page_state,
    ensure_captcha_resolved,
    load_config,
    safe_wait,
    take_screenshot,
    wait_for_page,
)
from crawler.selectors import SELECTORS


def select_latest_post_sort(page, config=None):
    """Switch the game community feed from smart ranking to latest posts when available."""
    config = config or load_config()
    sorter = page.locator(".topic-link__sorter").first
    if sorter.count() == 0:
        print("[sort] Sort control not found; keeping current feed order")
        return False

    current = sorter.inner_text().strip()
    if "最新" in current:
        print("[sort] Feed already uses latest posts")
        return True

    sorter.click()
    safe_wait(page, 1)
    options = page.get_by_text("最新", exact=True)
    visible_options = [
        options.nth(index)
        for index in range(options.count())
        if options.nth(index).is_visible()
    ]
    if not visible_options:
        print(f"[sort] Latest sort is unavailable; current={current!r}; keeping platform default")
        return False

    visible_options[-1].click()
    wait_for_page(page, config)
    print("[sort] Switched feed to latest posts")
    return True


def _goto(page, url, config, captcha_message):
    page.goto(url, wait_until="domcontentloaded")
    wait_for_page(page, config)
    ensure_captcha_resolved(page, captcha_message)


def go_to_home(page, config=None):
    config = config or load_config()
    start_url = config["crawl"]["start_url"]
    print(f"[nav] Opening home page: {start_url}")
    _goto(page, start_url, config, "Home page captcha wait timed out")
    capture_page_state(page, "01_home")
    print("[nav] Home page ready")
    return page


def go_to_community(page, config=None):
    config = config or load_config()
    print("[nav] Opening community page")
    take_screenshot(page, "02_before_community_click")

    community_button = page.locator("text=社区").first
    if community_button.count() > 0:
        community_button.click()
        wait_for_page(page, config)
        ensure_captcha_resolved(page, "Community page captcha wait timed out")
        capture_page_state(page, "02_community")
        print("[nav] Community page ready")
        return page

    for url in ("https://www.xiaoheihe.cn/community", "https://www.xiaoheihe.cn/community/index"):
        print(f"[nav] Trying community URL: {url}")
        _goto(page, url, config, f"Captcha wait timed out for {url}")
        capture_page_state(page, "02_community_alt")
        break
    return page


def go_to_game_community(page, game_name=None, config=None):
    config = config or load_config()
    game_name = game_name or config["crawl"]["target_game_name"]
    print(f"[nav] Searching game community: {game_name}")

    search_input = page.locator(SELECTORS["community"]["search_input"]).first
    if search_input.count() == 0:
        print("[nav] Search input not found")
        return page

    search_input.click()
    safe_wait(page, 0.5)
    search_input.fill(game_name)
    safe_wait(page, 2)
    take_screenshot(page, "03_search_input")
    page.keyboard.press("Enter")
    wait_for_page(page, config)
    ensure_captcha_resolved(page, "Search result captcha wait timed out")
    capture_page_state(page, "03_game_community")
    print(f"[nav] Navigation complete, URL: {page.url}")
    return page


def navigate_full_flow(page, config=None):
    config = config or load_config()
    go_to_home(page, config)
    go_to_community(page, config)
    go_to_game_community(page, config=config)
    capture_page_state(page, "04_final")
    print("[nav] Full navigation flow complete")
    return page
