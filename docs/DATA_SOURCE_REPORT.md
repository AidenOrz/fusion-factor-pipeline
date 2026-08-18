# 总股本 / 行业 PIT 数据源调研与接入报告

> 日期：2026-08
> 结论：**baostock 是总股本 PIT 的首选，且无需代理即可直连**；行业历史 PIT 三个免费源都只有“当前快照 + 更新时间”，不能算严格历史 PIT。WeStock 无需代理，可作为财务股本与行业快照的交叉验证。

## 1. 实测结果（均无代理）

| 数据源 | 总股本 | 行业 | 上市/退市日期 | PIT 能力 | 实测 |
|---|---|---|---|---|---|
| **baostock** | ✅ `query_profit_data` 提供 `totalShare`/`liqaShare` + `pubDate`/`statDate` | ✅ `query_stock_industry` 当前行业 + `updateDate` | ✅ `query_stock_basic` 提供 `ipoDate`/`outDate` | 总股本可做严格 as-of PIT；行业只有快照 | 000001、600519 查询成功 |
| **WeStock** | ⚠️ `finance --type balance` 的 `PaidInCapital`（≈股本，面值 1 元时等于总股本）；`profile` 的 `regCapital` | ✅ `profile` 当前行业 | ✅ `profile` 的 `listedDate` | 财务股本按报告期 + `InfoPublDate`；行业当前快照 | 贵州茅台 profile/finance 成功 |
| **AkShare** | ✅ 函数存在（`stock_balance_sheet_by_report_em` 等） | ✅ 函数存在（`stock_board_industry_cons_em`、个股 `stock_individual_info_em`） | ✅ 函数存在 | 应可做财务股本 PIT | 本次东财接口经代理直连均失败，未能实测 |

## 2. 已落地代码

- `bridge/refdata.py`
  - `query_shares_quarters`：按 code/year/quarter 拉取 `totalShare/liqaShare/pubDate/statDate`。
  - `query_listing_basic`：拉取 `ipoDate/outDate/status`。
  - `query_industry_snapshot`：拉取当前行业与 `updateDate`，并标记 `pit_note=snapshot_not_historical`。
  - `shares_to_market_cap`：按 `pub_date` as-of 回接，**发布日前不生效**（不把未来股本回填历史）。
- 全市场数据（2026-08 拉取完成）：
  - `data/refdata/shares_pit.parquet`：49,965 行，3,163 只股票，2022-2025 季度股本
  - `data/refdata/shares_missing_codes.txt`：31 只 605 新股无 profit 数据
  - `data/refdata/listing_basic.parquet`
  - `data/refdata/industry_snapshot.parquet`

验证：平安银行 2023-05-04（其 2023Q1 股本 2023-04-25 发布后）得到 `total_share=1.940592e+10`，`market_cap≈2.0415e+11`；发布日前为 NaN，符合 PIT。

## 3. 使用命令

```powershell
# 无需代理
python bridge/refdata.py --codes 000001,600519 --years 2023,2024,2025 shares
python bridge/refdata.py --codes 000001,600519 listing
python bridge/refdata.py --codes 000001,600519 industry

# 面板构建时把 shares_pit 接入 market_cap
python bridge/panel.py --expressions config/expressions.json \
  --out data/panel_fusion.parquet \
  --shares data/refdata/shares_pit.parquet
```

## 4. 明确限制

1. **行业历史 PIT**：baostock/WeStock/AkShare 免费接口都只给当前行业（baostock 带 `updateDate`）。要严格历史行业分类，需要交易所/指数公司历史成分快照或付费数据。
2. **WeStock `PaidInCapital`**：是资产负债表的实收资本，仅当每股面值=1 元时等于总股本；与 baostock `totalShare` 存在口径差异，只作交叉验证。
3. **AkShare**：本次东财域名 `push2.eastmoney.com` 经代理被远端断开，无法实测；接口存在，待代理/网络恢复后可补测。
4. 全市场股本回填已完成；`refdata.py` 支持分片、断点续传与 500 行批量落盘。

## 5. 结论

- **总股本 PIT**：直接用 baostock，不用代理，已落地并可扩展全市场。
- **行业**：当前快照可用；历史 PIT 不可得，面板中的行业列必须标注 `snapshot_not_historical`，不得宣称严格 PIT。
- **WeStock**：无需代理，适合快速对账（`finance balance` 的 `PaidInCapital`、`profile` 的行业/上市日期）。
- **AkShare**：保留作为第三来源，待网络恢复后补测。
