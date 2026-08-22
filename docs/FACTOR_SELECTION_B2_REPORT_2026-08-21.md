# B2 全市场因子选择与方向裁决报告

日期：2026-08-21
分类：`post_hoc_descriptive_only`。本报告和其 CSV 只用于设计下一轮预注册实验，不批准生产因子、方向翻转或 Top-K 策略。

## 本轮结论

主选择口径已经固定为全市场面板 `data/panel_fusion_full_market.parquet`、`universe=b2`、目标 `y_neutral_rank20_b2`。选择期为 2023-06-30 至 2024-09-12；其后预留 20 个交易日的标签净空，evaluation 理论起点为 2024-10-22。旧 `events + EXRET20` 审阅的结论没有被自动继承。

本次正式运行的漏斗为：

| 步骤 | 数量 | 规则 |
|---|---:|---|
| 实际可评估因子 | 316 | 全市场 B2 选择期 |
| 静态合格 | 217 | 有足够日度截面区分度且 `abs(IC) >= 0.01` |
| 静态排除 | 99 | 74 个弱 IC、24 个截面数据不足、1 个无有效日 |
| 方向/历史冲突已暂扣后的候选 | 177 | 40 个静态合格因子等待裁决 |
| `abs(rho) > 0.8` 去冗余后保留 | 105 | 只在 177 个可用候选中选代表 |

因此，`selected_features.json` 的 105 个因子不包含未裁决的方向冲突、watch-only 或旧决策冲突项。行业中性化候选、截面归一化源因子和交叉项种子也只从这 105 个代表中产生。

## 方向噪声修正

方向反号不再只看近期/历史均值异号。只有同时满足反号且 `max(abs(recent_ic), abs(historical_ic)) > 0.02`，才标为 `regime_sensitive` 和触发方向审阅；低于该幅度的反号标为 `no_significant_signal`，按弱信号淘汰，不占人工审阅队列。

| 指标 | 数量 |
|---|---:|
| 原始近期/历史反号 | 40 |
| 高于 0.02 幅度门槛的 `regime_sensitive` | 21 |
| 低幅度 `no_significant_signal` | 19 |
| 仍通过静态筛选、需要方向裁决的 regime 因子 | 13 |
| 高幅度但未通过静态 IC 下限的研究观察 | 7 |

这证实了用户指出的噪声问题：`alpha010`（选择期 IC `-0.000005`）和 `gtja_100`（`+0.000777`）都已变为 `no_significant_signal`，不再因微小反号触发人工审阅，也不会进入候选。

不过，“真实待审只会剩 6-8 个”并未被本次 B2 数据支持。门槛后仍有 13 个静态合格的高幅度 regime 因子；它们的近期与历史均值确实反号，幅度超过注册阈值，不能为了压低待审量而继续隐藏。正确处理是暂扣，而不是自动翻转或假定其稳定。

用户特别关心的项目如下：

| 因子 | 选择期 IC | 近期 IC | 历史 IC | 当前 B2 主导方向 | 当前处理 |
|---|---:|---:|---:|---|---|
| `gtja_035` | +0.0264 | -0.0052 | +0.0480 | long | 人工裁决，暂不入选 |
| `gtja_106` | -0.0155 | +0.0304 | -0.0470 | short | 人工裁决，暂不入选 |
| `gtja_116` | -0.0118 | +0.0301 | -0.0403 | short | 人工裁决，暂不入选 |
| `gtja_068` | -0.0041 | +0.0339 | -0.0300 | short | 研究观察，`abs(IC) < 0.01`，不得入候选 |
| `gtja_147` | -0.0038 | +0.0356 | -0.0312 | short | 研究观察，`abs(IC) < 0.01`，不得入候选 |
| `gtja_151` | +0.0001 | +0.0423 | -0.0289 | long | 研究观察，`abs(IC) < 0.01`，不得入候选 |

“当前 B2 主导方向”是描述性建议，不是对因子值做负号变换的命令。对 regime 因子，本轮保持排除；只有在未来预注册一个风格条件、样本期和验收标准后，才研究是否重新纳入。

## 旧决策冲突

2026-08-18 的人工决策来自旧 `events + EXRET20` 口径，既不能自动恢复，也不能自动删除。程序将涉及的 17 个因子作为 `legacy_review_conflict` 暂扣；其中 12 个在新 B2 静态筛选合格，需要你重新裁决。

