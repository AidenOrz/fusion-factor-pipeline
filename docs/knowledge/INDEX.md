# 知识库索引

> 只收录**经过验证、可迁移**的经验。每条都写了适用场景、现象、根因/洞见、推荐做法、验证依据与边界。
> 引用时仍须重新判断其对当前环境是否成立，不得当成不可质疑的事实。

## 数据获取与拼接

| 条目 | 一句话 | 适用场景 |
|---|---|---|
| [verify_external_series_by_exact_reproduction](verify_external_series_by_exact_reproduction.md) | 外部源补历史序列，必须与本地归档**逐值相等**才可采信；字段顺序、复权基期、同名不同物是三大坑 | 从第三方接口补一段已有本地归档的行情/指数 |
| [baostock_local_kline_splice](baostock_local_kline_splice.md) | baostock 与本地归档的**前复权基期不同且非恒定比例**，直接拼会造假跳空；用分段常数因子对齐 | 行情归档断档，需跨源延伸价格序列 |
| [baostock_partial_fetch_coverage](baostock_partial_fetch_coverage.md) | 会话中途掉线只拉完一批代码，**看行数会误判成"上游缺数据"**；必须核对每期代码覆盖数 | baostock 批量取数中断后续跑 |

## 面板与特征构建

| 条目 | 一句话 | 适用场景 |
|---|---|---|
| [local_kline_panel_rebuild](local_kline_panel_rebuild.md) | v6 schema 面板可由本地 K 线 CSV **精确重建**，8 个派生列逐值复现，不必等上游解锁 | 上游治理锁住面板地平线，需要延伸研究面板 |

## 评估与预注册

| 条目 | 一句话 | 适用场景 |
|---|---|---|
| [preregister_threshold_provenance](preregister_threshold_provenance.md) | 预注册必须连**"阈值是怎么来的"**一起登记；样本量常先于结果可见，不披露就等于给事后调阈值留口子 | 写前向观察 / 样本外验收协议 |
| [lofo_marginal_contribution](lofo_marginal_contribution.md) | LOFO 边际贡献受**噪声带**支配，单次删除的 IC 变化不可直接读成重要性 | 因子池精简与消融判读 |

## 加速与工程

| 条目 | 一句话 | 适用场景 |
|---|---|---|
| [rust_acceleration_with_parity_gate](rust_acceleration_with_parity_gate.md) | 把热点核迁到 Rust 必须有**逐值等价门**；FFI 转换开销可能吃掉全部收益，要实测而非假设 | 想把 Python 热点计算 Rust 化 |

## 环境

| 条目 | 一句话 | 适用场景 |
|---|---|---|
| [windows_sandbox_0o700_pytest](windows_sandbox_0o700_pytest.md) | Windows 沙箱下 `mkdir(mode=0o700)` 生成的 DACL 连创建者都列不出，pytest 因此大面积 PermissionError；**是环境缺陷不是代码回归** | 本机跑 pytest 出现大量 setup 阶段 PermissionError |
