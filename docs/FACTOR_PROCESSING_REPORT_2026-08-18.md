# 因子处理与训练方案实施报告

日期：2026-08-18  
项目：`fusion-factor-pipeline`  
报告性质：开发集描述性研究与工程实施记录，不是生产批准或正式锁箱结论

## 1. 先看结论

本轮已把方案中的关键工程环节落到代码中：

1. GBDT 基线使用 LightGBM `LGBMRegressor(objective="huber")`，并对 `42/43/44` 三个种子做预测均值集成。
2. `EXRET20` 的 purged gap 固定为 20 个交易日；特征选择结束后还会额外保留 20 个交易日的标签净空。
3. 截面 rank、z-score、winsorize、行业中性残差和乘除交叉项已经实现，但行业中性与交叉项必须使用选择期计划显式指定的因子列表。
4. 方向不会被程序自动翻转。程序只生成 `direction_review.csv`，把可能由市场风格变化或频繁换向造成的风险标记出来。
5. 基准评估增加了 NDCG 和年化超额字段；年化超额明确是重叠 `EXRET20` 的近似口径，不当作独立日收益复利。筛选/消融期以按日截面平均 Spearman IC、HAC IC_IR、NDCG 和折间一致性为主指标，Top-K 与上证超额降为附录；因子冻结后的验收期才把 Top-K 净R、右尾捕获和 regret 升为主指标。

当前最重要的判断是：方向报告适合作为人工审阅清单，不适合直接把所有建议写入配置。特别是行业中性候选数量较多，应该分批做增量消融，而不是一次性扩充模型。

## 2. 本轮完成了什么

### 2.1 方向审阅

实现位置：[factor_selection.py](../bridge/factor_selection.py) 的 `build_direction_review`。

每个因子会记录：

- 注册方向：来自表达式配置；没有注册信息的因子记为 `unknown`
- `selection_ic_mean`：选择期平均截面 IC
- `recent_ic_mean` 与 `historical_ic_mean`
- 60 日滚动 IC 的符号一致性和换向次数
- 原始日频换向次数，用于区分短期噪声和持续状态变化
- `market_style_change`
- `frequent_direction_switch`
- `direction_transform_required`
- `manual_review_required`
- `review_status`：`stable`、`regime_sensitive`、`unstable`、`insufficient_data`

程序只输出 `recommended_direction`，不会修改因子值、不会修改表达式配置，也不会自动继续训练。

### 2.2 特征扩展

实现位置：[feature_engineering.py](../bridge/feature_engineering.py)。

对于显式指定的原始因子，按交易日截面生成：

| 类型 | 列名后缀 | 处理方式 |
|---|---|---|
| 百分位排名 | `_cs_rank` | 当日截面 `rank(pct=True)` |
| 截面 z-score | `_cs_z` | 当日均值和样本标准差标准化 |
| winsorize | `_cs_winsor` | 当日 1% 和 99% 分位数截断 |
| 行业中性残差 | `_ind_neutral` | 当日因子对行业哑变量回归后的残差 |
| 乘法交叉 | `_x_` | 两个显式因子相乘 |
| 除法交叉 | `_div_` | 分母为零或接近零时返回 NaN |

未知行业（`NaN`、`-1`、`-1.0`）不会组成一个伪行业组，相关残差保持 NaN。

### 2.3 选择期增强计划

`factor_selection.py` 会写出 `feature_augmentation_plan.json`，其中包含：

- 截面变换的原始因子列表
- 选择期 `abs(IC) > 0.02` 的行业中性候选
- 按 `abs(IC)` 排序得到的 Top-5 交叉项种子
- 预期交叉特征数量
- `selection_only=true`
- `automatic_direction_flip=false`

面板构建可以通过 `--augmentation-plan` 显式加载该计划。计划只负责生成候选特征，不能改变方向配置。

### 2.4 模型与评估

实现位置：[gbdt.py](../bridge/gbdt.py) 和 [benchmark.py](../bridge/benchmark.py)。

LightGBM 模型的关键配置：

```text
objective=huber
alpha=0.9
subsample=0.8
subsample_freq=1
colsample_bytree=0.8
random_state=42/43/44
```

每个 walk-forward fold 训练三个模型，测试预测取均值。`subsample_freq=1` 是必要配置，否则不同 seed 可能没有实际 bagging 差异。

评估指标包括：

- Spearman IC
- Top-1/3/5/10/20 的平均前向收益
- Top-K 对上证指数的日胜率
- NDCG
- `annualized_excess`

年化超额采用：

```text
平均重叠 EXRET20 超额 × 252 / 20
```

该数值是研究比较指标，不等价于每天真实复利交易的年化收益。

## 3. 开发集方向审阅结果

实际报告目录：

[runs/factor_selection_v2_direction_review](../runs/factor_selection_v2_direction_review)

关键产物：

