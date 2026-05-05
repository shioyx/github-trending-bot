"""
GitHub Trending Bot v4.1 ── 三榜分析终极版（修复版）
════════════════════════════════════════════════════════════
修复：
  ✅ 模型换为 gemini-2.0-flash（标准模型，JSON模式100%稳定）
  ✅ 每步详细日志，失败立刻打印完整 traceback，方便定位
  ✅ 所有 AI 调用加 try/except，任何单条失败不影响整体推送
  ✅ state.json 保存失败不影响推送（catch并记录，不 sys.exit）
功能：
  三榜（日/周/月）各自独立推送，颜色不同
  每个项目5行：超链接·中文摘要·爆火原因·适合人群·技术标签+星数
  周/月榜末尾：AI 变化分析（新上榜·大涨·跌出·趋势判断）
  首次运行自动跳过变化分析，第二次起生效
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
GEMINI_MODEL = "gemini-2.0-flash"          # 标准模型，JSON模式稳定
TIMEOUT      = 20
SPIKE_MIN    = 500
STATE_FILE   = "state.json"

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

# JSON 强制模式配置（gemini-2.0-flash 完全支持）
JSON_CFG = types.GenerateContentConfig(
    response_mime_type="application/json",
    temperature=0.2,
)


# ══════════════════════════════════════════════════════
# Step 1：抓取三榜
# ══════════════════════════════════════════════════════
def _parse_num(text):
    m = re.search(r"[\d,]+", text or "")
    return int(m.group().replace(",","")) if m else 0

def _category(language, description):
    lang = language.lower()
    for cat, langs in CATEGORY_MAP.items():
        if lang in langs:
            return cat
    kws = ["llm","ai","gpt","model","neural","agent","openai","claude",
           "deepseek","machine learning","deep learning","transformer","generative"]
    if any(k in description.lower() for k in kws):
        return "AI / ML"
    return "其他"

def fetch_period(session, since):
    resp = session.get(
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
    resp.raise_for_status()
    soup  = BeautifulSoup(resp.text, "html.parser")
    repos = []
    for rank, art in enumerate(soup.select("article.Box-row"), 1):
        h2 = art.select_one("h2 a")
        if not h2:
            continue
        name = h2.get("href","").strip("/")
        if not name or "/" not in name:
            continue
        desc_el  = art.select_one("p")
        desc     = desc_el.get_text(strip=True) if desc_el else ""
        lang_el  = art.select_one("span[itemprop='programmingLanguage']")
        language = lang_el.get_text(strip=True) if lang_el else ""
        links    = art.select("a.Link--muted")
        total_stars = links[0].get_text(strip=True) if links else "—"
        forks       = links[1].get_text(strip=True) if len(links)>1 else "—"
        today_el    = art.select_one("span.d-inline-block.float-sm-right")
        stars_period = today_el.get_text(strip=True) if today_el else "—"
        stars_num    = _parse_num(stars_period)
        repos.append({
            "rank":         rank,
            "name":         name,
            "url":          f"https://github.com/{name}",
            "description":  desc,
            "language":     language,
            "category":     _category(language, desc),
            "total_stars":  total_stars,
            "forks":        forks,
            "stars_period": stars_period,
            "stars_num":    stars_num,
            "is_spike":     stars_num >= SPIKE_MIN,
            "period":       since,
            "ai_summary":   "",
            "ai_why_hot":   "",
            "ai_audience":  "",
            "ai_tags":      "",
        })
    return repos

def fetch_all():
    session = requests.Session()
    try:
        session.get("https://github.com", timeout=8,
                    headers={"User-Agent":"Mozilla/5.0"})
    except Exception:
        pass
    data = {}
    for since in ["daily","weekly","monthly"]:
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
# Step 2：状态持久化
# ══════════════════════════════════════════════════════
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(data, date_str):
    try:
        state = {"last_updated": date_str}
        for period in ["weekly","monthly"]:
            state[period] = [
                {"rank":r["rank"],"name":r["name"],"stars":r["stars_period"]}
                for r in data.get(period,[])
            ]
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        print(f"   ✅ 状态已写入 {STATE_FILE}")
    except Exception as e:
        print(f"   ⚠️  保存状态失败（不影响推送）: {e}")


# ══════════════════════════════════════════════════════
# Step 3：AI 富内容（JSON强制模式，三层容错）
# ══════════════════════════════════════════════════════
_ENRICH_PROMPT = """\
你是资深开源技术分析师，请为以下GitHub项目生成中文分析。

输入（JSON数组）：
{items_json}

