# 通用 `.sim` 只读预检查（core 工作流）

`submit_job(stage="preflight", case_ref=..., request_id=...)` 在 Windows 的算例副本中运行 `AuditAutomationPreflight.java`，写出 `automation_preflight.json`。它适用于**已建立的 STAR-CCM+ `.sim`**，不导入 CAD、不创建流体域、不修改边界、不运行求解。Mac 上用 `fetch_results(job_id)` 下载 JSON。

JSON 包含区域、边界类型的 Java 类名、物理模型、`geometryParts`（真正的 CAD/几何部件及面名称）、`derivedParts`（显示与后处理部件）、接口与报告，以及 warnings。不要用 `derivedParts` 的数量判断 CAD 是否存在。外边界识别看 `boundaryTypeClassSimpleName`，不要靠显示名称：它会随语言变化。例如 `PressureBoundary` 是压力出口，`WallBoundary` 是实体壁面。`InternalBoundary` 出现在接口邻接处，不能当作外边界。

`status="ok"` 表示宏列举完成且无字段警告；`partial` 需要逐条检查 `warnings`，不能自动当成已通过物理前置审计；`error` 使作业失败。`AUTOMATION_PREFLIGHT_COMPLETE` 只表示宏退出，worker 还要验 JSON 状态和必需字段。`sessionModified` 可能因加载旧版本 `.sim` 而为 true，不能单靠它判断宏是否修改了文件；检查输入副本的 SHA-256 与提交时一致。

最终版已在当前 20.02.008 上用三种 `.sim` 副本实测：原螺旋桨得到 2 个 Region、16 个 Boundary、2 个 CAD Part、3 个 Interface；从 `fluid.x_t` 和 `propeller.x_t` 新导入的几何算例也得到 2 个 CAD Part（共 10 个 PartSurface）；Siemens `motion\fan_start.sim` 得到 2 个 Region、44 个 Boundary、5 个 Interface。三者 `status=ok`，原始输入 SHA-256 与任务副本一致。这些数量是对象清单，不代表它们的边界或物理设置适合自由空气。

进入下一阶段前仍要核对：真实几何的尺度与封闭性、桨叶数和旋转轴、外流/风洞意图、来流及转速、部件表面标签、网格尺度和质量目标。无法从现有 `.sim` 清单自动推断用户想模拟的工况。
