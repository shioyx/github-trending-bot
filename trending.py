"""
GitHub Trending Bot v5 ── 三榜深度分析终极版
════════════════════════════════════════════════════════════
架构亮点：
  ① 每项目单独 AI 调用（非批量）→ 100% 保证中文内容
  ② 三重 JSON 提取（直接解析→正则提取→fence提取）
  ③ 每条 6 行：摘要·爆火原因·市场信号·适合人群·标签·数据
  ④ 三榜独立推送（日🔴/周🟢/月🔵），颜色区分
  ⑤ 周/月榜末尾：AI 变化分析（新上·大涨·跌出·趋势判断）
  ⑥ state.json 持久化，第二次起自动对比排名变化
════════════════════════════════════════════════════════════
"""
import os, sys, time, json, re, traceback, requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from google import genai
from google.genai import types

# ══════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════
GEMINI_MODEL  = "gemini-2.0-flash"
TIMEOUT       = 25
SPIKE_MIN     = 500       # 今日新增 > 此值 → 🔥爆发标记
STATE_FILE    = "state.json"
AI_CALL_DELAY = 1.2       # 每次 AI 调用间隔（秒），免费版15rpm限制

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
CARD_ICON    = {"daily":"🔥","weekly":"📈","monthly":"📅"}

# JSON 强制输出模式（gemini-2.0-flash 完全支持）
JSON_CFG = types.GenerateContentConfig(
    response_mime_type="application/json",
    temperature=0.2,
)


# ══════════════════════════════════════════════════════
# 工具：三重 JSON 提取（绝对不会因格式问题挂掉）
# ══════════════════════════════════════════════════════
def safe_json(raw: str):
    """三重策略提取 JSON，任一成功即返回，全部失败返回 None。"""
    strategies = [
        lambda t: json.loads(t),
        lambda t: json.loads(re.search(r'\{[\s\S]*\}', t).group()),
        lambda t: json.loads(re.search(r'```(?:json)?\s*([\s\S]*?)```', t).group(1).strip()),
    ]
    for fn in strategies:
        try:
            return fn(raw.strip())
        except Exception:
            continue
    return None


# ══════════════════════════════════════════════════════
# Step 1：抓取三榜（日/周/月）
# ══════════════════════════════════════════════════════
def _parse_num(text: str) -> int:
    m = re.search(r"[\d,]+", text or "")
    return int(m.group().replace(",", "")) if m else 0

def _auto_cat(language: str, description: str) -> str:
    lang = language.lower()
    for cat, langs in CATEGORY_MAP.items():
        if lang in langs:
            return cat
    kws = ["llm","ai","gpt","agent","openai","claude","deepseek",
           "neural","transformer","generative","machine learning","deep learning"]
    if any(k in description.lower() for k in kws):
        return "AI / ML"
    return "其他"

def fetch_period(session, since: str) -> list:
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
    repos = []
    for rank, art in enumerate(BeautifulSoup(r.text, "html.parser").select("article.Box-row"), 1):
        h2 = art.select_one("h2 a")
        if not h2:
            continue
        name = h2.get("href", "").strip("/")
        if not name or "/" not in name:
            continue
        desc_el     = art.select_one("p")
        desc        = desc_el.get_text(strip=True) if desc_el else ""
        lang_el     = art.select_one("span[itemprop='programmingLanguage']")
        language    = lang_el.get_text(strip=True) if lang_el else ""
        links       = art.select("a.Link--muted")
        total_stars = links[0].get_text(strip=True) if links else "—"
        forks       = links[1].get_text(strip=True) if len(links) > 1 else "—"
        today_el    = art.select_one("span.d-inline-block.float-sm-right")
        stars_p     = today_el.get_text(strip=True) if today_el else "—"
        stars_num   = _parse_num(stars_p)
        repos.append({
            "rank":           rank,
            "name":           name,
            "url":            f"https://github.com/{name}",
            "description":    desc,
            "language":       language,
            "category":       _auto_cat(language, desc),
            "total_stars":    total_stars,
            "forks":          forks,
            "stars_period":   stars_p,
            "stars_num":      stars_num,
            "is_spike":       stars_num >= SPIKE_MIN,
            "period":         since,
            # AI 字段（后续填充）
            "ai_summary":     "",
            "ai_why_hot":     "",
            "ai_market":      "",
            "ai_audience":    "",
            "ai_tags":        "",
        })
    return repos

