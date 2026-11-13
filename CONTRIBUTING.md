# 贡献与维护

使用 Python 3.12+。提交前运行 `python tools/check_source.py`、`python -m compileall -q scripts tools` 和 `python tools/check_offline.py`。

修改分发的已有源码后，需显式更新 `docs/source-snapshot.json` 中相应条目的 SHA-256，并记录修改原因；不要把不一致当作通过。修改运行时代码、宏或工作流后，原部署与业务批准不再代表新字节，应在实际环境重新独立审查。

不要提交密钥、真实连接配置、运行时批准、原始仿真或大型研究数据。新增测试优先使用小型人工夹具；保留失败时拒绝执行、哈希绑定、单作业和诚实质量标签的行为。计算与部署验证须说明输入、版本、证据和适用范围。
