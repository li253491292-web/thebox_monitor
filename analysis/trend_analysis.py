"""
舆情趋势分析 — 小时/日维度统计、互动增长、情感趋势
"""
import sys
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from crawler.database import get_conn


def get_hourly_trend(hours=24):
    conn = get_conn()
    rows = conn.execute("""
        SELECT strftime('%Y-%m-%d %H:00', COALESCE(NULLIF(publish_time, ''), first_crawl_at)) as hour,
               COUNT(*) as post_count
        FROM posts
        WHERE COALESCE(NULLIF(publish_time, ''), first_crawl_at) >= datetime('now', ? || ' hours')
        GROUP BY hour
        ORDER BY hour
    """, (f"-{hours}",)).fetchall()
    conn.close()
    return [(r["hour"], r["post_count"]) for r in rows]


def get_daily_trend(days=7):
    conn = get_conn()
    rows = conn.execute("""
        SELECT date(COALESCE(NULLIF(publish_time, ''), first_crawl_at)) as day, COUNT(*) as post_count
        FROM posts
        WHERE COALESCE(NULLIF(publish_time, ''), first_crawl_at) >= date('now', ? || ' days')
        GROUP BY day
        ORDER BY day
    """, (f"-{days}",)).fetchall()
    conn.close()
    return [(r["day"], r["post_count"]) for r in rows]


def get_interaction_growth():
    conn = get_conn()
    rows = conn.execute("""
        SELECT p.post_id, p.title,
               CAST(p.like_count AS INTEGER) as current_like,
               CAST(p.comment_count AS INTEGER) as current_comment,
               CAST(p.first_crawl_at AS TEXT) as first_seen
        FROM posts p
        ORDER BY CAST(p.comment_count AS INTEGER) DESC
        LIMIT 20
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]
    conn.close()
    return [dict(r) for r in rows]


def get_sentiment_trend():
    conn = get_conn()
    rows = conn.execute("""
        SELECT date(s.analyze_time) as day,
               s.sentiment_label,
               COUNT(*) as cnt
        FROM sentiment_results s
        JOIN posts p ON s.target_id = p.post_id
        WHERE s.analyze_time >= date('now', '-7 days')
        GROUP BY day, s.sentiment_label
        ORDER BY day, s.sentiment_label
    """).fetchall()
    conn.close()

    trend = defaultdict(lambda: {"positive": 0, "neutral": 0, "negative": 0})
    for r in rows:
        trend[r["day"]][r["sentiment_label"]] = r["cnt"]
    return dict(trend)


if __name__ == "__main__":
    print("Hourly trend:")
    for hour, count in get_hourly_trend():
        print(f"  {hour}: {count}")

    print("\nDaily trend:")
    for day, count in get_daily_trend():
        print(f"  {day}: {count}")

    print("\nInteraction stats (Top 5):")
    for r in get_interaction_growth()[:5]:
        print(f"  {r['title'][:40]}: like={r['current_like']}, comment={r['current_comment']}")
