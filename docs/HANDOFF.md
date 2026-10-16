# HANDOFF — 当前稳定点与下一步

> 更新时间：2026-10-17（阶段 D 数据地平线实测；D-4/D-5 前置受限，见 §3）
> 状态：C 轨经济验收已定案通过（2026-09-05 recast）。此后六周无新实验。下一主目标：**启动只读影子前向观察**；并行项：行业历史 PIT 集成。`production_authorized=false` 维持不变。

## 1. 最后稳定点（已验证，可直接依赖）

- **冻结资产**：`config/factor_pool_b2_frozen_2026-08-28.json`——37 因子、20 日标签、B2 域、C 轨剔除底部 10%、双 Huber 主线（评分源固定为 `huber_b2_neutral_rank` 单模型，种子 42/43/44）。
- **一次性内部 Evaluation（已消费，通过）**：`runs/factor_evaluation_v1_b2_frozen_2026-08-28/`，neutral/raw IC 0.1253/0.1346，HAC IC_IR 0.393/0.255，三块符号一致，十分位结构保持，Q1 可交易性 retention 1.06。结果不得用于修改因子、方向、horizon、验收轨。
- **C 轨经济验收（定案，当前唯一有效结果）**：`runs/c_track_acceptance_v2_recast_2026-09-05/`，commit 内嵌 `d858d1c`。机制层五门全过：毛年化 +3.353%（≥2%）、1x 净 +3.360%（≥1%）、2x 累计 +4.030%（>0）、超额回撤 0.659%（≤5%）、三段分期全正；机会成本 50/50 混合 +6.374%（>0）。v1（`..._v1_b2_frozen_2026-08-28/` 及 attempt 目录）为历史参照。
- **基准确则（2026-08-28 裁决，两层制）**：机制层硬门槛 = B2 域等权；机会成本层 = 中证 1000/2000 及 50/50 混合（次级判定）；上证 = 纯附录。旧"跑赢上证"门槛已被 `docs/PROJECT.md` §2 决策 1 的取代记录替代。
- **数据预算**：开发集 2023-06-01~2025-12-31 已消费；2026-01-01~07-17 永久消费（v6 M8）；**干净数据自 2026-07-18 起算**。
- **工程**：pytest 72 项全绿（含 C 轨端到端合成验收测试）；冻结提交 `d053c6e`；C 轨模拟器缺陷史与修复见 `docs/BUGFIX_REPORT_2026-09-05.md`。
  - **2026-10-17 复核**：72 项**确认仍全绿**。注意本机直接跑 `pytest tests` 会得到 `44 passed, 28 errors`，28 个全是 setup 阶段 `PermissionError`（栈在 `_pytest/tmpdir.py`、`pytestqt`，无项目源码），根因与绕行命令见 `docs/knowledge/windows_sandbox_0o700_pytest.md`：沙箱下 `mkdir(mode=0o700)`（pytest 临时目录固定用法）生成的 DACL 连创建者都列不出。**这是环境缺陷，非代码回归**；用该知识条目的插件+basetemp 组合可复现 72 passed。
- **观察锁箱**：`artifacts/dev_lockbox/dev/` active，3 个非空 live 信号日（2026-08），verify 通过。v6 官方锁箱未消费。

## 2. 关键已批准决策（不得在本轮回退）

1. 定位：C 轨（底部剔除）为主验收轨；B 轨未选（selection 期 Q10 不显著）；A 轨 Top-K 仅附录（v2 扣费后 Top-5/10/20 为 -10%/-8.3%/-6.4%，深负）。
2. 筛选/消融期指标仅限 IC、HAC IC_IR、NDCG 增量、折间稳定性；Top-K 收益不作任何中间环节决策输入。
3. 冻结评审记录：`gtja_090` 维持出局；`corr_pv_10` 以 starter 放回身份入池；f001/f008 为 momentum 组内豁免。
4. 任何对因子清单、标签、模型参数、剔除比例、成本、基准、成交语义的变更 = 新一轮完整预注册。
5. 2026-09-01 实现缺陷例外为一次性裁决，不构成一般许可；后续一次性运行均须运行前登记 + 产物内嵌 commit。

