---
name: starccm-propeller
description: 使用远程 MCP 运行已审计的 STAR-CCM+ 2502 120° 周期螺旋桨基准算例，准备参数与监控、执行有限续算并解释证据。自由空气螺旋桨和整机无人机需独立工作流包。
---

# STAR-CCM+ 螺旋桨工作流

通过 `starccm-cfd` MCP 操作 Windows 上的独立算例副本。保持用户给定工况和当前授权；用户已授权的阶段直接执行，不为每次工具调用重复询问。

## 范围与物理约束

当前适配器经验证的输入是两区域 FLUID/PROPELLER、120° 周期、+Z 转轴、原点为零、稳态可实现 k-ε、理想气体、正来流与正转速的基准算例。`prepare` 会检查这些前提。用户已为新的开放空气桨算例选择完整 360° 几何；当前适配器仍只适用于旧周期基线，不能用它代替扇区扩整周和边界重建。不要把它自动应用到整机无人机、偏航来流或其他破坏周期对称性的场景。

当前样例外侧圆柱面是固定无滑移壁面。用户已选择开放空气作为新算例目标；新域和边界需在独立副本中建立与验证，当前模板仍是历史壁面模型。`SUCCEEDED` 只表示执行成功，不能代表开放流场适用性或仿真精度已验证。

用户主算例、源 CAD 优先级及开放空气建模合同见 [references/free-air-plan.md](references/free-air-plan.md)。Windows 本地教程的已核对 API 范例与下一版开发顺序见 [references/siemens-examples.md](references/siemens-examples.md)。教程不是对当前算例的验证。

## 工具工作流

1. 用 `connection_health` 确认连接和运行版本。工具不可用时说明需要加载已配置的 MCP，不用 GUI 点击替代受控后台流程。
2. 用 `upload_case` 上传用户指定的 `.sim`；保存返回的 `case_ref` 和 SHA-256。上传不会修改源文件。
3. 对已有 `.sim` 先用 `submit_job(workflow_id="core", stage="preflight", ...)` 导出结构化清单，读取 [references/preflight.md](references/preflight.md) 的字段和告警。需要本包详细文本审计时用 `workflow_id="propeller-periodic-2502", stage="inspect"`。阶段是异步作业，先记录 `job_id`，然后用 `get_job` 等待结果。
4. 输入匹配且用户授权准备模板时提交 `prepare`。下阶段的输入使用返回产物 `jobs/<job_id>/baseline_prepared.sim`，不要误用之前的原算例。
5. 用户授权试运行时提交 `smoke`。目前固定为从已有场最多续算 100 步，每批 10 步检查；不会重新初始化或划网格。
6. 对 `jobs/<smoke_job>/baseline_after_smoke.sim` 提交 `qa`，导出网格、残差和外边界证据。
7. 用 `fetch_results` 回传报告；只有需要交付 `.sim` 时才设置 `include_sim=true`。阶段产物与解释见 [references/evidence.md](references/evidence.md)。

`request_id` 是幂等键：8–64 位小写字母、数字和连字符。遇到网络超时先查询该 ID，并用同一组参数和同一 ID 重试提交。不要因为连接中断就换 ID 重复计算。同一 ID 配不同参数会被拒绝。

同一工作器一次只执行一个任务，`REJECTED_BUSY` 表示未获得执行槽。等待当前任务结束后，如仍需运行，使用新的 ID 明确重提该阶段。正常轮询约 5–15 秒一次，不需要重复展示无变化日志。

## 诊断与停止

阶段状态为 PENDING/RUNNING/SUCCEEDED/FAILED/CANCELLED/TIMED_OUT/REJECTED_BUSY。具体协议见 [references/jobs.md](references/jobs.md)。

- 工具返回宏错误、缺失完成标记或不满足几何/模型前提时，读取日志定位问题。不要通过删除验收条件来绕过失败。
- STAR 的退出码可能为 0，但宏发生错误；以 worker 完成标记和产物检查为准。
- 用户要求停止任务时调用 `cancel_job`，再检查终态。它只停止该任务的进程树，不操作用户已打开的 STAR GUI；尚未保存的迭代可能丢失。
- 网络不可达时保留 job_id。Windows 任务独立于 SSH/MCP 会话运行，恢复连接后先 `get_job`/`list_jobs`。
- 不调用任意 Java 宏、Shell 或更改其他算例来绕过当前工具范围。

## 结果解读

区分原始扇区载荷与 `CFD_Periodic*` 三重周期换算值。新增扇区力/力矩显式关闭理想化扩展；总量已乘三，不得再次乘三。横向力不采用该简单换算。

`n` 单位是转/秒，转速 rpm=60n，功率 P=Q×2πn。效率 TV/P 只用于当前正来流推进工况，不用于悬停。

原残差 AUTO 归一化在从已有解续算时可能误导，结合 `residual_raw_csv`、载荷稳定性和守恒数据解释。不同方程不能机械采用同一未归一化残差阈值。

报告中写明：是否从已有场续算、完成步数、载荷窗口稳定性、质量守恒、网格质量、外边界假设，以及尚未做的网格独立性/物理验证。没有这些证据时，不使用“高精度”“完全收敛”“已验证自由空气性能”等结论。