| 因子 | B2 IC | B2 建议方向 | 当前建议 |
|---|---:|---|---|
| `alpha004` | +0.0188 | long | 维持暂扣；watch-only 未满足升级阈值 |
| `alpha055` | +0.0284 | long | 维持暂扣，等待重审 |
| `alpha059` | +0.0285 | long | 维持暂扣，等待重审 |
| `alpha091` | +0.0137 | long | 维持暂扣，等待重审 |
| `alpha093` | -0.0114 | short | 维持暂扣，等待重审 |
| `gtja_001` | +0.0149 | long | 维持暂扣，等待重审 |
| `gtja_058` | -0.0152 | short | 维持暂扣，等待重审 |
| `gtja_073` | +0.0168 | long | 维持暂扣，等待重审 |
| `gtja_090` | +0.0351 | long | 维持暂扣，等待重审 |
| `gtja_125` | +0.0125 | long | 同时存在 regime 反号，建议本轮删除 |
| `gtja_176` | -0.0284 | short | 维持暂扣，等待重审 |
| `gtja_181` | -0.0118 | short | 维持暂扣，等待重审 |

其余 5 个历史冲突项 `alpha052`、`alpha071`、`gtja_072`、`gtja_082`、`gtja_183` 未通过本轮 B2 静态筛选，已不构成当前候选层面的方向决定，也没有进入代表清单。

## 需要你裁决的 40 个因子

完整可过滤表在 `runs/factor_selection_v3_b2_neutral_selection_full_322/direction_adjudication_queue.csv`。其中 `review_scope=manual_decision` 的 40 项按原因分为四组；所有组在裁决前均不进入 `selected_features.json`。

### A. 旧口径冲突，12 个

`alpha004`、`alpha055`、`alpha059`、`alpha091`、`alpha093`、`gtja_001`、`gtja_058`、`gtja_073`、`gtja_090`、`gtja_125`、`gtja_176`、`gtja_181`。建议不要因 B2 单次统计而直接撤销旧决定；请逐个决定“新 B2 证据足以重新注册”还是“继续排除”。若重新注册，方向采用表中的 B2 建议方向，并在配置中人工写入。

### B. 注册方向与 B2 建议不一致，9 个

`alpha_qg_amt_ratio_5`、`alpha_qg_corr_pv_10`、`alpha_qg_f001`、`alpha_qg_f005`、`alpha_qg_f006`、`alpha_qg_f008`、`alpha_qg_f010`、`alpha_qg_mom_20`、`alpha_qg_vwap_dev_10`。

九个的 B2 建议方向均为 **short**，且 f001/f005/f006 的选择期 IC 分别为 `-0.0279`、`-0.0314`、`-0.0877`。建议：只有明确人工批准后，才将注册方向改为 short 并作为下一轮预注册候选；未批准即继续从清单排除。程序不会自动翻转。

### C. 高幅度 regime 反号且静态合格，13 个

`alpha066`、`alpha082`、`alpha097`、`gtja_008`、`gtja_009`、`gtja_014`、`gtja_017`、`gtja_021`、`gtja_024`、`gtja_035`、`gtja_103`、`gtja_106`、`gtja_116`。建议本轮全部删除，不按当前主导方向翻转。若认为这是可利用的市场风格效应，应另起预注册实验，明确风格状态、切换时点、训练/验证分界和失败条件；不能用 evaluation 期寻找切换规则。

### D. 方向不稳定，6 个

`alpha032`、`alpha037`、`alpha039`、`gtja_149`、`gtja_165`、`gtja_184`。日度符号或滚动方向不稳定；尽管当前均值建议为 long，但这不是足以注册 long 的证据。建议直接删除，除非能给出独立、先验的经济机制与后续预注册验证。

## 仅作研究观察的 7 个因子

`alpha031`、`gtja_055`、`gtja_068`、`gtja_138`、`gtja_147`、`gtja_151`、`gtja_170` 均有高幅度近期/历史反号，但选择期 `abs(IC) < 0.01`。它们记录为 `research_observation_not_candidate`，不是请你选择方向的候选，也不能因为近期值看起来强而捞回。任何后续研究必须作为新的预注册假设。

## 产物与边界

- `runs/factor_selection_v3_b2_neutral_selection_full_322/factor_ic_table.csv`：静态与方向候选状态、排除原因。
- `runs/factor_selection_v3_b2_neutral_selection_full_322/direction_review.csv`：原始反号、幅度、推荐方向与审阅状态。
- `runs/factor_selection_v3_b2_neutral_selection_full_322/direction_adjudication_queue.csv`：40 个 `manual_decision` 和 7 个非候选研究观察。
- `runs/factor_selection_v3_b2_neutral_selection_full_322/selected_features.json`：105 个当前可用于下一步消融的去冗余因子。
- `runs/factor_selection_v3_b2_neutral_selection_full_322/selection_manifest.json`：面板、标签、净空、计数和规则元数据。

本轮没有读取、比较、重跑或修改 a-share-factor-engine-v6 已消费的 M8 OOS。v6 只做了健康检查，确认版本为 `6.5.0.dev0` 且 M8 已执行。
