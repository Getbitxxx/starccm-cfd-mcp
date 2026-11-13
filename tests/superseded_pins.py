# -*- coding: utf-8 -*-
"""Shared helper for the pin assertions: which superseded macro hashes are RECORDED.

A macro repair legitimately stales the pipelines and review records that pin its previous bytes, and the
repository's practice is to record that supersession (round12-superseded-pins.json, round14-superseded-pins.json,
test-evidence/static-cylinder-step/r24-superseded-pins.json) rather than silently re-pin historical evidence.
The pin tests use this helper so that a RECORDED supersession is allowed and an UNEXPLAINED stale pin still
fails.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from supersession_ledger import documented_after as _documented_after  # 单处实现


def _macro_name(key: str) -> str:
    """键归一化为裸宏名：链式 ledger 用仓库相对路径（macros/X.java），旧记录用 X.java。

    2026-10-14（L1 后续）：键统一为仓库相对路径后，消费方（变体流水线测试）仍按裸名查表，
    于是查不到——归一化放在读取端，两种写法都能用。
    """
    key = key.replace('\\', '/')
    return key.split('/')[-1] if key.startswith('macros/') else key


def allowed_pins(root: Path) -> dict:
    """{macro file name: set of hashes that a record declares as superseded}."""
    allowed: dict[str, set] = {}
    for path in sorted(Path(root).rglob('*superseded-pins.json')):
        try:
            document = json.loads(path.read_text(encoding='utf-8'))
        except Exception:                                    # noqa: BLE001 - a broken record is not a pass
            continue
        for key_name in ('macro_repaired', 'macros_repaired',
                         'macro_changes_from_round_23_also_recorded_here'):
            for macro, entry in (document.get(key_name) or {}).items():
                # round12's record maps a macro straight to its REPAIRED hash (a string), the later records
                # use a block with `before`; only the recorded BEFORE values are allowances.
                if not isinstance(entry, dict):
                    continue
                values = allowed.setdefault(_macro_name(macro), set())
                for key in ('before', 'intermediate_from_round23'):
                    if entry.get(key):
                        values.add(entry[key])
    return allowed


def documented_files(root: Path) -> dict:
    """委托给 scripts/supersession_ledger.py（生产码与测试共用同一实现）。"""
    return _documented_after(root)


def _documented_files_legacy(root: Path) -> dict:
    """{file path as written in a record: set of hashes a record says the file NOW has}.

    Two conventions exist and both are read here, so a pin may drift only when some round RECORDED the move:
      * `macro_repaired` / `macros_repaired` / `macro_changes_from_round_23_also_recorded_here` — a repaired
        macro, whose `after` is the hash that repair produced;
      * `shared_state_files_updated_this_round` — a shared/state file a later authorized round updated.
    """
    documented: dict[str, set] = {}
    for path in sorted(Path(root).rglob('*superseded-pins.json')):
        try:
            document = json.loads(path.read_text(encoding='utf-8'))
        except Exception:                                    # noqa: BLE001
            continue
        for key_name in ('macro_repaired', 'macros_repaired',
                         'macro_changes_from_round_23_also_recorded_here'):
            for name, entry in (document.get(key_name) or {}).items():
                if isinstance(entry, dict) and entry.get('after'):
                    documented.setdefault(name, set()).add(entry['after'])
        for name, entry in (document.get('shared_state_files_updated_this_round') or {}).items():
            if isinstance(entry, dict) and entry.get('after'):
                documented.setdefault(name, set()).add(entry['after'])
    return documented


def is_recorded_change(name: str, digest: str, root: Path) -> bool:
    """该文件当前的字节是否被某条记录声明为"本轮之后应有的值"。

    冻结门用它区分两种漂移：**已记录**（允许，历史证据保持原字节，由 supersession 记录说明）与
    **未记录**（仍然失败）。2026-10-14 的时间预算撤销轮改了 runner/controller/provisioner/driver
    与求解宏，按仓库惯例不能改写历史证据，只能这样声明。
    """
    return digest in documented_files(root).get(name, set())


def recorded_names(root: Path) -> set:
    return set(documented_files(root))


def ledger_problems(root: Path) -> list:
    """链式 ledger 的结构校验，返回问题清单（空列表=合规）。

    L1（独立审查 2026-10-14，两次）：原 ledger 每条只存一个 before/after，文件在该记录登记后又移动时，
    after 会与磁盘不符，冻结门就会（正确地）报漂移，而没人能从 ledger 看出该怎么放行。
    **第二次审查又指出**：上一版只对带 segments 的条目生效，于是 macro_repaired 里的两个宏
    （裸名、无 segments、after 已过期）被整段跳过——那是"把判据适用面收窄到漏掉反例"（§2-O9）。
    因此本判据对声明 chain 格式的 ledger **遍历全部 section**，逐条检查：
    键为仓库相对路径；有 segments；段与段相接；无空移动；终结 after == 该文件当前磁盘 sha。
    历史 ledger（round12/14/r24）是冻结证据、沿用旧格式，不适用链式要求；读取方行为不变。

    2026-10-15（通用性轮）：判据中的"键是仓库相对路径"原实现为 `'/' not in name` 的**代理检查**。仓库根下的
    状态文件（README.md、progress.json）是真实的仓库相对路径，却被该代理当成裸文件名拒绝，于是本轮无法登记
    它们。改为按**判据本身**检查：键必须解析到仓库根下的一个真实文件；裸文件名（例如
    `RunExternalFlowSolve.java`，旧 ledger 用过这种写法）在仓库根下找不到，仍然报错。这是把代理换成判据，
    不是放宽：反例见 tests/test_supersession_ledger.py 的 bare-name 与 top-level 两条测试。
    """
    sections = ('macro_repaired', 'macros_repaired',
                'macro_changes_from_round_23_also_recorded_here',
                'shared_state_files_updated_this_round')
    problems = []
    for path in sorted(Path(root).rglob('*superseded-pins.json')):
        try:
            document = json.loads(path.read_text(encoding='utf-8'))
        except Exception:                                  # noqa: BLE001 - 坏记录不是通过
            problems.append('%s: 不可解析' % path.name)
            continue
        if (document.get('ledger_format') or {}).get('format') != 'chain':
            continue
        # 同一路径不得跨 section 重复登记（值一致时也可能在将来产生"同一文件两个 after"）
        seen: dict = {}
        for section in sections:
            for name in (document.get(section) or {}):
                if name in seen and seen[name] != section:
                    problems.append('%s: 同一路径在 %s 与 %s 两个 section 重复登记'
                                    % (name, seen[name], section))
                seen[name] = section
        for section in sections:
            for name, entry in (document.get(section) or {}).items():
                label = '%s[%s]' % (section, name)
                if not isinstance(entry, dict):
                    problems.append('%s: 条目不是对象' % label); continue
                if Path(name).is_absolute() or '..' in Path(name).parts:
                    # 独立审查 F2：只有"键解析到仓库根下的真实文件"还不够——'../outside.json' 也能命中仓库外
                    # 的真实文件。相对路径的构成必须先在形式上排除绝对路径与 `..`。
                    problems.append('%s: 键必须是仓库相对路径（不得为绝对路径或含 ..）' % label)
                    continue
                if section.startswith('macro') and not name.startswith('macros/'):
                    problems.append('%s: 宏条目必须以 macros/ 开头' % label)
                segments = entry.get('segments') or []
                if not segments:
                    problems.append('%s: 缺 segments（旧格式不得与 chain 格式混用）' % label); continue
                for index, segment in enumerate(segments):
                    if not segment.get('before') or not segment.get('after'):
                        problems.append('%s: 段 %d 缺 before/after' % (label, index))
                    if segment.get('before') == segment.get('after'):
                        problems.append('%s: 段 %d 是空移动' % (label, index))
                    if index and segments[index - 1].get('after') != segment.get('before'):
                        problems.append('%s: 段 %d 与上一段不相接' % (label, index))
                terminal = entry.get('after')
                if terminal != segments[-1].get('after'):
                    problems.append('%s: 终结 after 与最后一段不符' % label)
                base = Path(root).resolve().parent if Path(root).name == 'test-evidence' else Path.cwd()
                target = base / name
                if not target.resolve().is_relative_to(base.resolve()):
                    problems.append('%s: 键逃出仓库根（%s）' % (label, target))
                    continue
                if not target.is_file():
                    # 仓库根下找不到这个键 => 很可能是裸文件名（旧 ledger 用过 macros/X.java 的裸名）
                    problems.append('%s: 键在仓库根下找不到对应文件（不得用裸文件名）' % label)
                    continue
                import hashlib as _h
                live = _h.sha256(target.read_bytes()).hexdigest()
                if terminal != live:
                    problems.append('%s: 未终结的 after 与当前磁盘不符' % label)
    return problems
