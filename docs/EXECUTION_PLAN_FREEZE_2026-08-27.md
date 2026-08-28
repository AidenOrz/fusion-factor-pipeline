# 执行计划：因子冻结与验收轨选择（2026-08-27）

项目：`fusion-factor-pipeline`
日期：2026-08-27
性质：开发集研究计划，不构成生产批准、实盘授权或收益承诺。
前置状态：B2 方向裁决完成（`DIRECTION_ADJUDICATION_ABC_2026-08-27`），预注册 LOFO 消融完成（`runs/factor_ablation_v3_b2_2026-08-27/`），候选池 114 个因子。

## 一、定位决策（已确认，预注册）

- 项目主定位为 **B 轨（宽头部组合）或 C 轨（底部剔除）**，由本计划第 3 步的十分位诊断按预注册映射表选定，不两条都验收。
- **A 轨（Top-5/Top-10）降级为附加训练**：随最终验收一并产出数字，仅入附录，永不作为筛选、调参或冻结依据。
- 筛选与消融期指标仅限：日均 Spearman IC、HAC IC_IR、NDCG 增量、折间稳定性。Top-K 收益在任何中间环节不作决策输入。

## 二、当前因子池状态

- 裁决后候选池：114 个（`config/factor_pool_b2_adjudicated_2026-08-27_features.json`）。
- 旧 LOFO 曾把 starter 族 2 个、capital 组 1 个列为“立即移除项”，但该结论只属于 v3 历史结果；在 v4 人工确认前当前注册全集仍为 114 个。
- v4 同模型跨标签 LOFO 显示：momentum（33）、volatility（16）、gtja191（64）移除后均不损害两条 Huber 线，其中 momentum/gtja191 两条线均显著改善，volatility 一条显著改善、另一条带内。冻结评审按 ΔIC 方向字段解读，不按旧报告字面标签执行。

## 三、十分位结构诊断（与第 4 步并行）

目的：确定验收轨（B/C）、组合宽度、标签 horizon 是否成立。仅用 selection 期（2023-06-30 至 2024-09-12），输出标 `post_hoc_descriptive_only`。

输入：消融已登记的全集基线模型（当前注册全集 114 个因子），补预测落盘，无需重训；该诊断与剪后确认并行，不等待冻结后再做。

四项产出与预注册判读规则：

1. **十分位单调性表**：B2 域内每日按预测分升序 10 组，各组平均 `y_excess20` + HAC t + Spearman(组号, 组均值)。
   - 全程单调且 Q9+Q10 显著为正 → B 轨；
   - 仅 Q1/Q2 显著为负、头部平坦 → C 轨；
   - 两端均显著 → B 主 C 辅组合轨。
2. **底部组可交易性拆解（C 轨生死项）**：Q1 超额中剔除持有期内新 ST、停牌、退市前、ADV 垫底个股后的残存值。若底部超额主要来自不可交易个股，C 轨当场证伪。
3. **头部结构检查（B 轨生死项）**：Q10 内部再分 Top 5/10/20/50/100，确定超额从哪一档开始稀释，据此定 B 轨组合宽度（不为 5 的预设值）。
4. **IC 衰减曲线**：同一预测分对 horizon 1/5/10/20/40 日分别计算日 IC。若峰值显著偏离 20 日，允许在冻结前进行**一轮**预注册标签变更；这是冻结前唯一一次改标签的机会。

纪律：本诊断只用于选验收轨、定组合宽度、判断标签 horizon；不用于增删因子。剪后确认与本诊断各自独立登记，不能用诊断结果反向选择因子。

## 四、族内重剪（与第 3 步并行）

