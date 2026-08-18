# 代码审查修复意见 — Mining & Cleaning

> 审查日期：2026-08-17
> 审查对象：`bridge/llm_miner.py`、`bridge/ops.py`、`bridge/factor_selection.py`
> 审查来源：Claude 代码审查 + WorkBuddy 逐条代码验证（8 条全部属实）
> 定位：修复优先级意见，供下一轮开发（Codex）照单执行

---

## 0. 总览

| # | 位置 | 问题 | 严重度 | 优先级 |
|:--:|:---|:---|:---|:---:|
| 1 | `llm_miner.py:213-222` | 验证 dummy 太小，无法筛出问题表达式 | 静默垃圾入口 | **P1** |
| 2 | `ops.py` evaluate_builtin | 缺乘法；`/` 解析不处理括号嵌套 | 双引擎语义不一致 | P2 |
| 3 | `llm_miner.py` 出口 | 无语义去重 | 冗余因子堆积 | P3 |
| 4 | `factor_selection.py:141` | `fillna(0.5)` 虚增稀疏因子相关 | 去重选错代表 | P2 |
| 5 | `factor_selection.py:118-125` | IC_IR 未校正序列自相关（高估 ~4.5 倍） | **污染特征排序** | **P1** |
| 6 | `factor_selection.py:177-185` | 阈值自适应搜索循环论证 | 数量是目标非结果 | P2 |
| 7 | `factor_selection.py` + `gbdt.py` | 特征选择与 WF 评估同数据集（无隔离） | **选择级窥探** | **P1** |
| 8 | `factor_selection.py` | 缺因子衰退分析 | 均值掩盖失效 | P2 |

> **关键提醒**：#5 + #7 同时存在，意味着当前 `runs/factor_selection/` 选出的 80/60 特征集可信度要打折。好在 COMPLETION_REPORT 已诚实标注"三个池均未跑赢上证"——结论方向没被污染，但**在修复 #5/#7 之前，任何基于 IC 排序的新特征集都不能作为 pre-registration 依据**。

---

## 1. [P1] llm_miner 验证 dummy 太小（#1）

### 现状

`bridge/llm_miner.py:213-222`：

```python
dummy = pd.DataFrame({
    "trade_date": pd.to_datetime(["2024-01-01"] * 5),
    "stock_code": ["000001.SZ"] * 5,
    "open": [1.0, 1.1, 1.2, 1.1, 1.3],
    ...
})
```

只有 **5 行 × 1 只股票 × 1 个日期**。`ts_mean(close, 20)` 这类表达式在 dummy 上滚动窗口全 NaN，但 `parse_expression(expr)(dummy)` 只检查"能不能执行"不检查"有没有产出"——**废表达式静默通过校验，进入生产面板后是空因子列**。

### 修复

把 dummy 扩成：**60 个交易日 × 20 只股票 × 若干列**，并加入**一只连续涨停票（收盘价全部相同）**用于触发 ts_corr 零方差路径：

```python
import numpy as np
import pandas as pd

def _build_validation_dummy(n_days: int = 60, n_stocks: int = 20) -> pd.DataFrame:
    """Dummy with enough history to exercise rolling windows + zero-variance path."""
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    rows = []
    for s in range(n_stocks):
        code = f"{100000 + s}.SZ"
        # 一只连续涨停票：close 恒定 → 触发 ts_corr 零方差
        if s == n_stocks - 1:
            close = np.full(n_days, 10.0)
        else:
            close = 10.0 + np.cumsum(np.random.default_rng(s).normal(0, 0.1, n_days))
        open_ = close + np.random.default_rng(s + 1).normal(0, 0.02, n_days)
        high = np.maximum(open_, close) + 0.05
        low = np.minimum(open_, close) - 0.05
        volume = np.random.default_rng(s + 2).uniform(1e5, 1e6, n_days)
        amount = volume * close
        for t, d in enumerate(dates):
            rows.append({
                "trade_date": d, "stock_code": code,
                "open": open_[t], "high": high[t], "low": low[t], "close": close[t],
                "volume": volume[t], "amount": amount[t],
                "vwap": amount[t] / volume[t],
                "returns": close[t] / close[t-1] - 1 if t > 0 else 0.0,
            })
    return pd.DataFrame(rows)
```

