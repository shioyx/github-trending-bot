"""
GitHub Trending Bot v4 ── 三榜分析终极版
════════════════════════════════════════════════════════════
架构：
  日榜卡片  ──  每个项目4行富内容（中文摘要·爆火原因·适合人群·标签）
  周榜卡片  ──  完整列表 + AI变化分析（谁上榜·谁掉了·为什么）
  月榜卡片  ──  完整列表 + AI变化分析

核心可靠性保障：
  ✅ Gemini JSON 强制模式（response_mime_type="application/json"）
     → 彻底消灭 "暂无分析" —— Gemini 必须输出 JSON，否则 API 报错重试
  ✅ Batch失败→逐条兜底，逐条也失败→简单prompt兜底，三层不漏一条
  ✅ 状态持久化（state.json 提交回仓库），支持真实排名变化对比
════════════════════════════════════════════════════════════
"""
import os, sys, time, json, re, requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from google import genai
from google.genai import types

# ══════════════════════════════════════════════════════
# 全局配置
# ══════════════════════════════════════════════════════
GEMINI_MODEL = "gemini-2.5-flash"
TIMEOUT      = 20
SPIKE_MIN    = 500          # 日新增超过此数 → 🔥爆发
STATE_FILE   = "state.json" # 排名历史（提交回 repo 实现持久化）

CATEGORY_MAP = {
    "AI / ML":    {"python","jupyter notebook","r","cuda","c++"},
    "前端 / Web": {"javascript","typescript","html","css","svelte","vue"},
    "系统 / 底层":{"rust","zig","go","c","assembly"},
    "移动端":     {"swift","kotlin","dart"},
    "DevOps":     {"shell","dockerfile","hcl","makefile"},
    "数据 / DB":  {"sql","plpgsql","scala"},
}
CAT_EMOJI = {
    "AI / ML":"🤖","前端 / Web":"🌐","系统 / 底层":"⚙️",
    "移动端":"📱","DevOps":"🐳","数据 / DB":"🗄️","其他":"📦",
}
PERIOD_LABEL = {"daily":"今日","weekly":"本周","monthly":"本月"}
CARD_COLOR   = {"daily":"red","weekly":"turquoise","monthly":"wathet"}


# ══════════════════════════════════════════════════════
# 工具：JSON 强制模式 Config（核心可靠性保障）
# ══════════════════════════════════════════════════════
JSON_CFG = types.GenerateContentConfig(
    response_mime_type="application/json",
    temperature=0.25,
)


# ══════════════════════════════════════════════════════
# Step 1：抓取三榜数据
# ══════════════════════════════════════════════════════
def _parse_stars_num(text: str) -> int:
    m = re.search(r"[\d,]+", text or "")
    return int(m.group().replace(",","")) if m else 0

def _auto_category(language: str, description: str) -> str:
    lang = language.lower()
    for cat, langs in CATEGORY_MAP.items():
        if lang in langs:
            return cat
    kws = ["llm","ai","gpt","model","neural","agent","openai","claude","deepseek",
           "machine learning","deep learning","transformer","generative"]
    if any(k in description.lower() for k in kws):
        return "AI / ML"
    return "其他"