- [direction_review.csv](../runs/factor_selection_v2_direction_review/direction_review.csv)
- [feature_augmentation_plan.json](../runs/factor_selection_v2_direction_review/feature_augmentation_plan.json)
- [selection_manifest.json](../runs/factor_selection_v2_direction_review/selection_manifest.json)
- [factor_ic_table.csv](../runs/factor_selection_v2_direction_review/factor_ic_table.csv)
- [rolling_ic_by_family.csv](../runs/factor_selection_v2_direction_review/rolling_ic_by_family.csv)

### 3.1 时间切分

| 项目 | 日期 |
|---|---|
| 选择期开始 | 2023-06-01 |
| 选择期结束 | 2024-09-12 |
| 评估期理论起点 | 2024-10-22 |
| 标签净空 | 20 个交易日 |
| 目标 | `EXRET20` |

2024-09-12 的 `EXRET20` 仍然会读取后续价格，因此不能把 2024-09-13 直接当作无污染评估起点。报告中的 2024-10-22 是保留 20 个中间交易日后的起点。

### 3.2 状态分布

当前共审阅 322 个候选因子：

| 状态 | 数量 | 含义 |
|---|---:|---|
| `stable` | 268 | 60 日滚动 IC 方向较稳定，仍需结合收益指标验证 |
| `regime_sensitive` | 14 | 近期与历史 IC 均值发生方向变化，需重点人工审阅 |
| `unstable` | 3 | 滚动方向一致性不足或换向较频繁 |
| `insufficient_data` | 37 | 有效 IC 天数不足，不能据此判断方向 |

额外标记：

- `direction_transform_required=true`：9 个
- `market_style_change=true`：14 个
- `frequent_direction_switch=true`：1 个
- `manual_review_required=true`：63 个

这里的 `frequent_direction_switch` 使用 60 日滚动 IC 判断；报告同时保留日频换向次数，避免把单日 IC 噪声直接当作风格变化。

### 3.3 需要重点审阅的因子

9 个注册方向与选择期建议方向不同：

| 因子 | 注册方向 | 建议方向 | 处理意见 |
|---|---|---|---|
| `alpha_qg_amt_ratio_5` | long | short | 不自动翻转，人工确认后再决定 |
| `alpha_qg_corr_pv_10` | long | short | 检查表达式语义和方向定义 |
| `alpha_qg_f001` | long | short | 方向稳定但与注册方向冲突 |
| `alpha_qg_f005` | long | short | 同时检查滚动稳定性 |
| `alpha_qg_f006` | long | short | 方向冲突，暂不进入正式配置 |
| `alpha_qg_f008` | long | short | 方向冲突，暂不进入正式配置 |
| `alpha_qg_f010` | long | short | 方向冲突，保留人工决定 |
| `alpha_qg_mom_20` | long | short | 与动量表达式原假设不一致 |
| `alpha_qg_vwap_dev_10` | long | short | 检查 VWAP 偏离的经济含义 |

14 个 `regime_sensitive` 因子包括 `alpha004`、`alpha059`、`alpha071`、`alpha091`、`gtja_001`、`gtja_058`、`gtja_072`、`gtja_073`、`gtja_082`、`gtja_090`、`gtja_125`、`gtja_181`、`gtja_183` 等。它们不应该因为全周期平均 IC 为正或为负就直接进入生产方向配置。

## 4. 特征扩展计划的实际规模

选择期报告给出：

- 行业中性候选：259 个
- 交叉种子因子：5 个
- 交叉因子：20 个

交叉种子为：

```text
gtja_159
alpha_qg_vol_20
alpha_qg_f007
alpha_qg_f003
gtja_046
```

### 4.1 对规模的判断

259 个行业中性残差不适合一次性全部加入模型，原因是：

1. 特征数量会明显膨胀，增加 LightGBM 训练方差和解释难度。
2. 行业字段的历史 PIT 属性需要单独确认，行业中性不能自动等价于个股 alpha。
3. 这些候选是基于选择期 IC 的描述性结果，不是已经通过增量验证的正式特征。

推荐顺序：

1. 先只加入截面 rank/z-score/winsorize，观察增量效果。
2. 再从人工确认的少量稳定因子中做行业中性残差。
3. 最后加入 5 个交叉种子产生的 20 个交叉项，单独做 ablation。

## 5. 我对方案的判断

### 5.1 Huber 回归

适合作为第一版基线。`EXRET20` 存在肥尾收益，Huber 能降低极端样本对回归拟合的影响。但“Spearman IC 高就能把收益集中到 Top-K”并不成立。筛选/消融期必须先看：

- 按日截面平均 Spearman IC（目标为 `EXRET20`）
- HAC IC_IR
- `NDCG@5` / `NDCG@20`
- 相对随机排序的 NDCG 增量
- 各 walk-forward fold 的 IC 均值、符号一致性和标准差

