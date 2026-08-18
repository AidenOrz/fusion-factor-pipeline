# 下一轮因子训练方案（TRAINING_PLAN v2）

> 编制：2026-08-17 · 依据：18 表达式家族分析 + 316 池训练结果 + 修复后代码状态
> 状态：方案待批准执行
> 与上一轮差异：**从"堆因子"转向"重构低冗余可解释因子池"**

---

## 0. 上一轮教训（必须吸取）

| 教训 | 数据依据 | 本轮对策 |
|:---|:---|:---|
| 堆因子没用 | 316 池未跑赢上证，精简池（22 因子）Top1 反而跑赢 | **重构 20-40 低冗余因子**，不继续扩池 |
| 方向标签普遍错 | mom_20/f001/f008/vwap 全负 IC 却注册 long | **方向先验用 IC 符号修正**，负 IC 注册 short |
| LLM 挖矿换皮 | 10 个 LLM 表达式 7 个是 starter 换皮 | 本轮以**确定性表达式为主**，LLM 仅补充 |
| 相关性/IC 指标此前失真 | fillna(0.5) + IC_IR 未校正 | 已修复（HAC 校正 + 按日截面相关） |

---

## 1. 训练目标

1. **构造 24 个低冗余可解释因子**（3 个实证家族 × 8 个变体），全部确定性表达式，预注册方向
2. **purged walk-forward 4 折**（gap=10）评估：目标 = 打败**上证指数**（硬验收）
3. 与 legacy_score / 上一轮精简池做同窗口对比（诊断参照）
4. 达标 → 干净数据（2026-07-18 起）最终观察 → 再进 v6 官方锁箱

---

## 2. 因子池设计（24 个 = 3 家族 × 8 变体）

### 家族 A：低波动（IC 最强，+0.23）

| # | 表达式 | 方向 | 假设 |
|:--:|:---|:---:|:---|
| A1 | `rank(-ts_std(returns, 10))` | long | 10 日低波动 |
| A2 | `rank(-ts_std(returns, 20))` | long | 20 日低波动（=vol_20/f003，做基准） |
| A3 | `rank(-ts_std(returns, 60))` | long | 60 日低波动 |
| A4 | `rank(-ts_std((high-low)/close, 20))` | long | 20 日振幅波动 |
| A5 | `rank(-ts_mean((high-low)/close, 20))` | long | 20 日平均振幅 |
| A6 | `rank(-ts_std(returns, 20) / ts_mean(returns, 20))` | long | 波动/收益比（变异系数） |
| A7 | `rank(-ts_std(returns, 5))` | long | 短期低波动（事件后） |
| A8 | `rank(-atr_20 / close)` | long | ATR 相对价低（若算子支持） |

### 家族 B：反转（IC 强，+0.21）

| # | 表达式 | 方向 | 假设 |
|:--:|:---|:---:|:---|
| B1 | `rank(-ts_delta(close, 3) / close)` | long | 3 日反转（short 方向 = 买跌） |
| B2 | `rank(-ts_delta(close, 5) / close)` | long | 5 日反转（=rev_5 基准） |
| B3 | `rank(-ts_delta(close, 10) / close)` | long | 10 日反转 |
| B4 | `rank(-ts_delta(close, 20) / close)` | long | 20 日反转（=mom_20 反号修正） |
| B5 | `rank(-ts_mean(returns, 5))` | long | 5 日均收益反转（=f002） |
| B6 | `rank(-ts_mean(returns, 10))` | long | 10 日均收益反转 |
| B7 | `rank(-ts_decay_linear(returns, 5))` | long | 衰减加权反转 |
| B8 | `rank(close / vwap)` 取负 | short | VWAP 偏离反转（vwap_dev_10 反号） |

### 家族 C：区间收缩（f007 式，+0.22）

