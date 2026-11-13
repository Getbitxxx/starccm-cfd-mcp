# MCP 结果质量合同（2026-10-20）

状态：`ACTIVE`。判据、共享报告与 MCP 输出已通过独立 low 审查；业务发布须另经精确 manifest 审查。当前实测最高 Q1，E 战役保持挂账。

适用范围：本 MCP 返回的每一个计算结果、每一份报告、每一次发布。
目的：让“这个结果能用到什么程度”成为**可计算、可拒绝、不可越级宣称**的字段，而不是靠读者相信报告。

本文件不改变 `docs/product-acceptance-and-release-policy-20261018.md` 的发布口径：
**E 是质量标签，PAT 是发布门。** 本文件定义的是标签怎么产生、什么状态允许什么措辞、缺项时如何降级。

---

## 1. 三层质量模型

| 层 | 回答的问题 | 何时执行 | 失败后果 |
|---|---|---|---|
| L1 运行质检 | 这次运行本身是否可信（输入、网格、收敛、守恒、稳定性） | 每一个算例、每一次运行 | 结果降级或拒绝交付 |
| L2 结果标签 | 这个结果达到 E-num / E-phys 的哪一级 | 每次运行结束后自动计算 | 标签封顶，禁止越级措辞 |
| L3 产品验证 | 这套流程在某个场景/样本上是否被校准过 | 周期性 E 战役，按场景排期 | 支持范围矩阵标注 `UNVERIFIED`，不阻塞发布 |

L1 和 L2 是**每次运行必须执行**的；L3 是**质量体系的校准**，不是每个版本的门票。

---

## 2. 结果质量状态（只能由字段计算，不得人工填写）

| 状态 | 含义 | 允许的用途 |
|---|---|---|
| `Q0_FAILED` | 执行失败、必需产物缺失，或完整执行身份/产物无法证实 | 只能用于失败诊断 |
| `Q1_EXECUTED` | 流程执行完成，数值可信度未建立或未通过 | 只能用于流程/性能观察，不得用于工程判断 |
| `Q2_NUMERICALLY_VERIFIED` | E-num = PASS；E-phys = NOT_APPLICABLE 或 FAIL | 可用于同一模型内的相对比较、趋势和优化；不得用于绝对设计定值 |
| `Q3_ENGINEERING_VERIFIED` | E-num = PASS 且 E-phys = PASS（有已核验参考） | 可在声明的适用范围与容差内用于工程判断 |

状态封顶规则：
- 任一 L1 数值硬门缺失或失败（完整执行已证实）→ 最高 `Q1_EXECUTED`；
- E-num 任一子项缺失或失败 → 最高 `Q1_EXECUTED`；
- E-phys 缺参考或参考未核验 → 最高 `Q2_NUMERICALLY_VERIFIED`；
- 证据引用状态不是 CITABLE → 最高 `Q1_EXECUTED`，且不得作为任何 PASS 的证书。

---

## 3. 每个结果必须携带的 `quality` 块

以下字段缺一不可；缺项按第 2 节封顶，不得用文字补充代替字段。

```json
{
  "quality_state": "Q1_EXECUTED",
  "execution": {
    "run_id": "...",
    "stages": [{"name": "...", "state": "ok", "job_id": "..."}],
    "required_artifacts": [{"name": "...", "state": "VERIFIED", "sha256": "..."}]
  },
  "numerics": {
    "mesh_quality": {
      "cells": 58724,
      "min_cell_quality": 0.32865839082022785,
      "min_face_validity": 1.0,
      "min_volume_change": 0.022562361545129198,
      "min_wall_prism_layers": 6.0,
      "gates": {"cell_quality": 1e-05, "face_validity": 1.0, "volume_change": 0.01, "prism_layers": 6},
      "state": "PASS"
    },
    "convergence": {
      "verdict": "NOT_CONVERGED",
      "residual_gates": {"continuity": {}, "momentum": {}, "energy": {}},
      "residuals_met": false,
      "mass_imbalance_ratio": 5.26e-07,
      "mass_imbalance_gate": 1e-3,
      "target_window_relative_change": 0.022,
      "target_window_gate": 0.01,
      "target_quantities_steady": false
    },
    "sensitivity": {
      "mesh_levels": {"required": 3, "executed": 0, "gci_32": null},
      "domain_levels": {"required": 3, "executed": 0, "relative_change": null},
      "time_step": {"required": false, "executed": 0, "relative_change": null}
    }
  },
  "engineering": {
    "e_num": "FAIL",
    "e_phys": "NOT_APPLICABLE",
    "reference": {"status": "NOT_ESTABLISHED", "source": null, "value": null,
                  "tolerance": null, "relative_error": null},
    "scope_statement": "Re≈9.6e4 球体；稳态残差未过门，不能用于工程判断"
  },
  "claims": {
    "allowed": ["流程执行观察", "失败诊断"],
    "forbidden": ["工程验证", "设计定值", "与实验一致"]
  },
  "provenance": {
    "release_id": "30ab9bc...",
    "runtime_digest": "5893c0c3...",
    "case_spec_sha256": "...",
    "input_sha256": "...",
    "macro_sha256": {"...java": "..."},
    "run_record_citation": "CITABLE"
  }
}
```

