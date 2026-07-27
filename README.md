# Xiaoheihe Opinion Monitor

Collects Xiaoheihe game-community posts and comments into SQLite, then generates Markdown, HTML, and Excel reports.

## Install

```powershell
pip install -r requirements.txt
playwright install chromium
```

## Configuration

Main settings live in `config.yaml`:

- `crawl.target_game_name`: display name and search fallback keyword.
- `crawl.target_game_id`: preferred game id; when set, the crawler opens the game community directly.
- `database.path`: SQLite database path; relative paths are resolved from the project root.
- `browser.user_data_dir`: persistent browser profile for login state. The default
  `browser_profile/xiaoheihe_automation` is dedicated to this crawler; do not commit or open it
  in another Chrome process while a crawl is running.
- `browser.executable_path`: installed Google Chrome used by the crawler.
- `browser.chromium_sandbox`: set to `false` when the browser profile cannot be accessed from the
  Chromium sandbox; keep the profile local and trusted.

## Run

```powershell
python crawler/login_setup.py
python crawler/crawl_posts.py
python analysis/sentiment.py
python crawler/crawl_risk_content.py
python analysis/publish_time.py
python reports/generate_markdown.py
python reports/generate_html.py
python reports/generate_excel.py
```

Scheduler:

```powershell
python crawler/scheduler.py --once
python crawler/scheduler.py
```

The scheduler crawls the post list for the whole target community. Full post text and comments are
then fetched only for recent negative posts whose risk score reaches the configured threshold:
`like_count + comment_count * 3 + (1 - sentiment_score) * 300`. Configure the lookback window,
threshold, batch size, and comment refresh interval under `crawl.high_risk` in `config.yaml`.
The default high-risk comment limit is one post per run, three comment scrolls, and a six-hour
refresh interval. Normal post-list collection remains unchanged.

## Outputs

- Database: `data/xiaoheihe.sqlite`
- CSV export: `data/posts.csv`
- Reports: `reports/`
- Debug screenshots and HTML: `logs/`

## Security Notes

`browser_profile/` can contain cookies, login state, and browsing history. It is ignored by `.gitignore`; each user should log in locally instead of sharing the profile.


## Post Publication Time

The crawler stores the platform label in `publish_time_raw`, the crawler observation time in
`publish_time_crawled_at`, and the first-seen time in `first_crawl_at`. The HTML dashboard uses
`standard_publish_time` for its date filters, volume trend, sentiment river chart, and high-risk
post list.

Run the backfill manually after importing historical data:

```powershell
python analysis/publish_time.py
```

Normalization is deterministic and traceable. Each post receives these fields:

- `standard_publish_time`: normalized timestamp used by the dashboard.
- `standard_publish_time_source`: `raw_label`, `raw_label_clamped`,
  `post_id_interpolation`, `post_id_interpolation_clamped`, or `first_crawl_fallback`.
- `standard_publish_time_confidence`: confidence score from `0.35` to `0.98`.

Rules, in priority order:

1. Decode and parse Xiaoheihe relative or absolute time labels using the crawl observation time.
2. Prevent a parsed time from being later than the first crawl by more than 10 minutes; clamp it
   when the page was refreshed after the post was first seen.
3. When the page supplied no usable time label, interpolate from neighboring posts with reliable
   timestamps using the monotonic post ID sequence.
4. Use `first_crawl_at` only as a documented last fallback. It is never labeled as an exact
   platform timestamp.

The scheduled pipeline runs `analysis/publish_time.py` after post crawling and before report
production, so the exported CSV and HTML report receive the same normalized timestamps.

The scheduler stops when post crawling, publication-time normalization, sentiment analysis, or
alert evaluation fails. High-risk detail/comment crawling is non-blocking: on failure (for
example, a captcha timeout), the scheduler logs the skipped subtask and continues with report
generation and POPO notifications using the available post-list data.

## POPO Notifications

After reports are generated, the scheduler sends a text notification when an unnotified negative
post first reaches `notification.popo.min_risk_score`, provided it was first collected within the
configured `notification.popo.lookback_days` window. It records successful post IDs in SQLite to
prevent duplicate delivery across later runs. Configure the receiver and inner/outer
API base and `receivers` under `notification.popo` in `config.yaml`; set `POPO_BOT_APP_KEY` and
`POPO_BOT_APP_SECRET` in the process environment rather than storing credentials in the project.

Preview pending notifications without sending them:

```powershell
python crawler/popo_notifier.py --dry-run
```

The separate morning and evening summary jobs send compact mobile-friendly overviews at 09:00 and
17:00. They classify negative posts into technical/performance, version/content, online/fairness,
economy/progression, and service/other categories. Preview either window without sending:

```powershell
python crawler/popo_summary_notifier.py --period morning --dry-run
python crawler/popo_summary_notifier.py --period evening --dry-run
```

## GitHub Pages

> **Privacy:** Do not publish raw crawler reports or `site/` unless a separate anonymization step
> has removed post text, authors, source links, interaction data, recipient addresses, and any
> other sensitive operational content. The current report output is intended for internal use.

Run the static-site builder after generating the HTML dashboard:

```powershell
python scripts/publish_site.py
```

It creates `site/` with `index.html`, `report_latest.html`, `echarts.min.js`, and `.nojekyll`.
The builder is for local/internal preview only. Do not commit the generated `site/` output to a
public repository until a dedicated anonymized-public-report builder is implemented.

## HTML Dashboard Behavior

- The default range is the most recent 7 days; select **All** to view the full database.
- Dashboard filters, post totals, publication-volume line chart, sentiment river chart, and high-risk
  list all use `standard_publish_time`.
- The trend section shows the current filtered count and the number of estimated timestamps.
- The sentiment river chart groups positive, neutral, and negative post counts by publication day.
- High-risk posts are negative posts in the selected publication-time range, ranked by interaction
  heat and negative sentiment score.
- Chinese labels in the generated HTML use HTML entities or JavaScript Unicode escapes to avoid
  Windows console encoding corruption.
