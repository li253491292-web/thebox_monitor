"""Generate an HTML dashboard for Xiaoheihe opinion monitoring."""
import datetime
import html
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from crawler.browser_context import load_config
from crawler.content_cleaning import clean_post_body
from crawler.database import clean_existing_navigation_noise, clean_text_field, get_conn
from analysis.risk_scoring import risk_level, score_post


TOPIC_RULES = [
    ("版本/更新", ["更新", "版本", "补丁", "修复", "改动", "清榜", "ban"]),
    ("性能/技术", ["闪退", "崩溃", "卡", "掉帧", "优化", "显卡", "帧", "画质", "DLSS", "存档", "加载"]),
    ("线上/外挂", ["外挂", "线上", "联机", "开挂", "举报", "作弊"]),
    ("任务/奖励", ["任务", "赛季", "积分", "奖励", "锦标赛"]),
    ("刷CR/拍卖", ["cr", "拍卖", "刷", "技术点", "脚本"]),
    ("车辆/车包", ["车辆", "车包", "新车", "调校", "涂装"]),
    ("攻略/教程", ["攻略", "教程", "分享", "代码", "路线"]),
]


def _topic(text):
    text = (text or "").lower()
    for name, words in TOPIC_RULES:
        if any(word.lower() in text for word in words):
            return name
    return "其他"


def _load_posts(config):
    conn = get_conn()
    rows = conn.execute("""
        SELECT p.post_id, p.title, p.author_name, p.standard_publish_time,
               p.standard_publish_time_source, p.first_crawl_at, p.content_preview, p.body_content,
               p.like_count, p.comment_count,
               COALESCE(s.sentiment_label, 'neutral') AS sentiment_label,
               COALESCE(s.sentiment_score, 0.5) AS sentiment_score
        FROM posts p
        LEFT JOIN sentiment_results s ON s.target_id=p.post_id AND s.target_type='post'
        ORDER BY p.first_crawl_at DESC
    """).fetchall()
    conn.close()

    posts = []
    for row in rows:
        item = dict(row)
        for key in ("title", "author_name", "standard_publish_time", "content_preview"):
            item[key] = clean_text_field(item.get(key))
        body_content = clean_post_body(clean_text_field(item.get("body_content")))
        item["body_content"] = body_content
        summary = body_content or item["content_preview"]
        item["content_source"] = "正文" if body_content else "帖子摘要"
        item["summary"] = summary[:500]
        item["like_count"] = int(item.get("like_count") or 0)
        item["comment_count"] = int(item.get("comment_count") or 0)
        item["engagement_score"] = item["like_count"] + item["comment_count"] * 3
        item.update(score_post(item, config))
        item["risk_level"], item["risk_label"] = risk_level(item["risk_score"])
        item["topic"] = _topic(f'{item["title"]} {summary}')
        item["source_url"] = f'https://www.xiaoheihe.cn/app/bbs/link/{item["post_id"]}'
        posts.append(item)
    return posts


def _build_report(game_name, today, posts, risk_threshold):
    posts_json = json.dumps(posts, ensure_ascii=False).replace("</", "<\\/")
    template = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__GAME__ &#x793E;&#x533A;&#x8206;&#x60C5;&#x62A5;&#x544A; - __DATE__</title><script src="echarts.min.js"></script>
