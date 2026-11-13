# MCP 业务更新与首次切换

`scripts/server_gateway.py` 保持 stdio 连接，每次工具调用由独立的 `mcp_business_worker.py` 执行固定发布版本。业务数据、原审批、注册回执与任务记录仍位于原项目目录。

| 更新内容 | 处理方式 |
| --- | --- |
| 业务 Python 代码，工具契约相同 | 准备、独审并发布新版本；下一次调用使用新版本，连接保持 |
| 调用期间发布新版本 | 已派发调用及其 Python 子脚本继续用原版本；后续调用用新版本 |
| 工具名称、参数、默认值、说明或返回契约改变 | 发 `notifications/tools/list_changed`；客户端重新 `tools/list` 后调用，否则拒绝变更工具。Codex 自动刷新行为须实际验证 |
| 稳定入口或入口使用的发布管理器自身改变 | 重连入口进程，再验真实桌面健康信息 |
| Windows 已提交的 STAR 作业 | 原作业继续使用已下发的 runner、宏和输入；发布不重新提交作业 |

## 代理的发布步骤

使用桌面配置中的 Python 3.12/MCP 虚拟环境，在项目根目录准备版本：

```sh
/Users/example/Documents/Codex/2026-09-20/new-chat/work/mcp-venv/bin/python scripts/mcp_runtime_release.py --prepare-only
```

准备器冻结 `scripts/`、`cases/`、`cad/` 下的 Python 源码，生成内容寻址目录和工具契约。导入、源码读取、代码哈希和同步 Python 子脚本使用同一快照；保留原 `__file__`，兼容既有业务数据路径。未审查的工作区源码不会自动用于新操作。

独立 low 子代理审查代码、正反例及最终 manifest。精确批准放到该发布目录的 `release-review.json`，至少包含 `approved=true`、`independent=true`、实际 `release_id`、`manifest_sha256` 和完整 `runtime_hashes`。后者是全部 Python 相对路径到实际 SHA256 的映射。入口、业务进程及嵌套 CLI 逐项核验；缺失、篡改或源码混版时拒绝。支持版本批准不代替原 CFD 请求审批。

核对批准与实际文件后激活：

```sh
/Users/example/Documents/Codex/2026-09-20/new-chat/work/mcp-venv/bin/python scripts/mcp_runtime_release.py --activate <实际发布ID>
```

`current.json` 原子切换，旧目录和批准保留。回退也须使用已批准版本；变更性工具要求工作区对应源码与发布一致，入口字节改变时还须重连。

## 验收

独立客户端可做真实业务只读检查，但不代表桌面已经切换：

```sh
/Users/example/Documents/Codex/2026-09-20/new-chat/work/mcp-venv/bin/python tests/mcp_runtime_smoke.py --run-id <既有任务ID>
```

最终通过 Codex 真实 `connection_health` 核对：

- `business_runtime.execution_model == FRESH_PROCESS_PER_CALL`。
- `release_id` 与批准的活动版本一致。
- `all_files_match_disk`、`gateway_matches_disk`、旧 `desktop_runtime.loaded_matches_disk` 均为 true。
- 连续调用入口 PID 保持，业务 PID 改变；`_meta.starccm_runtime` 与响应中的版本一致。
- 旧任务可查询，原审批与哈希门生效。

旧入口五模块一致不能替代新架构验收。首次切换仍须重连一次；后续相同工具契约的业务版本发布无需因 Python 导入缓存重连。

Phase A 诊断说明统一由 `mcp_runtime_release.ERROR_ACTIONS` 生成，见 [MCP 用户指南](mcp-user-guide.md)。更新 gateway 或本发布管理器时，旧入口会返回 `GATEWAY_RECONNECT_REQUIRED`；必须先激活经过独审的新版本，再重连，真实 health 需核对新的 gateway PID 和完整发布 SHA。独立 CLI 成功不替代桌面连接验收。

## 2026-11-02 发布：两个只读 CaseSpec 工具（31 → 33）

已激活 `50b833d4aa210839832c42eceba2da8407662edfe2585d7938398a4d17599fd3`（manifest `3921727a…c6a8`，121 个文件），
独审记录在该目录的 `release-review.json`（`approved/independent` 均为真，`runtime_hashes` 与 `files` 全等）。

