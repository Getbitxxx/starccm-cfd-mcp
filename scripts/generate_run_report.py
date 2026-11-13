# -*- coding: utf-8 -*-
"""Automatic Chinese run report for one trial directory.

Why this exists (round 10): the five stages could already be assembled into one automatic run, but the
Chinese report was still written BY HAND after the fact (test-evidence/static-external-flow/
round9-solve-validation/REPORT.md). A hand-written report is not reproducible: it can say anything the
author believed, including things the records do not contain. This script reads only what the run left
behind, so "the report agrees with the evidence" is checkable instead of promised.

It is NOT a new audit macro and NOT a new validation system. It adds no threshold, no gate and no
judgement of its own:

  * which records may be cited as ok comes from scripts/run_record_gate.py, the single implementation
    of section 2-O8 (`citation_status` / `scan_run`), and is imported, not re-implemented;
  * which values are claimed comes from the stage records the Windows runner wrote and from the
    records the macros wrote, verbatim;
  * the pass/fail of the controller's numeric gate comes from the poller's `<trial>-result.json`;
  * every number in the report is accompanied by the sha256 of the file it was read from.

Outputs, next to the evidence (one entry command, no manual step):
    <trial>-report.md    the Chinese report
    <trial>-report.json  structured summary, including the evidence index
    <trial>-evidence-index.json  path -> sha256 -> citable state for every downloaded artefact
    <trial>-report-sha256.txt    sha256 of the three files above (the report's own hash)

A FAILED run also produces a report: that is the case where an honest record matters most. Exit status
is 0 whenever a report was written; --strict additionally exits 2 when the report's own verdict is not
REPORT_OK, so a caller may chain on it without parsing prose.

Usage:
    python scripts/generate_run_report.py --trial-id <id> --evidence <dir> [--strict]
"""
from __future__ import annotations

from quality_contract import presentation
import argparse
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

STAGE_TITLES = {
    'audit-body': '几何审计（只读）',
    'build-domain': '建外域',
    'region-physics-mesh': '区域/边界/物理/网格',
    'mesh-quality-audit': '网格质量审计（只读）',
    'solve': '有界求解',
    'validation': '独立验证（只读）',
}

# There is deliberately no record -> stage table here. Which stage owns an artefact is read from that
# stage's own `required` list in the submitted pipeline (see record_owner); a hard-coded table was the
# round-11 review's second blocker, because it made the verdict depend on the six baseline stage names.

UNREADABLE = 'UNREADABLE'

# The record gate implements section 2-O8. Import it; never copy its rules.
_spec = importlib.util.spec_from_file_location('run_record_gate', ROOT / 'scripts' / 'run_record_gate.py')
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def sha256(path: Path) -> str:
    # Streamed, not hashlib.file_digest: this repository's development machine runs the test suite on
    # a Python 3.9 interpreter where file_digest does not exist, and a report generator that cannot run
    # there is a report generator that will be skipped.
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path):
    try:
        # utf-8-sig, not utf-8: the Windows runner writes the stage records with a BOM (measured on
        # stage-solve.json / stage-validation.json), and a report generator that cannot read the
        # runner's own records would report every honest run as missing its stages.
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except Exception as exc:                                    # noqa: BLE001 - reported, not swallowed
        return {'__unreadable__': '%s: %s' % (type(exc).__name__, exc)}


def fmt(value) -> str:
    if value is None:
        return '—'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, float):
        if value != value:
            return 'NaN'
        if value == 0:
            return '0'
        return '%.6g' % value
    return str(value)


def collect(trial_id: str, evidence: Path) -> dict:
    """Read the trial's own artefacts. Nothing is inferred from anything outside `evidence`.

    Stage records are discovered by glob, NOT by a list of expected stage names: a pipeline whose stages
    are called something this repository has never seen must still report correctly. The same goes for
    the measurement records - every downloaded .json in the trial directory is read, and which stage owns
    which file is decided later from the config's `required` lists.
    """
    result_path = evidence / (trial_id + '-result.json')
    config_path = evidence / (trial_id + '-config.json')
    checkpoints_path = evidence / (trial_id + '-checkpoints.json')
    trial_dir = evidence / trial_id
    result = read_json(result_path) if result_path.is_file() else None
    config = read_json(config_path) if config_path.is_file() else None
    # The delivery record written by the entry command AFTER it compared the transferred .sim bytes with
    # the sha256 the Windows runner recorded. None (absent) is meaningful and is handled as such.
    delivery = read_json(checkpoints_path) if checkpoints_path.is_file() else None
    records = {}
    stages = {}
    if trial_dir.is_dir():
        for path in sorted(trial_dir.glob('*.json')):
            name = path.name
            if name.startswith('stage-'):
                stages[name[len('stage-'):-len('.json')]] = read_json(path)
            else:
                records[name] = read_json(path)
    return {'result': result, 'result_path': result_path, 'config': config, 'config_path': config_path,
            'trial_dir': trial_dir, 'records': records, 'stage_records': stages,
            'delivery': delivery, 'delivery_path': checkpoints_path}


def citable_index(trial_dir: Path) -> dict:
    """The citation state of every file under the trial directory, from the shared gate."""
    index = {}
    if not trial_dir.is_dir():
        return index
    for path in sorted(trial_dir.rglob('*')):
        if not path.is_file():
            continue
        rel = str(path.relative_to(trial_dir))
        entry = {'sha256': sha256(path), 'bytes': path.stat().st_size}
        if path.suffix == '.json':
            try:
                verdict = gate.citation_status(path)
            except Exception as exc:                            # noqa: BLE001
                verdict = {'state': 'GATE_ERROR', 'reason': str(exc)}
            # The gate's own vocabulary, verbatim: state, decided_by, run_kind. Never re-worded here -
            # this script summarises the judgement, it does not make one.
            entry['citation_state'] = verdict.get('state')
            entry['citation_decided_by'] = verdict.get('decided_by')
            entry['record_claims_ok'] = verdict.get('record_claims_ok')
        index[rel] = entry
    return index


