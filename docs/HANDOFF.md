# HANDOFF — 当前稳定点与下一步

> 更新时间：2026-08-28
> 状态：37 因子池已通过并消费一次性内部 Evaluation；C 轨评分源已固定为 neutral 单模型，验收基准冲突已于 2026-08-28 裁决。下一步为冻结 C 轨经济验收。

## 1. 最后稳定点

- 数据：
  - `data/refdata/shares_pit.parquet`：baostock 全市场股本 PIT，49,965 行 / 3,163 股。
  - `data/panel_fusion_full.parquet`：316 因子池，52,088 行 × 2,949 股。
  - `data/aurumq_alpha101_full.parquet` / `data/aurumq_gtja191_full.parquet`：aurumq-rl 因子全量。
- 因子筛选：
  - `runs/factor_selection/`：IC 表、族汇总、相关性矩阵、80 特征、60 特征、报告。
- 训练：
  - `runs/gbdt_walkforward_full/`（316）
  - `runs/gbdt_walkforward_reduced80/`（80）
  - `runs/gbdt_walkforward_pruned60/`（60）
- B2 冻结与 Evaluation：
  - `config/factor_pool_b2_frozen_2026-08-28.json`：37 因子、20 日标签、C 轨剔除底部 10%。
  - `docs/C_TRACK_ACCEPTANCE_PREREG_2026-08-28.md`：已预注册使用 `huber_b2_neutral_rank` 单模型；raw rank 仅作对照附录。
  - `runs/factor_evaluation_v1_b2_frozen_2026-08-28/`：一次性内部历史留出结果，`evaluation_consumed=true`、`evaluation_pass=true`、`production_authorized=false`。
  - `docs/B2_EVALUATION_REPORT_2026-08-28.md`：结果、边界和下一步的可读报告。
  - `docs/WORK_REPORT_2026-08-28_B2_FREEZE_EVALUATION.md`：冻结评审、因子往返、Evaluation 证据与 C 轨选择依据。
- 观察锁箱：
  - `artifacts/dev_lockbox/dev/`：active，3 个非空 live 信号日已追加，`verify` 通过。

## 2. 已验证事实

- baostock 与 WeStock 均可直连，不需要代理。
- lightgbm 4.7.0 已安装，判官用 `LGBMRegressor`，支持 NaN 原生处理。
- DeepSeek v4-flash/pro 是唯一模型；`deepseek-chat` 是旧别名。
- LLM 每次运行有完整记录：`runs/llm_miner/<UTC>/`。
- 2026-08-28 v6 健康检查：版本 `6.5.0.dev0`，M8 `preflight/started/executed` 均为 true；本轮 B2 Evaluation 的 `clean_2026_oos_access=false`。

## 3. 尚未完成 / 待决策

- [ ] 行业历史 PIT：需交易所/指数公司历史快照或付费数据。
- [ ] G2 未通过：当前任何因子池在开发集都未稳定跑赢上证指数，不得进 v6 官方锁箱。
- [x] 2026-08-27 B2 人工裁决已落地为 114 列预注册候选池；物化器已验证全市场键连接、short 变换和旧 events 拒绝。
- [x] 2026-08-28 已审阅旧 LOFO 的方向盲区与 LambdaRank 噪声问题；旧结果仍保留为历史证据。
- [x] 2026-08-28 已准备并登记新实验：`runs/factor_ablation_v4_b2_huber_cross_label_2026-08-28/`，两条主线均为 Huber，仅标签不同。
- [x] 2026-08-28 已完成 v4 两条完整集基线、family LOFO 和 info-type LOFO；方向字段与组级决策已落盘。
- [x] 35 因子首轮确认 raw IC 未过；按预注册规则唯一一次放回 `starter` 后，37 因子池双 Huber 确认通过。
- [x] 冻结评审选择 C 主轨、底部 10% 剔除、20 日标签；`gtja_090` 明确维持出局。冻结配置为 `config/factor_pool_b2_frozen_2026-08-28.json`。
- [x] 一次性内部 Evaluation 已消费并通过：raw/neutral IC `0.134586/0.125299`，HAC IC_IR `0.255165/0.393278`，两线三个时间块均为正且十分位结构保持。Evaluation 结果不得用于修改因子、方向、horizon 或验收轨。
- [ ] 下一步只做冻结 C 轨经济验收：
  1. 按 `C_TRACK_ACCEPTANCE_PREREG_2026-08-28.md` 已登记的 neutral 单模型、滑点、调仓频率、20 日重叠持仓和停牌/涨跌停成交语义执行。
  2. 以 B2 域等权为唯一第一层机制硬基准，报告扣费后的年化超额、分期稳定性、超额回撤、换手和 R 分布。
  3. 同时登记中证 1000、中证 2000、50/50 混合净超额；50/50 混合净超额 `> 0` 为次级判定，不能否决机制层；上证指数只进附录。
  4. 同批生成 raw 对照与 A 轨 Top-5/Top-10 附录，不以其结果改轨或改模型。

## 4. 恢复入口

```powershell
cd C:\Users\Lenovo\Documents\New project\fusion-factor-pipeline
python tests/run_p0_smoke.py
Get-Content -Raw runs/factor_evaluation_v1_b2_frozen_2026-08-28/evaluation_decision.json
Get-Content -Raw docs/B2_EVALUATION_REPORT_2026-08-28.md
```

不要再次执行 `python bridge/evaluation.py run`：`evaluation_decision.json` 已存在，本次一次性 Evaluation 已消费。

无凭据保存在项目文件；DeepSeek key 在 `QuantGPT/.env`（gitignored）。
