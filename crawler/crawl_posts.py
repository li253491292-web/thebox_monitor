"""
Day 2: 帖子列表采集脚本

用法:
    python crawler/crawl_posts.py

流程:
    1. 复用登录态进入社区
    2. 搜索目标游戏
    3. 滚动加载帖子列表
    4. 提取 post_id/title/author/content/like_count/comment_count
    5. 保存到 data/posts.csv
"""
import sys
import csv
import time
import random
import datetime
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from crawler.browser_context import browser_session, ensure_captcha_resolved, load_config, read_text, safe_error_text, safe_wait, take_screenshot
from crawler.crawl_navigation import (
    go_to_home, go_to_community, CaptchaBlocked, select_latest_post_sort
)
from crawler.selectors import SELECTORS
from crawler.database import init_db, start_run, finish_run, upsert_post, get_stats, get_conn, clean_existing_navigation_noise
from crawler.cooldown import wait_if_needed, mark_run_complete, mark_captcha_hit
from crawler.page_refresh import refresh_page_for_next_round


def human_delay(base_seconds, jitter_ratio=0.5):
    """模拟人类操作间隔，在 base 基础上随机 +/- jitter"""
    jitter = base_seconds * jitter_ratio * (random.random() * 2 - 1)
    delay = max(0.3, base_seconds + jitter)
    time.sleep(delay)
    return delay


def search_for_game(page, game_name, config=None):
    if config is None:
        config = load_config()

    print(f"[search] 搜索目标游戏: {game_name}")
    wait_s = config["crawl"].get("page_wait_seconds", 5)

    search_input = page.locator(SELECTORS["community"]["search_input"]).first
    if search_input.count() > 0:
        print("[search] 找到搜索框，输入游戏名...")
        search_input.click()
        safe_wait(page, 0.5)
        search_input.fill(game_name)
        safe_wait(page, 1.5)
        page.keyboard.press("Enter")
        safe_wait(page, wait_s)
        print(f"[search] 搜索完成，当前 URL: {page.url}")
    else:
        print("[search] 未找到搜索框，尝试搜索图标...")
        search_icon = page.locator(SELECTORS["community"].get("search_icon", "[class*=search]")).first
        if search_icon.count() > 0:
            search_icon.click()
            safe_wait(page, 2)
            search_input = page.locator(SELECTORS["community"]["search_input"]).first
            if search_input.count() > 0:
                search_input.fill(game_name)
                safe_wait(page, 1.5)
                page.keyboard.press("Enter")
                safe_wait(page, wait_s)
        else:
            raise Exception("无法找到搜索入口")

    return page


def normalize_time(raw, base_time=None):
    """
    Convert recognized relative/absolute time text to normalized datetime.
    Returns (normalized_time, confidence). Unknown non-time text returns ("", "unknown").
    """
    raw = (raw or "").strip()
    if not raw:
        return "", "unknown"
    now = base_time or datetime.datetime.now()

    match = re.match(r'(\d+)\u5206\u949f\u524d', raw)
    if match:
        dt = now - datetime.timedelta(minutes=int(match.group(1)))
        return dt.strftime("%Y-%m-%d %H:%M"), "relative"

    match = re.match(r'(\d+)\u5c0f\u65f6\u524d', raw)
    if match:
        dt = now - datetime.timedelta(hours=int(match.group(1)))
        return dt.strftime("%Y-%m-%d %H:%M"), "relative"

    match = re.match(r'\u6628\u5929\s*(\d{1,2}:\d{2})', raw)
    if match:
        dt = now - datetime.timedelta(days=1)
        return dt.strftime("%Y-%m-%d") + " " + match.group(1), "relative"

    match = re.match(r'\u524d\u5929\s*(\d{1,2}:\d{2})', raw)
    if match:
        dt = now - datetime.timedelta(days=2)
        return dt.strftime("%Y-%m-%d") + " " + match.group(1), "relative"

    match = re.match(r'(\d+)\u5929\u524d', raw)
    if match:
        dt = now - datetime.timedelta(days=int(match.group(1)))
        return dt.strftime("%Y-%m-%d 00:00"), "relative"

    match = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})\s*(\d{1,2}:\d{2})?', raw)
    if match:
        time_part = match.group(4) or "00:00"
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d} {time_part}", "absolute"

    match = re.match(r'(\d{1,2})-(\d{1,2})\s*(\d{1,2}:\d{2})?', raw)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        year = now.year
        if month > now.month or (month == now.month and day > now.day):
            year -= 1
        time_part = match.group(3) or "00:00"
        return f"{year}-{month:02d}-{day:02d} {time_part}", "absolute"

    return "", "unknown"


