# 推理资讯周报

自动抓取豆瓣推理新书和 RSS 资讯，经过规则过滤、AI 精筛和历史去重后，生成 Markdown/HTML 周报。

## 功能特点

- 抓取多个豆瓣标签页的新书信息
- 抓取 RSS 资讯并做规则过滤
- AI 精筛支持多 provider 顺序回退
- 生成“精选 + 完整列表”两种输出
- 支持定时自动执行

## 当前支持的 AI provider

- `openrouter`
- `openai`
- `qwen`
- `deepseek`
- `zhipu`
- `doubao`

项目现在支持按顺序尝试多个 provider。前一个失败、超时或未配置 key 时，会自动尝试下一个。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

```bash
cp config.yaml.example config.yaml
```

### 3. 配置 API Key

建议通过环境变量配置，不要把密钥直接写进 `config.yaml`。
如果仓库里曾经出现过真实密钥，应立即删除并轮换该密钥，不要只做覆盖提交。

```bash
# OpenRouter
export OPENROUTER_API_KEY="sk-or-xxx..."

# OpenAI
export OPENAI_API_KEY="sk-xxx..."

# Qwen / DashScope
export DASHSCOPE_API_KEY="sk-xxx..."

# DeepSeek
export DEEPSEEK_API_KEY="sk-xxx..."

# Zhipu / BigModel
export ZHIPUAI_API_KEY="xxx"

# Doubao / Ark
export DOUBAO_API_KEY="xxx"
export DOUBAO_ENDPOINT_ID_TEXT="ep-xxx"
```

PowerShell:

```powershell
$env:OPENROUTER_API_KEY="sk-or-xxx..."
$env:OPENAI_API_KEY="sk-xxx..."
$env:DASHSCOPE_API_KEY="sk-xxx..."
$env:DEEPSEEK_API_KEY="sk-xxx..."
$env:ZHIPUAI_API_KEY="xxx"
$env:DOUBAO_API_KEY="xxx"
$env:DOUBAO_ENDPOINT_ID_TEXT="ep-xxx"
```

### 4. 运行

```bash
python main.py --once
python main.py
```

### 5. 发布到 GitHub Pages（含历史归档）

```bash
python publish_pages.py
```

一键归档 + 提交 + 推送：

```bash
python publish_pages.py --git-push
```

执行后会：

- 用最新周报覆盖站点首页 `index.html`（显示最新一期）
- 把该周报归档到 `reports/推理资讯周报_YYYY-MM-DD.html`
- 自动生成 `reports/index.html` 历史目录页
- 在首页右下角注入“历史归档”入口

如果你希望发布时直接提交并推送：

```bash
python publish_pages.py --git-push
```

## AI 配置

推荐使用新版 `providers` 列表配置，按顺序回退：

```yaml
ai_filter:
  enabled: true
  filter_rss: true
  filter_douban: true
  providers:
    - provider: "openrouter"
      model: "openrouter/auto"
      timeout: 30
      max_retries: 1
    - provider: "deepseek"
      model: "deepseek-chat"
    - provider: "qwen"
      model: "qwen-plus"
    - provider: "zhipu"
      model: "GLM-4-Flash-250414"
      timeout: 300
    - provider: "doubao"
      endpoint_id: "ep-xxx"
    - provider: "openai"
      model: "gpt-4.1-mini"
```

说明：

- `api_key` 留空时，会自动读取对应环境变量
- `base_url` 留空时，会使用该 provider 的默认兼容接口地址
- `doubao` 优先使用 `endpoint_id` 或环境变量 `DOUBAO_ENDPOINT_ID_TEXT`
- `openrouter` 默认兼容接口地址是 `https://openrouter.ai/api/v1`
- `zhipu` 默认兼容接口地址是 `https://open.bigmodel.cn/api/paas/v4`
- `timeout` 和 `max_retries` 可按 provider 单独设置

旧版单 provider 配置仍然兼容：

```yaml
ai_filter:
  enabled: true
  provider: "openrouter"
  model: "google/gemini-2.0-flash-lite"
  api_key: ""
  base_url: ""
```

## 推荐模型

- `openrouter`: `openrouter/auto`
- `openrouter`: `google/gemini-2.0-flash-lite`
- `openrouter`: `deepseek/deepseek-chat`
- `openai`: `gpt-4.1-mini`
- `qwen`: `qwen-plus`
- `deepseek`: `deepseek-chat`
- `zhipu`: `GLM-4-Flash-250414`
- `doubao`: 使用你在火山方舟创建的文本 endpoint

## 输出文件

运行后会在 `output/` 目录生成：

- `推理资讯周报_YYYY-MM-DD.md`
- `推理资讯周报_YYYY-MM-DD.html`

## 项目结构

```text
推理资讯周报/
├── main.py
├── app_helpers.py
├── report.py
├── config.yaml
├── config.yaml.example
├── requirements.txt
├── scrapers/
│   ├── douban.py
│   ├── rss_feeds.py
│   └── ai_filter.py
└── output/
```

## 更新记录

### 2026-05-27

