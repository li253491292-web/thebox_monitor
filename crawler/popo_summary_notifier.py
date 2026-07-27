"""Send compact morning and evening Xiaoheihe opinion summaries to POPO."""
import argparse
import datetime
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from crawler.browser_context import load_config, safe_error_text
from crawler.database import get_conn, init_db
from crawler.popo_notifier import _get_access_token, _send_text, _settings
from analysis.risk_scoring import risk_level, score_post


NEGATIVE_CATEGORIES = (
    ("技术/性能", ("崩溃", "闪退", "卡顿", "掉帧", "帧率", "优化", "加载", "存档", "画面", "bug")),
    ("版本/内容", ("更新", "版本", "车辆", "车包", "活动", "赛季", "奖励", "任务", "内容")),
    ("线上/公平", ("外挂", "作弊", "联机", "匹配", "举报", "漏洞", "封号", "封禁")),
    ("经济/进度", ("cr", "拍卖", "抽奖", "价格", "刷钱", "刷cr", "刷取", "解锁", "积分", "进度")),
    ("服务/其他", ("版权", "客服", "发货", "账号", "购买", "平台", "退款", "订单", "登录")),
)
FALLBACK_CATEGORY = "服务/其他"


def categorize_negative_post(title, content):
    text = f"{title or ''} {content or ''}".lower()
    for category, keywords in NEGATIVE_CATEGORIES:
        if any(keyword.lower() in text for keyword in keywords):
            return category
    return FALLBACK_CATEGORY


def summary_window(period, now=None):
    now = now or datetime.datetime.now()
    today = now.date()
    if period == "morning":
        end = datetime.datetime.combine(today, datetime.time(9, 0))
        start = end - datetime.timedelta(hours=16)
        label = "09:00夜间"
    elif period == "evening":
        start = datetime.datetime.combine(today, datetime.time(9, 0))
        end = datetime.datetime.combine(today, datetime.time(17, 0))
        label = "17:00日间"
    else:
        raise ValueError(f"Unsupported period: {period}")
    return start, end, label


def _load_summary(start, end, max_high_risk_posts, config):
    start_text, end_text = start.isoformat(), end.isoformat()
    conn = get_conn()
    posts = [dict(row) for row in conn.execute("""
        SELECT p.post_id, p.title, p.content_preview, p.like_count, p.comment_count,
               COALESCE(s.sentiment_label, 'pending') AS sentiment_label,
               COALESCE(s.sentiment_score, 0.5) AS sentiment_score,
               p.standard_publish_time, p.first_crawl_at
        FROM posts p
        LEFT JOIN sentiment_results s ON s.target_id=p.post_id AND s.target_type='post'
        WHERE p.first_crawl_at >= ? AND p.first_crawl_at < ?
    """, (start_text, end_text)).fetchall()]
    comment_count = conn.execute(
        "SELECT COUNT(*) FROM comments WHERE crawl_time >= ? AND crawl_time < ?", (start_text, end_text)
    ).fetchone()[0]
    run_rows = conn.execute("""
        SELECT status, COUNT(*) AS count FROM crawl_runs
        WHERE start_time >= ? AND start_time < ? GROUP BY status
    """, (start_text, end_text)).fetchall()
    high_risk_rows = [dict(row) for row in conn.execute("""
        SELECT p.post_id, p.title, p.content_preview, p.like_count, p.comment_count,
               COALESCE(s.sentiment_score, 0.5) AS sentiment_score,
               p.standard_publish_time, p.first_crawl_at,
               r.risk_score AS risk_score
        FROM risk_threshold_events r
        JOIN posts p ON p.post_id=r.post_id
        LEFT JOIN sentiment_results s ON s.target_id=p.post_id AND s.target_type='post'
        WHERE r.first_reached_at >= ? AND r.first_reached_at < ?
        ORDER BY r.risk_score DESC LIMIT ?
    """, (start_text, end_text, max_high_risk_posts)).fetchall()]
    conn.close()

    for post in posts:
        post.update(score_post(post, config))
    for post in high_risk_rows:
        post["threshold_risk_score"] = round(float(post["risk_score"]))
        post.update(score_post(post, config))

    sentiment = {"negative": 0, "neutral": 0, "positive": 0, "pending": 0}
    categories = {category: 0 for category, _ in NEGATIVE_CATEGORIES}
    categories[FALLBACK_CATEGORY] = 0
    for post in posts:
        sentiment[post["sentiment_label"] if post["sentiment_label"] in sentiment else "pending"] += 1
        if post["sentiment_label"] == "negative":
            categories[categorize_negative_post(post["title"], post["content_preview"])] += 1
    runs = {row["status"]: row["count"] for row in run_rows}
    return {
        "posts": posts,
        "sentiment": sentiment,
        "categories": categories,
        "comments": comment_count,
        "runs": runs,
        "high_risk": high_risk_rows,
    }


