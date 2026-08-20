# 2026-08-20 工作报告：全市场因子排序首轮实施

项目：`fusion-factor-pipeline`
日期：2026-08-20
性质：开发集研究报告，不构成生产批准、实盘授权或收益承诺。

## 一、今日结论

今天完成了从“事件驱动 rerank”到“全市场横截面排序”的首轮实施，并完成三域、两类标签、模型辅助对照和完整测试。

当前最重要的结论不是 Top-K 收益，而是排序地基的相对强弱：

1. 当前面板覆盖的沪深主板可交易集合上，B2 中小盘域的行业/市值中性残差秩是最平衡的 LambdaRank 候选。
2. B3 原始秩的平均 IC 最高，但折间波动最大，NDCG 接近随机，不能据此专精。
3. Huber 回归原始秩基线在 B1 上的排序质量明显优于 B1 原始秩 LambdaRank，LambdaRank 目前不应被默认切换为主模型。
4. Top-K 收益仍没有稳定地基，2025 年后段多数组合走弱，继续把 Top-K 放在附录观察。
5. 因子方向不自动翻转。方向审阅只输出建议和风险标记，最终由人工确认后写入配置。

## 二、战略方向

### 2.1 主目标

主系统预测每日可交易股票域中下一期的横截面超额收益排序：

```text
y_excess20 = 个股 D+1 开盘 -> D+20 收盘收益 - 基准同期收益
y_rank      = 域内横截面百分位秩
```

标签日期按全市场交易日历定位，而不是按个股自身记录数跳跃。个股在所需的 D+1 或 D+20 缺价格时，标签为空。

### 2.2 事件面板降级为 legacy 诊断

`signals_m6/events.parquet` 不参与主训练、标签构造、因子选择、超参数选择或域专精决策。旧的 `events` scope 仍保留，只用于复现历史事件条件诊断。

### 2.3 三个研究域

| 域 | 定义 | 角色 |
|---|---|---|
| B1 | 当前主板面板中的全市场可交易域 | 广度基线 |
| B2 | 全市场流通市值日排名 801--3800，再与主板面板相交 | 中小盘研究域 |
| B3 | 全市场流通市值日排名 1801--3800，再与主板面板相交 | 聚焦小盘候选域 |

三个域均使用非 ST、上市满一年、当日交易和 `ADV20 >= 200 万元` 的基线过滤。当前数据实际不含创业板、科创板，因此本报告中的“全市场”不等同于完整 A 股。

### 2.4 标签并行比较

- 原始相对收益秩：保留可交易的行业和市值暴露。
- 行业/市值中性残差秩：逐日回归 `industry + log(float_mcap)` 后对残差做秩。

不预先假设中性化一定更好，使用同一时间切分和同一特征集比较。

### 2.5 因子方向纪律

本日方向审阅分组共覆盖 63 个候选：

| 分组 | 数量 | 含义 |
|---|---:|---|
| `A_transform` | 9 | 注册方向与 IC 建议方向不同，但可作为人工审阅候选 |
| `B_regime` | 14 | 市场风格变化导致历史/近期方向不同 |
| `C_unstable` | 3 | 方向或日频符号不稳定 |
| `D_insufficient` | 37 | 方向数据不足，暂不用于方向判断 |

其中 `B_regime` 和 `C_unstable` 必须谨慎处理，不能因为开发集均值 IC 而程序化翻转。方向建议文件只用于人工确认和配置变更记录。

## 三、今日实施内容

### 3.1 文档

- 修订 [TRAINING_PLAN_v3.md](TRAINING_PLAN_v3.md)：从“双域”统一为三域主实验，明确全市场排序和标签对照。
- 修订 [UNIVERSE_SPEC.md](UNIVERSE_SPEC.md)：明确逐日 PIT 市值排名、主板覆盖限制和成分快照不能回填历史主训练。
- 修订 [EXECUTION_PLAN_FULL_MARKET_2026-08-20.md](EXECUTION_PLAN_FULL_MARKET_2026-08-20.md)：记录 S0--S4 完成状态和未进入 Top-K 验收的原因。
- 新增 [FULL_MARKET_RANKING_RESULTS_2026-08-20.md](FULL_MARKET_RANKING_RESULTS_2026-08-20.md)：保存六项主矩阵和辅助模型的统一比较。

### 3.2 代码

- [universe.py](../bridge/universe.py)：生成 B1/B2/B3 逐日域掩码。
- [labels.py](../bridge/labels.py)：生成 20 日收益、原始秩和中性残差秩标签。
- [ranking.py](../bridge/ranking.py)：执行全市场 LambdaRank、20 日 purged walk-forward 和 IC/NDCG 评估。
- [baselines.py](../bridge/baselines.py)：执行 Huber 连续收益和 Huber 原始秩基线。
- [panel.py](../bridge/panel.py)：默认构建 full-market panel，显式标注 `panel_scope`。
- [dev_lockbox.py](../bridge/dev_lockbox.py)：只调整重复日期错误信息，锁箱拒绝行为不变。

## 四、真实训练结果