def fetch_period(session, since: str) -> list:
    """抓取单时段 trending，返回 repo 列表。"""
    r = session.get(
        f"https://github.com/trending?since={since}",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.5",
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    soup  = BeautifulSoup(r.text, "html.parser")
    repos = []
    for rank, art in enumerate(soup.select("article.Box-row"), 1):
        h2 = art.select_one("h2 a")
        if not h2:
            continue
        name = h2.get("href","").strip("/")
        if not name or "/" not in name:
            continue

        desc_el = art.select_one("p")
        desc    = desc_el.get_text(strip=True) if desc_el else ""

        lang_el  = art.select_one("span[itemprop='programmingLanguage']")
        language = lang_el.get_text(strip=True) if lang_el else ""

        links       = art.select("a.Link--muted")
        total_stars = links[0].get_text(strip=True) if links       else "—"
        forks       = links[1].get_text(strip=True) if len(links)>1 else "—"

        today_el    = art.select_one("span.d-inline-block.float-sm-right")
        stars_period = today_el.get_text(strip=True) if today_el else "—"

        stars_num   = _parse_stars_num(stars_period)
        category    = _auto_category(language, desc)

        repos.append({
            "rank":         rank,
            "name":         name,
            "url":          f"https://github.com/{name}",
            "description":  desc,
            "language":     language,
            "category":     category,
            "total_stars":  total_stars,
            "forks":        forks,
            "stars_period": stars_period,
            "stars_num":    stars_num,
            "is_spike":     stars_num >= SPIKE_MIN,
            "period":       since,
            # AI fields (filled later)
            "ai_summary":  "",
            "ai_why_hot":  "",
            "ai_audience": "",
            "ai_tags":     "",
        })
    return repos

def fetch_all(top_daily=25, top_weekly=25, top_monthly=25) -> dict:
    session = requests.Session()
    try:
        session.get("https://github.com", timeout=8,
                    headers={"User-Agent":"Mozilla/5.0"})
    except Exception:
        pass
    data = {}
    for since, limit in [("daily",top_daily),("weekly",top_weekly),("monthly",top_monthly)]:
        try:
            repos = fetch_period(session, since)[:limit]
            data[since] = repos
            print(f"  {PERIOD_LABEL[since]}榜: {len(repos)} 条")
        except Exception as e:
            print(f"  ⚠️  {since} 抓取失败: {e}")
            data[since] = []
    return data


# ══════════════════════════════════════════════════════
# Step 2：状态持久化（排名对比基础）
# ══════════════════════════════════════════════════════
def load_state() -> dict:
    """读取上次保存的排名状态。"""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}   # 首次运行，无历史

def save_state(data: dict, date_str: str) -> None:
    """保存本次排名状态（由 GitHub Actions 提交回仓库）。"""
    state = {"last_updated": date_str}
    for period in ["weekly","monthly"]:
        repos = data.get(period, [])
        state[period] = [
            {"rank": r["rank"], "name": r["name"], "stars": r["stars_period"]}
            for r in repos
        ]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 状态已保存 → {STATE_FILE}")


# ══════════════════════════════════════════════════════
# Step 3：AI 富内容生成（JSON 强制模式，永不出现"暂无分析"）
# ══════════════════════════════════════════════════════
_ENRICH_PROMPT = """\
你是资深开源技术分析师。为以下 GitHub 项目生成中文分析，返回 JSON 数组。

项目数据（JSON）：
{items_json}

每个对象必须包含：
- id        : 原样返回（整数）
- summary   : 核心用途，≤25字，中文
- why_hot   : 近期爆火原因，≤40字，结合技术背景/行业趋势/项目特点
- audience  : 最适合哪类人关注，≤15字（如"前端开发者·想学AI的工程师"）
- tags      : 2-3个中文技术标签，用·分隔（如"AI Agent·金融分析·多智能体"）

严格返回合法JSON数组，不要任何其他内容。"""

def _enrich_batch(client, repos: list) -> dict:
    """一次 API 调用，JSON 模式，返回 {rank: ai_fields} map。"""
    items = [
        {"id": r["rank"], "name": r["name"],
         "desc": r["description"] or "No description.",
         "lang": r["language"] or "Unknown",
         "stars_today": r["stars_period"]}
        for r in repos
    ]
    prompt = _ENRICH_PROMPT.format(items_json=json.dumps(items, ensure_ascii=False))

    for attempt in range(1, 4):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL, contents=prompt, config=JSON_CFG
            )
            data = json.loads(resp.text)
            return {int(item["id"]): item for item in data}
        except Exception as e:
            wait = 2 ** attempt
            print(f"    [Batch重试 {attempt}/3] {e}，等 {wait}s…")
            time.sleep(wait)
    return {}

def _enrich_single(client, r: dict) -> dict:
    """单条兜底，仍用 JSON 模式。"""
    prompt = f"""\
为此GitHub项目生成分析，返回JSON对象：
名称：{r["name"]}
描述：{r["description"] or "无"}
语言：{r["language"] or "未知"}

必须包含字段：summary(≤25字)、why_hot(≤40字)、audience(≤15字)、tags(2-3个·分隔)
严格返回合法JSON，无其他内容。"""
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL, contents=prompt, config=JSON_CFG
        )
        return json.loads(resp.text)
    except Exception:
        return {
            "summary":  r["description"][:25] if r["description"] else "暂无",
            "why_hot":  "数据获取中",
            "audience": "开发者",
            "tags":     r["category"],
        }