校验时**除了能执行，还要检查产出质量**：

```python
try:
    out = parse_expression(spec["expression"])(dummy)
    n_valid = out.notna().sum()
    if n_valid == 0:
        print(f"[miner] skip {spec.get('id')}: all-NaN on 60d dummy", file=sys.stderr)
        continue
    clean.append({**spec, "engine": "quantgpt"})
except Exception as exc:
    ...
```

**收益**：在 mining 阶段就过滤掉一批"20 期滚动窗口全是 NaN"的废表达式，避免污染面板。

---

## 2. [P2] evaluate_builtin 缺乘法 / / 解析不处理括号（#2）

### 现状

`bridge/ops.py` docstring 明确：

```
Supported: rank(x), ts_mean(x,N), ts_std(x,N), ts_shift(x,N),
ts_delta(x,N), ts_decay_linear(x,N), group_rank(x, group),
unary -x, and a / b
```

- **无 `*`**：`ts_mean(vol,5) * rank(close)` 这类表达式只能走 quantgpt 引擎
- **`/` 解析**（L262）：`text.split("/", 1)` 会把 `(a+b)/(c+d)` 劈成 `(a+b)` 和 `c+d)` 两段错误文本

结果：builtin 与 quantgpt 形成**两套未对齐的执行语义**——同一个表达式在两条路径上结果可能不同。

### 修复（二选一，推荐前者）

**方案 A（推荐）**：给 evaluate_builtin 补顶层 `*` 解析，并让 `/` 也走 `_split_top_level_*`（括号感知）：

```python
def _split_top_level_binary(text: str, op: str) -> tuple[str, str] | None:
    """Split on op at depth 0 (outside parentheses)."""
    depth = 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == op and depth == 0:
            return text[:i], text[i+1:]
    return None
```

在 evaluate_builtin 的 binary 分支里：

```python
for op in ("*", "/"):
    split = _split_top_level_binary(text, op)
    if split:
        left_text, right_text = split
        left, _ = evaluate_builtin(frame, left_text.strip(), expression_id=expression_id)
        right, _ = evaluate_builtin(frame, right_text.strip(), expression_id=expression_id)
        value = (left * right) if op == "*" else (left / right.replace(0, np.nan))
        return sanitize(value, expression_id)
```

注意：`*` 和 `/` 需要按**优先级处理**（先乘除后加减）——在 `_split_top_level_add_sub` 之后、`/` 之前插入 `*` 分支，保持 `*`、`/` 同层。

**方案 B（更彻底）**：明确**只用一个引擎**——所有表达式统一走 quantgpt `parse_expression`，`evaluate_builtin` 降级为纯测试用（P0 断言保留），删掉生产双轨。两条路比一条差。

---

## 3. [P3] 无语义去重（#3）

### 现状

LLM 容易生成 `rank(close/ts_mean(close,20))` 和 `rank(-ts_mean(close,20)/close)` 这类**符号相反、经济含义相同**的表达式。配置里两者并存，浪费因子预算、增加相关冗余。

### 修复

在写入 `expressions.json` 之前，用 dummy 数据算两两 Spearman 相关：

```python
def dedup_semantic(specs: list[dict], dummy: pd.DataFrame, threshold: float = 0.95) -> list[dict]:
    from quantgpt.expression_parser import parse_expression

    kept: list[dict] = []
    for spec in specs:
        series = parse_expression(spec["expression"])(dummy)
        if series is None or series.notna().sum() == 0:
            continue
        redundant = False
        for other in kept:
            other_series = parse_expression(other["expression"])(dummy)
            valid = series.notna() & other_series.notna()
            if valid.sum() < 30:
                continue
            rho = series[valid].corr(other_series[valid], method="spearman")
            if abs(rho) > threshold:
                redundant = True
                break
        if not redundant:
            kept.append(spec)
    return kept
```

**注意**：`abs(rho) > 0.95` 意味着符号相反的等价表达式也判重——去重时**保留"IC 方向更合理"的那个**（结合预注册方向，或先用少量真实数据看 IC 符号），而不是先到先得。

---

## 4. [P2] fillna(0.5) 虚增稀疏因子相关性（#4）

