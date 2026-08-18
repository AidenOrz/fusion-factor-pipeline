# P2 因子训练方案

> 状态：规划
> 前置：P0 数据底座 + P0.5 判官校准已完成
> 目标：筛选/消融期先建立稳定排序质量；因子冻结后的验收期再要求 Top-K 组合跑赢上证指数（日胜率 > 50%，年化超额 > 5%）

---

## 1. 训练目标与验收标准

**验收期硬门槛（G2，因子冻结后）：**
- Top-5 日均前向收益跑赢上证指数（000001.SH）
- 日胜率 > 50%（在有信号的交易日中，Top-5 组合收益超过指数的天数占比）
- 年化超额收益 > 5%

**筛选/消融期主指标：**
- 按日截面 `EXRET20` Spearman IC 均值、每折 IC 均值与符号一致性
- HAC `IC_IR`
- `NDCG@5` / `NDCG@20`
- NDCG 相对同日随机排序基线的增量

**验收期辅助参考：**
- 单笔净R分布、右尾捕获和 regret
- legacy_score 对比：不作硬性要求，仅保留为诊断基准
- Top-K 梯度：Top-1/3/5/10 的平均前向收益应单调递减（仅验收期）

**输出要求：**
- `runs/gbdt_walkforward/metrics.json`：每折 IC、NDCG、top-K vs 上证指数超额
- `runs/gbdt_walkforward/report.md`：是否跑赢指数的明确结论
- `runs/gbdt_walkforward/feature_importance.csv`：gain + SHAP 重要性排序

---

## 2. 模型训练配置

### 2.1 基线模型（优先实现）

```python
model = lgb.LGBMRegressor(
    objective='huber',           # 对极端值更鲁棒，避免为拟合大牛股牺牲排序
    alpha=0.9,                   # Huber 分位点，只把最极端 10% 当 outlier
    n_estimators=200,
    learning_rate=0.05,
    num_leaves=31,
    min_child_samples=20,
    subsample=0.8,
    subsample_freq=1,
    colsample_bytree=0.8,
    reg_lambda=1.0,
    random_state=42,
    n_jobs=1,
    verbosity=-1,
)
```

**理由：**
- 回归目标 `EXRET20` 够用；筛选/消融期先验证按日截面 Spearman IC、HAC IC_IR、NDCG 和折间稳定性，不能用 Top-K 偶然收益替代排序地基
- `huber` 比默认 `regression` 对少数 +50% 大牛股更稳健，不会为了拟合它们而牺牲大量 ±5% 票之间的排序
- 实现简单，不需要构造 query group，调参经验成熟

### 2.2 模型集成（简单有效）

每个 fold 训练 3 个模型，种子分别为 42、43、44：

```python
predictions_list = []
for seed in [42, 43, 44]:
    model = lgb.LGBMRegressor(..., random_state=seed)
    model.fit(X_train, y_train)
    pred = model.predict(X_test)
    predictions_list.append(pred)
predictions.loc[test_mask] = np.mean(predictions_list, axis=0)
```

当前实现同时设置 `subsample_freq=1`，确保 `subsample=0.8` 下三个种子实际产生 bagging 差异；LightGBM 不可用时才使用 sklearn 的鲁棒 L1 直方图 GBDT 回退。

**理由：**
- 单模型对随机种子敏感（尤其是 subsample=0.8 时），集成能降低方差
- 线性平均已足够，无需复杂 stacking
- 成本可控：3 倍训练时间，换取稳定性提升

### 2.3 何时需要 LambdaRank？

**只有出现这种情况时才换：**
- Spearman IC 尚可（≥ 0.05），但 Top-3/5 对上证指数的日胜率 < 50%
- 说明模型"排对了大致顺序，但 top 端不够精确"

**切换方式：**
```python
model = lgb.LGBMRanker(
    objective='lambdarank',
    metric='ndcg',
    ndcg_eval_at=[3, 5, 10],
    ...
)
# fit 时传入 group 参数
groups = work.loc[train_mask].groupby('date').size().to_numpy()
model.fit(X_train, y_train, group=groups)
```

**当前建议：** 先用回归，达标就不折腾 LambdaRank。

---

## 3. 特征工程补充

### 3.1 当前特征（已有）
- v6 四因子：`SQZ_ATR_REL__score` / `SQZ_RANGE_ABS__score` / `TRG_VOL_EXP__score` / `POS_EMA20_BAND__score`
- starter 8 个：mom_20 / rev_5 / vol_20 / trend_10 / corr_pv_10 / range_15 / amt_ratio_5 / vwap_dev_10
- LLM 生成：DeepSeek 挖矿 + QuantGPT 求值的表达式因子
- aurumq-rl 因子库（可选）：alpha101（101 个）/ gtja191（191 个）