1. `gtja191` 块：先把整块移除列为候选，并完成与 alpha101 的完全重复审计（此前已确认 65 对 IC 序列完全相同）；只有整块移除的冻结确认不通过时，才按固定 importance 规则做一次组内重剪。
2. `momentum`、`volatility` 信息类：先按同模型跨标签 LOFO 的方向结果列为整类移除候选；momentum 剪除必须显式豁免人工批准的 `alpha_qg_f001`、`alpha_qg_f008` short 系列（及其对应 manual 输出列），防止把方向已裁决的候选和噪声一起删除；只有冻结确认不通过时，才各保留预注册的 top 5~8 作为一次后备重剪，不逐因子来回试错。
3. 保留块（alpha101、correlation、volume_vwap、reversal）不动。
4. 目标规模不再先验固定为 40~60；先完成 family/info-type 重叠审计，再以冻结确认实验结果决定是否需要后备重剪。改善幅度不可相加：各组 LOFO 的 ΔIC 只描述单独移除，不能据此外推联合移除后的 IC。
5. `reversal` 作为弱保留组（只有 raw 线约 1.9σ 受损证据），不在本轮主动精简；如确认实验需要缩减，只能作为预注册的一次后备动作。

## 五、冻结确认实验（冻结闸门）

在跑本实验前，先解决第二主线判别力问题：LambdaRank 噪声带（0.0172）是 Huber（0.0025）的 7 倍，LOFO 阶段所有判定实为 Huber 单线做出。本轮已选择并执行方案甲：

- 方案甲：第二主线改为 Huber on `y_neutral_rank20_b2`（同模型跨标签，分离模型差异与标签差异）；
- 方案乙：LambdaRank 增加种子数压低噪声带（本轮不采用）。

实验设计：候选冻结清单 vs 当前注册全集（114），双主线各跑一次，selection 期；候选规模不再先验固定。

预注册通过标准（满足即冻结）：

- 剪后 IC 不低于全集 IC − 1 倍对应模型噪声带；
- 剪后 HAC IC_IR 不下降；
- 折间符号一致性保持 1.0。

若不通过：只允许将"罪魁祸首"族放回一档重试**一轮**；仍不通过则回到描述阶段，不得迭代微调。

本实验同时复算十分位表，作为第 3 步结构结论在剪后池上的最终确认；十分位单调性和 horizon IC 衰减诊断与本实验并行产出，结果仅用于冻结前确定验收轨和标签 horizon。

## 六、冻结与一次性验证

1. 冻结内容：最终因子清单 + 注册方向 + 标签定义 + 组合宽度 + 验收轨，写入配置并 commit。
2. evaluation 期（2024-10-22 起）**只跑一次**：双主线模型，指标 IC / HAC IC_IR / NDCG 增量 / 折间稳定性 + 十分位结构复验。
3. 预注册通过标准：
   - evaluation 期 HAC IC_IR ≥ 0.15；
   - 折间 IC 符号一致；
   - 十分位结构与 selection 期定性一致（轨不反转）。
4. 不达标不迭代，记录为锁箱否定结论，回到描述阶段重新立项。

## 七、验收与稳健性（冻结后）

按第 3 步选定的验收轨执行：

- **B 轨**：Top-N%（N 由头部结构检查定）等权组合，扣双边千一 + 滑点，与基准超额对照，报告年化超额、日胜率、换手。
- **C 轨**：B2 域剔除底部 X%（X 由十分位表定）后的组合超额，同样扣费；底部组可交易性拆解复验。
- **A 轨附加报告**：Top-5/Top-10 数字入附录。

稳健性三件套同步完成：

1. 行业快照非 PIT 敏感性：在冻结标签上复验，确认中性化结论不受行业快照驱动。
2. horizon 敏感性：以冻结标签为中心复算 IC 衰减曲线，确认所选 horizon 不在悬崖边。
3. 2026 年数据外推检验（单独决策项）：如需使用，必须预声明单一假设、一次性使用；不用则明确记录放弃。

## 八、全程纪律

1. 第 4 步之后不再新增任何因子；新表达式、LLM 挖矿一律等下一轮预注册。
2. evaluation 期全项目只碰一次；任何"再看一眼调一下"均为红色警报。
3. 每步产出落 `runs/`，结论在 `docs/` 留 WORK_REPORT 风格记录；本地大文件按仓库规则不提交。
4. 否定性结论（轨证伪、冻结失败、evaluation 不达标）均为合格产出，按锁箱纪律归档，不做情绪化返工。