| # | 表达式 | 方向 | 假设 |
|:--:|:---|:---:|:---|
| C1 | `rank(-ts_mean((high-low)/close, 5))` | long | 5 日振幅收缩 |
| C2 | `rank(-ts_mean((high-low)/close, 10))` | long | 10 日振幅收缩（=f007） |
| C3 | `rank(-ts_mean((high-low)/close, 20))` | long | 20 日振幅收缩 |
| C4 | `rank(-ts_std((high-low)/close, 10))` | long | 10 日振幅波动收缩 |
| C5 | `rank(-(high-low)/close)` 与 `ts_rank(振幅, 60) < 0.3` 条件 | long | 60 日分位低振幅（突破前夜） |
| C6 | `rank(-ts_mean((close-open)/close, 10))` | long | 日内实体收缩 |
| C7 | `rank(close / ts_max(close, 60))` 取反 | short | 距 60 日高点远（低位蓄势） |
| C8 | `rank(-ts_std(ts_delta(close, 1) / close, 20))` | long | 收益二阶波动收缩 |

**总量**：24 个确定性表达式 + 上一轮已验证的 4 个 v6 因子（SQZ_ATR_REL / SQZ_RANGE_ABS / TRG_VOL_EXP / POS_EMA20_BAND）+ market_cap/股本 → **~30 特征**。

**去重**：构建后按日截面 Spearman 相关 >0.80 去重（修复后的方法），保留 IC 方向正确者。

---

## 3. 训练配置（沿用已确认参数）

```bash
python bridge/gbdt.py --panel data/panel_fusion_v2.parquet \
  --features-file config/factor_pool_v2.json \
  --mode walk-forward --out-dir runs/gbdt_walkforward_v2
```

| 参数 | 值 | 说明 |
|:---|:---|:---|
| 模型 | lightgbm.LGBMRegressor | 已装 4.7.0 |
| n_estimators / lr / leaves | 200 / 0.05 / 31 | gbdt.py 默认 |
| 目标 | EXRET20 | 20 日前向超额收益 |
| purged WF | 4 折，gap=10 | 防标签泄漏 |
| 评估 | Top1/3/5 净R vs 上证 | 硬验收 |
| 基准对比 | legacy_score + 上轮精简池 | 诊断参照 |

---

## 4. 执行步骤（含数据预算纪律）

```
① 构建 panel_fusion_v2.parquet（24 表达式 + 4 v6 因子 + 股本市值）
   —— 开发集 2023-06~2025-12（复用现有数据，不消费预算）
② 方向先验校验：用【开发集前半段 2023-06~2024-06】算 IC 符号
   —— 与预注册方向不符的因子翻转或剔除（只允许这次修正，之后冻结）
③ 写 factor_pool_v2.json + manifest（SHA-256），锁定特征集
④ purged walk-forward 评估（开发集后半段 2024-07~2025-12 是评估主力）
⑤ 若达标 → 扩展面板到 2026-07-18+ → 干净数据观察
⑥ 干净数据稳定 → 进 v6 官方锁箱
```

**数据预算纪律**：②的 IC 符号校验只在开发集前半段做一次，结果写入 manifest；④之后**不得**回头改因子或调权重。这一步就是"预注册"。

---

## 5. 待办/依赖

| 项 | 状态 |
|:---|:---|
| 24 表达式是否全部可解析（含 C5/C7 条件表达式） | 需验证 parser 支持 |
| atr_20 算子是否存在 | 需查 QuantGPT parser 算子表 |
| 面板扩展脚本（追加 2026-01~2026-08） | 需写（baostock 增量） |
| DeepSeek key（LLM 补充挖矿，可选） | 已有（gitignored .env） |

---

## 6. 验收标准

- ✅ factor_pool_v2 manifest 含 SHA-256 + 方向预注册表
- ✅ walk-forward 报告含"ML vs 上证 Top1/3/5"表
- ✅ 若 Top1 超额 > 0 且日跑赢率 > 45% → 进入干净数据观察
- ✅ 干净数据阶段 20+ 个信号日无异常 → 提交 v6 锁箱