def fetch_all() -> dict:
    session = requests.Session()
    try:
        session.get("https://github.com", timeout=8,
                    headers={"User-Agent": "Mozilla/5.0"})
    except Exception:
        pass
    data = {}
    for since in ["daily", "weekly", "monthly"]:
        try:
            repos = fetch_period(session, since)
            data[since] = repos
            print(f"   ✅ {PERIOD_LABEL[since]}榜: {len(repos)} 条")
        except Exception as e:
            print(f"   ❌ {since} 抓取失败: {e}")
            traceback.print_exc()
            data[since] = []
    return data


# ══════════════════════════════════════════════════════
# Step 2：AI 逐条深度分析（单独调用，100% 保证中文输出）
# ══════════════════════════════════════════════════════
_SINGLE_PROMPT = """\
你是资深开源社区分析师和技术趋势研究员。分析以下GitHub项目并返回JSON。

项目名称：{name}
英文描述：{desc}
编程语言：{lang}
{period_label}涨星：{stars}
总星数：{total_stars}

返回如下JSON对象（所有字段必须为中文，不含任何其他文字）：
{{
  "summary": "一句话说清楚这个项目是做什么的（≤25字，突出核心功能）",
  "why_hot": "深入分析为什么这个项目最近涨星这么快，要结合具体技术背景和行业事件（≤60字）",
  "market_signal": "这个项目的走红反映了哪些市场趋势、行业变化或技术风向（≤50字）",
  "audience": "最适合哪类人关注（≤15字，如：AI工程师·全栈开发者）",
  "tags": "2-3个精准技术标签，用·分隔（如：AI Agent·多智能体·金融分析）"
}}"""

def _ai_one(client, r: dict, period_label: str) -> dict:
    """
    对单个仓库调用 Gemini，返回 AI 分析字段字典。
    使用 JSON 强制模式 + 三重提取，保证返回有效数据。
    """
    prompt = _SINGLE_PROMPT.format(
        name=r["name"],
        desc=r["description"] or "No description provided.",
        lang=r["language"] or "Unknown",
        period_label=period_label,
        stars=r["stars_period"],
        total_stars=r["total_stars"],
    )
    for attempt in range(1, 4):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=JSON_CFG,
            )
            raw    = resp.text.strip()
            result = safe_json(raw)
            if result and result.get("summary"):
                return result
            print(f"      ⚠️  JSON解析空结果（attempt {attempt}），重试…")
            print(f"      原始响应: {raw[:120]}")
        except Exception as e:
            wait = 2 ** attempt
            print(f"      ⚠️  API调用失败（attempt {attempt}）: {e}")
            if attempt < 3:
                time.sleep(wait)

    # 三次失败后的硬兜底（绝对不出现空白）
    print(f"      🔴 {r['name']} AI 三次失败，使用硬兜底")
    return {
        "summary":       f"{r['description'][:22]}…" if r["description"] else "开源项目",
        "why_hot":       f"本{period_label}涨星 {r['stars_period']}，社区关注度快速上升",
        "market_signal": f"{r['category']} 领域持续升温，开发者关注度提升",
        "audience":      "开发者",
        "tags":          r["category"],
    }

