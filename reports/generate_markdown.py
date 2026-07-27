"""
Markdown opinion-outline report generator.
The output structure follows reports/outline reference: overview, negative, positive, data table, risk, summary, suggestions.
"""
import sys
import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from crawler.browser_context import load_config
from crawler.database import get_conn, clean_existing_navigation_noise
from analysis.hot_content import get_top_posts, extract_keywords, get_daily_stats
from analysis.sentiment import get_sentiment_summary

ZH = {
    "title_tpl": "# 《{game}》小黑盒社区舆情累计监测总结（截至 {date}）",
    "sec1": "## 一、总体舆情概览",
    "sec2": "## 二、核心负面舆情（按互动强度排序）",
    "sec21": "### 2.1 负面主题归纳",
    "sec3": "## 三、正面舆情",
    "sec4": "## 四、关键舆情数据与高频词",
    "sec5": "## 五、舆情影响与风险判断",
    "sec6": "## 六、小结",
    "topic_overview": "【高频话题概览】",
    "negative_sample": "【负向样本 {n} 条，占已分析样本 {pct:.1f}%】",
    "positive_sample": "【正向样本 {n} 条，占已分析样本 {pct:.1f}%】",
    "no_negative": "暂无明显负向帖子。",
    "no_positive": "暂无明显正向帖子。",
    "suggestion_title": "**建议官方/运营侧：**",
}


def _md_cell(value):
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def _pct(part, total):
    return (part / total * 100) if total else 0.0


