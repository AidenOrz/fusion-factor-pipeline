# 执行状态

> 更新：2026-08（“一口气做完”轮次后）
> 分类：`developer_observation_only`

## 已完成

1. 项目立项：`fusion-factor-pipeline/` 目录、README、执行计划、锁箱契约。
2. P0：`bridge/data.py`、`bridge/ops.py`，四个数据坑断言全部通过。
3. P0.5：`bridge/gbdt.py` 静态校准，legacy_score 基准复现误差 < 0.00004。
4. P1：8 个 starter + DeepSeek v4-flash 10 个表达式，合并 18 个；多 provider 挖矿与运行审计记录完成。
5. aurumq-rl 因子库：`bridge/aurumq_factors.py`，alpha101/gtja191 全量计算完成。
6. P2：`data/panel_fusion_full.parquet`（52,088 行，316 因子列），lightgbm walk-forward 已训练。
7. P3：开发者观察锁箱已链式追加 3 个非空 live 信号日，链验证通过。
8. 验收口径：硬验收 = **跑赢上证指数**；`bridge/benchmark.py` 已接入。
9. 数据源：baostock 无需代理；**全市场股本 PIT 已拉取**（49,965 行 / 3,163 股，market_cap 覆盖率 99.2%）；行业只能快照。
10. 当前结果：精简池（股本/市值）开发集 Top1 超额 +0.0111；完整 316 因子池未跑赢上证指数；均为 post-hoc。
11. 因子体检与剪枝完成：322 → 80（相关性 0.95）→ 60（多折 SHAP）；三个池均未跑赢上证指数，产物在 `runs/factor_selection/`。
12. 长期文档：`docs/PROJECT.md`；交接文档：`docs/HANDOFF.md`。
11. DeepSeek key 已配置（gitignored `.env`）；直接表达式无需 key。
12. 文档：`docs/COMPLETION_REPORT.md`、`docs/DATA_SOURCE_REPORT.md`、`docs/LLM_MINING.md`。

## 待执行/待人工决策

- [x] 全市场股本回填：`data/refdata/shares_pit.parquet`（49,965 行 / 3,163 股 / 2022-2025）。
- [ ] 行业 PIT：免费源无历史分类，需要交易所/指数公司历史快照或付费数据。
- [x] lightgbm 已安装并重跑对照（清华镜像，4.7.0）。
- [x] DeepSeek key 已配置，LLM 挖矿已跑出 10 个表达式；key 仅存于 gitignored `.env`。
- [ ] 需要时可以继续用 QuantGPT HTTP 服务跑其本地回测，但其分数仍不作为生产判据。
- [ ] G2 未过（开发集 ML/legacy 均未跑赢上证指数），不进入 v6 官方锁箱。
- [ ] 每日将非空 live 信号追加进观察锁箱，定期 `observe` 而非揭盲。

## 常用命令

```powershell
cd fusion-factor-pipeline

# P0 断言
python tests/run_p0_smoke.py

# P1/P2 面板
python bridge/panel.py --expressions config/expressions.json --out data/panel_fusion.parquet

# aurumq-rl 因子（smoke / 全量）
python bridge/aurumq_factors.py --families alpha101 --codes 000001,600519
python bridge/aurumq_factors.py --families alpha101,gtja191

# P0.5/P2 判官（沙箱内先设置单线程）
$env:LOKY_MAX_CPU_COUNT='1'; $env:OMP_NUM_THREADS='1'
python bridge/gbdt.py --mode both --out-dir runs/gbdt_walkforward

# P3 观察锁箱
python bridge/dev_lockbox.py status
python bridge/dev_lockbox.py observe
python bridge/dev_lockbox.py append --date YYYY-MM-DD --signals path/to/signals.csv
python bridge/dev_lockbox.py verify
```

## 注意

- `observe` 不消费正式揭盲；`formal-reveal` 每 epoch 一次。
- 官方 v6 锁箱保持原样；本项目锁箱只是影子观察器。
- 受限沙箱中 pytest 系统临时目录不可用，使用 `tests/run_*.py` 独立 runner。
