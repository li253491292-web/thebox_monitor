"""Send newly crawled high-risk posts to a POPO robot as text messages."""
import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from crawler.browser_context import load_config
from crawler.content_cleaning import clean_post_body
from crawler.database import get_conn, init_db
from analysis.risk_scoring import risk_level, score_post


NOTIFICATION_TYPE = "popo_high_risk_post"


class PopoNotificationError(RuntimeError):
    pass


def _post_json(url, payload, headers=None, timeout=30):
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise PopoNotificationError(f"HTTP {error.code}: {body[:500]}") from error
    except urllib.error.URLError as error:
        raise PopoNotificationError(f"Network error: {error.reason}") from error
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise PopoNotificationError(f"Invalid POPO response: {body[:500]}") from error
    if parsed.get("errcode") not in (0, None):
        raise PopoNotificationError(f"POPO errcode={parsed.get('errcode')}: {parsed.get('errmsg')}")
    return parsed


def _settings(config):
    settings = config.get("notification", {}).get("popo", {})
    configured_receivers = settings.get("receivers") or [settings.get("receiver")]
    receivers = [str(receiver).strip() for receiver in configured_receivers if str(receiver or "").strip()]
    return {
        "enabled": bool(settings.get("enabled", False)),
        "api_base": str(settings.get("api_base") or "").rstrip("/"),
        "receivers": receivers,
        "app_key": os.environ.get(str(settings.get("app_key_env") or "POPO_BOT_APP_KEY"), ""),
        "app_secret": os.environ.get(str(settings.get("app_secret_env") or "POPO_BOT_APP_SECRET"), ""),
        "min_risk_score": float(settings.get("min_risk_score", 500)),
        "lookback_days": max(1, int(settings.get("lookback_days", 3))),
        "max_posts": max(1, int(settings.get("max_posts_per_message", 10))),
        "max_chars": max(500, min(int(settings.get("max_message_chars", 2800)), 3000)),
    }


def _get_access_token(settings):
    if not settings["app_key"] or not settings["app_secret"]:
        raise PopoNotificationError("Missing POPO_BOT_APP_KEY or POPO_BOT_APP_SECRET")
    response = _post_json(
        settings["api_base"] + "/open-apis/robots/v1/token",
        {"appKey": settings["app_key"], "appSecret": settings["app_secret"]},
    )
    token = (response.get("data") or {}).get("accessToken")
    if not token:
        raise PopoNotificationError("POPO token response did not include accessToken")
    return token


