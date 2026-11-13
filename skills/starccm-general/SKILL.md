---
name: starccm-general
description: 使用 STAR-CCM+ MCP 接入已有 .sim、审计几何与模型、整理 CaseSpec、检索 2502 资料、校验可执行计划并管理持久仿真任务。适用于通用 CFD 自动化；具体物理步骤由已验证操作和场景技能定义。
---

# STAR-CCM+ 通用算例工作流

用 `starccm-cfd` MCP 连接 Windows 工作器。先调用 `list_operations` 看**登记**了哪些操作与它们的证据，再用 `resolve_operations` 把操作解析成**真实调用路径**（阶段是否存在、宏是否在盘上、参数是什么、哪些是样本专属），最后用 `connection_health` 检查 STAR 版本和部署状态。**登记不等于可用**：`list_operations` 只说明声明，`resolve_operations` 才说明可用；解析不到的操作会以 `UNRESOLVED` 和原因返回，绝不当作已实现。操作按 `mechanism` 分三类：`workflow_stage`（正式 MCP 路径可提交的阶段）、`restricted_trial`（由已审查的受限试跑入口执行整条流水线）、`offline_tool`（离线工具，不启 STAR 进程）。

拿到用户保存的 `.sim` 后，用 `register_case` 从 Mac 文件或允许的 Windows 目录建立 SHA-256 校验副本，再用 `inspect_case` 得到结构化几何、流体域、边界、接口、物理模型和报告清单。审计不会保存或修改 `.sim`，但会短暂导出并清理 STL 以测量 CAD 包围盒。关键工况一次集中补齐，形成 SI 单位的 CaseSpec；字段及停止条件见 [references/casespec.md](references/casespec.md)。包围盒按原始坐标记录，单位核对前不能直接作为米使用。用成功的 `inspect_job_id` 调用 `validate_plan`，使几何选择和边界角色与真实审计产物对应。

根据用户目标、工况和审计证据选择已安装操作。四个能力等级**分开看**：`implementation`（有真实调用路径）、`verified_on_current_case`（有**本算例**的可引用证据，来自 FAILED 运行的记录不算）、`general_acceptance`（有无代表性正反例；只有正例或样本专属时不算通用操作）、`engineering`（恒为 `NOT_CLAIMED_BY_WIRING`——接线只证明操作能跑，永不证明物理可信）。AIR-3B 那类靠固定名选择的能力是 `sample_specific`，`provides` 为空：它不能满足通用计划的任何要求。外流与旋转分别读取 [starccm-external-flow](../starccm-external-flow/SKILL.md) 和 [starccm-rotating-machinery](../starccm-rotating-machinery/SKILL.md)。适用范围须查询实际 `list_workflows`、`resolve_operations` 和 `list_registered_workflows` 及可引用证据，不从旧样本说明推断当前能力；周期桨判断见 [starccm-propeller](../starccm-propeller/SKILL.md)。缺操作时用 `search_knowledge` 查证来源，编写小型宏并用 `validate_macro` 编译；返回 `trial_state=NOT_RUN` 时不得将它加入可执行流程。

完整计划通过 `validate_plan`（把**要提交的那份计划**作为 `plan_operations` 传进去：可执行性按它判定，绝不看各机制能力的并集）且所需操作均可执行后，使用 `submit_run` 创建持久流程：`steps` 显式给出阶段，或 `operations` 给出操作 id 由 `resolve_operations` 解析成真实阶段（`submit_run` 会拒绝未知操作、解析不到的操作，以及由受限试跑入口拥有的操作，并给出该用的入口）。`operations` 里也可以显式列出**前置/后置离线步骤**（`classify_parts` 为前置、`export_evidence`/`report` 为后置）：前置步骤在提交前真的执行（没有可信审计就拒绝），后置步骤由 `report_run` 在流程结束后真的执行并在运行目录写 `report.state.json`；只声明不调用不算完成，`applies_to` 声明本身更不算。阶段参数只接受该阶段**自己声明**的名字（`parameters`），进程关键变量（PATH/TEMP/…）一律拒绝；参数会进 `request_digest`，改参数就是另一个请求。**审查记录必须同时钉住两层 runner 各自的哈希**：`pipeline_runner_sha256`（`run_pipeline.ps1` 对自己的 `$PSCommandPath` 校验）与 `runner_sha256`（`run_job.ps1` 对自己的校验）；只钉一个会被拒，也不允许改用未经审查的即时哈希。给了 CaseSpec 的流程仍必须过完整计划门，省略 CaseSpec 只能用于显式诊断流程。用 `get_run` 查询（它同时给出提交时那份计划、以及后置步骤是否真的跑过）、`cancel_run` 停止、`resume_run` 从验证过的检查点恢复。**正式路径的报告入口是 `report_run(run_id, evidence)`**：它取回产物、把每个文件与 Windows runner 记录的 sha256 重新比对（没有记录的哈希只能标 `NO_RECORDED_HASH`，绝不写成已验证），再调用既有报告器，输出**流程 / 工程 / 送达**三列结论；失败的运行同样出诚实报告。`fetch_run_results` 仍可用于只取证据。诊断流程可只运行已安装阶段，完成状态为 `EXECUTION_COMPLETED` 且 `engineering_verified=false`。网络不确定时用相同 `request_id` 和参数重试；不要生成新 ID 重复计算。`WAITING_AGENT` 表示需要诊断或补齐操作，不直接重试同一失败宏。

