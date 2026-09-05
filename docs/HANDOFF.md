# HANDOFF — 当前稳定点与下一步

> 更新时间：2026-09-05（复算定案后）
> 状态：C 轨经济验收已按 `C_TRACK_RECAST_PREREG_2026-09-05.md` 完成单次复算并**定案通过**（机制层与机会成本层均过，`production_authorized=false`）。v1 运行改分类为历史参照。下一步：评分器等价校验 → 前向观察协议预注册 → 只读影子观察。

## 本轮修复与复算定案（2026-09-05）

- 09-05 上午：修复 C 轨模拟器 9 项缺陷并提交 `f5ca31f`（费用双扣、首日收益、分期首日、混合指数首日、晚入场边界、asof 键类型、未成交资金、实时剔除未来标签、机会成本同费用同持有规则），证据与边界见 `docs/BUGFIX_REPORT_2026-09-05.md`。
- 09-05 下午：用户授权复算。登记提交 `d858d1c`（复算预注册 + manifest/decision 内嵌 `code_commit` + 端到端合成验收测试）；同日以该冻结点单次运行 `runs/c_track_acceptance_v2_recast_2026-09-05/`，产物哈希与登记提交一致，一次落盘无重跑。
- 复算结论：机制层毛年化超额 +3.353%（门槛 2%）、1x 净 +3.360%（门槛 1%）、2x 累计 +4.030%、回撤 0.659%（上限 5%）、三段分期两段以上为正——五项门全过；机会成本 50/50 混合净超额 +6.374% > 0（v1 为 +4.660%，指数现承担同款队列费用）。466,402 笔交易与剔除尾部（51,970 股票日、均值 -1.952%）与 v1 完全一致。
- v1 处置：`runs/c_track_acceptance_v1_b2_frozen_2026-08-28/` 及 attempt 目录改为历史参照；09-01 裁决报告加 superseded 标头；08-28 预注册第八节加指针。逐项对照见 `docs/WORK_REPORT_2026-09-05_C_TRACK_RECAST.md`。
- 边界：复算未消费任何新数据区间；未修改冻结配置、模型、历史 runs；任何登记条款变更须重新完整预注册。
- P0 冒烟需在当前进程设置 `A_SHARE_KLINE_DATA_ROOT`，核实目录为 `C:\Users\Lenovo\Documents\New project\a-share-kline-data`。

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
  - `runs/c_track_acceptance_v1_b2_frozen_2026-08-28/`：历史参照（原实现产物，已被 09-05 复算取代为有效结果）。
  - `docs/WORK_REPORT_2026-09-01_C_TRACK_ACCEPTANCE_ADJUDICATION.md`：四个决策产物、实现缺陷例外与时间线考据（已加 superseded 标头）。
  - `runs/c_track_acceptance_v2_recast_2026-09-05/`：**C 轨经济验收当前有效结果**（机制层与机会成本层均通过，manifest/decision 内嵌 commit `d858d1c`）。
  - `docs/C_TRACK_RECAST_PREREG_2026-09-05.md` / `docs/WORK_REPORT_2026-09-05_C_TRACK_RECAST.md`：复算登记、执行记录与新旧对照。
- 观察锁箱：
  - `artifacts/dev_lockbox/dev/`：active，3 个非空 live 信号日已追加，`verify` 通过。

## 2. 已验证事实

- baostock 与 WeStock 均可直连，不需要代理。
- lightgbm 4.7.0 已安装，判官用 `LGBMRegressor`，支持 NaN 原生处理。
- DeepSeek v4-flash/pro 是唯一模型；`deepseek-chat` 是旧别名。
- LLM 每次运行有完整记录：`runs/llm_miner/<UTC>/`。
- 2026-08-28 v6 健康检查：版本 `6.5.0.dev0`，M8 `preflight/started/executed` 均为 true；本轮 B2 Evaluation 的 `clean_2026_oos_access=false`。
- C 轨当前有效结果（v2 复算，commit `d858d1c`）：毛年化超额 `3.353%`、1x 年化超额 `3.360%`、2x 累计超额 `4.030%`、1x 相对最大回撤 `0.659%`；50/50 机会成本年化超额 `6.374%`。v1 数值（毛 3.367%/净 3.384%/回撤 0.663%/机会成本 4.660%）仅作历史参照。任何后续条款变更须重新完整预注册。

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
- [x] 冻结 C 轨经济验收：2026-08-28 首次执行，两个早期 failed 产物经 09-01 追认为不消费；09-05 缺陷复核后按复算预注册单次重算并定案通过，v1 改历史参照。
- [x] 前向观察前的时间边界修复（`65c0c03`）：`EXRET{N}` 目标必须使用 `N` 个交易日的 purge gap；selection/evaluation 以真实交易日历要求至少 `horizon + 1` 个日期位置差。已在冻结面板确认 `2024-09-12 -> 2024-10-22` 合法。
- [x] C 轨生产路径已复用同一底部剔除函数，并在单次验收内复用 close 矩阵；Top-K 附录只模拟实际报告的 0x 与 1x 成本，避免无输出的成本档位重复计算。
- [x] C 轨复算定案（2026-09-05）：登记提交 `d858d1c`、单次运行通过、manifest 内嵌 commit、v1 改历史参照；执行合规与逐项对照见复算预注册第八节与 `WORK_REPORT_2026-09-05_C_TRACK_RECAST.md`。
- [ ] 重新物化并保存冻结 neutral Huber 三种子评分器，先与既有 Evaluation `predictions.parquet` 做等价校验。
- [ ] 前向观察协议预注册（观察窗口、评估节奏、过/不过判据、失败处置），然后从预先声明的新日期开始只读影子观察；不得回填已可见日期。

## 4. 恢复入口

```powershell
cd C:\Users\Lenovo\Documents\New project\fusion-factor-pipeline
python tests/run_p0_smoke.py
Get-Content -Raw runs/factor_evaluation_v1_b2_frozen_2026-08-28/evaluation_decision.json
Get-Content -Raw runs/c_track_acceptance_v2_recast_2026-09-05/acceptance_decision.json
Get-Content -Raw docs/C_TRACK_RECAST_PREREG_2026-09-05.md
Get-Content -Raw docs/WORK_REPORT_2026-09-05_C_TRACK_RECAST.md
```

不要再次执行 `python bridge/evaluation.py run`：`evaluation_decision.json` 已存在，本次一次性 Evaluation 已消费。
不要再次执行 C 轨验收：复算已定案（`runs/c_track_acceptance_v2_recast_2026-09-05/`），重跑需重新完整预注册并取得新的用户授权。

无凭据保存在项目文件；DeepSeek key 在 `QuantGPT/.env`（gitignored）。
