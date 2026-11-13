# 产品验收与发布政策（2026-10-18）

## 0. 三项用户决定

2026-10-18，用户明确接受：

1. **E 作为质量标签，PAT 作为发布门。**
2. **① 静止外流 + ② 稳态 MRF 首发；③ 旋转瞬态作为 beta。**
3. **允许“预审操作目录 + 运行时不逐次审查”的生产路径。**

这三项决定改变发布口径，但不降低“不得假通过”的底线。E 仍然计算、仍然报告，只是不再作为整个产品的发布门票。

## 1. 三层验收

| 层 | 回答的问题 | 判据 | 是否发布门 |
|---|---|---|---|
| **P 流程验收** | 这条流水线能不能把这个算例跑完、复现、失败时如实关闭 | 阶段完成、哈希可追溯、失败不假通过 | 是（隐含在 PAT 中） |
| **E 工程验证** | 这个算例的物理结果是否达到它自己的 `CaseSpec` 门槛 | 收敛、守恒、GCI、与参考值在容差内 | **否**，作为标签 |
| **PAT 产品验收** | 换几何、换工况、换名称、注入故障后，系统能不能无人值守地跑完并诚实报告 | 固定用例集的行为与产物 | **是** |

E 再拆成两个字段：

- **E-num**：数值可信度——收敛、守恒、目标量稳定、GCI、时间步敏感性。
- **E-phys**：与实验/基准的一致性——只在有可信参考时才有意义。

没有实验参考的工业算例可以 `E-phys = NOT_APPLICABLE`，但仍然可以 `E-num = PASS`。E 标签必须随报告交付，且不得被 PAT 的通过所替代。

**2026-10-24 口径：** 本政策只约束**产品发布**（PAT 门）。**算例/工程交付的完成判据是 E**（`AGENTS.md` §1/§9）：P/PAT 全绿不是“算得对”的证据；没有参考时 `E-phys=NOT_APPLICABLE`，最高只能到 Q2，报告不得写成“正确/工程验证”。

## 2. 发布范围

**v1**：场景 ①（静止外流）和 ②（稳态 MRF）。② 的 v1 范围明确限定为 AIR-3B 样本族；它不是通用螺旋桨能力，报告必须写 `sample_specific`。

**beta**：场景 ③（旋转瞬态）和 Windows 重启恢复。beta 用例必须存在、必须诚实；默认不阻塞 v1 发布，但 beta 的假通过仍然阻塞。

## 3. 发布门规则

`scripts/release_gate.py` 是唯一判据，规则如下：

- **G1** PAT-RESULT 必须由当前 PAT 定义生成（`pat_sha256` 匹配）。
- **G2** 每个 `release_channel=v1` 且 `required_for_release=true` 的用例必须 `PASS`。
- **G3** 任何用例不得出现 `FALSE_PASS`：记录不得声称 `engineering_verified=true`。
- **G4** beta 用例必须存在于结果中；默认允许 `NOT_RUN`，`--require-beta` 可把 beta 通过变为要求。
- **G5** v1 的 `NOT_RUN` 是失败，不是警告。
- **G7** 部署模式还必须至少一条v1 required、verification_basis=deployed_worker_execution、test_kind=full_chain且result=PASS。正式提交前被拒绝、仅历史full_chain、beta或非required、失败/未运行均不得满足；打印full_chain的通过/登记计数。非deployed模式不增加本规则。
- **G6** `release_context=deployed` 时，每个用例必须声明合法 `verification_basis`。至少4条 v1 required 的 `deployed_worker_execution` 必须 PASS，且含一条 `expected=SUCCESS`、一条 `expected=FAIL_CLOSED` 和 `PAT-16-checkpoint-delivery`。历史回读不得计入该最低数。
- **2026-10-21 命名空间注记（原文未改动）：** 本节 G1–G7 是 PAT 发布门（建议引作 PAT-G1…PAT-G7）；`docs/quality-contract-20261020.md` §4 另有一套 G1–G10 报告标签门（QC-G1…QC-G10）。另：上两行 G7 列于 G6 之前是排版笔误，条文逻辑不受影响，原文不改。
- 历史回读只能标 `historical_evidence_readback`，并要求 `simulation_rerun=false`；正式执行证据必须位于 `test-evidence/product-acceptance/deployed-20261018/` 下，排除 `history/`、路径穿越和解析后越界。可选 beta 的空证据保持 NOT_RUN，不取得执行计数。
- 报告分别打印证据身份与部署路径的 `{passed}/{total}`。分母统计已登记非空 evidence 的用例，包含已运行的 beta；PAT-15 的空证据不计入分母。发布最低4条另按 v1 required 计算，beta 不能凑数。
- 默认结果优先使用 PAT 的 `result_file`（相对路径以 PAT 所在目录为基准），否则用 `<PAT stem>-RESULT.json`；优先文件不存在时才明确警告并回退 `PAT-RESULT.json`。显式 `--result` 不回退，所有路径仍受 stale SHA 检查。

