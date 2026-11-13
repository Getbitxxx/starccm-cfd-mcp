# 网格工具政策：HyperMesh 内完成划定、检查与修复

> 本文件只写规则和它的可执行判据。当前状态、计数器和最新覆盖一律在 `progress.json` 与
> `test-evidence/`（`AGENTS.md` §2-O6）。规则来源：用户 2026-10-14 明确要求——"HyperMesh 里有
> 网格检查和坏网格定位，网格划定和检查修改都尽量在 HyperMesh 里完成"；以及 `AGENTS.md` §8。

## 1. 工具顺序（谁先做网格）

1. **几何简单、边界与区域可明确映射、STAR-CCM+ 已验证的自动网格流程能满足质量门槛时**，
   继续用 STAR-CCM+ 原生网格，保留 `.sim` 里的参数与可追溯操作。
2. **几何修复、局部尺寸场、复杂过渡，或 STAR 网格反复在同一位置失败时**，先用 **HyperMesh**
   在隔离副本上做：几何诊断 → 划定网格 → 原生检查 → 坏单元定位 → 在 HyperMesh 内定点修复。
   当前问题模型的替代外部网格路线以 HyperMesh 为准，不得因为已有 Gmsh 脚本方便而跳过它。
3. **Gmsh 只是限定后备路线**，并且只有满足 §5 的书面缺口条件才可启用；每次使用单独登记
   输入、方法、资源预算与实际结果，不把"换了工具"当作质量通过。

**2026-10-24 状态：** HyperMesh 路线当前挂起（无可用执行/批准）。挂起期间，**STAR 原生网格是主路线**；Gmsh 后备的 §5 书面缺口可以引用“HM 路线挂起、无法执行”这一事实，但仍须保存实际失败与坏单元定位（若有失败）。HM 恢复后本条失效，仍按 §1 的顺序。

## 2. 检查必须定位，而不只是计数

HyperMesh 能给出坏单元的位置，所以记录里必须带上它：

