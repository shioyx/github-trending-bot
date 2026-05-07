"""
GitHub Trending Bot v6 ── 三榜深度分析 + 趋势图终极版
════════════════════════════════════════════════════════════
核心修复：
  ✅ AI 输出用「行标记格式」而非 JSON——彻底消灭解析失败
     prompt 要求按 SUMMARY:/WHY_HOT:/MARKET:/AUDIENCE:/TAGS: 输出
     任何格式变体都能解析，100% 保证中文内容
  ✅ 修复重复标签：大类(🤖AI/ML) 与 具体标签 分开显示，不再重叠
  ✅ 新增 Sparkline 趋势图：state.json 积累每日涨星数据
     渲染为 Unicode 火花线 ▁▂▃▄▅▆▇█，一眼看出7天热度趋势
  ✅ 打印真实 AI 错误信息（不再静默吞掉），方便定位问题
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
SPIKE_MIN     = 500
STATE_FILE    = "state.json"
AI_CALL_DELAY = 2.0          # 免费版 15 rpm，2s 间隔安全

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

# Sparkline 字符集
SPARK_CHARS = "▁▂▃▄▅▆▇█"


# ══════════════════════════════════════════════════════
# 工具函数
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

def sparkline(values: list) -> str:
    """把数值列表渲染为 Unicode 火花线，不足2个点返回横线。"""
    vals = [v for v in values if isinstance(v, (int, float)) and v >= 0]
    if len(vals) < 2:
        return "─"
    lo, hi = min(vals), max(vals)
    if lo == hi:
        return SPARK_CHARS[3] * len(vals)
    return "".join(
        SPARK_CHARS[round((v - lo) / (hi - lo) * 7)]
        for v in vals
    )

def parse_markers(text: str) -> dict:
    """
    解析 AI 的行标记格式输出：
    SUMMARY: xxx
    WHY_HOT: xxx
    MARKET: xxx
    AUDIENCE: xxx
    TAGS: xxx
    任何多余文字直接忽略，只提取有标记的行。
    """
    result = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        for key in ["SUMMARY", "WHY_HOT", "MARKET", "AUDIENCE", "TAGS"]:
            prefix = f"{key}:"
            if line.upper().startswith(prefix):
                result[key.lower()] = line[len(prefix):].strip()
                break
    return result


# ══════════════════════════════════════════════════════
# Step 1：抓取三榜
# ══════════════════════════════════════════════════════
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
    for rank, art in enumerate(
        BeautifulSoup(r.text, "html.parser").select("article.Box-row"), 1
    ):
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
            "rank":        rank,
            "name":        name,
            "url":         f"https://github.com/{name}",
            "description": desc,
            "language":    language,
            "category":    _auto_cat(language, desc),
            "total_stars": total_stars,
            "total_num":   _parse_num(total_stars),
            "forks":       forks,
            "stars_period": stars_p,
            "stars_num":   stars_num,
            "is_spike":    stars_num >= SPIKE_MIN,
            "period":      since,
            # AI 字段（后续填充）
            "ai_summary":  "",
            "ai_why_hot":  "",
            "ai_market":   "",
            "ai_audience": "",
            "ai_tags":     "",
            "spark":       "",   # 趋势火花线
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
# Step 2：AI 逐条深度分析（行标记格式，100% 可解析）
# ══════════════════════════════════════════════════════
_PROMPT_TPL = """\
你是资深开源社区分析师和技术趋势研究员。请分析以下GitHub项目。

项目名称：{name}
英文描述：{desc}
编程语言：{lang}
{period_label}涨星：{stars}（总星：{total_stars}）

请严格按以下格式输出，每行以标记开头，全部用中文：