## 3. 待办与阻碍

- [ ] **影子前向观察未启动**（最大缺口）：前置依赖——面板数据止于 2025-12-31，需刷新至当前；干净前向窗口（2026-07-18 起）已在流逝。
  - **2026-10-17 实测数据地平线（关键，阻塞 D-4）**：上游 v6 面板 `a-share-factor-engine-v6/artifacts/panel/panel.parquet`（1,968,826 行 / 3,178 只 / 629 天）**止于 2025-12-31**，与本地 `panel_fusion_full_market.parquet` 完全一致。`bridge/panel.py` 的日线源是**该上游面板**，不是 baostock，因此**上游不推进则全市场面板无法重物化**。本地 K 线归档（`A_SHARE_KLINE_DATA_ROOT`，3,200 个 csv）全局最大日期 **2026-07-24**（3,199 只 ≥ 2026-07-01，3,191 只 ≥ 2026-07-18）。baostock 会话实测**在线且已到 2026-10-16**（`sh.600000` 探针 28 个交易日命中至 2026-10-16）。**结论：面板刷新不是本项目内可独立完成的步骤，必须先由上游 v6 重建 panel.parquet 至 2026-07-24 及以后。**
  - **2026-10-17 根治性证据：这不是"上游偷懒"，而是上游治理规则明确禁止**。直接执行上游 CLI 被拒：
    `python run.py build-panel --analysis-end 2026-07-24` → `ValueError: build-panel only accepts the development split 2023-06-01..2025-12-31`（`v6/src/.../cli.py:194`）。
    上游 `v6/config/data_splits.yaml` 把 `development` 硬钉在 2023-06-01..2025-12-31，`consumed_regression` = 2026-01-01..2026-07-17（仅 `engineering_regression_only`，禁止 rescan/candidate_comparison/parameter_variation/strategy_optimization），`final_test` 状态 `awaiting_candidate`、`start: null`。`v6/src/.../governance.py:81-94` 对以上每一条做启动期断言，**改窗口即抛错**。`v6/config/universe.yaml` 另设 `oos_start: 2026-01-01`，`scan` 命令遇到 ≥ 该日期的面板行会抛 `"Research panel contains locked OOS dates"`（`cli.py:233`）。
    **含义**：把 v6 面板延伸到 2026 需要修改上游治理常量，等于**主动破坏上游的数据预算锁**，不是本项目可以擅自做的动作；即使绕过断言，产出的面板也会被上游自身视为 locked OOS。另外上游 `artifacts/live/\*` 已有 2026-07-31 起的逐日信号记录，说明**前向数据在上游是"逐日 live 记录"形态，而非"重算历史面板"形态**。
    **上游已明确删除该能力**：`--unlock-oos` 曾经存在，但 v6 已**主动移除**——`docs/v6_5_incremental_panel_checkpoint.md:21`「研究 `build-panel` 现在统一使用 `data_splits.yaml` 的 development 边界，**拒绝 2026；旧 `--unlock-oos` 已删除**」；`docs/incremental_panels_v65.md:43` 同述。现行 `python run.py build-panel --help` 参数仅 `--kline-dir/--analysis-end/--output/--limit/--incremental/--rebuild-state`，**无任何解锁开关**。即：这不是"没做"，而是**上游设计上禁止**。
    **另一条线索（已排查，不构成解法）**：v6 里存在 `panel_recent.metadata.json`（`analysis_end: 2026-07-24`、3,194 只、2,394,957 行），但**对应的 `panel_recent.parquet` 并不存在**；`docs/architecture_review_2026_07_25.md:486-503` 已记录该批 2026 产物「由某个已删除的临时脚本生成，不在受审代码路径内」，且其 2026-07-20 之后 `benchmark_close`/`regime_allows` 100% 为 NaN（`_bench_*.csv` 只到 2026-07-17）。**结论：不可依赖、不可复现，不作为数据源。**
    **D-4 依赖链（2026-10-17 实测后修正，重要）**：我先前的"v6 派生特征本地无等价实现"判断**经实测被推翻**，记录如下以免误导后续：
    1. **日线 OHLCV：本地 K 线 CSV 与 v6 面板逐列逐行完全一致**。对 `open/high/low/close/amount/volume` 六列做全量比对（1,968,826 行），**max abs diff = 0**，行数与代码数也完全相等（3,178 只）。`bridge/data.py::load_kline_panel` 可直接读，且本地已到 **2026-07-24**。
    2. **本地 CSV 能 1:1 复现冻结因子（已验证）**：
       - `_polars_frame` 的输入分别由 *v6 面板* 与 *本地 CSV 重建的面板* 生成，24 列**逐值相同**（20 个数值列 0 差异、industry/sub_industry 0 不一致、键集合相同）。
       - 受控同进程比对（全量 1,968,826 行）：**本地重算 vs 已存 `aurumq_alpha101_full.parquet`，106/106 列完全一致（0 差异）**；**v6 面板重算 vs 已存，106/106 一致**；**本地 vs v6 重算，106/106 一致**；进程内重复计算同一输入，**0/106 差异**（确定性成立）。
       - 冻结清单 28 个 alpha101 特征单独比对同样 **0/28 差异**。
       - **诚实标注**：过程中有一次**跨进程**比对（非受控）曾显示 33/96 列有差异，**未能复现**，怀疑是该次比对脚本的列对齐/上下文问题。**结论以受控同进程比对为准**，但保留该不确定性提示，后续若以本地 CSV 重建因子应重跑一次全量等价校验。
       - 结论：`data/aurumq_alpha101_full.parquet` 可由本地 CSV 精确重建，**不依赖 v6 `build-panel`**。
    3. 其余 9 个特征：`manual__alpha004/055/059` 的底层就是 alpha101 列（步骤 2 已覆盖）；`alpha_qg_range_15`、`alpha_qg_corr_pv_10`、`alpha_qg_f001/f005/f006/f008` 由**本项目自己的表达式引擎**（`bridge/panel.py` + `config/expressions*.json`）基于日线计算，其表达式 token 只用到 `close/high/low/vol/vwap/amount/volume/returns`——**同样不依赖 v6 派生列**（`ema_*/gmma_*/atr_*` 等 100 列里的多数**并不在冻结 37 因子的依赖范围内**）。这 9 项尚未做等价性实测，但依赖面已明确。
    4. **真正的剩余缺口是基准与标签，不是特征**：`_bench_000300/000985` 只到 **2026-07-17**、C 轨指数 `000852/000915` 到 2026-07-24。标签 `y_excess20/y_neutral_rank20_*` 需要 `benchmark_open/benchmark_close`；且 20 日标签本身要求"未来 20 个交易日"，**在数据地平线处天然无法生成**（属于前瞻标签的固有边界，不是缺陷）。
    **修正后的结论**：D-4 的"特征层"**在本项目内可自给**（已实测），**并非必须先由上游重建**。阻塞点收缩为：(a) 上游 OOS 锁使 v6 `panel.parquet` 无法延伸（治理事实，见上）；(b) 基准 CSV 只到 2026-07-17；(c) 本地 K 线止于 2026-07-24，故**干净前向窗口实际只有 2026-07-20~07-24 共 5 个交易日**。    **但**：**影子前向观察不需要标签**（只需特征→评分→记录），因此这 5 天在原则上是可用的；若要从上游 `baostock` 续取到 2026-10-16，需同时补基准。**下一步（未开工）**：以本地 CSV 为源、按 v6 schema 物化 2026-07-18~07-24 的日线面板，跑 `(date,code)` 唯一性断言，再用 `bridge/panel.py` 生成冻结 37 特征并做与 evaluation 期一致的评分等价校验（对应阶段 E）。
    **建议**：影子前向观察应对齐上游语义——逐日记录（上游 `artifacts/live/*` 已是此形态），不重算 2026 面板；市值域侧（D-3b）已就绪，仅差"逐日行情进入评分链路"这一环。