---

## 4. L1 硬门（任一不过，结果不得升级）

| 门 | 判据 | 失败动作 |
|---|---|---|
| G1 输入身份 | 内容寻址 `.sim`、SHA 与审查记录一致 | 拒绝运行 |
| G2 网格质量 | Cell Quality / Face Validity / Volume Change / 实测壁面棱柱层全部达 CaseSpec | 拒绝求解 |
| G3 收敛 | CaseSpec 声明的收敛判据（稳态或 URANS 统计判据）通过 | 最高 Q1 |
| G4 守恒 | 质量不平衡在 CaseSpec 门内 | 最高 Q1 |
| G5 目标量稳定 | 力/力矩窗口或统计窗口达 CaseSpec 门内 | 最高 Q1 |
| G6 敏感性完整 | CaseSpec 要求的三网格/三域/时间步全部执行且达标 | E-num 不得 PASS |
| G7 参考完整 | E-phys 只在参考来源、值、容差三者齐全且已核验时可 PASS | E-phys 封顶 NOT_APPLICABLE |
| G8 可引用性 | 记录经 `run_record_gate.py` 判定 CITABLE | 不得作为成功证书 |
| G9 可追溯 | release、runtime、CaseSpec、输入、宏哈希齐全 | 最高 Q1 |
| G10 不越级 | 报告措辞与 `quality_state` 一致 | 报告拒绝生成 |

> **2026-10-21 命名空间注记（原文未改动）：** 本表 G1–G10 是**报告标签门**（本合同专用，对外引用建议写作 QC-G1…QC-G10）；`docs/product-acceptance-and-release-policy-20261018.md` §3 的 G1–G7 是 **PAT 发布门**（PAT-G1…PAT-G7）。两套编号同前缀不同义，审查清单引用时必须带前缀，否则 §10.4 的清单合并会发生冲突。

---

## 5. 措辞规则（报告与 MCP 回复）

| 状态 | 必须出现 | 禁止出现 |
|---|---|---|
| Q3 | “E-num PASS；E-phys PASS；适用范围：…；容差：…” | 超出适用范围的推广 |
| Q2 | “数值上自洽；未与实验/基准比对；可用于相对比较，不得用于绝对设计定值（若 E-phys=FAIL，替换中句为“已比对但参考一致性未通过”）” | “工程验证通过”“可用于设计” |
| Q1 | “仅证明流程执行；数值可信度未建立/未通过；不得用于工程判断” | “收敛”“验证通过”“结果可信” |
| Q0 | “失败；仅用于诊断” | 任何成功措辞 |

强制规则：
- 任何报告出现 `engineering_verified=true` 而无 Q3 字段 → 报告生成失败；
- 任何“验证通过”必须同时给出 E-num、E-phys、参考状态和适用范围；
- 未执行的敏感性不得写成“不敏感”或“已考虑”。

---

## 6. 发布门与 E 的关系

- **发布门 = PAT + 本合同的标签合规。** PAT 证明系统行为正确、失败如实；标签合规证明结果没有越级宣称。

> **2026-10-21 实现注记（原文未改动）：** 当前 `scripts/release_gate.py`（PAT-G1…PAT-G7）不读取 `quality_state`、不执行本合同标签合规检查；标签合规由报告渲染路径强制（`scripts/quality_contract.py::validate_report_claims`，违例抛 `QualityContractError`）。即“通过发布门”不自动保证“存在合规报告”。这是已知的双轨语义，收敛方案（把标签检查接入发布门，或在门输出中显式声明“标签合规未检”）待后续轮次决定。
- **E 不作为发布门。** 一个发布的 MCP 可以只支持 Q1/Q2；但支持范围矩阵必须如实写明每个场景/样本的最高状态。
- 任何场景若没有 Q2 结果，矩阵标 `NUMERICALLY_UNVERIFIED`；没有 Q3 标 `ENGINEERING_UNVERIFIED`。这不阻止发布，但阻止用户误以为它已验证。