def delivery_reasons_of(delivery: dict | None, required_by_stage: dict | None) -> list[str]:
    """Delivery failures, read from the entry command's own checkpoint record.

    Returns [] when there is nothing to complain about. When the record is ABSENT but the submitted
    config declared at least one .sim output, that absence is itself a reason: `run_static_external_flow_e2e.py`
    writes the record before it calls this script, so a missing record means the delivery was never
    checked, and "never checked" is not "arrived". A report must not upgrade the absence of a check into
    a claim that the checkpoint was delivered.
    """
    declared_sims = sorted({name for required in (required_by_stage or {}).values()
                            for name in required if name.lower().endswith('.sim')})
    if delivery is None:
        if declared_sims:
            return ['未找到 checkpoint 下载记录：无法主张 .sim 已交付（本次配置声明了 %s）'
                    % '、'.join(declared_sims)]
        return []
    reasons = []
    if delivery.get('runner_records_present') is False:
        reasons.append('未找到任何 runner 记录（config / case-probe.json / stage-*.json）：无法识别本次产物，'
                       '因此不能主张任何 .sim 已交付')
    for record in delivery.get('failures') or []:
        reasons.append('checkpoint `%s` 未通过送达核对：status=%s%s'
                       % (record.get('name'), record.get('status'),
                          '' if not record.get('error') else '（%s）' % record.get('error')))
    if delivery.get('download_succeeded') is False and not (delivery.get('failures') or []):
        reasons.append('checkpoint 下载记录显示 download_succeeded=false 但未列出失败项（记录本身不完整）')
    if declared_sims and delivery.get('solve_completed') and not delivery.get('checkpoint_delivered'):
        reasons.append('求解已结束，但最终 .sim 未通过送达核对（checkpoint_delivered=false）')
    return reasons


def verdict_of(result: dict | None, stages: dict, citable: dict,
               stage_names: list[str] | None = None,
               required_by_stage: dict | None = None,
               delivery: dict | None = None,
               exports_delivery: dict | None = None) -> tuple[str, list[str], list[str]]:
    """The report's own status: a summary of what the records already say, not a new judgement.

    Two things this run actually asserts are checked: (a) the controller's numeric gate did not fail,
    and (b) every stage ran ok AND the measurement record that stage's macro wrote is citable by the
    shared gate.

    A record that does NOT claim success is not required to be citable. The case spec, the trial config
    and the runner's state file never claim success, and `stage-<name>.json` is the attribution record
    itself rather than a measurement. The independent round-10 review corrected this function's first
    justification, which asserted a "measured" fact that is false: the region stage's
    `external_flow_region_probe.json` DOES carry `status: ok` (BuildExternalFlowRegion.java writes it,
    and the real round-7 record has it), and the shared gate rules it CITABLE because the stage record
    names it. So the rule is not "some artefacts carry no status"; it is the narrower and checkable one:
    a declared artefact that DOES claim success must be citable. The reviewer demonstrated the
    distinction by removing the probe from its stage record's `artifacts`, which turns the same bytes
    into NOT_CITABLE_NO_CLAIM - and at that point the report is right to say so.

    Round 12 adds (c): the run's .sim checkpoints were actually transferred and hash-verified. That state
    is NOT re-derived here - it is read from the delivery record the entry command wrote after comparing
    the transferred bytes against the sha256 the Windows runner recorded. A report that cannot see a
    delivery record does not get to assume the checkpoint arrived: the entry command always writes one
    before calling this script, so an absent record on a run that declares a .sim output is reported as
    a missing delivery claim, not as a success.
    """
    reasons, no_claim = [], []
    if result is None:
        return 'REPORT_NO_RESULT', ['试跑结果记录缺失：无法确认这次运行的状态'], []
    state = result.get('state')
    delivery_reasons = delivery_reasons_of(delivery, required_by_stage)
    if any('external_flow_export.json' in names for names in (required_by_stage or {}).values()):
        if not isinstance(exports_delivery, dict) or not exports_delivery.get('download_succeeded'):
            delivery_reasons.append('结果导出文件的自动取回与哈希核验未全部成立；见 exports.json，不主张交付完成')
    if state == 'GATE_FAILED':
        return ('REPORT_GATE_FAILED',
                ['控制器数值闸门未通过（runner_state=%s）' % result.get('runner_state')]
                + delivery_reasons, [])
    if state != 'AIR3B_PIPELINE_COMPLETE':
        return ('REPORT_RUN_FAILED',
                ['运行未完成：state=%s，error=%s' % (state, result.get('error'))] + delivery_reasons, [])
    if delivery_reasons:
        # The run finished but the deliverable did not arrive intact. This is a NEW verdict rather than
        # REPORT_INCOMPLETE because the failure is in this repository's delivery step, not in the STAR
        # run: saying "incomplete" would let a reader conclude another STAR run is needed, when re-running
        # the transfer is what is needed.
        return 'REPORT_ARTIFACT_NOT_DELIVERED', delivery_reasons, []

    # The expected stage set is the one THIS run declared in its config. When the config is missing the
    # expected set is whatever records exist, because a missing config is not evidence about which stages
    # should have run (round-10 review: assuming the six baseline names there would invent failures).
    expected = list(stage_names) if stage_names else sorted(stages)
    for name in expected:
        record = stages.get(name)
        if record is None:
            reasons.append('缺少阶段记录 stage-%s.json' % name)
            continue
        if record.get('state') != 'ok':
            reasons.append('阶段 %s 状态为 %s' % (name, record.get('state')))
    # Which artefacts must be citable: exactly the ones the owning stage declares in `required`, from the
    # config - never a hard-coded record list. A file that no stage declares is indexed (so its citation
    # state is visible) but is not required to be citable.
    for stage_name, required in sorted((required_by_stage or {}).items()):
        if stage_name not in stages:
            continue          # the stage itself is already reported missing or failed above
        for record_name in required:
            if record_name not in stages and record_name not in citable:
                # .sim and other non-JSON products are verified by the runner, not by this gate.
                continue
            if record_name not in citable:
                reasons.append('%s 缺失：该阶段的证据记录不在本次下载产物里' % record_name)
                continue
            entry = citable[record_name]
            status = entry.get('citation_state')
            if status == gate.CITABLE:
                continue
            if record_name not in stages and entry.get('record_claims_ok') is None:
                continue
            if entry.get('record_claims_ok'):
                reasons.append('%s 自称成功但不可引用：%s' % (record_name, status))
            else:
                no_claim.append('%s 不声称成功（%s）：作为佐证而非证书' % (record_name, status))
    if reasons:
        return 'REPORT_INCOMPLETE', reasons, no_claim
    return 'REPORT_OK', reasons, no_claim