### 现状

`bridge/factor_selection.py:141`：

```python
matrix = ranks.fillna(0.5).to_numpy()
corr = np.corrcoef(matrix, rowvar=False)
```

因子 A 在 2023 年全 NaN、因子 B 在 2025 年全 NaN → 填 0.5 后在各自无效区间外都表现为"中等水平" → corrcoef 报高度相关，**实际它们时间上完全错开、毫无关系**。

### 修复

**方案 A（推荐）**：按日分组算截面 Spearman 相关，再对日维度取均值：

```python
def build_rank_correlation(panel: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Cross-sectional Spearman corr per day, averaged over days with both valid."""
    n = len(columns)
    acc = np.zeros((n, n))
    cnt = np.zeros((n, n))
    for _, day in panel.groupby("date", sort=False):
        sub = day[columns]
        valid = sub.notna()
        if valid.sum().sum() == 0:
            continue
        ranks = sub.rank(pct=True)
        with np.errstate(invalid="ignore"):
            corr = np.corrcoef(ranks, rowvar=False)  # NaN 自动传播
        mask = valid.to_numpy().T @ valid.to_numpy().astype(float)
        mask = (mask > 0).astype(float)
        finite = np.isfinite(corr)
        acc += np.nan_to_num(corr) * mask * finite
        cnt += mask * finite
    with np.errstate(invalid="ignore"):
        avg = np.where(cnt > 0, acc / np.maximum(cnt, 1), np.nan)
    return pd.DataFrame(avg, index=columns, columns=columns)
```

**方案 B（更简单）**：只在两因子同时有值的行上算相关（`pairwise complete obs`），用 `df[cols].corr(method="spearman")` 默认行为（pandas 的 corr 天然是 pairwise）。

---

## 5. [P1] IC_IR 未校正序列自相关，高估 ~4.5 倍（#5）

### 现状

`bridge/factor_selection.py:118-125`：

```python
ic_ir = round(float(values.mean() / values.std(ddof=1)), 6)
```

EXRET20 是 20 日前向收益，**相邻日重叠 19/20** → IC 序列强自相关 → `values.std(ddof=1)` 假设每日独立，实际有效样本量约 `n_days / 20` → **IC_IR 高估 sqrt(20) ≈ 4.5 倍**。

### 修复

**方案 A（推荐）**：Newey-West 校正（lag = holding_period）：

```python
from statsmodels.stats.sandwich_covariance import cov_hac

def ic_ir_newey_west(ics: np.ndarray, lag: int = 20) -> float | None:
    if len(ics) < lag + 2:
        return None
    x = np.column_stack([np.ones(len(ics)), np.zeros(len(ics))])
    # 用 OLS 截距的 HAC 标准误近似 IC 均值的标准误
    import statsmodels.api as sm
    model = sm.OLS(ics, np.ones(len(ics))).fit()
    cov = cov_hac(model, nlags=lag)
    se = float(np.sqrt(cov[0, 0]))
    return float(ics.mean() / se) if se > 0 else None
```

（若不想引入 statsmodels：至少把 IC_IR 的语义改为 `ic_ir_unadjusted` 并在报告里显式标注"未校正序列相关，真实值约为展示值的 1/4~1/5"。）

**方案 B（降级标注）**：只报 `ic_mean`，把 `ic_ir` 标注为"未校正序列相关"，不让它进特征排序权重。

---

## 6. [P2] 阈值自适应搜索循环论证（#6）

### 现状

`bridge/factor_selection.py:177-185`：

```python
def choose_threshold(corr, ic_table):
    for threshold in (0.95, 0.90, 0.85, 0.80, 0.75, 0.70):
        selected = greedy_representatives(corr, ic_table, threshold=threshold, max_keep=80)
        if 40 <= len(selected) <= 80:
            return threshold, selected
    ...
```

从 0.95 向下搜，找到第一个使因子数落在 40-80 就停——**因子数量成了程序目标，不是统计结果**。

### 修复

固定一个**有经济含义的阈值**，接受输出数量是结果：

```python
CORRELATION_REDUNDANCY_THRESHOLD = 0.80  # 截面排名相关 > 80% 视为冗余

selected = greedy_representatives(
    corr, ic_table, threshold=CORRELATION_REDUNDANCY_THRESHOLD, max_keep=80
)
```

