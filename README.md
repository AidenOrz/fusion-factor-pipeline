# Fusion Factor Pipeline（三项目融合立项）

> 状态：立项 + P0/P0.5 起步
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

## 5. 当前进展

- [x] P0 数据底座 + 四数据坑断言（`tests/run_p0_smoke.py` 全过）。
- [x] P0.5 判官校准：legacy_score 基准复现误差 < 0.00004。
- [x] P1 完成：8 个 starter + 10 个 DeepSeek 生成表达式（key 在 gitignored `.env`）。
- [x] P2 面板 + purged walk-forward：`data/panel_fusion.parquet`、`runs/gbdt_walkforward/`。
- [x] P3 开发者观察锁箱：已追加 3 个非空 live 信号日，链验证通过。
- [ ] G2 通过前不进入 v6 官方锁箱；继续 clean-date 观察。

详见 `docs/COMPLETION_REPORT.md`。

## 6. 纪律红线

- 干净数据自 `2026-07-18` 起算；2026-01-01~07-17 永久消费。
- post-hoc 描述性统计不得升级为选择依据。
- 锁箱通过 = 进入前向观察，不等于 alpha 已验证。
- **硬验收 = 跑赢上证指数（000001.SH）**；legacy_score 因历史回测引擎记账问题只作诊断参照。