## 九、里程碑顺序

```text
十分位诊断（114 全集）∥ 族内重剪（114 → 候选规模待确认）
        ↓ 汇合
冻结确认实验（剪后 vs 全集，含十分位复验）
        ↓ 通过
冻结 + commit → evaluation 一次性验证
        ↓ 通过
B/C 轨验收（扣费）+ A 附加报告 + 稳健性三件套
        ↓
开发者观察锁箱持续运行，评估是否进入 v6 官方锁箱
```

## 十、2026-08-28 执行修订：同模型跨标签 LOFO

本节是对第五步的执行登记修订，不改写 `runs/factor_ablation_v3_b2_2026-08-27/` 的历史结果，也不自动修改候选配置。

### 修订原因

- 旧规则只识别“移除后受损”，没有识别“移除后显著变好”。因此 `delta_ic > noise_std` 必须解释为移除改善、该组边际贡献为负，而不是机械保留。
- 原 Huber 原始秩 vs LambdaRank 中性秩同时改变了估计器和标签；LambdaRank 噪声带约为 Huber 的 7 倍，不能作为有判别力的第二主线。
- v4 同模型跨标签 LOFO 显示 `gtja191`、`momentum`、`volatility` 均没有可检测的正边际贡献；因此旧计划中“先各保留 top 5~8”的默认顺序需要后移。

### 新注册口径

- 主线一：`huber_b2_raw_rank`，目标 `y_raw_rank20_b2`。
- 主线二：`huber_b2_neutral_rank`，目标 `y_neutral_rank20_b2`。
- 两条线共用 3 折、种子 `42/43/44`、20 个交易日 purge gap 和同一组特征；只改变标签。
- 新实验目录：`runs/factor_ablation_v4_b2_huber_cross_label_2026-08-28/`。
- 新实验分类仍为 `post_hoc_descriptive_only`，不得自动翻转方向、修改候选池或进入 evaluation。

### 方向敏感判定

对每个模型分别比较 `delta_ic` 与该模型完整集基线噪声带：

- `delta_ic > noise_std`：`removal_improves`，边际贡献 `negative`；
- `delta_ic < -noise_std`：`removal_harms`，边际贡献 `positive`；
- `abs(delta_ic) <= noise_std`：`indistinguishable`，没有可检测边际贡献。

组级规则改为：没有任何主线 `removal_harms` 且至少一条主线改善或全部带内，才可列入人工移除候选；同时出现改善与受损时标记 `direction_conflict`，不得自动决策。结果写入 `lofo_group_decisions.csv`。

### 执行顺序

1. 只注册新实验并检查 manifest；
2. 在 selection period 跑两条 Huber 完整集基线，保存新的 `noise_band.json`；
3. 先复验 `starter`、`capital`、`alpha101`、`gtja191`、`llm` family LOFO，并复核 `momentum`、`volatility` 信息类 LOFO；
4. 对 `gtja191`、`momentum`、`volatility` 先登记“整组移除候选”，完成 family/info-type 交叉重叠审计；只有整组移除的冻结确认不通过时，才按预注册规则各做一次组内重剪，不逐因子来回试错；
5. 剪后候选池与全集各跑一次冻结确认；通过后才允许 evaluation 一次性验证。

### 恢复命令

```powershell
python bridge/ablation.py register --out-dir runs/factor_ablation_v4_b2_huber_cross_label_2026-08-28
python bridge/ablation.py run --phase baseline --out-dir runs/factor_ablation_v4_b2_huber_cross_label_2026-08-28
python bridge/ablation.py run --phase lofo --group-kind family --out-dir runs/factor_ablation_v4_b2_huber_cross_label_2026-08-28
python bridge/ablation.py run --phase lofo --group-kind info_type --out-dir runs/factor_ablation_v4_b2_huber_cross_label_2026-08-28
```

上述命令只写 selection 开发集产物；没有授权时不运行 evaluation，也不写冻结配置。