<style>
:root{--bg:#0f1117;--surface:#181c25;--surface2:#222936;--text:#edf0f7;--muted:#9ca3af;--green:#2ed573;--red:#ff4757;--orange:#ffa502;--blue:#4dabf7;--border:#303847}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}.container{max-width:1240px;margin:auto;padding:30px 20px 50px}.hero,.panel,.stat{background:var(--surface);border:1px solid var(--border);border-radius:16px}.hero{padding:28px;margin-bottom:18px}h1{margin:0 0 8px;font-size:32px}h2{font-size:20px;margin:0}.muted{color:var(--muted)}.filters{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}button,input{background:var(--surface2);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:7px 12px}button{cursor:pointer}button.active,button:hover{background:var(--green);color:#08140d}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.stat{padding:18px}.value{font-size:30px;font-weight:800}.panel{padding:18px;margin-top:16px}.chart{height:260px}.river-chart{height:320px}.section-head{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:8px}.risk{border-left:4px solid var(--red);padding:18px;margin:12px 0;background:var(--surface2);border-radius:10px}.risk.medium{border-left-color:var(--orange)}.risk-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.risk h3{margin:0;font-size:17px}.risk a{color:var(--blue)}.badges{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0}.badge{border-radius:999px;padding:2px 8px;font-size:12px;background:#303847;color:var(--text)}.badge.high{background:rgba(255,71,87,.18);color:#ff8792}.badge.medium{background:rgba(255,165,2,.18);color:#ffc45d}.risk-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:12px 0}.metric{padding:9px;border:1px solid var(--border);border-radius:8px}.metric strong{display:block;font-size:17px}.evidence{border-top:1px solid var(--border);padding-top:12px;margin-top:10px}.evidence p{white-space:pre-wrap;margin:6px 0 12px}.empty{color:var(--muted);padding:12px 0}@media(max-width:800px){.stats,.risk-grid{grid-template-columns:repeat(2,1fr)}h1{font-size:25px}.chart{height:230px}.river-chart{height:280px}.risk-head{display:block}}
</style></head><body><main class="container">
<section class="hero"><div class="muted">XIAOHEIHE OPINION MONITOR &#xB7; __DATE__</div><h1>__GAME__<br>&#x793E;&#x533A;&#x8206;&#x60C5;&#x53EF;&#x89C6;&#x5316;&#x62A5;&#x544A;</h1><p class="muted">&#x9AD8;&#x98CE;&#x9669;&#x5361;&#x7247;&#x53EA;&#x5C55;&#x793A;&#x5E16;&#x5B50;&#x6B63;&#x6587;&#x6216;&#x5E16;&#x5B50;&#x6458;&#x8981;&#xFF0C;&#x4E0D;&#x5C55;&#x793A;&#x8BC4;&#x8BBA;&#x697C;&#x5C42;&#x3002;</p></section>
<div class="filters"><button data-days="1">1&#x5929;</button><button data-days="7" class="active">7&#x5929;</button><button data-days="14">14&#x5929;</button><button data-days="30">30&#x5929;</button><button data-days="0">&#x5168;&#x90E8;</button><input id="from" type="date"><input id="to" type="date"></div>
<section class="stats"><div class="stat"><div id="posts" class="value">-</div><div class="muted">&#x5E16;&#x5B50;&#x6570;</div></div><div class="stat"><div id="positive" class="value" style="color:var(--green)">-</div><div class="muted">&#x6B63;&#x5411;&#x5E16;&#x5B50;</div></div><div class="stat"><div id="negative" class="value" style="color:var(--red)">-</div><div class="muted">&#x8D1F;&#x5411;&#x5E16;&#x5B50;</div></div><div class="stat"><div id="hot" class="value" style="color:var(--orange)">-</div><div class="muted">&#x6700;&#x9AD8;&#x70ED;&#x5EA6;</div></div></section>
<section class="panel"><div class="section-head"><h2>&#x53D1;&#x5E16;&#x4E0E;&#x60C5;&#x611F;&#x8D8B;&#x52BF;</h2><span id="trendRange" class="muted"></span></div><div id="volumeTrend" class="chart"></div><div id="sentimentRiver" class="river-chart"></div></section>
<section class="panel"><div class="section-head"><h2>&#x9AD8;&#x98CE;&#x9669;&#x5E16;&#x5B50;</h2><span id="riskRange" class="muted"></span></div><p class="muted">&#x5165;&#x9009;&#x9608;&#x503C;&#xFF1A;&#x98CE;&#x9669;&#x5206; &ge; 500&#x3002;&#x98CE;&#x9669;&#x5206; = &#x4E92;&#x52A8;&#x70ED;&#x5EA6; + &#x8D1F;&#x5411;&#x5F3A;&#x5EA6;&#xFF0C;&#x4E92;&#x52A8;&#x70ED;&#x5EA6; = &#x70B9;&#x8D5E; + &#x8BC4;&#x8BBA; &times; 3&#x3002;</p><div id="risks"></div></section>
</main><script>
const POSTS=__POSTS__;let range=7;
const esc=value=>String(value||'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const day=post=>String(post.standard_publish_time||'').slice(0,10);
const riskLevel=score=>score>=700?'high':'medium';
function selectedRange(){const end=new Date(),start=new Date();if(range===0)start.setFullYear(2000,0,1);else start.setDate(end.getDate()-range);const from=document.getElementById('from').value,to=document.getElementById('to').value;return {start:from||start.toISOString().slice(0,10),end:to||end.toISOString().slice(0,10)}}
function postsInRange(){const selected=selectedRange();return POSTS.filter(post=>{const value=day(post);return value&&value>=selected.start&&value<=selected.end})}
function renderChart(id,option){const element=document.getElementById(id),instance=echarts.getInstanceByDom(element)||echarts.init(element);instance.setOption(option,true);return instance}
function renderRisks(posts,start,end){const risks=posts.filter(post=>post.sentiment_label==='negative'&&Number(post.risk_score)>=500).sort((a,b)=>b.risk_score-a.risk_score);document.getElementById('riskRange').textContent=`${start}  ${end}  ${risks.length}  ${'\u9ad8\u98ce\u9669\u5019\u9009'}`;document.getElementById('risks').innerHTML=risks.slice(0,20).map(post=>{const level=riskLevel(post.risk_score);return `<article class="risk ${level}"><div class="risk-head"><div><h3>${esc(post.title||'\u65e0\u6807\u9898')}</h3><div class="badges"><span class="badge ${level}">${level==='high'?'\u9ad8\u98ce\u9669':'\u4e2d\u98ce\u9669'}</span><span class="badge">${esc(post.topic)}</span><span class="badge">${esc(post.content_source)}</span></div></div><a href="${post.source_url}" target="_blank" rel="noopener">\u67e5\u770b\u5e16\u5b50</a></div><div class="risk-grid"><div class="metric"><span class="muted">\u98ce\u9669\u5206</span><strong>${post.risk_score}</strong></div><div class="metric"><span class="muted">\u4e92\u52a8\u70ed\u5ea6</span><strong>${post.engagement_score}</strong></div><div class="metric"><span class="muted">\u8d1f\u5411\u5206</span><strong>${Number(post.sentiment_score||.5).toFixed(2)}</strong></div><div class="metric"><span class="muted">\u8bc4\u8bba\u6570</span><strong>${post.comment_count}</strong></div></div><div class="evidence"><strong>\u5e16\u5b50\u5185\u5bb9</strong><p>${esc(post.summary||'\u6682\u65e0\u53ef\u7528\u5e16\u5b50\u6458\u8981')}</p><span class="muted">\u53d1\u5e03\u65f6\u95f4 ${esc(post.standard_publish_time)}  \u70b9\u8d5e ${post.like_count}  \u6570\u636e\u6765\u6e90 ${esc(post.content_source)}</span></div></article>`}).join('')||'<p class="empty">\u5f53\u524d\u65e5\u671f\u8303\u56f4\u5185\u6682\u65e0\u98ce\u9669\u5206\u8fbe\u6807\u7684\u8d1f\u5411\u5e16\u5b50\u3002</p>'}
function update(){const selected=selectedRange(),data=postsInRange(),positive=data.filter(post=>post.sentiment_label==='positive').length,negative=data.filter(post=>post.sentiment_label==='negative').length;document.getElementById('posts').textContent=data.length;document.getElementById('positive').textContent=positive;document.getElementById('negative').textContent=negative;document.getElementById('hot').textContent=Math.max(0,...data.map(post=>post.engagement_score));document.getElementById('trendRange').textContent=`${selected.start}  ${selected.end}  ${data.length} \u6761\u5e16\u5b50`;const byDay={};data.forEach(post=>{const value=day(post);if(!byDay[value])byDay[value]={positive:0,neutral:0,negative:0,total:0};const label=['positive','neutral','negative'].includes(post.sentiment_label)?post.sentiment_label:'neutral';byDay[value][label]++;byDay[value].total++});const days=Object.keys(byDay).sort();renderChart('volumeTrend',{color:['#ffa502'],tooltip:{trigger:'axis'},grid:{left:46,right:22,top:30,bottom:34},xAxis:{type:'category',data:days},yAxis:{type:'value'},series:[{name:'\u53d1\u5e16\u6570',type:'line',smooth:true,areaStyle:{color:'rgba(255,165,2,.16)'},data:days.map(value=>byDay[value].total)}]});const river=[];days.forEach(value=>['positive','neutral','negative'].forEach(label=>{if(byDay[value][label])river.push([value,byDay[value][label],label])}));renderChart('sentimentRiver',{singleAxis:{type:'time',top:30,bottom:30},series:[{type:'themeRiver',data:river}]});renderRisks(data,selected.start,selected.end)}
document.querySelectorAll('[data-days]').forEach(button=>button.addEventListener('click',()=>{document.querySelectorAll('[data-days]').forEach(item=>item.classList.remove('active'));button.classList.add('active');range=Number(button.dataset.days);update()}));document.getElementById('from').addEventListener('input',update);document.getElementById('to').addEventListener('input',update);window.addEventListener('resize',()=>['volumeTrend','sentimentRiver'].forEach(id=>{const instance=echarts.getInstanceByDom(document.getElementById(id));if(instance)instance.resize()}));update();
</script></body></html>'''
    return (
        template.replace("\x16", "\u00b7")
        .replace("Number(post.risk_score)>=500", f"Number(post.risk_score)>={risk_threshold}")
        .replace("const riskLevel=score=>score>=700?'high':'medium';", "const riskLevel=score=>score>=800?'high':score>=500?'elevated':'medium';const riskLabel=score=>score>=800?'\\u9ad8\\u98ce\\u9669':score>=500?'\\u8f83\\u9ad8\\u98ce\\u9669':'\\u4e2d\\u98ce\\u9669';")
        .replace("${level==='high'?'\\u9ad8\\u98ce\\u9669':'\\u4e2d\\u98ce\\u9669'}", "${riskLabel(post.risk_score)}")
        .replace("&#x98CE;&#x9669;&#x5206; &ge; 500", f"&#x65B0;&#x9C9C;&#x98CE;&#x9669;&#x5206; &ge; {risk_threshold}")
        .replace(".risk.medium{border-left-color:var(--orange)}", ".risk.medium{border-left-color:var(--blue)}.risk.elevated{border-left-color:var(--orange)}.risk.high{border-left-color:var(--red)}")
        .replace(".badge.medium{background:rgba(255,165,2,.18);color:#ffc45d}", ".badge.medium{background:rgba(77,171,247,.18);color:#8fc9ff}.badge.elevated{background:rgba(255,165,2,.18);color:#ffc45d}")
        .replace("&#x98CE;&#x9669;&#x5206; &ge; 500", "&#x65B0;&#x9C9C;&#x98CE;&#x9669;&#x5206; &ge; 500")
        .replace(
            '<div id="risks"></div>',
            '<p class="muted">&#x65B0;&#x9C9C;&#x98CE;&#x9669;&#x5206; = &#x57FA;&#x7840;&#x98CE;&#x9669;&#x5206; &#x00D7; &#x65B0;&#x9C9C;&#x5EA6;&#x7CFB;&#x6570;&#xFF1A;6&#x5C0F;&#x65F6;&#x5185; 1.50&#x3001;24&#x5C0F;&#x65F6;&#x5185; 1.25&#x3001;48&#x5C0F;&#x65F6;&#x5185; 1.00&#x3001;&#x66F4;&#x65E9;&#x5185;&#x5BB9; 0.45&#x3002;</p><div id="risks"></div>',
        )
        .replace(
            "series:[{type:'themeRiver',data:river}]",
            "series:[{type:'themeRiver',data:river,itemStyle:{color:params=>({positive:'#2ed573',neutral:'#4dabf7',negative:'#ff4757'}[params.data[2]]||'#4dabf7')}}]",
        )
        .replace("__GAME__", html.escape(game_name))
        .replace("__DATE__", today)
        .replace("__POSTS__", posts_json)
    )


def generate_html():
    clean_existing_navigation_noise()
    config = load_config()
    today = datetime.date.today().isoformat()
    report_dir = Path(config.get("report", {}).get("output_dir", "./reports"))
    if not report_dir.is_absolute():
        report_dir = PROJECT_ROOT / report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"report_{today}.html"
    risk_threshold = int(config.get("analysis", {}).get("fresh_risk_threshold", 500))
    path.write_text(_build_report(config["crawl"]["target_game_name"], today, _load_posts(config), risk_threshold), encoding="utf-8")
    print(f"[report] HTML report generated: {path}")
    return str(path)


if __name__ == "__main__":
    generate_html()