---

## 7. 支持范围矩阵（发布必需）

`README` 与每份报告必须包含：

| 场景/样本 | P | 最高质量状态 | E-num | E-phys | 适用范围 | 已知限制 |
|---|---|---|---|---|---|---|
| ① 球体 Re≈9.6e4 | PASS | Q1 | FAIL | NOT_APPLICABLE | 流程演示、失败诊断 | 稳态残差未过门；URANS 未建立 |
| ② AIR-3B MRF | PASS | Q0（规范化完整执行证明未建立） | 按最新记录 | NOT_APPLICABLE | 失败诊断 | 残差平台、GCI/域/近壁未闭合 |
| ③ 瞬态旋转 | PASS | Q0（规范化完整执行证明未建立） | 未评估 | NOT_APPLICABLE | 流程演示 | 周期性与时间步敏感性未建立 |

矩阵中的每个状态必须指向一条可引用记录，不得只写结论。

---

## 8. 证据与可引用性

- 每个质量字段必须能追溯到记录路径和 SHA；不得用人工摘要代替。
- 失败阶段里的 `status: ok` 记录不得升级任何状态（沿用 `run_record_gate.py`）。
- 历史记录、旧批准、旧输入不得改写；状态变化只通过新记录表达。
- 任何“不可引用”的记录只能用于失败诊断，不得作为 Q2/Q3 的证书。

---

## 9. 周期性验证（L3）

- 每个受支持场景至少登记一个**有已核验参考**的基准样本；按季度或按重大改动重跑。
- 基准样本必须给出：参考来源、容差、E-num 证据、E-phys 误差；结果写入支持范围矩阵。
- E 战役可以使用探索模式（不受一次授权限制的 scratch worker），但只有通过独立审查的候选才进入正式证据。
- L3 的目的是校准系统，不是为每个版本背书；未完成 L3 不阻塞发布，但必须显示为 `UNVERIFIED`。

---

## 10. 执行与测试

最小实现（按现有代码落点）：

1. `scripts/generate_run_report.py` / `scripts/formal_run_report.py` 生成 `quality_state` 与 `quality` 块；缺字段直接拒绝生成报告。
2. `scripts/run_record_gate.py` 作为 G8 的唯一判据；任何 PASS 引用必须先过它。
3. 新增 `tests/test_quality_contract.py`：
   - 正例：Q1/Q2/Q3 各一条，字段齐全、措辞正确；
   - 反例：缺收敛字段、缺敏感性、参考未核验、失败记录冒充成功、报告写“工程验证通过”但状态 Q1/Q2；
   - 每条反例必须导致报告生成失败或状态降级。
4. 独立审查清单加入本合同第 4、5 节；审查者逐项核对，不得只看文字。

---

## 11. 豁免与版本

- 豁免只能由用户书面批准，必须记录范围、理由、到期时间和补偿检查；不得豁免 G1、G8、G10。
- 本合同版本化；修改判据必须走独立审查，并保留旧版本与旧记录。
- 本合同不降低任何现有 CaseSpec 门槛；它只规定结果如何标注、何时拒绝、如何呈现。

## 可执行解释与当前范围

- 单一实现为 `scripts/quality_contract.py`；旧报告中的布尔观察只保留作兼容数据，不认证 Q2/Q3。
- 执行图取真实 request 的有序步骤；实际 state/stage/产物哈希、runtime manifest 及其工作流 SHA 必须绑定。失败或缺必需产物为 Q0；完整执行而缺数值/引用/身份字段，最多 Q1。失败阶段的 `status: ok` 不能作证书。
- 缺实测值按 null 封顶；已渲染 quality 块缺必填结构时拒绝报告。不得用缩短 required 产物列表消除失败。
- Q2 逐项验证 CaseSpec 原门、实测残差/守恒/目标量、三档网格与域、适用的时间步敏感性。逐档记录必须可引用且数值合格，GCI 与相对变化由代码复算；不合格记录不能计算为通过。
- Q3 还需核验参考源 artifact SHA、独立参考审查与 CaseSpec/范围/容差绑定、本次实测目标量及误差。`verified=true` 和来源文字本身不足。旧接口 `engineering_verified=false` 保留，以 quality_state/quality 表达分级。
- 报告不得出现越级肯定措辞；否定描述、隔离的原始数据不作为肯定结论。Q1 的生成模板只写流程观察和强制限制语。
- 历史 PAT 证据不改写，重新报告和只读快照写入新目录，旧远端 `report.state.json` 保留。
- 当前矩阵由 progress 与可引用证据生成；旧样本若缺规范化完整执行/产物绑定，显示 Q0 与 UNVERIFIED，而不撤销独立的历史 P 验收。不得猜测 Q2。
- E 战役与 BOM 修复保持 parked；本合同不授权任何 STAR/部署，§9 探索模式不能代替用户逐次授权。PAT-18d 仍为发布门 PASS；P=3/3、E=0/3。

