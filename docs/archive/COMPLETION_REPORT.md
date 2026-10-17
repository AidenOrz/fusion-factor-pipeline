# 一口气执行完成报告

> 日期：2026-08
> 分类：`post_hoc_descriptive_only` / `developer_observation_only`
> 范围：P0~P3 全部执行一遍；所有结论只用于开发者观察，不构成生产授权。
> **验收口径（用户最新决定）**：不再把 legacy_score 作为硬门槛（其历史回测引擎有记账问题）；**唯一硬验收 = 跑赢上证指数（000001.SH）**。legacy_score 仅作诊断参照。

## 1. 执行结果总览

| 阶段 | 状态 | 关键产物 | 结果 |
|---|---|---|---|
| P0 数据底座 + 四坑断言 | ✅ | `bridge/data.py`、`bridge/ops.py`、`tests/run_p0_smoke.py` | 四项检查全过 |
| P0.5 判官校准 | ✅ | `bridge/gbdt.py` | binding 216 条上 legacy/v6 与审计漂移 < 0.00004 |
| P1 表达式 | ✅ | `config/expressions.json` + `config/expressions_llm.json` + `config/expressions_combined.json` | 8 个确定性 starter + 10 个 DeepSeek **v4-flash** 生成表达式（key 只存于 gitignored `QuantGPT/.env`） |
| P2 全市场面板 + walk-forward | ✅ | `data/panel_fusion.parquet`、`runs/gbdt_walkforward/` | 52,088 行 × 2,949 股 × 629 日；purged WF 跑通 |
| P3 开发者观察锁箱 | ✅ | `artifacts/dev_lockbox/dev/` | 已追加 2026-07-31 / 08-07 / 08-10 三个非空 live 信号日，链验证通过 |

## 2. P0 四个数据坑断言结果

```text
[ok] vwap lots->shares: real 000001 row ratio=1.154
[ok] vwap wrong unit: rejected as expected
[ok] group_rank missing industry: rejected as expected
[ok] group_rank: grouped percentile ranks computed, no silent global fallback
[ok] ts_corr zero-variance: NaN handled
[ok] market cap proxy: rejected as expected
[ok] market_cap: close*total_shares only; close*volume proxy rejected
```

## 3. P0.5 基准复现

使用候选账本 216 条 binding 候选，`isolated_r_multiple` 为目标的 Top-K 均值：

| top_k | legacy_score 复现 | v6 composite 复现 | 相对审计漂移 |
|---:|---:|---:|---:|
| 1 | 1.172280 | 0.693722 | ≤ 0.000024 |
| 3 | 0.660376 | 0.569278 | ≤ 0.000024 |
| 5 | 0.451578 | 0.400365 | ≤ 0.000035 |

结论：评估 harness 无 bug，`legacy > v6` 的已知答案被精确复现。

## 4. P2 walk-forward 结果（开发集，post-hoc）

- 模型：**lightgbm `LGBMRegressor`**（已安装，清华镜像可用；`bridge/gbdt.py` 保留 sklearn 回退）。
- 目标：`EXRET20`；purged walk-forward 4 折，gap=10 个交易日。
- 特征：v6 四因子 + 8 个 starter + 10 个 DeepSeek 表达式 + **baostock 总股本/市值**（`data/panel_fusion_combined.parquet`）。
- 评估行数：26,311。

| 指标 | ML 预测（starter+LLM+股本/市值，lightgbm） | legacy_score |
|---|---:|---:|
| 总体 Spearman IC | 0.1178 | — |
| Top1 平均 EXRET20 | +0.0027 | -0.0128 |
| Top3 平均 EXRET20 | -0.0108 | -0.0154 |
| Top5 平均 EXRET20 | -0.0128 | -0.0121 |
| ML-legacy Top3 | +0.0056 | — |

**上证指数验收（新口径）**：在 walk-forward 测试集 426 个交易日上：

| Top-K | ML 组合前瞻收益 | 上证同期 | 超额 | 日度跑赢率 |
|---:|---:|---:|---:|---:|
| 1 | 0.028731 | 0.017595 | **+0.011136** | 43.2% |
| 3 | 0.013188 | 0.017595 | -0.004407 | 41.1% |
| 5 | 0.010017 | 0.017595 | -0.007578 | 40.6% |

**裁决**：加入股本/市值后，**开发集 Top1 首次跑赢上证指数（超额 +0.0111）**；Top3/5 仍未跑赢。该结果仍属 `post_hoc_descriptive_only`，**不得据此选候选或改权重**；需要用 2026-07-18 之后干净数据继续验证。

### 4b. 完整因子池训练（starter + LLM + alpha101 + gtja191 + 股本/市值）

- 面板：`data/panel_fusion_full.parquet`，52,088 行，**316 个因子列**（v6 四因子 + 市值/股本 + 107 alpha101 + 191 gtja191 + 18 alpha_qg）。
- 训练：lightgbm purged walk-forward，评估 41,521 行。

| 指标 | 完整因子池 ML | legacy_score |
|---|---:|---:|
| Spearman IC | 0.1147 | — |
| Top1 EXRET20 | -0.0199 | -0.0136 |
| Top3 EXRET20 | -0.0160 | -0.0182 |
| Top5 EXRET20 | -0.0127 | -0.0166 |

