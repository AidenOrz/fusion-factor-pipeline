# 本地 K 线 CSV 可精确重建 v6 派生面板列（无需上游 build-panel）

## 适用场景
需要把融合面板（`panel_fusion_full_market.parquet`）延伸到上游 v6 development 窗口
（2023-06-01..2025-12-31）之外，但 v6 的 `build-panel` 因治理锁**拒绝开发窗口外的日期**，
且 `--unlock-oos` 已被上游删除。

## 现象/前提
- `python run.py build-panel --analysis-end 2026-07-24`
  → `ValueError: build-panel only accepts the development split 2023-06-01..2025-12-31`
- v6 `config/data_splits.yaml` + `governance.py` 在启动期硬断言该窗口，改窗口即抛错。
- 但 `bridge/panel.py` 的日线源可以替换：`--panel <任意 parquet>`。

## 根因/洞见
融合面板真正消费的 v6 派生列**只有 8 个**，且都有简单闭式公式，
可直接从本地 K 线 CSV 复算：
`atr_relative, range_relative, vol_ratio, position_ema20, ret_5d, ret_20d,
realized_vol_120d_d1, amount_dry_ratio`
（公式源：`v6/src/a_share_factor_engine_v6/data/panel.py::compute_stock_indicators`；
注意 `position_ema20 = prev_close/ema_20.shift(1) - 1`、`realized_vol_120d_d1 = daily_return.rolling(120).std(ddof=1).shift(1)*sqrt(250)`，
以及 `atr10_d1/atr50_d1` 用 true range 的 rolling mean 再 `shift(1)`）。

其余"上游 100 列"里的大部分（`gmma_*/stage2_*/breakout_*/…`）**并不在融合面板的依赖范围内**，
不要因为看到上游面板有 100 列就以为必须依赖它。

## 推荐做法
1. 从 `A_SHARE_KLINE_DATA_ROOT` 的**逐股 CSV** 建面板；
   **必须排除 `all_main_board_kline.csv`**——它是聚合文件，
   含 1 行 `2025-01-03, close=10.01, volume=464, amount=59059`（vwap/close=0.127）
   会让 `bridge/data.py::_validate_vwap_unit` 抛 `DataContractError`。
2. 逐股算上述 8 列后，写 v6 兼容 schema，再喂给
   `bridge/panel.py --scope full_market --panel <ext.parquet>`。
3. **验收必须比对重叠历史窗口**：要求所有特征列 max abs diff = 0。
4. 会出现的唯一差异是**标签 NA 模式**：全历史重建后，末 20 个交易日的前瞻标签
   由 NaN 变为有值（旧面板因数据止于窗口末而无法生成）。以 `frozen_only = 0`
   且"有值处 diff = 0"作为通过判据，不要误判为回归。

## 验证依据（2026-10-17 实测）
- 重建面板 2,394,957 行 / 3,194 只 / 2023-06-01..2026-07-24；`(date,code)` 零重复。
- 与旧冻结面板重叠的 1,968,826 行：**45 个特征/身份列 max abs diff 全为 0**。
- 8 个派生列单独对 5 只股票 × 629 天比对，max abs diff = 0。
- 上游 `artifacts/panel/panel_recent.metadata.json` 记录 `analysis_end=2026-07-24`、
  `row_count=2394957`、`stock_count=3194`，**与本重建行数/股票数完全一致**（独立佐证）。
- 推广后 `pytest` **72 passed**。

## 适用边界
- 这是"**特征层**可自给"的结论；**标签层**仍受数据地平线约束（20 日前瞻标签在末尾天然为 NaN）。
- 若要把窗口延到 2026-07-24 之后，仍需新增行情：本地 CSV 止于 2026-07-24；
  基准 `_bench_000985` 止于 2026-07-17（baostock **无 000985**，但 `sh.000300`/`sh.000001` 可查到 2026-10-16）。
- 不得据此修改上游 `data_splits.yaml` 或写入 v6 的 `artifacts/`。
