import re


COMMENT_SECTION_MARKERS = ("全部评论", "评论区")


def normalize_post_body(value, limit=5000):
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    return "\n".join(lines)[:limit]


def is_comment_contaminated(value):
    text = normalize_post_body(value)
    if not any(marker in text for marker in COMMENT_SECTION_MARKERS):
        return False
    has_level = bool(re.search(r"\bLv\.\d+\b", text))
    reply_markers = text.count("回复") + text.count("作者赞过")
    return has_level or reply_markers >= 3


def clean_post_body(value):
    text = normalize_post_body(value)
    return "" if is_comment_contaminated(text) else text
