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

## 注意事项

1. 不要把真实 API Key 提交到仓库。
2. `config.yaml` 中的 `api_key` 建议始终保持为空，由程序从环境变量读取。
3. 如果历史提交里已经包含真实密钥，仅修改当前文件不够，还需要尽快轮换该密钥。
4. 豆瓣抓取建议保持 `delay >= 2`。
5. AI 精筛会产生 API 调用费用。
