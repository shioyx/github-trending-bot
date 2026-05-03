"""
GitHub Trending Bot v3 — 飞书终极版
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
问题修复：
  ✅ GitHub 日榜不足20条 → 自动补充周榜/月榜（三层合并策略）
  ✅ AI 中文摘要不显示 → 极简 Batch prompt + 多重 JSON 提取
  ✅ 内容太浅 → 每个项目生成：一句话摘要 + 上榜原因 + 技术标签

新能力：
  ★ 每个项目展示：中文摘要 · 爆火原因 · 适合人群 · 分类标签
  ★ 今日/本周/本月来源标注，清楚知道热度时间跨度
  ★ 爆发项目 🔥 高亮（今日新增 > 500 ⭐）
  ★ 一键跳转按钮
  ★ 每日统计（各领域分布 + 爆发项目）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import os, sys, time, json, re, requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from google import genai

# ══════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════
TOP_N          = 20
BATCH_SIZE     = 10           # 每张飞书卡片条数
CARD_COLOR     = "red"
GEMINI_MODEL   = "gemini-2.5-flash"
TIMEOUT        = 20
SPIKE_MIN      = 500          # 今日新增超过此数 → 🔥爆发

CATEGORY_MAP = {
    "AI / ML":    {"python", "jupyter notebook", "r", "cuda", "c++"},
    "前端 / Web": {"javascript", "typescript", "html", "css", "svelte", "vue"},
    "系统 / 底层":{"rust", "zig", "go", "c", "assembly"},
    "移动端":     {"swift", "kotlin", "dart"},
    "DevOps":     {"shell", "dockerfile", "hcl", "makefile"},
    "数据 / DB":  {"sql", "plpgsql", "scala"},
}
CAT_EMOJI = {
    "AI / ML":"🤖", "前端 / Web":"🌐", "系统 / 底层":"⚙️",
    "移动端":"📱", "DevOps":"🐳", "数据 / DB":"🗄️", "其他":"📦"
}
PERIOD_LABEL = {"daily":"今日🔥", "weekly":"本周📈", "monthly":"本月⭐"}

# ══════════════════════════════════════════════════
# Step 1: 三层合并抓取（daily → weekly → monthly 补足20条）
# ══════════════════════════════════════════════════
def _fetch_one_period(session, since: str) -> list:
    """抓取单个时段的 trending 页面。"""
    url = f"https://github.com/trending?since={since}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    resp = session.get(url, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    articles = soup.select("article.Box-row")

    repos = []
    for art in articles:
        h2 = art.select_one("h2 a")
        if not h2:
            continue
        full_name = h2.get("href", "").strip("/")
        if not full_name or "/" not in full_name:
            continue

        desc_el     = art.select_one("p")
        description = desc_el.get_text(strip=True) if desc_el else ""

        lang_el  = art.select_one("span[itemprop='programmingLanguage']")
        language = lang_el.get_text(strip=True) if lang_el else ""

        links       = art.select("a.Link--muted")
        total_stars = links[0].get_text(strip=True) if links else "—"
        forks       = links[1].get_text(strip=True) if len(links) > 1 else "—"

        today_el    = art.select_one("span.d-inline-block.float-sm-right")
        stars_today = today_el.get_text(strip=True) if today_el else "—"

        m = re.search(r"[\d,]+", stars_today)
        stars_num = int(m.group().replace(",", "")) if m else 0

        lang_lower = language.lower()
        category   = "其他"
        for cat, langs in CATEGORY_MAP.items():
            if lang_lower in langs:
                category = cat
                break
        if category == "其他":
            kws = ["llm","ai","gpt","model","neural","machine learning","agent","openai","claude","deepseek"]
            if any(k in description.lower() for k in kws):
                category = "AI / ML"

        repos.append({
            "name":           full_name,
            "url":            f"https://github.com/{full_name}",
            "description":    description,
            "language":       language,
            "category":       category,
            "total_stars":    total_stars,
            "forks":          forks,
            "stars_today":    stars_today,
            "stars_today_num": stars_num,
            "period":         since,
        })
    return repos


def fetch_trending_merged(limit: int = TOP_N) -> list:
    """
    三层合并：daily → weekly → monthly，去重后取前 limit 条。
    每条标记来源 period，推送时展示热度时间跨度。
    """
    session = requests.Session()
    # 先访问主页取 cookie，避免部分请求被拒
    try:
        session.get("https://github.com", timeout=10,
                    headers={"User-Agent": "Mozilla/5.0"})
    except Exception:
        pass

    seen  = {}   # name -> repo dict，保留第一次出现（daily 优先）
    order = []   # 保持插入顺序

    for since in ["daily", "weekly", "monthly"]:
        if len(seen) >= limit:
            break
        try:
            batch = _fetch_one_period(session, since)
            print(f"   {PERIOD_LABEL[since]}: 抓到 {len(batch)} 条")
            for r in batch:
                if r["name"] not in seen:
                    seen[r["name"]] = r
                    order.append(r["name"])
        except Exception as e:
            print(f"   ⚠️  {since} 抓取失败: {e}")

    result = [seen[n] for n in order[:limit]]
    # 重新编排排名（合并后的全局排名）
    for i, r in enumerate(result, 1):
        r["rank"] = i
        r["is_spike"] = r["stars_today_num"] >= SPIKE_MIN
    return result


# ══════════════════════════════════════════════════
# Step 2: Batch AI 富内容生成（summary + why_hot + tags）
# ══════════════════════════════════════════════════
_BATCH_PROMPT_TPL = """你是一名资深技术分析师，熟悉全球开源社区动态。