def _load_qualifying_posts(config, min_risk_score, lookback_days, limit):
    cutoff_date = (datetime.date.today() - datetime.timedelta(days=lookback_days - 1)).isoformat()
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT p.post_id, p.title, p.content_preview, p.body_content,
               CAST(COALESCE(p.like_count, 0) AS INTEGER) AS like_count,
               CAST(COALESCE(p.comment_count, 0) AS INTEGER) AS comment_count,
               COALESCE(s.sentiment_score, 0.5) AS sentiment_score,
               p.standard_publish_time, p.first_crawl_at
        FROM posts p
        JOIN sentiment_results s ON s.target_id=p.post_id AND s.target_type='post'
        WHERE date(p.first_crawl_at) >= date(?)
          AND s.sentiment_label='negative'
        ORDER BY p.first_crawl_at DESC
        """,
        (cutoff_date,),
    ).fetchall()
    conn.close()
    posts = []
    for row in rows:
        post = dict(row)
        post.update(score_post(post, config))
        if post["risk_score"] >= min_risk_score:
            posts.append(post)
    return sorted(posts, key=lambda post: (post["risk_score"], post["comment_count"], post["like_count"]), reverse=True)[:limit]


def _summary(post, limit=220):
    body = clean_post_body(post.get("body_content"))
    text = body or str(post.get("content_preview") or "")
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def build_message_chunks(posts, today, max_chars):
    header = f"【小黑盒高风险舆情】{today}\n首次达到推送阈值的帖子 {len(posts)} 条\n"
    chunks = []
    current = header
    current_posts = []
    for index, post in enumerate(posts, 1):
        title = str(post.get("title") or "（无标题）").strip()
        item = (
            f"\n{index}. {title}\n"
            f"摘要：{_summary(post)}\n"
            f"新鲜风险：{round(float(post['risk_score']))}（{risk_level(post['risk_score'])[1]}）｜点赞：{post['like_count']}｜评论：{post['comment_count']}\n"
            f"https://www.xiaoheihe.cn/app/bbs/link/{post['post_id']}\n"
        )
        if current_posts and len(current) + len(item) > max_chars:
            chunks.append((current.rstrip(), current_posts))
            current = f"【小黑盒高风险舆情】{today}（续）\n"
            current_posts = []
        current += item
        current_posts.append(post)
    if current_posts:
        chunks.append((current.rstrip(), current_posts))
    return chunks


def _send_text(settings, access_token, receiver, content):
    response = _post_json(
        settings["api_base"] + "/open-apis/robots/v1/im/send-msg",
        {"receiver": receiver, "msgType": "text", "message": {"content": content}},
        headers={"Open-Access-Token": access_token},
    )
    return str((response.get("data") or {}).get("msgInfo", {}).get(receiver, ""))


def _migrate_legacy_notifications(receivers, fallback_risk_score):
    """Preserve known successful legacy deliveries during the schema transition."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT target_id, sent_at, message_id
        FROM notification_events
        WHERE notification_type=? AND COALESCE(message_id, '') NOT LIKE 'manual_backfill_%'
    """, (NOTIFICATION_TYPE,)).fetchall()
    for row in rows:
        conn.execute(
            "INSERT OR IGNORE INTO risk_threshold_events (post_id, first_reached_at, risk_score) VALUES (?, ?, ?)",
            (row["target_id"], row["sent_at"], fallback_risk_score),
        )
        conn.execute(
            "UPDATE risk_threshold_events SET risk_score=? WHERE post_id=? AND risk_score<=0",
            (fallback_risk_score, row["target_id"]),
        )
        for receiver in receivers:
            conn.execute("""
                INSERT OR IGNORE INTO notification_deliveries
                (notification_type, target_id, receiver, sent_at, message_id)
                VALUES (?, ?, ?, ?, ?)
            """, (NOTIFICATION_TYPE, row["target_id"], receiver, row["sent_at"], row["message_id"]))
    conn.commit()
    conn.close()


def _ensure_risk_events(posts):
    now = datetime.datetime.now().isoformat()
    conn = get_conn()
    conn.executemany(
        """INSERT OR IGNORE INTO risk_threshold_events
           (post_id, first_reached_at, risk_score) VALUES (?, ?, ?)""",
        [(str(post["post_id"]), now, float(post["risk_score"])) for post in posts],
    )
    conn.commit()
    conn.close()


def _load_deliveries(post_ids):
    if not post_ids:
        return set()
    conn = get_conn()
    placeholders = ",".join("?" for _ in post_ids)
    rows = conn.execute(f"""
        SELECT target_id, receiver FROM notification_deliveries
        WHERE notification_type=? AND target_id IN ({placeholders})
    """, (NOTIFICATION_TYPE, *post_ids)).fetchall()
    conn.close()
    return {(str(row["target_id"]), str(row["receiver"])) for row in rows}


def _record_delivery(posts, receiver, message_id):
    now = datetime.datetime.now().isoformat()
    conn = get_conn()
    conn.executemany(
        """INSERT OR IGNORE INTO notification_deliveries
           (notification_type, target_id, receiver, sent_at, message_id)
           VALUES (?, ?, ?, ?, ?)""",
        [(NOTIFICATION_TYPE, str(post["post_id"]), receiver, now, message_id) for post in posts],
    )
    conn.commit()
    conn.close()


def send_daily_high_risk_notifications(dry_run=False):
    config = load_config()
    settings = _settings(config)
    if not settings["enabled"]:
        print("[popo] notification disabled")
        return 0
    if not settings["api_base"] or not settings["receivers"]:
        raise PopoNotificationError("Missing POPO api_base or receivers configuration")

    _migrate_legacy_notifications(settings["receivers"], settings["min_risk_score"])
    today = datetime.date.today().isoformat()
    qualifying_posts = _load_qualifying_posts(
        config, settings["min_risk_score"], settings["lookback_days"], settings["max_posts"]
    )
    if not qualifying_posts:
        print("[popo] no newly qualifying high-risk posts to send")
        return 0

    deliveries = _load_deliveries([str(post["post_id"]) for post in qualifying_posts])
    pending_by_receiver = {
        receiver: [post for post in qualifying_posts if (str(post["post_id"]), receiver) not in deliveries]
        for receiver in settings["receivers"]
    }
    if not any(pending_by_receiver.values()):
        print("[popo] all qualifying posts already delivered")
        return 0

    if dry_run:
        for receiver, posts in pending_by_receiver.items():
            for content, _ in build_message_chunks(posts, today, settings["max_chars"]):
                print(f"[popo] receiver={receiver}")
                print(content)
        return sum(len(posts) for posts in pending_by_receiver.values())

    _ensure_risk_events(qualifying_posts)
    access_token = _get_access_token(settings)
    sent_count = 0
    for receiver, posts in pending_by_receiver.items():
        for content, chunk_posts in build_message_chunks(posts, today, settings["max_chars"]):
            message_id = _send_text(settings, access_token, receiver, content)
            _record_delivery(chunk_posts, receiver, message_id)
            sent_count += len(chunk_posts)
    print(
        f"[popo] delivered {sent_count} high-risk post notification(s) "
        f"to {len(settings['receivers'])} receiver(s)"
    )
    return sent_count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    init_db()
    try:
        send_daily_high_risk_notifications(dry_run=args.dry_run)
    except PopoNotificationError as error:
        print(f"[popo] failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