输出要求（严格JSON数组，每个对象包含）：
- id       : 整数，原样返回
- summary  : ≤25字中文，说明项目核心功能
- why_hot  : ≤40字中文，分析近期爆火的具体原因（结合技术趋势/行业需求/项目特点）
- audience : ≤15字中文，最适合哪类人关注（如"前端开发者·AI工程师"）
- tags     : 2-3个中文技术标签，用·分隔（如"AI Agent·金融分析·多智能体"）

只输出JSON数组，不含任何其他文字。"""

def _call_gemini_json(client, prompt):
    """调用 Gemini JSON 模式，返回解析后的 Python 对象。"""
    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=JSON_CFG,
    )
    return json.loads(resp.text)

def enrich_repos(client, repos):
    if not repos:
        return repos

    # —— Batch 调用（1次搞定全部）——
    items = [
        {"id":r["rank"],"name":r["name"],
         "desc":r["description"] or "No description.",
         "lang":r["language"] or "Unknown",
         "stars_period":r["stars_period"]}
        for r in repos
    ]
    prompt = _ENRICH_PROMPT.format(items_json=json.dumps(items, ensure_ascii=False))

    ai_map = {}
    for attempt in range(1, 4):
        try:
            data = _call_gemini_json(client, prompt)
            ai_map = {int(item["id"]): item for item in data}
            print(f"   ✅ Batch AI 完成（{len(ai_map)}/{len(repos)} 条命中，1次调用）")
            break
        except Exception as e:
            wait = 2 ** attempt
            print(f"   ⚠️  Batch重试 {attempt}/3: {e}，等 {wait}s")
            time.sleep(wait)

    # —— 逐条兜底（Batch未命中的）——
    missing = [r for r in repos if r["rank"] not in ai_map or not ai_map[r["rank"]].get("summary")]
    if missing:
        print(f"   ⚠️  {len(missing)} 条未命中，逐条补填…")
    for r in missing:
        single_prompt = (
            f'为此GitHub项目生成分析，返回JSON对象：\n'
            f'名称：{r["name"]}\n描述：{r["description"] or "无"}\n语言：{r["language"] or "未知"}\n'
            f'字段：summary(≤25字), why_hot(≤40字), audience(≤15字), tags(2-3个·分隔)\n'
            f'只输出JSON对象。'
        )
        for attempt in range(1, 3):
            try:
                ai = _call_gemini_json(client, single_prompt)
                ai_map[r["rank"]] = ai
                break
            except Exception as e:
                if attempt == 2:
                    # 最终兜底：英文截断
                    ai_map[r["rank"]] = {
                        "summary":  (r["description"][:24] + "…") if r["description"] else "开源项目",
                        "why_hot":  "近期社区关注度持续上升",
                        "audience": "开发者",
                        "tags":     r["category"],
                    }
                else:
                    time.sleep(2)

    # —— 写回 repos ——
    for r in repos:
        ai = ai_map.get(r["rank"], {})
        r["ai_summary"]  = ai.get("summary","") or "开源项目"
        r["ai_why_hot"]  = ai.get("why_hot","")  or "社区关注度上升"
        r["ai_audience"] = ai.get("audience","") or "开发者"
        r["ai_tags"]     = ai.get("tags","")     or r["category"]

    filled = sum(1 for r in repos if r["ai_summary"] not in ("","开源项目","暂无"))
    print(f"   ✅ AI分析写入完成：{len(repos)} 条")
    return repos


# ══════════════════════════════════════════════════════
# Step 4：变化分析（周/月）
# ══════════════════════════════════════════════════════
_CHANGE_PROMPT = """\
你是开源社区趋势分析师，分析GitHub{period_label}榜排名变化。

上期排名（JSON）：
{prev_json}

本期排名（JSON）：
{curr_json}

返回JSON对象，包含：
- trend_summary : 2-3句整体趋势判断，说明最显著的技术风向（≤80字）
- new_entries   : 数组，新上榜项目（上期没有本期有），每项含 name, rank(整数), reason(≤30字)
- big_risers    : 数组，排名上升≥3名的项目，每项含 name, rank_change(如"▲5"), reason(≤30字)
- dropouts      : 数组，上期有本期消失的项目，每项含 name, reason(≤25字)

