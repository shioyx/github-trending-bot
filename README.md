# 🔥 GitHub Trending Bot · 飞书版

每天早上 **08:00（北京时间）** 自动抓取 GitHub 热榜 Top 20，  
由 Claude AI 为每个项目生成中文一句话摘要，  
以**飞书卡片消息**形式推送到你的群/单聊。  
运行在 GitHub Actions，完全免费，零服务器。

---

## 推送效果

每天发送 **2 张卡片**（各 10 条），样式如下：

```
🔥 GitHub 热榜 2025-06-01 · Top 20  (1/2)
─────────────────────────────────────
1. openai/whisper  `Python`
🤖 开源多语言语音识别系统，支持转录与翻译
⭐ 68.2k  🚀 今日 +1,234  🍴 7.8k
─────────────────────────────────────
2. vercel/next.js  `JavaScript`
🤖 基于 React 的全栈 Web 框架，支持 SSR 与静态生成
⭐ 121k  🚀 今日 +890  🍴 26k
...
```

---

## 完整部署步骤

### 第一步：创建 GitHub 仓库

1. 登录 GitHub，点击右上角 **+** → **New repository**
2. 填写仓库名，例如 `github-trending-bot`
3. 选 **Public**（Actions 免费无限额）或 Private 均可
4. 点击 **Create repository**

### 第二步：上传代码文件

把以下 4 个文件按原始路径上传到仓库：

```
your-repo/
├── .github/
│   └── workflows/
│       └── trending.yml      ← GitHub Actions 定时任务
├── trending.py                ← 主脚本
├── requirements.txt           ← Python 依赖
└── README.md
```

**方法 A（网页上传）**
- 在仓库页面点 `Add file → Upload files`，依次上传
- `.github/workflows/trending.yml` 需先在本地建好目录结构再上传

**方法 B（git 命令行）**
```bash
git clone https://github.com/你的用户名/github-trending-bot.git
cd github-trending-bot
# 把文件放进来
git add .
git commit -m "init"
git push
```

### 第三步：配置飞书 Webhook

你已经有了飞书 Webhook URL（来自群机器人），格式为：

```
https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

> 如果还没有，在飞书群 → 右上角「···」→ 「机器人」→ 「添加机器人」→ 选「自定义机器人」→ 复制 Webhook 地址。

### 第四步：配置 GitHub Secrets

在仓库页面依次点击：

```
Settings（顶部菜单）
  → Secrets and variables（左侧栏）
    → Actions
      → New repository secret（绿色按钮）
```

添加以下 **2 个** Secret：

| Secret 名称 | 值 |
|---|---|
| `FEISHU_WEBHOOK_URL` | 你的飞书 Webhook URL（完整 https://... 地址） |
| `ANTHROPIC_API_KEY` | 你的 Anthropic API Key（在 console.anthropic.com/keys 获取） |

> ⚠️ Secret 值粘贴后不会再显示明文，保存前确认复制正确。

### 第五步：启用 Actions

1. 点击仓库顶部的 **Actions** 标签
2. 如果看到黄色提示「Workflows aren't being run on this forked repository」，点击 **Enable GitHub Actions**

### 第六步：手动测试

1. 进入 `Actions` → 左侧选 `GitHub Trending Daily Push`
2. 点右侧 **Run workflow** → 再点绿色 **Run workflow** 按钮
3. 等约 2–4 分钟，查看飞书是否收到 2 张卡片消息
4. Actions 页面绿色勾 = 成功，红色叉 = 失败（点进去看日志）

之后每天 **08:00（北京时间）** 自动运行，无需任何操作。

---

## 自定义配置

编辑 `trending.py` 顶部的配置区：

```python
TOP_N        = 20    # 每次推送几个项目（建议 10–25）
BATCH_SIZE   = 10    # 每张卡片几条（Top 20 → 2 张卡片）
CARD_TEMPLATE = "red" # 卡片标题颜色
                      # 可选: red / wathet / turquoise / green
                      #       yellow / orange / carmine / violet
                      #       purple / indigo / grey / blue
```

修改推送时间，编辑 `.github/workflows/trending.yml` 中的 cron：

```yaml
# 格式：分 时 日 月 星期（UTC 时间）
- cron: "0 0 * * *"    # UTC 00:00 = 北京 08:00（默认）
- cron: "0 1 * * *"    # UTC 01:00 = 北京 09:00
- cron: "30 22 * * *"  # UTC 22:30 = 北京 06:30
- cron: "0 0 * * 1"    # 每周一 08:00（周报模式）
```

---

## 常见问题

**Q: Actions 运行成功但飞书没收到消息？**  
检查 Webhook URL 是否完整复制，机器人是否还在群中，飞书群是否开启了机器人权限。

**Q: 日志显示 AI摘要失败？**  
检查 `ANTHROPIC_API_KEY` 是否正确，以及 Anthropic 账号余额。

**Q: 抓取到 0 个仓库？**  
GitHub Trending 页面可能临时挂了，等下次自动重试。如果持续出现，检查 `trending.py` 中的 CSS 选择器是否需要更新。

**Q: Private 仓库 Actions 免费额度够吗？**  
每次运行约 3–4 分钟，每月 30 次 ≈ 90–120 分钟，GitHub 免费账号每月有 2000 分钟，完全够用。
