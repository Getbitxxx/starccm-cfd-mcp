---
name: starccm-external-flow
description: 为 STAR-CCM+ 空气中的静止外部绕流或无人机机身外流组织计算域、边界、网格、求解和工程验证；结合通用 MCP 核对已实现操作并识别缺口。
---

# 外部绕流工作流

读取 [starccm-general](../starccm-general/SKILL.md) 的 CaseSpec 与执行规则。目标场景是开放空气；先核对模型长度单位、来流方向和速度、空气状态、机身与开口、要求的阻力/升力/压力结果。通过审计证据分类现有壁面和远场，记录任何修复对实际几何的影响。

可执行计划应依序包含几何诊断、必要的全周几何构造、外域构造、边界映射、物理连续体、局部与近壁网格、网格质量、试算、正式求解、守恒与载荷稳定性、三档网格和计算域敏感性、场图和报告。`external-domain-2502/build` 创建显式边界盒体；`build-cylinder` 根据已审计桨径设计的显式尺寸创建 Z 轴圆柱并扣除选定的 CAD 部件；`create-full-region` 从已审计圆柱布尔结果创建新 Region 和独立边界；`audit-cylinder-patches` 只读定位两个端面及侧面；`map-full-flow-outer-boundaries` 将它们映射为入口、压力出口和径向对称边界；`remove-legacy-sector-regions` 在已审计副本清理旧扇区区域及接口；`configure-normal-inlet-outlet` 根据显式 CaseSpec 为 Z 轴圆柱设置上游法向入口和下游压力出口数值。各阶段均不判定域距离是否充分。

静止外流的实际可执行范围须查询 `list_workflows`、`list_operations`、`resolve_operations`、`list_registered_workflows`，并核对 `progress.json` 与 `run_record_gate` 可引用证据。内建球体几何入口与已审计 STEP 圆柱入口是不同来源；不能把旧“仅支持sphere”描述用作所有登记输入的范围。圆柱新源/新CaseSpec已在②R25通过真实桌面候选→独审→登记→有限六阶段链并交付，证据见 `test-evidence/static-cylinder-step/r25/REPORT.md`；这只证明该受限输入和工况。没有代表性变化验收时不宣称任意形状通用。

新模型从真实几何-only源、独立几何证书与契约明确角色、SI单位、特征长度、参考面积、域与网格，不能复制旧样本身份。`prepare_registered_workflow_candidate` 生成待审候选和当前输入/宏/依赖哈希；候选不等于批准。既有依赖清单自动包含两直接导入的legacy适配器，新字节须独立新审查与登记，历史批准不重签。完整工程计划是否可执行以实际 `validate_plan` 为准，域/网格敏感性与分类调用绑定缺项须逐项报告，有限流程不取消工程要求。

历史 `get_registered_workflow_run` / `fetch_registered_workflow_results` 使用已有handle及其批准快照只读查询/取回，不授权新计算；新提交继续要求当前字节匹配的新批准。数值收敛、流程完成与工程验证分别报告，P/E及PARTIAL/sample_specific按证据更新。

边界角色、近壁分辨率或模型尺度含糊时先集中澄清。没有安装并验证的步骤时，报告缺口并用知识库/本地教程开发候选操作；不要把教程的固定部件名称或尺寸直接用于新模型。用官方教程文件当输入前必须先只读审计它到底装了什么——实测 `anisotropic_OneraM6Wing_Start.sim` 与 `dualElementWing_start.sim` 都是 0 part / 0 region 的空壳，几何根本不在文件里。

仿真报告写明几何单位、外域尺寸、网格质量、壁面处理、收敛窗口和各档结果差异。`EXECUTION_COMPLETED` 不代表这些检查已经通过。

完整交付候选入口为 `prepare_static_external_flow` → `validate_plan(workflow_id=...)` → 各档逐次独审/登记/提交/查询/取回 → `assemble_registered_workflow_studies`。用户使用入口是“对这个已保存的源.sim完成静止空气外流，目标为……”，由代理生成工况澄清、CaseSpec、来源合同与完整调用计划，内部管理证据路径。研究使用当前源合同及当前pipeline；复用旧档必须经现有身份/参数/宏版本/引用门，数值不收敛的基线即使同身份也不得算有效工程档。三档网格与三档域都列出，离线准备不记为实跑或泛化验收。当前合并验收及最小实跑清单见仓库 `test-evidence/static-release-candidate/ACCEPTANCE.md`。

当前完整导出只支持 `model_length_unit=m`；可信mm几何证据不等于完整导出兼容，当前宏要求SI metre，入口须返回 `UNSUPPORTED_FULL_EXPORT_UNIT`。受限路径仅start/poll，终态取回不能宣称求解或Windows重启恢复。

授权任务内可按工程需要追加额度，执行前登记，按实际发票逐次且只扣一次；保留历史账和批准。预算增加不单独等待确认。旧FAILED请求不得自动重试。2026-10-07工程修订只完成编译/离线检查：8核64GB，预检可用内存至少80GB；1020s求解进程帽包含120加载/设置、600迭代软预算、30整chunk余量、150导出/检查点保存、120退出策略预留，预留不是实测。首段全链1360s、有效检查点新请求1200s；当前旧域失败输出只可诊断。后续段必须绑定成功solve证书、确切迭代及原执行身份，取得当前字节独审。物性近壁60s入口、真实恢复及Windows重启仍未验收。
