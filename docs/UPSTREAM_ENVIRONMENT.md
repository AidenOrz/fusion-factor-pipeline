# 上游依赖路径与启动校验

`fusion-factor-pipeline` 不再根据目录层级猜测兄弟仓库位置。运行需要读取上游仓库或行情文件的命令，必须在当前进程设置以下环境变量：

```powershell
$env:A_SHARE_FACTOR_ENGINE_V6_ROOT = 'C:\path\to\a-share-factor-engine-v6'
$env:AURUMQ_RL_ROOT = 'C:\path\to\aurumq-rl'
$env:QUANTGPT_ROOT = 'C:\path\to\QuantGPT'
$env:A_SHARE_KLINE_DATA_ROOT = 'C:\path\to\a-share-kline-data'
```

路径可以是绝对路径，也可以是相对于本项目根目录的路径。程序不会读取未设置变量对应的相邻目录作为隐式回退。

## 启动检查

运行完整检查：

```powershell
python -m bridge.paths
```

只检查某个依赖：

```powershell
python -m bridge.paths --only v6,kline
```

检查内容是：环境变量已设置、路径是目录，以及目录包含稳定标志：v6 要有 `src/` 和 `pyproject.toml`，aurumq-rl 要有 `src/`，QuantGPT 要有 `quantgpt/`，行情目录要有 `_bench_000001SH.csv`。

`bridge.panel`、`bridge.aurumq_factors`、`bridge.gbdt`、`bridge.benchmark`、`bridge.llm_miner` 和 `_s1_mc_fetch.py` 在真正读取数据前都会执行相同校验。单元测试中显式传入临时面板或数据目录时，不需要设置无关的上游变量。

## 失败处理

校验失败会列出缺失的变量、实际解析路径或缺失标志，并以非零状态退出。不要把路径写进源码或把真实密钥写进示例文件；环境变量只负责位置，API 密钥仍遵循现有的 `.env`/进程环境约定。