- 新工具：`diff_case_specs`（两个 CaseSpec 逐键对比，分出 declared / undeclared / metadata）、
  `check_mesh_recipe`（棱柱配方算术自检）。两者 `readOnlyHint=true`，不发 STAR、不写盘。
- **客户端必须重新 `tools/list`**：工具数已变（`TOOL_CONTRACT_CHANGED` 会拒绝旧清单），重连 `starccm-cfd` 后再调用。
- 相对上一版 `0aa2bc57…` 的增量为 34 新增 / 3 变更（`casespec.py`、`quality_contract.py`、`server_v2.py`），
  本次审查覆盖全部 121 条哈希；其中 31 个脚本**不在 `server_v2` 导入闭包内**（惰性字节），日后进入闭包须先补审。

### `casespec.py` 的三处无条件收紧（只加拒绝、不加通过）

`box_face_roles`、`external_static` 实现名等价、`body_shape` 枚举与 arbitrary 六面界：旧写法会被拒。
对全仓 848 个 `case_spec.json` 的对照运行结果是"新增错误 33 条、丢失错误 0 条"，工具可达的 `cases/` 15 个 spec 结果不变。

### 已知结构性红灯（不由本发布引入，也不由本发布修复）

`tests.test_contract_amendment_c3_20261023` 的两项（`collection_runtime_digest_differs`：F2/mesh、F2/solve、
F3/mesh、F4/mesh）是"静态记录 × 部署指针"的确定性函数：12 个成员记录的 `runtime_digest` 是 `4d579f3c…`
（`formal-worker-deployment-20261021-hardening`），活动指针指向 `3f5aeec8…`
（`formal-worker-deployment-20261023-urans-statistics`，提交 `1089fbc` 于 2026-10-23T18:25:53+03:00 重钉），
而集合在 43 分钟前的旧指针下绑定。**禁止把指针倒钉回 `4d579f3c…` 来变绿**；该状态按 KNOWN STRUCTURAL 记录。
`docs/quality-contract-20261020.md` §13 里"runtime 观测值 12/12 与活动指针一致"因此已过期，需由文档所有者加注。

## 2026-11-02 第二次发布：门豁免（工具契约不变）

已激活 `849a27a52b5b79876d599ac8944f61b756ffac0cd0ed43bd46a243c541cbae82`（manifest `5bf8cb66…23fb04`）。
相对 `50b833d4…` **只改 `scripts/quality_contract.py`**，33 个工具契约逐字节未变，
因此**无需重连**（按本文件首表：工具契约相同的业务版本发布，下一次调用即生效）。

上一版 `9d579881…` 被独审 **REJECT**：`usable` 只判 `SIGNED` + 有 CaseSpec，不看 `errors`，
导致被判无效的豁免仍标为"可引用"并进入 `waived_gates`。本版修正为
`usable = SIGNED 且 交叉核对过 且 errors 为空 且 conditions 为空`，并补 `case_id` 比对、
`_number_or_none` 限有限值、`scope_limits` 类型收紧、`gate.case_spec_sha256`（若记录则比哈希）。

已知留白（独审条件，不改本发布）：`waivers_for_run` 每次全树 glob（实测 ~39 ms/次，在 `get_run` 热路径上）；
run 绑定仅靠 `mesh.run_id` 自述，未比对运行实际网格 sha；报告 Markdown 尚未自动插入豁免表。

## 2026-11-02 第三次发布：窄发布（发布集合 121 → 99）

已激活 `734f7a944076530dc8fbc86a19878707792d79fa10a1c270cde400ebd7571b39`（manifest `7c453253…53a1`）。
变更文件只有 `scripts/mcp_runtime_release.py`，另新增 `release-scope.json`；工具仍 33 个、契约未变。
发布集合由声明的作用域决定，被挂起的 22 个脚本不进 manifest 但在 `manifest.scope.held` 里逐个点名，
详见 [发布作用域](release-scope-20261102.md)。

**这一版改了 `GATEWAY_FILES`（`mcp_runtime_release.py`），所以已连接的客户端必须重连一次**：
在重连前，网关会对**所有**工具返回 `GATEWAY_RECONNECT_REQUIRED`（`server_gateway.py` 的入口字节检查不分读写）。
新建连接的进程不受影响（实测 33 工具、`all_files_match_disk=true`）。

## 2026-11-04 三次部署：清单驱动 → 156 文件 → 默认 1200

