"""
SQLite 持久化层 — 帖子去重、评论存储、爬取记录。

表结构:
    posts            — 帖子（post_id 主键，自动去重）
    crawl_runs       — 每次爬取的运行记录
    comment_counts   — 帖子评论数快照（跟踪增长）
"""
import sqlite3
import datetime
import re
import uuid
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "xiaoheihe.sqlite"


def _resolve_db_path():
    config_path = PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        return DEFAULT_DB_PATH

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception:
        return DEFAULT_DB_PATH

    db_path = config.get("database", {}).get("path") or str(DEFAULT_DB_PATH)
    path = Path(db_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


DB_PATH = _resolve_db_path()


def parse_count(value):
    """Parse common interaction counts, such as 1.2w, 3,000, and --."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--"}:
        return 0

    match = re.search(r"(\d+(?:\.\d+)?)\s*([\u4e07\u5343wWkK]?)", text)
    if not match:
        return 0

    number = float(match.group(1))
    unit = match.group(2)
    if unit in {"\u4e07", "w", "W"}:
        number *= 10000
    elif unit in {"\u5343", "k", "K"}:
        number *= 1000
    return int(number)


NAV_NOISE_ITEMS = (
    "\u9996\u9875",
    "\u793e\u533a",
    "\u5c0f\u9ed1\u76d2\u52a0\u901f\u5668",
    "\u9ed1\u76d2\u8bed\u97f3",
    "\u9ed1\u76d2\u5de5\u574a",
    "\u5f00\u653e\u5e73\u53f0",
    "\u52a0\u5165\u6211\u4eec",
    "\u53d1\u5e03\u5185\u5bb9",
)


def _normalize_noise_text(value):
    return "\n".join(line.strip() for line in str(value or "").splitlines() if line.strip())


def is_navigation_noise(value):
    """Return True when text is exactly the injected Xiaoheihe navigation block."""
    normalized = _normalize_noise_text(value)
    if not normalized:
        return False
    lines = normalized.split("\n")
    return len(lines) >= 5 and set(lines).issubset(set(NAV_NOISE_ITEMS))


def clean_text_field(value):
    return "" if is_navigation_noise(value) else (value or "")


def clean_post_data(data):
    cleaned = dict(data)
    for key in ("title", "content_preview", "publish_time"):
        if key in cleaned:
            cleaned[key] = clean_text_field(cleaned.get(key))
    return cleaned


def clean_existing_navigation_noise():
    """Clean already persisted injected navigation text. Returns affected row count."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT post_id, title, content_preview, publish_time FROM posts"
    ).fetchall()
    affected = 0
    for row in rows:
        updates = {}
        for key in ("title", "content_preview", "publish_time"):
            value = row[key]
            if is_navigation_noise(value):
                updates[key] = ""
        if updates:
            assignments = ", ".join(f"{key}=?" for key in updates)
            params = list(updates.values()) + [row["post_id"]]
            conn.execute(f"UPDATE posts SET {assignments} WHERE post_id=?", params)
            affected += 1
    conn.commit()
    conn.close()
    return affected


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _mark_stale_runs(conn, now=None):
    now = now or datetime.datetime.now()
    return conn.execute(
        "UPDATE crawl_runs SET end_time=?, status='interrupted' "
        "WHERE status='running' AND start_time < ?",
        (now.isoformat(), (now - datetime.timedelta(hours=2)).isoformat()),
    ).rowcount


def mark_runs_timed_out_since(start_time):
    """Mark crawler runs abandoned by a scheduler subprocess timeout."""
    conn = get_conn()
    now = datetime.datetime.now().isoformat()
    affected = conn.execute(
        """UPDATE crawl_runs SET end_time=?, status='timeout'
           WHERE status='running' AND start_time >= ?""",
        (now, start_time.isoformat()),
    ).rowcount
    conn.commit()
    conn.close()
    return affected



