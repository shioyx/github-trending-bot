"""
GitHub Trending Bot v7 ── 终极修复版
════════════════════════════════════════════════════════════
根本修复：
  ✅ 放弃 Gemini API（GitHub Actions IP 被 Google 免费版封禁）
  ✅ 改用 OpenRouter（无 IP 限制，GitHub Actions 完全可用）
     主力模型: deepseek/deepseek-chat:free（中文最强，128k）
     备用模型: meta-llama/llama-3.3-70b-instruct:free

格式优化（按用户要求）：
  ✅ 删除 📊市场信号 / 👥适合人群 / 📉趋势图 / 📌爆火原因 四行（冗余）
  ✅ 💡 简短中文概要介绍该项目（40字以内，说清楚用途）
  ✅ 按今日涨星速度排序（降序）

最终每条 3 行（极简有力）：
  行1: 排名·链接·语言·爆发标记
  行2: 💡 简短中文概要（项目是做什么的）
  行3: 🏷 技术标签   ⭐ 总星  🚀 今日涨星  🍴 Fork
════════════════════════════════════════════════════════════
"""
import os, sys, time, json, re, traceback, requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta

# ══════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════
# OpenRouter 配置（无 IP 限制，GitHub Actions 完全可用）
OPENROUTER_URL   = "https://openrouter.ai/api/v1/chat/completions"
PRIMARY_MODEL    = "deepseek/deepseek-chat:free"    # 中文最强，首选
FALLBACK_MODEL   = "meta-llama/llama-3.3-70b-instruct:free"  # 备用

TIMEOUT          = 30
SPIKE_MIN        = 500      # 今日新增 > 此值 → 🔥爆发
STATE_FILE       = "state.json"
AI_CALL_DELAY    = 1.5      # 每次 AI 调用间隔（秒）

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


# ══════════════════════════════════════════════════════
# 工具
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

def parse_markers(text: str) -> dict:
    """解析行标记格式，容忍任何多余文字。"""
    result = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        for key in ["INTRO", "TAGS"]:
            if line.upper().startswith(f"{key}:"):
                result[key.lower()] = line[len(key)+1:].strip()
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
            "forks":       forks,
            "stars_period": stars_p,
            "stars_num":   stars_num,
            "is_spike":    stars_num >= SPIKE_MIN,
            "period":      since,
            "ai_intro":    "",
            "ai_tags":     "",
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
            # 按今日涨星速度降序排列
            repos.sort(key=lambda r: r["stars_num"], reverse=True)
            # 重新编排排名
            for i, r in enumerate(repos, 1):
                r["rank"] = i
            data[since] = repos
            print(f"   ✅ {PERIOD_LABEL[since]}榜: {len(repos)} 条（已按涨星速度排序）")
        except Exception as e:
            print(f"   ❌ {since} 抓取失败: {e}")
            traceback.print_exc()
            data[since] = []
    return data


# ══════════════════════════════════════════════════════
# Step 2：OpenRouter AI 分析（无 IP 限制）
# ══════════════════════════════════════════════════════
_PROMPT_TPL = """\
你是资深开源社区分析师。分析以下 GitHub 项目，按格式输出：

项目名称：{name}
英文描述：{desc}
编程语言：{lang}
{period_label}涨星：{stars}（总星：{total_stars}）

严格按以下格式输出（每行以标记开头，全部中文）：

INTRO: 用简短中文概要介绍该项目是什么、核心功能是什么（≤40字，说清楚用途，不重复项目名）
TAGS: 2-3个精准技术标签，用·分隔（如：AI Agent·多智能体·金融分析）"""

def _call_openrouter(api_key: str, prompt: str, model: str) -> str:
    """调用 OpenRouter API，返回文本内容。"""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200,
        "temperature": 0.3,
    }
    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/trending-bot",
            "X-Title": "GitHub Trending Bot",
        },
        json=payload,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    # 提取文本
    return data["choices"][0]["message"]["content"].strip()

