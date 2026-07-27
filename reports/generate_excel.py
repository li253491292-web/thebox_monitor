"""
Excel 多 Sheet 导出 — posts、comment_counts、sentiment
"""
import sys
from pathlib import Path
import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from crawler.browser_context import load_config
from crawler.database import get_conn
from analysis.hot_content import get_daily_stats
from analysis.sentiment import get_sentiment_summary


def export_excel():
    config = load_config()
    today = datetime.date.today().isoformat()

    try:
        import pandas as pd
    except ImportError:
        print("[export] pandas 未安装，跳过 Excel 导出")
        return None

    conn = get_conn()

    report_dir = Path(config.get("report", {}).get("output_dir", "./reports"))
    if not report_dir.is_absolute():
        report_dir = PROJECT_ROOT / report_dir
    report_dir.mkdir(parents=True, exist_ok=True)

    filepath = report_dir / f"data_export_{today}.xlsx"

    with pd.ExcelWriter(str(filepath), engine="openpyxl") as writer:
        posts_df = pd.read_sql_query(
            "SELECT post_id, source_url, title, author_name, author_level, publish_time, "
            "standard_publish_time, standard_publish_time_source, like_count, comment_count, "
            "content_preview, body_content, first_crawl_at, last_crawl_at FROM posts "
            "ORDER BY first_crawl_at DESC",
            conn
        )
        posts_df.to_excel(writer, sheet_name="posts", index=False)

        comment_counts_df = pd.read_sql_query(
            "SELECT * FROM comment_counts ORDER BY snapshot_at DESC",
            conn,
        )
        comment_counts_df.to_excel(writer, sheet_name="comment_counts", index=False)

        sentiment_df = pd.read_sql_query(
            "SELECT * FROM sentiment_results ORDER BY analyze_time DESC",
            conn
        )
        sentiment_df.to_excel(writer, sheet_name="sentiment", index=False)

        runs_df = pd.read_sql_query(
            "SELECT * FROM crawl_runs ORDER BY start_time DESC LIMIT 50",
            conn
        )
        runs_df.to_excel(writer, sheet_name="crawl_runs", index=False)

    conn.close()
    print(f"[export] Excel 已导出: {filepath}")
    return str(filepath)


if __name__ == "__main__":
    export_excel()