- [ ] 行业历史 PIT：用户已确认数据可得（2026-09），未集成、未验证纯度、未做敏感性实验。
  - **2026-10-17 实测（阻塞 D-5；纯度已直接测量，结论：非 PIT）**：本地 `data/refdata/industry_snapshot.parquet` **仅 2 行**（000001/600519），`pit_note=snapshot_not_historical`、`update_date=2026-08-10`，是演示数据。`bridge/refdata.py` 文件头已自陈：三个免费源（baostock `query_stock_industry`、WeStock profile/balance）**均无历史行业分类**，只能给出带 `updateDate` 的当前快照。
    **对冻结面板的量测（决定性）**：`panel_fusion_full_market.parquet` 的 `industry` 在 **629 个交易日里对全部 3,178 只股票零变化**（每只股票的不同行业数 max = 1），31 个行业、零缺失。其上游 `honest_backtest/data/stock_sector_map.csv`（3,194 行，仅 `code,industry` 两列）**没有任何日期列**。即：**冻结面板的行业是静态快照，不是 PIT**，且这个"非 PIT"是在当前冻结结果中**实际生效的**，不是潜在风险。
    **两个源甚至不可比**：demo 快照用证监会门类代码（`J66金融业`、`C15酒饮料和精制茶制造业`），v6 用短名（`银行`、`食品饮料`）；两只重叠股票**冲突率 2/2**，属分类体系不同而非数据错误。
    **含义**：D-5 要验证的"纯度"已经可以给出否定结论——当前行业字段是快照口径。要做行业 PIT 需**用户提供真实历史行业分类数据**（含生效日），且需同时解决分类体系对齐；在此之前 `industry_code`/`industry` 的一切行业中性化与行业暴露披露都应按"快照口径"标注，**不得声称 PIT**。
