# Fusion Factor Pipeline（三项目融合立项）

> 状态：B2 全市场排序范式；37 因子冻结池已通过一次性 Evaluation 与 C 轨经济验收（2026-09-05 定案）；下一步为前向影子观察
> 创建：2026-08
> 定位：把 `QuantGPT`（表达式挖矿）、`aurumq-rl`（工程件/top-k）、`a-share-factor-engine-v6`（事件门、legacy 基准、锁箱纪律）组装为一条可复现、防过拟合、开发者可观察的因子生产流水线。

## 1. 立项目标

1. 建立统一前复权数据底座，堵住 vwap 单位、group_rank 降级、ts_corr NaN/inf、市值代理四个数据坑。
2. 自建 LightGBM purged walk-forward 判官，先用 v6 四因子复现 `legacy_score` 基准（P0.5 已知答案校准）。
3. 新排序器必须在干净数据上同时打败随机基准与 `legacy_score` 基准。
4. 提供**开发者观察锁箱**：保留 SHA-256 防篡改链，但允许开发者随时只读观察，正式揭盲仍单次生效；**观察不消费官方 v6 锁箱**。

## 2. 非目标

- 不修改 v6 官方 `final_test.py` 的单次揭盲语义。
- 不接入券商下单，不做实盘交易系统。
- 不把开发集 `2023-06-01~2025-12-31` 的数字升级为生产结论。
- 不在开发者观察锁箱中做 alpha 优越性宣称。

## 3. 目录

```text
fusion-factor-pipeline/
├── README.md
├── AGENTS.md                       # 协作与文档规范
├── docs/
│   ├── PROJECT.md                  # 长期决策与边界（权威）
│   ├── HANDOFF.md                  # 当前稳定点与下一步（权威）
│   ├── UNIVERSE_SPEC.md            # B1/B2/B3 域定义与口径
│   ├── UPSTREAM_ENVIRONMENT.md     # 上游路径与启动校验
│   ├── DATA_SOURCE_REPORT.md       # 数据源与行业 PIT 结论
│   ├── INDUSTRY_PIT_2026-10-18.md  # 申万 SW2021 PIT 集成与判读
│   ├── FORWARD_SHADOW_2026-10-17.md# 前向影子评分首次运行
│   ├── C_TRACK_*_PREREG_*.md       # 验收预注册
│   ├── WORK_REPORT_*.md            # 各轮定案报告（历史依据）
│   ├── DEV_LOCKBOX.md              # 观察锁箱契约
│   └── knowledge/                  # 可复用经验条目
├── config/                         # 冻结配置与表达式
├── bridge/                         # 面板/标签/评分/验收实现
├── tests/                          # pytest（90 项）
├── runs/                           # 各次实验结果
└── artifacts/                      # 派生产物与锁箱
```

文档以 `docs/PROJECT.md`（长期决策）与 `docs/HANDOFF.md`（当前状态与下一步）为准；
`docs/knowledge/` 存放经过验证、可迁移的经验条目。

## 4. 上游仓库

- [AidenOrz/a-share-factor-engine-v6](https://github.com/AidenOrz/a-share-factor-engine-v6)
- [yupoet/aurumq-rl](https://github.com/yupoet/aurumq-rl)
- [Miasyster/QuantGPT](https://github.com/Miasyster/QuantGPT)（本地 fork：`AidenOrz/QuantGPT`）

## 上游路径

运行时通过环境变量指定兄弟仓库和行情数据目录，程序启动时会校验路径及关键标志文件，不再根据当前目录层级隐式回退：

```powershell
$env:A_SHARE_FACTOR_ENGINE_V6_ROOT = 'C:\path\to\a-share-factor-engine-v6'
$env:AURUMQ_RL_ROOT = 'C:\path\to\aurumq-rl'
$env:QUANTGPT_ROOT = 'C:\path\to\QuantGPT'
$env:A_SHARE_KLINE_DATA_ROOT = 'C:\path\to\a-share-kline-data'
python -m bridge.paths
```

完整说明见 [`docs/UPSTREAM_ENVIRONMENT.md`](docs/UPSTREAM_ENVIRONMENT.md)。

## 5. 当前进展

- [x] P0 数据底座 + 四数据坑断言（`tests/run_p0_smoke.py` 全过）。
- [x] P0.5 判官校准：legacy_score 基准复现误差 < 0.00004。
- [x] P1 完成：8 个 starter + 10 个 DeepSeek 生成表达式（key 在 gitignored `.env`）。
- [x] P2 面板 + purged walk-forward：`data/panel_fusion.parquet`、`runs/gbdt_walkforward/`。
- [x] P3 开发者观察锁箱：已追加 3 个非空 live 信号日，链验证通过。
- [x] 全市场 B2 排序范式（2026-08-20 起）：三域 × 两标签矩阵，Huber 为主基线；旧 events 域降级为 legacy 诊断。
- [x] B2 因子裁决 + 消融 + 冻结：316 → 114 裁决池 → LOFO → **37 因子冻结池**（`config/factor_pool_b2_frozen_2026-08-28.json`）。
- [x] 一次性内部 Evaluation（已消费，通过）：neutral HAC IC_IR 0.393、raw 0.255。
- [x] **C 轨经济验收定案通过（2026-09-05 recast，`runs/c_track_acceptance_v2_recast_2026-09-05/`）**：机制层 1x 净年化超额 +3.360%、机会成本 50/50 混合 +6.374%；`production_authorized=false`。
- [x] **阶段 D 数据刷新（2026-10-17 完成）**：股本 PIT 至 2026 两期、市值面板至 2026-10-16；
  全市场面板重物化至 **2026-10-16**（2,537,139 行），`(date,code)` 零重复，历史行逐列不变。
  面板重物化不再依赖上游 v6（改用 `bridge/panel_extension.py` 从本地 K 线重建）。
- [x] **前向影子评分首次运行（2026-10-17）**：冻结 `huber_b2_neutral_rank` 在选择期训练一次，
  对 2026-07-20~10-16 共 59 个交易日**无标签**打分（95,285 行 / 2,150 只）；前向期零标签。
- [x] **行业 PIT 集成（2026-10-18）**：申万 SW2021 官方历史分类表（带计入日期，真 PIT）已固化；
  量测确认冻结面板有 156 只股票行业确实变更过；底部 10% 重合率 95.79% → **快照隐患关闭**。
- [ ] 前向观察**预注册协议**：尚未登记（阶段 F 第 7 项），是正式观察前唯一的空白。
- [ ] 评分器可重建/预测等价校验（阶段 E）尚未做。
- [ ] 前向 IC 与经济性评估：需等 20 日标签落地（当前前向窗口标签全为 NaN）。

详见 `docs/HANDOFF.md` 与 `docs/WORK_REPORT_2026-09-05_C_TRACK_RECAST.md`。

## 6. 纪律红线

- 干净数据自 `2026-07-18` 起算；2026-01-01~07-17 永久消费。
- post-hoc 描述性统计不得升级为选择依据。
- 锁箱通过 = 进入前向观察，不等于 alpha 已验证。
- **硬验收基准（2026-08-28 裁决）= 两层制**：机制层 B2 域等权（硬门槛）+ 机会成本层中证 1000/2000（次级判定）；上证指数降为纯附录，不再承担门槛职能。详见 `docs/PROJECT.md` §2 决策 1 取代记录。