| Top-K | ML 前瞻收益 | 上证同期 | 超额 | 日度跑赢率 |
|---:|---:|---:|---:|---:|
| 1 | 0.005824 | 0.017896 | -0.012072 | 41.3% |
| 3 | 0.007973 | 0.017896 | -0.009922 | 40.6% |
| 5 | 0.009990 | 0.017896 | -0.007906 | 41.5% |

**结论**：完整 316 因子池在开发集上**未跑赢上证指数**，且整体弱于“四因子+18表达式+股本市值”的精简池。该现象不用于反向选因子，只说明当前特征池存在冗余/噪声；下一步应做相关性去重或 SHAP 剪枝，而不是继续堆因子。

### 4c. 因子体检与剪枝（2026-08 完成）

- 分族 IC、相关性去重、SHAP 剪枝的完整产物在 `runs/factor_selection/`。
- 322 个因子列 → 相关性去重保留 80 → SHAP 剪枝保留 60。
- 三个池（316/80/60）在开发集上均未跑赢上证指数；结果只用于下一轮预注册设计。
- 长期依据见 `docs/PROJECT.md`，当前稳定点见 `docs/HANDOFF.md`。

## 5. P3 观察锁箱

- 官方 v6 锁箱未被消费。
- 本项目 `dev` epoch 已链式追加三个非空 live 信号日（共 4 条信号），`verify=valid`。
- `observe` 为无限次非消费读取；`formal-reveal` 仍每 epoch 一次。

## 5b. 总股本 / 行业 PIT 数据源（已实测）

- **baostock 无需代理即可直连**，`query_profit_data` 提供 `totalShare/liqaShare/pubDate/statDate`。
- **全市场股本已拉取**：`data/refdata/shares_pit.parquet`，49,965 行，覆盖 3,163 只股票（全部 3,194 只中 31 只 605 新股无数据），`pub_date` 范围 2022-04-07 ~ 2026-05-08；分片断点续传 + 合并完成。
- 股本 as-of 接入已验证：发布日前为 NaN，发布日后生效；融合面板 `market_cap` 覆盖率 99.2%。
- **行业历史 PIT 不可得**：baostock/WeStock/AkShare 均只有当前快照；baostock 快照带 `updateDate`，已落地为 `industry_snapshot.parquet`，并标记 `snapshot_not_historical`。
- WeStock 无需代理：`finance balance` 的 `PaidInCapital`、`profile` 的行业/`regCapital`/`listedDate` 可用作交叉验证。
- AkShare 函数存在，但本次东财接口网络失败，待补测。
- 详见 `docs/DATA_SOURCE_REPORT.md`。

## 5c. LLM 运行记录

- DeepSeek `/v1/models` 实测只有 `deepseek-v4-flash`、`deepseek-v4-pro`；`deepseek-chat` 是旧别名，服务端实际返回 v4-flash。
- QuantGPT 代码默认模型已从 `deepseek-chat` 改为 `deepseek-v4-flash`（`llm_service.py` / `iteration.py` / `daily_summary.py` / `.env.example`）。
- v4 是推理模型，max_tokens 太小会导致 `content` 为空；`llm_miner.py` 已用 30000 tokens 并从 `reasoning_content` 提取最后合法 JSON。
- 每次运行完整记录在 `runs/llm_miner/<UTC时间戳>/`：`request.json`（无 key）、`response.json`（含 usage/served_model）、`expressions.json`、`manifest.json`（含 SHA-256）。
- 多 provider 指南：`docs/LLM_MINING.md`；Claude 用 `--provider claude`，ChatGPT 用 `--provider openai`。

## 5d. DeepSeek key 是否必需（代码确认）

**不是必需。** `QuantGPT/quantgpt/routes/backtest_tasks.py:140-155`：
- 输入本身就是合法表达式 → 无 key 直接解析回测；
- 只有“自然语言转表达式”（第 147-155 行）和“表达式自动修复/迭代挖矿”才要求 `DEEPSEEK_API_KEY`。

## 6. 环境限制与降级记录

1. `lightgbm` 已安装（4.7.0，清华镜像），`bridge/gbdt.py` 已切换到 `LGBMRegressor` 并重跑；sklearn 仅作回退。
2. pytest `tmp_path` 在系统临时目录被拒绝，已用 `tests/run_p0_smoke.py`、`tests/run_smoke.py` 两个独立 runner 验证。
3. DeepSeek key 已配置在 `QuantGPT/.env`（gitignored），API 直连 200；已生成 10 个 LLM 表达式。
4. 行业历史 PIT 免费源不可得；`market_cap` 用 baostock 全市场总股本 as-of 接入，覆盖率 99.2%。
5. live 信号 2026-08-03/04/05/06/11/13/14 为空，锁箱仅追加有信号日；这是观察事实，不是研究解锁。

## 7. 下一步（需人工决策）

- 提供总股本/行业 PIT 数据源后，再开市值与行业中性化断言。
- 在正常环境安装 lightgbm，替换 `HistGradientBoostingRegressor` 重跑 P0.5/P2 对照。
- 配置 DeepSeek key 后运行 QuantGPT 挖矿，把表达式补进 `config/expressions.json`。
- G2 未过：不进入 v6 官方锁箱；继续积累 clean-date 观察。