- [ ] 冻结评分器可重建/预测等价校验：未做（影子观察前置）。
- [ ] 行业快照敏感性、horizon 敏感性：稳健性三件套欠两项（40 日已声明只作敏感性不改标签）。
- [x] 工作区卫生（已完成）：裁决 xlsx（`docs/DIRECTION_ADJUDICATION_ABC_2026-08-27*.xlsx`）、`docs/DIRECTION_ADJUDICATION_2026-08-22.md` 已由提交 `afd2afb` 纳入版本库。根目录 `_s1_mc_fetch.py` **经核查是 git 跟踪文件（非临时探针），且是市值面板延伸的唯一入口**，故**保留不删**；2026-10-17 已为其补 `--start/--end/--codes-from` 并修正 `--resume` 语义。
- [ ] v6 官方锁箱：待影子观察积累后评估，当前不得进入。

## 4. 整体计划与步骤

### 阶段 H：工作区卫生（当天可完成）

1. 提交裁决记录 xlsx 与 8-22 裁决文档；归档或删除 `_s1_*.py`。
2. 更新 PROJECT.md/HANDOFF 残留过时条目（原"G2 未通过"表述已被两层基准裁决取代，以本文件 §1 为准）。

### 阶段 D：数据刷新（影子观察前置，本周）

3. baostock 日线、股本 PIT（`data/refdata/shares_pit.parquet` 同口径按 pubDate as-of 延伸）、全市场市值面板（`data/s1_mc_panel.parquet` 同口径）刷新至当前日期。
   - **2026-10-17 已完成（D-3a 收口，已验证）**：股本 PIT 从 49,965 行 → **56,372 行**，覆盖 3,194 只，新增 2026-03-31 / 2026-06-30 两期，各覆盖 **3,193 只**（3,194 中仅 1 只缺）。质量校验全通过：`(code,stat_date)` 零重复、`pub_date ≥ stat_date` 零违例、2026 行零空 pub_date、`pub_date` 延伸到 **2026-10-16**。
   - **过程教训（重要）**：首次运行 `logout failed!` 会话中途掉线，只拉完**前 122 只**（全部 00 前缀）。若只看行数会误判为"2026 已披露数据少"；对缺口 `000544/000545/000546` 单点复查上游**均有数据**，才证实是取数中断。已 `--resume` 续跑补全。**以后该脚本中断后必须核对"每期代码覆盖数"而非行数**；脚本按 `(code,stat_date)` 跳过已有键，故续跑安全幂等。
