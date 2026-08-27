# PROJECT — Fusion Factor Pipeline 项目长期依据

> 更新时间：2026-08
> 依据：用户拍板与已执行的工程结果。

## 1. 项目目标与边界

目标：把 QuantGPT（表达式挖矿）、aurumq-rl（因子库与工程件）、a-share-factor-engine-v6（事件门、基准、锁箱纪律）组装为可复现、防过拟合的 A 股 Top-K 选股流水线。

边界：

- 不是实盘交易系统；不做券商下单。
- 2023-06-01~2025-12-31 是已消费开发集；2026-01-01~07-17 永久消费；干净数据自 2026-07-18 起算。
- 所有开发集结论为 `post_hoc_descriptive_only`，不得直接选候选或改生产配置。

## 2. 已批准的关键决策

1. **验收基准 = 跑赢上证指数（000001.SH）**。legacy_score 因历史回测引擎记账问题只作诊断参照，不作硬门槛。
2. **锁箱语义**：官方 v6 锁箱保持单次揭盲；本项目开发者观察锁箱允许无限次 `observe`，`formal-reveal` 每 epoch 一次。锁箱通过只表示“进入前向观察”，不等于 alpha 验证。
3. **数据口径**：v6 CSV 为前复权；桥接层统一 baostock 前复权数据；volume 统一为股；vwap=amount/volume；市值=close×总股本（baostock `totalShare`，按 `pubDate` as-of）。
4. **因子生产**：模型只生成表达式文本，收益由本项目判官评估。DeepSeek 当前可用模型为 `deepseek-v4-flash` 与 `deepseek-v4-pro`；支持 openai/anthropic provider。
5. **主判官**：lightgbm `LGBMRegressor` + purged walk-forward（gap≥10 交易日）；筛选/消融期主看按日截面 IC、NDCG、HAC IC_IR 和折间一致性，因子冻结后的验收期再以单笔净R/Top-K 前瞻收益跑赢上证指数为硬验收。
6. **RL 不进主判官**；aurumq-rl 的 PPO/A2C/SAC 保留为外部研究。

## 3. 因子池现状（2026-08）

- 完整池：v6 四因子 + 股本/市值 + 107 alpha101 + 191 gtja191 + 18 starter/LLM = 316 列。
- 体检结论：v6 与 capital 平均 |IC| 最高；alpha101/gtja191 覆盖广但冗余重。
- 相关性去重（|ρ|>0.95 聚类）保留 80；SHAP 剪枝后保留 60。
- 开发集上完整池、80 池、60 池均未跑赢上证指数。该事实只用于设计下一轮预注册候选，不得反向选因子。
- 精简池（v6 四因子 + 18 表达式 + 股本/市值）曾在开发集 Top1 超额 +0.0111；同样是 post-hoc。
- 2026-08-27 B2 人工裁决候选池登记在 `config/factor_pool_b2_adjudicated_2026-08-27.json`：105 个 B2 代表加 9 个显式批准方向列，共 114 个预注册消融候选；`alpha052`、`gtja_183` 仅观察。该池尚未通过消融或 evaluation 验收，不是生产配置。

## 4. 长期约束

- 上游代码版本、数据快照、因子表达式和训练脚本都必须可哈希复现。
- 行业历史 PIT 免费源不可得；当前只有快照，使用必须标注 `snapshot_not_historical`。
- 不得把 live 状态回填历史面板；Top-K 必须落在可成交集合（停牌/ST/新股/涨跌停）。
- 每次“挖→算→训→验”为一次完整实验；OOS 结果不回传调参。
- 新的生产候选必须在 2026-07-18 后干净数据上同时通过：跑赢上证指数 + 工程完整 + R 分布未退化。