- 官方 2023 文档（本仓库已下载实际字节）：
  [`*elementtestjacobian`](https://2023.help.altair.com/2023/hwdesktop/hwd/topics/reference/hm/_elementtestjacobian.htm)、
  [`*elementtestskew`](https://2023.help.altair.com/2023/hwdesktop/hwd/topics/reference/hm/_elementtestskew.htm)
  写明"puts those that fail on the specified **outputmark**"；
  [`hm_getelemcheckvalues`](https://2023.help.altair.com/2023/hwdesktop/hwd/topics/reference/hm/hm_getelemcheckvalues.htm)
  "Returns the check value for elements from within a selection"。
- 实际执行证据（同一安装的 `info commands` 探针与真实作业）：
  `*elementtestaspect` / `*elementtestskew` / `*elementtestjacobian` 可用，
  `*elementtest` / `*elementtestvolumeskew` 在该安装不可用；
  真实质量检查 Tcl 已用 `*createmark elements 2` + `*elementtest…` + `hm_marklength elems 2` 统计失败数。
  记录位置：`test-evidence/finite-trailing-edge-policy-20261014/hypermesh-native2d-preparation/`、
  `hypermesh-surface-probe/_gen_naca-finite-te-surface-20261014a.tcl`。

**因此，任何一项检查只要出现失败，记录必须同时保存**：失败单元 ID（output mark 上的实际成员）、
逐单元检查值、单元坐标（或节点坐标）、检查命令与短标签、准则阈值与方向、单位、
被检查单元总数、失败数与最坏值、网格与输入源 SHA-256、工具版本。
只保存"失败数 + 最坏值"的记录**不合格**：它答不出"该在 HyperMesh 里修哪几个单元"。

**失败 ID 必须能证明属于这份网格。** 记录要声明 `mesh.element_id_bounds = [lo, hi]`（实际单元 ID 的最小/最大值，且 `hi-lo+1 >= element_count`）；失败单元 ID 落在范围外即视为**定位不成立**。没有该范围时记录仍可判 `FAILED_CRITERIA_WITH_LOCALIZATION`，但**不得**被引用为切换工具的能力缺口（独立审查 C-6：否则填 999999 也算定位完整）。

**两套维度编码不同名。** `*elementtest*` 用 2/4，`hm_getelemcheckvalues` 用 1/2/3。网格声明了 `dimension_counts`（同时含 2D 与 3D 时）时，**每条准则都必须显式写整数 `dimension`（2 或 3）**，且每个声明维度都要被覆盖；单维度网格由 `mesh.dimension` 表明维度，不强制重复声明（独立审查 C-5；R2-6：文档口径必须与实现一致，不得比实现严）。

### 2.1 定位数据怎么产出（形状说明，**未执行、未审查、不是可直接运行的批准**）

本项目已实际跑通的形状（源：`hypermesh-surface-probe/_gen_naca-finite-te-surface-20261014a.tcl`
与 `hypermesh-native2d-surface-repair2-preparation/` 的检查消费者）是：

```tcl
# 1) 待查单元放 mark 1（该脚本用自己的 elements_2d/elements_3d 取集合）
# 2) 清空 output mark 2，跑原生检查：不合格单元会落到 mark 2
*createmark elements 2
*elementtestjacobian elements 1 <threshold> 2 <...> "2d_jacobian"
set nfail [hm_marklength elems 2]
# 3) 定位：失败单元 ID + 逐单元检查值
set ids   [hm_getmark elems 2]
set pairs [hm_getelemcheckvalues 2 2 "2d_jacobian"]   # 返回 id/value 成对列表
```

**本小节有两处用法尚未被真实回读核验**（独立审查 C-4）：① `check_type` 传字符串标签（官方页面只列 jacobian/skew/aspect）；② `hm_getmark elems 2` 取失败单元 ID。坐标需要单元的节点信息：具体语法**必须**先按安装版官方文档核验
（`hm_getvalue` / 节点查询页），本文件不把未核验的语法写成可用能力；
核验后按 §3 的记录字段落盘。参数表（阈值、config、标志位）一律照抄已审查的实际脚本，
不凭记忆猜。已有的真实检查脚本只保存了"失败数 + 最坏值"（`qc` 过程丢弃了 ID），
所以**本轮没有把任何一条 HyperMesh 记录升级为含定位的记录**——这正是下一步要做的事。

## 3. 唯一准入判据

准入判定只有一处实现：`scripts/hypermesh_quality_gate.py`（`evaluate()` / `assert_admissible()`）。
消费方（STAR 导入前的准备器、报告器、审查记录）不得各自重写门槛比较。它给出四个结论：

| 结论 | 含义 |
|---|---|
| `RECORD_INCOMPLETE` | 记录缺字段、范围未知、或全过却没有逐单元检查值：不算通过 |
| `LOCALIZATION_MISSING` | 有失败但缺失败单元定位：既不准入，**也不得读成能力缺口** |
| `FAILED_CRITERIA_WITH_LOCALIZATION` | 检查完整、失败已定位：网格 FAIL，但足以在 HyperMesh 内定点修复 |
| `ADMITTED` | 全部准则通过且定位完整：仅批准进入下一步**独立**验收。声明了 `dimension_counts` 的网格必须**每个维度**都被至少一条准则覆盖（独立审查 D-1：只查一部分属于未知范围，不是通过） |

`quality_admitted` 只覆盖"HyperMesh 原生检查这一关"；该模块的 `star_admitted` /
`cfd_admitted` / `engineering_verified` **恒为 False**——两个工具的质量指标互不替代（`AGENTS.md` §8）。
表面检查只能批准对应的表面交换，不能批准体网格或近壁层。

## 4. 修复在 HyperMesh 内定点完成

- 用户 2026-10-15 重申：主动使用 HyperMesh 的坏单元定位功能，并善用其网格划分能力。当前问题先在 HyperMesh 将失败单元 ID、逐项值和坐标关联到实际 CAD/壁面，再选择局部尺寸、过渡或近壁控制；修复后核对实际网格响应及加宽后缘是否保留。接口或脚本未生效的诊断与软件网格能力分开记录，后备工具仍按 §5 准入。
- 用 `repair_targets` 给出的失败单元 ID 与坐标建立 mark，在该处调整局部尺寸场、几何或网格，
  而不是整片重划或靠无限追加局部网格绕过规则（`AGENTS.md` §7 有限尾缘条）。
- 每次修复单独登记：输入/输出 SHA、改动内容、修复前后同一准则的失败数与定位、实际耗时与线程、
  原生机时与编排时间分开记账。修复计数器（每路线、每阶段）只增不减，不得重置。
- 修复后必须重新跑**同一套**准则并重新定位；"设置改了"不等于"失败单元修好了"。

## 5. 允许切换到 Gmsh 的条件（书面缺口）

必须同时具备，且都记进 `test-evidence/`：

1. 一份**定位完整**（`FAILED_CRITERIA_WITH_LOCALIZATION`）的 HyperMesh 质量记录，说明失败在哪、
   有多少；**`LOCALIZATION_MISSING` 不满足本条**——它连修哪儿都没答出来。
2. 在 HyperMesh 内已按 §4 做过有预算的定点修复尝试，或已记录一条具体的能力缺口
   （命令/探针/官方资料的实际依据，不得用"不好用"当理由）。
3. 该后备路线单独登记输入、单位、方法、资源预算、实际结果与失败；不继承旧批准、目录、阈值或计数。

## 6. 与 STAR 的边界（不可替代）

即使 `ADMITTED`，导入 STAR 后仍须**独立**核几何、单位、part/region/boundary 标签与成员、
接口连通、近壁层实际层数与首层高度，以及 STAR 原生质量门（本项目原门：CQ ≥ 1e-5、FV = 1、
VC ≥ 0.01，除非 `CaseSpec` 事前另有声明）。HyperMesh 的报告不能替代这些验收，
也不能降低 `CaseSpec` 门槛。真实网格的逐单元检查、坐标与最坏位置才是判断"坏网格在哪里"的依据，
容器或编排层的日志不能代替它。
