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
├── docs/
│   ├── EXECUTION_PLAN.md      # 执行计划
│   └── DEV_LOCKBOX.md         # 放松版锁箱契约
├── config/
│   └── dev_lockbox.yaml       # 观察锁箱配置
├── bridge/
│   ├── __init__.py
│   └── dev_lockbox.py         # 开发者观察锁箱实现
├── tests/
│   └── test_dev_lockbox.py    # 锁箱冒烟与防篡改测试
└── artifacts/
    └── dev_lockbox/           # 链条、信号快照、状态
```

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
- [ ] 前向影子观察：待数据刷新 + 评分器等价校验 + 观察协议预注册后启动。
- [ ] 行业历史 PIT：数据已可得（2026-09），待纯度验证与敏感性实验。

详见 `docs/HANDOFF.md` 与 `docs/WORK_REPORT_2026-09-05_C_TRACK_RECAST.md`。

## 6. 纪律红线

- 干净数据自 `2026-07-18` 起算；2026-01-01~07-17 永久消费。
- post-hoc 描述性统计不得升级为选择依据。
- 锁箱通过 = 进入前向观察，不等于 alpha 已验证。
- **硬验收基准（2026-08-28 裁决）= 两层制**：机制层 B2 域等权（硬门槛）+ 机会成本层中证 1000/2000（次级判定）；上证指数降为纯附录，不再承担门槛职能。详见 `docs/PROJECT.md` §2 决策 1 取代记录。