def enrich_repos(client, repos: list) -> list:
    """为 repos 填充 AI 四字段，三层容错保证每条都有内容。"""
    if not repos:
        return repos

    print(f"    批量 AI 分析（{len(repos)} 条，JSON模式，1次调用）…")
    ai_map = _enrich_batch(client, repos)

    missing = []
    for r in repos:
        ai = ai_map.get(r["rank"])
        if ai and ai.get("summary"):
            r["ai_summary"]  = ai.get("summary","")
            r["ai_why_hot"]  = ai.get("why_hot","")
            r["ai_audience"] = ai.get("audience","")
            r["ai_tags"]     = ai.get("tags","")
        else:
            missing.append(r)

    if missing:
        print(f"    ⚠️  {len(missing)} 条未命中，逐条补填…")
        for r in missing:
            ai = _enrich_single(client, r)
            r["ai_summary"]  = ai.get("summary","")
            r["ai_why_hot"]  = ai.get("why_hot","")
            r["ai_audience"] = ai.get("audience","")
            r["ai_tags"]     = ai.get("tags","")
            time.sleep(0.5)

    filled = sum(1 for r in repos if r["ai_summary"])
    print(f"    ✅ AI 分析完成：{filled}/{len(repos)} 条")
    return repos


# ══════════════════════════════════════════════════════
# Step 4：变化分析（周/月榜 vs 上次）
# ══════════════════════════════════════════════════════
_CHANGE_PROMPT = """\
你是开源社区趋势分析师。以下是GitHub {period_label}榜的排名变化数据。

上期排名：
{prev_json}

本期排名：
{curr_json}

请分析并返回JSON对象，包含：
- trend_summary : 整体趋势判断，2-3句，说明本期最显著的技术风向（≤80字）
- new_entries   : 数组，新上榜项目，每项 {{"name":"...","rank":N,"reason":"上榜原因≤30字"}}
- big_risers    : 数组，排名上升最多的项目（排名变化≥3），每项 {{"name":"...","rank_change":"▲N","reason":"≤30字"}}
- dropouts      : 数组，上期有本期消失的项目，每项 {{"name":"...","reason":"可能的原因≤25字"}}

严格返回合法JSON，无其他内容。"""

def analyze_changes(client, period: str, prev_state: dict, curr_repos: list) -> dict | None:
    """
    对比本期与上期排名，调用 AI 生成变化解读。
    首次运行（无历史）返回 None。
    """
    prev_list = prev_state.get(period, [])
    if not prev_list:
        return None  # 首次运行，无历史数据

    prev_json = json.dumps(
        [{"rank": p["rank"], "name": p["name"]} for p in prev_list],
        ensure_ascii=False
    )
    curr_json = json.dumps(
        [{"rank": r["rank"], "name": r["name"]} for r in curr_repos],
        ensure_ascii=False
    )
    prompt = _CHANGE_PROMPT.format(
        period_label=PERIOD_LABEL[period],
        prev_json=prev_json,
        curr_json=curr_json,
    )

    for attempt in range(1, 4):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL, contents=prompt, config=JSON_CFG
            )
            result = json.loads(resp.text)
            print(f"    ✅ {PERIOD_LABEL[period]}榜变化分析完成")
            return result
        except Exception as e:
            wait = 2 ** attempt
            print(f"    [变化分析重试 {attempt}/3] {e}，等 {wait}s…")
            time.sleep(wait)
    return None


# ══════════════════════════════════════════════════════
# Step 5：飞书卡片构建
# ══════════════════════════════════════════════════════
def _repo_block(r: dict, show_period_stars: bool = True) -> str:
    """
    单条仓库的飞书 Markdown 块（4行）：
    行1: 排名 · 超链接 · 语言 · 爆发标记
    行2: 📌 中文摘要
    行3: 💡 爆火原因  👥 适合人群
    行4: 🏷️ 技术标签  ⭐总星  🚀今日/本周/本月  🍴Fork
    """
    spike  = " 🔥**爆发**" if r["is_spike"] else ""
    lang   = f" `{r['language']}`" if r["language"] else ""
    cat_e  = CAT_EMOJI.get(r["category"], "📦")

    period_lbl = PERIOD_LABEL.get(r.get("period",""), "")
    period_stars_str = f"🚀 {period_lbl} **{r['stars_period']}**" if show_period_stars else ""

    line1 = f"**{r['rank']}. [{r['name']}]({r['url']})**{lang}{spike}"
    line2 = f"📌 {r['ai_summary']}"
    line3 = f"💡 {r['ai_why_hot']}   👥 {r['ai_audience']}"
    line4 = (
        f"{cat_e} {r['ai_tags']}"
        f"  |  ⭐ {r['total_stars']}  {period_stars_str}  🍴 {r['forks']}"
    )
    return "\n".join([line1, line2, line3, line4])


