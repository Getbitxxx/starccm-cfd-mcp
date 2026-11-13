# 阶段证据与验收

| 阶段 | 主要产物 | 解释 |
|---|---|---|
| inspect | case_inventory_licensed.txt、case_evidence.txt | 模型设置、报告值、监控样本数。反射探测中的不支持查询可标记 UNAVAILABLE，不等于模型损坏。 |
| prepare | baseline_prepared.sim、baseline_preflight.txt | 验证 120°、+Z、直径一致性等后保存模板；没有求解。 |
| smoke | baseline_after_smoke.sim、smoke_status.json、smoke_reports.csv、monitor_csv/ | 从已有场续算 100 步。SMOKE_COMPLETED 是执行状态。 |
| qa | residual_raw_csv/、residual_normalization.txt、mesh_quality_summary_*.csv、outer_boundary_audit.txt | 未归一化残差、网格和外边界诊断。 |

可将 smoke 和 qa 回传目录中的数据合并到单独分析目录，再运行插件 `scripts/evaluate_baseline.py <分析目录>`。它检查 100 个样本、公式一致性，以及暂定的载荷相对波动 <0.5%、质量不平衡 <0.1%。这些是初步筛选条件，不是通用 CFD 标准。

基准已知：V=30 m/s，n=10000/60 s^-1，D=0.4 m；两个流体域，57398 个单元。原文件有场值但初始监控历史为空，不能用“迭代数为0”推断它从未求解。

现有外侧约1 m半径圆柱面是 NO_SLIP / FIXED / ADIABATIC。若用户要自由空气，必须另外设计外边界和域尺寸，并验证新工况。自动化工具目前不会转换边界或自动生成无人机/螺旋桨新网格。

质量 CSV 的 unavailable_or_empty/NaN 是缺失值，不能当作0或质量通过。Cell Aspect Ratio、Least Squares Quality 是 STAR 对应指标，不能按别的软件同名指标阈值直接解释。

总推力/轴向力矩/功率只在周期假设成立时由扇区乘三得到。历史 Lift Drag Ratio 属于遗留报告，不能自动解释为孤立螺旋桨的整桨升阻比。
