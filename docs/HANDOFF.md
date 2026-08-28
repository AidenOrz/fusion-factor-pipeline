# HANDOFF — 当前稳定点与下一步

> 更新时间：2026-08-28
> 状态：37 因子池已通过 selection-only 确认并冻结，下一步为一次性 evaluation；无阻塞。

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
- [x] 2026-08-28 已审阅旧 LOFO 的方向盲区与 LambdaRank 噪声问题；旧结果仍保留为历史证据。
- [x] 2026-08-28 已准备并登记新实验：`runs/factor_ablation_v4_b2_huber_cross_label_2026-08-28/`，两条主线均为 Huber，仅标签不同。
- [x] 2026-08-28 已完成 v4 两条完整集基线、family LOFO 和 info-type LOFO；方向字段与组级决策已落盘。
- [x] 35 因子首轮确认 raw IC 未过；按预注册规则唯一一次放回 `starter` 后，37 因子池双 Huber 确认通过。
- [x] 冻结评审选择 C 主轨、底部 10% 剔除、20 日标签；`gtja_090` 明确维持出局。冻结配置为 `config/factor_pool_b2_frozen_2026-08-28.json`。
- [ ] 下一步：
  1. selection 期训练固定 37 因子双 Huber 模型；evaluation 期只预测一次，不用 evaluation 标签更新模型。
  2. 按冻结标准检查 HAC IC_IR、三个连续块符号一致性和十分位结构。
  3. evaluation 未全过则记录最终否定，不再修改本候选；全过才进入 C 轨扣费验收，A 轨 Top-K 仅附录。

## 4. 恢复入口

```powershell
cd C:\Users\Lenovo\Documents\New project\fusion-factor-pipeline
python tests/run_p0_smoke.py
python bridge/ablation.py register --out-dir runs/factor_ablation_v4_b2_huber_cross_label_2026-08-28
python bridge/ablation.py run --phase baseline --out-dir runs/factor_ablation_v4_b2_huber_cross_label_2026-08-28
python bridge/ablation.py run --phase lofo --group-kind family --out-dir runs/factor_ablation_v4_b2_huber_cross_label_2026-08-28
python bridge/ablation.py run --phase lofo --group-kind info_type --out-dir runs/factor_ablation_v4_b2_huber_cross_label_2026-08-28
python bridge/gbdt.py --panel data/panel_fusion_full.parquet --features-file runs/factor_selection/selected_features_shap.json --mode walk-forward --out-dir runs/gbdt_walkforward_pruned60
python bridge/dev_lockbox.py status
```

无凭据保存在项目文件；DeepSeek key 在 `QuantGPT/.env`（gitignored）。