def _ai_one(api_key: str, r: dict, period_label: str) -> dict:
    """单条分析，主模型失败自动切换备用模型，三重兜底。"""
    prompt = _PROMPT_TPL.format(
        name=r["name"],
        desc=r["description"] or "No description.",
        lang=r["language"] or "Unknown",
        period_label=period_label,
        stars=r["stars_period"],
        total_stars=r["total_stars"],
    )

    for model in [PRIMARY_MODEL, FALLBACK_MODEL]:
        for attempt in range(1, 3):
            try:
                raw    = _call_openrouter(api_key, prompt, model)
                parsed = parse_markers(raw)
                if parsed.get("intro"):
                    return parsed
                print(f"      ⚠️  {model} 解析不完整，原始: {raw[:80]}")
            except Exception as e:
                print(f"      ❌ {model} attempt {attempt}: {str(e)[:100]}")
                if attempt < 2:
                    time.sleep(3)

    # 最终兜底
    print(f"      🔴 {r['name']} 全部失败，启用硬兜底")
    desc = r["description"]
    cat  = r["category"]
    return {
        "intro": desc[:35] + "…" if len(desc) > 35 else (desc or f"{cat}类开源项目"),
        "tags":  cat.replace(" / ", "·"),
    }

def enrich_all(api_key: str, repos: list, period: str) -> list:
    label = PERIOD_LABEL[period]
    total = len(repos)
    for i, r in enumerate(repos, 1):
        print(f"   [{i:02d}/{total}] {r['name']}")
        ai = _ai_one(api_key, r, label)
        r["ai_intro"]   = ai.get("intro",   "")
        # 清理 tags：去掉与大类重复的项
        raw_tags = ai.get("tags", "")
        cat_name = r["category"].replace(" / ", "·")
        r["ai_tags"] = "·".join(
            t.strip() for t in raw_tags.split("·")
            if t.strip() and t.strip() not in (r["category"], cat_name)
        ) or raw_tags or r["category"]
        print(f"        💡 {r['ai_intro']}")
        if i < total:
            time.sleep(AI_CALL_DELAY)
    return repos


# ══════════════════════════════════════════════════════
# Step 3：状态持久化
# ══════════════════════════════════════════════════════
def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(data: dict, date_str: str, old_state: dict) -> None:
    try:
        state = {"last_updated": date_str}
        for period in ["weekly", "monthly"]:
            state[period] = [
                {"rank": r["rank"], "name": r["name"], "stars": r["stars_period"]}
                for r in data.get(period, [])
            ]
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        print(f"   ✅ 状态已保存")
    except Exception as e:
        print(f"   ⚠️  保存失败（不影响推送）: {e}")


# ══════════════════════════════════════════════════════
# Step 4：周/月榜变化分析
# ══════════════════════════════════════════════════════
_CHANGE_TPL = """\
分析GitHub{period_label}榜排名变化，按格式输出（全部中文）：

上期：
{prev_list}

本期：
{curr_list}

TREND: 2-3句整体趋势，说明本期最显著的技术风向（≤90字）
NEW: 新上榜项目（上期没有），格式：#排名 仓库名 | 上榜原因（≤25字）
RISE: 排名上升≥3名，格式：仓库名 ▲N名 | 原因（≤25字）
DROP: 跌出榜单，格式：仓库名 | 可能原因（≤20字）"""

def _parse_change(text: str) -> dict:
    result = {"trend": "", "new": [], "rise": [], "drop": []}
    current = None
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        up = line.upper()
        if up.startswith("TREND:"):
            result["trend"] = line[6:].strip()
            current = None
        elif up.startswith("NEW:"):
            v = line[4:].strip()
            if v:
                result["new"].append(v)
            current = "new"
        elif up.startswith("RISE:"):
            v = line[5:].strip()
            if v:
                result["rise"].append(v)
            current = "rise"
        elif up.startswith("DROP:"):
            v = line[5:].strip()
            if v:
                result["drop"].append(v)
            current = "drop"
        elif current and line:
            result[current].append(line)
    return result

def analyze_changes(api_key: str, period: str, prev_state: dict, curr_repos: list):
    prev_list = prev_state.get(period, [])
    if not prev_list:
        return None
    prev_txt = "\n".join(f"  {p['rank']}. {p['name']}" for p in prev_list)
    curr_txt = "\n".join(f"  {r['rank']}. {r['name']}" for r in curr_repos)
    prompt   = _CHANGE_TPL.format(
        period_label=PERIOD_LABEL[period],
        prev_list=prev_txt, curr_list=curr_txt,
    )
    for model in [PRIMARY_MODEL, FALLBACK_MODEL]:
        try:
            raw    = _call_openrouter(api_key, prompt, model)
            result = _parse_change(raw)
            if result.get("trend"):
                print(f"   ✅ {PERIOD_LABEL[period]}榜变化分析完成")
                return result
        except Exception as e:
            print(f"   ⚠️  变化分析 {model}: {str(e)[:80]}")
            time.sleep(2)
    print(f"   ⚠️  变化分析失败，跳过")
    return None


