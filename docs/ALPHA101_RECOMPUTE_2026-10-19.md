# alpha101 前向段重算（2026-10-19）

> 触发：上一轮发现前向窗 37 个冻结特征里 8 个不存在、28 个全 NaN，前向 IC 无效并撤回。
> 本轮重算 alpha101 前向段并重跑评分，给出第一份建立在完整特征之上的前向判读。

## 1. 为什么要重算

`artifacts/alpha101_ext.parquet` 的前向段对 `alpha028` 有系统性缺口：59 个前向日里 33 天全空，
而历史期只有 12-23%。生成该文件的脚本未纳入版本库，无法复现其前向段，因此只能重算。

## 2. 做法

在补齐后的面板（2,567,271 行 / 3,194 只，含补回的 558 只）上用项目自带
`aurumq_rl.factors.registry.ALPHA101_REGISTRY` 重算全部 107 个因子。

内存设计（本机 16 GB）：横截面算子按 `trade_date` 分区，必须全截面在场，
所以按因子维度分批而非按股票；最终数值块 2.57M x 107 x 8B 约 2.2 GB，一次性写出。

## 3. 对齐验证（决定性）

与权威源 `data/aurumq_alpha101_full.parquet` 在 1,968,826 行重叠上逐值比对：

| 项 | 结果 |
|---|---|
| 逐值完全一致的因子 | **106 / 107** |
| 唯一发散因子 | `alpha056`（max diff 0.886） |
| `alpha056` 是否在冻结 37 特征内 | **否**（`FEATURE_COLUMNS_EXCLUDED` 早已排除） |

即：冻结池用到的 26 个 alpha101 特征全部零差异。

## 4. 两个关键坑

### 4.1 漏传 industry/cap 使 18 个因子静默降级

首次重算只传了 OHLCV，结果 18 个发散因子全部是行业或市值依赖的，最大偏差 894。
原因：缺 `industry` 列时行业中性化退化成单一 UNKNOWN 桶；缺 `cap` 列时 `alpha056` 直接失败。

补上两列后 106/107 精确复现。教训：行业中性化不是可选参数，缺列不会报错，只会静默降级。

### 4.2 cs_scale 的正负 inf 相加变 NaN

`alpha028` 的 `cs_scale` 按日除以 `sum(|x|)`。而 `ts_corr` 在 194/817 天会产生正负 inf；
两者相加等于 NaN，使该日 abs_sum 为 NaN，整个截面作废。

验证方式：用独立 pandas 实现同一公式交叉验证——独立实现前向 0 空日，存储文件 33 空日，
两者对哪天为空的一致率只有 0.441。这排除了因子本身稀疏的解释。

修复：求和前把正负 inf 置为 NaN。前向覆盖 44.06% 升至 99.94%，全窗空日 4 降至 0。

## 5. 重跑结果

| 项 | 补齐前 | 现在 |
|---|---|---|
| 前向 B2 行数 | 95,285 | **117,791** |
| 前向代码数 | 2,150 | **2,461** |
| 冻结特征前向可用 | 1 / 37 | **37 / 37** |
| 最弱特征覆盖 | 0% | **90.57%** |

### 前向判读（预注册 v1 口径）

| 指标 | 值 |
|---|---|
| 判定 | **EXPLORATORY**（39 < 40 标签日） |
| 日 IC 均值 | **0.12840** |
| IC 胜率 | 82.05% |
| HAC IC_IR | 0.899（仅报告） |
| 前半 / 后半窗 | 0.09069 / 0.16422 |
| 剔除组前向超额 | **-2.521%** |
| 每日实际进出只数 | 93.5 |

四项 PASS 全满足、七项 HALT 全未触发，但标签日 39 < 40，按规则不得判通过。

必须区分：旧值 IC 0.12689 是在 36 个特征缺失下算出的，已撤回；
新值 0.12840 建立在完整特征与补齐域之上。两者不是同一个测量。

仍需注意：B2 从 1,585 扩到 1,996，每日实际进出从 76.7 升至 93.5 只；
变更率 0.4673 与历史 0.3805 的比较仍须按尾部规模匹配口径（见 ROBUSTNESS 文档 1.4 节）。

## 6. 复现

```powershell
python artifacts/recompute_alpha101.py --out artifacts/alpha101_ext_v2.parquet
python artifacts/verify_alpha101_recompute.py      # 期望 106/107 精确一致
python artifacts/fix_alpha028_in_file.py          # inf 求和修复
python artifacts/rebuild_feature_panel_v3.py
python artifacts/derive_expressions.py            # 补齐 5 个 alpha_qg_*
python bridge/forward_scoring.py --panel data/panel_forward_features_2026-10-16.parquet --forward-start 2026-07-20 --forward-end 2026-10-16 --industry-pit data/refdata/sw2021_industry_pit.parquet --out-dir runs/forward_shadow_2026-10-19
python bridge/forward_labels.py --out runs/forward_labels_2026-10-19/forward_labels.parquet
python bridge/forward_evaluation.py --out-dir runs/forward_evaluation_2026-10-19
```

## 7. 产物

- `artifacts/alpha101_ext_v2.parquet`（已修复 alpha028）
- `runs/alpha101_recompute_2026-10-19/`（recompute / verification / alpha028_fix）
- `runs/forward_shadow_2026-10-19/`、`runs/forward_labels_2026-10-19/`、`runs/forward_evaluation_2026-10-19/`