def delivery_section(delivery: dict | None, required_by_stage: dict | None) -> list[str]:
    """The `.sim` delivery panel. Values are the entry command's own record, verbatim.

    The two statuses are shown on separate lines and never merged: a reader has to be able to see
    "the solve finished" and "the checkpoint was retrieved and its sha256 matched" as two facts.
    """
    lines = []
    if delivery is None:
        declared_sims = sorted({name for required in (required_by_stage or {}).values()
                                for name in required if name.lower().endswith('.sim')})
        if declared_sims:
            lines.append('- **没有 checkpoint 下载记录**：本次配置声明了 `.sim` 产物（%s），但没有对应的'
                         ' `<trial>-checkpoints.json`，因此**不能主张这些 .sim 已被取回**。'
                         % '、'.join('`%s`' % name for name in declared_sims))
        else:
            lines.append('- 本次提交没有声明 `.sim` 产物，无需 checkpoint 下载记录。')
        return lines
    lines.append('- 求解侧：`solve_state` = `%s`，`solve_completed` = %s，`numeric_gate_passed` = %s'
                 % (fmt(delivery.get('solve_state')), fmt(delivery.get('solve_completed')),
                    fmt(delivery.get('numeric_gate_passed'))))
    lines.append('- 下载侧：`download_succeeded` = %s，`checkpoint_delivered` = %s，`runner_records_present` = %s'
                 % (fmt(delivery.get('download_succeeded')), fmt(delivery.get('checkpoint_delivered')),
                    fmt(delivery.get('runner_records_present'))))
    final = delivery.get('final_sim')
    if final:
        lines.append('- 最终产物：`%s`（status=%s，sha256=`%s`）'
                     % (final.get('name'), fmt(final.get('status')), final.get('sha256')))
    else:
        lines.append('- 最终产物：无（本次运行没有任何成功阶段声明 `.sim` 输出）')
    artifacts = delivery.get('artifacts') or []
    if artifacts:
        lines.append('')
        lines.append('| .sim | runner 记录 sha256 | 本地 sha256 | 字节 | 状态 | 声明来源 |')
        lines.append('|---|---|---|---|---|---|')
        for record in artifacts:
            lines.append('| `%s` | `%s` | `%s` | %s | %s | %s |'
                         % (record.get('name'), record.get('recorded_sha256') or '—',
                            record.get('local_sha256') or '—', fmt(record.get('bytes')),
                            record.get('status'),
                            '、'.join(record.get('declared_by') or []) or '—'))
    failures = delivery.get('failures') or []
    if failures:
        lines.append('')
        lines.append('**送达失败项**（求解完成不等于产物已交付）：')
        for record in failures:
            lines.append('- `%s`：status=%s%s'
                         % (record.get('name'), record.get('status'),
                            '' if not record.get('error') else '，%s' % record.get('error')))
    else:
        lines.append('')
        lines.append('- 本次没有任何声明过的 `.sim` 产物送达失败。')
    return lines