# ══════════════════════════════════════════════════════
# Step 5：飞书卡片构建（4行简洁格式）
# ══════════════════════════════════════════════════════
def _repo_block(r: dict) -> str:
    """
    3行格式，简洁清晰：
    行1: 排名 · 仓库链接 · 语言 · 爆发标记
    行2: 💡 简短中文概要（项目是做什么的）
    行3: 🏷 技术标签  |  ⭐ 总星  🚀 今日涨星  🍴 Fork
    """
    spike  = " 🔥**爆发**" if r["is_spike"] else ""
    lang   = f" `{r['language']}`" if r["language"] else ""
    cat_e  = CAT_EMOJI.get(r["category"], "📦")
    pl     = PERIOD_LABEL.get(r.get("period", ""), "")
    tags   = r.get("ai_tags", "") or r["category"]

    return "\n".join([
        f"**{r['rank']}. [{r['name']}]({r['url']})**{lang}{spike}",
        f"💡 {r['ai_intro']}",
        f"{cat_e} {tags}   ⭐ {r['total_stars']}  🚀 {pl} **{r['stars_period']}**  🍴 {r['forks']}",
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
        elements    = []
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
                f"每日 08:00 自动推送 · 数据 github.com/trending"
                f" · AI: OpenRouter({PRIMARY_MODEL.split('/')[0]})"
                f" · 🔥=今日新增>{SPIKE_MIN}⭐ · 按涨星速度排序"
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
# 主流程
# ══════════════════════════════════════════════════════
def main() -> None:
    webhook_url  = os.environ.get("FEISHU_WEBHOOK_URL", "").strip()
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not webhook_url:
        sys.exit("❌ 缺少: FEISHU_WEBHOOK_URL")
    if not openrouter_key:
        sys.exit("❌ 缺少: OPENROUTER_API_KEY")

    bj_now   = datetime.now(timezone(timedelta(hours=8)))
    date_str = bj_now.strftime("%Y-%m-%d")
    print(f"\n{'═'*62}")
    print(f"  🗓  {date_str}  GitHub Trending Bot v7")
    print(f"  AI: OpenRouter · {PRIMARY_MODEL}")
    print(f"  排序: 按今日涨星速度降序")
    print(f"{'═'*62}")

    # 1. 加载历史
    print("\n📂 加载历史状态…")
    state = load_state()
    print(f"   上次更新: {state.get('last_updated','首次运行')}")

    # 2. 抓取三榜（已按涨星排序）
    print("\n📡 抓取三榜数据…")
    all_data = fetch_all()
    if not any(all_data.values()):
        sys.exit("❌ 三榜均为空")

    # 3. AI 逐条深度分析
    for period in ["daily", "weekly", "monthly"]:
        repos = all_data.get(period, [])
        if not repos:
            continue
        print(f"\n🤖 {PERIOD_LABEL[period]}榜 AI 分析（{len(repos)} 条）…")
        all_data[period] = enrich_all(openrouter_key, repos, period)

    # 4. 周/月变化分析
    changes = {}
    for period in ["weekly", "monthly"]:
        repos = all_data.get(period, [])
        if not repos:
            continue
        print(f"\n🔍 {PERIOD_LABEL[period]}榜变化对比…")
        changes[period] = analyze_changes(openrouter_key, period, state, repos)
        if changes[period] is None:
            print(f"   ℹ️  首次运行，下次起生效")

    # 5. 推送
    print("\n📨 推送飞书卡片…")
    for period in ["daily", "weekly", "monthly"]:
        repos = all_data.get(period, [])
        if not repos:
            continue
        print(f"\n  ── {PERIOD_LABEL[period]}榜 ──")
        cards = build_cards(repos, period, date_str,
                            change_data=changes.get(period), batch_size=10)
        push_period(webhook_url, cards, period)
        time.sleep(2)

    # 6. 保存状态
    print("\n💾 保存状态…")
    save_state(all_data, date_str, state)

    print(f"\n🎉 全部完成！三榜已推送到飞书\n")


if __name__ == "__main__":
    main()