def extract_post_data(card):
    href = card.get_attribute("href") or ""
    crawled_at = datetime.datetime.now()
    raw_time = read_text(card, SELECTORS["post_card"]["publish_time"])
    publish_time, confidence = normalize_time(raw_time, crawled_at) if raw_time else ("", "unknown")

    return {
        "post_id": href.split("/")[-1] if href else "",
        "source_url": href,
        "title": read_text(card, SELECTORS["post_card"]["title"]),
        "author_name": read_text(card, SELECTORS["post_card"]["author"]),
        "author_level": read_text(card, SELECTORS["post_card"]["level"]),
        "content_preview": read_text(card, SELECTORS["post_card"]["content"], limit=200),
        "publish_time_crawled_at": crawled_at.isoformat(),
        "publish_time_raw": raw_time,
        "publish_time": publish_time,
        "publish_time_confidence": confidence,
        "like_count": read_text(card, SELECTORS["post_card"]["like_count"], default="0"),
        "comment_count": read_text(card, SELECTORS["post_card"]["comment_count"], default="0"),
    }


def enter_game_community(page, config=None):
    """Open the game community via configured target_game_id, or fall back to search."""
    if config is None:
        config = load_config()
    wait_s = config["crawl"].get("page_wait_seconds", 5)
    game_id = str(config["crawl"].get("target_game_id") or "").strip()
    if not game_id:
        print("[nav] target_game_id is not configured; falling back to search")
        go_to_community(page, config)
        return search_for_game(page, config["crawl"]["target_game_name"], config)

    game_url = f"https://www.xiaoheihe.cn/app/topic/game/pc/{game_id}"

    print(f"[nav] Open game page directly: {game_url}")
    page.goto(game_url, wait_until="domcontentloaded")
    safe_wait(page, wait_s)

    ensure_captcha_resolved(page, "Game page captcha wait timed out")

    community_tab = page.locator(".slide-tab__tab-item:has-text('\u793e\u533a')").first
    if community_tab.count() > 0:
        print("[nav] Click community tab")
        community_tab.click()
        safe_wait(page, wait_s)
        print(f"[nav] Current URL: {page.url}")
        take_screenshot(page, "crawl_game_community")
    else:
        print("[nav] Community tab not found; staying on game page")

    select_latest_post_sort(page, config)
    return page

def crawl_posts(page, config, max_scrolls=None, stop_after_no_new=2):
    max_scrolls = max_scrolls or config["crawl"].get("max_scrolls", 5)
    scroll_wait = config["crawl"].get("scroll_wait_seconds", 3)
    batch_size = config["crawl"].get("post_batch_size", 5)
    batch_delay_min = config["crawl"].get("post_batch_delay_min", 1)
    batch_delay_max = config["crawl"].get("post_batch_delay_max", 5)
    seen_ids = set()
    new_count_total = 0
    updated_count_total = 0
    skipped_count_total = 0
    no_new_streak = 0

    print(f"\n[crawl] start: max_scrolls={max_scrolls}")
    print(f"[crawl] batch delay={batch_delay_min}-{batch_delay_max}s, size={batch_size}")

    for scroll_idx in range(max_scrolls):
        if scroll_idx > 0:
            delay = human_delay(2)
            print(f"  [sleep] scroll delay {delay:.1f}s")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        safe_wait(page, scroll_wait)

        cards = page.locator(SELECTORS["search_result"]["post_card"])
        card_count = cards.count()
        scroll_new = 0
        scroll_updated = 0
        scroll_skipped = 0
        scroll_seen_before = 0
        batch_count = 0

        for i in range(card_count):
            try:
                card = cards.nth(i)
                href = card.get_attribute("href") or ""
                post_id = href.split("/")[-1]

                if not post_id:
                    continue
                if post_id in seen_ids:
                    scroll_seen_before += 1
                    continue

                seen_ids.add(post_id)
                data = extract_post_data(card)
                result = upsert_post(data)

                if result == "new":
                    scroll_new += 1
                elif result == "updated":
                    scroll_updated += 1
                else:
                    scroll_skipped += 1

                batch_count += 1
                if batch_count >= batch_size:
                    delay = random.uniform(batch_delay_min, batch_delay_max)
                    time.sleep(delay)
                    batch_count = 0
            except Exception as e:
                if scroll_idx == 0 and i < 3:
                    print(f"  [debug] card #{i} extraction failed: {e}")

        new_count_total += scroll_new
        updated_count_total += scroll_updated
        skipped_count_total += scroll_skipped
        if scroll_new == 0:
            no_new_streak += 1
        else:
            no_new_streak = 0

        print(
            f"  [scroll {scroll_idx + 1}/{max_scrolls}] cards={card_count}, "
            f"new={scroll_new}, updated={scroll_updated}, skipped={scroll_skipped}, "
            f"seen={scroll_seen_before}, total_new={new_count_total}, no_new={no_new_streak}"
        )

        if no_new_streak >= stop_after_no_new:
            print(f"  [crawl] stopped after {no_new_streak} consecutive no-new scrolls")
            break

    print(f"\n[crawl] complete: new={new_count_total}, updated={updated_count_total}, skipped={skipped_count_total}")
    return new_count_total, updated_count_total, scroll_idx + 1


