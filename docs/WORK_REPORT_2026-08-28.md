# 2026-08-28 工作报告：B2 预注册整组消融

> **状态说明（2026-08-28）：本文 v3 结论已被 v4 同模型跨标签复验取代。当前结论以 [`WORK_REPORT_2026-08-28_ABLATION_V4.md`](WORK_REPORT_2026-08-28_ABLATION_V4.md) 及后续剪后确认报告为准；本文仅保留为历史实验记录。**

项目：`fusion-factor-pipeline`
性质：selection period 开发集描述性实验，全部标记为 `post_hoc_descriptive_only`，不构成生产批准或实盘授权。

## 一、实验边界

- 面板：全市场 `panel_scope=full_market`，B2 universe。
- 标签：Huber 线使用 `y_raw_rank20_b2`；LambdaRank 线使用 `y_neutral_rank20_b2`。
- 选择期：截至 2024-09-12；标签净空 20 个交易日；首个无训练折跳过，实际 3 折。
- 种子：42、43、44；两条模型均使用同一日期切分。
- 方向：不自动翻转；消融不自动修改候选清单或生产配置。
- v6：没有读取、重跑或比较 2026-01-01 至 2026-07-17 已消费 OOS。

## 二、信息类型元数据

原来的 103 行 `column_name_fallback` 已用 aurumq-rl `FactorEntry.category`、QuantGPT 表达式元数据和人工批准候选元数据重分类，保留原始细分类并归并成六个宽类：

| 宽类 | 因子数 |
|---|---:|
| `volume_vwap` | 37 |
| `momentum` | 33 |
| `correlation` | 20 |
| `volatility` | 16 |
| `reversal` | 7 |
| `capital` | 1 |

审计文件：[`B2_INFO_TYPE_REVIEW_2026-08-27.csv`](B2_INFO_TYPE_REVIEW_2026-08-27.csv)。所有 114 行均有可追溯来源，未解决项为 0。

## 三、完整集基线与噪声带

| 模型 | 日均 IC | HAC IC_IR | 3 折 IC 标准差 | 种子扰动噪声标准差 |
|---|---:|---:|---:|---:|
| Huber 原始秩 | 0.106226 | 0.188559 | 0.075256 | 0.002455 |
| LambdaRank 中性秩 | 0.088378 | 0.290987 | 0.045437 | 0.017190 |

噪声带在任何 LOFO 结果之前登记，定义为完整集各折各 seed IC 与三 seed 集成 IC 差值的样本标准差。

## 四、Family LOFO 结果

只有当一个组在两条主模型上都满足 `abs(delta_ic) <= 对应噪声标准差`，才允许标记为“可列入移除候选”。

| Family | Huber ΔIC | LambdaRank ΔIC | 预注册解释 |
|---|---:|---:|---|
| `alpha101` | -0.005596 | -0.024284 | 保留，移除明显超出噪声带 |
| `gtja191` | +0.004448 | +0.000510 | 保留，Huber 超出噪声带 |
| `llm` | -0.002930 | -0.008539 | 保留，Huber 超出噪声带 |
| `starter` | -0.000351 | -0.002459 | **可列入移除候选，需人工冻结** |

## 五、信息类型 LOFO 结果

| 信息类 | Huber ΔIC | LambdaRank ΔIC | 预注册解释 |
|---|---:|---:|---|
| `capital` | -0.000821 | -0.004681 | **可列入移除候选，需人工冻结** |
| `correlation` | -0.011488 | -0.008716 | 保留，Huber 超出噪声带 |
| `momentum` | +0.012246 | -0.008472 | 保留，两条主线方向不一致 |
| `reversal` | -0.004718 | -0.004844 | 保留，Huber 超出噪声带 |
| `volatility` | +0.005558 | +0.002647 | 保留，Huber 超出噪声带 |
| `volume_vwap` | -0.015444 | +0.008056 | 保留，两条主线方向不一致 |

完整明细：[`lofo_summary.csv`](../runs/factor_ablation_v3_b2_2026-08-27/lofo_summary.csv)。可读报告：[`ablation_report.md`](../runs/factor_ablation_v3_b2_2026-08-27/ablation_report.md)。

## 六、我的意见与下一步

这轮结果不支持“整族大幅精简”的结论。`alpha101`、`gtja191` 和 `llm` 仍有跨模型影响，不能因单条模型改善或恶化而删除；信息类型结果显示 Huber 原始秩与 LambdaRank 中性秩对 momentum、volume_vwap 的判断不一致，说明标签/模型交互仍然存在。`starter` 只有 2 列且两条主线均在噪声带内，`capital` 只有 1 列，二者可以进入人工冻结候选，但应记录为降复杂度而非“证明无效”。

下一步顺序固定为：

1. 人工确认是否冻结移除 `starter` 与 `capital`，新建版本化特征清单，不回写原始表达式配置。
2. 用冻结清单在 evaluation period 一次性验证排序质量；不在 evaluation 结果上迭代。
3. 只有排序质量跨折稳定后，才生成十分位单调性、horizon IC 衰减、Top-K 净收益、右尾捕获和 regret 报告。
