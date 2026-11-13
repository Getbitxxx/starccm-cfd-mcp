# 工作流包约定

MCP 核心只负责连接、内容校验、隔离作业、状态、取消和产物回传。`workflows/<id>/manifest.json` 描述一个经过审查的适用范围。`list_workflows` 把它暴露给模型；`submit_job` 只能运行打包清单中的阶段。运行时按内容哈希部署，已有作业仍指向提交时的版本。

## 清单字段

`schema_version` 当前为 1；`id` 与目录同名。`input` 描述输入类别，`validated_on` 记录已验证的 STAR 版本。每个 `stage` 必须给出 `description`、`macros`、`markers`、`required` 和 `postcheck`。宏文件名须位于插件 `macros/`，不接受路径。当前后置检查为：`artifacts_v1`（退出码、标记、必要产物），`preflight_v1`（另检查结构化清单字段），`smoke_100_v1`（另检查 100 步续算状态）。需要不同物理验收时，先在工作器中实现并验证新的检查器，再把名称加入清单允许列表。

## 一个新 CFD 包应提供的证据

1. **输入合同**：几何单位、坐标轴、计算工况、旋转方向、预期边界、材料和目标量；列明自动拒绝条件。
2. **几何与建域**：封闭性、重叠/间隙、流体域类型、远场或入口/出口距离、旋转域及接口。只对独立副本执行修改。
3. **网格**：局部加密、近壁层、质量门槛、网格规模和失败恢复；至少有可比较的网格敏感性方案。
4. **物理与数值**：连续体、湍流/可压缩性/运动模型、边界条件、初始化、时间步或迭代策略、资源预算。
5. **求解监控**：残差、守恒、力/力矩、关键物理量的历史；异常发散或停滞时停止并留下证据。
6. **验收与报告**：完成状态、稳定性、边界/计算域影响、网格敏感性、量纲和符号、适用范围；必要时与实验或可信基准比较。进程成功不等于物理验证。

导出的目标量 CSV 可用 `scripts/evaluate_convergence.py CASE.json EVIDENCE_DIR` 做独立监控筛查。CaseSpec JSON 提供 `scenario`、`target_histories`（目标名到 CSV 文件名的映射）及可选 `criteria`；稳态 CSV 首两列为迭代数和目标量，瞬态首两列为物理时间（秒）和目标量，并另外提供 `rpm` 与 `stability_window_revolutions`。可选的 `mass_flow.inlet/outlet` 检查流入/流出质量流量绝对值差。评估结果中的 `physical_validation` 固定为 false；该筛查不能替代 STAR 残差方程逐项评估、网格质量、敏感性研究或实验对比。

网格、计算域和时间步研究可用 `scripts/evaluate_sensitivity.py {mesh|domain|time_step} STUDY.csv` 比较目标量。CSV 每行是一档，首列分别使用单元数 `cells`、域尺寸比例 `domain_scale` 或物理时间步 `time_step_s`，其余列为载荷或其他目标量。至少需要三档网格、两档域尺寸和三档时间步；参数自动按粗到细/小域到大域排序。默认相邻档相对变化限值为 2%；接近零的目标量应通过 Python API 的 `absolute_scale_floors` 为该量提供有物理单位的绝对尺度。该结果是离散化敏感性比较，不等价于 GCI 外推或物理验证。

近壁验收使用 STAR `ExportMeshQuality` 五列 CSV，通过 `assess_near_wall_quality` 按 CaseSpec 明确的每个壁面最少实际棱柱层数和 y+ 范围逐项核对。棱柱层记录约定为 `section=prism_layer, region=<Region/Boundary>, metric=PrismLayerNumber, stat=min|max`；缺少行按证据不足拒收。候选宏 `candidates/ExportPrismLayerEvidence.java` 已用 STAR 2502 的 471 个 API JAR 编译，但还没有隔离试跑，所以未加入或执行于工作流。现有 `ExportMeshQuality` 导出壁面 y+ 的最小值、最大值和面积平均值，但不导出 `PrismLayerNumber`。y+ 范围取决于湍流与壁面处理模型，必须由 CaseSpec 显式提供。

新增包先在代表性正例和不匹配反例上测试，保留输入哈希、STAR 版本、宏日志和产物，然后更新技能中的选择规则。自由空气螺旋桨应是独立包，不能沿用当前固定外壁的周期桨结果作为自由空气性能结论。