4. 全市场面板重物化至当前：`panel_fusion_full_market.parquet` 同 schema 延伸；主键 `(date, code)` 无重复断言必跑。
   - **2026-10-17 已完成（D-3b 收口，已验证）**：市值面板 `data/s1_mc_panel.parquet` 从 2,928,983 行 → **3,814,262 行**，日期 2023-06-01 → **2026-10-16**（817 个交易日，新增 2026 年 188 个交易日 / 4,729 只 / 885,279 行）。
     - **同口径验证**：抽取 600000/000001/000002/600519/300750 重算 `float_mcap = close × volume / (turn/100)`，与既有行**逐行相对差 = 0.0**，确认公式口径完全一致。
     - **质量**：`(date, code)` 零重复、`float_mcap` 零空值零非正；月度中位数 63~69 亿与 2025 下半年 57~64 亿连续，无单位断点；跨年连续性——4,719 只股票 2025-12-31 → 2026 首个交易日收盘价差中位数 1.47%、**无一超过 50%**。
     - **历史不可变**（关键安全性质）：合并后 2023-06-01~2025-12-31 的 2,928,983 行**逐列完全相同**；`_float_mcap_membership` 复算的历史 B2/B3 成员（1,887,000 行）**逐行相同**。原因：市值排名按 `groupby(date)` 逐日计算，追加新日期不会改变旧日期名次（已单独实验证明）。
     - **新增能力**：2026 年每个交易日 B2 域新增 3,000 只成员、B3 域 2,000 只（此前 2026 完全无成员）。**干净前向窗口 2026-07-20 ~ 2026-10-16 已完整覆盖，共 59 个交易日，每日 B2/B3 成员数稳定为 3,000/2,000**（min = max = median）。即：一旦上游日线地平线打通，**域/市值侧无需再等**。
     - **主键断言（D-4 要求的 `(date,code)` 唯一性）**：对现行冻结面板 `panel_fusion_full_market.parquet` 与延伸后的市值面板各跑一次，均 **0 重复**（1,968,826 / 3,814,262 行）。重物化完成后必须对**新面板**再跑一次，本项不代表新面板已通过。
     - **回滚点**：旧文件保留为 `data/s1_mc_panel.parquet.bak_20261017`（sha256 前缀 `f12c254ce0a50eb3`）。
     - **工具改动**：`_s1_mc_fetch.py` 新增 `--start/--end/--codes-from`，并把 `--resume` 从"代码出现过就跳过"改为"该代码在目标窗口内已有数据才跳过"（旧语义在延伸窗口时会跳过全部代码，等于无法续跑）。该文件是 git 跟踪的，改动随本提交入库。
     - **2026-10-17 已完成（D-4 收口，已验证）——重物化不再需要上游**：先前的"必须等上游 v6 重建 panel.parquet"结论**已被实测推翻并撤回**。做法：用本地 K 线 CSV 重建 v6 schema 的日线面板（`artifacts/build_extension_2026.py`），再喂给 `bridge/panel.py` 产出完整融合面板。
     - **产物**：`data/panel_fusion_full_market.parquet` 从 1,968,826 行 → **2,394,957 行**，日期 2023-06-01 → **2026-07-24**（763 个交易日，3,194 只）。回滚点 `data/panel_fusion_full_market.parquet.bak_20261017`。
     - **主键断言（D-4 明确要求，已在最终产物上执行）**：`(date, code)` **重复 = 0**，`panel_scope` 全为 `full_market`。
     - **历史等价性（决定性证据）**：与旧冻结面板逐行比对 1,968,826 行，**全部 45 个特征/身份列 max abs diff = 0**（OHLCV/amount/volume 均为 0，`industry` 0 不一致，8 个 v6 派生列 `atr_relative/range_relative/vol_ratio/position_ema20/ret_5d/ret_20d/realized_vol_120d_d1/amount_dry_ratio` 亦为 0）。即**旧数据一字未改**。
     - **唯一差异是标签的 NA 模式，且是修复而非回归**：`stock_ret20/benchmark_ret20/y_excess20/y_*` 在 2025-12-04~12-31 有 63,169 行"旧缺新有"（`frozen_only = 0`，即有值处 **diff = 0**）。旧面板因数据止于 2025-12-31 而在末 20 个交易日无法生成 20 日前瞻标签；新面板能看到后续行情，故正确填充。**这不是冲突**。
     - **8 个 v6 派生列已证实可本地精确复现**（对 5 只股票 629 天全量比对，max abs diff = 0），公式取自 `v6/src/.../data/panel.py::compute_stock_indicators` 并镜像实现。
     - **上游 `panel_recent.metadata.json` 的交叉印证**：其记录 `analysis_end=2026-07-24`、`row_count=2394957`、`stock_count=3194`——与本次重建的 **行数与股票数完全一致**，独立佐证重建口径正确。
     - **仍受限（非阻塞，属固有边界）**：干净前向窗口实际只有 **2026-07-20~07-24 共 5 个交易日**（本地 K 线止于 07-24）；基准 `_bench_000985` 只到 **2026-07-17**（baostock 无该代码，但 `sh.000300`/`sh.000001` 可到 2026-10-16）；20 日标签在数据边缘天然为 NaN。
     - **2026-10-17 实测：延伸至 2026-10-16 需要跨源基准对齐，不能直接拼接**（重要，避免后续踩坑）：
       - baostock 可查到 2026-10-16，其**成交量/成交额**与本地归档在 6 只股票 × 39 个重叠日上对齐到浮点精度（本地"手" × 100 == 股，偏差 ~1e-6）。
       - 但**收盘价不一致，且差值随日期变化**：本地隐含因子在重叠段有多个取值（如 `000001` 8 个、`600519` 12 个），单因子拟合残差远超 2 位小数舍入容差（`600519` 残差 28.02 元）。即两端**前复权基期不同且非恒定比例**。
       - **直接拼接会在接缝引入假跳空**（实测 `600000` 为 −4.47%），污染 `pct_change/ret_*/atr_relative/position_ema20` 等全部价格类派生列。
       - **正确做法**：在重叠段估计逐日基准因子 `f(t)=local_close/baostock_adj_close`（分段常数，仅在除权日跳变），用它把 baostock 序列整体折算到本地基期后再延伸，并断言接缝日收益不超历史 p99.9。详见 `docs/knowledge/baostock_local_kline_splice.md`。
       - 另一路（更稳妥）：**等待/触发本地 K 线归档自身刷新**，然后重跑 `bridge/panel_extension.py` 即可，无需混源。
     - **脚本**：`artifacts/build_extension_2026.py`（本次新增，一次性构建器；排除聚合文件 `all_main_board_kline.csv`，它含 1 行违反 vwap 契约的数据会触发 `bridge/data.py` 断言）。