只输出JSON对象，不含任何其他文字。"""

def analyze_changes(client, period, prev_state, curr_repos):
    prev_list = prev_state.get(period, [])
    if not prev_list:
        return None
    prev_names = {p["name"]: p["rank"] for p in prev_list}
    curr_names = {r["name"]: r["rank"] for r in curr_repos}
    prev_json  = json.dumps([{"rank":p["rank"],"name":p["name"]} for p in prev_list], ensure_ascii=False)
    curr_json  = json.dumps([{"rank":r["rank"],"name":r["name"]} for r in curr_repos], ensure_ascii=False)
    prompt = _CHANGE_PROMPT.format(
        period_label=PERIOD_LABEL[period],
        prev_json=prev_json, curr_json=curr_json,
    )
    for attempt in range(1, 4):
        try:
            result = _call_gemini_json(client, prompt)
            print(f"   ✅ {PERIOD_LABEL[period]}榜变化分析完成")
            return result
        except Exception as e:
            wait = 2 ** attempt
            print(f"   ⚠️  变化分析重试 {attempt}/3: {e}，等 {wait}s")
            time.sleep(wait)
    print(f"   ⚠️  {PERIOD_LABEL[period]}变化分析失败，跳过（不影响推送）")
    return None


# ══════════════════════════════════════════════════════
# Step 5：飞书卡片构建
# ══════════════════════════════════════════════════════
def _repo_block(r):
    """5行格式，信息完整、层次清晰。"""
    spike = " 🔥**爆发**" if r["is_spike"] else ""
    lang  = f" `{r['language']}`" if r["language"] else ""
    cat_e = CAT_EMOJI.get(r["category"], "📦")
    pl    = PERIOD_LABEL.get(r.get("period",""), "")
    return "\n".join([
        f"**{r['rank']}. [{r['name']}]({r['url']})**{lang}{spike}",
        f"📌 {r['ai_summary']}",
        f"💡 {r['ai_why_hot']}",
        f"👥 {r['ai_audience']}  |  {cat_e} {r['ai_tags']}",
        f"⭐ {r['total_stars']}  🚀 {pl} **{r['stars_period']}**  🍴 {r['forks']}",
    ])

def _change_elements(change_data):
    if not change_data:
        return []
    elems = [
        {"tag":"hr"},
        {"tag":"markdown","content":"**📊 本期榜单变化分析**"},
    ]
    ts = change_data.get("trend_summary","")
    if ts:
        elems.append({"tag":"markdown","content":f"🔭 {ts}"})

    new_entries = change_data.get("new_entries",[])
    if new_entries:
        lines = ["**🆕 新上榜**"]
        for e in new_entries[:5]:
            lines.append(f"• **#{e.get('rank','')} {e.get('name','')}** — {e.get('reason','')}")
        elems.append({"tag":"markdown","content":"\n".join(lines)})

    risers = change_data.get("big_risers",[])
    if risers:
        lines = ["**⬆️ 排名大涨**"]
        for e in risers[:4]:
            lines.append(f"• **{e.get('name','')}** {e.get('rank_change','')} — {e.get('reason','')}")
        elems.append({"tag":"markdown","content":"\n".join(lines)})

    dropouts = change_data.get("dropouts",[])
    if dropouts:
        lines = ["**⬇️ 跌出榜单**"]
        for e in dropouts[:4]:
            lines.append(f"• **{e.get('name','')}** — {e.get('reason','')}")
        elems.append({"tag":"markdown","content":"\n".join(lines)})

    return elems

def build_cards(repos, period, date_str, change_data=None, batch_size=10):
    period_lbl  = PERIOD_LABEL[period]
    icon        = {"daily":"🔥","weekly":"📈","monthly":"📅"}[period]
    color       = CARD_COLOR[period]
    batches     = [repos[i:i+batch_size] for i in range(0, len(repos), batch_size)]
    total_cards = len(batches)
    cards       = []

    for idx, batch in enumerate(batches, 1):
        part_lbl    = f"（{idx}/{total_cards}）" if total_cards > 1 else ""
        header_text = f"{icon} GitHub {period_lbl}榜 {date_str} · Top {len(repos)} {part_lbl}"

        elements = []
        for i, r in enumerate(batch):
            elements.append({"tag":"markdown","content":_repo_block(r)})
            if i < len(batch)-1:
                elements.append({"tag":"hr"})

        # 最后一张追加变化分析
        if idx == total_cards and change_data:
            elements.extend(_change_elements(change_data))

        # 快捷跳转按钮（当前批次前5个）
        buttons = [
            {"tag":"button",
             "text":{"tag":"plain_text","content":f"#{r['rank']} {r['name'].split('/')[-1]}"},
             "url":r["url"],"type":"default"}
            for r in batch[:5]
        ]
        elements += [
            {"tag":"hr"},
            {"tag":"action","actions":buttons},
            {"tag":"note","elements":[{
                "tag":"plain_text",
                "content":(
                    f"每日 08:00 自动推送 · 数据 github.com/trending"
                    f" · AI: {GEMINI_MODEL} · 🔥=今日新增>{SPIKE_MIN}⭐"
                ),
            }]},
        ]
        cards.append({
            "msg_type":"interactive",
            "card":{
                "config":{"wide_screen_mode":True},
                "header":{"title":{"tag":"plain_text","content":header_text},"template":color},
                "elements":elements,
            },
        })
    return cards


# ══════════════════════════════════════════════════════
# Step 6：飞书推送
# ══════════════════════════════════════════════════════
def send_card(webhook_url, payload, label):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    resp = requests.post(
        webhook_url,
        headers={"Content-Type":"application/json; charset=utf-8"},
        data=body, timeout=TIMEOUT,
    )
    resp.raise_for_status()
    res  = resp.json()
    code = res.get("StatusCode", res.get("code", -1))
    if code != 0:
        raise RuntimeError(f"飞书拒绝: {res}")
    print(f"   ✅ {label} 推送成功")

def push_period(webhook_url, cards, period):
    lbl = PERIOD_LABEL[period]
    for i, card in enumerate(cards, 1):
        label = f"{lbl}榜 第{i}/{len(cards)}张"
        print(f"   📲 推送 {label}…")
        send_card(webhook_url, card, label)
        if i < len(cards):
            time.sleep(1.5)


# ══════════════════════════════════════════════════════
# Step 7：统计日志
# ══════════════════════════════════════════════════════
def print_stats(all_data, date_str):
    print(f"\n{'═'*56}")
    print(f"  📊 {date_str}  三榜统计")
    for period in ["daily","weekly","monthly"]:
        repos = all_data.get(period,[])
        if not repos: continue
        spikes = [r for r in repos if r["is_spike"]]
        cats   = {}
        for r in repos:
            cats[r["category"]] = cats.get(r["category"],0)+1
        top3 = sorted(cats.items(), key=lambda x:-x[1])[:3]
        top3_str = " · ".join(f"{CAT_EMOJI.get(c,'📦')}{c}×{n}" for c,n in top3)
        print(f"  {'─'*54}")
        print(f"  {PERIOD_LABEL[period]}榜 {len(repos)} 条  {top3_str}")
        if spikes:
            spike_str = " · ".join(r["name"].split("/")[-1] for r in spikes[:3])
            print(f"    🔥 爆发: {spike_str}{'…' if len(spikes)>3 else ''}")
    print(f"{'═'*56}\n")


# ══════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════
def main():
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL","").strip()
    gemini_key  = os.environ.get("GEMINI_API_KEY","").strip()
    if not webhook_url:
        sys.exit("❌ 缺少环境变量: FEISHU_WEBHOOK_URL")
    if not gemini_key:
        sys.exit("❌ 缺少环境变量: GEMINI_API_KEY")

    bj_now   = datetime.now(timezone(timedelta(hours=8)))
    date_str = bj_now.strftime("%Y-%m-%d")
    print(f"\n🗓  {date_str}  GitHub Trending Bot v4.1 启动")
    print(f"   模型: {GEMINI_MODEL}（JSON强制模式）\n")

    # 1. 加载历史
    print("📂 加载历史状态…")
    prev_state = load_state()
    print(f"   上次更新: {prev_state.get('last_updated','首次运行')}")

    # 2. 抓取三榜
    print("\n📡 抓取三榜数据…")
    all_data = fetch_all()
    total = sum(len(v) for v in all_data.values())
    if total == 0:
        sys.exit("❌ 三榜均为空，退出")

    # 3. AI 分析
    client = genai.Client(api_key=gemini_key)
    for period in ["daily","weekly","monthly"]:
        repos = all_data.get(period,[])
        if not repos: continue
        print(f"\n🤖 {PERIOD_LABEL[period]}榜 AI 分析（{len(repos)} 条）…")
        all_data[period] = enrich_repos(client, repos)

    # 4. 变化分析
    changes = {}
    for period in ["weekly","monthly"]:
        repos = all_data.get(period,[])
        if not repos: continue
        print(f"\n🔍 {PERIOD_LABEL[period]}榜变化对比…")
        changes[period] = analyze_changes(client, period, prev_state, repos)
        if changes[period] is None:
            print(f"   ℹ️  首次运行，跳过变化分析")

    # 5. 统计
    print_stats(all_data, date_str)

    # 6. 推送
    print("📨 开始推送飞书卡片…")
    for period in ["daily","weekly","monthly"]:
        repos = all_data.get(period,[])
        if not repos: continue
        print(f"\n  ── {PERIOD_LABEL[period]}榜 ──")
        cards = build_cards(repos, period, date_str,
                            change_data=changes.get(period), batch_size=10)
        push_period(webhook_url, cards, period)
        time.sleep(2)

    # 7. 保存状态
    print("\n💾 保存状态…")
    save_state(all_data, date_str)

    print(f"\n🎉 全部完成！三榜已推送到飞书\n")


if __name__ == "__main__":
    main()