共同设定：开发集 2023-06-01 至 2025-12-31；20 个交易日标签；训练末尾 purge gap=20；种子 42/43/44 集成；首折没有训练，实际评估 3 折；指标优先看日均 Spearman IC、HAC IC_IR、NDCG 和折间稳定性。

### 4.1 三域 × 两类标签 LambdaRank

| 实验 | 日均 IC | HAC IC_IR | NDCG@5 增量 | NDCG@20 增量 | 折间 IC 标准差 |
|---|---:|---:|---:|---:|---:|
| B1 原始秩 | 0.0434 | 0.0620 | 0.0131 | 0.0133 | 0.0105 |
| B1 中性秩 | 0.0706 | 0.1874 | 0.0254 | 0.0317 | 0.0122 |
| B2 原始秩 | 0.0709 | 0.1152 | 0.0092 | 0.0075 | 0.0281 |
| B2 中性秩 | **0.0861** | 0.2087 | 0.0165 | 0.0199 | **0.0098** |
| B3 原始秩 | **0.0946** | 0.1650 | 0.0004 | 0.0040 | 0.0417 |
| B3 中性秩 | 0.0862 | **0.2190** | 0.0091 | 0.0058 | 0.0173 |

所有六项的三折 IC 均同号，但这只能说明没有直接反向，不能等同于稳定可交易。B3 原始秩从第一折 IC `0.0522` 上升到第三折 `0.1356`，时变特征明显。

### 4.2 Huber 和 10 档辅助对照

| 实验 | 模型与目标 | 日均 IC | HAC IC_IR | NDCG@5 增量 | 折间 IC 标准差 |
|---|---|---:|---:|---:|---:|
| A0 | Huber 回归 `y_excess20` | 0.0786 | 0.1581 | 0.0103 | 0.0287 |
| A1 | Huber 回归 `y_raw_rank20_b1` | **0.0859** | 0.1438 | **0.0453** | 0.0257 |
| C | LambdaRank，10 档有序 relevance | 0.0468 | 0.0660 | 0.0099 | 0.0133 |

模型对照说明：在 B1 原始秩目标上，Huber 回归的 IC 和 NDCG 均优于 100 档 LambdaRank 及 10 档 LambdaRank。因此下一轮应保留 Huber 为主基线，同时继续比较 B2 中性秩，而不是直接把 LambdaRank 设为唯一模型。

### 4.3 Top-K 只作附录

整体 Top-5 平均原始超额收益：

| 实验 | Top-5 平均超额收益 | Top-5 日胜率 |
|---|---:|---:|
| B1 原始秩 | -0.0002 | 49.6% |
| B1 中性秩 | -0.0013 | 46.6% |
| B2 原始秩 | -0.0021 | 49.4% |
| B2 中性秩 | 0.0000 | 50.0% |
| B3 原始秩 | 0.0024 | 45.2% |
| B3 中性秩 | 0.0009 | 48.9% |

这些是重叠 20 日标签窗口上的描述性结果，未扣交易成本，没有执行模型和基准超额对照，不能用于收益验收，也不能反向调参。

## 五、数据与安全边界

- 全市场面板：1,968,826 行、3,178 只股票、629 个交易日。
- `(date, code)` 主键重复数：0。
- `panel_scope` 全部为 `full_market`。
- 主面板不读取 `events.parquet`，不含 `legacy_score`、`composite_score` 等事件专属输出。
- v6 健康检查：版本 `6.5.0.dev0`；2026-01-01 至 2026-07-17 的 M8 OOS 已消费。本日没有读取、重跑、调参或修改该 OOS，也没有修改 v6 仓库。
- 行业字段仍是当前快照，不是完整历史 PIT 行业库；中性标签结果需要行业 PIT 敏感性检验后再定稿。

## 六、验证与产物

验证结果：

```text
22 passed
git diff --check passed
```

本地大文件按仓库规则不提交：

- `data/panel_fusion_full_market.parquet`
- `data/panel_fusion_full_market_universe_summary.csv`
- `runs/full_market_*/metrics.json`
- `runs/full_market_*/report.md`

它们已经在结果文档中记录路径、口径和关键指标，可用以下命令复现主面板和训练：

```powershell
python bridge/panel.py --scope full_market --expressions config/expressions.json --out data/panel_fusion_full_market.parquet
python bridge/ranking.py --panel data/panel_fusion_full_market.parquet --universe b2 --target neutral_rank --out-dir runs/full_market_lambdarank_b2_neutral
python bridge/baselines.py --panel data/panel_fusion_full_market.parquet --target raw_rank --out-dir runs/full_market_huber_a1_raw_rank
```

## 七、下一阶段

1. 以 Huber A1 和 B2 中性秩为主线做时间/风格归因，确认高 IC 是否来自稳定的可解释暴露。
2. 做 starter 因子消融和方向稳定性报告，特别审阅 `B_regime`、`C_unstable` 组，不自动翻转。
3. 对行业快照缺失历史 PIT 的影响做敏感性检验。
4. 只有排序质量跨时间稳定后，才进入因子冻结、Top-K 净收益、右尾捕获、regret、基准超额和成本敏感性验收。
