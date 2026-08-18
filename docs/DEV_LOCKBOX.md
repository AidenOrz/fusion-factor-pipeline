# 开发者观察锁箱契约（放松版）

> 目的：保留 v6 锁箱的**防篡改哈希链**，同时放开**只读观察**，让开发者在正式揭盲前可以随时查看每日候选、结果和链条状态。
> 与官方锁箱的关系：本项目锁箱是**影子观察器**，不修改、不消费 `a-share-factor-engine-v6` 官方 `final_test.py` 的单次揭盲。

## 1. 两种读权限

| 操作 | 官方 v6 锁箱 | 本观察锁箱 |
|---|---|---|
| 追加每日候选 | 冻结后一次性允许 | 允许，按日期递增 |
| 查看链条/快照 | 受限 | **任意次数只读**（`observe`） |
| 查看回测结果 | 正式揭盲一次性 | **任意次数只读**（`observe --with-results`） |
| 正式揭盲 | 全项目一次 | 每 epoch 一次（`formal-reveal`） |

关键点：`observe` 是**非消费性**读取，不增加 `formal_reveal_count`；`formal-reveal` 才增加计数。

## 2. 数据链

每条 entry：

```json
{
  "seq": 1,
  "date": "2026-07-20",
  "signals_sha256": "<sha256>",
  "n_rows": 5,
  "previous_hash": "<prev-entry-hash | null>",
  "entry_hash": "<sha256(payload)>"
}
```

- 快照原文保存到 `artifacts/dev_lockbox/<epoch>/signals/YYYY-MM-DD.csv`。
- `entry_hash` 由除 `entry_hash` 外的 payload 规范 JSON 计算，串成链。
- `verify` 逐条重算 `entry_hash` 与 `previous_hash`。

## 3. 输入校验

`append --date YYYY-MM-DD --signals file.csv` 要求：

1. CSV 必须含 `date, code, score` 三列（允许额外 `name, signal, close`）。
2. 所有行 `date` 等于 `--date`。
3. `code` 为六位字符串且当日无重复。
4. 禁止前视列：`EXRET* / MFE* / MAE* / net_r / pnl` 不得出现在信号 CSV 中。
5. 日期必须严格晚于上一条 entry。
6. 同日期重复 append 拒绝。

## 4. 结果文件

`formal-reveal --results result.json`（或 `observe --with-results result.json`）要求 JSON 含：

```json
{
  "period_start": "2026-07-18",
  "period_end": "2026-07-21",
  "closed_trades": 3,
  "metrics": {"net_r_median": 0.02, "net_r_iqr": 0.3, "win_rate": 0.4}
}
```

- 只读 `observe` 不会写 registry，也不会消费揭盲。
- 正式揭盲时 `results_sha256` 写入 registry，同一 epoch 第二次正式揭盲直接拒绝。

## 5. 分类声明

任何本锁箱输出必须标注：

```text
classification = developer_observation_only
```

该分类不构成 alpha 证据，不改变生产配置，不选择生产候选。

## 6. 配置

见 `config/dev_lockbox.yaml`：

```yaml
schema: 1
mode: developer_observation
formal_reveal_limit: 1
observe_unlimited: true
min_observation_days: 0
min_closed_trades_for_formal_reveal: 0
allowed_extra_columns: [name, signal, close, stop_loss]
```

开发者模式下不设最少观察天数/交易数门槛；正式生产模式可恢复门槛。
