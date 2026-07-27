"""
帖子详情采集 — 打开帖子详情页，提取完整正文内容。
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from crawler.browser_context import browser_session, load_config, safe_wait
from crawler.content_cleaning import clean_post_body, is_comment_contaminated
from crawler.database import init_db, get_conn
from crawler.cooldown import wait_if_needed, mark_run_complete, random_post_delay




def crawl_risk_post_detail(conn, page, post_id, config=None):
    """Fetch and persist full post content for a high-risk post."""
    if config is None:
        config = load_config()

    content = crawl_post_content(page, post_id, config)
    if content and len(content) > 20:
        conn.execute(
            "UPDATE posts SET body_content=? WHERE post_id=?",
            (content, post_id)
        )
        conn.commit()
        print(f"  [risk detail] {post_id}: {content[:50]}...")
        return content
    return ""

def crawl_post_content(page, post_id, config=None):
    if config is None:
        config = load_config()

    post_url = f"https://www.xiaoheihe.cn/app/bbs/link/{post_id}"
    page.goto(post_url, wait_until="domcontentloaded")
    safe_wait(page, 4)

    content_selectors = [
        ".hb-bbs-link__content",
        ".hb-article__content",
        ".hb-article",
        ".bbs-content__content",
    ]
    for selector in content_selectors:
        try:
            elements = page.locator(selector)
            for index in range(min(elements.count(), 3)):
                element = elements.nth(index)
                text = element.evaluate("""
                    node => {
                        const copy = node.cloneNode(true);
                        copy.querySelectorAll(
                            "[class*='comment'], [class*='reply'], [data-comment-id]"
                        ).forEach(item => item.remove());
                        return copy.innerText || "";
                    }
                """)
                content = clean_post_body(text)
                if len(content) > 50 and "??" not in content[:10]:
                    return content
        except Exception:
            continue
    return ""


def clear_contaminated_body_content(conn):
    rows = conn.execute(
        "SELECT post_id, body_content FROM posts WHERE COALESCE(TRIM(body_content), '') <> ''"
    ).fetchall()
    post_ids = [row["post_id"] for row in rows if is_comment_contaminated(row["body_content"])]
    if post_ids:
        conn.executemany("UPDATE posts SET body_content='' WHERE post_id=?", [(post_id,) for post_id in post_ids])
        conn.commit()
    return post_ids


def crawl_missing_content(config=None, limit=None):
    if config is None:
        config = load_config()

    conn = get_conn()
    if limit:
        rows = conn.execute(
            """SELECT post_id FROM posts
               WHERE COALESCE(TRIM(body_content), '') = ''
                 AND COALESCE(TRIM(content_preview), '') <> ''
               LIMIT ?""",
            (limit,)
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT post_id FROM posts
               WHERE COALESCE(TRIM(body_content), '') = ''
                 AND COALESCE(TRIM(content_preview), '') <> ''"""
        ).fetchall()

    if not rows:
        print("[detail] 所有帖子已有正文")
        conn.close()
        return 0

    print(f"[detail] 待采集完整正文: {len(rows)} 个帖子")

    updated = 0

    try:
        with browser_session(config) as page:
            for row in rows:
                pid = row["post_id"]
                content = crawl_post_content(page, pid, config)
                if content and len(content) > 20:
                    conn.execute(
                        "UPDATE posts SET body_content=? WHERE post_id=?",
                        (content, pid)
                    )
                    updated += 1
                    print(f"  [{updated}] {pid}: {content[:50]}...")

                if updated % 5 == 0 and updated > 0:
                    conn.commit()
                    random_post_delay()

            conn.commit()
    except Exception as e:
        print(f"[detail] Error: {e}")
    finally:
        conn.close()
    print(f"[detail] 更新 {updated} 条帖子正文")
    return updated


if __name__ == "__main__":
    init_db()
    wait_if_needed()
    n = crawl_missing_content()
    mark_run_complete()
    print(f"Done: {n} posts updated")
