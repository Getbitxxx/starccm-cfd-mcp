# 持久作业协议

Mac 上的 stdio MCP 通过已验证主机指纹的 SSH 连接 Windows。Windows 使用按任务创建的计划任务运行隔离 worker；无需开放新的入站端口。

- `connection_health()`：版本、主机资源、计划任务服务和 worker 部署情况。
- `upload_case(local_path)`：SHA-256 命名的只读源库引用 `library/<sha256>.sim`。
- `submit_job(stage, case_ref, request_id, timeout_seconds=600)`：提交后立即返回。允许阶段 inspect/prepare/smoke/qa；超时范围 30–900 秒，固定单进程。
- `get_job(job_id)`：持久状态、worker 进度、有限日志尾部、产物引用。
- `list_jobs()`：最近 30 个任务的状态。
- `cancel_job(job_id)`：写取消请求，worker 停止自己创建的进程树。
- `fetch_results(job_id, include_sim=false)`：终态后下载文本/CSV/JSON/日志；可选择下载生成的 `.sim`，不下载工作输入。

状态：PENDING → STARTING → RUNNING → SUCCEEDED。失败出口：FAILED、TIMED_OUT、CANCELLED、REJECTED_BUSY。

worker 将失败原因写入 state.json，结束标记/必需文件不满足即 FAILED。运行中的 smoke_status.json 作为 solver 子对象返回，不与 worker 终态混淆。

runtime 按宏和 worker 内容摘要部署到独立目录。每个任务记录 runtime_digest、request_digest、源文件 SHA-256。相同 request_id 的重试不会创建第二个任务；参数变化会拒绝。

后台运行依赖 Windows 计划任务服务及已授予的本地账户权限。S4U 任务使用本地资源，本原型已按本机许可证配置验证；不能推定它可访问其他网络共享或网络许可证。任务注册保留用于诊断，结果文件不会因聊天结束而清理。