def add_body_content_column(conn=None):
    """Add body_content TEXT column to posts table if it does not exist."""
    close_after = False
    if conn is None:
        conn = get_conn()
        close_after = True
    try:
        conn.execute("ALTER TABLE posts ADD COLUMN body_content TEXT")
        conn.commit()
        print("[db] body_content column added")
    except sqlite3.OperationalError:
        pass
    finally:
        if close_after:
            conn.close()


def update_post_body(post_id, body_content):
    """Update body_content for a given post."""
    conn = get_conn()
    conn.execute(
        "UPDATE posts SET body_content=? WHERE post_id=?",
        (body_content, post_id)
    )
    conn.commit()
    conn.close()

def init_db():
    conn = get_conn()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS posts (
            post_id         TEXT PRIMARY KEY,
            author_name     TEXT,
            author_level    TEXT,
            publish_time    TEXT,
            publish_time_raw TEXT,
            publish_time_confidence TEXT,
            publish_time_crawled_at TEXT,
            standard_publish_time TEXT,
            standard_publish_time_source TEXT,
            standard_publish_time_confidence REAL,
            title           TEXT,
            content_preview TEXT,
            like_count      INTEGER DEFAULT 0,
            comment_count   INTEGER DEFAULT 0,
            source_url      TEXT,
            first_crawl_at  TEXT,
            last_crawl_at   TEXT,
            crawl_count     INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS crawl_runs (
            run_id          TEXT PRIMARY KEY,
            start_time      TEXT,
            end_time        TEXT,
            total_scrolled   INTEGER DEFAULT 0,
            new_posts        INTEGER DEFAULT 0,
            updated_posts    INTEGER DEFAULT 0,
            captcha_hit      INTEGER DEFAULT 0,
            status           TEXT DEFAULT 'ok'
        );

        CREATE TABLE IF NOT EXISTS comments (
            comment_id TEXT PRIMARY KEY,
            post_id TEXT,
            parent_id TEXT,
            author_name TEXT,
            content TEXT,
            publish_time TEXT,
            like_count INTEGER DEFAULT 0,
            reply_count INTEGER DEFAULT 0,
            crawl_time TEXT
        );

        CREATE TABLE IF NOT EXISTS comment_counts (
            post_id         TEXT,
            comment_count   INTEGER,
            snapshot_at     TEXT,
            PRIMARY KEY (post_id, snapshot_at)
        );

        CREATE TABLE IF NOT EXISTS sentiment_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_type TEXT DEFAULT 'post',
            target_id TEXT,
            sentiment_score REAL,
            sentiment_label TEXT,
            content_text TEXT,
            analyze_time TEXT,
            UNIQUE(target_type, target_id)
        );

        CREATE TABLE IF NOT EXISTS alert_events (
            alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_type TEXT,
            target_type TEXT DEFAULT 'post',
            target_id TEXT,
            alert_reason TEXT,
            metric_value REAL,
            alert_time TEXT
        );

        CREATE TABLE IF NOT EXISTS notification_events (
            notification_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            event_date TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            message_id TEXT,
            PRIMARY KEY (notification_type, target_id)
        );

        CREATE TABLE IF NOT EXISTS summary_notification_events (
            summary_key TEXT PRIMARY KEY,
            period TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            message_ids TEXT
        );

        CREATE TABLE IF NOT EXISTS risk_threshold_events (
            post_id TEXT PRIMARY KEY,
            first_reached_at TEXT NOT NULL,
            risk_score REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notification_deliveries (
            notification_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            receiver TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            message_id TEXT,
            PRIMARY KEY (notification_type, target_id, receiver)
        );
    """)
    for column in (
        "publish_time_raw", "publish_time_confidence", "publish_time_crawled_at",
        "standard_publish_time", "standard_publish_time_source", "standard_publish_time_confidence",
    ):
        try:
            conn.execute(f"ALTER TABLE posts ADD COLUMN {column} TEXT")
        except sqlite3.OperationalError:
            pass
    add_body_content_column(conn)
    recovered = _mark_stale_runs(conn)
    conn.commit()
    conn.close()
    if recovered:
        print(f"[db] marked {recovered} stale crawl runs as interrupted")
    print(f"[db] ready: {DB_PATH}")


def start_run():
    now = datetime.datetime.now()
    run_id = f"{now.strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}"
    conn = get_conn()
    _mark_stale_runs(conn, now)
    conn.execute(
        "INSERT INTO crawl_runs (run_id, start_time, status) VALUES (?, ?, ?)",
        (run_id, now.isoformat(), "running")
    )
    conn.commit()
    conn.close()
    return run_id


def finish_run(run_id, new_posts=0, updated_posts=0, total_scrolled=0, captcha_hit=0, status="ok"):
    conn = get_conn()
    conn.execute("""
        UPDATE crawl_runs
        SET end_time=?, new_posts=?, updated_posts=?, total_scrolled=?,
            captcha_hit=?, status=?
        WHERE run_id=?
    """, (datetime.datetime.now().isoformat(), new_posts, updated_posts,
          total_scrolled, captcha_hit, status, run_id))
    conn.commit()
    conn.close()


def upsert_post(data):
    """
    插入或更新帖子。post_id 已存在则更新互动数和 last_crawl_at，
    并保存 comment_count 快照以跟踪增长。
    返回: 'new' | 'updated' | 'skipped'
    """
    conn = get_conn()
    now = datetime.datetime.now().isoformat()

    existing = conn.execute(
        "SELECT post_id, comment_count, like_count FROM posts WHERE post_id=?",
        (data["post_id"],)
    ).fetchone()

    if existing is None:
        conn.execute("""
            INSERT INTO posts (post_id, author_name, author_level, publish_time,
                publish_time_raw, publish_time_confidence, publish_time_crawled_at,
                title, content_preview, like_count, comment_count, source_url,
                first_crawl_at, last_crawl_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["post_id"], data.get("author_name", ""),
            data.get("author_level", ""), data.get("publish_time", ""),
            data.get("publish_time_raw", ""), data.get("publish_time_confidence", ""),
            data.get("publish_time_crawled_at", ""),
            data.get("title", ""), data.get("content_preview", ""),
            parse_count(data.get("like_count")),
            parse_count(data.get("comment_count")),
            data.get("source_url", ""), now, now
        ))
        comment_count = parse_count(data.get("comment_count"))
        conn.execute(
            "INSERT OR IGNORE INTO comment_counts (post_id, comment_count, snapshot_at) VALUES (?, ?, ?)",
            (data["post_id"], comment_count, now)
        )
        conn.commit()
        conn.close()
        return "new"

    old_like = existing["like_count"] or 0
    old_comment = existing["comment_count"] or 0
    new_like = parse_count(data.get("like_count"))
    new_comment = parse_count(data.get("comment_count"))

    if new_like != old_like or new_comment != old_comment:
        conn.execute("""
            UPDATE posts SET like_count=?, comment_count=?,
                author_level=?, publish_time=?, publish_time_raw=?,
                publish_time_confidence=?, publish_time_crawled_at=?,
                last_crawl_at=?, crawl_count=crawl_count+1
            WHERE post_id=?
        """, (
            new_like, new_comment, data.get("author_level", ""),
            data.get("publish_time", ""), data.get("publish_time_raw", ""),
            data.get("publish_time_confidence", ""), data.get("publish_time_crawled_at", ""),
            now, data["post_id"]
        ))
        conn.execute(
            "INSERT OR IGNORE INTO comment_counts (post_id, comment_count, snapshot_at) VALUES (?, ?, ?)",
            (data["post_id"], new_comment, now)
        )
        conn.commit()
        conn.close()
        return "updated"

    conn.close()
    return "skipped"


def get_stats():
    conn = get_conn()
    total_posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    total_new_today = conn.execute(
        "SELECT COUNT(*) FROM posts WHERE date(first_crawl_at)=date(?)",
        (datetime.date.today().isoformat(),)
    ).fetchone()[0]
    conn.close()
    return {"total_posts": total_posts, "new_today": total_new_today}


if __name__ == "__main__":
    init_db()
    stats = get_stats()
    print(f"帖子总数: {stats['total_posts']}, 今日新增: {stats['new_today']}")