5. 行业 PIT 数据纯度验证：调整生效日个案抽查（已知改行个股的公告日前后）、与现有快照源同日期冲突率、2023 年起覆盖率；结论写入 `docs/DATA_SOURCE_REPORT.md` 更新。**验证不过则行业 PIT 不进入任何生产路径。**

### 阶段 E：评分器等价校验（阶段 D 后）

6. 用冻结配置重新物化 `huber_b2_neutral_rank` 三种子评分器，对 evaluation 期纯预测比对：与 `runs/factor_evaluation_v1_b2_frozen_2026-08-28/huber_b2_neutral_rank/predictions.parquet` 逐键逐分一致（不重新训练、不看标签）。不等价则停线诊断，不得带病进入前向。

### 阶段 F：前向观察（协议先行）

7. 预注册影子观察协议（启动前登记）：观察频率、指标（日 IC、滚动 IC、剔除名单变更率、剔除组后续超额）、判定标准（**IC 方向与量级落在历史分布范围内**，而非复制 +3.4% 超额）、中止/升级条件、行业暴露持续披露口径。
8. 从预先声明的新日期启动只读影子观察；不回填已可见日期为前向样本。
9. 并行：行业 PIT 影子评分器敏感性实验（需先写预注册）：真实 PIT 中性标签训练影子评分器 → 与冻结分对比 rank 相关、底部 10% 名单重合率、影子口径 C 轨组合数学重算；预注册判读（如重合率 >85% 且机制门槛仍过 → 快照隐患永久关闭；翻转 → 冻结结果标注脆弱性，前向权重加大）。

