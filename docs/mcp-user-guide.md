# MCP 结果使用与故障处理

每次 get_run、fetch_run_results、report_run 以及报告顶部固定输出 `quality_state`、`scope_statement`、`forbidden_uses`、`Q0_REASON`。Q0 有机器可读原因；Q1 仅作流程观察。执行、交付和工程三列独立。当前②/③有旧交付记录与输入/宏哈希；Q0 的 `UNBOUND_HISTORICAL_EVIDENCE` 指无法绑定合同要求的历史完整执行身份及 runtime/manifest，`NO_DELIVERY_RECORD` 指缺少合同要求的完整 runner 绑定交付证明，不表示目录中没有文件。新观察者绑定不能把当前 release/runtime 写成历史执行者。若该次运行被一份已登记的**门豁免**指名（`mesh.run_id` 相同），输出会额外带 `waivers` 与 `waived_gates`；豁免记录的是未通过项，**不改变** `quality_state` 与数值/工程两列，见 [门豁免契约](gate-waiver-contract-20261102.md)。`STAGE_FAILED` 只用于实际失败；缺记录或宏状态未知保持 unknown。

## 错误码与用户操作

以下表从 `scripts/mcp_runtime_release.py::ERROR_ACTIONS` 生成，由测试与 MCP 错误文本保持一致。未知错误有保守处理动作，所有错误均不自动重交 STAR。

| 错误码 | 用户操作 |
|---|---|
| REJECTED_BUSY | 已有任务占用单槽。用原 run_id 查询状态，等待空槽；不要重复提交或结束其他 STAR。 |
| GATEWAY_RECONNECT_REQUIRED | 活动入口字节已变。先确认已审查版本已激活，再在 MCP 设置中重连 starccm-cfd 并刷新工具列表。 |
| TOOL_TIMEOUT | 本地工具调用超时不证明远端任务已停止。用原 run_id 查询状态并取回证据；不要创建新 request_id 重交。 |
| RELEASE_REVIEW_REQUIRED | 业务版本缺少独立审查。由维护者准备版本并取得完整哈希独审后激活；不要绕过审查。 |
| RELEASE_REVIEW_MISMATCH | 业务版本与独审字节不符。由维护者核对完整版本清单并重新独审；不要手改旧批准。 |
| UNPUBLISHED_CODE | 工作树代码尚未发布。由维护者一次完成修改、测试、prepare、独审和 activate，再调用写操作。 |
| DEPLOYMENT_REVIEW_MISMATCH | Windows 运行时与部署独审不符。由维护者核对差异；取得新独立部署审查后才可部署和健康核验。不要修改 scope 或旧批准。 |
| TOOL_CONTRACT_CHANGED | 工具契约已变。刷新 tools/list；客户端未刷新时重连 starccm-cfd 后再查询，勿重交已有任务。 |
| RESULTS_QUOTA_EXCEEDED | 本地结果缓存达到配额或磁盘余量不足。先保全被证据引用的文件，人工清理未引用缓存或调整结果目录/配额，再取回结果。 |
| UNCLASSIFIED_ERROR | 保留完整错误与 run_id，交由维护者诊断。不要自动重试 STAR 提交。 |

## 本地结果目录、保留与配额

`connection.json.local_results` 是下载缓存根目录；当前为 `test-evidence/star-downloads`。已有下载按 backend 的 `runs/<run_id>/stages/<operation>/` 布局保存；质量报告默认在 `<local_results>/quality-reports/<run_id>/<snapshot_sha>/`。只读原始 JSON 与远端 SHA 快照另存 `work/quality-contract/readbacks/<run_id>/<snapshot_sha>/`，报告不得回写历史远端 `report.state.json`。

`result_storage_policy.py` 执行取回前配额检查：默认缓存总量 50 GiB，磁盘至少余 16 GiB。local_results 与 work/quality-contract/readbacks 合计，get_run 快照及 report include_sim 也先经过门。缓存已达配额或余量不足时，在新取回前拒绝并返回 `RESULTS_QUOTA_EXCEEDED`。这是下载前占用门，不是未知传输大小的最终容量预留；不保证一次下载后仍低于 50 GiB。配置外显式 evidence 路径不计入此缓存配额，由维护者管理和保全。

默认保留 30 天，超过 30 天仅列为人工清理复核候选，绝不自动删除。先核查 progress、引用证书、transfer、历史审批及报告 SHA：被引用文件必须归档并通过新增回读记录绑定，禁止直接删除或改历史。年龄不构成删除授权。符号链接不列清理候选，不递归删除目录。人工调整配额应走代码测试和独审发布；本轮不执行清理。

## 使用边界

支持矩阵与中文报告由 `quality_contract.py` 的同一个 renderer 生成，修改矩阵输入会拒绝生成报告。P=3/3、E=0/3；PAT-18d 的 v1 证据保持原样。URANS 和 BOM 修复 parked。Q2 基准仅有提案，未授权不提交。
