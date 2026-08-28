# 执行登记：B2 剪后确认与结构诊断（2026-08-28）

本登记承接 `EXECUTION_PLAN_FREEZE_2026-08-27.md`，只描述 selection-only 开发集步骤，不构成生产批准、实盘授权或 evaluation 解锁。

## 已注册输入

- 面板：`data/panel_fusion_b2_adjudicated_2026-08-27.parquet`，`panel_scope=full_market`，`universe_b2`。
- 全集：`config/factor_pool_b2_adjudicated_2026-08-27_features.json`，114 个因子，作为 8/27 裁决配置的只读参考。
- 剪后候选：`config/factor_pool_b2_post_lofo_candidate_2026-08-28_features.json`，35 个因子；不覆盖全集配置。
- 标签：`y_raw_rank20_b2` 与 `y_neutral_rank20_b2`，均为 20 日横截面秩；purge gap 固定 20 个交易日。
- 模型：两条同配置 `LGBMRegressor(objective='huber')`，种子 42/43/44，3 个有效 expanding folds。

## 交叉重叠审计规则

`docs/B2_CROSS_OVERLAP_AUDIT_2026-08-28.csv` 逐因子记录 family 与 info-type 的来源、LOFO 判定和最终动作。

1. 人工批准的 `manual__alpha_qg_f001__short`、`manual__alpha_qg_f008__short` 豁免最高，不能被 `momentum` 整组删除覆盖。
2. `gtja191`、`starter` family 与 `capital`、`momentum`、`volatility` info-type 的移除候选取并集。
3. `alpha_qg_f009` 的 llm family 保留证据不覆盖 momentum 信息类候选，故列为删除候选；该冲突在审计表中显式记录。
4. `manual__alpha_qg_corr_pv_10__short` 因 starter family 候选而删除；其单因子 IC 强、方向稳定不构成模型边际价值证据。

结果：114 个因子中 79 个删除候选、35 个保留，未自动翻转方向、未修改生产配置。

## 剪后确认

先执行 `python bridge/confirmation.py register` 写入 manifest，再执行 `python bridge/confirmation.py run`。确认实验只使用 selection 期，生成两池两模型的严格折外预测、指标、十分位表和 horizon IC 表。

通过必须同时满足两条主线：

- 剪后日均 Spearman IC ≥ 全集 IC − 完整集已登记的 1σ 噪声带；
- 剪后 HAC IC_IR 不低于全集；
- 剪后折间 IC 符号一致性为 1.0；
- Q1、Q10、Q10−Q1 与十分位单调 Spearman 的符号结构与全集一致。

失败只允许一次预注册的罪魁祸首 family 放回重试；仍失败则回到描述阶段，不逐因子调参。

## 并行结构诊断

同一严格折外预测同时输出：

- 十分位单调性：每日按预测分分 10 组，输出 `y_excess20`、真实秩及可用暴露；只用于在 evaluation 前确定 B/C 验收轨和组合宽度。
- horizon IC 衰减：1/5/10/20/40 日前向超额收益；每个 horizon 的退出日期必须 ≤ `2024-09-12`，避免把标签延伸到 evaluation。

上述诊断统一标记 `post_hoc_descriptive_only`，不得反向增删因子或修改人工方向。

## 下一步边界

只有确认通过且验收轨/horizon 已登记，才允许创建冻结配置并在 evaluation 期一次性运行。确认未通过时不进入 evaluation。
