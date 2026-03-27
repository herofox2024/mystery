# OpenRouter 配置指南

推理资讯周报现已支持 OpenRouter，可以无梯子调用多个国际大模型。

## 快速开始

### 1. 获取 OpenRouter API Key

- 访问 [OpenRouter.io](https://openrouter.io)
- 注册账号并登录
- 在 Dashboard 中获取 API Key
- OpenRouter 支持国内访问，无需梯子

### 2. 配置 API Key

**方式一：环境变量（推荐）**
```bash
# Linux/Mac
export OPENROUTER_API_KEY="sk-or-xxx..."

# Windows (PowerShell)
$env:OPENROUTER_API_KEY="sk-or-xxx..."

# Windows (CMD)
set OPENROUTER_API_KEY=sk-or-xxx...
```

**方式二：配置文件**
编辑 `config.yaml`：
```yaml
ai_filter:
  api_key: "sk-or-xxx..."
```

### 3. 选择模型

在 `config.yaml` 中修改 `model` 字段：

```yaml
ai_filter:
  # 推荐：Gemini 2.0 Flash Lite（快速、便宜）
  model: "google/gemini-2.0-flash-lite"

  # 其他选项：
  # model: "deepseek/deepseek-chat"          # 深度求索
  # model: "openai/gpt-4o-mini"               # OpenAI
  # model: "meta-llama/llama-3.2-90b-vision" # Llama
  # model: "openrouter/auto"                  # 自动选择
```

### 4. 运行

```bash
python main.py --once
```

## 常见问题

**Q: OpenRouter 需要梯子吗？**
A: 不需要，OpenRouter 支持国内访问。

**Q: 如何选择模型？**
A: 推荐使用 `google/gemini-2.0-flash-lite`，速度快且成本低。

**Q: 费用如何？**
A: OpenRouter 是按 token 计费的，Gemini 成本最低。可在 [OpenRouter 价格页面](https://openrouter.io/docs/models) 查看。

**Q: 支持哪些模型？**
A: OpenRouter 支持 50+ 个模型，具体列表见 [Model List](https://openrouter.io/docs/models)。