### 3.2 新增特征（按优先级）

**方向审阅先于特征扩展：** `factor_selection.py` 在选择期前段输出 `direction_review.csv`。它记录注册方向、选择期/近期 IC、60 日滚动 IC 的符号一致性与换向次数、市场风格反转和人工变换标记；程序只给建议，不自动翻转因子或继续训练。`regime_sensitive`、`unstable` 与 `insufficient_data` 因子应人工审阅后才允许写入后续配置。

**优先级 1：截面归一化版本**

为每个原始因子生成：
- `{原列名}_cs_rank`：当日截面百分位排名（0-1）
- `{原列名}_cs_z`：当日截面 zscore

```python
for col in expression_columns:
    panel[f'{col}_cs_rank'] = panel.groupby('date')[col].rank(pct=True)
    panel[f'{col}_cs_z'] = panel.groupby('date')[col].transform(
        lambda x: (x - x.mean()) / x.std() if x.std() > 0 else 0
    )
```

**理由：** LightGBM 天然对单调变换不变，但截面归一化能直接编码"这只票在当日全市场的相对位置"，减少决策树分裂次数。

实现输出 `{原列名}_cs_winsor`（0.01/0.99）以及 `_cs_rank`、`_cs_z`。行业中性因子列表和交叉项种子必须来自选择期报告中的 `feature_augmentation_plan.json`，不能在全周期面板上重新按 IC 选取。

**优先级 2：行业中性残差**

对 |IC| > 0.02 的因子，回归到行业哑变量，残差作为新因子（`{原列名}_ind_neutral`）：

```python
# 需要先跑一次 factor_selection.py 得到 IC 表
high_ic_factors = ic_table[ic_table['ic_mean'].abs() > 0.02]['factor'].tolist()
for col in high_ic_factors:
    if 'industry_code' in panel.columns:
        panel[f'{col}_ind_neutral'] = panel.groupby('date').apply(
            lambda g: sm.OLS(g[col], pd.get_dummies(g['industry_code'])).fit().resid
        ).reset_index(level=0, drop=True)
```

**理由：** 去除行业共同因子，捕获个股 alpha。

`industry_code` 为 NaN 或 `-1` 的股票不参与该日行业回归，残差保持 NaN；不能把所有未知行业合成一个伪行业组。

**优先级 3：二阶交叉项（有预算再做）**

选 IC 绝对值最高的 5 个因子，两两相乘、两两相除，得 20 个交叉因子：

```python
top5 = select_top_abs_ic_factors(ic_table, top_n=5)
for i, f1 in enumerate(top5):
    for f2 in top5[i+1:]:
        panel[f'{f1}_x_{f2}'] = panel[f1] * panel[f2]
        panel[f'{f1}_div_{f2}'] = panel[f1] / panel[f2].replace(0, np.nan)
```

选择逻辑固定按 `abs(ic_mean)` 排序，并对分母为零/接近零的比值返回 NaN。面板 CLI 可使用 `--augmentation-plan` 显式载入选择期计划；计划只负责生成特征，不会自动改变方向配置。

**理由：** 捕获非线性交互（如"高动量 × 低波动"组合），但特征数爆炸，优先级最低。

---

## 4. 超参搜索（可选）

**当前参数：** 固定写死 `n_estimators=200, learning_rate=0.05, num_leaves=31`

**优化方案（有预算再做）：**

在第一个 fold 上做 30 次随机搜索：
```python
param_space = {
    'n_estimators': [100, 200, 300],
    'learning_rate': [0.03, 0.05, 0.08],
    'num_leaves': [15, 31, 63],
    'min_child_samples': [10, 20, 40],
    'colsample_bytree': [0.6, 0.8, 1.0],
}
```

选验证集 Spearman IC 最高的配置，固定用于后续 fold。

**注意：** 预算有限时跳过，固定参数已够用。

---

## 5. 训练流程检查清单

### 5.1 数据准备
- [ ] `data/panel_fusion.parquet` 已生成，包含 v6 四因子 + starter + LLM 表达式
- [ ] 因子列数在 50–80 之间（`factor_selection` 去冗余后）
- [ ] 所有因子缺失率 < 20%，inf 已替换为 NaN
- [ ] `EXRET20` 目标变量已附加（前向 20 日超额收益）

