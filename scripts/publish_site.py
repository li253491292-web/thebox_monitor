"""Build a GitHub Pages-ready static site from the latest generated dashboard."""
import datetime
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = PROJECT_ROOT / "reports"
SITE_DIR = PROJECT_ROOT / "site"
REPORT_PATH = REPORTS_DIR / f"report_{datetime.date.today().isoformat()}.html"


def build_site():
    if not REPORT_PATH.exists():
        raise FileNotFoundError(f"Latest HTML report was not found: {REPORT_PATH}")

    SITE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPORT_PATH, SITE_DIR / "report_latest.html")
    shutil.copy2(REPORTS_DIR / "echarts.min.js", SITE_DIR / "echarts.min.js")
    (SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")
    (SITE_DIR / "index.html").write_text(
        """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="0; url=report_latest.html"><title>FH6 小黑盒舆情监控</title></head>
<body><p>正在打开最新报告：<a href="report_latest.html">FH6 小黑盒舆情监控</a></p></body></html>
""",
        encoding="utf-8",
    )
    print(f"[site] published {REPORT_PATH.name} to {SITE_DIR}")


if __name__ == "__main__":
    build_site()