def study_section(stage_names: list, stages: dict, records: dict,
                  required_by_stage: dict | None) -> list:
    """Section 6's sensitivity paragraph, read from the run's own study records.

    There is deliberately no list of study names here. A step is a study step because its own record
    says `not_a_star_stage: true`, and the details come from the artefact that record names in its
    `artifacts` list. A step the plan declared but that left no record is called out as 未执行 when its
    declared `required` list names a study artefact - that suffix is the same convention the writer of
    those artefacts already uses, not a threshold.

    The three states the round-13 review asked for are the three cases below:
      已执行            a record exists and its cited records verify
      证据不可引用      a record exists but the cited records do not (or fewer than expected)
      未执行            the plan declared the step and no record was produced
    """
    lines = []
    for name in list(stage_names):
        record = stages.get(name)
        if record is None:
            declared = [item for item in (required_by_stage or {}).get(name, [])
                        if str(item).endswith('-study-reuse.json')]
            if declared:
                lines.append('- `%s`：**未执行** —— 计划声明了这一步，但本次没有 `stage-%s.json` 记录，'
                             '因此没有任何研究结论。' % (name, name))
            continue
        if record.get('not_a_star_stage') is not True:
            continue                      # a STAR stage: sections 1-2 report it
        detail = None
        for artifact in (record.get('artifacts') or []):
            if str(artifact).endswith('-study-reuse.json') and artifact in records:
                detail = records[artifact]
        if detail is None:
            lines.append('- `%s`：已执行（`state=%s`），但本次下载产物里没有它声明的 `-study-reuse.json`，'
                         '研究判定不可读。' % (name, fmt(record.get('state'))))
            continue
        cited = detail.get('records_cited')
        expected = detail.get('records_expected')
        not_citable = detail.get('records_not_citable') or []
        status = detail.get('sensitivity_status')
        operation = detail.get('operation') or detail.get('kind') or name
        if isinstance(operation, dict):
            # the reuse writer records the invoked symbol as a block; the readable label is its name
            operation = operation.get('name') or operation.get('id') or name
        if not_citable or (isinstance(cited, int) and isinstance(expected, int) and cited < expected):
            lines.append('- `%s`（%s）：**证据不可引用** —— 引用记录 %s/%s，不可引用项：%s。'
                         '无可引用的测量记录时不给任何敏感性结论。'
                         % (name, operation, fmt(cited), fmt(expected),
                            '、'.join(str(item) for item in not_citable) or '（数量不足）'))
            continue
        verdict = ('**ESTABLISHED**' if detail.get('sensitivity_passed') else '**NOT_ESTABLISHED**')
        lines.append('- `%s`（%s）：已执行，判定 %s。按 `%s` 需要 %s 档有效，本次有效档位 %s'
                     '（声明 %s 档）；引用记录 %s/%s 可引用。'
                     % (name, operation, verdict,
                        detail.get('minimum_levels_source') or '既有档位数规则',
                        fmt(detail.get('minimum_levels_required')),
                        '、'.join(str(item) for item in (detail.get('valid_levels') or [])) or '（无）',
                        fmt(detail.get('declared_level_count')), fmt(cited), fmt(expected)))
        for key in sorted(detail):
            if key.startswith('excluded_'):
                lines.append('  - 关闭档位 `%s`：%s' % (key[len('excluded_'):], detail[key]))
        for key in ('mesh_independent', 'domain_independent'):
            if key in detail:
                lines.append('  - `%s` = `%s`' % (key, detail.get(key)))
        conclusion = detail.get('conclusion')
        if isinstance(conclusion, dict):
            conclusion = conclusion.get('reading') or json.dumps(conclusion, ensure_ascii=False)
        if conclusion:
            lines.append('  - 研究自身结论：%s' % conclusion)
    return lines


def stage_title(name: str) -> str:
    """Display label for a stage. Known stages get a Chinese title; anything else keeps its own name.

    This table is DISPLAY ONLY and is deliberately not a list of allowed stages: the set and order of
    stages comes from the pipeline JSON recorded into the trial config, so a new pipeline with new stage
    names still produces a report (round 11 generalisation).
    """
    return STAGE_TITLES.get(name, '`%s`' % name)


def run_view(cfg) -> tuple[list[str], dict]:
    """The run's own stage order and required-artefact map, read from the trial config.

    Returns (stage_names, {stage_name: [required artefacts]}). Falls back to whatever stage-*.json files
    are present when the config is missing, so a failed submission still gets a usable report.
    """
    stages = []
    if isinstance(cfg, dict):
        for stage in cfg.get('stages') or []:
            if isinstance(stage, dict) and stage.get('name'):
                stages.append((stage['name'], [str(a) for a in (stage.get('required') or [])]))
    if not stages:
        return [], {}
    return [name for name, _ in stages], {name: required for name, required in stages}


def record_owner(required_by_stage: dict) -> dict:
    """Which stage DECLARES each artefact, from the config's own required lists.

    The positive direction of the existing presentation map: instead of a hard-coded
    record -> stage table, the owning stage is whatever declares the file. A record declared by more than
    one stage is reported as such rather than silently assigned.
    """
    owners = {}
    for stage, required in required_by_stage.items():
        for name in required:
            owners.setdefault(name, []).append(stage)
    return owners


def stage_table(stage_names: list[str], stages: dict) -> list[str]:
    lines = ['| 阶段 | 状态 | 秒 | 宏（逐个独立 STAR 进程） | 输出 sha256 |', '|---|---|---|---|---|']
    for name in stage_names:
        record = stages.get(name)
        if record is None:
            lines.append('| %s | 无阶段记录 | — | — | — |' % stage_title(name))
            continue
        macros = ', '.join('%s:%s' % (m.get('macro'), m.get('status'))
                           for m in (record.get('macros') or [])) or '—'
        lines.append('| %s | %s | %s | %s | `%s` |'
                     % (stage_title(name), record.get('state'), fmt(record.get('seconds')), macros,
                        (record.get('output_sha256') or '—')[:16]))
    return lines


def artefact_states(name: str, trial_dir: Path, delivery: dict | None) -> dict:
    """File presence, transfer record and hash verification, each answered from its own evidence.

    A non-JSON artefact has no payload, which says nothing about whether the file is there: that question is
    answered by the filesystem. `未知` is written wherever the evidence does not decide - a missing delivery
    record cannot show a transfer, and a file with no runner-recorded hash cannot be hash-verified.
    """
    path = Path(trial_dir) / name
    present = path.is_file()
    recorded = None
    if delivery is None:
        transfer = '未知'
    else:
        for item in (delivery.get('artifacts') or []):
            if item.get('name') == name:
                recorded = item.get('recorded_sha256')
                break
        final = delivery.get('final_sim') or {}
        if isinstance(final, dict) and final.get('name') == name:
            recorded = final.get('sha256')
        transfer = '是' if recorded else '否'
    if recorded and present:
        verified = '是' if sha256(path) == recorded else '否'
    else:
        verified = '未知'
    return {'present': present, 'transfer': transfer, 'verified': verified,
            'recorded_sha256': recorded}