当天三次部署，运行时字节与业务发布分开推进。**入口全程未变**（`server_gateway.py` `08d36eea…`、
`mcp_runtime_release.py` `3187fba7…`），所以不需要为入口重连。

| 部署 | 运行时 | 安装名 | 改变了什么 |
| --- | --- | --- | --- |
| `20261104b` | 133 文件 / `3f5aeec8…` | `3f5aeec851407e71-5b4a8a4981552c84` | 无运行时字节变化：只为 `backend.py` 的「清单驱动」改动重签审批 |
| `20261104c` | 156 文件 / `307e48ae…` | `307e48ae88d99703-7719bb7997ad5930` | 把回滚时移出的 23 个实验宏装回运行时（23 条 100% rename，字节未改） |
| `20261104d` | 156 文件 / `e647d524…` | `e647d524c8020d22-aedb8b324228dc23` | `RunExternalFlowSolve.java` 默认迭代上限 600 → 1200；`operations/catalog.json` 同步 1200 并删掉 `EXTERNAL_FLOW_BUDGET_SECONDS` |

### 业务发布 `7a3cf1f4`

`release_id` `7a3cf1f40a76fa1270836df2d8c92340ddbfe8408bcf190e2db99ad69d89614f`，
manifest `b51d9574e65347a9ebf5460c1a7d5a962b1200590849757f9d4ae888f8c3dbd5`，99 文件，
独审记录在该目录的 `release-review.json`。

- `Backend.runtime_files()` 改为读 `runtime-manifest.txt`；新增包含性检查（条目必须 resolve 在仓库内且
  属于 `macros/` 直接子文件、`worker/`、`workflows/`、`scripts/resource_contract.py`），
  `../outside.java`、`macros/adhoc/*.java`、`/etc/passwd` 现在都被拒绝并有 6 条用例钉住。
- `formal_runtime_pins()` 清单优先；真实仓库缺清单直接 raise（只有非仓库 fixture root 走旧 glob）。
- `server_v2.py`：`get_run` / `get_registered_workflow_run` 的 `readOnlyHint` 保持 `false`。
- **客户端动作**：这两个工具的注解与说明变了，属于工具契约变更。WorkBuddy 侧已重新 `tools/list`；
  **Codex 需重启一次 `starccm-cfd` 会话（或新开一轮）让它重新拉取工具清单**，否则调用这两个工具会被
  `TOOL_CONTRACT_CHANGED` 拒绝。

### 1200 的证据链

- 默认值：`macros/RunExternalFlowSolve.java:74`
  `int maxIterations = (int) env("EXTERNAL_FLOW_MAX_ITERATIONS", 1200.0);`
- ad-hoc 证明（`campaigns/uav-2026/evidence/solve-default-1200-20261104/`）：步骤 `env` 为空；运行器在
  启动任何 STAR 之前断言 6 个 `EXTERNAL_FLOW_*` 在 Process/User/Machine 三作用域均为 null（非 null 即拒），
  读数写入 `run.json` 的 `env_audit`。结果 `iteration_cap=1200 / iterations_run=1200 / stop_reason=iteration_cap`。
- 正式路径复验（任务 `a7d3e95c-4f18-4b72-8c60-2e19f5b7d043`，`static-external-flow-2502 / solve`）：
  `SUCCEEDED`、660.71 s、64 核，同样的 1200/1200，`Cd=0.7579893894433737` 与 ad-hoc 逐位相同。
- **两者的边界**：正式复验未传 `parameters`，因此证明的是"宏默认 1200"；`operations/catalog.json`
  传 1200 那条路由 `resolve_operations` 单独实测（返回 `{CHUNK:50, MAX_ITERATIONS:1200}`）。

### 两个已知问题（本轮未修）

1. `submit_job` 不会为 `parameters.source='case_spec'` 的阶段 stage `case_spec.json`，缺它时 STAR 会先起来
   再在宏第一步失败（实测 28.86 s、exit 1）。应由提交侧 fail-closed 拒绝。修它要动 `backend.py`，
   而它在部署 `support_hashes` 里，需与下一次部署一起做。
2. 历史 v1 注册运行的读取需要每份 evidence 下的 `historical-read-review.json`；支持字节一变即全部失效。
   当前 30 个历史 handle 都缺这份记录，`get_registered_workflow_run(<旧 run id>)` 会返回
   `Historical read support bytes changed`。新任务不受影响。
