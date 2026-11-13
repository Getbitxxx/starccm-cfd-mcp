# Windows 本地 STAR-CCM+ 2502 教程索引

以下文件位于 Windows `F:\starccm\Siemens.STAR-CCM+2502.0_20.02.007_VerificationSuite_&_Tutorials-SSQ\tutorials`。本机运行版为 20.02.008；教程宏属于 20.02.007，相关 API 仍需在 20.02.008 的隔离副本上验证。这里只记录已经读到的代码，不声称教程能直接应用于现有螺旋桨 `.sim`。

| 文件（相对于 tutorials） | 已核对的做法 | 对自动仿真的作用与边界 |
|---|---|---|
| `motion\MovingReferenceFramesRotatingFan.java` | 153–169 行建立 `UserRotatingReferenceFrame`，设置 rpm 并赋给旋转 Region；109 行执行体网格，421 行求解 | 稳态 MRF 的 API 范例。教程是旋转风扇，不能直接推断为开放空间无人机桨域；须另验旋转轴、域间接口和边界。 |
| `motion\RigidBodyMotionRotatingFan.java` | 101–128 行关闭 `SteadyModel`、启用 `ImplicitUnsteadyModel`、建立 `RotatingMotion` 并赋给旋转 Region | 瞬态旋转的 API 范例。需重新设定时间步、网格运动接口和周期采样；不能当作 100 步稳态续算的等价替代。 |
| `motion\BladeElementMethodHelicopterRotorFuselageInteraction.java` | 316 行启用 `VirtualDiskModel`，332–335 行取得并配置 `VirtualDisk`；教程说明以叶素/虚拟盘表示旋翼，无需解析桨叶网格 | 整机机身与旋翼相互作用的低成本备选模型。需要翼型/叶素输入与校准，不能当作已解析桨叶 CFD 的等价结果。 |
| `motion\MovingReferenceFramesMarinePropellerInOpenWater.java` | 183 行建立表面/Trimmed/棱柱层网格操作，701 行由部件创建 Region，706 行生成体网格，872–895 行建立力和力矩报告 | 展示桨域网格、旋转与载荷报告的完整构造。它是船舶开水域案例，流体与尺度不同，数值和边界不能照搬到空气螺旋桨。 |
| `mesh\PartsBasedMeshingExternalAerodynamics1.java` | 348 行建立 Surface Wrapper；597–614 行减去部件形成流体域；777 行创建 Region；825–843 行建立含表面修复、Trimmed、棱柱层的自动网格 | 整机外流场几何与网格流水线的参考。示例物体是机翼，使用前必须确认无人机 CAD 的封闭性、部件标签、缝隙和流体域。 |
| `mesh\GapClosureInSurfaceWrapperDroneMainBody.java` | 146–162 行从机身 Upper/Lower 部件建立 Surface Wrapper 并开启 gap closure；167–169 行设置基准尺寸 | 无人机机身缝隙处理参考。自动封缝可能改变有效通气孔，必须检查几何和封缝结果。 |
| `geometry\AdvancedSurfaceFreeformUnmannedAerialVehicle.java` | 168–188 行使用 3D-CAD 自由曲面特征修改特定面与点 | 无人机几何编辑范例。该操作依赖具体面索引，不能对任意导入 CAD 自动复用。 |
| `automation\IntermediateJavaMacroRunMultipleSimulations1.java` | 274 行建立网格操作，481 行生成体网格，616–641 行建立力系数报告/监控，1090 行运行 | 多阶段宏和监控构建参考；案例工况与当前螺旋桨不同。 |

教程中 `.sim` 的“最终状态”文件可用于隔离验证 API 与几何类型。它们是示范案例，不能充当用户 UAV 的物理验证数据。

对同盘的 `VerificationSuite` 指南标题和 `VerificationData` 目录做了只读检索，未发现明确针对空气螺旋桨或无人机旋翼推力/功率的验证案例。`rae2822` 提供翼型升阻力等数值回归检查范例，例如 `VerificationData\rae2822\user\rae2822.CoupledImplicitMUSCLRkeTest\testLiftCoefficient` 下的 `.json` 与 `.msg`，但其 1.50% 的报告容差仅属于该翼型案例，不能移植成当前螺旋桨验收阈值。

## 下一版工作顺序

1. **几何与物理前置审计**：识别导入部件、封闭性、旋转轴/中心、桨叶数、尺度、现有边界和接口；生成待确认的物理输入表。全机与周期扇区在此分流。验收：在复制 `.sim` 上输出可机器读取的诊断，缺关键输入时拒绝建模。
2. **整机外流场网格模板**：基于外流场与无人机 gap-closure 教程建立可配置流水线，输出封缝差异、边界标签、网格质量及尺度敏感性。验收：在一个明确的 UAV 几何上重复生成、边界分类可审计、网格无负体积并完成至少两档网格比较。
3. **桨域与整机耦合模板**：先完成稳态 MRF，另建瞬态运动模式。验收：接口/轴向/转速检查、转矩与推力符号检查、质量守恒、载荷周期或稳态窗口、时间步/网格独立性门槛。不可用周期扇区结果直接代替整机干扰结果。

当前 v0.1 仍只执行已有 120° 周期桨模板。上面三项是后续开发与验收清单，并非已提供的工具能力。
