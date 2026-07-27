"""Normalize post publication timestamps from raw labels and crawl metadata."""
import datetime as dt
import re
import sys
from bisect import bisect_left
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from crawler.database import get_conn, init_db

TIME_FORMAT = "%Y-%m-%d %H:%M"
MAX_CRAWL_SKEW = dt.timedelta(minutes=10)


def _parse_datetime(value):
    value = str(value or "").strip().replace("T", " ")
    if not value:
        return None
    for format_string in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(value, format_string)
        except ValueError:
            continue
    return None


def _format_datetime(value):
    return value.strftime(TIME_FORMAT) if value else ""


def _decode_mojibake(value):
    text = str(value or "").strip()
    if not text:
        return ""
    for encoding in ("gb18030", "gbk"):
        try:
            repaired = text.encode("latin1").decode(encoding)
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if any(token in repaired for token in ("\u5206\u949f", "\u5c0f\u65f6", "\u6628\u5929", "\u524d\u5929", "\u5929\u524d")):
            return repaired
    return text


def parse_raw_publish_time(raw_value, reference_time):
    """Return a timestamp parsed from a Xiaoheihe time label, or None."""
    raw = _decode_mojibake(raw_value)
    if not raw or reference_time is None:
        return None
    raw = raw.strip()
    if raw in {"\u521a\u521a", "\u521a\u624d"}:
        return reference_time

    match = re.fullmatch(r"(\d+)\u5206\u949f\u524d", raw)
    if match:
        return reference_time - dt.timedelta(minutes=int(match.group(1)))
    match = re.fullmatch(r"(\d+)\u5c0f\u65f6\u524d", raw)
    if match:
        return reference_time - dt.timedelta(hours=int(match.group(1)))
    match = re.fullmatch(r"(\d+)\u5929\u524d", raw)
    if match:
        return reference_time - dt.timedelta(days=int(match.group(1)))
    match = re.fullmatch(r"\u6628\u5929\s*(\d{1,2}:\d{2})?", raw)
    if match:
        return (reference_time - dt.timedelta(days=1)).replace(
            hour=int((match.group(1) or "00:00").split(":")[0]),
            minute=int((match.group(1) or "00:00").split(":")[1]),
            second=0,
            microsecond=0,
        )
    match = re.fullmatch(r"\u524d\u5929\s*(\d{1,2}:\d{2})?", raw)
    if match:
        return (reference_time - dt.timedelta(days=2)).replace(
            hour=int((match.group(1) or "00:00").split(":")[0]),
            minute=int((match.group(1) or "00:00").split(":")[1]),
            second=0,
            microsecond=0,
        )
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}:\d{2}))?", raw)
    if match:
        time_text = match.group(4) or "00:00"
        return dt.datetime.strptime(
            f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d} {time_text}",
            TIME_FORMAT,
        )
    match = re.fullmatch(r"(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}:\d{2}))?", raw)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        year = reference_time.year
        if (month, day) > (reference_time.month, reference_time.day):
            year -= 1
        return dt.datetime.strptime(
            f"{year:04d}-{month:02d}-{day:02d} {match.group(3) or '00:00'}",
            TIME_FORMAT,
        )
    return None


def _bounded_time(candidate, first_crawl):
    if candidate is None:
        return None, False
    if first_crawl and candidate > first_crawl + MAX_CRAWL_SKEW:
        return first_crawl, True
    return candidate, False


def _build_id_anchors(rows):
    anchors = []
    for row in rows:
        post_id = str(row["post_id"] or "")
        if not post_id.isdigit():
            continue
        first_crawl = _parse_datetime(row["first_crawl_at"])
        reference = _parse_datetime(row["publish_time_crawled_at"]) or first_crawl
        parsed = parse_raw_publish_time(row["publish_time_raw"], reference)
        parsed, clamped = _bounded_time(parsed, first_crawl)
        if parsed and not clamped:
            anchors.append((int(post_id), parsed))
    return sorted(anchors)


def _interpolate_from_post_id(post_id, anchors):
    if not str(post_id or "").isdigit() or len(anchors) < 2:
        return None
    value = int(post_id)
    ids = [anchor[0] for anchor in anchors]
    index = bisect_left(ids, value)
    if index == 0 or index == len(anchors):
        return None
    left_id, left_time = anchors[index - 1]
    right_id, right_time = anchors[index]
    if right_id == left_id:
        return None
    ratio = (value - left_id) / (right_id - left_id)
    return left_time + (right_time - left_time) * ratio


def normalize_post_times(dry_run=False):
    """Backfill standard publication time for every post with source metadata."""
    init_db()
    conn = get_conn()
    rows = conn.execute(
        "SELECT post_id, publish_time_raw, publish_time, publish_time_crawled_at, first_crawl_at FROM posts"
    ).fetchall()
    anchors = _build_id_anchors(rows)
    updates = []
    source_counts = {}

    for row in rows:
        first_crawl = _parse_datetime(row["first_crawl_at"])
        reference = _parse_datetime(row["publish_time_crawled_at"]) or first_crawl
        parsed = parse_raw_publish_time(row["publish_time_raw"], reference)
        parsed, clamped = _bounded_time(parsed, first_crawl)
        if parsed:
            source = "raw_label_clamped" if clamped else "raw_label"
            confidence = 0.72 if clamped else 0.98
        else:
            parsed = _interpolate_from_post_id(row["post_id"], anchors)
            parsed, clamped = _bounded_time(parsed, first_crawl)
            if parsed:
                source = "post_id_interpolation_clamped" if clamped else "post_id_interpolation"
                confidence = 0.52 if clamped else 0.68
            else:
                parsed = first_crawl
                source = "first_crawl_fallback"
                confidence = 0.35

        source_counts[source] = source_counts.get(source, 0) + 1
        updates.append((_format_datetime(parsed), source, confidence, row["post_id"]))

    if not dry_run:
        conn.executemany(
            "UPDATE posts SET standard_publish_time=?, standard_publish_time_source=?, standard_publish_time_confidence=? WHERE post_id=?",
            updates,
        )
        conn.commit()
    conn.close()
    return {"total": len(rows), "anchors": len(anchors), "sources": source_counts}


if __name__ == "__main__":
    print(normalize_post_times())