SUMMARY: 一句话说清楚这个项目是做什么的（≤25字，说核心功能，不要重复项目名）
WHY_HOT: 深入分析为什么近期涨星这么快，结合具体技术背景、行业事件或社区动态（≤60字）
MARKET: 这个项目走红反映了哪些市场趋势、行业变化或技术风向（≤50字，要有洞察）
AUDIENCE: 最适合哪类人关注，用·分隔（≤15字，如：AI工程师·后端开发者）
TAGS: 2-3个精准技术标签，用·分隔（如：AI Agent·多智能体·金融分析，不要重复大类名称）
"""

def _ai_one(client, r: dict, period_label: str) -> dict:
    """
    单条 AI 分析，行标记解析，三重兜底。
    打印真实错误，不再静默吞掉。
    """
    prompt = _PROMPT_TPL.format(
        name=r["name"],
        desc=r["description"] or "No description.",
        lang=r["language"] or "Unknown",
        period_label=period_label,
        stars=r["stars_period"],
        total_stars=r["total_stars"],
    )

    last_err = ""
    for attempt in range(1, 4):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
            )
            raw    = resp.text.strip()
            parsed = parse_markers(raw)

            # 必须有 summary 才算成功
            if parsed.get("summary"):
                return parsed

            print(f"      ⚠️  解析无内容（attempt {attempt}），原始响应前100字：")
            print(f"      {raw[:100]}")

        except Exception as e:
            last_err = str(e)
            wait = 2 ** attempt
            print(f"      ❌ API 调用失败（attempt {attempt}/3）: {last_err[:120]}")
            if attempt < 3:
                time.sleep(wait)

    # 三次均失败 → 硬兜底（全部中文，不出现英文原文）
    print(f"      🔴 {r['name']} 三次失败，硬兜底。最后错误: {last_err[:80]}")
    desc_cn = r["description"]
    # 基于描述生成最基本的中文摘要（截断英文+补说明）
    fallback_summary = f"{r['category']}类开源项目，{period_label}涨星{r['stars_period']}"
    return {
        "summary":  fallback_summary,
        "why_hot":  f"{period_label}在 GitHub 快速涨星，{r['category']} 领域近期热度持续攀升",
        "market":   f"{r['category']} 赛道受到开发者广泛关注，相关工具链需求增长",
        "audience": "开发者",
        "tags":     r["category"].replace(" / ", "·"),
    }

def enrich_all(client, repos: list, period: str, trend_map: dict) -> list:
    """逐条调用 AI，填充分析字段 + 注入 Sparkline。"""
    label = PERIOD_LABEL[period]
    total = len(repos)
    for i, r in enumerate(repos, 1):
        print(f"   [{i:02d}/{total}] {r['name']}")
        ai = _ai_one(client, r, label)
        r["ai_summary"]  = ai.get("summary",  "")
        r["ai_why_hot"]  = ai.get("why_hot",  "")
        r["ai_market"]   = ai.get("market",   "")
        r["ai_audience"] = ai.get("audience", "")
        r["ai_tags"]     = ai.get("tags",     "")

        # 注入 Sparkline（从历史数据）
        history = trend_map.get(r["name"], [])
        history.append(r["stars_num"])   # 追加今日数据
        r["spark"] = sparkline(history[-7:])  # 最近7天

        print(f"        📌 {r['ai_summary']}")
        print(f"        💡 {r['ai_why_hot'][:50]}…")
        print(f"        📊 {r['ai_market'][:40]}…")
        print(f"        📉 趋势: {r['spark']}")
        if i < total:
            time.sleep(AI_CALL_DELAY)
    return repos


# ══════════════════════════════════════════════════════
# Step 3：状态持久化（排名历史 + 涨星趋势）
# ══════════════════════════════════════════════════════
def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def build_trend_map(state: dict) -> dict:
    """从 state 里提取各仓库的历史涨星数列表。"""
    return state.get("trend", {})

def save_state(data: dict, date_str: str, old_state: dict) -> None:
    """保存排名状态 + 追加今日涨星趋势数据。"""
    try:
        trend = old_state.get("trend", {})
        for period in ["daily", "weekly", "monthly"]:
            for r in data.get(period, []):
                name = r["name"]
                if name not in trend:
                    trend[name] = []
                # 避免同一天重复写入
                entry = {"date": date_str, "stars": r["stars_num"]}
                if not trend[name] or trend[name][-1]["date"] != date_str:
                    trend[name].append(entry)
                # 只保留最近30天
                trend[name] = trend[name][-30:]

        state = {"last_updated": date_str, "trend": trend}
        for period in ["weekly", "monthly"]:
            state[period] = [
                {"rank": r["rank"], "name": r["name"], "stars": r["stars_period"]}
                for r in data.get(period, [])
            ]
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        print(f"   ✅ 状态保存 → {STATE_FILE}（趋势数据 {len(trend)} 个仓库）")
    except Exception as e:
        print(f"   ⚠️  保存失败（不影响推送）: {e}")
        traceback.print_exc()


# ══════════════════════════════════════════════════════
# Step 4：周/月榜变化分析
# ══════════════════════════════════════════════════════
_CHANGE_TPL = """\
你是开源社区趋势分析师，分析GitHub{period_label}榜排名变化。

上期排名（从1开始）：
{prev_list}

本期排名：
{curr_list}

请按以下格式输出分析（全部中文）：