`PASS` 的含义按用例的 `expected` 区分：

- `SUCCESS`：运行完成、所有阶段 ok、必需产物存在、没有工程验证声明。
- `HONEST_NEGATIVE`：运行完成、所有阶段 ok，但声明的标签记录明确写着未收敛/未周期/未工程验证。**系统跑完并说了真话，算 PAT 通过。**
- `FAIL_CLOSED`：运行以有记录的失败停止，且没有成功证书。**正确失败算 PAT 通过。**

## 4. 当前 PAT 快照（2026-10-20跨阶段交接）

活动MCP业务release仍为`30ab9bc558f64442029463036b300beed9e1fd3ee12e9186bf2c84763bbf0596`；本轮无Python业务/宏/runner/判据改动，未prepare/activate业务发布。只在static workflow的region-physics-mesh声明从阶段名build继承external_domain_build.json；独立审查生产者/消费者、两层哈希复核及反例后，部署新Windows runtime `5893c0c36ff1c2320b9f34725b6a9e2d795c2d120520de4ffd5b41aa2c0278a0`。实际health四项true。

static formal新版本2026-10-20a独审批准，review通过materialize-formal机械生成，绑定新runtime/manifest及原runner/宏/input/semantic-v1 scope；旧批准不改。PAT18 d从geometry library输入重新提交一次，未resume c，六阶段6/6为ok，结果PASS。真实inherited_inputs及required三方SHA见[回执](../test-evidence/product-acceptance/deployed-20261018/handover-runtime-20261020/REPORT.md)。不预签未执行阶段结果。

当前**证据身份12/12、部署路径5/5、full_chain1/1、v1 16/16、beta1/2、FALSE_PASS=0、退出码0**。没有剩余v1发布阻塞。 PAT18的SUCCESS、required、deployed/full_chain身份不改。短PAT13/14/16/17继续沿用同业务release30ab9bc原证据，明确其旧runtime11163…；本轮只d为新runtime5893…实跑。PAT14阈值注入、PAT16一步续算、PAT17单阶段smoke不能代替完整链。

76项指定离线测试通过；既有零字节POLL和test_stage_handover StructuralPins错误保留，未改历史。validation只读审计如实报告NOT_CONVERGED，残差/目标量稳定未满足，参考NOT_ESTABLISHED；没有降低质量门。PAT15 beta NOT_RUN，重启/计费幂等机制无变化；不阻塞v1。P=3/3、E=0/3保持，PAT不等于物理验证。下文§8–11是历史轮次，不覆盖本节。

## 5. 生产操作目录与运行时审查

现行证据路径要求每次提交都有一份独立的 `pipeline-review.json`。这对新产品研发是正确的，对无人值守产品不可行。决定 3 允许的生产路径如下：