def _load_posts_by_label(label, limit=12):
    conn = get_conn()
    rows = conn.execute("""
        SELECT p.post_id, p.title, p.author_name, p.content_preview,
               CAST(p.like_count AS INTEGER) AS like_count,
               CAST(p.comment_count AS INTEGER) AS comment_count,
               COALESCE(s.sentiment_score, 0.5) AS sentiment_score
        FROM posts p
        LEFT JOIN sentiment_results s ON s.target_id = p.post_id AND s.target_type = 'post'
        WHERE COALESCE(s.sentiment_label, 'neutral') = ?
        ORDER BY CAST(p.comment_count AS INTEGER) DESC, CAST(p.like_count AS INTEGER) DESC
        LIMIT ?
    """, (label, limit)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def _short_text(item, max_len=90):
    text = item.get("title") or item.get("content_preview") or "（无标题）"
    text = _md_cell(text)
    return text[:max_len] + ("..." if len(text) > max_len else "")


def _topic_summary(keywords, top_n=8):
    if not keywords:
        return "暂无足够关键词。"
    return "、".join(f"{word}（{count}次）" for word, count in keywords[:top_n])


def generate_daily_report():
    clean_existing_navigation_noise()
    config = load_config()
    game_name = config["crawl"]["target_game_name"]
    today = datetime.date.today().isoformat()

    stats = get_daily_stats()
    sentiment = get_sentiment_summary()
    top_posts = get_top_posts(10)
    keywords = extract_keywords(30)
    negative_posts = _load_posts_by_label("negative", 12)
    positive_posts = _load_posts_by_label("positive", 10)

    total_sentiment = sentiment.get("total", 0)
    positive = sentiment.get("positive", 0)
    neutral = sentiment.get("neutral", 0)
    negative = sentiment.get("negative", 0)
    pos_pct = _pct(positive, total_sentiment)
    neu_pct = _pct(neutral, total_sentiment)
    neg_pct = _pct(negative, total_sentiment)
    top_keywords = _topic_summary(keywords, 10)

    lines = []
    lines.append(ZH["title_tpl"].format(game=game_name, date=today))
    lines.append("")

    lines.append(ZH["sec1"])
    lines.append("")
    lines.append(
        f"累计监测样本覆盖 **{stats['total_posts']}** 条帖子、**{stats['total_comments']}** 条评论；"
        f"其中已完成情感分析 **{total_sentiment}** 条，正向 **{positive}** 条（{pos_pct:.1f}%），"
        f"中性 **{neutral}** 条（{neu_pct:.1f}%），负向 **{negative}** 条（{neg_pct:.1f}%）。"
    )
    trend_desc = "正面基本面占优" if pos_pct >= neg_pct else "负面压力高于正向声量"
    lines.append(f"整体判断：当前小黑盒社区舆情呈现 **{trend_desc}**，负向占比为 **{neg_pct:.1f}%**，需结合高热度负向帖持续跟踪。")
    lines.append("")
    lines.append(ZH["topic_overview"])
    lines.append(top_keywords)
    lines.append("")

    lines.append(ZH["sec2"])
    lines.append("")
    lines.append(ZH["negative_sample"].format(n=negative, pct=neg_pct))
    lines.append("")
    if negative_posts:
        for idx, post in enumerate(negative_posts[:5], 1):
            lines.append(
                f"{idx}. **{_short_text(post, 60)}** —— 评论 {post['comment_count']}，点赞 {post['like_count']}，"
                f"负向分 {post['sentiment_score']:.2f}。{_md_cell(post.get('content_preview'))[:120]}"
            )
    else:
        lines.append(ZH["no_negative"])
    lines.append("")
    lines.append(ZH["sec21"])
    lines.append("")
    lines.append("- 高互动负向内容主要集中在更新反馈、任务/奖励机制、线上环境、性能/技术问题、刷 CR/拍卖行等方向。")
    lines.append("- 若负向帖同时具备高评论数与高点赞数，建议优先人工复核其评论区扩散方向。")
    lines.append("- 当前报告基于小黑盒帖子语义与互动数据自动归纳，后续可接入更细的标签体系以对标 Steam 周报中的 B/C/D/A 类权重。")
    lines.append("")

    lines.append(ZH["sec3"])
    lines.append("")
    lines.append(ZH["positive_sample"].format(n=positive, pct=pos_pct))
    lines.append("")
    if positive_posts:
        for idx, post in enumerate(positive_posts[:5], 1):
            lines.append(
                f"{idx}. **{_short_text(post, 60)}** —— 评论 {post['comment_count']}，点赞 {post['like_count']}，"
                f"正向分 {post['sentiment_score']:.2f}。{_md_cell(post.get('content_preview'))[:120]}"
            )
    else:
        lines.append(ZH["no_positive"])
    lines.append("")
    lines.append("正面内容主要由攻略、车辆/涂装分享、拍照展示、实用教程和玩家互助构成；这类内容对社区活跃度和长尾口碑具有支撑作用。")
    lines.append("")

    lines.append(ZH["sec4"])
    lines.append("")
    lines.append("| 维度 | 关键信息 |")
    lines.append("|---|---|")
    lines.append(f"| 累计样本规模 | 帖子 {stats['total_posts']} 条 / 评论 {stats['total_comments']} 条 / 今日新增帖子 {stats['today_posts']} 条 |")
    lines.append(f"| 情感分布 | 正向 {positive}（{pos_pct:.1f}%） / 中性 {neutral}（{neu_pct:.1f}%） / 负向 {negative}（{neg_pct:.1f}%） |")
    lines.append(f"| 最高频词 | {_md_cell(_topic_summary(keywords, 15))} |")
    lines.append("| 热门帖子口径 | 热度分 = 点赞数 + 评论数 × 3 |")
    if top_posts:
        top = top_posts[0]
        lines.append(f"| 最高热度内容 | {_md_cell(top['title'])[:60]}（热度 {top['hot_score']}，评论 {top['comment_count']}，点赞 {top['like_count']}） |")
    lines.append("")

    lines.append(ZH["sec5"])
    lines.append("")
    if neg_pct >= 30:
        risk_level = "偏高"
        risk_text = "负向占比较高，建议优先处理高互动负向主题，并跟踪是否向更大范围扩散。"
    elif neg_pct >= 15:
        risk_level = "中等"
        risk_text = "负向讨论存在但未压过正向基本面，应关注高评论负向帖与重复出现的体验问题。"
    else:
        risk_level = "较低"
        risk_text = "整体负向压力较低，可重点维护正向攻略、创作和互助内容。"
    lines.append(f"当前风险等级：**{risk_level}**。{risk_text}")
    lines.append("")
    lines.append("关键风险信号：")
    lines.append("- 高评论负向帖可能代表玩家争议集中点，建议人工查看评论楼层情绪。")
    lines.append("- 若更新后出现集中技术问题、存档问题或奖励机制争议，应单独建立事件追踪。")
    lines.append("- 小黑盒内容包含较多攻略/分享帖，单纯情感分类可能低估玩法机制争议，需要结合关键词和评论增长判断。")
    lines.append("")

    lines.append(ZH["sec6"])
    lines.append("")
    lines.append(
        f"截至 {today}，《{game_name}》小黑盒社区累计舆情样本为 {stats['total_posts']} 条帖子，"
        f"正向 {pos_pct:.1f}%、负向 {neg_pct:.1f}%。高频讨论集中在 {top_keywords}。"
    )
    if top_posts:
        lines.append(f"当前最高热度帖为 **{_md_cell(top_posts[0]['title'])[:80]}**，建议作为当日社区扩散观察样本。")
    lines.append("")
    lines.append(ZH["suggestion_title"])
    lines.append("")
    lines.append("- 1）优先复核高互动负向帖，确认是否为版本问题、机制争议或个体吐槽。")
    lines.append("- 2）围绕高频词建立连续周报，观察关键词是否持续上升。")
    lines.append("- 3）对攻略、涂装、车辆分享等正向内容给予运营放大，稳定社区基本面。")
    lines.append("- 4）对评论增长异常的帖子设置预警，避免争议扩散后才被动处理。")
    lines.append("- 5）后续报告建议补充 B 技术故障 / C 内容品质 / D 性能优化 / A 其他 的多标签归因，以进一步对齐参考大纲。")
    lines.append("")

    report_dir = Path(config.get("report", {}).get("output_dir", "./reports"))
    if not report_dir.is_absolute():
        report_dir = PROJECT_ROOT / report_dir
    report_dir.mkdir(parents=True, exist_ok=True)

    report_path = report_dir / f"daily_report_{today}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[report] 日报已生成: {report_path}")
    return str(report_path)


if __name__ == "__main__":
    generate_daily_report()
