"""
GitHub Trending Bot — 飞书版
每天自动抓取 GitHub 热榜 Top 20，
用 Claude AI 生成中文摘要，通过飞书群机器人 Webhook 推送卡片消息。
"""

import os
import sys
import time
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
import anthropic

# ── 配置 ──────────────────────────────────────────────
TRENDING_URL    = "https://github.com/trending"
TOP_N           = 20
REQUEST_TIMEOUT = 15
AI_RETRY        = 2     # 摘要失败最大重试次数
DELAY_BETWEEN_AI = 0.3  # 相邻 AI 请求间隔（秒）
BATCH_SIZE      = 10    # 每张飞书卡片展示几条（分 2 张发送，避免超长）
CARD_TEMPLATE   = "red" # 卡片标题颜色: red/wathet/turquoise/green/yellow/orange/carmine/violet/purple/indigo/grey/blue


# ── 数据抓取 ──────────────────────────────────────────
def fetch_trending(since: str = "daily", limit: int = TOP_N) -> list:
    """
    抓取 GitHub Trending 页面，返回最多 limit 条仓库信息。
    since: "daily" | "weekly" | "monthly"
    """
    url = f"{TRENDING_URL}?since={since}"
    headers = {
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (compatible; GithubTrendingBot/1.0; "
            "+https://github.com)"
        ),
    }
    resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    articles = soup.select("article.Box-row")[:limit]

    repos = []
    for rank, article in enumerate(articles, start=1):
        h2 = article.select_one("h2 a")
        if not h2:
            continue
        full_name = h2["href"].strip("/")

        desc_el     = article.select_one("p")
        description = desc_el.get_text(strip=True) if desc_el else ""

        lang_el  = article.select_one("span[itemprop='programmingLanguage']")
        language = lang_el.get_text(strip=True) if lang_el else ""

        link_els    = article.select("a.Link--muted")
        total_stars = link_els[0].get_text(strip=True) if link_els else "—"
        forks       = link_els[1].get_text(strip=True) if len(link_els) > 1 else "—"

        today_el    = article.select_one("span.d-inline-block.float-sm-right")
        stars_today = today_el.get_text(strip=True) if today_el else "—"

        repos.append({
            "rank":        rank,
            "name":        full_name,
            "url":         f"https://github.com/{full_name}",
            "description": description,
            "language":    language,
            "total_stars": total_stars,
            "forks":       forks,
            "stars_today": stars_today,
        })

    return repos


# ── AI 摘要 ───────────────────────────────────────────
def get_ai_summary(client, name, description):
    """调用 Claude 生成不超过 30 字的中文一句话摘要。"""
    if not description:
        return "暂无描述"

    prompt = (
        f"用一句简洁的中文（不超过30字）介绍这个GitHub项目的核心用途。\n"
        f"项目名：{name}\n"
        f"英文描述：{description}\n\n"
        f"只输出中文介绍，不要任何前缀、标点包裹或额外说明。"
    )

    for attempt in range(1, AI_RETRY + 2):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=80,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text.strip()
        except Exception as exc:
            if attempt <= AI_RETRY:
                print(f"  [重试 {attempt}] AI摘要失败: {exc}")
                time.sleep(1)
            else:
                print(f"  [跳过] AI摘要最终失败: {exc}")
                return description[:50] + ("…" if len(description) > 50 else "")


# ── 飞书卡片构建 ──────────────────────────────────────
def _repo_markdown(r):
    """将单条仓库信息渲染为飞书 markdown 文本。"""
    lang_part = f" `{r['language']}`" if r["language"] else ""
    name_link = f"[{r['name']}]({r['url']})"
    lines = [
        f"**{r['rank']}. {name_link}**{lang_part}",
        f"🤖 {r['ai_summary']}",
        f"⭐ {r['total_stars']}  🚀 今日 {r['stars_today']}  🍴 {r['forks']}",
    ]
    return "\n".join(lines)