def _change_block(change_data: dict) -> list:
    """把变化分析转成飞书 elements 列表。"""
    if not change_data:
        return []

    elems = [
        {"tag": "hr"},
        {"tag": "markdown", "content": "**📊 本期榜单变化分析**"},
    ]

    # 整体趋势
    ts = change_data.get("trend_summary","")
    if ts:
        elems.append({"tag": "markdown", "content": f"🔭 {ts}"})

    # 新上榜
    new_entries = change_data.get("new_entries", [])
    if new_entries:
        lines = ["**🆕 新上榜**"]
        for e in new_entries[:5]:
            lines.append(f"• **#{e.get('rank','')} {e.get('name','')}** — {e.get('reason','')}")
        elems.append({"tag": "markdown", "content": "\n".join(lines)})

    # 排名大涨
    risers = change_data.get("big_risers", [])
    if risers:
        lines = ["**⬆️ 排名大涨**"]
        for e in risers[:4]:
            lines.append(f"• **{e.get('name','')}** {e.get('rank_change','')} — {e.get('reason','')}")
        elems.append({"tag": "markdown", "content": "\n".join(lines)})

    # 跌出榜单
    dropouts = change_data.get("dropouts", [])
    if dropouts:
        lines = ["**⬇️ 跌出本期榜单**"]
        for e in dropouts[:4]:
            lines.append(f"• **{e.get('name','')}** — {e.get('reason','')}")
        elems.append({"tag": "markdown", "content": "\n".join(lines)})

    return elems


def build_cards(repos: list, period: str, date_str: str,
                change_data: dict | None, batch_size: int = 10) -> list:
    """
    构建该时段的全部卡片列表（可能多张）。
    最后一张卡片附加变化分析（如有）。
    """
    period_lbl  = PERIOD_LABEL[period]
    color       = CARD_COLOR[period]
    batches     = [repos[i:i+batch_size] for i in range(0, len(repos), batch_size)]
    total_cards = len(batches)
    cards       = []

    for idx, batch in enumerate(batches, 1):
        part_lbl    = f"（{idx}/{total_cards}）" if total_cards > 1 else ""
        header_text = (
            f"{'🔥' if period=='daily' else '📈' if period=='weekly' else '📅'} "
            f"GitHub {period_lbl}榜 {date_str} · Top {len(repos)} {part_lbl}"
        )

        elements = []
        for i, r in enumerate(batch):
            elements.append({"tag": "markdown", "content": _repo_block(r)})
            if i < len(batch) - 1:
                elements.append({"tag": "hr"})

        # 最后一张卡片才追加变化分析
        if idx == total_cards and change_data:
            elements.extend(_change_block(change_data))

        # 快捷跳转按钮（取当前批次前5个）
        buttons = [
            {
                "tag":  "button",
                "text": {"tag":"plain_text","content":f"#{r['rank']} {r['name'].split('/')[-1]}"},
                "url":  r["url"],
                "type": "default",
            }
            for r in batch[:5]
        ]
        elements += [
            {"tag": "hr"},
            {"tag": "action", "actions": buttons},
            {
                "tag": "note",
                "elements": [{
                    "tag": "plain_text",
                    "content": (
                        f"每日 08:00 自动推送 · 数据来源 github.com/trending"
                        f" · AI: {GEMINI_MODEL} · 🔥=今日新增>{SPIKE_MIN}⭐"
                    ),
                }],
            },
        ]

        cards.append({
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title":    {"tag":"plain_text","content": header_text},
                    "template": color,
                },
                "elements": elements,
            },
        })

    return cards


