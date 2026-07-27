"""Build an anonymized public dashboard with aggregate Xiaoheihe metrics only."""
import datetime
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_DIR = PROJECT_ROOT / "public_site"
sys.path.insert(0, str(PROJECT_ROOT))

from analysis.risk_scoring import risk_level, score_post
from crawler.browser_context import load_config
from crawler.database import get_conn
from crawler.popo_summary_notifier import categorize_negative_post


FORBIDDEN_PUBLIC_KEYS = {"title", "summary", "author", "source_url", "post_id", "content", "receiver"}


def build_payload(days=30):
    config = load_config()
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=days - 1)
    conn = get_conn()
    rows = [dict(row) for row in conn.execute("""
        SELECT p.standard_publish_time, p.first_crawl_at, p.title, p.content_preview,
               p.like_count, p.comment_count, COALESCE(s.sentiment_label, 'neutral') AS sentiment_label,
               COALESCE(s.sentiment_score, 0.5) AS sentiment_score
        FROM posts p
        LEFT JOIN sentiment_results s ON s.target_id=p.post_id AND s.target_type='post'
        WHERE date(COALESCE(NULLIF(p.standard_publish_time, ''), p.first_crawl_at)) >= date(?)
    """, (start_date.isoformat(),)).fetchall()]
    conn.close()

    daily = defaultdict(lambda: {"posts": 0, "positive": 0, "neutral": 0, "negative": 0})
    sentiment = Counter()
    categories = Counter()
    risk_levels = Counter()
    for row in rows:
        day = str(row.get("standard_publish_time") or row.get("first_crawl_at") or "")[:10]
        if not day:
            continue
        label = row["sentiment_label"] if row["sentiment_label"] in {"positive", "neutral", "negative"} else "neutral"
        daily[day]["posts"] += 1
        daily[day][label] += 1
        sentiment[label] += 1
        if label == "negative":
            categories[categorize_negative_post(row["title"], row["content_preview"])] += 1
            _, level_label = risk_level(score_post(row, config)["risk_score"])
            risk_levels[level_label] += 1

    payload = {
        "generated_date": end_date.isoformat(),
        "range_start": start_date.isoformat(),
        "range_end": end_date.isoformat(),
        "posts": len(rows),
        "sentiment": {label: sentiment[label] for label in ("positive", "neutral", "negative")},
        "negative_categories": dict(sorted(categories.items())),
        "negative_risk_levels": {label: risk_levels[label] for label in ("中风险", "较高风险", "高风险")},
        "daily": [{"date": day, **daily[day]} for day in sorted(daily)],
    }
    assert not (set(payload) & FORBIDDEN_PUBLIC_KEYS)
    return payload


def build_site():
    payload = build_payload()
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    (PUBLIC_DIR / ".nojekyll").write_text("", encoding="utf-8")
    (PUBLIC_DIR / "public_data.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (PUBLIC_DIR / "index.html").write_text("""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FH6 社区舆情公开概况</title><style>
body{margin:0;background:#0f1117;color:#edf0f7;font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif}.wrap{max-width:860px;margin:auto;padding:28px 18px}.card{background:#181c25;border:1px solid #303847;border-radius:14px;padding:18px;margin:14px 0}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.stat{background:#222936;border-radius:10px;padding:12px}.value{font-size:24px;font-weight:700}.muted{color:#9ca3af}.positive{color:#2ed573}.neutral{color:#4dabf7}.negative{color:#ff4757}table{width:100%;border-collapse:collapse}td,th{padding:7px;border-bottom:1px solid #303847;text-align:right}td:first-child,th:first-child{text-align:left}@media(max-width:640px){.stats{grid-template-columns:repeat(2,1fr)}}
</style></head><body><main class="wrap"><h1>FH6 社区舆情公开概况</h1><p class="muted">本页面仅展示匿名聚合统计，不包含帖子原文、作者、链接、账号或推送信息。</p><section class="card"><div id="range" class="muted"></div><div class="stats"><div class="stat"><div id="posts" class="value">-</div>帖子数</div><div class="stat"><div id="positive" class="value positive">-</div>正向</div><div class="stat"><div id="neutral" class="value neutral">-</div>中性</div><div class="stat"><div id="negative" class="value negative">-</div>负面</div></div></section><section class="card"><h2>负面分类</h2><div id="categories"></div></section><section class="card"><h2>负面风险等级</h2><div id="levels"></div></section><section class="card"><h2>近 30 天趋势</h2><table><thead><tr><th>日期</th><th>帖子</th><th>正向</th><th>中性</th><th>负面</th></tr></thead><tbody id="daily"></tbody></table></section></main><script>
fetch('public_data.json').then(response=>response.json()).then(data=>{document.getElementById('range').textContent=`统计区间：${data.range_start} 至 ${data.range_end}；更新日期：${data.generated_date}`;for(const key of ['posts','positive','neutral','negative'])document.getElementById(key).textContent=key==='posts'?data.posts:data.sentiment[key];const render=(target,source)=>document.getElementById(target).innerHTML=Object.entries(source).map(([key,value])=>`<p>${key}：<strong>${value}</strong></p>`).join('')||'<p class="muted">暂无数据</p>';render('categories',data.negative_categories);render('levels',data.negative_risk_levels);document.getElementById('daily').innerHTML=data.daily.map(row=>`<tr><td>${row.date}</td><td>${row.posts}</td><td>${row.positive}</td><td>${row.neutral}</td><td>${row.negative}</td></tr>`).join('')});
</script></body></html>""", encoding="utf-8")
    print(f"[public-site] built anonymized dashboard in {PUBLIC_DIR}")


if __name__ == "__main__":
    build_site()
