"""
Day 1 核心脚本：探测小黑盒真实 DOM 选择器

用法:
    python crawler/probe_selectors.py

会完成以下操作：
    1. 打开本地浏览器，复用登录态
    2. 进入小黑盒 → 社区 → 目标游戏社区
    3. 保存每步截图和 HTML 用于选择器分析
    4. 打印当前页面常见元素列表帮助确认真实 DOM
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from crawler.browser_context import (
    launch_browser, load_config, take_screenshot, save_page_html
)
from crawler.crawl_navigation import navigate_full_flow, CaptchaBlocked


def probe_page_elements(page, label):
    print(f"\n{'='*60}")
    print(f"[probe] 探测页面元素: {label}")
    print(f"[probe] URL: {page.url}")
    print(f"{'='*60}")

    print("\n--- 通用元素统计 ---")
    tags_of_interest = {
        "链接 (a)": "a",
        "按钮 (button)": "button",
        "输入框 (input)": "input",
        "列表项 (li)": "li",
        "图片 (img)": "img",
    }

    for desc, selector in tags_of_interest.items():
        count = page.locator(selector).count()
        if 0 < count < 100:
            for i in range(min(count, 3)):
                try:
                    el = page.locator(selector).nth(i)
                    text = el.inner_text().strip()[:60]
                    cls = el.get_attribute("class") or ""
                    href = el.get_attribute("href") or ""
                    parts = [f"text=\"{text}\""] if text else []
                    if cls:
                        parts.append(f"class=\"{cls[:50]}\"")
                    if href:
                        parts.append(f"href=\"{href[:50]}\"")
                    if parts:
                        print(f"  [{desc}] #{i}: {' | '.join(parts)}")
                except Exception:
                    pass
        elif count >= 100:
            print(f"  [{desc}]: {count} 个")
        else:
            print(f"  [{desc}]: 0 个")

    print("\n--- 帖子卡片探测 ---")
    _probe_post_cards(page)

    print("\n--- 话题/游戏列表探测 ---")
    _probe_topic_list(page)

    save_page_html(page, f"probe_{label}")
    take_screenshot(page, f"probe_{label}")


def _probe_post_cards(page):
    known_selectors = [
        ("a.hb-cpt__bbs-content", "bbs content link"),
        ("div.bbs-home__content-item", "content item"),
        ("a[href*='/app/bbs/link/']", "bbs link (=post card)"),
    ]

    for sel, desc in known_selectors:
        count = page.locator(sel).count()
        if count > 0:
            print(f"  [{desc}] selector={sel}: {count} 个")
            for i in range(min(count, 3)):
                try:
                    card = page.locator(sel).nth(i)
                    title_el = card.locator(".bbs-content__title").first
                    author_el = card.locator(".list-content__username").first
                    content_el = card.locator(".bbs-content__content").first
                    comment_el = card.locator("use[xlink\\:href='#icon-bbs_comment_filled_24x24']").first
                    like_el = card.locator("use[xlink\\:href='#icon-bbs_thumbs-up_filled_24x24']").first
                    level_el = card.locator(".hb-level-tag__inner__text").first
                    href = card.get_attribute("href") or ""

                    title = title_el.inner_text().strip()[:40] if title_el.count() > 0 else "-"
                    author = author_el.inner_text().strip() if author_el.count() > 0 else "-"
                    level = level_el.inner_text().strip() if level_el.count() > 0 else "-"
                    content_preview = content_el.inner_text().strip()[:50] if content_el.count() > 0 else "-"

                    comment_count = "-"
                    if comment_el.count() > 0:
                        parent_text = page.locator(sel).nth(i).locator(".bbs-new-style-bottom__action:has(use[xlink\\:href='#icon-bbs_comment_filled_24x24']) span").last.inner_text().strip()
                        comment_count = parent_text

                    like_count = "-"
                    if like_el.count() > 0:
                        parent_text = page.locator(sel).nth(i).locator(".bbs-new-style-bottom__action:has(use[xlink\\:href='#icon-bbs_thumbs-up_filled_24x24']) span").last.inner_text().strip()
                        like_count = parent_text

                    post_id = href.split("/")[-1] if href else "-"

                    print(f"  [{i+1}] post_id={post_id} | title={title}")
                    print(f"       author={author} {level} | comment={comment_count} like={like_count}")
                    print(f"       content={content_preview}...")
                    print(f"       href={href}")
                except Exception as e:
                    print(f"  [{i+1}] 解析异常: {e}")
            break


def _probe_topic_list(page):
    topic_items = page.locator("button.bbs-home__topic-item")
    count = topic_items.count()
    if count > 0:
        print(f"  button.bbs-home__topic-item: {count} 个")
        for i in range(count):
            try:
                name_el = topic_items.nth(i).locator("p.bbs-home__topic-name")
                if name_el.count() > 0:
                    name = name_el.inner_text().strip()
                    print(f"  [{i}] {name}")
            except Exception:
                pass
    else:
        print("  未找到 bbs-home__topic-item")
        alt = page.locator("[class*='topic']").first
        if alt.count() > 0:
            print(f"  尝试其他 topic 选择器: {alt.get_attribute('class')}")


def main():
    print("=" * 60)
    print("小黑盒 MVP Day 1 - 选择器探测脚本")
    print("=" * 60)

    config = load_config()
    playwright, context, page = launch_browser(config)

    try:
        navigate_full_flow(page, config)
        probe_page_elements(page, "final")

        print("\n" + "=" * 60)
        print("Day 1 探测完成！")
        print("=" * 60)
        print(f"截图和 HTML 已保存到: {PROJECT_ROOT / 'logs'}")
        print("请检查以下内容:")
        print("  1. 截图中的页面结构")
        print("  2. HTML 文件中的实际 DOM class 和 data 属性")
        print("  3. 帖子卡片的定位方式")
        print("=" * 60)

    except CaptchaBlocked as e:
        print(f"\n[BLOCKED] 验证码拦截: {e}")
        print("请手动执行以下步骤后重试脚本:")
        print("  1. 打开 Chrome 浏览器")
        print("  2. 访问 https://www.xiaoheihe.cn 并完成登录+验证码")
        print(f"  3. 确认 browser_profile 目录存在登录态文件")
        print(f"日志目录: {PROJECT_ROOT / 'logs'}")

    except Exception as e:
        print(f"\n[ERROR] 脚本异常: {e}")
        import traceback
        traceback.print_exc()

    finally:
        context.close()
        playwright.stop()
        print("[done] 浏览器已关闭")


if __name__ == "__main__":
    main()
