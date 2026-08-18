# 执行计划

> 项目：`fusion-factor-pipeline`
> 版本：v1（2026-08 立项）
> 依据：`独立评审意见与复现检验.md`、`v6_下一代路线纪要_v2.md`、`rank_profitability_audit.md`

## 0. 阶段总览

| 阶段 | 交付 | 验收 | 状态 |
|---|---|---|---|
| P0 | bridge/data.py + bridge/panel.py | 四数据坑断言全过；面板契约通过 | 完成 |
| P0.5 | bridge/gbdt.py purged WF 判官 | 复现 legacy > v6 已知结论（仅作 harness 校准） | 完成 |
| P1 | QuantGPT 表达式 8–10 个 | 表达式可算、缺失率达标、方向预注册 | 完成（starter 降级） |
| P2 | 全市场面板 + walk-forward | 筛选/消融先通过 IC、NDCG、HAC IC_IR 和折间一致性；冻结后再验收 Top-K | 主指标已补齐；排序地基尚未稳定 |
| P3 | v6 官方锁箱前向观察 | 工程完整 + R 分布未退化 | 观察锁箱运行中 |

> 2026-08 用户口径变更：legacy_score 回测引擎有已知记账问题，不再作为硬验收门槛；**因子冻结后的验收期硬验收 = 跑赢上证指数**。筛选/消融期先验证排序质量，legacy_score 仅保留为诊断参照。

## 1. 纪律（执行期不可破坏）

1. 数据预算：2023-06-01~2025-12-31 开发集；2026-01-01~07-17 永久消费；干净数据自 2026-07-18。
2. 单次揭盲：每轮“挖→算→训→验”是完整实验，OOS 结果不回传调参。
3. 锁箱通过 = 进入前向观察/小资金跟踪，不是 alpha 已验证。
4. **验收分两阶段**：筛选/消融期主看按日截面 IC、NDCG、HAC IC_IR 和折间一致性；因子冻结后的验收期才要求 Top-K 组合跑赢上证指数（000001.SH），legacy_score 只作诊断。
4. post-hoc 数字不得选候选、改权重、切排序器。
5. v6 CSV 为前复权；桥接层统一 baostock qfq + 单位/PIT 断言。
6. Top-K 评估复用 `aurumq_rl.data_loader.build_tradeable_mask`。
7. RL 不进主判官；主判官为自建 LightGBM。

## 2. P0 数据底座

- 产出：`data/panel_qfq.parquet`，满足 aurumq-rl 契约。
- 必堵四坑：vwap 单位、group_rank 静默降级、ts_corr NaN/inf、市值代理。
- 历史股票池：显式 listing/delist 日期，防幸存者偏差。
- 验收：`bridge/test_data_pit.py` 全过；`FactorPanelLoader` 可读。

## 3. P0.5 判官校准

- 已完成：v6 冻结审计复现，结果见 `a-share-factor-engine-v6/artifacts/rank_profitability_audit/report.md`。
- 待做：`pip install lightgbm`；实现 `bridge/gbdt.py`；用 v6 四因子作为特征、legacy_score 作为已知基线，验证判官重现“legacy > v6”。
- 验收：binding 候选上 legacy Top1/3/5 净R 与审计一致（1.1723/0.6604/0.4516），v6 为 0.6937/0.5693/0.4004。

## 4. P1 QuantGPT 挖矿

- 只取表达式文本 + 假设 + 预注册方向；弃用其回测分数。
- 禁 Rust 求值、自动翻方向、akshare 混源刷新。
- 产出 `expressions.json`（8–10 个）。

## 5. P2 全市场面板与判官

- 面板列：v6 四因子 + legacy 八项 + QuantGPT 表达式 + aurumq-rl 296 量价因子。
- 判官：LightGBM 回归集成（top-3 配置 × 3 种子），purged walk-forward，gap ≥ 持有期。
- 主验证：筛选/消融期每折日截面 IC、HAC IC_IR、NDCG 和折间稳定性；Top-K 前瞻收益、上证超额、右尾捕获和 regret 仅作附录。因子冻结后的验收期再把这些 Top-K 指标升为主判据。

## 6. P3 v6 官方锁箱 + daily_ops

- 使用 v6 官方 `final-test-freeze / append / evaluate`。
- 通过 = 进入前向观察；不通过 = 整轮进 failures。
- 开发者观察使用本项目 `bridge/dev_lockbox.py`，不消耗官方揭盲。

## 7. 里程碑

| 门 | 条件 | 未过处理 |
|---|---|---|
| G0 | 四数据坑断言全过 | 禁入 P0.5 |
| G1 | 判官复现已知答案 | 修 harness |
| G2 | 冻结后的验收期 Top-K 跑赢上证指数 | 进 failures，不调参 |
| G3 | 锁箱工程完整 + R 未退化 | 整轮废弃 |

## 8. 风险

| 风险 | 缓解 |
|---|---|
| 表达式语义漂移 | 锁 parser 版本 + 哈希 |
| 幸存者偏差 | listing/delist 显式 |
| vwap 单位混用 | P0 断言 |
| harness bug | P0.5 已知答案先行 |
| 自欺 | 单次揭盲 + post-hoc 纪律 |