1. 操作在进入生产目录前完成一次独立审查，审查记录钉住：pipeline、controller、runner 两层、每个宏、support 文件、resource contract。
2. 生产目录条目记录 `approved_for_autonomous_run=true`、`scope`、`release_channel`、`approved_hashes`、`review_record`。
3. 运行时由 `scripts/production_catalog.py materialize` 从目录条目**机械生成**一份 `pipeline-review.json`：
   - `approved=true, independent=true`；
   - `reviewer="catalog:<entry>@<version>"`；
   - 宏/pipeline/support/controller/runner 哈希来自目录条目；
   - `input_hashes` 来自本次实际输入；
   - 若目录条目要求输入预检，则必须存在 `status=ok` 的几何/单位/角色预检记录。
4. 现有 `air3b_pipeline_trial.check_review` 和 Windows runner 继续逐字节复核；任何字节漂移都会让生成的记录失配，运行被拒绝。**没有删除任何哈希门，只是把“人工逐次审查”换成“目录预审 + 运行时机器比对”。**

这样做的边界：

- 目录条目只覆盖**预审过的字节**。宏、pipeline、runner、controller 任一变化，条目失效，必须重审。
- 输入不是“预审过”的；它由输入预检门保证（几何闭合/流形、单位、角色、接口）。输入预检失败即 fail-closed。
- 生产目录不适用于新操作或改动的操作；它们仍走证据路径的完整审查。
- 生产目录的批准记录本身也要有独立审查，且必须能被执行门读取，不能只是文档。

## 6. 不变量

- 可以算不准，不可以把算不准写成通过。
- 失败必须留下可解析记录，且来自失败阶段的记录不得被引用为成功。
- 任何“放宽”都必须有用户授权、旧判据保留、变更记录可追溯。
- E 标签不得被 PAT 通过替代；PAT 通过不表示物理正确。
- 生产目录不得成为绕过审查的通道；它只是把审查从“每次运行”前移到“每个操作版本”。

## 7. 命令

```bash
# 评估 PAT（读取既有证据，不运行 STAR）
python3 scripts/run_pat.py \
  --pat test-evidence/product-acceptance/PAT-20261018.json \
  --root . \
  --out test-evidence/product-acceptance/PAT-20261018-RESULT.json

# 发布门（v1 必需用例必须全过；beta 默认只要求存在）
python3 scripts/release_gate.py \
  --pat test-evidence/product-acceptance/PAT-20261018.json

# 测试发布门本身
python3 -m unittest tests.test_product_acceptance
```

## 8. 历史生产目录与部署状态（当前见§4）

历史CLI v1 PAT为14/14；当前部署PAT为 **证据身份12/12、部署路径4/5、full_chain 0/1，v1 15/16、beta 1/2、release gate退出码1、FALSE_PASS=0**（完整链未运行，详见§10）。P=3/3、E=0/3 保持，静态与 MRF c 仍是 P=PASS、E-num=FAIL、E-phys=NOT_APPLICABLE。

| 条目 | 活动支持版本 | 通道与范围 | 状态 |
|---|---|---|---|
| `static-sphere-chain-v1` | `2026-10-18g` | v1，精确球体输入 | 独立批准，verify/materialize/check_review 通过 |
| `air3b-mrf-chain-v1` | `2026-10-18g` | v1，MRF c，`sample_specific=true`、`allowed_case_ids=["air-three-blade"]` | 同上；原 c 的 pipeline/宏/输入不变 |
| `air3b-transient-chain-beta` | `0-pending` | beta，AIR-3B 瞬态 | 未批准 |

旧批准和 MRF b 的 47/44 壁面失败原样保留。g 只升级经过独审的支持/准入实现，不放宽原质量、收敛或适用范围。+Z 圆柱全链 pipeline 尚未定位，未登记生产目录。

