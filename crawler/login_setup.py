"""
手动登录辅助脚本 — 记录登录态到 browser_profile

用法:
    python crawler/login_setup.py

会打开浏览器到小黑盒首页，浏览器会保持打开。
你在浏览器中手动登录（扫码/手机验证），完成后在终端输入 'done' 回车。
脚本会验证 cookies 是否已保存。
"""
import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from crawler.browser_context import launch_browser, load_config, take_screenshot, save_page_html


import time


def main():
    config = load_config()
    timeout = int(os.environ.get("LOGIN_TIMEOUT", "90"))

    print("=" * 60)
    print("小黑盒手动登录助手")
    print("=" * 60)
    print(f"浏览器窗口即将打开，请在 {timeout} 秒内完成登录操作")
    print("如果出现验证码，也请在浏览器中手动完成")
    print()

    playwright, context, page = launch_browser(config)

    profile_dir_raw = config["browser"]["user_data_dir"]
    if not os.path.isabs(profile_dir_raw):
        profile_dir_raw = PROJECT_ROOT / profile_dir_raw
    profile_dir = Path(profile_dir_raw).resolve()
    print(f"[profile] 路径: {profile_dir}")

    try:
        page.goto(config["crawl"]["start_url"], wait_until="domcontentloaded")
        print(f"[page] 已打开: {page.url}")
        take_screenshot(page, "login_before")
        save_page_html(page, "login_before")
        print("[page] 初始截图: logs/login_before.png")

        print(f"\n>>> 等待 {timeout} 秒供你手动登录...")
        print("（浏览器窗口已打开，请在其中完成登录/扫码/验证码）\n")

        for remaining in range(timeout, 0, -10):
            time.sleep(10)
            url = page.url
            cookies = context.cookies()
            print(f"  [{timeout - remaining + 10}s] URL={url[:60]} | cookies={len(cookies)}")

        print("\n[verify] 时间到，验证登录态...")

        page.reload(wait_until="domcontentloaded")
        take_screenshot(page, "login_after")
        save_page_html(page, "login_after")
        print(f"[verify] 重新加载后 URL: {page.url}")

        cookies = context.cookies()
        print(f"\n[cookies] 共 {len(cookies)} 个 cookie:")
        for c in cookies[:15]:
            print(f"  {c['name']:30s} domain={c['domain']:25s} httpOnly={c['httpOnly']}")

        if len(cookies) > 0:
            print(f"\n[OK] 登录态已保存！({len(cookies)} 个 cookie)")
            print(f"   存储在: {profile_dir}")
        else:
            print(f"\n[WARN] 未检测到 cookies。Browser profile 目录: {profile_dir}")
            files_in_profile = list(profile_dir.rglob("*")) if profile_dir.exists() else []
            print(f"   目录文件数: {len(files_in_profile)}")

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()

    finally:
        print("\n关闭浏览器...")
        context.close()
        playwright.stop()
        print("Done.")


if __name__ == "__main__":
    main()
