# Windows 沙箱下 0o700 目录不可列出（pytest 假失败）

## 适用场景
在本项目（或任何 DSH Windows 沙箱工作区）直接运行 `python -m pytest`，大量用例在
**setup 阶段**报 `PermissionError: [WinError 5] 拒绝访问`，指向 pytest 自己的临时目录或
工作区内的 `artifacts/pytest_tmp_*`，而不是项目代码。

## 现象
- 报错位置在 `_pytest/tmpdir.py`、`_pytest/pathlib.py:cleanup_dead_symlinks` 或
  `pytestqt/plugin.py` 的 hook 里，栈里没有任何项目源码。
- 2026-10-17 实测：`pytest tests` = **44 passed, 28 errors**（28 个全是 PermissionError）。
- `icacls <该目录>` 输出 `Access is denied`，即连读 DACL 都被拒。

## 根因
Python 在该 Windows 环境下用 `mkdir(mode=0o700)` 建目录时，会生成一个受限 DACL，
**连创建它的沙箱进程自己都无法再列出/递归该目录**。pytest 的临时目录机制
（`_pytest/tmpdir.py` 多处写死 `mode=0o700`，`pathlib.make_numbered_dir(mode=0o700)`）
正好每次都这样做，于是 setup 就炸。

最小复现（同一进程内）：
```python
import os, pathlib
os.mkdir("artifacts/_m700", 0o700)                 # 0o700 -> 下一步必失败
print([x.name for x in os.scandir("artifacts/_m700")])  # PermissionError WinError 5
pathlib.Path("artifacts/_m777").mkdir(mode=0o777)  # 0o777 -> 正常
```
对照：mode `0o777` 与不传 mode 都正常；只有 `0o700` 触发。因此**与项目代码无关**，
是沙箱 + POSIX mode 的交互缺陷。副作用：这种目录事后连 `Remove-Item -Recurse -Force`
都删不掉，会永久留在工作区并让后续每次 `pytest` 收集阶段就报错。

## 推荐做法
1. **不要**据此判定项目测试失败。先看栈里有没有项目源码；全是 `_pytest`/`pytestqt` 的
   即属此症。
2. 用一次性插件把目录 mode 中和掉，并显式给 basetemp（必须成对使用，因为
   `--basetemp` 指向的目录会被 pytest 先 `rm_rf` 再 `mkdir(mode=0o700)` 重建）：
   ```python
   # artifacts/_permfix_plugin.py
   import pathlib
   _orig = pathlib.Path.mkdir
   def _mkdir(self, mode=0o777, parents=False, exist_ok=False):
       return _orig(self, 0o777, parents=parents, exist_ok=exist_ok)
   pathlib.Path.mkdir = _mkdir
   ```
   ```powershell
   $env:PYTHONPATH="$PWD\artifacts"
   python -m pytest tests -q -p no:cacheprovider -p _permfix_plugin --basetemp=artifacts/pytest_base
   ```
3. 2026-10-17 用此法实测：**72 passed**，与 HANDOFF 声明的 72 项全绿一致。

## 验证依据
- 逐 mode 对照实验（`_mode_448`/0o700 失败、`_mode_511`/0o777 与默认成功）。
- 单文件对照：`tests/test_manual_factor_pool.py` 在打补丁前 6 errors、打补丁后 6 passed。
- 全量对照：同一工作区 `pytest tests` 44 passed/28 errors → 加插件后 **72 passed**。

## 适用边界
- 仅针对 DSH Windows 沙箱工作区；不受限环境下 pytest 本来正常，无需插件。
- 插件是诊断/绕行手段，**不要**提交进仓库或写进项目配置。
- 真正修复需要放开沙箱权限（见 skill `diagnose-windows-sandbox-acl`），但该 skill 的
  修复目标是缺失 WRITE_DAC/WRITE_OWNER 与 AppContainer 包 ACE，与本症的
  "自建 0o700 目录不可列出" 不完全一致，是否有必要动用需另行判断。
- 已知遗留：`artifacts/_mode_448`、`artifacts/pytest_base_pre`、
  `artifacts/tmp_verify_20261017` 及 2026-09-05 的 `artifacts/pytest_tmp_*` 属此类不可删目录。
