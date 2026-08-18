# LLM 因子挖矿多模型指南

> 状态：可用
> 工具：`bridge/llm_miner.py`
> 原则：**模型只负责生成表达式文本；表达式必须通过 QuantGPT 解析器校验，收益必须由本项目的判官在统一数据上评估。** LLM 提供商自己的回测/评分不采用。

## 1. 支持哪些 provider

| provider | API 类型 | 环境变量 | 默认模型 |
|---|---|---|---|
| `deepseek` | OpenAI-compatible | `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` | `deepseek-v4-flash` |
| `openai`（ChatGPT） | OpenAI-compatible | `OPENAI_API_KEY` / `OPENAI_BASE_URL` | `gpt-4.1-mini` |
| `claude` / `anthropic` | Anthropic Messages | `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` | `claude-sonnet-4-20250514` |

Profile 集中维护在 `config/llm_profiles.json`，也可用 `--model` 临时覆盖。

## 2. DeepSeek 模型现状（实测 2026-08）

`GET https://api.deepseek.com/v1/models` 只返回两个模型：

```text
deepseek-v4-flash
deepseek-v4-pro
```

`deepseek-chat` 已不在列表，但旧别名请求仍会 200，响应里 `model` 字段实际返回 `deepseek-v4-flash`——这是“居然有结果”的原因。QuantGPT 代码默认值已从 `deepseek-chat` 改为 `deepseek-v4-flash`（`llm_service.py`、`iteration.py`、`daily_summary.py`、`.env.example`）。

**重要**：v4 模型是推理模型。若 `max_tokens` 太小，它会把额度全花在 `reasoning_content` 上，`content` 为空。`llm_miner.py` 的 deepseek profile 默认 `max_tokens=30000`，并从 `reasoning_content` 中提取最后一个合法 JSON 数组。

## 3. 命令

```powershell
# DeepSeek（默认）
python bridge/llm_miner.py --provider deepseek

# ChatGPT
$env:OPENAI_API_KEY="sk-..."
python bridge/llm_miner.py --provider openai --model gpt-4.1-mini

# Claude
$env:ANTHROPIC_API_KEY="sk-ant-..."
python bridge/llm_miner.py --provider claude --model claude-sonnet-4-20250514

# 只生成 5 个、改输出路径
python bridge/llm_miner.py --count 5 --out config/expressions_llm_test.json
```

也可以把 key 写进 gitignored 的 `.env`（例如 `QuantGPT/.env` 或项目根 `.env`），脚本只读取环境，不把 key 写入任何产物。

## 3b. 一轮挖矿返回几个结果？

一轮 = 一次 LLM 调用，返回 **N 个表达式**（默认 `--count 10`），不是 1 个。这 10 个表达式都会进入因子面板，最终由判官统一评估，而不是只挑一个。

此外，因子来源可以叠加：

| 来源 | 数量 | 状态 |
|---|---|---|
| deterministic starter | 8 | 已接入 |
| DeepSeek LLM | 10（可调） | 已接入 |
| aurumq-rl alpha101 | 105+（质量分级） | `bridge/aurumq_factors.py` 已接入，5 股 smoke 跑通 |
| aurumq-rl gtja191 | 191（质量分级） | 同一模块支持 `--families alpha101,gtja191` |
| v6 四因子 | 4 | 已接入 |

## 4. 运行记录

每次成功运行都会在 `runs/llm_miner/<UTC时间戳>/` 下记录：

| 文件 | 内容 |
|---|---|
| `request.json` | provider、model、base_url、prompt、参数、prompt SHA-256（**无 key**） |
| `response.json` | 实际服务模型、usage、完整内容（含 reasoning_content） |
| `expressions.json` | 通过 QuantGPT 解析器校验后的表达式 |
| `manifest.json` | 运行摘要 + 上述三个文件的 SHA-256 |

示例（本次实际运行）：

```text
runs/llm_miner/20260815T152115Z/
├── request.json
├── response.json
├── expressions.json
└── manifest.json
```

## 5. 多模型评审（后续）

若想实现“一个模型生成、另一个模型评审”，建议：

```text
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_MODEL_REVIEW=deepseek-v4-pro
```

- 生成用 flash（快、便宜）
- 评审/交叉验证用 pro
- 生成结果先写 `runs/llm_miner/.../expressions.json`，再让 review provider 读取同一文件做否决/打分，避免两个模型串话。
