# STAR-CCM+ CFD MCP

通过 MCP 从 macOS 控制端调度 Windows 上的 STAR-CCM+ 2502。包含 Python 控制器、Java 宏、PowerShell 持久工作器和面向代理的 Skills，适用于低速外流研究与已限定的旋转机械工作流。

**这是整理后的源码快照。** 仿真输入、运行记录、独立批准、活动版本和私有研究证据不随仓库分发。克隆源码不会继承原工作区的计算授权，也不代表任意几何均已通过工程验证。

## 能力与边界

- 接入并登记 `.sim`，核对 SHA-256，审计几何、区域、边界和物理配置。
- 校验 CaseSpec、解析操作目录和实际可执行路径，检索本地知识索引。
- 编译候选 Java 宏，按已审查的工作流提交、查询、取消和取回持久任务。
- 网格、收敛、质量守恒、GCI 和质量标签判定；分别报告流程、工程和送达结果。
- 稳定 stdio gateway 为每次调用启动固定版本的业务进程，核对独立审查及完整源码哈希。

静止外流与旋转机械的支持范围取决于具体操作、几何和证据。目录登记不等于通用验收，进程成功不等于收敛或物理正确。原工作区的历史政策和已知限制保留在 `docs/`；其中提及的证据链接可能仅存在于原工作区。

## 环境

- 控制端：Python **3.12 或更高版本**，OpenSSH 客户端；原环境在 macOS 上使用。
- 计算端：Windows、PowerShell、OpenSSH Server、STAR-CCM+ 2502 及合法许可证、随安装提供的 JDK。
- SSH 主机身份须预先核验；控制器使用 `StrictHostKeyChecking=yes` 和批处理密钥认证。

## 安装与离线检查

```bash
git clone git@github.com:Getbitxxx/starccm-cfd-mcp.git
cd starccm-cfd-mcp
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python tools/check_source.py
python tools/check_offline.py
```

`requirements.txt` 固定 MCP 的直接依赖；`requirements-lock.txt` 保留原环境的完整版本清单，未宣称它在所有平台均可安装。上述检查不连接 Windows、不部署、不启动 STAR。

## 连接配置与首次启用

1. 复制 `connection.example.json` 为 `connection.json`，填写自己的主机、账号、密钥路径、STAR/JDK 安装路径、Windows 执行目录与允许的输入目录。密钥文件留在仓库之外。
2. 先核验主机 SSH 指纹及受限身份，准备隔离执行目录。现行项目采用 Windows 主存储；大型 `.sim`、全场与逐面数据保留在 Windows，按明确存储政策取回小型报告。
3. 维护者在**新环境**中按 [运行时说明](docs/mcp-runtime-updates.md) 准备源码版本，取得针对当前完整哈希的独立审查后激活。Windows 运行时另需部署审查。历史批准不随此仓库提供，也不得套用到新字节。
4. 将 `.mcp.example.json` 的绝对路径替换为本机路径，再按客户端要求配置 `starccm-cfd`。配置中的入口为 `scripts/server_gateway.py`。
5. 连接后检查 `connection_health`、`list_workflows` 与实际操作解析结果，确认版本、部署和哈希一致；真实计算另需匹配的输入、CaseSpec、审查与运行授权。

准备待审业务版本的命令（只准备，不激活、不计算）：

```bash
python scripts/mcp_runtime_release.py --prepare-only
```

首次未准备、未批准或未激活版本时，gateway 会拒绝启动或调用。这是预期行为，不能通过删门、伪造审查或复制旧 `current.json` 解决。现有结果下载接口包含 `.sim` 选项，使用时必须遵守具体项目的 Windows 主存储政策。

## 目录

| 路径 | 内容 |
|---|---|
| `scripts/` | MCP 入口、业务控制器、计划、证据与数值判定 |
| `macros/` | STAR Java 宏；`adhoc/` 为实验宏 |
| `worker/` | Windows 持久执行和身份/运行时检查 |
| `workflows/`、`operations/` | 工作流与操作目录 |
| `skills/` | 代理使用说明和参考材料 |
| `tests/` | 原始测试及小型夹具，部分依赖原工作区证据 |
| `cases/` | CaseSpec 示例与历史配置，不能直接视为已授权算例 |
| `tools/` | 源码分发检查与可移植离线回归入口 |
| `docs/source-snapshot.json` | 源提交、分发文件哈希和脱敏标记 |

## 验证口径

GitHub Actions 运行源码分发检查、Python 编译和精选离线测试。完整历史测试集仍予保留；其中依赖 `test-evidence/`、`campaigns/`、`review/` 或远端 STAR 的测试须在原环境核验，不能将此仓库的 CI 通过解释为完整 PAT、部署或工程验收。详见 [分发与验证说明](docs/distribution.md)。

本仓库不包含 Siemens 软件、许可证、本地密钥或 CFD 大文件。本次未选择开源许可证；仓库上传本身不授予第三方开源许可。