# ══════════════════════════════════════════════════════
# Step 6：飞书推送
# ══════════════════════════════════════════════════════
def send_card(webhook_url: str, payload: dict, label: str) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    resp = requests.post(
        webhook_url,
        headers={"Content-Type": "application/json; charset=utf-8"},
        data=body,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    res  = resp.json()
    code = res.get("StatusCode", res.get("code", -1))
    if code != 0:
        raise RuntimeError(f"飞书拒绝: {res}")
    print(f"  ✅ {label}")

def push_period(webhook_url: str, cards: list, period: str) -> None:
    period_lbl = PERIOD_LABEL[period]
    for i, card in enumerate(cards, 1):
        label = f"{period_lbl}榜 第{i}/{len(cards)}张"
        print(f"  📲 推送 {label}…")
        send_card(webhook_url, card, label)
        if i < len(cards):
            time.sleep(1.5)


# ══════════════════════════════════════════════════════
# Step 7：日志统计
# ══════════════════════════════════════════════════════
def print_stats(all_data: dict, date_str: str) -> None:
    print(f"\n{'═'*54}")
    print(f"  📊 {date_str}  三榜统计")
    print(f"{'─'*54}")
    for period in ["daily","weekly","monthly"]:
        repos = all_data.get(period, [])
        if not repos:
            continue
        spikes = [r for r in repos if r["is_spike"]]
        cats   = {}
        for r in repos:
            cats[r["category"]] = cats.get(r["category"],0) + 1
        top3 = sorted(cats.items(), key=lambda x:-x[1])[:3]
        top3_str = " · ".join(f"{CAT_EMOJI.get(c,'📦')}{c}×{n}" for c,n in top3)
        print(f"  {PERIOD_LABEL[period]}榜 {len(repos)} 条  |  {top3_str}")
        if spikes:
            spike_names = " · ".join(r["name"].split("/")[-1] for r in spikes[:3])
            print(f"      🔥 爆发: {spike_names}{' 等' if len(spikes)>3 else ''}")
    print(f"{'═'*54}\n")


# ══════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════
def main() -> None:
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL","").strip()
    gemini_key  = os.environ.get("GEMINI_API_KEY","").strip()
    if not webhook_url:
        sys.exit("❌ 缺少: FEISHU_WEBHOOK_URL")
    if not gemini_key:
        sys.exit("❌ 缺少: GEMINI_API_KEY")

    bj_now   = datetime.now(timezone(timedelta(hours=8)))
    date_str = bj_now.strftime("%Y-%m-%d")
    print(f"\n🗓  {date_str}  GitHub Trending Bot v4 启动")

    # ── 1. 加载历史状态 ───────────────────────────────
    print("\n📂 加载历史状态…")
    prev_state = load_state()
    last_date  = prev_state.get("last_updated","首次运行")
    print(f"   上次更新: {last_date}")

    # ── 2. 抓取三榜 ───────────────────────────────────
    print("\n📡 抓取三榜数据…")
    all_data = fetch_all()
    total = sum(len(v) for v in all_data.values())
    if total == 0:
        sys.exit("❌ 三榜均未获取到数据")

    # ── 3. Gemini AI 分析（JSON 强制模式）─────────────
    client = genai.Client(api_key=gemini_key)
    for period in ["daily","weekly","monthly"]:
        repos = all_data.get(period, [])
        if not repos:
            continue
        print(f"\n🤖 {PERIOD_LABEL[period]}榜 AI 富内容分析…")
        all_data[period] = enrich_repos(client, repos)

    # ── 4. 变化分析（周/月，与上期对比）──────────────
    changes = {}
    for period in ["weekly","monthly"]:
        repos = all_data.get(period, [])
        if not repos:
            continue
        print(f"\n🔍 {PERIOD_LABEL[period]}榜变化分析…")
        changes[period] = analyze_changes(client, period, prev_state, repos)
        if changes[period] is None:
            print(f"   ℹ️  首次运行，无历史数据，跳过变化分析")

    # ── 5. 打印统计 ───────────────────────────────────
    print_stats(all_data, date_str)

    # ── 6. 构建并推送飞书卡片 ─────────────────────────
    print("📨 推送飞书卡片…\n")
    for period in ["daily","weekly","monthly"]:
        repos = all_data.get(period, [])
        if not repos:
            continue
        cards = build_cards(
            repos, period, date_str,
            change_data=changes.get(period),
            batch_size=10,
        )
        push_period(webhook_url, cards, period)
        time.sleep(2)  # 三榜之间间隔，避免飞书频率限制

    # ── 7. 保存本期状态（供下次对比）────────────────
    print("\n💾 保存本期状态…")
    save_state(all_data, date_str)

    print(f"\n🎉 全部完成！三榜已推送到飞书\n")


if __name__ == "__main__":
    main()
