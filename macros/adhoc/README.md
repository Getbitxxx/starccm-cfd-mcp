# `macros/adhoc/` — 实验宏，不进 Windows 运行时

`scripts/backend.py::runtime_files()` 用 **非递归** 的 `macros/*.java` glob 组成运行时清单。所以：

- 放在 `macros/` 根目录的每个 `.java` 都会进 `runtime_digest`，**改一个字或加一个文件就会让部署审批失效**（`deployment_review()` 在第一个摘要上就拒）。
- 放在这个子目录里的 `.java` **不会**进运行时，也不影响 `runtime_digest`。

## 规则

1. 会被任何 `workflows/*/manifest.json` 的 stage 引用的宏，**必须**在 `macros/` 根目录，并且有钉住当前字节的独立审查（`tests/test_runtime_review_coverage.py` 会在缺覆盖时变红）。
2. 一次性诊断、spike、探针类宏，放这里。它们在本地或 ad-hoc 脚本里可以照常编译运行（`scripts/compile_macros.py` 按命令行显式路径编译，不 glob）。
3. 从 `macros/` 根目录往这里移之前，先确认没有 stage 或脚本按名字引用它：runner 是按 `macros/<name>.java` 解析的，移走会让引用失效。

## 为什么要有这条规则

2026-11-03 实测：trapwing 攻坚期往 `macros/` 放了 24 个实验宏，运行时从 10-23 审批的 133 个文件漂到 157 个，`runtime_digest` 从 `3f5aeec8…` 变成 `6e3f0a79…`，正式路径整体停摆——而没有人改动过任何审批决定。**日常开发冲掉审批**是结构问题，不是谁做错了事。
