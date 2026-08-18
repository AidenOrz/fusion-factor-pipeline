# 人工确认候选池：下一步实施记录

日期：2026-08-18
项目：`fusion-factor-pipeline`
性质：开发集描述性增量实验，不是生产批准或正式锁箱

## 结论

方向清单的第一轮收缩方案可以执行，但方向建议必须保持人工确认边界：

- 原始 `config/expressions*.json` 没有改写。
- 程序不会读取 IC 后自动翻转方向。
- 只有显式调用 `bridge/manual_factor_pool.py`，并且配置中明确列出的 3 个因子才会生成 short 候选列。
- `regime_sensitive`、`unstable`、`insufficient_data` 的人工复核项不会进入第一轮候选池。

人工确认文件为 [factor_pool_v2_manual_review.json](../config/factor_pool_v2_manual_review.json)，其中只把以下 3 个因子作为第一轮 short 候选：

| 原始列 | 候选列 | 处理 |
|---|---|---|
| `alpha_qg_f001` | `manual__alpha_qg_f001__short` | 显式取负，动量/反转簇代表 |
| `alpha_qg_f005` | `manual__alpha_qg_f005__short` | 显式取负，量能簇代表 |
| `alpha_qg_f006` | `manual__alpha_qg_f006__short` | 显式取负，VWAP 簇代表 |

## 候选池物化

执行命令：

```powershell
python bridge/manual_factor_pool.py `
  --pool config/factor_pool_v2_manual_review.json `
  --panel data/panel_fusion_full.parquet `
  --out-panel data/panel_fusion_v2_manual_review.parquet `
  --features-out config/factor_pool_v2_manual_review_features.json
```

结果：

- 输入/输出面板均为 52,088 行、2,949 只股票。
- 既有选择结果中 204 个特征排除 63 个 `manual_review_required` 项，留下 155 个基础特征。
- 加入 3 个显式 short 候选后，训练特征共 158 个。
- 3 个新列逐行等于对应原始列的负值；原始列仍保留。
- 63 个人工复核原始列没有泄漏进训练特征文件。

## 第一轮 walk-forward

执行时固定：目标 `EXRET20`、选择截止日 `2024-09-12`、标签净空 20 个交易日、LightGBM Huber、种子 `42/43/44`。选择后的理论评估起点是 `2024-10-22`；由于第一折没有历史训练样本，实际有预测的首个测试日为 `2025-02-26`。

报告目录（本地生成，面板和训练产物不纳入 Git）：`runs/gbdt_walkforward_v2_manual_review/`

| 指标 | 结果 |
|---|---:|
| 总体 Spearman IC | 0.073101 |
| ML Top-5 平均 EXRET20 | -1.1621% |
| ML Top-5 日胜率 | 35.47% |
| ML Top-5 对上证平均超额 | -1.0575% |
| ML Top-5 对上证日胜率 | 41.05% |
| ML Top-5 年化超额近似 | -13.3245% |

这里的年化超额仍是重叠 `EXRET20` 的研究近似值，不等价于独立日收益复利。Top-1 曾为正不能抵消 Top-3/Top-5 的负结果；各测试折 Top-5 平均目标分别为 -2.14%、-1.51%、+0.18%，稳定性不足。

## 对方向结论的影响

这次结果不支持把 `f001/f005/f006` 的 short 方向称为已验证 alpha，也不支持回写注册方向。它只证明候选池可以在不污染原始配置的情况下被显式物化和评估。

清单 D 类的“截面区分度不足”不是 `direction_review.csv` 自带字段，而是对 `data/panel_fusion_full.parquet` 在选择期 `2023-06-01..2024-09-12` 按日复算唯一值数量得到的体检结果。复算使用与方向审阅相同的截面数据；`daily_ic_series` 要求每日因子至少有 5 个不同取值，因此中位唯一值为 1/2/3 的因子不能据此获得有效 IC。这一依据支持第一轮剔除，但不把它解释成单纯的覆盖缺失。

下一轮应保持单变量增量顺序：先对同一人工池加入截面 rank/z-score/winsorize，再单独测试少量行业中性残差，最后才测试交叉项。每一轮都要从选择期结束后的日期重新评估，不回看本轮结果来改方向或调权重。
