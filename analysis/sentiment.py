"""
LLM-first sentiment analysis for Xiaoheihe opinion monitoring.

Default mode uses an OpenAI-compatible Chat Completions endpoint configured in
config.yaml under analysis.llm. It writes post-level sentiment into SQLite.
"""
import argparse
import http.client
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from crawler.database import get_conn, init_db, clean_text_field
from crawler.browser_context import load_config

LABELS = {"positive", "neutral", "negative"}


def _resolve_env_value(value):
    if not isinstance(value, str):
        return value
    value = value.strip()
    if value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1], "")
    return value


def _get_llm_config(config):
    llm = config.get("analysis", {}).get("llm", {})
    return {
        "enabled": bool(llm.get("enabled", True)),
        "api_base": str(llm.get("api_base") or "https://api.openai.com/v1").rstrip("/"),
        "api_key": _resolve_env_value(llm.get("api_key") or "${OPENAI_API_KEY}"),
        "model": llm.get("model") or "gpt-4o-mini",
        "max_tokens": int(llm.get("max_tokens", 2000)),
        "temperature": float(llm.get("temperature", 0.1)),
        "batch_size": int(llm.get("batch_size", 10)),
        "timeout": int(llm.get("timeout_seconds", 90)),
        "wire_api": str(llm.get("wire_api") or "chat_completions"),
    }