### 阶段 G：评估与升级（影子观察积累后）

10. 影子观察达到预注册时长/样本量后评估：前向 IC 行为、剔除经济性、行业暴露稳定性。
11. 通过则评估是否进入 v6 官方锁箱（单次揭盲语义不变）；不通过则回描述阶段，冻结结果归档锁箱。
12. 知识库沉淀：行业快照→PIT 直接测量结果、C 轨实现缺陷教训（组合语义须合成测试锁定、manifest 必须内嵌 commit）。

## 5. 恢复入口（下一会话可直接执行）

**环境前置（已验证可用，2026-10-17）**：四个上游变量指向项目同级目录即可，`python -m bridge.paths` 通过：
```powershell
$env:A_SHARE_FACTOR_ENGINE_V6_ROOT = 'C:\Users\Lenovo\Documents\New project\a-share-factor-engine-v6'
$env:AURUMQ_RL_ROOT                 = 'C:\Users\Lenovo\Documents\New project\aurumq-rl'
$env:QUANTGPT_ROOT                  = 'C:\Users\Lenovo\Documents\New project\QuantGPT'
$env:A_SHARE_KLINE_DATA_ROOT        = 'C:\Users\Lenovo\Documents\New project\a-share-kline-data'
```
每个 pwsh 进程独立，变量不跨会话保留，必须逐次设置。

1. **（需用户决策）打通上游数据地平线**：D-4 全市场面板重物化的硬前置是上游 v6 的 `artifacts/panel/panel.parquet` 重建至 2026-07-24 及以后。这是本项目外的动作，需确认由谁/如何推进，或确认是否接受"面板仍止于 2025-12-31"而把影子观察改为**不依赖新面板的轻量前向记录**。
2. 阶段 D-3a（已完成/可续跑）：股本 PIT 2026 年四个季度刷新，命令为
   `python bridge/refdata.py --out-dir data/refdata --years 2026 --codes-file data/refdata/all_codes.txt shares --resume data/refdata/shares_pit.parquet`
   （幂等+断点续传，重复执行安全）。
3. 阶段 D-3b（**已完成**，2026-10-17）：市值面板已延伸至 2026-10-16。如需继续延伸到更新日期，命令为
   `python _s1_mc_fetch.py --codes-from data/s1_mc_panel.parquet --start 2026-10-17 --end <目标日> --batch-size 0 --resume --out artifacts/mc_ext.parquet`
   然后按 §2 阶段 D 第 4 条同口径合并（历史行必须保持不变后再落盘）。注意该脚本**只在全批结束时落盘一次**，中断即全丢，务必先确认批内代码数可控。
4. 阶段 D-5：行业 PIT 纯度抽查——**需用户提供真实历史行业 PIT 数据文件位置与格式**；本地仅有 2 行演示快照，无法自行开展。

## 6. 关键文件索引

- 冻结配置：`config/factor_pool_b2_frozen_2026-08-28.json`
- C 轨有效结果：`runs/c_track_acceptance_v2_recast_2026-09-05/`
- 验收预注册：`docs/C_TRACK_ACCEPTANCE_PREREG_2026-08-28.md`（含两层基准）、`docs/C_TRACK_RECAST_PREREG_2026-09-05.md`
- 定案报告：`docs/WORK_REPORT_2026-09-05_C_TRACK_RECAST.md`
- 裁决记录：`docs/DIRECTION_ADJUDICATION_ABC_2026-08-27_kimi.xlsx`（含最终决定列）
- 长期决策：`docs/PROJECT.md`；上游环境：`docs/UPSTREAM_ENVIRONMENT.md`