def crawl_posts_deep(page, config):
    deep_cfg = config.get("crawl", {}).get("deep", {})
    refresh_rounds = int(deep_cfg.get("refresh_rounds", 6))
    scrolls_per_round = int(deep_cfg.get("scrolls_per_round", 5))
    stop_refresh_no_new = int(deep_cfg.get("stop_refresh_no_new", 3))
    stop_after_no_new = int(deep_cfg.get("stop_after_no_new", 3))
    refresh_delay_seconds = float(deep_cfg.get("refresh_delay_seconds", 8))

    print("\n[deep] start deep crawl")
    print(
        f"[deep] refresh_rounds={refresh_rounds}, scrolls_per_round={scrolls_per_round}, "
        f"stop_refresh_no_new={stop_refresh_no_new}"
    )

    total_new = 0
    total_updated = 0
    total_scrolled = 0
    no_new_refresh_streak = 0

    for round_idx in range(refresh_rounds):
        if round_idx > 0:
            refresh_page_for_next_round(
                page,
                config,
                round_idx + 1,
                refresh_delay_seconds,
            )

        print(f"\n[deep] round {round_idx + 1}/{refresh_rounds}")
        round_new, round_updated, round_scrolled = crawl_posts(
            page,
            config,
            max_scrolls=scrolls_per_round,
            stop_after_no_new=stop_after_no_new,
        )
        total_new += round_new
        total_updated += round_updated
        total_scrolled += round_scrolled

        if round_new == 0:
            no_new_refresh_streak += 1
        else:
            no_new_refresh_streak = 0
        print(
            f"[deep] round {round_idx + 1}: new={round_new}, updated={round_updated}, "
            f"no_new_rounds={no_new_refresh_streak}"
        )

        if no_new_refresh_streak >= stop_refresh_no_new:
            print(f"[deep] stopped after {no_new_refresh_streak} no-new rounds")
            break

    print(f"\n[deep] complete: new={total_new}, updated={total_updated}")
    return total_new, total_updated, total_scrolled


def export_csv(filepath):
    """从 SQLite 导出全部帖子到 CSV（兼容旧接口）"""
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    clean_existing_navigation_noise()
    conn = get_conn()
    rows = conn.execute("SELECT * FROM posts ORDER BY first_crawl_at DESC").fetchall()
    conn.close()

    fieldnames = [
        "post_id", "source_url", "title", "author_name", "author_level",
        "publish_time", "publish_time_raw", "publish_time_confidence",
        "publish_time_crawled_at", "standard_publish_time", "standard_publish_time_source",
        "standard_publish_time_confidence", "content_preview", "like_count", "comment_count",
        "first_crawl_at"
    ]

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in fieldnames if k in row.keys()})

    print(f"[export] 已导出 {len(rows)} 条到: {filepath}")


def main():
    config = load_config()
    csv_path = PROJECT_ROOT / "data" / "posts.csv"

    print("=" * 60)
    print("Xiaoheihe post crawler")
    print("=" * 60)

    init_db()
    wait_if_needed()
    stats_before = get_stats()
    print(f"[db] current={stats_before['total_posts']}, new_today={stats_before['new_today']}")

    run_id = start_run()
    exit_code = 0
    try:
        with browser_session(config) as page:
            go_to_home(page, config)
            delay = human_delay(3)
            print(f"[sleep] after home load: {delay:.1f}s")

            enter_game_community(page, config)
            delay = human_delay(4)
            print(f"[sleep] after entering community: {delay:.1f}s")

            take_screenshot(page, "crawl_posts_before")
            if "--deep" in sys.argv:
                new_count, updated_count, total_scrolled = crawl_posts_deep(page, config)
            else:
                new_count, updated_count, total_scrolled = crawl_posts(page, config)
            take_screenshot(page, "crawl_posts_after")

        finish_run(
            run_id,
            new_posts=new_count,
            updated_posts=updated_count,
            total_scrolled=total_scrolled,
        )
        stats_after = get_stats()
        print(f"\n[db] inventory: {stats_before['total_posts']} -> {stats_after['total_posts']}")
        print(f"[db] this run: new={new_count}, updated={updated_count}")
        export_csv(csv_path)
        mark_run_complete()
    except CaptchaBlocked as error:
        print(f"\n[BLOCKED] {error}")
        exit_code = 2
        finish_run(run_id, status="captcha", captcha_hit=1)
        mark_captcha_hit()
    except Exception as error:
        print(f"\n[ERROR] {safe_error_text(error)}")
        exit_code = 1
        import traceback
        traceback.print_exc()
        finish_run(run_id, status="error")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
