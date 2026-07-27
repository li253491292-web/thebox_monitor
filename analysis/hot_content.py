"""
热门内容分析 — 热度评分、Top N 排行、高频词统计
"""
import re
import datetime
import sys
from pathlib import Path
from collections import Counter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from crawler.database import get_conn
from crawler.browser_context import load_config


STOP_WORDS = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
    "什么", "怎么", "如何", "为什么", "可以", "这个", "那个", "还是",
    "已经", "因为", "所以", "但是", "如果", "虽然", "而且", "或者",
    "就是", "还是", "只是", "不是", "真的", "太", "比较", "非常",
    "出了", "出来", "来了", "一下", "可能", "应该", "感觉",
    "觉得", "想要", "有点", "不过", "的话", "的时候", "这次",
    "然后", "之后", "之前", "以后", "其实", "其他", "一样", "这种",
    "那种", "很多", "一些", "现在", "今天", "昨天", "明天",
    "问问", "请问", "有没有", "有人",
}

_jieba = None


def _get_jieba():
    global _jieba
    if _jieba is None:
        try:
            import jieba
            _jieba = jieba
        except ImportError:
            pass
    return _jieba


def _tokenize(text):
    """分词：优先 jieba，回退滑动窗口"""
    jieba = _get_jieba()
    if jieba:
        return jieba.lcut(text)

    text = re.sub(r"[^\u4e00-\u9fff]+", " ", text)
    words = []
    for seg in text.split():
        for i in range(len(seg)):
            for j in [2, 3, 4]:
                if i + j <= len(seg):
                    words.append(seg[i:i+j])
    return words


def calc_hot_score(like_count, comment_count, config=None):
    return like_count + comment_count * 3


def get_top_posts(limit=None, config=None):
    if config is None:
        config = load_config()
    if limit is None:
        limit = config.get("analysis", {}).get("top_n", 10)

    conn = get_conn()
    rows = conn.execute("""
        SELECT post_id, title, author_name, author_level,
               like_count, comment_count, publish_time, content_preview,
               first_crawl_at
        FROM posts
        ORDER BY CAST(like_count AS INTEGER) + CAST(comment_count AS INTEGER) * 3 DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

    results = []
    for row in rows:
        like = int(row["like_count"] or 0)
        comment = int(row["comment_count"] or 0)
        results.append({
            "post_id": row["post_id"],
            "title": row["title"],
            "author_name": row["author_name"],
            "author_level": row["author_level"],
            "like_count": like,
            "comment_count": comment,
            "hot_score": calc_hot_score(like, comment, config),
            "publish_time": row["publish_time"],
            "content_preview": row["content_preview"],
        })

    return results


def extract_keywords(limit=50, config=None):
    if config is None:
        config = load_config()
    if limit is None:
        limit = config.get("analysis", {}).get("top_keywords", 50)

    conn = get_conn()
    since = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    rows = conn.execute("""
        SELECT title, content_preview FROM posts
        WHERE date(first_crawl_at) >= date(?)
    """, (since,)).fetchall()
    conn.close()

    words = Counter()
    for row in rows:
        text = f"{row['title'] or ''} {row['content_preview'] or ''}"
        tokens = _tokenize(text)
        for t in tokens:
            t = t.strip()
            if t not in STOP_WORDS and len(t) >= 2:
                words[t] += 1

    return words.most_common(limit)


def get_daily_stats():
    conn = get_conn()
    today = datetime.date.today().isoformat()
    today_posts = conn.execute(
        "SELECT COUNT(*) FROM posts WHERE date(first_crawl_at)=date(?)",
        (today,)
    ).fetchone()[0]
    total_posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]

    today_comments = 0
    total_comments = 0
    try:
        today_comments = conn.execute(
            "SELECT COUNT(*) FROM comments WHERE date(crawl_time)=date(?)",
            (today,)
        ).fetchone()[0]
        total_comments = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
    except Exception:
        pass

    conn.close()
    return {
        "today_posts": today_posts,
        "today_comments": today_comments,
        "total_posts": total_posts,
        "total_comments": total_comments,
    }


if __name__ == "__main__":
    top = get_top_posts()
    print("Top 10 热门帖子:")
    for i, p in enumerate(top):
        print(f"  [{i+1}] {p['title'][:40]} | score={p['hot_score']} | like={p['like_count']} comment={p['comment_count']}")

    kw = extract_keywords(20)
    print(f"\nTop 20 高频词:")
    for w, c in kw:
        print(f"  {w}: {c}")
