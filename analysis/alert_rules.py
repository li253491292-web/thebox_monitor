"""
舆情预警规则 — 基于阈值和增长率检测异常
"""
import sys
import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from crawler.database import get_conn


RULES = {
    "single_post_comment_spike": {
        "desc": "单帖短时评论激增",
        "check": lambda conn: _check_comment_spike(conn, hours=1, threshold=200),
    },
    "single_post_negative": {
        "desc": "高互动负面帖子",
        "check": lambda conn: _check_negative_posts(conn, threshold=50),
    },
    "hot_post_detected": {
        "desc": "高热度帖子",
        "check": lambda conn: _check_hot_posts(conn, score_threshold=500),
    },
}


def _check_comment_spike(conn, hours=1, threshold=200):
    rows = conn.execute("""
        SELECT p.post_id, p.title,
               CAST(p.comment_count AS INTEGER) as current_comments,
               MAX(CAST(c.comment_count AS INTEGER)) as old_comments
        FROM posts p
        LEFT JOIN comment_counts c ON p.post_id = c.post_id
            AND c.snapshot_at < datetime('now', ? || ' hours')
        WHERE p.first_crawl_at >= datetime('now', '-48 hours')
        GROUP BY p.post_id
        HAVING current_comments - IFNULL(old_comments, 0) > ?
    """, (f"-{hours}", threshold)).fetchall()
    return [dict(r) for r in rows]


def _check_negative_posts(conn, threshold=50):
    rows = conn.execute("""
        SELECT p.post_id, p.title, CAST(p.comment_count AS INTEGER) as neg_count
        FROM posts p
        JOIN sentiment_results s ON p.post_id = s.target_id AND s.target_type='post'
        WHERE s.sentiment_label = 'negative'
          AND CAST(p.comment_count AS INTEGER) >= ?
        ORDER BY CAST(p.comment_count AS INTEGER) DESC, s.sentiment_score ASC
        LIMIT 10
    """, (threshold,)).fetchall()
    return [dict(r) for r in rows]


def _check_hot_posts(conn, score_threshold=500):
    rows = conn.execute("""
        SELECT post_id, title, author_name,
               CAST(like_count AS INTEGER) as likes,
               CAST(comment_count AS INTEGER) as comments,
               CAST(like_count AS INTEGER) + CAST(comment_count AS INTEGER) * 3 as hot_score
        FROM posts
        WHERE CAST(like_count AS INTEGER) + CAST(comment_count AS INTEGER) * 3 > ?
        ORDER BY hot_score DESC
        LIMIT 10
    """, (score_threshold,)).fetchall()
    return [dict(r) for r in rows]


def run_alerts():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alert_events (
            alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_type TEXT,
            target_type TEXT DEFAULT 'post',
            target_id TEXT,
            alert_reason TEXT,
            metric_value REAL,
            alert_time TEXT
        )
    """)
    conn.commit()

    alerts = []
    now = datetime.datetime.now().isoformat()

    for rule_id, rule in RULES.items():
        try:
            results = rule["check"](conn)
            for r in results:
                reason = f"{rule['desc']}: {r.get('title', '')[:40]}"
                metric = r.get("current_comments") or r.get("hot_score") or r.get("neg_count", 0)

                existing = conn.execute(
                    "SELECT alert_id FROM alert_events WHERE alert_type=? AND target_id=? AND date(alert_time)=date('now')",
                    (rule_id, r.get("post_id", ""))
                ).fetchone()

                if not existing:
                    conn.execute("""
                        INSERT INTO alert_events (alert_type, target_id, alert_reason, metric_value, alert_time)
                        VALUES (?, ?, ?, ?, ?)
                    """, (rule_id, r.get("post_id", ""), reason, metric, now))
                    alerts.append({"type": rule_id, "reason": reason, "metric": metric})
        except Exception as e:
            print(f"[alert] {rule_id} check failed: {e}")

    conn.commit()
    conn.close()

    print(f"[alert] 检测到 {len(alerts)} 条预警")
    for a in alerts:
        print(f"  [{a['type']}] {a['reason']} (metric={a['metric']})")
    return alerts


if __name__ == "__main__":
    run_alerts()