def record_overview(stage_names: list[str], stages: dict, required_by_stage: dict,
                    records: dict, index: dict, trial_dir: Path = None,
                    delivery: dict | None = None) -> list[str]:
    """One panel per stage: the stage's own state plus every required artefact it declared.

    The values shown are the record's own scalar fields, verbatim, so the panel cannot drift from the
    evidence and needs no per-case knowledge of which record carries what.
    """
    lines = []
    for name in stage_names:
        record = stages.get(name)
        lines.append('### `%s`%s' % (name, '' if record else '（无阶段记录）'))
        lines.append('')
        if record is None:
            lines.append('- 本次下载产物里没有 `stage-%s.json`，该阶段的状态无法从记录判定。' % name)
            lines.append('')
            continue
        lines.append('| 项 | 值 |')
        lines.append('|---|---|')
        lines.append('| `state` | %s |' % fmt(record.get('state')))
        lines.append('| `seconds` | %s |' % fmt(record.get('seconds')))
        if record.get('output'):
            lines.append('| `output` | `%s` |' % record.get('output'))
        if record.get('output_sha256'):
            lines.append('| `output_sha256` | `%s` |' % record.get('output_sha256'))
        if record.get('error'):
            lines.append('| `error` | %s |' % fmt(record.get('error')))
        for macro in record.get('macros') or []:
            lines.append('| 宏 `%s` | ran=%s status=%s exit_code=%s%s |'
                         % (macro.get('macro'), fmt(macro.get('ran')), fmt(macro.get('status')),
                            fmt(macro.get('exit_code')),
                            '' if not macro.get('error') else ' error=%s' % fmt(macro.get('error'))))
        lines.append('')
        required = required_by_stage.get(name) or []
        for artefact in required:
            payload = records.get(artefact)
            citation = (index.get(artefact) or {}).get('citation_state')
            states = artefact_states(artefact, trial_dir or Path('.'), delivery)
            lines.append('**`%s`**（文件存在：%s；传输登记：%s；哈希核验：%s；引用状态 %s）'
                         % (artefact, '是' if states['present'] else '否', states['transfer'],
                            states['verified'], citation or '—'))
            lines.append('')
            if payload is None:
                continue
            scalars = [(k, v) for k, v in sorted(payload.items())
                       if isinstance(v, (str, int, float, bool))]
            if not scalars:
                lines.append('- （该记录只有嵌套字段，见证据索引；原始字节可复核）')
                lines.append('')
                continue
            lines.append('| 键 | 值 |')
            lines.append('|---|---|')
            for key, value in scalars[:40]:
                lines.append('| `%s` | %s |' % (key, fmt(value)))
            if len(scalars) > 40:
                lines.append('| … | 另有 %d 个标量字段，见证据索引中的记录原文 |' % (len(scalars) - 40))
            lines.append('')
    return lines


def result_export_section(trial_id: str, evidence: Path, trial_dir: Path, index: dict) -> list[str]:
    """Embed only real, transferred, hash-matching result files from a citable export stage."""
    record = trial_dir / 'external_flow_export.json'
    if not record.is_file():
        return []
    lines = ['## 结果场图、截面及监控数据', '',
             '这些是保存 checkpoint 的数值结果；没有实验验证。导出不重新求解或划网格，'
             '不据此升级网格/计算域敏感性或工程验证结论。', '']
    citation = gate.citation_status(record)
    delivery = read_json(evidence / (trial_id + '-exports.json'))
    if citation['state'] != gate.CITABLE:
        return lines + ['- 导出记录不可引用：`%s`；不嵌入场图。' % citation['state'], '']
    if not isinstance(delivery, dict) or not delivery.get('download_succeeded'):
        return lines + ['- 结果文件送达未全部成立；不嵌入场图，见 exports.json 诊断。', '']
    payload=read_json(record)
    lines += ['- 载入迭代 `%s` → 导出后 `%s`；单元 `%s`。' %
              (payload.get('start_iteration'), payload.get('end_iteration'), payload.get('cells')),
              '- 输入 checkpoint SHA-256：`%s`。' % payload.get('input_sim_sha256'),
              '- 截面原点（m）：`%s`；法向：`%s`。' % (payload.get('section_origin_m'), payload.get('section_normal_unit')),
              '- 压力含义：%s。' % payload.get('pressure_basis'),
              '- 系数含义：%s。' % payload.get('coefficient_basis'), '']
    lines += ['| 文件 | SHA-256 | 来源 |', '|---|---|---|']
    items = delivery.get('artifacts', []) + delivery.get('derived_curves', [])
    images = []
    for item in items:
        name=item.get('name');path=trial_dir/str(name)
        expected=item.get('sha256')
        if not name or path.parent!=trial_dir or not path.is_file() or sha256(path)!=expected:
            lines.append('| `%s` | 文件缺失或哈希不符 | 不展示 |' % name)
            continue
        lines.append('| [%s](%s) | `%s` | %s |' %
                     (name,path.resolve(),expected,item.get('origin','STAR checkpoint export')))
        if path.suffix in ('.png','.svg'):
            images += ['', '![%s](%s)' % (name,path.resolve()), '']
    return lines + [''] + images


