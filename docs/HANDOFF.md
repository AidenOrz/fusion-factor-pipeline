# HANDOFF — 当前稳定点与下一步

> 更新时间：2026-09-05
> 状态：37 因子池的内部 Evaluation 历史记录保留；本轮发现并修复 C 轨费用、首日收益、机会成本执行规则等遗漏。旧 C 轨 passed 是原实现的历史结果，不能证明修复版通过。未重跑历史验收，`production_authorized=false`。时间边界修复早已提交，不再是待办。

## 本轮修复与恢复位置（2026-09-05）

- 修改范围：`bridge/c_track_acceptance.py`、`tests/test_c_track_acceptance.py` 和本交接；未提交。开始时的五个未跟踪文件保持原样。
- 已验证并修复：买入费用重复扣除；首日收益、年化和首日相对回撤遗漏；分期统计丢失首日收益；50/50 指数抹掉首日收益；晚入场与退出同时延迟时绕过计划窗口；外部日历排除价格行后 asof 键类型不一致；无 cohort_size 时未成交资金被重分配；实时剔除不应依赖未来标签。
- 机会成本原实现仅使用无费用买入持有指数，与预注册的同费用、同持有规则不符。验收调用现传入信号日及市场日历，复用 20 日队列和 1x 费用模拟。未传信号日的旧诊断接口仍保留被动指数曲线。
- 复现证据：前八个新增测试在修改实现前均失败；固定价单笔交易原净值 `0.9940079840`，正确单边各 0.2% 后应为 `0.9960079840`。旧固定价指数净值为 `1.0`，单个 1/20 队列应为 `0.9998003992`。这些都是合成数据，不是历史验收重跑。
- 验证：C 轨 19 项测试通过；P0 冒烟和 `tests/run_bugfix_smoke.py` 通过。完整回归的最终结果见本轮完成记录。P0 需要在当前进程设置 `A_SHARE_KLINE_DATA_ROOT`，本次核实目录为 `C:\Users\Lenovo\Documents\New project\a-share-kline-data`。
- 重要边界：未修改冻结配置、模型或历史 runs；未新增 hash、contract、baseline 或 gate。下文的历史通过数值仅描述原运行，不是修复版验收结果。
- 未完成：冻结三种子评分器保存及预测等价校验；行业历史 PIT 数据；新前向观察日期与执行；修复版历史验收的处理裁决。本轮没有足够依据宣称项目所有潜在 bug 均已清除。
- 下一步：先决定旧 C 轨历史验收在新缺陷证据下如何处理；继续实现评分器保存和纯预测等价验证；从预先声明的新日期启动影子观察。不要直接重跑历史验收。

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
  - `runs/c_track_acceptance_v1_b2_frozen_2026-08-28/`：追认的有效 C 轨内部历史经济验收，机制层与机会成本层均通过。
  - `docs/WORK_REPORT_2026-09-01_C_TRACK_ACCEPTANCE_ADJUDICATION.md`：四个决策产物、实现缺陷例外、有效运行与未改变的生产边界。
- 观察锁箱：
  - `artifacts/dev_lockbox/dev/`：active，3 个非空 live 信号日已追加，`verify` 通过。

## 2. 已验证事实

- baostock 与 WeStock 均可直连，不需要代理。
- lightgbm 4.7.0 已安装，判官用 `LGBMRegressor`，支持 NaN 原生处理。
- DeepSeek v4-flash/pro 是唯一模型；`deepseek-chat` 是旧别名。
- LLM 每次运行有完整记录：`runs/llm_miner/<UTC>/`。
- 2026-08-28 v6 健康检查：版本 `6.5.0.dev0`，M8 `preflight/started/executed` 均为 true；本轮 B2 Evaluation 的 `clean_2026_oos_access=false`。
- C 轨最终有效运行：毛年化超额 `3.367%`、1x 年化超额 `3.384%`、2x 累计超额 `4.031%`、1x 相对最大回撤 `0.663%`；50/50 机会成本年化超额 `4.660%`。这是一项用户明确授权的、仅限本次实现缺陷的追认，不是未来历史重跑的通行证。

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
- [x] 冻结 C 轨经济验收：最终 `325f458` 运行通过机制层与机会成本层。两个早期 failed 产物因未实现“保留池等权”的资金分母错误，经 2026-09-01 用户明确授权，追认为不消费结果；完整边界见 C 轨裁决工作报告。
- [x] 前向观察前的时间边界修复（`65c0c03`）：`EXRET{N}` 目标必须使用 `N` 个交易日的 purge gap；selection/evaluation 以真实交易日历要求至少 `horizon + 1` 个日期位置差。已在冻结面板确认 `2024-09-12 -> 2024-10-22` 合法。
- [x] C 轨生产路径已复用同一底部剔除函数，并在单次验收内复用 close 矩阵；Top-K 附录只模拟实际报告的 0x 与 1x 成本，避免无输出的成本档位重复计算。
- [ ] 重新物化并保存冻结 neutral Huber 三种子评分器，先与既有 Evaluation `predictions.parquet` 做等价校验，再从新的预先声明日期开始只读影子观察。

## 4. 恢复入口

```powershell
cd C:\Users\Lenovo\Documents\New project\fusion-factor-pipeline
python tests/run_p0_smoke.py
Get-Content -Raw runs/factor_evaluation_v1_b2_frozen_2026-08-28/evaluation_decision.json
Get-Content -Raw docs/B2_EVALUATION_REPORT_2026-08-28.md
Get-Content -Raw docs/WORK_REPORT_2026-09-01_C_TRACK_ACCEPTANCE_ADJUDICATION.md
Get-Content -Raw runs/c_track_acceptance_v1_b2_frozen_2026-08-28/acceptance_decision.json
```

不要再次执行 `python bridge/evaluation.py run`：`evaluation_decision.json` 已存在，本次一次性 Evaluation 已消费。
不要再次执行 C 轨历史验收：本次追认只确认已完成的最终运行，不授权任何新的历史重跑。

无凭据保存在项目文件；DeepSeek key 在 `QuantGPT/.env`（gitignored）。