def _ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sentiment_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_type TEXT DEFAULT 'post',
            target_id TEXT,
            sentiment_score REAL,
            sentiment_label TEXT,
            content_text TEXT,
            analyze_time TEXT,
            UNIQUE(target_type, target_id)
        )
    """)
    conn.commit()




def _load_posts_by_ids(conn, post_ids):
    placeholders = ','.join('?' for _ in post_ids)
    sql = f"""
        SELECT p.post_id, p.title, p.content_preview, p.body_content,
               p.like_count, p.comment_count
        FROM posts p
        WHERE p.post_id IN ({placeholders})
    """
    rows = conn.execute(sql, tuple(post_ids)).fetchall()
    posts = []
    for row in rows:
        item = dict(row)
        item["title"] = clean_text_field(item.get("title"))
        item["content_preview"] = clean_text_field(item.get("content_preview"))
        item["body_content"] = clean_text_field(item.get("body_content"))
        posts.append(item)
    return posts


def reanalyze_posts_with_body(post_ids, llm_cfg=None):
    """Re-analyze sentiment for given post_ids using body_content when available."""
    config = load_config()
    if llm_cfg is None:
        llm_cfg = _get_llm_config(config)
    if not llm_cfg["enabled"]:
        raise RuntimeError("analysis.llm.enabled is false")
    if not llm_cfg["api_key"]:
        if llm_cfg["api_base"].startswith(("http://127.0.0.1", "http://localhost")):
            llm_cfg["api_key"] = "local-newapi"
        else:
            raise RuntimeError("Missing LLM API key")

    post_ids = [str(pid) for pid in post_ids if pid]
    if not post_ids:
        print("[sentiment] no post ids provided")
        return 0

    conn = get_conn()
    _ensure_table(conn)
    posts = _load_posts_by_ids(conn, post_ids)
    if not posts:
        print("[sentiment] no matching posts found")
        conn.close()
        return 0
    print(f"[sentiment] reanalyze: {len(posts)} posts")

    batch_size = max(1, min(llm_cfg["batch_size"], 3 if llm_cfg.get("wire_api") == "responses" else llm_cfg["batch_size"]))
    total_saved = 0
    for start in range(0, len(posts), batch_size):
        batch = posts[start:start + batch_size]
        posts_by_id = {str(p["post_id"]): p for p in batch}
        for attempt in range(3):
            try:
                if llm_cfg.get("wire_api") == "responses":
                    results = _call_responses_compatible(llm_cfg, batch)
                else:
                    results = _call_openai_compatible(llm_cfg, batch)
                saved = _save_results(conn, posts_by_id, results)
                total_saved += saved
                print(f"[sentiment] reanalyze batch {start // batch_size + 1}: saved {saved}/{len(batch)}")
                break
            except Exception as exc:
                print(f"[sentiment] reanalyze error attempt={attempt+1}: {exc}")
                if attempt == 2:
                    if len(batch) > 1:
                        saved = _analyze_single_fallback(conn, llm_cfg, batch)
                        total_saved += saved
                        print(f"[sentiment] reanalyze fallback saved {saved}/{len(batch)}")
                        break
                    conn.close()
                    raise
                time.sleep(2 * (attempt + 1))

    conn.close()
    print(f"[sentiment] reanalyze complete: {total_saved} updated")
    return total_saved

def _load_pending_posts(conn, limit=None):
    sql = """
        SELECT p.post_id, p.title, p.content_preview, p.like_count, p.comment_count
        FROM posts p
        LEFT JOIN sentiment_results s ON s.target_id = p.post_id AND s.target_type='post'
        WHERE s.target_id IS NULL
        ORDER BY p.first_crawl_at DESC
    """
    if limit:
        sql += " LIMIT ?"
        rows = conn.execute(sql, (limit,)).fetchall()
    else:
        rows = conn.execute(sql).fetchall()
    posts = []
    for row in rows:
        item = dict(row)
        item["title"] = clean_text_field(item.get("title"))
        item["content_preview"] = clean_text_field(item.get("content_preview"))
        posts.append(item)
    return posts


def _build_prompt(batch):
    payload = []
    for post in batch:
        title = post.get('title') or ''
        body = post.get('body_content') or ''
        preview = post.get('content_preview') or ''
        text = f"{title}\n{body or preview}".strip()[:1500]
        payload.append({
            "post_id": str(post["post_id"]),
            "text": text,
            "like_count": int(post.get("like_count") or 0),
            "comment_count": int(post.get("comment_count") or 0),
        })
    system = (
        "你是游戏社区舆情分析师。请判断小黑盒帖子对目标游戏的情感倾向。"
        "只输出 JSON，不要输出 Markdown。label 只能是 positive、neutral、negative。"
        "score 为 0 到 1，越接近 1 越正向，越接近 0 越负向，0.5 为中性。"
        "要区分：攻略/求助/客观新闻通常为 neutral；明确赞美体验为 positive；"
        "吐槽、退款、bug、外挂、崩溃、机制不满、反讽负面为 negative。"
    )
    user = {
        "task": "analyze_sentiment",
        "rules": "Return compact JSON only. items must include post_id,label,score. No reason unless necessary.",
        "schema": {"items": [{"post_id": "string", "label": "positive|neutral|negative", "score": "0..1"}]},
        "posts": payload,
    }
    return system, json.dumps(user, ensure_ascii=False)


def _extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _extract_responses_text(parsed):
    texts = []
    for item in parsed.get("output", []) or []:
        for content in item.get("content", []) or []:
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                texts.append(content["text"])
    if texts:
        return "\n".join(texts)
    if parsed.get("output_text"):
        return parsed["output_text"]
    raise ValueError("Responses API returned no output text")


def _call_responses_compatible(llm_cfg, batch):
    system, user = _build_prompt(batch)
    body = {
        "model": llm_cfg["model"],
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system}]},
            {"role": "user", "content": [{"type": "input_text", "text": user}]},
        ],
        "temperature": llm_cfg["temperature"],
        "max_output_tokens": llm_cfg["max_tokens"],
        "text": {"format": {"type": "json_object"}},
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        llm_cfg["api_base"] + "/responses",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {llm_cfg['api_key']}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=llm_cfg["timeout"]) as resp:
        try:
            raw_bytes = resp.read()
        except http.client.IncompleteRead as exc:
            raw_bytes = exc.partial
        raw = raw_bytes.decode("utf-8", errors="replace")
    parsed = json.loads(raw)
    content = _extract_responses_text(parsed)
    result = _extract_json(content)
    if isinstance(result, list):
        return result
    return result.get("items") or result.get("results") or []


def _call_openai_compatible(llm_cfg, batch):
    system, user = _build_prompt(batch)
    body = {
        "model": llm_cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": llm_cfg["temperature"],
        "max_tokens": llm_cfg["max_tokens"],
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        llm_cfg["api_base"] + "/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {llm_cfg['api_key']}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=llm_cfg["timeout"]) as resp:
        try:
            raw_bytes = resp.read()
        except http.client.IncompleteRead as exc:
            raw_bytes = exc.partial
        raw = raw_bytes.decode("utf-8", errors="replace")
    parsed = json.loads(raw)
    content = parsed["choices"][0]["message"]["content"]
    result = _extract_json(content)
    if isinstance(result, list):
        return result
    return result.get("items") or result.get("results") or []


def _normalize_result(item, fallback_post_id):
    post_id = str(item.get("post_id") or fallback_post_id)
    label = str(item.get("label") or item.get("sentiment_label") or "neutral").lower()
    if label not in LABELS:
        label = "neutral"
    try:
        score = float(item.get("score", item.get("sentiment_score", 0.5)))
    except Exception:
        score = 0.5
    score = max(0.0, min(1.0, score))
    reason = str(item.get("reason") or "")[:160]
    tags = item.get("topic_tags") or item.get("tags") or []
    if isinstance(tags, list):
        tag_text = "、".join(str(x) for x in tags[:5])
    else:
        tag_text = str(tags)
    return post_id, label, score, reason, tag_text[:120]


def _save_results(conn, posts_by_id, results):
    now = datetime.datetime.now().isoformat()
    count = 0
    for item in results:
        post_id, label, score, reason, tag_text = _normalize_result(item, item.get("post_id", ""))
        if post_id not in posts_by_id:
            continue
        post = posts_by_id[post_id]
        text = f"{post.get('title') or ''} {post.get('content_preview') or ''}".strip()[:200]
        if reason or tag_text:
            text = (text + f" | LLM原因:{reason} | 标签:{tag_text}")[:500]
        conn.execute("""
            INSERT OR REPLACE INTO sentiment_results
            (target_type, target_id, sentiment_score, sentiment_label, content_text, analyze_time)
            VALUES ('post', ?, ?, ?, ?, ?)
        """, (post_id, score, label, text, now))
        count += 1
    conn.commit()
    return count


def _analyze_single_fallback(conn, llm_cfg, batch):
    saved_total = 0
    for post in batch:
        posts_by_id = {str(post["post_id"]): post}
        try:
            if llm_cfg.get("wire_api") == "responses":
                results = _call_responses_compatible(llm_cfg, [post])
            else:
                results = _call_openai_compatible(llm_cfg, [post])
            saved_total += _save_results(conn, posts_by_id, results)
        except Exception as exc:
            print(f"[sentiment] single fallback failed post={post['post_id']}: {exc}")
    return saved_total


def analyze_posts(mode="llm", limit=None, clear=False):
    config = load_config()
    conn = get_conn()
    _ensure_table(conn)
    if clear:
        deleted = conn.execute("DELETE FROM sentiment_results WHERE target_type='post'").rowcount
        conn.commit()
        print(f"[sentiment] cleared old post sentiment rows: {deleted}")

    posts = _load_pending_posts(conn, limit=limit)
    if not posts:
        print("[sentiment] 没有待分析的帖子")
        conn.close()
        return get_sentiment_summary()
    print(f"[sentiment] 待分析: {len(posts)} 条")

    if mode != "llm":
        print("[sentiment] non-llm mode requested; use --mode llm for model analysis")
        conn.close()
        raise SystemExit(2)

    llm_cfg = _get_llm_config(config)
    if not llm_cfg["enabled"]:
        conn.close()
        raise RuntimeError("analysis.llm.enabled is false")
    if not llm_cfg["api_key"]:
        if llm_cfg["api_base"].startswith("http://127.0.0.1") or llm_cfg["api_base"].startswith("http://localhost"):
            llm_cfg["api_key"] = "local-newapi"
        else:
            conn.close()
            raise RuntimeError("Missing LLM API key. Set OPENAI_API_KEY or configured environment variable.")

    batch_size = max(1, min(llm_cfg["batch_size"], 3 if llm_cfg.get("wire_api") == "responses" else llm_cfg["batch_size"]))
    total_saved = 0
    for start in range(0, len(posts), batch_size):
        batch = posts[start:start + batch_size]
        posts_by_id = {str(p["post_id"]): p for p in batch}
        for attempt in range(3):
            try:
                if llm_cfg.get("wire_api") == "responses":
                    results = _call_responses_compatible(llm_cfg, batch)
                else:
                    results = _call_openai_compatible(llm_cfg, batch)
                saved = _save_results(conn, posts_by_id, results)
                total_saved += saved
                print(f"[sentiment] batch {start // batch_size + 1}: saved {saved}/{len(batch)}")
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                print(f"[sentiment] HTTPError attempt={attempt+1}: {exc.code} {detail}")
                if attempt == 2:
                    if len(batch) > 1:
                        saved = _analyze_single_fallback(conn, llm_cfg, batch)
                        total_saved += saved
                        print(f"[sentiment] split fallback saved {saved}/{len(batch)}")
                        break
                    conn.close()
                    raise
                time.sleep(2 * (attempt + 1))
            except Exception as exc:
                print(f"[sentiment] error attempt={attempt+1}: {exc}")
                if attempt == 2:
                    if len(batch) > 1:
                        saved = _analyze_single_fallback(conn, llm_cfg, batch)
                        total_saved += saved
                        print(f"[sentiment] split fallback saved {saved}/{len(batch)}")
                        break
                    conn.close()
                    raise
                time.sleep(2 * (attempt + 1))

    conn.close()
    print(f"[sentiment] LLM 完成: 写入 {total_saved} 条")
    return get_sentiment_summary()


def get_sentiment_summary():
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM sentiment_results WHERE target_type='post'").fetchone()[0]
    if total == 0:
        conn.close()
        return {"positive": 0, "neutral": 0, "negative": 0, "total": 0}
    stats = conn.execute("""
        SELECT sentiment_label, COUNT(*) as cnt
        FROM sentiment_results
        WHERE target_type='post'
        GROUP BY sentiment_label
    """).fetchall()
    conn.close()
    result = {"positive": 0, "neutral": 0, "negative": 0, "total": total}
    for row in stats:
        result[row["sentiment_label"]] = row["cnt"]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="llm", choices=["llm"])
    parser.add_argument("--clear", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--reanalyze-ids", type=str, default=None,
                        help="comma-separated post ids to reanalyze using body_content")
    args = parser.parse_args()
    init_db()
    if args.reanalyze_ids:
        ids = [pid.strip() for pid in args.reanalyze_ids.split(",") if pid.strip()]
        reanalyze_posts_with_body(ids)
        return
    summary = analyze_posts(mode=args.mode, limit=args.limit, clear=args.clear)
    print(f"[sentiment] summary: {summary}")

if __name__ == "__main__":
    main()