正式 worker 已在版本 d 独立审查后部署，Windows runtime digest 为 `2723edb01a7b7b9b54276bf36d0b75e69328a6e99c6c6f4d9dbbc61fdef6f492`。完整 manifest/131 文件、双 runner、宏与输入复核；两层总耗时停止已删除，时间值只观察。默认 900 秒健康窗只判无日志/产物进展，受管树终止反例通过。计划任务均 `ExecutionTimeLimit=0`，受限固定 SID 非管理员；runtime 不可写、凭据不可读、job 可写，真实身份/文件操作探针 PASS。原三类 open counterexamples 已关闭，旧 open 记录不改。

桌面 MCP 已激活独立批准的业务发布 `a374487c44f35ecb853a772844bd24b81c7ad4f5d5763cf858e61af113f4c606`。两次实际桌面回读确认 `worker_deployed=true`、`all_files_match_disk=true`、`gateway_matches_disk=true`、`desktop_runtime.loaded_matches_disk=true`；入口 PID 94473 不变，业务 PID 改变。证据不主张响应里未保存的 `_meta` 字段。

部署 PAT 的来源明确分开：PAT-01～12 经用户明确授权，保留历史 CFD 字节，新增桌面操作接口回读和 151 文件源/副本 SHA/中央可引用性回归；没有在新 worker 重算。PAT-13 是新桌面 job/run 占槽拒绝，无新目录/任务；PAT-14 是新 worker 的严格 CQ=1.0 故障注入，实测 CQ=0.328658、QA 宏失败，记录不可引用为成功，原门不改；PAT-16 是新 worker 从旧600步副本仅推进至601并交付 SHA 绑定的 `.sim`，converged=false，不冒称五阶段全链重算。

部署定义已配置 `result_file="PAT-DEPLOYED-RESULT.json"`，验收无需手工 `--result`：

```sh
python3 scripts/run_pat.py --pat test-evidence/product-acceptance/PAT-DEPLOYED-20261018.json --root . --out test-evidence/product-acceptance/PAT-DEPLOYED-RESULT.json
python3 scripts/release_gate.py --pat test-evidence/product-acceptance/PAT-DEPLOYED-20261018.json
```

原默认读旧结果而拒绝的记录保留；本轮修复自动选择并加严部署证据门。两版PAT-13陈旧notes已修正，旧定义/结果快照保留，历史CFD、旧批准、MRF b失败及旧PAT-RESULT均未改。

## 9. 历史测试、质量标签与缺口（当前见§4）

报告保持 P / E-num / E-phys / PAT 分栏，任何记录不得声明 `engineering_verified=true`。本轮4个独立、串行的单宏请求：smoke成功、取消终止、QA按预期失败、检查点交付成功；保守 worker stage 区间总计196.29秒，含启动/监控/检查开销，非精确 STAR PID 秒、非CPU核小时，一次记账。取消副本CSV记录601但无新保存解，未记录的后续内存进展未知；PAT16独立副本保存601。没有新CAD/网格或工程验证晋升。

PAT-15 保持 **beta NOT_RUN**：正式 worker 已部署，但尚无真实重启后不重复迭代/计费、不覆盖产物的验收；不以stage级检查点功能代替恢复通过。

正式 Windows 反例11/11通过，新增11项ACL白名单反例通过；最终产品回归539项：513通过、26跳过、0失败/错误，退出码0；Windows反例另行实际执行。全仓严格证据检查仍有两项既有失败：2026-10-16 零字节 POLL 违反 ParseRatchet，旧诊断宏合法 `q()` 转义被 scanner 误报。历史文件与门未改，不声称全部测试通过。

[部署回执](../test-evidence/product-acceptance/deployed-20261018/REPORT.md) · [真实健康回读](../test-evidence/product-acceptance/deployed-20261018/HEALTH-BEFORE-AFTER.json) · [部署 PAT](../test-evidence/product-acceptance/PAT-DEPLOYED-RESULT.json) · [独立部署审查](../test-evidence/product-acceptance/deployed-20261018/review/formal-worker-atomic-state-independent-review-d.json)

