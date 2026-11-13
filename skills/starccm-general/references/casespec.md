# CaseSpec v1

`validate_plan(case_spec, inspect_job_id, case, plan_operations)` 在可信预检查结果上核对工况，并把每个必需操作**解析到真实调用路径**后判断可用性；`case` 是算例身份，用来绑定「本算例已验证」这一等级（不给就只有 `verified_on_current_case=false` 与原因，不会默认通过）。`plan_operations` 是**真正要提交的那份计划**（一串操作 id）：给了它，可执行性就按这份计划判定；不给，只有「某一个操作单独覆盖全部要求」才算可执行，而多阶段正式流程永远要靠显式计划。首版场景为 `external_static`、`propeller_mrf`、`rotating_transient`，介质为 `air`。输入 `.sim` 必须是 `register_case` 返回的 `case_ref`。

核心字段：`schema_version: 1`、`scenario`、`case_ref`、`objective`、`model_length_unit`（m/mm/cm/in）、`medium: air`、`geometry.body_parts`、`flow.velocity_m_s` 三分量、`flow.pressure_pa`、`flow.temperature_k`。旋转场景另需 `geometry.rotor_parts`、`rotation.axis`、`rotation.center_m`、`rotation.rpm`。几何部件名称必须在审计清单中真实存在。

`rotating_transient` 还必须提供 `transient.time_step_s`、`transient.physical_duration_s`、`transient.steps_per_revolution`、`transient.stability_window_revolutions` 和非空 `transient.target_quantities`。时间步必须满足 `dt = 60 / (abs(rpm) × steps_per_revolution)`；持续时间和稳定窗口均为正值。目标量应明确列出需要监控周期稳定性的载荷或流场量，例如 `thrust`、`torque`。这些字段只保证瞬态计划的物理时间定义完整，不代表瞬态求解、收敛或时间步敏感性操作已经安装。

执行 `external-domain-2502/build` 时还需要 `domain.outer_bounds_m: [xmin, ymin, zmin, xmax, ymax, zmax]`。这是实验室坐标系下的 SI 边界；宏按 `model_length_unit` 转换后建立长方体，并扣除 `geometry.body_parts` 与 `geometry.rotor_parts` 的去重集合。每个被选部件的实测边界必须完全落在外域范围内。六个坐标必须有限，且每轴都有正长度。盒体阶段只使用显式给出的范围，不自行推断尺寸；入口、出口与侧界距离需另行评估。

`external-domain-2502/build-cylinder` 接受 `domain.shape: cylinder`、`domain.cylinder_center_xy_m: [x, y]`、`domain.cylinder_radius_m`、`domain.cylinder_z_min_m`、`domain.cylinder_z_max_m`，以及与圆柱轴对齐包围盒一致的 `domain.outer_bounds_m`。圆柱轴平行 Z，两个端面平行 XY。所选几何的实测包围盒必须保守地落在圆柱内。外域可以按已审计桨径和公开算例的尺寸比例设计；须保存比例、坐标方向和来源，并在正式工程验收中做计算域敏感性检查。此阶段只构造几何。

每个被选作机身或转子的部件还必须有审计得到的 `boundingBoxRaw`，格式为 `[xmin, ymin, zmin, xmax, ymax, zmax]`。当前数值来自 STAR-CCM+ 导出的二进制 STL，按原始坐标记录；在坐标单位与导入缩放核对完成前，不得直接将这些数值当作 CaseSpec 中以米表示的尺寸。

如果输入 `.sim` 已建立区域且含壁面，`geometry.existing_wall_roles` 应按 `区域名/边界名` 指定每个壁面为 `body_wall`、`farfield_to_replace` 或 `other_wall`。不确定的角色集中询问用户；旧外壁不得自动继承为自由空气边界。

默认工程验收要求三档网格、计算域敏感性，瞬态另做时间步敏感性。`limits` 的上限为 64 逻辑核、256 GB、48 小时。`validate_plan` 的 `valid` 表示输入齐全、无已发现冲突；`executable` 由**实际选定的执行计划**决定：可信审计 + 计划里每个操作都解析到真实调用路径 + 全部必需能力由这份计划（含它那一个运行机制真正会跑的前置/后置步骤）覆盖 + 计划只用一个执行机制。**绝不用各机制能力的并集当结论**——那是本轮修掉的假通过形状。返回值里要把这几项分开读：

- `operations_detail[op]`：`served_by`（哪些操作能满足它）、`mechanism`、`implementation`、`verified_on_current_case` 及原因、`general_acceptance`、`engineering`；
- `unavailable_operations` + `missing_reasons`：缺哪些操作，以及**为什么**缺——`reserved interface`（如 `mesh_study`/`domain_study`，窗口一实现中，契约见 catalog 的 `reserved_interfaces`）、`only SAMPLE-SPECIFIC implementations exist`（固定名能力不能顶替通用要求）、或 `no implementation is registered`（如 `time_step_study`，不设占位条目）；
- `mechanism_coverage`：每种运行机制（`workflow_stage` / `restricted_trial`）**端到端**能覆盖到哪一步、还差什么，外加 `missing` 与 `complete_plan`（哪一种单一执行路径真的覆盖了全部要求，没有就是 `null`）；没有任何单一机制能覆盖时如实列出。**最终结论看这里，不看并集**；
- `plan_operations` / `plan_mechanism` / `plan_blocker`：这次判的是哪份计划、它用哪个执行机制、以及**具体是什么**让它不可执行（缺哪些能力 / 计划里有没有解析不到的操作 / 一份计划混了两个执行机制 / 没有可信审计）；
- `unresolved_operations`：登记了但解析不到调用路径的条目（这属于配置缺陷，会让计划不可执行）。

**离线工具要靠被调用才算数（R12）。** 分类、证据导出、报告都是 `offline_tool`：它们只有通过 `invoked_by`（具名文件 + 具名函数，由解析器在盘上核对）绑定到某个运行机制、且带 `plan_role`（`pre`/`post`）时才计入该机制的计划覆盖；`applies_to_run_mechanisms` 只是展示字段，**声明不算调用**。实例：`report` 现在同时绑到正式路径（`scripts/formal_run_report.py::report_run`）和受限试跑入口；而 `classify_parts` 尽管声明了 `restricted_trial`，受限入口里**没有任何代码调用它**，所以在那边它现在是诚实的缺项。`submit_run` 的 `operations` 可以显式列出这些前置/后置步骤：`pre` 步骤会在提交前**真的执行**（没有可信审计就无法执行，计划被拒），`post` 步骤由 `report_run` 在流程结束后真的执行并在运行目录留下 `report.state.json`。

场景① 的六个阶段（`audit-body`/`region-physics-mesh`/`mesh-quality-audit`/`solve`/`validation` 加 `build-body`）已按**同一条已实跑宏链**登记进 `static-external-flow-2502`，`external-domain-2502/build` 提供外域；正式路径的 report 步骤也已接通（`mcp report_run`）。因此 ① 的完整通用计划目前**只差**两项保留接口的研究。**登记不等于跑过**：这些阶段声明与受限试跑流水线等价，但它们本身还没有审查记录。