请对以下 {n} 个 GitHub 项目，各生成三个字段：
1. summary   : 一句中文介绍（≤25字，说明核心功能/用途）
2. why_hot   : 为何近期在 GitHub 上爆火的原因分析（≤35字，结合技术趋势、行业需求、项目特点）
3. tags      : 2~3个中文技术标签（精简，如"AI Agent · 金融分析"，用" · "分隔）

输入数据：
{data}

⚠️ 严格只输出合法 JSON 数组，不要任何解释、代码块或 markdown：
[{{"id":1,"summary":"...","why_hot":"...","tags":"..."}},...,{{"id":{n},...}}]"""


def batch_ai_enrich(client, repos: list) -> list:
    """
    一次 API 调用为所有仓库生成三字段富内容。
    失败时逐条降级，最终兜底用英文截断。
    """
    items = [
        {
            "id":   r["rank"],
            "name": r["name"],
            "desc": r["description"] or "No description provided.",
            "lang": r["language"] or "Unknown",
            "stars_today": r["stars_today"],
        }
        for r in repos
    ]
    prompt = _BATCH_PROMPT_TPL.format(
        n=len(items),
        data=json.dumps(items, ensure_ascii=False, indent=None)
    )

    ai_map = {}
    for attempt in range(1, 4):
        try:
            resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            raw  = resp.text.strip()

            # 多重提取策略：优先完整数组，再尝试 findall 拼接
            match = re.search(r"\[[\s\S]*\]", raw)
            if match:
                data = json.loads(match.group())
            else:
                # 兜底：逐个对象提取再拼
                objs = re.findall(r'\{[^{}]+\}', raw)
                data = [json.loads(o) for o in objs]

            for item in data:
                ai_map[int(item["id"])] = {
                    "summary": item.get("summary", ""),
                    "why_hot": item.get("why_hot", ""),
                    "tags":    item.get("tags", ""),
                }
            print(f"  ✅ Batch AI 完成（{len(ai_map)}/{len(repos)} 条，1次API调用）")
            break

        except Exception as exc:
            wait = 2 ** attempt
            print(f"  [Batch重试 {attempt}/3] {exc}，等{wait}s…")
            time.sleep(wait)

    # 写回 repos
    for r in repos:
        ai = ai_map.get(r["rank"], {})
        r["ai_summary"] = ai.get("summary") or _simple_fallback(client, r)
        r["ai_why_hot"] = ai.get("why_hot") or "暂无分析"
        r["ai_tags"]    = ai.get("tags")    or r["category"]
    return repos


def _simple_fallback(client, r: dict) -> str:
    """单条简单摘要兜底（仅在 Batch 彻底失败时调用）。"""
    if not r["description"]:
        return "暂无描述"
    prompt = (
        f"用不超过25字的中文介绍这个GitHub项目的核心用途，只输出中文：\n"
        f"项目：{r['name']}\n描述：{r['description']}"
    )
    try:
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return resp.text.strip()[:40]
    except Exception:
        return r["description"][:40] + "…"


# ══════════════════════════════════════════════════
# Step 3: 飞书卡片构建（终极版格式）
# ══════════════════════════════════════════════════
def _repo_block(r: dict) -> str:
    """
    每条仓库的飞书 Markdown 块，包含4行富内容：
      行1: 排名 + 超链接 + 语言 + 爆发标记 + 来源时段
      行2: 📌 中文摘要
      行3: 💡 爆火原因
      行4: 🏷️ 技术标签   ⭐总星  🚀今日  🍴Fork
    """
    spike  = " 🔥**爆发**" if r["is_spike"] else ""
    lang   = f" `{r['language']}`" if r["language"] else ""
    period = PERIOD_LABEL.get(r["period"], "")
    cat_e  = CAT_EMOJI.get(r["category"], "📦")

    line1 = f"**{r['rank']}. [{r['name']}]({r['url']})**{lang}{spike}  _{period}_"
    line2 = f"📌 {r['ai_summary']}"
    line3 = f"💡 {r['ai_why_hot']}"
    line4 = (
        f"{cat_e} {r['ai_tags']}"
        f"  |  ⭐ {r['total_stars']}  🚀 今日 **{r['stars_today']}**  🍴 {r['forks']}"
    )
    return "\n".join([line1, line2, line3, line4])


def build_card(repos: list, date_str: str, part: int, total: int) -> dict:
    part_label  = f"（{part}/{total}）" if total > 1 else ""
    header_text = f"🔥 GitHub 热榜 {date_str} · Top {TOP_N} {part_label}"

    elements = []
    for i, r in enumerate(repos):
        elements.append({"tag": "markdown", "content": _repo_block(r)})
        if i < len(repos) - 1:
            elements.append({"tag": "hr"})

    # 快捷按钮（前5条）
    buttons = [
        {
            "tag":  "button",
            "text": {"tag": "plain_text", "content": f"#{r['rank']} {r['name'].split('/')[-1]}"},
            "url":  r["url"],
            "type": "default",
        }
        for r in repos[:5]
    ]
    elements += [
        {"tag": "hr"},
        {"tag": "action", "actions": buttons},
        {
            "tag": "note",
            "elements": [{
                "tag":     "plain_text",
                "content": (
                    f"每日 08:00 自动推送 · 数据: github.com/trending"
                    f" · AI: {GEMINI_MODEL} · 🔥爆发=今日新增>{SPIKE_MIN}⭐"
                    f" · 🔥今日榜 / 📈本周榜 / ⭐本月榜"
                ),
            }],
        },
    ]

    return {
        "msg_type": "interactive",
        "card": {
            "config":  {"wide_screen_mode": True},
            "header":  {
                "title":    {"tag": "plain_text", "content": header_text},
                "template": CARD_COLOR,
            },
            "elements": elements,
        },
    }


# ══════════════════════════════════════════════════
# Step 4: 推送 & 统计
# ══════════════════════════════════════════════════
def send_feishu(webhook_url: str, payload: dict, label: str) -> None:
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
    print(f"  ✅ {label} 推送成功")


def push_all(webhook_url: str, repos: list, date_str: str) -> None:
    batches = [repos[i:i + BATCH_SIZE] for i in range(0, len(repos), BATCH_SIZE)]
    total   = len(batches)
    for idx, batch in enumerate(batches, 1):
        label   = f"第{idx}/{total}张（#{batch[0]['rank']}–#{batch[-1]['rank']}）"
        payload = build_card(batch, date_str, idx, total)
        print(f"📲 推送 {label}…")
        send_feishu(webhook_url, payload, label)
        if idx < total:
            time.sleep(1.5)


def print_stats(repos: list, date_str: str) -> None:
    cats   = {}
    spikes = []
    daily_count = sum(1 for r in repos if r["period"] == "daily")
    for r in repos:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
        if r["is_spike"]:
            spikes.append(r)

    print(f"\n{'═'*52}")
    print(f"  📊 {date_str} 热榜统计")
    print(f"  📅 今日榜 {daily_count} · 补充周/月榜 {len(repos)-daily_count}")
    print(f"{'─'*52}")
    for cat, cnt in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {CAT_EMOJI.get(cat,'📦')} {cat}: {cnt} 个")
    if spikes:
        print(f"\n  🔥 爆发项目（今日 >{SPIKE_MIN}⭐）:")
        for r in spikes:
            print(f"     #{r['rank']} {r['name']} → {r['stars_today']}")
    print(f"{'═'*52}\n")


# ══════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════
def main() -> None:
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL", "").strip()
    gemini_key  = os.environ.get("GEMINI_API_KEY", "").strip()
    if not webhook_url:
        sys.exit("❌ 缺少: FEISHU_WEBHOOK_URL")
    if not gemini_key:
        sys.exit("❌ 缺少: GEMINI_API_KEY")

    bj_now   = datetime.now(timezone(timedelta(hours=8)))
    date_str = bj_now.strftime("%Y-%m-%d")
    print(f"🗓  {date_str}  GitHub Trending Bot v3 启动")

    # 1. 三层合并抓取
    print(f"\n📡 三层合并抓取（daily → weekly → monthly，目标 {TOP_N} 条）…")
    repos = fetch_trending_merged(limit=TOP_N)
    if not repos:
        sys.exit("❌ 未获取到任何仓库")
    print(f"   ✅ 合并后共 {len(repos)} 条")

    # 2. Batch AI 富内容（1次调用）
    print(f"\n🤖 Batch AI 富内容生成（{GEMINI_MODEL}，1次调用）…")
    client = genai.Client(api_key=gemini_key)
    repos  = batch_ai_enrich(client, repos)

    # 3. 打印统计
    print_stats(repos, date_str)

    # 4. 推送飞书
    print("📨 推送飞书卡片…")
    push_all(webhook_url, repos, date_str)
    print(f"\n🎉 完成！{len(repos)} 个项目 → 飞书\n")


if __name__ == "__main__":
    main()