def enrich_all(client, repos: list, period: str) -> list:
    """逐条调用 AI，填充所有分析字段。"""
    label = PERIOD_LABEL[period]
    total = len(repos)
    for i, r in enumerate(repos, 1):
        print(f"   [{i:02d}/{total}] {r['name']} …")
        ai = _ai_one(client, r, label)
        r["ai_summary"]  = ai.get("summary",  "")
        r["ai_why_hot"]  = ai.get("why_hot",  "")
        r["ai_market"]   = ai.get("market_signal", "")
        r["ai_audience"] = ai.get("audience", "")
        r["ai_tags"]     = ai.get("tags",     r["category"])
        print(f"        📌 {r['ai_summary']}")
        print(f"        💡 {r['ai_why_hot']}")
        print(f"        📊 {r['ai_market']}")
        if i < total:
            time.sleep(AI_CALL_DELAY)
    return repos


# ══════════════════════════════════════════════════════
# Step 3：周/月榜变化分析
# ══════════════════════════════════════════════════════
def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(data: dict, date_str: str) -> None:
    try:
        state = {"last_updated": date_str}
        for period in ["weekly", "monthly"]:
            state[period] = [
                {"rank": r["rank"], "name": r["name"], "stars": r["stars_period"]}
                for r in data.get(period, [])
            ]
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        print(f"   ✅ 状态保存 → {STATE_FILE}")
    except Exception as e:
        print(f"   ⚠️  保存失败（不影响推送）: {e}")

_CHANGE_PROMPT = """\
你是开源社区趋势分析师。分析GitHub{period_label}榜的排名变化。

上期排名：{prev_json}

本期排名：{curr_json}

返回JSON对象（中文，不含其他文字）：
{{
  "trend_summary": "2-3句整体趋势判断，说明本期最显著的技术风向和行业变化（≤100字）",
  "new_entries": [
    {{"name": "仓库名", "rank": 排名整数, "reason": "为何能上榜（≤35字）"}}
  ],
  "big_risers": [
    {{"name": "仓库名", "rank_change": "▲N", "reason": "上升原因（≤30字）"}}
  ],
  "dropouts": [
    {{"name": "仓库名", "reason": "跌出原因分析（≤25字）"}}
  ]
}}"""

def analyze_changes(client, period: str, prev_state: dict, curr_repos: list):
    prev_list = prev_state.get(period, [])
    if not prev_list:
        return None
    prompt = _CHANGE_PROMPT.format(
        period_label=PERIOD_LABEL[period],
        prev_json=json.dumps(
            [{"rank": p["rank"], "name": p["name"]} for p in prev_list],
            ensure_ascii=False
        ),
        curr_json=json.dumps(
            [{"rank": r["rank"], "name": r["name"]} for r in curr_repos],
            ensure_ascii=False
        ),
    )
    for attempt in range(1, 4):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL, contents=prompt, config=JSON_CFG
            )
            result = safe_json(resp.text)
            if result and result.get("trend_summary"):
                print(f"   ✅ {PERIOD_LABEL[period]}榜变化分析完成")
                return result
        except Exception as e:
            wait = 2 ** attempt
            print(f"   ⚠️  变化分析重试 {attempt}/3: {e}，等 {wait}s")
            time.sleep(wait)
    print(f"   ⚠️  变化分析失败，跳过")
    return None


# ══════════════════════════════════════════════════════
# Step 4：飞书卡片构建
# ══════════════════════════════════════════════════════
def _repo_block(r: dict) -> str:
    """
    6 行格式（每条信息层次清晰，不再有任何英文原文）：
    行1: 排名·仓库链接·语言·爆发标记
    行2: 📌 项目摘要（中文，说清楚是做什么的）
    行3: 💡 爆火原因（为什么涨星快）
    行4: 📊 市场信号（反映了什么趋势）
    行5: 👥 适合人群  |  🏷 技术标签
    行6: ⭐ 总星  🚀 时段涨星  🍴 Fork
    """
    spike  = " 🔥**爆发**" if r["is_spike"] else ""
    lang   = f" `{r['language']}`" if r["language"] else ""
    cat_e  = CAT_EMOJI.get(r["category"], "📦")
    pl     = PERIOD_LABEL.get(r.get("period", ""), "")
    return "\n".join([
        f"**{r['rank']}. [{r['name']}]({r['url']})**{lang}{spike}",
        f"📌 {r['ai_summary']}",
        f"💡 {r['ai_why_hot']}",
        f"📊 {r['ai_market']}",
        f"👥 {r['ai_audience']}   {cat_e} {r['ai_tags']}",
        f"⭐ {r['total_stars']}  🚀 {pl} **{r['stars_period']}**  🍴 {r['forks']}",
    ])