- 敏感性相对差：比较所有档位对，使用 `abs(a-b)/max(abs(a),abs(b))`，非仅相邻档位；网格按真实单元数导出的等比细化比与 Fs=1.25 复算 GCI。逐档绑定各自的真实有序执行图、CaseSpec、runner 产物和目标统计源；仅允许研究变量不同，不能跨样本拼接。
- G9 的单字段缺失可封顶 Q1；如果连执行图、runtime/manifest 或必需产物都无法证实完整，则 Q0。不可引用的诊断记录不能作成功证书；完整执行且缺数值证书时最高 Q1。

- 参数形状沿用真实 CaseSpec：域 `outer_bounds_m=[xmin,ymin,zmin,xmax,ymax,zmax]`，域研究参数取其体积；瞬态 `transient.time_step_s`。等比三网格 GCI 为当前已实现的方法，非等比网格不猜测观测阶，保守封顶 Q1。Q2/Q3 正例为明确标注的构造记录，当前实测最高 Q1，不代表新增 E。

## Phase A 呈现与历史诊断

`quality_state`、`scope_statement`、`forbidden_uses` 固定在 get_run/fetch/report 返回与报告顶部；Q0 还必须有可复算的 `Q0_REASON`。缺字段、原因与实际证据不符、或 Q1 越级声明均拒绝报告。缺阶段/宏状态只说明 unknown，不能发明 `STAGE_FAILED`。当前②③已有旧交付与输入/宏哈希，缺少可认证的历史 runtime/manifest/完整根请求绑定；仅新建当前观察者回读不能冒充历史执行者，保持 Q0 `UNBOUND_HISTORICAL_EVIDENCE`。其 P 验收不变。

> **2026-10-21 术语注记（原文未改动）：** 上文“get_run/fetch/report”的准确工具名为 `get_run` / `fetch_run_results` / `report_run`（与 release policy 末节及 README 生成矩阵一致）。

README 与中文报告共享同一矩阵生成函数，传入与中央矩阵不符的数据会拒绝生成。统一错误动作及缓存占用、30天人工保留复核策略见 [MCP 用户指南](mcp-user-guide.md)，配额不以自动删除历史文件实现。E 战役保持 parked，本节不授权 Phase B 计算。

---

## 12. 用户签字的合同修正（2026-10-23，合同对齐轮）

本节为**增补**，上文原文一字未改。两条修正均由用户于 2026-10-23 的 AskUserQuestion 明确签字，签字凭据为本轮提示词 `docs/next-round-prompt-20261023-contract-alignment.md` 及该次交互记录。

修正的起因：机算判决 `test-evidence/handoff-20261023/Q3-MACHINE-VERDICT.json` 给出 `Q0_FAILED` 与六条具名 blocker，其中两条不是证据缺陷，而是**合同文字与已登记工具、已签字参考状态不一致**——合同在要求它自己登记的工具并不要求的条件。

### C1 网格族准入：逐对实测比，不要求等步长（修 P3）

**原实现：** `quality_contract.sensitivity_proof` 对三点网格族硬编码 `math.isclose(r1, r2, rel_tol=1e-6)`，即要求相邻加密比彼此相等。

**修正为：** 相邻比逐一实测、每个比值都在登记门内，且**逐对计算**：

1. 相邻 `cell_ratio` ≤ **2.2**；
2. 相邻 `h_ratio` ∈ **[1.26, 1.32]**；
3. 原生网格质量 QC-G2（Cell Quality / Face Validity / Volume Change / 实测棱柱层，按 CaseSpec）——此项不变。

门值出处为 `docs/ruling-family-admission-gates-20261022.md` 第 1、2 条（s3-a 登记门原值），**不是本轮新设**；该裁定已声明"不改任何登记门"。本修正只是把合同里"相邻比一致"这一**合同自造**的要求，换成登记门里本来就有的要求。