2026-10-19验证基础修订只读取既有证据，未调用STAR、远端提交或重新部署。新增反例、实际命令、前后计数及字节保护结果见[本轮回执](../test-evidence/product-acceptance/deployed-20261018/basis-gate-20261019/REPORT.md)。既有Windows 11/11是2026-10-18证据，本轮未重跑远端测试。

2026-10-19离线产品回归561项：535通过、26跳过、0失败/错误；定向45/45通过（新增22项）。严格证据检查仍为36通过、两项既有失败；1,270保护文件SHA保持，不声明全仓全绿。

## 10. 历史a尝试与当时发布阻塞（当前见§4）

目录static-sphere-chain-v1（2026-10-18g）的精确输入/pipeline/宏及批准未改；materialize成功生成pipeline-review.json。新ID=formal-pat18-full-chain-20261019a，仅调用一次正式submit_run，实际网关响应UNPUBLISHED_CODE，尚未进入任何worker阶段。get_run因远端state.json不存在而拒绝；独立只读检查确认无run/job/task、STAR=0、运行受管任务=0。没有二次提交、重试、取消或按墙钟停止。

次级离线诊断确认：当前materialize是受限试跑审批格式，正式load_review_record首先拒绝缺pipeline_runner_sha256；两种入口还绑定不同controller、runner、manifest与输入键。目录预审不能凭手工改review扩展为正式批准。业务发布与受审正式目录接线均未在本轮修改；修复后仍需新的明确运行授权，不自动重提本请求。

PAT18的三份产物未产生，runner/远端/桌面SHA均标NOT_PRODUCED，不报告三方通过；没有伪造worker state/stage。本轮新增full_chain发布门和拒绝反例；可选q() scanner修复未做，两项既有严格全仓失败保持。

[本轮回执](../test-evidence/product-acceptance/deployed-20261018/full-chain-20261019/REPORT.md) · [原始提交](../test-evidence/product-acceptance/deployed-20261018/full-chain/SUBMIT.json) · [三方SHA未产生记录](../test-evidence/product-acceptance/deployed-20261018/full-chain/TRANSFER-VERIFY.json)

完整链轮的离线产品回归570项：544通过、26跳过、0失败/错误；定向54/54通过。严格证据检查38项：36通过、2项既有失败，未修scanner或历史POLL。上述测试通过不改变PAT18 NOT_RUN与release gate=1。

## 11. 正式目录桥接与本轮执行授权

受限trial的materialize保持原语义。正式materialize-formal只采用独立formal批准，绑定backend controller、双runner、每workflow manifest、全部stage宏、不可变library输入、实际CaseSpec/有序阶段/参数/操作计划、support/resource/runtime pins；提交时再次复核scope，漂移即拒绝。生成review不得手工补字段。旧批准和旧输入不覆盖，新实现需新独审与发布。

本轮PAT18额外一次性用户授权来自最新任务的明确要求；目录/实现/发布批准不替代这次运行授权。完整链成功也不证明E。业务release关联以原始健康前后、新提交、远端request/review/runtime证据推断；connector未返回的_meta不得补造，不得只给旧证据添加release_id就宣称fresh。上述§8–10为既有轮次事实，当前状态以§4和当前报告为准。

## 2026-10-20 可执行结果质量合同

PAT 是发布门，E 是质量标签；E 战役 parked，P=3/3、E=0/3。报告与 get_run/report_run/fetch_run_results 统一调用 `scripts/quality_contract.py`，每份结果携带 quality_state/quality，流程、送达、工程三列分离。Q0 诊断，Q1 只证明流程执行，Q2 限同一模型内相对比较，Q3 仅限核验参考的范围和容差。缺实测字段封顶，越级措辞拒绝生成。支持矩阵由 progress 与可引用记录计算，旧 PAT 及 v1 证据不重算、不改写。业务 Python 按独审 release 更新，不部署 Windows runtime、不运行 STAR。
