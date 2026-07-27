"""
评论采集 — 打开帖子详情页，滚动加载评论（含子评论/盖楼），存入 SQLite。

用法:
    python crawler/crawl_comments.py              # 采集高风险帖的评论
    python crawler/crawl_comments.py --post 123   # 采集指定帖子
"""
import sys
import time
import random
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from crawler.browser_context import browser_session, ensure_captcha_resolved, load_config, read_text, safe_error_text, safe_wait
from crawler.crawl_navigation import CaptchaBlocked
from crawler.selectors import SELECTORS
from crawler.database import init_db, get_conn, parse_count
from crawler.risk_targets import get_high_risk_posts
from crawler.cooldown import (
    wait_if_needed, mark_run_complete, mark_captcha_hit,
    random_post_delay, random_comment_batch_delay, random_comment_page_delay
)


def crawl_post_comments(page, post_id, config=None, navigate=True, max_scrolls=None):
    """
    打开帖子详情页，滚动加载评论，提取一级评论和子评论。
    返回: (new_comment_count, total_comments)
    """
    if config is None:
        config = load_config()
    wait_s = config["crawl"].get("page_wait_seconds", 5)
    if max_scrolls is None:
        max_scrolls = config.get("crawl", {}).get("high_risk", {}).get(
            "comment_max_scrolls", config["crawl"].get("max_scrolls", 10)
        )
    s = SELECTORS["comment"]
    comment_batch_size = config.get("crawl", {}).get("cooldown", {}).get("comment_batch_size", 5)

    post_url = f"https://www.xiaoheihe.cn/app/bbs/link/{post_id}"
    print(f"\n[crawl] 帖子 {post_id}: {post_url}")
    if navigate:
        page.goto(post_url, wait_until="domcontentloaded")
        safe_wait(page, wait_s)

    ensure_captcha_resolved(page, f"Comment page captcha wait timed out for post {post_id}")

    # Scroll to load comments (comments are at the bottom)
    new_count = 0
    seen_comment_ids = set()
    all_comments = []

    for scroll_idx in range(max_scrolls):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        safe_wait(page, random.uniform(2, 4))

        items = page.locator(s["item"])
        comment_count = items.count()

        for i in range(comment_count):
            try:
                if i > 0 and i % comment_batch_size == 0:
                    random_comment_batch_delay()

                item = items.nth(i)
                cid_raw = item.get_attribute(s["comment_id"])
                if not cid_raw:
                    continue

                comment_id = str(cid_raw)
                if comment_id not in seen_comment_ids:
                    seen_comment_ids.add(comment_id)
                    comment_data = _extract_comment(item, post_id, level=1)
                    all_comments.append(comment_data)

                # Check for child replies
                children_container = item.locator(s["children_container"]).first
                if children_container.count() > 0:
                    # Try expanding child comments
                    expand_btn = children_container.locator(s["expand_btn"]).first
                    if expand_btn.count() > 0:
                        try:
                            expand_btn.click()
                            safe_wait(page, 1)
                        except Exception:
                            pass

                    child_items = children_container.locator(s["child_item"])
                    for j in range(child_items.count()):
                        try:
                            child = child_items.nth(j)
                            child_data = _extract_child(child, post_id, comment_id, j)
                            if child_data and child_data["comment_id"] not in seen_comment_ids:
                                seen_comment_ids.add(child_data["comment_id"])
                                all_comments.append(child_data)
                        except Exception:
                            pass

                new_count += 1

            except Exception as e:
                continue

        print(f"  [scroll {scroll_idx+1}] comments on page={comment_count}, extracted so far={len(all_comments)}")

        if scroll_idx > 2 and len(all_comments) > 0 and comment_count == len(seen_comment_ids):
            break

    # Upsert all comments to DB
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            comment_id TEXT PRIMARY KEY,
            post_id TEXT,
            parent_id TEXT,
            author_name TEXT,
            content TEXT,
            publish_time TEXT,
            like_count INTEGER DEFAULT 0,
            reply_count INTEGER DEFAULT 0,
            crawl_time TEXT
        )
    """)
    conn.commit()

    inserted = 0
    import datetime
    now = datetime.datetime.now().isoformat()
    for c in all_comments:
        try:
            existing = conn.execute(
                "SELECT 1 FROM comments WHERE comment_id=?", (c["comment_id"],)
            ).fetchone()
            conn.execute("""
                INSERT INTO comments
                (comment_id, post_id, parent_id, author_name, content, publish_time, like_count, crawl_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(comment_id) DO UPDATE SET
                    author_name=excluded.author_name,
                    content=excluded.content,
                    publish_time=excluded.publish_time,
                    like_count=excluded.like_count,
                    crawl_time=excluded.crawl_time
            """, (
                c["comment_id"], c["post_id"], c["parent_id"],
                c.get("author_name", ""), c.get("content", ""),
                c.get("publish_time", ""), parse_count(c.get("like_count")),
                now
            ))
            inserted += 0 if existing else 1
        except Exception:
            pass
    conn.commit()

    # Update reply counts
    for c in all_comments:
        if c.get("level") == 1:
            reply_count = sum(1 for x in all_comments if x.get("parent_id") == c["comment_id"])
            if reply_count > 0:
                conn.execute("UPDATE comments SET reply_count=? WHERE comment_id=?", (reply_count, c["comment_id"]))

    conn.commit()
    conn.close()

    print(f"  [done] 新增评论: {inserted} (一级+子评论)")
    return inserted


def _extract_comment(item, post_id, level=1):
    selectors = SELECTORS["comment"]
    return {
        "comment_id": str(item.get_attribute(selectors["comment_id"]) or ""),
        "post_id": post_id,
        "parent_id": str(post_id) if level == 1 else "",
        "level": level,
        "author_name": read_text(item, selectors["username"]),
        "publish_time": read_text(item, selectors["time"]),
        "content": read_text(item, selectors["content"]),
        "like_count": read_text(item, selectors["like_count"], default="0"),
    }


def _extract_child(item, post_id, parent_id, child_index=0):
    selectors = SELECTORS["comment"]
    comment_id = str(item.get_attribute(selectors["comment_id"]) or "")
    if not comment_id:
        return None
    return {
        "comment_id": comment_id,
        "post_id": post_id,
        "parent_id": parent_id,
        "level": 2,
        "author_name": read_text(item, selectors["child_username"]),
        "publish_time": read_text(item, selectors["child_time"]),
        "content": read_text(item, selectors["child_content"]),
        "like_count": read_text(item, selectors["child_like"], default="0"),
    }


def crawl_top_posts_comments(page, config=None, limit=5):
    if config is None:
        config = load_config()
    conn = get_conn()
    posts = conn.execute("""
        SELECT post_id FROM posts
        WHERE post_id NOT IN (SELECT DISTINCT post_id FROM comments)
        ORDER BY CAST(comment_count AS INTEGER) DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

    if not posts:
        print("[crawl] 所有帖子评论已采集完毕")
        return 0

    total = 0
    for row in posts:
        try:
            random_comment_page_delay()
            n = crawl_post_comments(page, row["post_id"], config)
            total += n
        except Exception as e:
            print(f"  [error] 帖子 {row['post_id']} 评论采集失败: {e}")

    return total


def crawl_high_risk_posts_comments(page, config=None):
    if config is None:
        config = load_config()
    posts = get_high_risk_posts(config, eligible_only=True)
    if not posts:
        print("[crawl] no high-risk posts need a comment refresh")
        return 0

    total = 0
    for row in posts:
        try:
            random_comment_page_delay()
            total += crawl_post_comments(page, row["post_id"], config)
        except CaptchaBlocked:
            raise
        except Exception as error:
            print(f"  [error] high-risk post {row['post_id']} comment crawl failed: {error}")
    return total


def main():
    config = load_config()
    init_db()
    wait_if_needed()

    print("=" * 60)
    print("Xiaoheihe comment crawler")
    print("=" * 60)

    exit_code = 0
    try:
        with browser_session(config) as page:
            if "--post" in sys.argv:
                post_id = sys.argv[sys.argv.index("--post") + 1]
                crawl_post_comments(page, post_id, config)
            elif "--all" in sys.argv:
                conn = get_conn()
                posts = conn.execute(
                    "SELECT post_id FROM posts WHERE post_id NOT IN (SELECT DISTINCT post_id FROM comments) ORDER BY CAST(comment_count AS INTEGER) DESC"
                ).fetchall()
                conn.close()
                print(f"[crawl] pending posts: {len(posts)}")
                total = 0
                for row in posts:
                    try:
                        random_comment_page_delay()
                        total += crawl_post_comments(page, row["post_id"], config)
                    except CaptchaBlocked as error:
                        print(f"[BLOCKED] {error}")
                        exit_code = 2
                        mark_captcha_hit()
                        break
                print(f"\n[crawl] total new comments: {total}")
            else:
                crawl_high_risk_posts_comments(page, config)

        if exit_code == 0:
            mark_run_complete()
    except CaptchaBlocked as error:
        print(f"\n[BLOCKED] {error}")
        exit_code = 2
        mark_captcha_hit()
    except Exception as error:
        print(f"\n[ERROR] {safe_error_text(error)}")
        exit_code = 1
        import traceback
        traceback.print_exc()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