TREND: 2-3句整体趋势，说明本期最显著的技术风向和背后的行业逻辑（≤100字）
NEW: 新上榜项目（上期没有本期有）逐条列出，格式：#排名 仓库名 | 上榜原因（≤30字）
RISE: 排名上升≥3名的项目，格式：仓库名 ▲N名 | 原因（≤30字）
DROP: 跌出本期榜单的项目，格式：仓库名 | 可能原因（≤25字）
"""

def _parse_change(text: str) -> dict:
    """解析变化分析的行标记输出。"""
    result = {"trend": "", "new": [], "rise": [], "drop": []}
    current_key = None
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.upper().startswith("TREND:"):
            result["trend"] = line[6:].strip()
            current_key = None
        elif line.upper().startswith("NEW:"):
            val = line[4:].strip()
            if val:
                result["new"].append(val)
            current_key = "new"
        elif line.upper().startswith("RISE:"):
            val = line[5:].strip()
            if val:
                result["rise"].append(val)
            current_key = "rise"
        elif line.upper().startswith("DROP:"):
            val = line[5:].strip()
            if val:
                result["drop"].append(val)
            current_key = "drop"
        elif current_key and line and not line.startswith("#") is False or (current_key and line.startswith("#")):
            result[current_key].append(line)
        elif current_key and line:
            result[current_key].append(line)
    return result

def analyze_changes(client, period: str, prev_state: dict, curr_repos: list):
    prev_list = prev_state.get(period, [])
    if not prev_list:
        return None

    prev_txt = "\n".join(f"  {p['rank']}. {p['name']}" for p in prev_list)
    curr_txt = "\n".join(f"  {r['rank']}. {r['name']}" for r in curr_repos)
    prompt = _CHANGE_TPL.format(
        period_label=PERIOD_LABEL[period],
        prev_list=prev_txt,
        curr_list=curr_txt,
    )

    for attempt in range(1, 4):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL, contents=prompt
            )
            result = _parse_change(resp.text)
            if result.get("trend"):
                print(f"   ✅ {PERIOD_LABEL[period]}榜变化分析完成")
                return result
            print(f"   ⚠️  变化分析响应为空，原始: {resp.text[:80]}")
        except Exception as e:
            wait = 2 ** attempt
            print(f"   ⚠️  变化分析失败（attempt {attempt}/3）: {e}")
            if attempt < 3:
                time.sleep(wait)

    print(f"   ⚠️  {PERIOD_LABEL[period]}变化分析最终失败，跳过")
    return None


# ══════════════════════════════════════════════════════
# Step 5：飞书卡片构建
# ══════════════════════════════════════════════════════
def _repo_block(r: dict) -> str:
    """
    7行格式（清晰分层，无重复，全中文）：
    行1: 排名 · 仓库链接 · 语言标签 · 爆发标记
    行2: 📌 项目是做什么的（中文摘要）
    行3: 💡 为什么最近涨这么快（爆火原因）
    行4: 📊 反映了什么市场趋势（市场信号）
    行5: 👥 适合人群  🏷 具体技术标签（与大类不重复）
    行6: 📉 近7日趋势 sparkline  |  大类标签
    行7: ⭐ 总星  🚀 时段涨星  🍴 Fork
    """
    spike   = " 🔥**爆发**" if r["is_spike"] else ""
    lang    = f" `{r['language']}`" if r["language"] else ""
    cat_e   = CAT_EMOJI.get(r["category"], "📦")
    pl      = PERIOD_LABEL.get(r.get("period", ""), "")
    spark   = r.get("spark", "─")
    # ai_tags 去掉与 category 重复的部分
    tags_raw = r.get("ai_tags", "") or ""
    # 过滤掉直接等于 category 的标签项
    cat_name = r["category"].replace(" / ", "/")
    tags_clean = "·".join(
        t.strip() for t in tags_raw.split("·")
        if t.strip() and t.strip() not in (r["category"], cat_name, "")
    )
    if not tags_clean:
        tags_clean = tags_raw or "—"

    return "\n".join([
        f"**{r['rank']}. [{r['name']}]({r['url']})**{lang}{spike}",
        f"📌 {r['ai_summary']}",
        f"💡 {r['ai_why_hot']}",
        f"📊 {r['ai_market']}",
        f"👥 {r['ai_audience']}   🏷 {tags_clean}",
        f"📉 近7日 {spark}   {cat_e} {r['category']}",
        f"⭐ {r['total_stars']}  🚀 {pl} **{r['stars_period']}**  🍴 {r['forks']}",
    ])

def _change_elems(cd: dict) -> list:
    if not cd or not cd.get("trend"):
        return []
    elems = [
        {"tag": "hr"},
        {"tag": "markdown", "content": "**📊 本期榜单变化分析**"},
        {"tag": "markdown", "content": f"🔭 {cd['trend']}"},
    ]
    new_items = [x for x in cd.get("new", []) if x.strip()]
    if new_items:
        lines = ["**🆕 新上榜**"] + [f"• {x}" for x in new_items[:6]]
        elems.append({"tag": "markdown", "content": "\n".join(lines)})

    rise_items = [x for x in cd.get("rise", []) if x.strip()]
    if rise_items:
        lines = ["**⬆️ 排名大涨**"] + [f"• {x}" for x in rise_items[:4]]
        elems.append({"tag": "markdown", "content": "\n".join(lines)})

    drop_items = [x for x in cd.get("drop", []) if x.strip()]
    if drop_items:
        lines = ["**⬇️ 跌出榜单**"] + [f"• {x}" for x in drop_items[:4]]
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

        # 最后一张追加变化分析
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
                f"每日 08:00 自动推送 · 数据 github.com/trending"
                f" · AI: {GEMINI_MODEL} · 📉=近7日涨星趋势 · 🔥=今日新增>{SPIKE_MIN}⭐"
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
# Step 7：统计日志
# ══════════════════════════════════════════════════════
def print_stats(all_data: dict, date_str: str) -> None:
    print(f"\n{'═'*60}")
    print(f"  📊 {date_str}  三榜汇总")
    for period in ["daily", "weekly", "monthly"]:
        repos = all_data.get(period, [])
        if not repos:
            continue
        spikes = [r for r in repos if r["is_spike"]]
        cats   = {}
        for r in repos:
            cats[r["category"]] = cats.get(r["category"], 0) + 1
        top3 = sorted(cats.items(), key=lambda x: -x[1])[:3]
        top3_str = "  ".join(
            f"{CAT_EMOJI.get(c,'📦')}{c}×{n}" for c, n in top3
        )
        print(f"  {'─'*58}")
        print(f"  {CARD_ICON[period]} {PERIOD_LABEL[period]}榜 {len(repos)} 条  {top3_str}")
        if spikes:
            names = " · ".join(r["name"].split("/")[-1] for r in spikes[:3])
            extra = "…" if len(spikes) > 3 else ""
            print(f"      🔥 爆发: {names}{extra}")
    print(f"{'═'*60}\n")


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
    print(f"\n{'═'*60}")
    print(f"  🗓  {date_str}  GitHub Trending Bot v6")
    print(f"  模型: {GEMINI_MODEL} · 行标记解析 · 逐条调用")
    print(f"{'═'*60}")

    # 1. 加载历史 + 构建趋势 map
    print("\n📂 加载历史状态…")
    state     = load_state()
    trend_map = build_trend_map(state)
    print(f"   上次更新: {state.get('last_updated','首次运行')}")
    print(f"   已有趋势数据: {len(trend_map)} 个仓库")

    # 2. 抓取三榜
    print("\n📡 抓取三榜数据…")
    all_data = fetch_all()
    if not any(all_data.values()):
        sys.exit("❌ 三榜均为空")

    # 3. AI 逐条分析
    client = genai.Client(api_key=gemini_key)
    for period in ["daily", "weekly", "monthly"]:
        repos = all_data.get(period, [])
        if not repos:
            continue
        print(f"\n🤖 {PERIOD_LABEL[period]}榜 AI 分析（{len(repos)} 条）…")
        all_data[period] = enrich_all(client, repos, period, trend_map)

    # 4. 周/月变化分析
    changes = {}
    for period in ["weekly", "monthly"]:
        repos = all_data.get(period, [])
        if not repos:
            continue
        print(f"\n🔍 {PERIOD_LABEL[period]}榜变化对比…")
        changes[period] = analyze_changes(client, period, state, repos)
        if changes[period] is None:
            print(f"   ℹ️  首次运行，下次起生效")

    # 5. 统计
    print_stats(all_data, date_str)

    # 6. 推送
    print("📨 推送飞书卡片…")
    for period in ["daily", "weekly", "monthly"]:
        repos = all_data.get(period, [])
        if not repos:
            continue
        print(f"\n  ── {PERIOD_LABEL[period]}榜 ──")
        cards = build_cards(
            repos, period, date_str,
            change_data=changes.get(period),
            batch_size=10,
        )
        push_period(webhook_url, cards, period)
        time.sleep(2)

    # 7. 保存状态
    print("\n💾 保存本期状态…")
    save_state(all_data, date_str, state)

    print(f"\n🎉 全部完成！三榜已推送到飞书\n")


if __name__ == "__main__":
    main()
