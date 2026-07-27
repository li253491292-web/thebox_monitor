"""Shared high-risk post selection for detail and comment crawling."""
import datetime

from analysis.risk_scoring import score_post
from crawler.browser_context import load_config
from crawler.database import get_conn


DEFAULTS = {
    "lookback_days": 7,
    "min_risk_score": 500,
    "max_posts_per_run": 5,
    "comment_recrawl_hours": 2,
}


def _settings(config):
    configured = config.get("crawl", {}).get("high_risk", {})
    settings = {key: configured.get(key, value) for key, value in DEFAULTS.items()}
    settings["min_risk_score"] = float(config.get("analysis", {}).get("fresh_risk_threshold", settings["min_risk_score"]))
    return settings


def get_high_risk_posts(config, eligible_only=False):
    """Return recent negative posts ranked by engagement and sentiment risk."""
    settings = _settings(config)
    since = (datetime.datetime.now() - datetime.timedelta(
        days=int(settings["lookback_days"])
    )).isoformat()
    stale_before = (datetime.datetime.now() - datetime.timedelta(
        hours=int(settings["comment_recrawl_hours"])
    )).isoformat()

    eligibility_sql = ""
    params = [since]
    if eligible_only:
        eligibility_sql = """
            AND (
                COALESCE(TRIM(r.body_content), '') = ''
                OR NOT EXISTS (
                    SELECT 1 FROM comments c
                    WHERE c.post_id = r.post_id
                    GROUP BY c.post_id
                    HAVING MAX(c.crawl_time) >= ?
                )
            )
        """
        params.append(stale_before)

    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT *
        FROM (
            SELECT p.post_id, p.title, p.body_content,
                   CAST(COALESCE(p.like_count, 0) AS INTEGER) AS like_count,
                   CAST(COALESCE(p.comment_count, 0) AS INTEGER) AS comment_count,
                   s.sentiment_score, p.standard_publish_time, p.first_crawl_at
            FROM posts p
            JOIN sentiment_results s
              ON s.target_id = p.post_id AND s.target_type = 'post'
            WHERE s.sentiment_label = 'negative'
              AND COALESCE(p.standard_publish_time, p.first_crawl_at) >= ?
        ) r
        {eligibility_sql}
        ORDER BY r.comment_count DESC, r.like_count DESC
        """,
        tuple(params),
    ).fetchall()
    conn.close()
    scored = []
    scoring_config = load_config()
    for row in rows:
        post = dict(row)
        post.update(score_post(post, scoring_config))
        if post["risk_score"] >= float(settings["min_risk_score"]):
            scored.append(post)
    return sorted(scored, key=lambda post: (post["risk_score"], post["comment_count"], post["like_count"]), reverse=True)[:int(settings["max_posts_per_run"])]