新增可复用操作时，在插件清单中声明输入、阶段、宏、完成标记、产物和后置检查。记录 STAR 版本、哈希、正例、反例和实际结果后再部署新运行时。现阶段 `validate_macro` 已有编译和静态风险提示；自动生成宏的受限执行环境与副本试跑尚未验收，因此编译通过不等于允许执行。新增操作时按同一套登记格式写 `operations/catalog.json`：`mechanism`、调用路径、`provides`、参数（含已验证值）、输入输出、适用范围、证据与四个等级；只写 id 不写调用路径会被 `resolve_operations` 判为 `UNRESOLVED`，也不会让任何计划变成可执行。

新输入登记使用实际目录可见的 `prepare_registered_workflow_candidate` → 独立逐项哈希审查 → `register_reviewed_workflow_candidate` → `submit_registered_workflow` 返回持久run_id → 查询/取回。候选清单自动绑定直接导入的 `run_registered_workflow.py`、`registered_workflow_mcp.py`，复用既有审查门，不靠每轮人工补两项。输入或代码改变须新版本批准，历史批准不得重签授权新字节。

既有运行的查询/取回通过原handle、登记回执绑定的批准快照验证身份与已执行字节；这与新提交要求当前执行字节批准是两种用途。旧记录仍可读不代表旧批准可启动新代码。桌面进程更新后须重连并以真实MCP工具验收，独立测试客户端成功不能替代桌面验收。能力等级、P/E及泛化状态以实际目录和run_record_gate证据为准。

稳定 gateway 模式以 `connection_health.business_runtime.execution_model=FRESH_PROCESS_PER_CALL` 识别。相同工具契约的业务源码更新后，由代理准备、low独审、发布新版本，下一调用自动载入，无须用户重连；勿把工作区未发布的源码当当前执行版本。入口或其发布管理器自身更新仍须重连；工具契约变化需刷新tools/list，不响应通知时再使用重连。真实桌面须核business_runtime.release_id与独立批准的活动版本一致、all_files_match_disk及gateway_matches_disk为true，连续调用入口PID保持、业务PID变化；原五模块loaded_matches_disk单独为true不能证明新入口已加载。支持版本审查不能代替CFD请求审批，旧Windows任务不因发布而重提。具体发布步骤见项目 `docs/mcp-runtime-updates.md`。

静止外流交付候选使用 `prepare_static_external_flow(source_sim, case_spec, workflow_id)`：用户只提供源与工况，代理整理显式CaseSpec，工具内部查找同源哈希的可信审计、生成来源合同并准备基础链及网格/计算域各档候选。未知源返回 `NEEDS_TRUSTED_SOURCE_AUDIT`，由代理调用既有登记和经独审的审计；不得用旧源合同代替新源审计。支持范围为当前单实体球体/保留STEP CAD的+Z圆柱、m、+X静止空气外流（mm历史几何审计保留，当前完整导出宏要求SI metre CaseSpec，入口返回UNSUPPORTED_FULL_EXPORT_UNIT），来源与角色必须有可信证据。无法可靠推断的工况一次集中澄清；不向用户索要证据路径或运行器命令。

对这条所选路径调用 `validate_plan(case_spec, workflow_id=候选ID)`。它逐档检查实际CaseSpec、pipeline、宏、提交工具和唯一request_id；省略研究不算完整。`call_plan_executable`仅表示调用实现和参数齐备，`submission_ready`另要求当前逐次独审、登记回执、请求额度和整份计划预算；两者均不代表实跑。依照返回的base_call及studies逐次走原审查/登记/submit_registered_workflow，每次提交后用原持久查询确认终态并取回，再推进下一档，同机一个流程。不要调用研究CLI的阻塞批量提交来绕开该路径。无计算授权的技术批准保持 `registered_request_ids=[]`。

研究取回后调用 `assemble_registered_workflow_studies(workflow_id, run_id)`，复用研究汇总和中文完成报告。缺档、未收敛、GCI适用性无证据均如实保留。现有受限路径的终态断点取回与持久查询可复用；求解检查点重启恢复须单独验收，不能因正式路径有resume_run就宣称该受限路径也通过。版本加载以真实桌面 `connection_health.desktop_runtime.loaded_matches_disk`及新增工具调用为准。

授权任务内可按工程需要追加额度，执行前登记，按实际发票逐次且只扣一次；保留历史账和批准。预算增加不单独等待确认。旧FAILED请求不得自动重试。2026-10-07工程修订只完成编译/离线检查：8核64GB，预检可用内存至少80GB；1020s求解进程帽包含120加载/设置、600迭代软预算、30整chunk余量、150导出/检查点保存、120退出策略预留，预留不是实测。首段全链1360s、有效检查点新请求1200s；当前旧域失败输出只可诊断。后续段必须绑定成功solve证书、确切迭代及原执行身份，取得当前字节独审。物性近壁60s入口、真实恢复及Windows重启仍未验收。