def _short_summary(post, limit=80):
    text = " ".join(str(post.get("content_preview") or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


def build_summary_text(start, end, label, summary):
    sentiment = summary["sentiment"]
    categories = summary["categories"]
    successful_runs = summary["runs"].get("ok", 0)
    abnormal_runs = sum(count for status, count in summary["runs"].items() if status != "ok")
    lines = [
        f"【FH6 舆情概况｜{label}】",
        f"{start:%m-%d %H:%M} - {end:%m-%d %H:%M}",
        "",
        f"📊 新增 {len(summary['posts'])}｜负面 {sentiment['negative']}｜中性 {sentiment['neutral']}｜正面 {sentiment['positive']}",
        f"高风险新增 {len(summary['high_risk'])}｜新增评论 {summary['comments']}",
        f"任务：成功 {successful_runs}｜异常 {abnormal_runs}",
        "",
        "⚠️ 负面分类",
        f"技术/性能 {categories['技术/性能']}｜版本/内容 {categories['版本/内容']}",
        f"线上/公平 {categories['线上/公平']}｜经济/进度 {categories['经济/进度']}｜服务/其他 {categories[FALLBACK_CATEGORY]}",
        "",
        "🔥 重点风险",
    ]
    if not summary["high_risk"]:
        lines.append("本时段暂无新达到阈值的高风险帖子。")
    else:
        for index, post in enumerate(summary["high_risk"], 1):
            category = categorize_negative_post(post["title"], post["content_preview"])
            lines.extend([
                f"{index}. 【{category}】{post['title'] or '（无标题）'}",
                f"触发风险 {post.get('threshold_risk_score', round(float(post['risk_score'])))}+（{risk_level(post.get('threshold_risk_score', post['risk_score']))[1]}）｜赞 {post['like_count']}｜评 {post['comment_count']}",
                _short_summary(post),
                f"https://www.xiaoheihe.cn/app/bbs/link/{post['post_id']}",
            ])
    lines.extend(["", "📝 运行", "高风险正文/评论采集：已暂停", "概况推送：正常"])
    return "\n".join(lines)


def _already_sent(summary_key):
    conn = get_conn()
    exists = conn.execute(
        "SELECT 1 FROM summary_notification_events WHERE summary_key=?", (summary_key,)
    ).fetchone()
    conn.close()
    return bool(exists)


def _record_sent(summary_key, period, start, end, message_ids):
    conn = get_conn()
    conn.execute("""
        INSERT OR IGNORE INTO summary_notification_events
        (summary_key, period, start_time, end_time, sent_at, message_ids)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (summary_key, period, start.isoformat(), end.isoformat(), datetime.datetime.now().isoformat(), ",".join(message_ids)))
    conn.commit()
    conn.close()


def send_period_summary(period, dry_run=False):
    config = load_config()
    summary_settings = config.get("notification", {}).get("summary", {})
    if not bool(summary_settings.get("enabled", True)):
        print("[summary] notification disabled")
        return 0
    settings = _settings(config)
    if not settings["enabled"] or not settings["receivers"]:
        raise RuntimeError("POPO notification is not configured")
    start, end, label = summary_window(period)
    summary_key = f"popo_summary_{end:%Y%m%d}_{period}"
    if _already_sent(summary_key) and not dry_run:
        print(f"[summary] already sent: {summary_key}")
        return 0
    summary = _load_summary(start, end, int(summary_settings.get("max_high_risk_posts", 3)), config)
    content = build_summary_text(start, end, label, summary)
    if dry_run:
        print(content.encode("ascii", "backslashreplace").decode("ascii"))
        return 0
    access_token = _get_access_token(settings)
    message_ids = [_send_text(settings, access_token, receiver, content) for receiver in settings["receivers"]]
    _record_sent(summary_key, period, start, end, message_ids)
    print(f"[summary] sent {period} summary to {len(message_ids)} receiver(s)")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", required=True, choices=("morning", "evening"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    init_db()
    try:
        return send_period_summary(args.period, dry_run=args.dry_run)
    except Exception as error:
        print(f"[summary] failed: {safe_error_text(error)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