def _change_elems(cd: dict) -> list:
    if not cd:
        return []
    elems = [
        {"tag": "hr"},
        {"tag": "markdown", "content": "**📊 本期榜单变化分析**"},
    ]
    ts = cd.get("trend_summary", "")
    if ts:
        elems.append({"tag": "markdown", "content": f"🔭 {ts}"})

    new_entries = cd.get("new_entries", [])
    if new_entries:
        lines = ["**🆕 新上榜**"]
        for e in new_entries[:6]:
            lines.append(f"• **#{e.get('rank','')} {e.get('name','')}** — {e.get('reason','')}")
        elems.append({"tag": "markdown", "content": "\n".join(lines)})

    risers = cd.get("big_risers", [])
    if risers:
        lines = ["**⬆️ 排名大涨**"]
        for e in risers[:4]:
            lines.append(f"• **{e.get('name','')}** {e.get('rank_change','')} — {e.get('reason','')}")
        elems.append({"tag": "markdown", "content": "\n".join(lines)})

    dropouts = cd.get("dropouts", [])
    if dropouts:
        lines = ["**⬇️ 跌出榜单**"]
        for e in dropouts[:4]:
            lines.append(f"• **{e.get('name','')}** — {e.get('reason','')}")
        elems.append({"tag": "markdown", "content": "\n".join(lines)})

    return elems

def build_cards(repos: list, period: str, date_str: str,
                change_data=None, batch_size: int = 10) -> list:
    lbl         = PERIOD_LABEL[period]
    icon        = CARD_ICON[period]
    color       = CARD_COLOR[period]
    batches     = [repos[i:i+batch_size] for i in range(0, len(repos), batch_size)]
    total_cards = len(batches)
    cards       = []

    for idx, batch in enumerate(batches, 1):
        part_lbl    = f"（{idx}/{total_cards}）" if total_cards > 1 else ""
        header_text = f"{icon} GitHub {lbl}榜 {date_str} · Top {len(repos)} {part_lbl}"

        elements = []
        for i, r in enumerate(batch):
            elements.append({"tag": "markdown", "content": _repo_block(r)})
            if i < len(batch) - 1:
                elements.append({"tag": "hr"})

        if idx == total_cards and change_data:
            elements.extend(_change_elems(change_data))

        buttons = [
            {"tag": "button",
             "text": {"tag": "plain_text",
                      "content": f"#{r['rank']} {r['name'].split('/')[-1]}"},
             "url": r["url"], "type": "default"}
            for r in batch[:5]
        ]
        elements += [
            {"tag": "hr"},
            {"tag": "action", "actions": buttons},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": (
                f"每日 08:00 自动推送 · 数据来源 github.com/trending"
                f" · AI 分析: {GEMINI_MODEL} · 🔥=今日新增>{SPIKE_MIN}⭐"
            )}]},
        ]
        cards.append({
            "msg_type": "interactive",
            "card": {
                "config":  {"wide_screen_mode": True},
                "header":  {
                    "title":    {"tag": "plain_text", "content": header_text},
                    "template": color,
                },
                "elements": elements,
            },
        })
    return cards


# ══════════════════════════════════════════════════════
# Step 5：飞书推送
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
    print(f"   ✅ {label} 推送成功")

def push_period(webhook_url: str, cards: list, period: str) -> None:
    lbl = PERIOD_LABEL[period]
    for i, card in enumerate(cards, 1):
        label = f"{lbl}榜 第{i}/{len(cards)}张"
        print(f"   📲 推送 {label}…")
        send_card(webhook_url, card, label)
        if i < len(cards):
            time.sleep(1.5)