### 5.2 Walk-forward 配置
- [ ] `n_splits=4`，gap=20 个交易日（与 EXRET20 持有期一致，防止标签泄漏）
- [ ] 每折训练集末尾 20 日被 purge 掉，不进训练
- [ ] 特征选择期结束后另留 20 个交易日标签净空，walk-forward 评估从第 21 个交易日开始
- [ ] 测试集不回传调参（单次揭盲纪律）

### 5.3 评估指标
- [ ] 筛选/消融期每折报告：按日截面平均 Spearman IC、HAC IC_IR、NDCG@5/@20
- [ ] 筛选/消融期报告：折间 IC 均值、符号一致性和标准差
- [ ] 验收期才报告：Top-1/3/5/10 平均 EXRET20、Top-5 vs 上证指数日胜率、年化超额、右尾捕获和 regret
- [ ] 年化超额明确标记为重叠 EXRET20 的近似口径（均值超额 × 252 / 20），不当作独立日收益复利
- [ ] 全局报告：4 折汇总后的 Top-5 vs 指数结果
- [ ] Feature importance：gain + SHAP（如果 LightGBM 可用）

### 5.4 产物完整性
- [ ] `runs/gbdt_walkforward/metrics.json`
- [ ] `runs/gbdt_walkforward/report.md`（markdown 报告，含"是否跑赢指数"明确结论）
- [ ] `runs/gbdt_walkforward/feature_importance.csv`

---

## 6. 失败应对

**G2 不通过（Top-5 未跑赢上证指数）：**
1. 检查 IC 时间序列：是否在最近 6 个月系统性反转？
2. 检查因子族贡献：v6/starter/llm/alpha101 哪族拖后腿？
3. 检查 Top-K 梯度：Top-1 是否显著高于 Top-10？如果无梯度，说明排序器失效
4. **不调参**：整轮记录进 `failures/`，回到 P1 重新挖因子

**Spearman IC 尚可但 Top 端胜率不足：**
- 尝试切换到 `LGBMRanker` + `objective='lambdarank'`
- 或加大 Top-K 惩罚：用 `focal_loss` 类权重放大高收益票的梯度

**特征重要性全集中在 market_cap：**
- 说明模型退化为"买小盘股"，丢失 alpha
- 强制加入市值中性化特征（`factor_ind_neutral` 系列）
- 或在训练时对 market_cap 加 L1 惩罚

---

## 7. 后续路线（G2 通过后）

### 7.1 P3 观察锁箱
- 使用 `bridge/dev_lockbox.py` 追加 clean-date（2026-07-18 起）的实盘信号
- 每日运行 `observe` 查看 Top-5 选股，**不消耗官方 v6 锁箱的揭盲次数**
- 积累 60+ 个交易日后，评估前向收益分布

### 7.2 在线更新（生产部署后）
- 每周用最近 60 个交易日数据做增量训练（`init_model` 参数传入上周模型）
- 保留最近 4 周的模型做滚动集成（4 个模型等权平均）
- 记录每周验证集 NDCG，连续 4 周下降 > 20% 触发全量重训告警

---

## 8. 时间预算

| 步骤 | 预计时间 | 备注 |
|---|---|---|
| 特征工程（截面归一化） | 0.5 天 | 优先级 1 |
| 模型训练（4 折 × 3 种子） | 0.5 天 | 基线模型 |
| 评估报告生成 | 0.5 天 | metrics.json + report.md |
| **总计** | **1.5 天** | 不含超参搜索和交叉项 |

**预算不足时删减顺序：** 超参搜索 → 交叉项 → 行业中性化。只保留截面归一化 + 3-seed 集成。

---

## 9. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| LightGBM 安装失败 | 低 | 高 | 已有 sklearn `HistGradientBoostingRegressor` 回退 |
| IC 尚可但 Top 端差 | 中 | 中 | 切换 LambdaRank |
| 因子全部失效 | 低 | 高 | 回到 P1 重新挖矿，不调参 |
| 市值因子主导 | 中 | 中 | 加入市值中性化特征 |
| 计算资源不足 | 低 | 低 | 减少 aurumq 因子数量，只用 alpha101 前 50 个 |

---

## 10. 完成标志

**P2 阶段验收：**
- [ ] `runs/gbdt_walkforward/report.md` 明确写出"Top-5 跑赢上证指数"
- [ ] 日胜率 > 50%，年化超额 > 5%
- [ ] 特征重要性前 10 名中至少 3 个来自非市值因子
- [ ] 代码可复现：相同输入 + 相同种子 → 相同 metrics.json（SHA-256 一致）

**达标后进入 P3 观察锁箱，不达标整轮记入 `failures/`，回到 P1。**
