# 2026-08-28 工作报告：B2 同模型跨标签整组消融

项目：`fusion-factor-pipeline`
性质：selection period 开发集描述性实验，全部标记为 `post_hoc_descriptive_only`，不构成生产批准或实盘授权。

## 一、实验修订

上一轮 `v3` 同时改变估计器和标签：Huber 原始秩 vs LambdaRank 中性秩；且 LambdaRank 噪声带约为 Huber 的 7 倍，不能作为有判别力的第二主线。`v3` 目录和结果保持不动，作为历史证据保留。

本轮 `v4` 改为同模型跨标签：

- `huber_b2_raw_rank`：`LGBMRegressor(objective='huber')` + `y_raw_rank20_b2`；
- `huber_b2_neutral_rank`：同一 Huber 配置 + `y_neutral_rank20_b2`；
- 全市场面板、B2 universe、选择期截至 2024-09-12、20 日 purge gap、3 折、种子 42/43/44；
- 不自动翻转方向，不自动改候选配置，不读取 v6 已消费的 2026 OOS。

代码登记与结果目录：[`bridge/ablation.py`](../bridge/ablation.py)、[`factor_ablation_v4_b2_huber_cross_label_2026-08-28`](../runs/factor_ablation_v4_b2_huber_cross_label_2026-08-28)。`runs/` 按仓库规则为本地生成产物，不纳入 Git。

## 二、基线与噪声带

| 主线 | 日均 Spearman IC | HAC IC_IR | NDCG@5 增量 | 噪声标准差 |
|---|---:|---:|---:|---:|
| Huber 原始秩 | 0.106226 | 0.188559 | 0.026437 | 0.002455 |
| Huber 中性秩 | 0.103780 | 0.291825 | 0.064070 | 0.001742 |

两条噪声带均由完整集基线先行登记，数值为各折各 seed IC 相对三 seed 集成 IC 的样本标准差。

## 三、方向敏感判定

- `delta_ic > noise_std`：`removal_improves`，被移除组的边际贡献为负；
- `delta_ic < -noise_std`：`removal_harms`，被移除组的边际贡献为正；
- `abs(delta_ic) <= noise_std`：`indistinguishable`，没有可检测边际贡献。

组级规则是：没有任何主线 `removal_harms` 时才可列入人工移除候选；全部带内属于“无可检测边际贡献”，至少一条改善属于“移除后显著改善”；同时出现改善与受损则为 `direction_conflict`，必须人工复核。程序不会自动执行删除。

## 四、Family LOFO

| Family | Huber raw ΔIC | Huber neutral ΔIC | 组级解释 |
|---|---:|---:|---|
| `alpha101` | -0.005596 | -0.000389 | 保留：raw 线显示移除显著受损 |
| `gtja191` | +0.004448 | +0.017644 | 可列入移除候选：两条线均显著改善 |
| `llm` | -0.002930 | -0.003635 | 保留：两条线均显示移除受损 |
| `starter` | -0.000351 | +0.000275 | 可列入移除候选：两条线均带内 |

结论：`gtja191` 的“移除后变好”在同模型跨标签复验中更强，而不是旧 LambdaRank 线的模型差异假象；但仍需完成重复因子和剪后全集确认，不能直接改配置。

## 五、信息类型 LOFO

| 信息类 | Huber raw ΔIC | Huber neutral ΔIC | 组级解释 |
|---|---:|---:|---|
| `capital` | -0.000821 | +0.000559 | 可列入移除候选：两条线均带内 |
| `correlation` | -0.011488 | -0.006254 | 保留：两条线均显示移除受损 |
| `momentum` | +0.012246 | +0.016738 | 可列入移除候选：两条线均显著改善 |
| `reversal` | -0.004718 | -0.001243 | 保留：raw 线显示移除显著受损 |
| `volatility` | +0.005558 | +0.001091 | 可列入移除候选：raw 显著改善、neutral 带内 |
| `volume_vwap` | -0.015444 | -0.016051 | 保留：两条线均显示移除受损 |

这直接支持“不能把正的 ΔIC 机械写成保留”的审阅意见：`momentum` 两条线均为负边际贡献；`volatility` 没有一条线显示正边际贡献。`gtja191` 则在 family 维度同样成立。

## 六、下一步

1. 读取 [`lofo_group_decisions.csv`](../runs/factor_ablation_v4_b2_huber_cross_label_2026-08-28/lofo_group_decisions.csv)，做 family 与 info-type 的交叉重叠审计，避免同一因子被重复计入删除。
2. 对 `gtja191`、`momentum`、`volatility` 依计划执行一次组内重剪或整组候选登记；固定规则，不逐因子来回试错。
3. 生成版本化剪后清单，但不覆盖 `factor_pool_b2_adjudicated_2026-08-27_features.json`，也不改人工方向裁决。
4. 用剪后清单与当前注册的 114 因子全集做一次冻结确认；检查 IC、HAC IC_IR、折间符号一致性。
5. 只有冻结确认通过，才在 evaluation 期做一次性验证；Top-K、十分位与 horizon 分析继续作为后验描述，不反向调参。
