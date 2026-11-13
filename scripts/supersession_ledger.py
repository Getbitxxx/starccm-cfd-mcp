# -*- coding: utf-8 -*-
"""Supersession ledger 的唯一读取实现（生产码与测试共用）。

用户 2026-10-14 裁定选项 (a)：历史证据的字节**不改写**；后续经批准的字节移动由 ledger 记录
（before/after + 原因 + 授权依据）。冻结门与注册门读取本模块后放行**已记录**的移动；
**未记录**的漂移仍然失败。判据只有这一处实现——分散检查是本仓库已发生过的漏检原因。
"""
from __future__ import annotations

import json
from pathlib import Path


def _sections() -> tuple:
    return ('macro_repaired', 'macros_repaired',
            'macro_changes_from_round_23_also_recorded_here',
            'shared_state_files_updated_this_round')


def alias_keys(name: str) -> set:
    """同一文件在历史记录里的两种写法都要认：`macros/X.java` 与旧记录的裸名 `X.java`。

    2026-10-15（通用性轮）：L1 已把**写入端**统一成仓库相对路径（`macros/X.java`），并在
    `tests/superseded_pins.allowed_pins` 的**读取端**做了裸名归一化；但同一读取端的
    `documented_after` 没有归一化，于是 round12/r24/r25/r26/remediation-20261008 那些**用裸宏名**
    记录的宏移动，对 `is_recorded_change('macros/X.java', …)` 不可见——②R17 的冻结门因此只报漂移、
    无从放行（它 pin 的正是 `macros/…` 写法）。归一化放在读取端，两个方向都能查到；
    判据本身不变：仍然只认"某条记录声明过这个 sha256"。

    反向别名只给裸的 `.java` 文件名（旧记录里唯一的裸名形态），以免给任意顶层文件造出
    `macros/…` 假名。
    """
    name = (name or '').replace('\\', '/')
    keys = {name}
    if name.startswith('macros/'):
        keys.add(name.split('/')[-1])
    elif '/' not in name and name.endswith('.java'):
        keys.add('macros/' + name)
    return keys


def documented_after(root: Path) -> dict:
    """{仓库相对路径: {ledger 声明为"该文件当前应有"的 sha256}}。"""
    documented: dict[str, set] = {}
    for path in sorted(Path(root).rglob('*superseded-pins.json')):
        try:
            document = json.loads(path.read_text(encoding='utf-8'))
        except Exception:                                  # noqa: BLE001 - 坏记录不是通过
            continue
        for section in _sections():
            for name, entry in (document.get(section) or {}).items():
                if not isinstance(entry, dict):
                    continue
                values = set()
                for key in ('after', 'intermediate_from_round23'):
                    if entry.get(key):
                        values.add(entry[key])
                for segment in entry.get('segments') or []:
                    if segment.get('after'):
                        values.add(segment['after'])
                for key in alias_keys(name):
                    documented.setdefault(key, set()).update(values)
    return documented


def is_recorded_move(name: str, digest: str, root: Path) -> bool:
    """该文件当前的字节是否被 ledger 声明为已批准的移动结果。"""
    documented = documented_after(root)
    return any(digest in documented.get(candidate, set()) for candidate in alias_keys(name))
