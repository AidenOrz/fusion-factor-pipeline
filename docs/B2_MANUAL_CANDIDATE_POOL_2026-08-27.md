# B2 人工裁决候选池记录

日期：2026-08-27
分类：`post_hoc_descriptive_only`，仅用于下一轮预注册式消融，不构成生产批准或实盘授权。

## 结论

以 `docs/DIRECTION_ADJUDICATION_ABC_2026-08-27_kimi.xlsx` 的明细表“ABC方向裁决”为本次裁决记录。明细表 42 行已经填写“我的决定”，落地为：

| 结果 | 数量 | 处理 |
|---|---:|---|
| 注册 long | 4 | `alpha004`、`alpha055`、`alpha059`、`gtja_090` |
| 注册 short | 5 | `alpha_qg_corr_pv_10`、`alpha_qg_f001`、`alpha_qg_f005`、`alpha_qg_f006`、`alpha_qg_f008` |
| 维持剔除/删除 | 31 | 不进入本候选池，但不删除原始面板列或表达式 |
| 维持观察 | 2 | `alpha052`、`gtja_183`，不进入候选池 |

摘要页的“维持剔除/删除（27）”与分组加总不一致，且和明细统计不一致。本项目以明细表为准，并保留原工作簿作为审计来源。

## 候选池组成

```text
105 个 B2 选择期代表
+ 9 个经人工明确批准的方向候选
= 114 个预注册消融候选
```

9 个新增候选不会被旧的 `greedy_representatives` 逻辑静默删除。其与现有代表的相关关系记录在 [`B2_MANUAL_CANDIDATE_AUDIT_2026-08-27.csv`](B2_MANUAL_CANDIDATE_AUDIT_2026-08-27.csv)，后续通过族级/信息类型消融处理。

消融分组清单见 [`B2_ABLATION_GROUPING_2026-08-27.csv`](B2_ABLATION_GROUPING_2026-08-27.csv)。其中 103 行的 `info_type_source` 是 `column_name_fallback`，说明 parquet 没有表达式语义元数据；这些行必须在消融启动前人工检查，不能把 `other` 当作确认后的经济信息类型。当前 114 列的家族分布为 `alpha101=43`、`gtja191=64`、`llm=5`、`starter=2`，没有 v6/capital 代表。

重点关系：

- `gtja_090` 与 `gtja_064` 的绝对 rank 相关为 `0.887123`，留在候选池，交由组内消融决定替代关系。
- `alpha_qg_f005` 与 `gtja_168` 是绝对相关 `1.0` 的镜像；保留人工批准列，但禁止把它解释成两份独立信息。
- `alpha_qg_f006` 与 `gtja_124` 的绝对相关为 `0.931398`，与 `vwap_dev_10` 的绝对相关为 `0.963`；交由消融比较。
- `alpha_qg_corr_pv_10` 与 `alpha_qg_f001/f005/f006` 的绝对相关不超过 `0.275`，作为独立量价相关候选保留。
- `alpha_qg_f008` 与旧待裁决的 `f001` 相关 `0.771`，未达到 `0.8` 的预注册去冗余阈值，按人工决定注册 short。

## 物化与边界

配置文件为 [`factor_pool_b2_adjudicated_2026-08-27.json`](../config/factor_pool_b2_adjudicated_2026-08-27.json)，特征清单为 [`factor_pool_b2_adjudicated_2026-08-27_features.json`](../config/factor_pool_b2_adjudicated_2026-08-27_features.json)。配置显式引用：

- 全市场面板：`data/panel_fusion_full_market.parquet`
- B2 标签：`y_neutral_rank20_b2`
- 选择期：`2023-06-30..2024-09-12`
- 标签净空：20 个交易日，evaluation 理论起点 `2024-10-22`
- Alpha101、GTJA191 和 QuantGPT 外部 parquet

生成命令：

```powershell
python bridge/manual_factor_pool.py `
  --pool config/factor_pool_b2_adjudicated_2026-08-27.json `
  --panel data/panel_fusion_full_market.parquet `
  --out-panel data/panel_fusion_b2_adjudicated_2026-08-27.parquet `
  --features-out config/factor_pool_b2_adjudicated_2026-08-27_features.json
```

物化器只按 `(date, code)` 键连接外部列；新配置要求 `panel_scope=full_market`、B2 掩码和 B2 标签存在，并拒绝 events 面板。short 列使用配置中显式的 `negate`，不会自动翻转任何未批准因子，也不会修改原始表达式配置。

## 下一步

1. 以 114 列生成 `family_of()` 和 `info_type_of()` 分组清单，先人工检查归类。
2. 在 selection period 内登记完整集与每个族的 LOFO 运行项；主线为 Huber 原始秩和 B2 中性秩 LambdaRank，固定 3 折 × 3 种子、20 日净空。
3. 噪声带必须在看结果前登记；比较使用同一 fold/seed 的配对差。
4. 只有冻结后的清单才能在 evaluation 期一次性验证；Top-K、十分位和 IC 衰减仍是后续描述/验收阶段，不能回写本候选池。

Huber 基线现已支持显式 `--universe` 和 `neutral_rank` 目标，后续可以与 B2 LambdaRank 在同一 20 日净空和折划分下比较；本轮尚未运行大规模消融，也没有用结果改写清单。

本文件不声明 114 个因子已经通过排序质量验收；它只记录人工裁决如何进入下一轮可复现实验。
