"""Shared risk scoring with a freshness multiplier for recent posts."""
import datetime as dt


DEFAULT_FRESHNESS = {
    "under_6_hours": 1.50,
    "under_24_hours": 1.25,
    "under_48_hours": 1.00,
    "historical": 0.45,
}


def _parse_time(value):
    text = str(value or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def freshness_settings(config=None):
    configured = (config or {}).get("analysis", {}).get("freshness", {})
    return {key: float(configured.get(key, value)) for key, value in DEFAULT_FRESHNESS.items()}


def base_risk_score(like_count, comment_count, sentiment_score):
    return (
        int(like_count or 0)
        + int(comment_count or 0) * 3
        + (1 - float(sentiment_score if sentiment_score is not None else 0.5)) * 300
    )


def freshness_bucket(publish_time, fallback_time=None, now=None):
    now = now or dt.datetime.now()
    published_at = _parse_time(publish_time) or _parse_time(fallback_time)
    if published_at is None:
        return "historical", None
    age_hours = max(0.0, (now - published_at).total_seconds() / 3600)
    if age_hours < 6:
        return "under_6_hours", age_hours
    if age_hours < 24:
        return "under_24_hours", age_hours
    if age_hours < 48:
        return "under_48_hours", age_hours
    return "historical", age_hours


def score_post(post, config=None, now=None):
    settings = freshness_settings(config)
    bucket, age_hours = freshness_bucket(
        post.get("standard_publish_time") or post.get("publish_time"),
        post.get("first_crawl_at"),
        now,
    )
    base_score = base_risk_score(post.get("like_count"), post.get("comment_count"), post.get("sentiment_score"))
    multiplier = settings[bucket]
    return {
        "base_risk_score": round(base_score),
        "freshness_bucket": bucket,
        "freshness_multiplier": multiplier,
        "freshness_age_hours": age_hours,
        "risk_score": round(base_score * multiplier),
    }


def risk_level(risk_score):
    score = float(risk_score or 0)
    if score >= 800:
        return "high", "高风险"
    if score >= 500:
        return "elevated", "较高风险"
    if score >= 300:
        return "medium", "中风险"
    return "low", "低风险"
