"""评论 DOM 结构探测 — 打开一个高评论帖子，截图+保存 HTML"""
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from crawler.browser_context import launch_browser, load_config, safe_wait, take_screenshot, save_page_html
from crawler.database import get_conn

c = get_conn()
row = c.execute(
    "SELECT post_id, comment_count FROM posts WHERE CAST(comment_count AS INTEGER) > 50 "
    "ORDER BY CAST(comment_count AS INTEGER) DESC LIMIT 1"
).fetchone()
c.close()

if not row:
    print("No posts with comments found")
    sys.exit(1)

post_id = row[0]
comment_count = row[1]
post_url = f"https://www.xiaoheihe.cn/app/bbs/link/{post_id}"
print(f"Probing post {post_id} ({comment_count} comments)")
print(f"URL: {post_url}")

config = load_config()
playwright, context, page = launch_browser(config)

try:
    page.goto(post_url, wait_until="domcontentloaded")
    safe_wait(page, 5)
    print(f"URL: {page.url}")
    take_screenshot(page, "comment_probe_initial")

    for i in range(3):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        safe_wait(page, 3)

    save_page_html(page, "comment_probe")
    take_screenshot(page, "comment_probe_final")

    comment_els = page.locator("[class*=comment]").count()
    reply_els = page.locator("[class*=reply]").count()
    print(f"Elements with 'comment' class: {comment_els}")
    print(f"Elements with 'reply' class: {reply_els}")

    expand_btns = page.locator("text=展开").count()
    print(f"'Expand' buttons: {expand_btns}")

except Exception as e:
    print(f"Error: {e}")
finally:
    context.close()
    playwright.stop()
    print("Done")
