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

## 5. 行业列的纯度量测（2026-10-17 追加，直接测量）

本节把上文"行业只有快照"的原则性判断，升级为对**实际冻结面板**的直接量测。

| 量测项 | 结果 |
|---|---|
| 冻结面板 `panel_fusion_full_market.parquet` 行业列跨时间变化 | 629 个交易日内，**每只股票的不同行业数最大 = 1**（3,178 只全部不变） |
| 行业取值 | 31 个，零缺失 |
| 上游来源 `honest_backtest/data/stock_sector_map.csv` | 3,194 行，仅 `code,industry` 两列，**无任何日期/更新列** |
| demo 快照 vs v6 映射表分类体系 | demo 用证监会门类代码（`J66金融业`、`C15酒饮料和精制茶制造业`），v6 用短名（`银行`、`食品饮料`） |
| 两源重叠代码冲突率 | 2/2 全部冲突（分类体系不同，非数据错误） |

**结论（纯度验证：不通过）**：冻结面板的行业字段是**静态快照**，不是 PIT。该口径正在**已定案的 C 轨结果与实际生效的行业中性化中生效**，属于既成事实而非潜在隐患。据此：

1. 行业 PIT **不进入任何生产路径**（符合 HANDOFF 阶段 D-5 的既定判据）；
2. `industry_code`/`industry` 相关的一切行业中性化与行业暴露披露，**必须标注为快照口径**，不得声称 PIT；
3. 要做真正的行业 PIT，需用户提供**含生效日的真实历史行业分类数据**，且必须同时解决分类体系对齐（证监会门类代码 ↔ v6 短名）；
4. 在获得该数据前，无法开展 HANDOFF §4 阶段 F 第 9 项的"行业 PIT 影子评分器敏感性实验"。

### 5.1 已解决（2026-10-18）：申万 SW2021 官方历史分类表

用户提供的 `https://www.swsresearch.com/swindex/pdf/SwClass2021/StockClassifyUse_stock.xls`
**带 `计入日期`，是真正的历史 PIT**，上文的"只能拿到快照"结论已被推翻：

- 12,929 行 / 5,934 只 / 生效日 1990-01-01 ~ 2026-10-14；一级 38 / 二级 194 / 三级 553。
- 纯度：`(code, effective_date)` 零重复、同股票生效日严格递增（0 例违反）、
  面板 as-of 连接**未匹配 0 / 2,537,139**、抽样 5 个交易日覆盖率 **100%**。
- 已固化：`data/refdata/sw2021_stock_classify_raw.xls`（sha256 `0bd177b9...`）+
  `data/refdata/sw2021_industry_pit.parquet` + `.meta.json`，今后无需再依赖外网与证书豁免。
- **量测结果**：冻结面板 3,194 只**全部**行业不变；申万 PIT 显示其中 **156 只确实变更过**
  （最多 3 次，影响 4.92% 的 code-day）。即上文判断成立。
- **敏感性判读**：底部 10% 重合率 **95.79%**（> 85% 门槛）、日 rank 相关 0.992、
  差异集完全对称（404 vs 404）且市值/流动性/ST 无富集 → **快照隐患关闭**。
  详见 `docs/INDUSTRY_PIT_2026-10-18.md`。

因此第 3 条限制应更新为：**分类体系对齐问题已解决**（SW 一级代码经 31 个行业中文名对照验证，
无歧义代码）；行业 PIT 现已可用，但**冻结的 C 轨结果仍沿用快照口径**，
是否切换到 PIT 需另立预注册（本条不构成切换许可）。

### 5.2 基准指数 000985 的地平线缺口已闭合（2026-10-18）

前向标签 `y_excess20 = stock_ret20 − benchmark_ret20` 依赖 `benchmark_open/close`，
而这两个字段全部取自本地 `_bench_000985.csv`。该文件**止于 2026-07-17**，
baostock **不提供** 000985（`sh.000985` 查询返回 0 行，其余 `sh.000300/000001/000852/000905/000016`
均正常返回至 2026-10-16）。结果是 2026-07-20 之后 **158,134 行基准为空**，
前向窗口的 `y_excess20` **全部为 NaN**，前向 IC 在结构上不可计算。

**采用的源**：腾讯行情接口（`web.ifzq.gtimg.cn`），实测可用；东方财富接口在同一时段
反复返回 `ProxyError / RemoteDisconnected`，未采用。

**强制验证（不通过就不写盘）**：

| 检查 | 结果 |
|---|---|
| 重叠交易日 | **1,098** |
| open / high / low / close 的 max abs diff | **全部 = 0**（逐值完全相同） |
| 本地归档日期缺失数 | **0** |
| 扩展段 | 59 行，2026-07-20 ~ 2026-10-16 |

这条验证不是形式：**腾讯返回的字段顺序是 `date, open, close, high, low, volume`**，
close 在 high/low 之前，与本地 CSV 顺序相反。若解析顺序照抄本地，会把收盘价与最高价对调，
而"看起来像个指数"的表不会自己报错——只有逐值比对能抓住这一点。故测试里专门有一条
`test_verify_catches_swapped_high_and_close` 锁定该行为。

**落地方式**：扩展段单独保存为 `data/refdata/benchmark_000985_ext.parquet` + `.meta.json`
（含源 URL、重叠验证明细、输出 sha256）；**本地归档从未被改写**。
代码：`bridge/benchmark_extension.py`。

**边界（必须保留的披露）**：2026-07-17 之后的标签依赖**第二个数据源**。
即使已证明逐值一致，这个来源事实不能省略。若日后该接口不可用或改版，该段需重新验证。

## 6. 结论

- **总股本 PIT**：直接用 baostock，不用代理，已落地并可扩展全市场。
- **行业**：当前快照可用；历史 PIT 不可得，面板中的行业列必须标注 `snapshot_not_historical`，不得宣称严格 PIT。**2026-10-17 量测确认冻结面板实际就是该快照口径（见 §5）。**
- **WeStock**：无需代理，适合快速对账（`finance balance` 的 `PaidInCapital`、`profile` 的行业/上市日期）。
- **AkShare**：保留作为第三来源，待网络恢复后补测。
- **基准 000985**：2026-07-17 前用本地归档；之后用腾讯扩展段（已验证 1,098 个重叠日逐值一致），
  见 §5.2。baostock 无此代码，不能作为替代。