**为什么这样修：** 登记工具 `scripts/grid_convergence.py`（sha256 前缀 `40272873`）的 `three_grid_gci` 接受单个 `ratio` 入参、对给定一对相邻差计算、**从不取平均**、且明确不对 `log` 取绝对值。工具本身完全支持非等比族；是合同多加了工具没有的等步长要求。修正后，合同与登记工具一致。

**不取平均的约束仍然成立：** 修正后每一个实测比各自调用一次 `three_grid_gci`，两个结果都须 `valid` 且 `converged`，不得先平均再算、也不得以任一比代替另一比。反例 `tests/test_contract_amendments_20261023.py::C1PerPairRatios` 逐条钉住：非均匀但在门内 → 准入；任一对越门 → 拒绝；均匀但越门 → **同样拒绝**（本次修正不是放宽）；cell 门与 h 门各自独立生效。

**数学旁注（不影响判据）：** 三点 GCI 的外推值与误差界只依赖两个相邻差之比，`r` 在 `expm1(p·ln r) = |ε21|/|ε32| − 1` 中被约掉，故不同 `r` 给出逐位相同的 GCI，只有观测阶 `p` 随 `r` 变。这正是"逐对计算"不会自相矛盾的原因，也是双口径 `r_cell^p_cell = r_h^p_h` 恒等式的根据。

### C2 参考登记状态词表：纳入 APPROVED_A_LEG_RATIO（修 P5）

**原实现：** `_assessment` 只接受 `ref['status'] in ('ESTABLISHED', 'VERIFIED')`。

**修正为：** 词表 `REFERENCE_VERIFIED_STATUSES = ('ESTABLISHED', 'VERIFIED', 'APPROVED_A_LEG_RATIO')`。

**依据：** `APPROVED_A_LEG_RATIO` 是用户 2026-10-22 签字确认的参考登记有效状态，其验证方式为**算术复算**（Schiller-Naumann 关联式 `Cd(Re=100) = 1.0917310910948732`，可复算），B/C 腿定性为**待补增强**而非缺失验证。登记文件 `docs/reference-registration-20261021/REFERENCE-REGISTRATION.json`（sha256 前缀 `03b08b4a`）未被改写，其文件内状态仍为 `PENDING_INDEPENDENT_REVIEW`，有效状态由 `test-evidence/def-round-20261022/REFERENCE-STATUS-20261022.json` 以新记录表达，符合本合同 §8"状态变化只通过新记录表达"。

**词表仍是封闭的：** 加入一个已签字状态不等于开放词表。`verified` 必须为 `True`，来源、值、容差、相对误差、字段绑定、独立参考审查仍逐项核验；词表外任何状态（含 `PENDING_INDEPENDENT_REVIEW`）依旧封顶。反例见 `tests/test_contract_amendments_20261023.py::C2ReferenceVocabulary`。

### 未纳入本轮修正的事项

- `legacy_label_observations` 中 `reference_status == 'ESTABLISHED'` 的入口判断**未改**：它是旧报告列的兼容投影，不是主判定，改动会改变历史报告的兼容语义。主判定路径（`_assessment`）已经用词表常量。
- P1/P2/P4/P6 不属于合同文字遗漏：它们是战役证据的**组织形态**（逐档 CaseSpec、域研究事后实测、作业级授权）与合同期望的（单一多层声明）之间的结构差异，由战役级绑定文件 `docs/campaign-binding-e-num-20261023/` 以**喂入**方式对齐，不改各档 CaseSpec、不发明新授权。若喂入后仍有 blocker，按 §3 如实落档，不手授、不放宽。

---

## 13. 第三次用户签字的合同修正（2026-10-23，P6 修正轮）

本节为**增补**，上文原文一字未改。修正由用户于 2026-10-23 的 AskUserQuestion 明确签字，凭据为本轮提示词 `docs/next-round-prompt-20261023-p6-amendment.md`。

**起因：** 合同对齐轮机算重跑消解了 P1–P5，唯 P6 `execution:authority_missing_or_different` 仍阻断。根因不是证据被跳过，而是**合同形态覆盖不全**：one-shot 准入是探针/诊断作业的既定授权机制，而合同只认得"单一根请求 + 有序 steps 图 + runtime manifest 快照"的 pipeline 形态，从未为由逐作业授权拼装的多层战役定义形态。补齐 run_id、request_digest、steps 图会构成发明授权，为裁定所禁止。

