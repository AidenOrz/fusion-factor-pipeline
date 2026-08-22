# 2026-08-22 工作报告：全市场因子筛选闭环

项目：`fusion-factor-pipeline`
日期：2026-08-22
性质：开发集研究与工程实施记录，不构成生产批准、实盘授权或收益承诺。

## 一、阶段结论

本阶段已经把因子处理主线从旧 `events + EXRET20` 口径迁移到全市场 B2 横截面排序口径，并完成代码审阅修复、上游路径治理、静态因子体检、方向风险隔离和正式结果重算。

当前可以进入下一阶段预注册式整组消融，但有一个明确前置事项：40 个静态合格因子仍处于人工方向裁决状态。在裁决完成前，它们不会进入候选清单，程序也不会自动翻转方向。

本轮没有推进 Top-K 专精或收益验收。原因不变：当前阶段的任务是先建立可复现、无选择期泄漏的排序因子地基；Top-K 仍属于冻结因子后的验收指标。

## 二、战略口径

主任务固定为：在每日全市场可交易股票域内，预测下一期横截面超额收益排序。当前主选择配置为：

```text
panel_scope = full_market
universe    = b2
target      = y_neutral_rank20_b2
selection  = 2023-06-30 至 2024-09-12
label_gap  = 20 个交易日
evaluation = 2024-10-22 起
```

旧事件面板只保留为 legacy 诊断，不能参与主因子选择、主训练或方向继承。2026-08-18 基于旧口径形成的人工决定没有被自动迁移；涉及的因子改为显式暂扣并输出新 B2 证据，等待重新裁决。

方向处理遵守以下纪律：

- 不根据选择期 IC 自动对因子取负号。
- 市场风格反号或频繁换向的因子先排除，再决定是否建立独立的预注册研究。
- 低幅度反号不占人工审阅资源。只有 `max(abs(recent_ic), abs(historical_ic)) > 0.02` 才进入 `regime_sensitive`。
- evaluation 期只允许冻结清单后一次性验证，不用于反复增删因子。

## 三、工程实施

### 3.1 代码审阅问题修复

- 将 20 日前向标签对应的 purged gap 修正为 20 个交易日。
- 修复 `ts_corr` 临时列原地写入调用方 DataFrame 的副作用。
- 为 AurumQ 因子输出增加行数一致性检查，避免失败跳过后按行位置拼接错位。
- 未知行业不再以 `industry_code=-1` 形成虚假行业组排名。
- LLM 空内容错误按 provider 响应结构处理，Anthropic 响应不再错误访问 OpenAI 的 `choices` 字段。

### 3.2 上游路径治理

新增 `bridge.paths`，兄弟仓库与行情目录改由以下环境变量显式提供，并在真正读取前校验目录和稳定标志：

```text
A_SHARE_FACTOR_ENGINE_V6_ROOT
AURUMQ_RL_ROOT
QUANTGPT_ROOT
A_SHARE_KLINE_DATA_ROOT
```

启动检查已接入面板、AurumQ、GBDT、benchmark、LLM miner、K 线读取及相关 CLI。路径配置说明见 `docs/UPSTREAM_ENVIRONMENT.md`。此改动解决了复制仓库或改变目录层级后静默读取错误兄弟仓库的具体失败场景。

### 3.3 全市场 B2 因子选择

`bridge.factor_selection` 现在：

- 拒绝 `panel_scope=events` 进入主选择流程；
- 按 `(date, code)` 显式连接 QuantGPT、Alpha101 和 GTJA191 因子文件；
- 拒绝外部因子文件重复主键；
- 只读取选择所需列，并使用 DuckDB 合并外部 parquet，控制全市场运行的内存峰值；
- 先执行覆盖率、截面区分度和 `abs(IC) >= 0.01` 静态规则，再执行方向隔离和相关性去冗余；
- 分离 `static_selection_eligible` 与 `candidate_eligible`，避免把静态质量与待裁决状态混为一类；
- 输出 `direction_review.csv`、`direction_adjudication_queue.csv`、覆盖率诊断和完整 manifest；
- 只允许最终可用代表进入截面归一化、行业中性化和交叉项候选。

## 四、正式运行结果

正式运行读取 316 个因子、296 个交易日、560,873 行和 2,243 只股票。漏斗结果如下：

| 阶段 | 数量 | 说明 |
|---|---:|---|
| 输入因子 | 316 | 全市场 B2 选择期 |
| 静态合格 | 217 | 通过截面质量和 IC 下限 |
| 静态排除 | 99 | 74 个弱 IC、24 个截面不足、1 个无有效日 |
| 方向暂扣后的候选 | 177 | 40 个静态合格因子待人工裁决 |
| 相关性去冗余后 | 105 | 固定 `abs(rho) > 0.8` 阈值 |

方向幅度门槛把 40 个原始近期/历史反号拆分为：

- 19 个 `no_significant_signal`，不再触发人工审阅；
- 21 个高幅度 `regime_sensitive`；
- 其中 13 个通过静态筛选，进入人工裁决；
- 7 个未通过静态 IC 下限，只保留为非候选研究观察。

`alpha010` 和 `gtja_100` 等近零 IC 因子已不再因微小反号占用人工队列。用户点名的 `alpha004`、`alpha055`、`alpha059`、`alpha091`、`alpha093`、`gtja_058`、`gtja_125`、`gtja_181` 均未进入最终 105 因子清单。

完整解释见 `docs/FACTOR_SELECTION_B2_REPORT_2026-08-21.md`。正式结果保存在 `runs/factor_selection_v3_b2_neutral_selection_full_322/`，该目录按仓库规则不提交 Git。

## 五、人工裁决状态

当前共有 40 个 `manual_decision`：

| 原因 | 数量 | 当前建议 |
|---|---:|---|
| 旧口径人工决策冲突 | 12 | 逐项重新注册或继续排除 |
| 注册方向与 B2 建议冲突 | 9 | 未批准前排除；当前建议均为 short |
| 高幅度 regime 反号 | 13 | 本轮删除，不自动翻转 |
| 方向不稳定 | 6 | 原则上删除，除非另有先验机制 |

另有 7 个高幅度反号因子因为 `abs(IC) < 0.01` 标为 `research_observation_not_candidate`。它们不是当前需要选择方向的候选。

## 六、验证证据

本阶段已经完成：

```text
tests/test_factor_selection_full_market.py: 9 passed
目标回归测试集: 28 passed
tests/run_review_smoke.py: passed
tests/run_training_plan_smoke.py: passed
python -m py_compile bridge/factor_selection.py: passed
git diff --check: passed
```

正式全市场 B2 选择流程已完成三次一致性重跑，最终均得到 105 个代表因子。v6 健康检查确认版本 `6.5.0.dev0` 且 M8 已执行；本阶段没有读取、比较、重跑或修改已消费的 M8 OOS。

## 七、提交边界与下一步

本次 Git 提交包含可复现的工程代码、测试、执行计划和报告，不包含大体积 parquet、`runs/` 运行结果或一次性研究探针。

下一步按以下顺序进行：

1. 用户裁决 `direction_adjudication_queue.csv` 中的 40 个 `manual_decision`。
2. 将批准结果人工写入因子配置，生成下一轮预注册候选清单。
3. 对冻结候选执行 leave-one-family-out 和信息类型消融。
4. 只在因子清单冻结后做一次 evaluation 验证。
5. 排序质量稳定后，再进入十分位、IC 衰减和 Top-K 净收益验收。
