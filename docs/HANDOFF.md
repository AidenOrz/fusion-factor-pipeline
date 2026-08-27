# HANDOFF — 当前稳定点与下一步

> 更新时间：2026-08
> 状态：运行中（观察阶段），无阻塞。

## 1. 最后稳定点

- 数据：
  - `data/refdata/shares_pit.parquet`：baostock 全市场股本 PIT，49,965 行 / 3,163 股。
  - `data/panel_fusion_full.parquet`：316 因子池，52,088 行 × 2,949 股。
  - `data/aurumq_alpha101_full.parquet` / `data/aurumq_gtja191_full.parquet`：aurumq-rl 因子全量。
- 因子筛选：
  - `runs/factor_selection/`：IC 表、族汇总、相关性矩阵、80 特征、60 特征、报告。
- 训练：
  - `runs/gbdt_walkforward_full/`（316）
  - `runs/gbdt_walkforward_reduced80/`（80）
  - `runs/gbdt_walkforward_pruned60/`（60）
- 观察锁箱：
  - `artifacts/dev_lockbox/dev/`：active，3 个非空 live 信号日已追加，`verify` 通过。

## 2. 已验证事实

- baostock 与 WeStock 均可直连，不需要代理。
- lightgbm 4.7.0 已安装，判官用 `LGBMRegressor`，支持 NaN 原生处理。
- DeepSeek v4-flash/pro 是唯一模型；`deepseek-chat` 是旧别名。
- LLM 每次运行有完整记录：`runs/llm_miner/<UTC>/`。

## 3. 尚未完成 / 待决策

- [ ] 行业历史 PIT：需交易所/指数公司历史快照或付费数据。
- [ ] G2 未通过：当前任何因子池在开发集都未稳定跑赢上证指数，不得进 v6 官方锁箱。
- [x] 2026-08-27 B2 人工裁决已落地为 114 列预注册候选池；物化器已验证全市场键连接、short 变换和旧 events 拒绝。
- [ ] 下一步建议：
  1. 先审阅 `docs/B2_ABLATION_GROUPING_2026-08-27.csv` 中 103 行 `column_name_fallback` 信息类型归类。
  2. 在 selection period 对 114 列执行预注册 LOFO/信息类型消融；主线为 B2 Huber 原始秩与 B2 中性秩 LambdaRank。
  3. 仅在清单冻结后于 evaluation 期一次性验证，再考虑十分位、IC 衰减和 Top-K 验收。

## 4. 恢复入口

```powershell
cd C:\Users\Lenovo\Documents\New project\fusion-factor-pipeline
python tests/run_p0_smoke.py
python bridge/gbdt.py --panel data/panel_fusion_full.parquet --features-file runs/factor_selection/selected_features_shap.json --mode walk-forward --out-dir runs/gbdt_walkforward_pruned60
python bridge/dev_lockbox.py status
```

无凭据保存在项目文件；DeepSeek key 在 `QuantGPT/.env`（gitignored）。