### C3 执行授权的第二条合法形态：one-shot 授权集合

`execution_authority` 在原 pipeline 路径之外增加第二条路径 `execution_authority_collection`，当且仅当 `ex['collection']` 声明、且下列五项属性**逐一机检通过**时成立。原路径优先且未被削弱；未声明集合时行为与修正前完全一致。

1. **时序**——每个成员作业的**准入提交时间戳先于**该作业提交时间。时间戳取自引入该记录的 git 提交（`quality_contract.git_introduced`），不取记录内部字段：提交时间戳无法由写文件回溯伪造。
2. **钉扎**——每个成员准入钉住该作业的源/宏、CaseSpec、请求与 runtime-digest 观测值的 SHA-256，且钉住值与作业自身记录一致。批量准入（`requests` 块登记多个请求）按其块内登记逐项比对（`admission_names_request`），不推断。
3. **独审**——每个成员准入有独立审查文件，且其哈希在集合绑定中被实算；该审查必须由**准入自身以哈希字段点名**（排除 `deployment_review_sha256`，那是对运行时的部署审查，不是对本准入的审查），并声明独立审查者。
4. **集合绑定**——绑定文件本身经哈希实算（`checked_file`），成员清单完备：`declared_jobs` 与成员集合相等、成员不重复、无未登记作业。
5. **runtime 绑定的替代证据**——每个成员作业记录含 `runtime_digest` 观测值，与活动部署指针一致。指针由 `review/formal-worker-deployment-active.json` 经哈希读取其指向的部署审查得到，是**实测量**而非常量。

**反自由度条款（本修正的核心，与五项属性同等效力）：** 上述五项**全部只能由作业执行前/执行时产生的记录满足**。本形态**不接受事后补写的授权文件、steps 图或 manifest 快照**。它定义的是**已发生事实的机器可读汇总**，不是事后补造的通道。机器上的落实方式是时间戳：任何事后写成的文件，其引入提交都晚于它必须为早的提交记录，故无法通过。合同常量 `COLLECTION_NO_RETROFIT` 与 `COLLECTION_FORWARD_LOOKING` 把这句话写进判定所依赖的文本，测试 `test_a_review_written_after_submission_is_refused` 钉住它。

**展望条款：** 未来战役**优先用 pipeline 执行并保留 runtime manifest 快照**；本形态是为既有 one-shot 记录定义的可机检汇总形态，不作为新战役的授权通道。

**反例测试：** `tests/test_contract_amendment_c3_20261023.py`（13 条）——合法集合通过（正例取自本轮真实记录：四个带准入点名独审的成员）、时序倒置拒绝、缺独审拒绝、审查非准入库点名拒绝、事后补写审查拒绝、集合哈希篡改拒绝、runtime_digest 缺失/不一致拒绝、成员清单不完备拒绝、未声明形态拒绝、未声明集合时原路径不变；另有一类直接对真实战役集合断言"恰是八个具名成员缺独审"，其余属性全部成立。

### 本形态在本战役上的机检结果（如实记录）

集合取自 `docs/campaign-binding-e-num-20261023/EXECUTION-AUTHORITY-COLLECTION.json`，成员为受评的十二个作业（F2/F3/F4 与 B0/D1/D2 各网格+求解各一）。机检结论：**时序 12/12 成立、钉扎 12/12 成立、runtime 观测值 12/12 与活动指针一致、集合绑定哈希成立；独审 4/12**——`F3/solve`、`F4/solve`、`domain-B0/mesh`、`domain-B0/solve`、`domain-D1/mesh`、`domain-D1/solve`、`domain-D2/mesh`、`domain-D2/solve` 八个成员的准入**没有点名任何独立审查文件**（其准入自身声明 `independent: true` 与离线复算审查者，但那不是 C3 第 3 项要求的独立审查文件）。F0 三个作业无准入记录且运行于已被取代的 runtime `b593f736`，F1 不在受评三元组内，均列为"已声明、未受评"并写明理由，不静默丢弃。

据此 P6 未被消解，机算输出仍 `Q0_FAILED`、`granted_tier = null`，E 计数与 `engineering_verified` 不因本修正移动。八个成员缺独审属**证据真实缺陷**：补写新的独立审查文件即为事后补造，为本形态明确拒绝；故只能由新证据（新战役按 pipeline 执行并留存 manifest 与逐作业独审）改变，不能由再次修正改变。
