# 2026-08-27 工作报告：B2 人工裁决候选池落地

项目：`fusion-factor-pipeline`
性质：开发集研究与工程记录，全部结论为 `post_hoc_descriptive_only`，不构成生产批准、实盘授权或收益承诺。

## 一、战略方向

主系统继续定义为：在每日预先定义的全市场可交易域内，预测下一期横截面超额收益排序。事件子集只保留为 legacy 诊断，不参与本轮训练、标签、因子选择或方向继承。

当前主研究口径：

```text
panel_scope = full_market
universe    = b2
target      = y_neutral_rank20_b2
selection   = 2023-06-30..2024-09-12
label_gap   = 20 个交易日
evaluation  = 2024-10-22 起（冻结清单后一次性验证）
```

筛选/消融期主指标仍是日均 Spearman IC、分折一致性、NDCG 和 HAC IC_IR；Top-K、右尾捕获、regret 与跑赢上证只在因子冻结后的验收期升为主指标。方向不会由程序自动翻转，方向变化或频繁换向的因子保留人工审阅标记。

## 二、既有训练结果（作为当前基线）

2026-08-20 首轮全市场实验的关键事实：

| 实验 | 日均 IC | HAC IC_IR | 折间 IC 标准差 | 结论 |
|---|---:|---:|---:|---|
| B2 原始秩 LambdaRank | 0.0709 | 0.1152 | 0.0281 | 低于中性秩稳定性 |
| B2 中性秩 LambdaRank | **0.0861** | 0.2087 | **0.0098** | 当前域/标签主候选 |
| B1 Huber 原始秩 | 0.0859 | 0.1438 | 0.0257 | 保留为回归基线 |
| B3 原始秩 LambdaRank | 0.0946 | 0.1650 | 0.0417 | 均值高但不稳定，不专精 |

首轮 Top-K 只作附录：B2 中性秩 Top-5 平均超额约 0，日胜率 50.0%，未形成可验收的收益地基。因此本轮不因 Top-K 结果增删因子，也不触碰已消费的 v6 2026 OOS。

## 三、今日落地

### 1. 人工裁决进入新配置

新增 [`config/factor_pool_b2_adjudicated_2026-08-27.json`](../config/factor_pool_b2_adjudicated_2026-08-27.json)：

- 105 个既有 B2 代表作为基线；
- 4 个 long：`alpha004`、`alpha055`、`alpha059`、`gtja_090`；
- 5 个 short：`alpha_qg_corr_pv_10`、`alpha_qg_f001`、`alpha_qg_f005`、`alpha_qg_f006`、`alpha_qg_f008`；
- 2 个 watch-only：`alpha052`、`gtja_183`，不进候选；
- 其余明细裁决为剔除/删除，不删除原始面板列或表达式注册。

每个新增候选都要求 `human_approved=true`，方向和 `identity/negate` 变换显式写入。旧 `factor_pool_v2_manual_review.json`、原始表达式配置和历史选择结果保持不变。

Excel 摘要页的“维持剔除/删除（27）”与分组加总、明细行不一致；本次以 `ABC方向裁决` 明细 42 行为准：31 个剔除/删除、9 个注册、2 个观察。

### 2. 全市场外部因子按键合并

`bridge/manual_factor_pool.py` 现在支持配置 `source.factor_files`，只按唯一 `(date, code)` 合并缺失的 Alpha101、GTJA191 和 QuantGPT 列，并拒绝重复键。带 `scope` 的新配置要求 `panel_scope=full_market`、`universe_b2` 和 `y_neutral_rank20_b2`，误接旧 events 面板会直接失败。

真实物化命令已执行，得到本地 `data/panel_fusion_b2_adjudicated_2026-08-27.parquet`：

```text
1,968,826 行；唯一 (date, code)；panel_scope=full_market
114 个特征全部存在；B2 行 1,155,725
9 个手工列与源列覆盖率一致；short 列逐值为源列取负
```

该 parquet 属于生成数据，不提交 Git。

### 3. 消融前置记录

新增：

- [`docs/B2_MANUAL_CANDIDATE_AUDIT_2026-08-27.csv`](B2_MANUAL_CANDIDATE_AUDIT_2026-08-27.csv)：新增候选的最近代表、相关数值和镜像/替代说明；
- [`docs/B2_ABLATION_GROUPING_2026-08-27.csv`](B2_ABLATION_GROUPING_2026-08-27.csv)：114 行分组清单；
- [`docs/B2_MANUAL_CANDIDATE_POOL_2026-08-27.md`](B2_MANUAL_CANDIDATE_POOL_2026-08-27.md)：裁决、边界和后续执行说明。

当前家族分布为 `alpha101=43`、`gtja191=64`、`llm=5`、`starter=2`，本轮 105 代表中没有 v6/capital。分组清单中 103 行的 `info_type_source=column_name_fallback`，表示外部 parquet 没有表达式语义元数据；这些行必须先人工检查，不能把 `other` 当成已经确认的信息类型。

## 四、验证结果

```text
pytest -q                                      38 passed
pytest -q（新增相关测试集合）                  21 passed
tests/run_review_smoke.py                      passed（设置四个上游路径变量后）
tests/run_training_plan_smoke.py               passed
python -m py_compile manual_factor_pool/baselines passed
git diff --check                               passed
v6 health                                      6.5.0.dev0，M8 executed=true
```

今天没有读取、比较、重跑或修改 v6 已消费的 2026-01-01..2026-07-17 OOS。

## 五、尚未完成与下一步

今天只完成候选池物化和消融前置，没有运行 114 列的大规模 LOFO，也没有用任何新结果改写人工决定。下一步固定为：

1. 人工检查 `B2_ABLATION_GROUPING_2026-08-27.csv` 中 103 行信息类型回退，确认关键词归类后再登记运行表。
2. 在 selection period 运行完整集与 `family_of()` / `info_type_of()` 整组移除；主线为 B2 Huber 原始秩和 B2 中性秩 LambdaRank，3 折 × 3 种子、20 日 purge gap。
3. 结果使用同一 fold/seed 的配对差，并在训练前固定噪声带；不做逐因子反复试错。
4. 冻结幸存清单后，evaluation 期只验证一次；排序质量稳定后才进行十分位、IC 衰减、Top-K 净收益、右尾捕获、regret 和上证超额验收。