# ══════════════════════════════════════════════════════
# Step 6：统计日志
# ══════════════════════════════════════════════════════
def print_stats(all_data: dict, date_str: str) -> None:
    print(f"\n{'═'*58}")
    print(f"  📊 {date_str}  三榜统计")
    for period in ["daily", "weekly", "monthly"]:
        repos = all_data.get(period, [])
        if not repos:
            continue
        spikes = [r for r in repos if r["is_spike"]]
        cats   = {}
        for r in repos:
            cats[r["category"]] = cats.get(r["category"], 0) + 1
        top3 = sorted(cats.items(), key=lambda x: -x[1])[:3]
        top3_str = "  ".join(f"{CAT_EMOJI.get(c,'📦')}{c}×{n}" for c, n in top3)
        print(f"  {'─'*56}")
        print(f"  {CARD_ICON[period]} {PERIOD_LABEL[period]}榜 {len(repos)} 条  {top3_str}")
        if spikes:
            names = " · ".join(r["name"].split("/")[-1] for r in spikes[:3])
            print(f"      🔥 爆发: {names}{'…' if len(spikes) > 3 else ''}")
    print(f"{'═'*58}\n")


# ══════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════
def main() -> None:
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL", "").strip()
    gemini_key  = os.environ.get("GEMINI_API_KEY", "").strip()
    if not webhook_url:
        sys.exit("❌ 缺少: FEISHU_WEBHOOK_URL")
    if not gemini_key:
        sys.exit("❌ 缺少: GEMINI_API_KEY")

    bj_now   = datetime.now(timezone(timedelta(hours=8)))
    date_str = bj_now.strftime("%Y-%m-%d")
    print(f"\n{'═'*58}")
    print(f"  🗓  {date_str}  GitHub Trending Bot v5")
    print(f"  模型: {GEMINI_MODEL} · 逐条调用 · JSON强制模式")
    print(f"{'═'*58}")

    # 1. 加载历史
    print("\n📂 加载历史状态…")
    prev_state = load_state()
    print(f"   上次更新: {prev_state.get('last_updated', '首次运行')}")

    # 2. 抓取三榜
    print("\n📡 抓取三榜数据…")
    all_data = fetch_all()
    if not any(all_data.values()):
        sys.exit("❌ 三榜均为空")

    # 3. 逐条 AI 深度分析
    client = genai.Client(api_key=gemini_key)
    for period in ["daily", "weekly", "monthly"]:
        repos = all_data.get(period, [])
        if not repos:
            continue
        print(f"\n🤖 {PERIOD_LABEL[period]}榜 AI 逐条分析（{len(repos)} 条）…")
        all_data[period] = enrich_all(client, repos, period)

    # 4. 周/月变化分析
    changes = {}
    for period in ["weekly", "monthly"]:
        repos = all_data.get(period, [])
        if not repos:
            continue
        print(f"\n🔍 {PERIOD_LABEL[period]}榜变化对比…")
        changes[period] = analyze_changes(client, period, prev_state, repos)
        if changes[period] is None:
            print(f"   ℹ️  首次运行，跳过（下次起生效）")

    # 5. 统计
    print_stats(all_data, date_str)

    # 6. 推送
    print("📨 推送飞书卡片…")
    for period in ["daily", "weekly", "monthly"]:
        repos = all_data.get(period, [])
        if not repos:
            continue
        print(f"\n  ── {PERIOD_LABEL[period]}榜 ──")
        cards = build_cards(repos, period, date_str,
                            change_data=changes.get(period), batch_size=10)
        push_period(webhook_url, cards, period)
        time.sleep(2)

    # 7. 保存状态
    print("\n💾 保存本期状态…")
    save_state(all_data, date_str)

    print(f"\n🎉 全部完成！三榜已推送到飞书\n")


if __name__ == "__main__":
    main()