- 增加 P0 稳定性保护：AI 筛选阶段会输出摘要日志，包含筛选前后数量、可用 provider 数量、调用成功/失败/不可用次数、丢弃数量和透传数量，便于定位“为什么没有生成内容”。
- 增强测试模式：`python main.py --once --test` 会使用独立状态文件和独立输出目录；测试模式下 AI 筛选默认 fail-open，AI 不可用时保留规则过滤后的候选内容，避免测试运行被 API Key 或 provider 故障误清空。
- 新增回归测试：`tests/test_p0_guardrails.py` 覆盖 AI 无 Key fail-open、测试状态隔离、智谱 provider 默认参数和环境变量解析。
- 新增智谱 AI provider：provider 名称为 `zhipu`，默认兼容接口为 `https://open.bigmodel.cn/api/paas/v4`，默认读取 `ZHIPUAI_API_KEY`，兼容 `ZHIPU_API_KEY`。
- 智谱默认模型设置为 `GLM-4-Flash-250414`，默认 `timeout` 设置为 `300` 秒；`config.yaml` 和 `config.yaml.example` 已加入对应配置。
- 已验证命令：

```bash
python -m py_compile main.py app_helpers.py report.py publish_pages.py scrapers/__init__.py scrapers/ai_filter.py scrapers/china_sources.py scrapers/douban.py scrapers/rss_feeds.py tests/test_p0_guardrails.py
python -m pytest -q
```

## 注意事项

1. 不要把真实 API Key 提交到仓库。
2. `config.yaml` 中的 `api_key` 建议始终保持为空，由程序从环境变量读取。
3. 如果历史提交里已经包含真实密钥，仅修改当前文件不够，还需要尽快轮换该密钥。
4. 豆瓣抓取建议保持 `delay >= 2`。
5. AI 精筛会产生 API 调用费用。

## 本地网页控制台

项目现在支持在本地电脑启动一个网页控制台，用来手动触发周报生成、查看运行状态、查看最近日志和打开最新 HTML 报告。后端只运行在本机，不需要部署到公网服务器。

安装依赖后运行：

```bash
uvicorn web_app:app --host 127.0.0.1 --port 8000
```

然后在浏览器打开：

```text
http://127.0.0.1:8000
```

控制台提供：

- `立即更新周报`：调用现有 `main.run_once` 执行正式抓取、AI 过滤和报告生成。
- `测试运行`：使用测试状态文件和 `output_test/` 输出目录。
- 运行状态：展示 `running / success / no_new_items / failed` 等状态，以及当前阶段。
- 抓取统计：展示原始书籍、规则过滤后书籍、新增书籍、原始资讯、规则过滤后资讯、新增资讯。
- 最近日志：读取 `data/runtime/weekly_report.log`。
- 历史报告：列出 `output/` 下已有 HTML 报告。

运行状态会写入：

```text
data/runtime/last_run.json
```

测试运行状态会写入：

```text
data/runtime/last_run_test.json
```

这些运行时文件已经被 `.gitignore` 忽略，不会上传到 GitHub。
## 本地静态发布（P2）

本地网页控制台现在支持把 `output/` 中最新周报同步成 GitHub Pages 静态站点文件。

页面按钮说明：

- `发布预检`：只校验最新 HTML 周报、归档路径、Git 状态，不写入 `index.html` 或 `reports/`。
- `发布静态页`：把最新周报复制为站点首页 `index.html`，并同步到 `reports/` 历史归档。
- `生成并发布`：先执行一次正式周报生成；只有生成了新的 HTML 周报时，才继续发布静态页。

本地接口：

```text
GET  /api/publish/status
GET  /api/publish/plan
POST /api/publish?dry_run=true
POST /api/publish
POST /api/run-and-publish
```

发布状态会写入：

```text
data/runtime/last_publish.json
```

默认不会执行 `git push`，避免误提交远程仓库。接口保留了 `git_push=true` 参数用于后续自动提交和推送。

## GitHub Pages 展示增强（P3）

发布静态页时会额外生成面向 GitHub Pages 的展示文件：

- `index.html`：站点首页，展示最新一期周报正文。
- `reports/latest.html`：最新一期周报的稳定访问别名。
- `reports/index.html`：历史周报归档页，展示最新一期摘要、历史列表和访问入口。
- `reports/index.json`：机器可读索引，包含生成时间、最新一期、历史周报列表、文件大小、修改时间，以及可解析到的精选新书/资讯数量。
- `reports/推理资讯周报_YYYY-MM-DD.html`：按日期归档的历史周报。

P3 仍然保持 GitHub Pages 只做静态展示，不在公网运行爬虫、大模型调用或本地控制台接口。

## 本地定时自动更新（P4）

P4 新增 `scheduled_runner.py`，用于被 Windows 任务计划程序定时调用。它不依赖本地网页控制台是否启动，可以在后台完成生成和发布。

手动验证一次：

```bash
python scheduled_runner.py --mode run-and-publish
```

只生成周报：

```bash
python scheduled_runner.py --mode run
```

只发布已有最新周报：

```bash
python scheduled_runner.py --mode publish
```

发布前预检，不写入站点文件：

```bash
python scheduled_runner.py --mode publish --dry-run
```

运行状态会写入：

```text
data/runtime/last_schedule.json
```

安装 Windows 任务计划程序任务：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_windows_task.ps1 -DayOfWeek Friday -At 18:00 -Mode run-and-publish
```

卸载任务：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/uninstall_windows_task.ps1
```

默认安装的任务不会执行 `git push`。如果确认要自动提交并推送静态页，可以在安装时加：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_windows_task.ps1 -DayOfWeek Friday -At 18:00 -Mode run-and-publish -GitPush
```

建议先不加 `-GitPush`，观察几次 `data/runtime/last_schedule.json` 和本地生成结果，确认稳定后再启用自动推送。