def build_feishu_card(repos, date_str, part, total_parts):
    """
    构建飞书交互卡片 payload。
    每张卡片包含 BATCH_SIZE 条仓库，若总数超过一张容量则分多张发送。
    """
    part_label  = f" ({part}/{total_parts})" if total_parts > 1 else ""
    header_text = f"🔥 GitHub 热榜 {date_str} · Top {TOP_N}{part_label}"

    elements = []
    for i, r in enumerate(repos):
        elements.append({
            "tag": "markdown",
            "content": _repo_markdown(r),
        })
        if i < len(repos) - 1:
            elements.append({"tag": "hr"})

    elements.append({"tag": "hr"})
    elements.append({
        "tag": "note",
        "elements": [{
            "tag": "plain_text",
            "content": "每日 08:00 自动推送 · 数据来源 github.com/trending · AI 摘要由 Claude 生成",
        }],
    })

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": header_text},
                "template": CARD_TEMPLATE,
            },
            "elements": elements,
        },
    }


# ── 飞书推送 ──────────────────────────────────────────
def send_feishu_card(webhook_url, payload):
    """POST 一张卡片到飞书群机器人 Webhook，失败抛出异常。"""
    resp = requests.post(
        webhook_url,
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    result = resp.json()
    code = result.get("StatusCode", result.get("code", -1))
    if code != 0:
        raise RuntimeError(f"飞书返回错误: {result}")
    print(f"  ✅ 卡片推送成功: {result.get('StatusMessage', 'ok')}")


def send_all_cards(webhook_url, repos, date_str):
    """
    将 repos 分批构建卡片并依次推送。
    BATCH_SIZE 控制每张卡片的条目数，默认 10（即 Top 20 分 2 张）。
    """
    batches     = [repos[i:i + BATCH_SIZE] for i in range(0, len(repos), BATCH_SIZE)]
    total_parts = len(batches)

    for part_idx, batch in enumerate(batches, start=1):
        print(f"📲 推送第 {part_idx}/{total_parts} 张卡片 "
              f"(第 {batch[0]['rank']}–{batch[-1]['rank']} 名)…")
        payload = build_feishu_card(batch, date_str, part_idx, total_parts)
        send_feishu_card(webhook_url, payload)
        if part_idx < total_parts:
            time.sleep(1)  # 两张卡片之间稍作间隔，避免触发频率限制


# ── 主流程 ────────────────────────────────────────────
def main():
    webhook_url   = os.environ.get("FEISHU_WEBHOOK_URL", "").strip()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    if not webhook_url:
        sys.exit("❌ 缺少环境变量 FEISHU_WEBHOOK_URL")
    if not anthropic_key:
        sys.exit("❌ 缺少环境变量 ANTHROPIC_API_KEY")

    bj_time  = datetime.now(timezone(timedelta(hours=8)))
    date_str = bj_time.strftime("%Y-%m-%d")

    # 1. 抓取 Trending
    print(f"📡 正在抓取 GitHub Trending (Top {TOP_N})…")
    repos = fetch_trending(since="daily", limit=TOP_N)
    print(f"   获取到 {len(repos)} 个仓库")

    if not repos:
        sys.exit("❌ 未抓取到任何仓库，页面结构可能已变化，请检查 CSS 选择器")

    # 2. 逐条生成 AI 中文摘要
    client = anthropic.Anthropic(api_key=anthropic_key)
    print("🤖 正在生成 AI 中文摘要…")
    for i, repo in enumerate(repos, start=1):
        summary = get_ai_summary(client, repo["name"], repo["description"])
        repo["ai_summary"] = summary
        print(f"   [{i:02d}/{len(repos)}] {repo['name']}: {summary}")
        time.sleep(DELAY_BETWEEN_AI)

    # 3. 构建并推送飞书卡片
    send_all_cards(webhook_url, repos, date_str)
    print(f"\n🎉 全部完成！共推送 {len(repos)} 个项目到飞书")


if __name__ == "__main__":
    main()