def quality_labels(records: dict, index: dict) -> dict:
    """Compatibility observations; final state uses the executable quality contract."""
    from quality_contract import legacy_label_observations
    return legacy_label_observations(records,index)


def quality_section(labels: dict, process: dict) -> list[str]:
    lines = ['## 质量标签与产品验收', '', '| P 流程 | E-num 数值 | E-phys 参考一致性 | PAT 产品发布门 |',
             '|---|---|---|---|', '| %s | %s | %s | NOT_EVALUATED（单独由 release_gate.py 判定） |'
             % (process['status'], labels['E-num']['status'], labels['E-phys']['status']), '',
             'E 标签不决定 PAT；运行完成也不自动建立 E 标签。', '', '| E-num 项 | 状态 | 原因 |', '|---|---|---|']
    for key, item in labels['E-num']['checks'].items():
        lines.append('| %s | %s | %s |' % (key, item['status'], item['reason']))
    phys = labels['E-phys']
    lines += ['', '**E-phys**：%s；来源：%s；参考值：%s；容差：%s；相对误差：%s。'
              % (phys['reason'], phys['reference_source'] or '无', fmt(phys['reference_value']),
                 fmt(phys['tolerance']), fmt(phys['relative_error'])), '']
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--trial-id', required=True)
    ap.add_argument('--evidence', type=Path, required=True)
    ap.add_argument('--case-label', default=None,
                    help='display-only case name; default is the trial config case_label/pipeline_id')
    ap.add_argument('--strict', action='store_true')
    a = ap.parse_args()
    evidence = a.evidence
    trial_id = a.trial_id
    collected = collect(trial_id, evidence)
    cfg = collected['config']
    result = collected['result']
    records = collected['records']
    stages = collected['stage_records']
    trial_dir = collected['trial_dir']
    delivery = collected['delivery']
    index = citable_index(trial_dir)

    # The run's own shape, from the pipeline it submitted. No case name, stage name or record name is
    # assumed anywhere in this file: a new pipeline is judged and displayed by its own configuration.
    stage_names, required_by_stage = run_view(cfg)
    if not stage_names:
        stage_names = sorted(stages)
    verdict, reasons, no_claim = verdict_of(result, stages, index, stage_names, required_by_stage,
                                            delivery, read_json(evidence / (trial_id + '-exports.json')))
    owners = record_owner(required_by_stage)
    case_label = (a.case_label or (cfg or {}).get('case_label') or (cfg or {}).get('pipeline_id')
                  or evidence.name)
    solve = records.get('external_flow_solve.json', {})
    valid = records.get('external_flow_validation.json', {})
    mesh = records.get('external_flow_mesh_quality.json', {})
    geom = records.get('static_body_audit.json', {})
    domain = records.get('external_domain_build.json', {})

    from quality_contract import read, records_from_directory, render_quality_block, render_report, support_matrix
    input_path=evidence/(trial_id+'-quality-inputs.json')
    quality=render_quality_block(read(input_path) if input_path.is_file() else records_from_directory(trial_dir,run_id=trial_id,execution_state=(result or {}).get('formal_state') or (result or {}).get('state')))
    from quality_contract import report_labels
    labels = report_labels(quality)
    process = {'status': 'PASS' if verdict == 'REPORT_OK' else 'NOT_ESTABLISHED',
               'report_verdict': verdict, 'reasons': reasons}
    md = []
    md.append('# 自动运行报告 —— %s' % case_label)
    md.append('')
    md.append('**试跑**：`%s`　**证据目录**：`%s`' % (trial_id, evidence))
    md.append('**报告状态**：`%s`　**流水线**：`%s`　**STAR 版本**：Simcenter STAR-CCM+ 2502.0001 (20.02.008)'
              % (verdict, (cfg or {}).get('pipeline_id') or '—'))
    md.append('**本报告由 `scripts/generate_run_report.py` 从本次证据自动生成**；文本中的每个数值都可用文末'
              '证据索引里的 sha256 复核，没有任何人工填写的结论。阶段清单与逐阶段产物取自本次试跑的配置与'
              '`stage-*.json` 记录，不写死任何案例名。')
    md.append('')
    md.extend(quality_section(labels, process))
    if reasons:
        md.append('## 0. 未完成项 / 阻断（原因由记录本身给出）')
        for reason in reasons:
            md.append('- %s' % reason)
        md.append('')
    if no_claim:
        md.append('## 0b. 不声称成功的记录（佐证，不是证书）')
        for note in no_claim:
            md.append('- %s' % note)
        md.append('')
    md.append('## 1. 阶段账（每个宏一个 STAR 进程，失败宏之后的宏记为 not_run）')
    md.append('')
    md.extend(stage_table(stage_names, stages))
    md.append('')
    md.append('控制器数值闸门：`state=%s`%s'
              % ((result or {}).get('state'),
                 '' if result is None else '，`gate_passed=%s`' % (result or {}).get('gate_passed')))
    md.append('')
    md.append('## 1b. 产物送达（.sim checkpoint 的传输与哈希核对）')
    md.append('')
    md.extend(delivery_section(delivery, required_by_stage))
    md.append('')
    md.append('## 2. 逐阶段记录（由该阶段的 `required` 清单驱动，值取自记录原文）')
    md.append('')
    if len(stage_names) == 1:
        # A one-stage submission is often a targeted positive step too (the round-11 geometry build is),
        # so describe the shape instead of asserting a reason. Whether a rejection was expected is
        # visible from the stage state and the run state below, not from a sentence typed here.
        md.append('本次提交只声明了 1 个阶段（`%s`）：这是一次单阶段试跑，不是完整流水线。'
                  '本报告只覆盖该阶段；其余阶段不属于本次提交。' % stage_names[0])
        md.append('')
    md.extend(record_overview(stage_names, stages, required_by_stage, records, index,
                             trial_dir, delivery))
    md.extend(result_export_section(trial_id, evidence, trial_dir, index))

    if solve or valid:
        md.append('## 3. 求解与独立验证对照')
        md.append('')
        md.append('| 项 | 求解阶段 | 验证阶段（只读重测） |')
        md.append('|---|---|---|')
        pairs = [('迭代', 'end_iteration', 'iteration'),
                 ('残差行数', 'residual_rows', 'residual_rows'),
                 ('stop_reason / 判定', 'stop_reason', 'validation_verdict'),
                 ('有限状态', 'finite_state', 'stage1_finite_state'),
                 ('壁面 +X 力 / N', 'measured_drag_x_n_final', 'measured_drag_x_n'),
                 ('质量不平衡比', 'measured_mass_imbalance_final', 'measured_mass_imbalance_ratio'),
                 ('区域平均密度 / kg·m⁻³', 'measured_density_volume_average_kg_m3',
                  'measured_density_volume_average_kg_m3'),
                 ('推导阻力系数', 'measured_drag_coefficient_estimate',
                  'measured_drag_coefficient_estimate'),
                 ('残差门槛全过', 'residuals_met', 'residuals_met'),
                 ('目标量稳定', 'target_quantities_steady', 'target_quantities_steady'),
                 ('与求解阶段一致', '—', 'stage1_agrees')]
        for label, a_key, b_key in pairs:
            left = '—' if a_key == '—' else fmt(solve.get(a_key))
            right = '—' if b_key == '—' else fmt(valid.get(b_key))
            md.append('| %s | %s | %s |' % (label, left, right))
        md.append('')
        if valid and (valid.get('usable') is False or valid.get('status') == 'error'):
            # The validation record itself says it did not judge. This must never be read as a criteria
            # rejection: the record is the audit's own "I could not produce a verdict" statement.
            md.append('独立验证记录本身是**未产生判定**的记录：`status=%s`，`usable=%s`，`error_class=%s`，'
                      '`error=%s`。这意味着这一步**没有给出任何收敛/质量判定**，既不是"判据不通过"，'
                      '也不是"物理不收敛"；原因写在上面这项记录里。'
                      % (fmt(valid.get('status')), fmt(valid.get('usable')),
                         fmt(valid.get('error_class')), fmt(valid.get('error'))))
            md.append('')
        elif valid:
            md.append('参考值策略：`reference_status=%s`，`reference_value=%s`，'
                      '`relative_error_against_reference=%s`。'
                      % (fmt(valid.get('reference_status')), fmt(valid.get('reference_value')),
                         fmt(valid.get('relative_error_against_reference'))))
            md.append('')
            md.append('只读可核验：`read_only_verified=%s`（本次打开的 .sim 的 sha256 出现在该阶段之前 '
                      'runner 写的 `stage-solve.json` 里）；复用的力报告 '
                      '`drag_report_inherited_from_solve=%s`，其归属与方向经 `dragReportVerified` 核对，'
                      '`drag_report_parts_verified=%s`，方向钉为 `%s`。'
                      % (fmt(valid.get('read_only_verified')),
                         fmt(valid.get('drag_report_inherited_from_solve')),
                         fmt(valid.get('drag_report_parts_verified')),
                         fmt(valid.get('drag_report_direction_unit'))))
            if valid.get('read_only_verified') is not True:
                seen = valid.get('stage_record_hashes_seen')
                if seen == 0:
                    md.append('本次工作目录里没有 runner 写的 `stage-solve.json`'
                              '（`stage_record_hashes_seen=0`，`input_sim_name=%s`），因此这一步只读核对'
                              '**无法进行**；这与"检测到写入"不是一回事，也与"判定不通过"不是一回事。'
                              % fmt(valid.get('input_sim_name')))
                elif isinstance(seen, int):
                    md.append('runner 的阶段记录里有 %d 个哈希，但没有一个等于本次打开的 .sim'
                              '（`input_sim_name=%s`）：这是一次**哈希不符**，不是"无法核对"。'
                              % (seen, fmt(valid.get('input_sim_name'))))
            if valid.get('drag_window_source') == 'none':
                md.append('目标量稳定窗口**没有被测量**（`drag_window_source=none`，'
                          '`drag_window_samples=%s`，`target_quantities_steady=%s`）：本工作目录里既没有 '
                          'report-monitor 序列，也没有求解阶段的 `external_flow_solve_history.csv`。'
                          % (fmt(valid.get('drag_window_samples')),
                             fmt(valid.get('target_quantities_stay_steady')
                                 if 'target_quantities_stay_steady' in valid
                                 else valid.get('target_quantities_steady'))))
            md.append('')
        md.append('**必须区分**：`converged`/`validation_verdict` 是**流程判定**（对 CaseSpec 里已声明的过程门槛），'
                  '推导阻力系数由 `2·Fx/(ρ·U²·A)` 从模型量算出，不是对物理量的测量；没有已核验参考值时不主张工程验证。')
        md.append('')

    known = [name for name in ('static_body_audit.json', 'external_domain_build.json',
                               'external_flow_mesh_quality.json') if name in records]
    if known:
        md.append('## 4. 关键实测摘录')
        md.append('')
        if 'static_body_audit.json' in records:
            md.append('**几何审计**（`%s`）' % ('static_body_audit.json'))
            md.append('')
            for key in ('status', 'body_part_name', 'is_closed', 'is_manifold', 'regions',
                        'triangle_count', 'measured_centre_offset_ratio',
                        'measured_vertex_radius_error_ratio', 'measured_volume_relative_deviation',
                        'tessellation_stl_sha256'):
                md.append('- `%s` = %s' % (key, fmt(geom.get(key))))
            md.append('')
        if 'external_domain_build.json' in records:
            md.append('**建外域**')
            md.append('')
            for key in ('outer_bounds_m', 'subtracted_parts', 'model_length_unit'):
                md.append('- `%s` = %s' % (key, fmt(domain.get(key))))
            md.append('')
        if 'external_flow_mesh_quality.json' in records:
            md.append('**网格质量**（全新会话重开已保存网格）')
            md.append('')
            md.append('- 单元数 = %s' % fmt(mesh.get('cells')))
            md.append('- min Cell Quality = %s' % fmt(mesh.get('measured_cell_quality_min')))
            md.append('- min Face Validity = %s' % fmt(mesh.get('measured_face_validity_min')))
            md.append('- min Volume Change = %s' % fmt(mesh.get('measured_volume_change_min')))
            md.append('- 最差壁面实测棱柱层数 = %s（要求 ≥ %s）'
                      % (fmt(mesh.get('measured_wall_prism_layers_min')),
                         fmt(mesh.get('required_wall_prism_layers'))))
            md.append('')

    md.append('## 5. 证据索引（路径 → sha256 → 可引用性；可引用性由 `scripts/run_record_gate.py` 判定）')
    md.append('')
    md.append('| 文件 | 字节 | sha256 | 引用状态 |')
    md.append('|---|---|---|---|')
    for rel, entry in sorted(index.items()):
        md.append('| `%s` | %d | `%s` | %s |'
                  % (rel, entry['bytes'], entry['sha256'], entry.get('citation_state', '—')))
    md.append('')
    md.append('## 6. 工程验证（轨道 E）缺口——本轮不因流程通过而关闭')
    md.append('')
    for key, item in labels['E-num']['checks'].items():
        if item['status'] != 'PASS':
            md.append('- E-num / %s：%s（%s）' % (key, item['status'], item['reason']))
    md.append('- E-phys：%s（%s）' % (labels['E-phys']['status'], labels['E-phys']['reason']))
    md.extend(study_section(stage_names, stages, records, required_by_stage))
    md.append('- 参考来源、容差与误差只取自可引用的验证记录；缺值不推断为通过。')
    md.append('')
    md.append('## 7. 多产物声明')
    md.append('')
    if owners:
        multi = {name: stages_ for name, stages_ in owners.items() if len(stages_) > 1}
        if multi:
            for name, stages_ in sorted(multi.items()):
                md.append('- `%s` 被多个阶段声明为必需产物：%s' % (name, '、'.join(stages_)))
        else:
            md.append('- 本次没有任何产物被多个阶段同时声明为必需产物。')
    md.append('')

    report_path = evidence / (trial_id + '-report.md')
    json_path = evidence / (trial_id + '-report.json')
    index_path = evidence / (trial_id + '-evidence-index.json')
    sums_path = evidence / (trial_id + '-report-sha256.txt')
    gated_text=render_report(quality,process=dict(process,PAT='NOT_EVALUATED'),delivery=delivery,matrix=support_matrix())
    report_path.write_text(gated_text, encoding='utf-8')
    summary = {
        'schema_version': 2,
        **presentation(quality), 'quality': quality,
        'trial_id': trial_id,
        'case_label': case_label,
        'pipeline': (cfg or {}).get('pipeline_id'),
        'stage_names': stage_names,
        'P': process,
        'E-num': labels['E-num'],
        'E-phys': labels['E-phys'],
        'PAT': {'status': 'NOT_EVALUATED', 'evaluator': 'scripts/release_gate.py'},
        'engineering_verified': False,
        'report_verdict': verdict,
        'report_reasons': reasons,
        'records_without_a_success_claim': no_claim,
        'runner_state': (result or {}).get('state'),
        'gate_passed': (result or {}).get('gate_passed'),
        'stages': {name: {'state': (stages.get(name) or {}).get('state'),
                          'seconds': (stages.get(name) or {}).get('seconds'),
                          'output_sha256': (stages.get(name) or {}).get('output_sha256')}
                   for name in stage_names},
        'validated_verdict': valid.get('validation_verdict'),
        'converged': solve.get('converged'),
        'delivery': (None if delivery is None else {
            'solve_state': delivery.get('solve_state'),
            'solve_completed': delivery.get('solve_completed'),
            'runner_records_present': delivery.get('runner_records_present'),
            'download_succeeded': delivery.get('download_succeeded'),
            'checkpoint_delivered': delivery.get('checkpoint_delivered'),
            'final_sim': delivery.get('final_sim'),
            'failures': [record.get('name') for record in (delivery.get('failures') or [])],
        }),
        'evidence_index': index,
        'result_exports_delivery': read_json(evidence / (trial_id + '-exports.json')),
    }
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['%s  %s' % (sha256(report_path), report_path.name),
             '%s  %s' % (sha256(json_path), json_path.name),
             '%s  %s' % (sha256(index_path), index_path.name)]
    sums_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({**presentation(quality),'quality':quality,'report': str(report_path), 'report_sha256': sha256(report_path),
                      'verdict': verdict, 'reasons': reasons, 'case_label': case_label,
                      'stage_names': stage_names,
                      'report_json': str(json_path), 'evidence_index': str(index_path),
                      'hashes': str(sums_path)}, ensure_ascii=False, indent=2))
    if a.strict and verdict != 'REPORT_OK':
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
