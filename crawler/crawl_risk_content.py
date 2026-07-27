"""Fetch full text and comments only for high-risk posts."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis.sentiment import reanalyze_posts_with_body
from crawler.browser_context import browser_session, load_config
from crawler.crawl_comments import crawl_post_comments
from crawler.crawl_navigation import CaptchaBlocked
from crawler.crawl_post_detail import clear_contaminated_body_content, crawl_risk_post_detail
from crawler.database import get_conn, init_db
from crawler.risk_targets import get_high_risk_posts


def crawl_high_risk_content(config=None):
    if config is None:
        config = load_config()
    cleanup_conn = get_conn()
    try:
        cleared_ids = clear_contaminated_body_content(cleanup_conn)
    finally:
        cleanup_conn.close()
    if cleared_ids:
        print(f"[risk crawl] cleared {len(cleared_ids)} contaminated body records")
    targets = get_high_risk_posts(config, eligible_only=True)
    if not targets:
        print("[risk crawl] no high-risk posts need a detail or comment refresh")
        return []

    print(f"[risk crawl] targets: {len(targets)}")
    completed_ids = []
    conn = get_conn()
    try:
        with browser_session(config) as page:
            for target in targets:
                post_id = str(target["post_id"])
                try:
                    crawl_risk_post_detail(conn, page, post_id, config)
                    crawl_post_comments(page, post_id, config, navigate=False)
                    completed_ids.append(post_id)
                except CaptchaBlocked:
                    raise
                except Exception as error:
                    print(f"[risk crawl] post {post_id} failed: {error}")
    finally:
        conn.close()

    if completed_ids:
        reanalyze_posts_with_body(completed_ids)
    print(f"[risk crawl] complete: {len(completed_ids)} posts")
    return completed_ids


def main():
    init_db()
    try:
        crawl_high_risk_content()
    except CaptchaBlocked as error:
        print(f"[BLOCKED] {error}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
