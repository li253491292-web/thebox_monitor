import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from analysis.publish_time import parse_raw_publish_time
from crawler.browser_context import safe_error_text
from crawler.content_cleaning import clean_post_body, is_comment_contaminated
from crawler.crawl_comments import _extract_child
from crawler.database import parse_count
from crawler.popo_notifier import PopoNotificationError, _post_json, build_message_chunks
from crawler.popo_summary_notifier import categorize_negative_post, summary_window
from analysis.risk_scoring import risk_level, score_post
from scripts.audit_public_repo import is_forbidden_path, is_placeholder
from reports import generate_excel


class _CommentWithoutId:
    def get_attribute(self, _selector):
        return None


class CoreBehaviorTests(unittest.TestCase):
    def test_parse_count_handles_platform_units(self):
        self.assertEqual(parse_count("1.2w"), 12000)
        self.assertEqual(parse_count("3,500"), 3500)
        self.assertEqual(parse_count("--"), 0)

    def test_publish_time_parser_handles_relative_label(self):
        reference = dt.datetime(2026, 7, 24, 12, 0)
        self.assertEqual(
            parse_raw_publish_time("90分钟前", reference),
            dt.datetime(2026, 7, 24, 10, 30),
        )

    def test_child_comment_without_stable_id_is_skipped(self):
        self.assertIsNone(_extract_child(_CommentWithoutId(), "post", "parent"))

    def test_error_text_is_console_safe(self):
        self.assertEqual(safe_error_text(Exception("bad \udcff")), "bad \\udcff")

    def test_comment_contaminated_body_is_not_displayed(self):
        body = "正文\n全部评论\n玩家A Lv.10\n作者赞过\n回复内容"
        self.assertTrue(is_comment_contaminated(body))
        self.assertEqual(clean_post_body(body), "")

    def test_empty_database_still_exports_excel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = sqlite3.connect(Path(temp_dir) / "empty.sqlite")
            connection.executescript("""
                CREATE TABLE posts (post_id TEXT, source_url TEXT, title TEXT, author_name TEXT, author_level TEXT,
                    publish_time TEXT, standard_publish_time TEXT, standard_publish_time_source TEXT, like_count INTEGER,
                    comment_count INTEGER, content_preview TEXT, body_content TEXT, first_crawl_at TEXT, last_crawl_at TEXT);
                CREATE TABLE comment_counts (post_id TEXT, comment_count INTEGER, snapshot_at TEXT);
                CREATE TABLE sentiment_results (id INTEGER, target_type TEXT, target_id TEXT, sentiment_score REAL,
                    sentiment_label TEXT, content_text TEXT, analyze_time TEXT);
                CREATE TABLE crawl_runs (run_id TEXT, start_time TEXT, end_time TEXT, total_scrolled INTEGER,
                    new_posts INTEGER, updated_posts INTEGER, captcha_hit INTEGER, status TEXT);
            """)
            output_dir = Path(temp_dir) / "reports"
            with patch.object(generate_excel, "get_conn", return_value=connection), patch.object(
                generate_excel, "load_config", return_value={"report": {"output_dir": str(output_dir)}}
            ):
                self.assertTrue(Path(generate_excel.export_excel()).exists())

    def test_notification_message_contains_title_summary_and_link(self):
        posts = [{
            "post_id": "123", "title": "风险标题", "content_preview": "风险摘要",
            "body_content": "", "risk_score": 650, "like_count": 12, "comment_count": 55,
        }]
        chunks = build_message_chunks(posts, "2026-07-24", 2800)
        self.assertEqual(len(chunks), 1)
        content, chunk_posts = chunks[0]
        self.assertIn("风险标题", content)
        self.assertIn("首次达到推送阈值", content)
        self.assertIn("风险摘要", content)
        self.assertIn("https://www.xiaoheihe.cn/app/bbs/link/123", content)
        self.assertEqual(chunk_posts, posts)

    def test_notification_network_error_is_normalized(self):
        with patch("crawler.popo_notifier.urllib.request.urlopen", side_effect=URLError("timed out")):
            with self.assertRaisesRegex(PopoNotificationError, "Network error: timed out"):
                _post_json("https://example.invalid", {})

    def test_notification_settings_accept_multiple_receivers(self):
        from crawler.popo_notifier import _settings

        settings = _settings({"notification": {"popo": {
            "receivers": ["first@example.com", "second@example.com"],
            "lookback_days": 3,
        }}})
        self.assertEqual(settings["receivers"], ["first@example.com", "second@example.com"])
        self.assertEqual(settings["lookback_days"], 3)

    def test_negative_category_and_morning_window(self):
        self.assertEqual(categorize_negative_post("闪退问题", ""), "技术/性能")
        self.assertEqual(categorize_negative_post("版权迟迟没有谈下来", ""), "服务/其他")
        start, end, label = summary_window("morning", dt.datetime(2026, 7, 27, 9, 0))
        self.assertEqual(start, dt.datetime(2026, 7, 26, 17, 0))
        self.assertEqual(end, dt.datetime(2026, 7, 27, 9, 0))
        self.assertEqual(label, "09:00夜间")

    def test_freshness_score_prioritizes_recent_post(self):
        recent = score_post({"like_count": 20, "comment_count": 30, "sentiment_score": 0.2,
                             "standard_publish_time": "2026-07-27 10:00"}, now=dt.datetime(2026, 7, 27, 12, 0))
        old = score_post({"like_count": 20, "comment_count": 30, "sentiment_score": 0.2,
                          "standard_publish_time": "2026-07-24 10:00"}, now=dt.datetime(2026, 7, 27, 12, 0))
        self.assertEqual(recent["freshness_bucket"], "under_6_hours")
        self.assertGreater(recent["risk_score"], recent["base_risk_score"])
        self.assertLess(old["risk_score"], old["base_risk_score"])

    def test_freshness_score_has_positive_integer_risk(self):
        score = score_post({"like_count": 0, "comment_count": 0, "sentiment_score": 0.5})
        self.assertGreater(score["risk_score"], 0)

    def test_freshness_risk_levels(self):
        self.assertEqual(risk_level(300), ("medium", "中风险"))
        self.assertEqual(risk_level(500), ("elevated", "较高风险"))
        self.assertEqual(risk_level(800), ("high", "高风险"))

    def test_public_audit_rejects_runtime_paths_and_accepts_placeholders(self):
        self.assertTrue(is_forbidden_path("site/report_latest.html"))
        self.assertTrue(is_forbidden_path("config.yaml"))
        self.assertFalse(is_forbidden_path("config.example.yaml"))
        self.assertTrue(is_placeholder("${POPO_BOT_APP_SECRET}"))
        self.assertFalse(is_placeholder("real-secret-value"))


if __name__ == "__main__":
    unittest.main()