Top-K 的净R、右尾捕获、regret 和上证超额只在因子冻结后的验收期作为主指标；当前阶段仅放在附录供参考。

### 5.2 截面归一化

这是风险最低的第一步。rank 是单调变换，树模型理论上不会获得太多新信息；z-score 和 winsorize 更可能改善跨日期尺度差异和极端值影响。因此应把它看作稳健性和可解释性增强，而不是预期必然带来 alpha。

### 5.3 行业中性

方向是合理的，但 259 个候选过多。行业中性应该作为小规模、有明确候选来源的增量实验，而不是自动对所有高 IC 因子执行。

### 5.4 二阶交叉项

5 个因子两两乘除共 20 个，规模可控，但 LightGBM 本身已能学习部分交互。交叉项必须单独报告增量效果，不能因为某个交叉项在选择期表现好就直接保留。

### 5.5 方向变换

方向变化是一个风险标签，不是自动优化机会：

- 注册方向与建议方向不同：先检查表达式定义和 long/short 语义
- 历史与近期方向相反：标记为 `regime_sensitive`
- 滚动 IC 多次换向：标记为 `unstable` 或人工审阅
- 没有足够有效天数：不作方向判断

## 6. 验证情况与未完成事项

已通过：

- 特征工程烟测
- 方向审阅和 20 个交易日净空烟测
- Huber 三种子小样本 walk-forward
- 按日截面 IC、HAC IC_IR、NDCG 和折间一致性指标测试
- NDCG 与年化超额指标烟测
- 原有 bugfix/review smoke tests
- Python 编译检查

完整 `pytest tests -q` 当前为 `11 passed, 1 failed`。唯一失败是已有锁箱测试的错误文案不匹配：

```text
tests/test_dev_lockbox.py::test_duplicate_or_out_of_order_append_rejected
```

测试要求错误信息包含 `already sealed`，当前实现返回“必须严格晚于上一日期”。这不是本轮因子处理改动造成的失败，本轮没有修改锁箱逻辑。

尚未完成：

1. 尚未根据 `direction_review.csv` 自动改写任何因子方向。
2. 尚未把 259 个行业中性候选一次性加入正式面板。
3. 尚未基于增强特征重跑完整 GBDT 验收，因此本报告不宣称 Top-5 已达到收益门槛。
4. 尚未把开发集描述性结果推进到 v6 官方锁箱或生产候选。

## 7. 建议的下一步操作

本轮方向清单已形成一个显式人工候选池，实施和第一轮描述性结果见 [MANUAL_FACTOR_POOL_NEXT_STEP_2026-08-18.md](MANUAL_FACTOR_POOL_NEXT_STEP_2026-08-18.md)。该候选池不会自动激活，也没有把 short 建议写回原始表达式配置。

### 第一步：人工处理方向报告

先查看 [direction_review.csv](../runs/factor_selection_v2_direction_review/direction_review.csv)，优先处理：

- `direction_transform_required=true`
- `market_style_change=true`
- `review_status=unstable`
- `review_status=insufficient_data`

人工确认后，另行维护一份明确的因子配置；程序不自动把建议写回表达式配置。

### 第二步：建立小规模增强面板

使用 `feature_augmentation_plan.json` 作为候选来源，但先只选择人工确认的稳定因子：

```powershell
python bridge/panel.py `
  --augmentation-plan runs/factor_selection_v2_direction_review/feature_augmentation_plan.json `
  --expressions config/expressions_combined.json `
  --extra-factors data/aurumq_alpha101_full.parquet,data/aurumq_gtja191_full.parquet `
  --out data/panel_fusion_v2_features.parquet
```

实际使用前应把计划中的行业中性列表缩小到人工确认的子集，避免一次性产生过大的面板。

### 第三步：按增量顺序评估

建议分三轮比较：

| 轮次 | 特征 | 目的 |
|---|---|---|
| A | 原始因子 + 截面变换 | 验证尺度稳健性 |
| B | A + 少量行业中性残差 | 验证是否削弱行业共同暴露 |
| C | B + 20 个交叉项 | 验证交互是否带来真正增量 |

每轮只生成描述性报告，不根据单次结果自动调参、翻转方向或进入生产锁箱。

## 8. 相关文件

- 方案文档：[TRAINING_PLAN.md](TRAINING_PLAN.md)
- 方向报告：[direction_review.csv](../runs/factor_selection_v2_direction_review/direction_review.csv)
- 特征增强计划：[feature_augmentation_plan.json](../runs/factor_selection_v2_direction_review/feature_augmentation_plan.json)
- 选择清单：[selected_features.json](../runs/factor_selection_v2_direction_review/selected_features.json)
- GBDT 实现：[gbdt.py](../bridge/gbdt.py)
- 特征工程实现：[feature_engineering.py](../bridge/feature_engineering.py)
- 基准评估实现：[benchmark.py](../bridge/benchmark.py)