如果固定 0.80 后数量不足/过多，**报告实际数量**而不是调阈值硬凑（数量少是真实信号：因子高度冗余；数量多说明该扩因子池）。

---

## 7. [P1] 特征选择与 WF 评估同数据集（#7）

### 现状

- `factor_selection.py` 基于开发集 EXRET20 的 IC_mean 排序选出特征集
- `gbdt.py` 的 purged walk-forward 在**同一个开发集**上评估

→ 特征选择和模型评估之间**没有隔离**：IC 高的因子被优先选入，然后在同一段数据上评估发现表现尚可——这是"特征集选择级"的数据窥探。

### 修复（二选一）

**方案 A（推荐，最干净）**：**特征集预注册**——特征集在进入开发集评估之前固定：

```
1. 用开发集【前半段】(如 2023-06 ~ 2024-06) 做 IC 排序/去重 → 产出特征集
2. 特征集锁定（写 manifest，含 SHA-256）
3. 开发集【后半段】(2024-07 ~ 2025-12) 才跑 purged walk-forward 评估
4. 干净数据 (2026-07-18 起) 只做最终验收
```

**方案 B**：嵌套交叉验证——特征选择在外层折内做，评估在内层折做（成本高，但对当前规模可行）。

> 与纪律 §8 一致：post-hoc 数字不得选候选/改权重。特征集选择同样适用——**选特征的代码决策也要在评估之前完成并固定**。

---

## 8. [P2] 缺因子衰退分析（#8）

### 现状

IC 表格在整个开发期取均值，**掩盖因子是否已衰退**。量价因子在 A 股半衰期通常 12-18 个月——2023 年有效的因子到 2025-2026 可能失效或方向反转。

### 修复

在清洗步骤加 **rolling 60 日 IC 时间序列**，按因子族分组叠加：

```python
def rolling_ic_series(panel: pd.DataFrame, factor: str, target: str = "EXRET20",
                      window: int = 60) -> pd.Series:
    """Rolling cross-sectional Spearman IC over a trailing window."""
    dates = sorted(panel["date"].unique())
    ics = []
    for i, d in enumerate(dates):
        day = panel[panel["date"] == d]
        if day[factor].notna().sum() < 20 or day[target].notna().sum() < 20:
            ics.append(np.nan)
            continue
        from scipy.stats import spearmanr
        rho = spearmanr(day[factor], day[target]).statistic
        ics.append(rho)
    s = pd.Series(ics, index=pd.to_datetime(dates))
    return s.rolling(window, min_periods=20).mean()
```

产出：`runs/factor_selection/rolling_ic_by_family.csv` + 一张分族叠加图。**判定规则**：某族最近 6 个月 IC 符号系统性反转 → 显式标记 `recently_reversed`，不进下一轮候选；而不是被 2023-2025 均值掩盖。

---

## 9. 修复顺序建议

| 轮次 | 修什么 | 理由 |
|:--:|:---|:---|
| **第一轮（P1）** | #1 dummy + #5 IC_IR + #7 特征选择隔离 | 直接影响判官/特征集可信度；不修则下一轮 pre-registration 依据不可靠 |
| **第二轮（P2）** | #2 乘法/括号 + #4 相关性 + #6 阈值 + #8 衰退分析 | 工程正确性与分析深度 |
| **第三轮（P3）** | #3 语义去重 | 锦上添花，缓解 LLM 冗余 |

## 10. 验收标准

- #1：`llm_miner` 对 `ts_mean(close,20)` 类表达式在 60 日 dummy 上产出全 NaN 时报"skip"而不是通过
- #2：`evaluate_builtin("ts_mean(vol,5) * rank(close)")` 与 quantgpt 引擎结果一致（或已删除 builtin 双轨）
- #5：`ic_ir_newey_west` 输出约为原值的 1/4~1/5；报告标注校正方法
- #6：固定阈值 0.80，数量是输出不是目标
- #7：特征集 manifest 含 SHA-256，且特征选择日期 < WF 评估开始日期
- #8：`rolling_ic_by_family.csv` 存在，反转族有标记
