# -*- coding: utf-8 -*-
"""The FORMAL run's report step: retrieve -> verify against the runner's records -> report.

Why this file exists (②R12)
---------------------------
`report` was wired into the restricted-trial entry (`scripts/run_static_external_flow_e2e.py`) only, so
a formal `submit_run` graph could never produce the Chinese report - and the plan gate used to hide that
by counting the offline tool for the formal mechanism on the strength of an `applies_to` *declaration*
(none of which any code checked). This is the missing formal step.

What it does (and deliberately does NOT do)
-------------------------------------------
It does NOT re-implement the report or its criteria. `scripts/generate_run_report.py` stays the single
reporter and `scripts/run_record_gate.py` stays the single citation judge; this file only performs the
adaptation a formal run needs to be readable by them:

  1. **Retrieve.** `Backend.fetch_run_results` downloads the run's records and artifacts, preserving the
     runner's directory layout.
  2. **Verify against the runner's own hashes.** Every `.sim` (and the `input.sim` when the raw run
     directory is used) is re-hashed locally and compared with the sha256 the WINDOWS runner recorded
     (`stages/<key>/checkpoint.json` `output_sha256`/`input_sha256` and `state.json` `final_sha256`).
     A file with no recorded hash is reported as `NO_RECORDED_HASH`, never as verified.
  3. **Lay the evidence out** as `<evidence>/<run_id>/stage-<stage>.json` plus the artifacts the stage
     record itself declares, and write the three records the reporter reads
     (`<run_id>-config.json`, `<run_id>-result.json`, `<run_id>-checkpoints.json`).
  4. **Call the reporter** as a subprocess.

A FAILED run gets an honest report too: the reporter is still called, and the result record carries the
run's real state. The one translation is the reporter's completion vocabulary, and it is allowed only
when the run really reached `EXECUTION_COMPLETED` with every stage record `ok` and no hash failure; the
original state is preserved in the same file as `formal_state`, so the translation is always re-checkable.

Usage
-----
    python3 scripts/formal_run_report.py --run-id <run_id> --evidence <dir> [--case-label X] [--strict]
    python3 scripts/formal_run_report.py --run-id <id> --evidence <dir> --run-dir <already-downloaded>

Exit codes: 0 (a report was produced, whatever its verdict), 2 with --strict when the verdict is not
REPORT_OK, 1 on an error in this adapter. A run that failed is NOT an adapter error.
"""
from __future__ import annotations

from quality_contract import presentation
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
REPORT_SCRIPT = ROOT / 'scripts' / 'generate_run_report.py'

# The reporter's completion vocabulary, and the formal runner's. The reporter is owned by branch 1 and is
# deliberately not modified here, so the adapter translates ONE state, under the guard below, and records
# both values. Nothing else is translated.
REPORTER_COMPLETE_STATE = 'AIR3B_PIPELINE_COMPLETE'
FORMAL_COMPLETE_STATE = 'EXECUTION_COMPLETED'
FORMAL_SUCCESS_STATES = frozenset({FORMAL_COMPLETE_STATE})
STAGE_RECORD_GLOB = 'stage-*.json'
# Engineering verdict fields read verbatim from the validation stage's own record, when the run produced
# one. This adapter never computes an engineering verdict of its own.
ENGINEERING_FIELDS = ('validation_verdict', 'residuals_met', 'target_quantities_steady',
                      'read_only_verified', 'reference_status', 'reference_value',
                      'relative_error_against_reference', 'stage1_agrees')
VALIDATION_RECORD_NAMES = ('external_flow_validation.json', 'air3b_transient_validation.json')


def sha256(path: Path) -> str:
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_json(path: Path):
    """Parse a record, or return None. A malformed runner record must not crash the report step."""
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return None


def stage_is_ok(record: object) -> bool:
    """Is this stage record a SUCCESS at the record's own granularity, macros included?

    `state: ok` alone is not enough for a completion claim: the round-6 defect this repository keeps
    meeting is a record that says ok while the thing it describes failed, and `worker/run_job.ps1` only
    writes `state='ok'` after every macro succeeded. A record whose `macros` contradict its `state` (or
    that has no macro list at all) therefore does not count as ok here. R12's review found the adapter
    checked only the top-level state.
    """
    if not isinstance(record, dict) or record.get('state') != 'ok':
        return False
    if record.get('not_a_star_stage') is True:
        # A POST step of the plan that is not a STAR stage (the ②R13 study summary/reuse step). It must
        # carry its own evidence-based condition instead of a macro list: it may only be ok when it
        # re-verified every record it relies on and found none uncitable. Anything else is not a success.
        return (record.get('records_verified') == record.get('records_expected')
                and record.get('records_expected', 0) > 0
                and not (record.get('records_not_citable') or []))
    macros = record.get('macros')
    if not isinstance(macros, list) or not macros:
        return False
    for macro in macros:
        if not isinstance(macro, dict):
            return False
        if macro.get('status') != 'ok' or macro.get('ran') is False:
            return False
    return True


def stage_jobs(run_dir: Path) -> list[Path]:
    """The stage job directories of a run, in the order the pipeline executed them.

    Order comes from `state.json.completed` (the runner's own execution record) and falls back to the
    directory names, so a run whose state is unreadable is still reportable.
    """
    stages_root = Path(run_dir) / 'stages'
    if not stages_root.is_dir():
        return []
    found = {path.parent.name: path.parent for path in sorted(stages_root.glob('*/' + STAGE_RECORD_GLOB))}
    state = read_json(Path(run_dir) / 'state.json') or {}
    ordered, seen = [], set()
    for key in state.get('completed') or []:
        if isinstance(key, str) and key in found and key not in seen:
            ordered.append(found[key])
            seen.add(key)
    ordered.extend(found[key] for key in sorted(found) if key not in seen)
    return ordered


def declared_stage_for(run_dir: Path, op_key: str) -> str | None:
    """The manifest stage the SUBMITTED PLAN names for this job directory, or None.

    The downloaded evidence copy of a run deliberately excludes `request.json` (the runner's listing skips it),
    so the job's own stage cannot always be read from the job. The run's `state.json.plan_operations` and the
    operation catalog are both in the evidence, and `run_pipeline.ps1` names each directory after the operation
    key, so the plan is an available source of truth for "which stage did this directory run".
    """
    state = read_json(Path(run_dir) / 'state.json') or {}
    plan = [str(item) for item in (state.get('plan_operations') or [])]
    if op_key not in plan:
        return None
    try:
        from casespec import resolve_operations
        entry = (resolve_operations([op_key]).get('operations') or {}).get(op_key) or {}
        stage = (entry.get('call_path') or {}).get('stage')
        return str(stage) if stage else None
    except Exception:                                                        # noqa: BLE001 - never fatal
        return None


def stage_records(run_dir: Path) -> list[dict]:
    """Per stage: the job directory, the runner's stage record, its checkpoint and its state.

    ②R14: a stage directory may contain MORE than one `stage-*.json` file. The validation stage receives the
    SOLVE stage's own record as a declared inherited input (`inherited_inputs` in the workflow manifest),
    because the independent audit greps it for the read-only hash proof. The stage's OWN record is therefore
    resolved by NAME, from the job's `request.json` when it is present and otherwise from the submitted plan
    plus the operation catalog. A record that cannot be attributed this way is never adopted on a guess: if
    exactly one payload remains and the plan does not name a different stage, it is used but TAGGED as a
    fallback, and anything else is reported as ambiguous so the caller fails closed.
    """
    records = []
    for job in stage_jobs(run_dir):
        payloads = sorted(job.glob(STAGE_RECORD_GLOB))
        request = read_json(job / 'request.json') or {}
        from_request = str(request.get('stage') or '')
        expected = from_request or declared_stage_for(run_dir, job.name) or ''
        own = job / ('stage-%s.json' % expected) if expected else None
        attribution = None
        if own is not None and own.is_file():
            chosen = own
            extras = [item for item in payloads if item != own]
            attribution = 'request.json' if from_request else 'plan_and_catalog'
        elif len(payloads) == 1:
            single_stage = str((read_json(payloads[0]) or {}).get('stage') or '')
            if expected and single_stage != expected:
                # the only record here claims to be a DIFFERENT stage than the plan says this directory ran:
                # it is somebody else's record, and adopting it would misattribute an outcome.
                chosen, extras, attribution = None, payloads, None
            else:
                chosen, extras, attribution = payloads[0], [], 'single_payload_fallback'
        else:
            chosen, extras, attribution = None, payloads, None
        records.append({'job': job, 'key': job.name, 'expected_stage': expected or None,
                        'record': read_json(chosen) if chosen is not None else None,
                        'record_path': chosen, 'record_attribution': attribution,
                        'ambiguous_stage_records': [item.name for item in payloads] if chosen is None else [],
                        'inherited_stage_records': [item.name for item in extras],
                        'checkpoint': read_json(job / 'checkpoint.json'),
                        'state': read_json(job / 'state.json')})
    return records


def expected_hashes(run_dir: Path, stages: list[dict]) -> list[dict]:
    """What the WINDOWS runner recorded for each file, and where it recorded it.

    Three sources, all written by the runner itself:

      * the per-stage `checkpoint.json` (`worker/run_pipeline.ps1`, after it hashed the stage's declared
        output);
      * the per-MACRO records inside `stage-<name>.json` (`worker/run_job.ps1` records `output` and
        `output_sha256` for every macro that produced a file) - ②R15: without these, an intermediate
        product such as `external_flow_physics.sim` was reported as "no recorded hash" even though the
        runner HAD hashed it, which is the adapter failing to read a record, not the runner failing to
        write one;
      * the run-level `state.json.final_sha256`.

    Duplicate records are kept, not deduplicated: if two records disagree about the same file in the same
    stage, `verify_artifacts` reports CONFLICTING_RECORDS and fails, rather than silently picking one. A
    file the runner never hashed at all still cannot be claimed as verified.
    """
    expected = []
    for stage in stages:
        checkpoint = stage.get('checkpoint') or {}
        output = checkpoint.get('output_path')
        if checkpoint.get('output_sha256') and output:
            expected.append({'name': PurePosixPath(str(output).replace('\\', '/')).name,
                             'sha256': str(checkpoint['output_sha256']).lower(),
                             'source': 'stages/%s/checkpoint.json output_sha256' % stage['key'],
                             'job': stage['job'], 'final': False})
        if checkpoint.get('input_sha256'):
            expected.append({'name': 'input.sim', 'sha256': str(checkpoint['input_sha256']).lower(),
                             'source': 'stages/%s/checkpoint.json input_sha256' % stage['key'],
                             'job': stage['job'], 'final': False, 'is_input': True})
        # ②R15: the per-macro outputs the runner wrote into the stage record. Only OUTPUTS are read here:
        # a later macro's input is an earlier macro's output (a different file with a different hash), so
        # reading macro inputs would manufacture conflicts that are not conflicts.
        record = stage.get('record') or {}
        for index, macro in enumerate(record.get('macros') or []):
            if not isinstance(macro, dict):
                continue
            name = macro.get('output')
            digest = macro.get('output_sha256')
            if not name or not isinstance(digest, str) or len(digest) != 64:
                continue
            expected.append({'name': str(name), 'sha256': digest.lower(),
                             'source': 'stages/%s/stage-%s.json macros[%s].output_sha256'
                                       % (stage['key'], record.get('stage'), index),
                             'job': stage['job'], 'final': False, 'stage': stage['key'],
                             'macro': macro.get('macro')})
    state = read_json(Path(run_dir) / 'state.json') or {}
    if state.get('final_sha256') and state.get('final_sim'):
        name = PurePosixPath(str(state['final_sim']).replace('\\', '/')).name
        digest = str(state['final_sha256']).lower()
        # The last stage's checkpoint already records the final .sim; mark THAT entry as the final one
        # instead of listing the same file twice under two sources. If nothing matches, the run-level
        # record is the only thing that names the deliverable, so it is added on its own.
        for item in expected:
            if item['name'] == name and item['sha256'] == digest:
                item['final'] = True
                break
        else:
            expected.append({'name': name, 'sha256': digest, 'source': 'state.json final_sha256',
                             'job': None, 'final': True})
    return expected


def located(job: Path | None, name: str, run_dir: Path) -> Path | None:
    """Where a recorded artifact actually is, refusing an ambiguous name."""
    if job is not None and (job / name).is_file():
        return job / name
    matches = sorted(path for path in Path(run_dir).rglob(name) if path.is_file())
    if len(matches) == 1:
        return matches[0]
    return None


def verify_artifacts(run_dir: Path, stages: list[dict]) -> dict:
    """Re-hash every artifact the runner recorded and say, per file, what actually matched."""
    run_dir = Path(run_dir)
    verified, failures, seen = [], [], set()
    # Group by (stage, name) first: the SAME file may be recorded by the checkpoint and by a macro record,
    # and the same NAME may legitimately appear in different stages (input.sim, an intermediate product).
    grouped = {}
    for item in expected_hashes(run_dir, stages):
        identity = (str(item['job']) if item['job'] else '', item['name'])
        grouped.setdefault(identity, []).append(item)
    for identity, items in grouped.items():
        digests = {item['sha256'] for item in items}
        recorded_by = [item['source'] for item in items]
        item = items[0]
        path = located(item['job'], item['name'], run_dir)
        if len(digests) > 1:
            # Two records from the SAME stage disagree about the same file. There is no safe way to choose,
            # so this is an explicit failure - never a silent pick and never "verified".
            entry = {'name': item['name'], 'recorded_sha256': None,
                     'recorded_sha256_by_source': [{'sha256': entry_item['sha256'],
                                                    'recorded_by': entry_item['source']}
                                                   for entry_item in items],
                     'recorded_by': recorded_by, 'final': False, 'status': 'CONFLICTING_RECORDS',
                     'local_sha256': sha256(path) if path else None,
                     'error': 'the runner recorded contradictory sha256 values for this file in the same '
                              'stage; no record can be trusted'}
            failures.append(entry)
            verified.append(entry)
            continue
        entry = {'name': item['name'], 'recorded_sha256': item['sha256'],
                 'recorded_by': recorded_by if len(recorded_by) > 1 else item['source'],
                 'final': item['final']}
        if path is None and item.get('is_input'):
            # THE INPUT IS NOT A DELIVERABLE AND IS NOT RETRIEVED: worker/run_job.ps1 copies it to
            # input.sim in the job directory, and `_get_run_status` deliberately excludes that name from
            # the artifact list, so a fetched run directory never contains it. Counting this as a failed
            # delivery would make every REAL run look undelivered while the offline fixture (which writes
            # input.sim) hid it. The input's integrity is still checked twice elsewhere: macOS verifies the
            # library content address, and the Windows runner compares the file hash with the reviewed hash
            # before each stage. Recorded here as NOT_RETRIEVED, never as verified or as a failure.
            entry.update({'status': 'NOT_RETRIEVED', 'local_sha256': None, 'bytes': None,
                          'reason': ('retrieval does not download input.sim; the runner verified it against '
                                     'the reviewed content address before the stage started')})
        elif path is None:
            entry.update({'status': 'MISSING', 'local_sha256': None, 'bytes': None,
                          'error': 'the runner recorded this file but it is not in the retrieved evidence'})
            failures.append(entry)
        else:
            local = sha256(path)
            entry.update({'status': 'VERIFIED' if local == item['sha256'] else 'HASH_MISMATCH',
                          'local_sha256': local, 'bytes': path.stat().st_size,
                          'relative': str(path.relative_to(run_dir))})
            if local != item['sha256']:
                entry['error'] = 'transferred bytes differ from the sha256 the runner recorded'
                failures.append(entry)
        verified.append(entry)
    # Artifacts with no recorded hash are listed too: absence of a runner hash is not verification.
    recorded_names = {item['name'] for item in verified}
    unrecorded = []
    for path in sorted(run_dir.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in ('.sim', '.sim.gz'):
            continue
        if path.name in recorded_names:
            continue
        if path.name == 'input.sim':
            continue
        unrecorded.append({'name': str(path.relative_to(run_dir)), 'status': 'NO_RECORDED_HASH',
                           'local_sha256': sha256(path), 'bytes': path.stat().st_size})
    final = next((item for item in verified if item['final']), None)
    return {'artifacts': verified, 'no_recorded_hash': unrecorded, 'failures': failures,
            'download_succeeded': not failures,
            'checkpoint_delivered': bool(final and final['status'] == 'VERIFIED'),
            'final_sim': (None if final is None else
                          {'name': final['name'], 'status': final['status'],
                           'sha256': final.get('local_sha256')})}


def engineering_view(run_dir: Path, stages: list[dict]) -> dict:
    """The engineering line, read from the validation stage's OWN record - never asserted here.

    The formal path proves that stages ran and that bytes arrived. It establishes nothing about the
    physics, so `claimed` is always false and the numbers below are whatever the run's own validation
    record says (or an explicit note that no such record was produced).
    """
    view = {'claimed': False,
            'reason': ('execution and delivery never establish engineering validity: the CaseSpec '
                       'thresholds are judged only by the run\'s own validation record, and this adapter '
                       'computes no verdict of its own')}
    for stage in stages:
        record = stage.get('record') or {}
        for name in (record.get('artifacts') or []):
            if str(name) not in VALIDATION_RECORD_NAMES:
                continue
            payload = read_json(stage['job'] / str(name))
            if isinstance(payload, dict):
                view['record'] = str(name)
                view['verdict_fields'] = {key: payload.get(key) for key in ENGINEERING_FIELDS
                                          if key in payload}
                return view
    view['record'] = None
    view['note'] = 'this run produced no recognised validation record'
    return view


def build_evidence(run_id: str, run_dir: Path, evidence: Path, case_label: str | None = None,
                   studies: list[str] | None = None) -> dict:
    """Lay a downloaded run out in the layout `scripts/generate_run_report.py` already reads.

    `studies` names the study operations the submitted plan declared as POST steps (`mesh_study` /
    `domain_study`). Each one is really INVOKED here through `formal_study_reuse.summarize_study`, its
    summary is written as a declared artifact, and a stage record for it is written beside the STAR stages
    so the (unmodified) shared reporter prints its conclusion and every exclusion reason. Naming a study in
    the catalog without calling it would not satisfy the plan gate, and this is where it gets called.
    """
    run_dir = Path(run_dir)
    evidence = Path(evidence)
    trial_dir = evidence / run_id
    trial_dir.mkdir(parents=True, exist_ok=True)
    stages = stage_records(run_dir)
    ambiguous = [stage for stage in stages if stage['ambiguous_stage_records']]
    if ambiguous:
        raise ValueError('a stage directory holds more than one stage-*.json record: %s'
                         % ', '.join(stage['key'] for stage in ambiguous))
    config_stages, stage_states, copied = [], {}, []
    # ②R14 review, concern 1: a stage record adopted without the job naming its stage (a single payload and no
    # plan information - only reachable for pre-R12 / steps-mode run directories) is surfaced here instead of
    # being silently trusted. It is not made fatal: those directories are historical evidence, and refusing to
    # report them would hide runs that really happened. The outcome carries the caveat instead.
    fallback_attributions = []
    for stage in stages:
        record = stage.get('record')
        if not isinstance(record, dict) or not record.get('stage'):
            raise ValueError('stage %s has no readable stage record with a stage name' % stage['key'])
        name = str(record['stage'])
        if name in stage_states:
            raise ValueError('two stage directories report the same stage name: %s' % name)
        stage_states[name] = record.get('state')
        if stage.get('record_attribution') == 'single_payload_fallback':
            fallback_attributions.append('%s -> %s' % (stage['key'], name))
        # The record is copied VERBATIM (the reporter's values must stay the runner's own). The output
        # hash the stage produced lives in the pipeline's checkpoint next to it, so it is added under its
        # own name with the source recorded - it is the runner's value, not a recomputed one.
        enriched = dict(record)
        checkpoint = stage.get('checkpoint') or {}
        if checkpoint.get('output_path'):
            enriched['output'] = PurePosixPath(str(checkpoint['output_path']).replace('\\', '/')).name
        if checkpoint.get('output_sha256'):
            enriched['output_sha256'] = str(checkpoint['output_sha256']).lower()
        enriched['adapted_from'] = ('stage record copied verbatim from the formal run; output/output_sha256 '
                                    'merged from the pipeline checkpoint.json written by worker/run_pipeline.ps1')
        (trial_dir / ('stage-%s.json' % name)).write_text(
            json.dumps(enriched, ensure_ascii=False, indent=2), encoding='utf-8')
        copied.append('stage-%s.json' % name)
        required = [str(item) for item in (record.get('artifacts') or []) if item]
        for artifact in required:
            source = located(stage['job'], artifact, run_dir)
            if source is not None:
                (trial_dir / artifact).write_bytes(source.read_bytes())
                copied.append(artifact)
        config_stages.append({'name': name, 'required': required})
    # The plan's study POST steps: really invoked, not merely declared.
    studies_written = []
    if studies:
        import formal_study_reuse
        from casespec import load_catalog
        catalog = {entry.get('id'): entry for entry in load_catalog().get('operations') or []}
        for op_id in studies:
            entry = catalog.get(op_id)
            if not entry:
                raise ValueError('the plan declares the study %s but the catalog has no such operation' % op_id)
            kind = op_id[:-len('_study')] if op_id.endswith('_study') else op_id
            endpoint = entry.get('entry') or {}
            manifest = ROOT / str(endpoint.get('levels_manifest'))
            report = ROOT / str(endpoint.get('study_report'))
            if not manifest.is_file() or not report.is_file():
                raise ValueError('study %s has no levels manifest/report to reuse' % op_id)
            base_case_spec = None
            for candidate in (ROOT / 'cases' / 'static-sphere-external-flow' / 'case_spec.json',):
                if candidate.is_file():
                    base_case_spec = candidate
            summary = formal_study_reuse.summarize_study(kind, manifest, report, base_case_spec,
                                                         current_run_dir=run_dir)
            verified = bool(summary['operation']['evidence_re_verified'])
            summary['status'] = 'ok' if verified else 'gate_failed'
            summary['_status_scope'] = ('status ok means THIS STEP re-verified the study evidence; '
                                        'sensitivity_status is the study\'s own verdict and is reported '
                                        'separately')
            summary_name = '%s-study-reuse.json' % kind
            (trial_dir / summary_name).write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                                  encoding='utf-8')
            copied.append(summary_name)
            # The study's own report is copied too, so the reporter shows its source values.
            report_name = '%s-study-report.json' % kind
            (trial_dir / report_name).write_bytes(report.read_bytes())
            copied.append(report_name)
            record = {'schema_version': 1, 'stage': op_id, 'state': 'ok' if verified else 'failed',
                      'not_a_star_stage': True,
                      'seconds': 0.0, 'macros': [],
                      'artifacts': [summary_name, report_name],
                      'mode': summary['mode'],
                      'records_verified': summary['records_cited'],
                      'records_expected': summary['records_expected'],
                      'records_not_citable': summary['records_not_citable'],
                      'measured_in_this_run': summary['provenance']['measured_in_this_run'],
                      'reused_levels': [item['level'] for item in summary['levels'] if item['reused']],
                      'sensitivity_status': summary['sensitivity_status'],
                      'sensitivity_passed': summary['sensitivity_passed'],
                      'thresholds_added': summary['operation']['thresholds_added'],
                      'recomputed_levels': summary['operation']['recomputed_levels'],
                      'note': ('a POST step of the plan, not a STAR stage: it re-verified the existing study '
                               'evidence through scripts/run_record_gate.py and carries the study\'s own verdict')}
            record['adapted_from'] = ('written by scripts/formal_run_report.py::build_evidence, which called '
                                      'scripts/formal_study_reuse.py::summarize_study for this plan step')
            (trial_dir / ('stage-%s.json' % op_id)).write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            copied.append('stage-%s.json' % op_id)
            stage_states[op_id] = record['state']
            config_stages.append({'name': op_id, 'required': [summary_name, report_name]})
            studies_written.append({'operation': op_id, 'status': summary['status'],
                                    'sensitivity_status': summary['sensitivity_status'],
                                    'records_cited': summary['records_cited'],
                                    'records_expected': summary['records_expected'],
                                    'measured_in_this_run': summary['provenance']['measured_in_this_run'],
                                    'reused_levels': [item['level'] for item in summary['levels']
                                                      if item['reused']]})
    # The run-level state is copied as well: it is the fallback the shared gate uses when no stage record
    # names an artifact, and copying it is what keeps that fallback available in the evidence directory.
    manifest_state = read_json(run_dir / 'state.json')
    if isinstance(manifest_state, dict):
        (trial_dir / 'state.json').write_text(json.dumps(manifest_state, ensure_ascii=False, indent=2),
                                              encoding='utf-8')
        copied.append('state.json')
    delivery = verify_artifacts(run_dir, stages)
    run_state = manifest_state if isinstance(manifest_state, dict) else {}
    formal_state = run_state.get('state')
    post_records = [read_json(trial_dir / ('stage-%s.json' % item['operation']))
                    for item in studies_written]
    all_ok = (bool(stage_states)
              and all(stage_is_ok(stage.get('record')) for stage in stages)
              and all(stage_is_ok(record) for record in post_records))
    # The run's OWN execution record must match what was retrieved: a run that declares three completed
    # stages while only one stage directory arrived is not a complete run. R12's review found this
    # unchecked, which turned a partially retrieved run into AIR3B_PIPELINE_COMPLETE / REPORT_OK.
    declared_completed = [key for key in (run_state.get('completed') or []) if isinstance(key, str)]
    retrieved_keys = {stage['key'] for stage in stages}
    missing_stage_dirs = [key for key in declared_completed if key not in retrieved_keys]
    # PROCESS completion only. Delivery is deliberately NOT part of this guard: a transfer that failed
    # after a successful run is a delivery failure, and the reporter already has a separate verdict for
    # it (REPORT_ARTIFACT_NOT_DELIVERED) fed by the checkpoints record below. Folding it in here would
    # report a completed run as "the run did not finish", which points a reader at the wrong repair.
    complete = (formal_state in FORMAL_SUCCESS_STATES and all_ok and bool(stages)
                and not missing_stage_dirs)
    error = run_state.get('error')
    if formal_state in FORMAL_SUCCESS_STATES and not complete:
        # The runner's own `reason` on a success state is an OPTIMISTIC sentence ("All installed stages
        # executed; engineering verification is evaluated separately"). Repeating it as `error` makes the
        # report read "the run did not finish: error=all stages executed", which is contradictory. Measured
        # on the real 2026-09-24 downloads, which predate the stage-record rule. So the explanation of WHY
        # the evidence does not verify replaces it.
        details = []
        if missing_stage_dirs:
            details.append('the runner recorded these completed stages but no stage directory was '
                           'retrieved: ' + ', '.join(missing_stage_dirs))
        if stage_states:
            details.append('stage records are ' + ', '.join('%s=%s' % (k, v)
                                                            for k, v in sorted(stage_states.items())))
        else:
            details.append('stage records are absent')
        bad = [stage['key'] for stage in stages if not stage_is_ok(stage.get('record'))]
        if bad and any(stage_states.get(key) == 'ok' for key in
                       [stage['record'].get('stage') for stage in stages
                        if isinstance(stage.get('record'), dict)]):
            details.append('a stage record says ok while its own macros do not (or it lists no macro): '
                           + ', '.join(bad))
        error = ('the run reported %s but the executed evidence does not verify: %s'
                 % (formal_state, '; '.join(details)))
    elif error is None:
        error = run_state.get('reason')
    result = {'schema_version': 1, 'run_id': run_id,
              # The only translation in this file. `state` is the reporter's vocabulary; `formal_state`
              # keeps the runner's own value beside it so the translation is always re-checkable.
              'state': REPORTER_COMPLETE_STATE if complete else formal_state,
              'formal_state': formal_state,
              'translated_state': REPORTER_COMPLETE_STATE if complete else None,
              'translation_rule': ('EXECUTION_COMPLETED -> AIR3B_PIPELINE_COMPLETE only when the runner '
                                   'reported EXECUTION_COMPLETED and every stage record is ok; otherwise '
                                   'the runner\'s own state is passed through unchanged. Delivery is '
                                   'judged separately by the checkpoints record, never by this field.'),
              'gate_passed': None,
              'numeric_gate': 'NOT_APPLICABLE_ON_THE_FORMAL_PATH',
              'error': error, 'stages': stage_states,
              'hash_verification': {'verified': sum(1 for item in delivery['artifacts']
                                                    if item['status'] == 'VERIFIED'),
                                    'failed': [item['name'] for item in delivery['failures']],
                                    'no_recorded_hash': [item['name'] for item in delivery['no_recorded_hash']]}}
    checkpoints = {'schema_version': 1, 'run_id': run_id,
                   'runner_records_present': bool(stages),
                   'solve_state': formal_state,
                   'solve_completed': formal_state in FORMAL_SUCCESS_STATES,
                   'numeric_gate_passed': None,
                   'download_succeeded': delivery['download_succeeded'],
                   'checkpoint_delivered': delivery['checkpoint_delivered'],
                   'final_sim': delivery['final_sim'],
                   'artifacts': delivery['artifacts'],
                   'artifacts_without_a_recorded_hash': delivery['no_recorded_hash'],
                   'failures': [{'name': item['name'], 'status': item['status'],
                                 'error': item.get('error')} for item in delivery['failures']],
                   'verified_against': 'the sha256 recorded by worker/run_pipeline.ps1 and worker/run_job.ps1'}
    config = {'schema_version': 1, 'trial_id': run_id, 'pipeline_id': run_id,
              'case_label': case_label or run_id, 'stages': config_stages,
              'layout': 'formal run (MCP submit_run -> worker/run_pipeline.ps1 -> worker/run_job.ps1)'}
    (evidence / (run_id + '-config.json')).write_text(json.dumps(config, ensure_ascii=False, indent=2),
                                                      encoding='utf-8')
    (evidence / (run_id + '-result.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                                      encoding='utf-8')
    (evidence / (run_id + '-checkpoints.json')).write_text(
        json.dumps(checkpoints, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'trial_dir': trial_dir, 'config': config, 'result': result, 'delivery': checkpoints,
            'stages': stages, 'files': copied, 'studies': studies_written,
            'fallback_attributions': fallback_attributions}


def run_reporter(run_id: str, evidence: Path, case_label: str | None = None,
                 strict: bool = False) -> dict:
    """Call the SHARED reporter. It stays the only implementation of the report and its criteria."""
    command = [sys.executable, str(REPORT_SCRIPT), '--trial-id', run_id, '--evidence', str(evidence)]
    if case_label:
        command += ['--case-label', case_label]
    if strict:
        command += ['--strict']
    completed = subprocess.run(command, capture_output=True, text=True, cwd=str(ROOT))
    if completed.returncode not in (0, 2):
        raise RuntimeError('the report generator failed (%d): %s'
                           % (completed.returncode, completed.stderr.strip()[-2000:]))
    try:
        return json.loads(completed.stdout)
    except ValueError as exc:
        raise RuntimeError('the report generator did not return JSON (%s): %s'
                           % (exc, completed.stdout.strip()[-500:]))


def retrieve(run_id: str, backend=None, include_sim: bool = True) -> dict:
    """Download the run's records and artifacts (the existing retrieval path, unchanged)."""
    if backend is None:
        from backend import Backend
        backend = Backend()
    result = backend.fetch_run_results(run_id, include_sim=include_sim)
    return {'directory': Path(result['directory']), 'files': result.get('files') or [],
            'state': result.get('state')}


def report_run(run_id: str, evidence: Path, run_dir: Path | None = None, case_label: str | None = None,
               strict: bool = False, include_sim: bool = True, backend=None,
               studies: list[str] | None = None) -> dict:
    """Retrieve (unless a run directory is given), verify, adapt and report. Three conclusions out.

    The plan's study POST steps are taken from the run's own `state.json.post_operations` unless the caller
    names them: whatever the submission declared is what gets invoked and reported here.
    """
    if run_dir is None:
        fetched = retrieve(run_id, backend=backend, include_sim=include_sim)
        run_dir = fetched['directory']
    run_dir = Path(run_dir)
    if studies is None:
        state_json = read_json(run_dir / 'state.json') or {}
        declared = state_json.get('post_operations') or []
        # Only the STUDY post steps are summarized here: export_evidence/report are performed by this
        # function itself and have no levels manifest to re-verify.
        studies = [str(item) for item in declared if str(item).endswith('_study')]
    from quality_contract import records_from_directory, render_quality_block
    quality_inputs=records_from_directory(run_dir,run_id=run_id)
    # Validate before the adapter writes any report artifacts.
    render_quality_block(quality_inputs)
    built = build_evidence(run_id, run_dir, Path(evidence), case_label, studies=studies)
    (Path(evidence)/(run_id+'-quality-inputs.json')).write_text(json.dumps(quality_inputs,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    report = run_reporter(run_id, Path(evidence), case_label=case_label, strict=strict)
    result, delivery = built['result'], built['delivery']
    quality=report['quality']
    declared = (read_json(run_dir / 'state.json') or {}).get('post_operations') or []
    conclusions = {
        'process': {'formal_run_state': result['formal_state'],
                    'reported_state': result['state'],
                    'translated': result['translated_state'],
                    'stage_records': result['stages'],
                    'all_stage_records_ok': all(value == 'ok'
                                                for value in result['stages'].values())
                    if result['stages'] else False,
                    'report_verdict': report.get('verdict'),
                    'report_reasons': report.get('reasons'),
                    'report_json': report.get('report_json')},
        'engineering': engineering_view(run_dir, built['stages']),
        'delivery': {'download_succeeded': delivery['download_succeeded'],
                     'checkpoint_delivered': delivery['checkpoint_delivered'],
                     'final_sim': delivery['final_sim'],
                     'verified': [item['name'] for item in delivery['artifacts']
                                  if item['status'] == 'VERIFIED'],
                     'no_recorded_hash': [item['name'] for item
                                          in delivery['artifacts_without_a_recorded_hash']],
                     'failures': delivery['failures']}}
    declares_post_steps = 'post_operations' in (read_json(run_dir / 'state.json') or {})
    outcome = {**presentation(quality),'quality':quality,'run_id': run_id, 'run_dir': str(run_dir), 'evidence': str(evidence),
               'trial_dir': str(built['trial_dir']), 'files': built['files'],
               'studies': built.get('studies') or [],
               # A run whose state.json carries NO post_operations list is not a run that failed its post
               # steps - it is a run from before the plan recorded them. Saying so is the difference between
               # "nothing to verify" and "verified": review ②R13 round 1 reached a green report with zero
               # study steps through the --run-dir re-report path and nothing in the output said so.
               'plan_declares_post_steps': declares_post_steps,
               # The three conclusions are kept apart on purpose: a deliverable that arrived says nothing
               # about the physics, and a process that ran says nothing about delivery.
               'conclusions': conclusions, 'report': report}
    # Tell the RUN itself which of its declared post steps really happened. If the caller supplied no
    # backend (a purely local re-report) nothing is written, and `recorded` stays false - an absent record
    # is not a claim that the step ran.
    performed = sorted(({'export_evidence', 'report'} | {item['operation'] for item in built.get('studies') or []})
                       & {str(item) for item in declared})
    marker = {'schema_version': 1, 'run_id': run_id, 'report_verdict': report.get('verdict'),
              'report_sha256': report.get('report_sha256'), 'evidence': str(evidence),
              'declared_post_operations': [str(item) for item in declared],
              'performed_post_operations': performed,
              'study_steps': [{'operation': item['operation'], 'status': item['status'],
                               'sensitivity_status': item['sensitivity_status'],
                               'records_cited': item['records_cited'],
                               'records_expected': item['records_expected'],
                               'measured_in_this_run': item['measured_in_this_run'],
                               'reused_levels': item['reused_levels']}
                              for item in built.get('studies') or []],
              'export_evidence': {'download_succeeded': delivery['download_succeeded'],
                                  'checkpoint_delivered': delivery['checkpoint_delivered'],
                                  'verified': sum(1 for item in delivery['artifacts']
                                                  if item['status'] == 'VERIFIED'),
                                  'failures': [item['name'] for item in delivery['failures']]},
              'engineering_claimed': False}
    receipt_dir=Path(evidence)/'quality-receipts'
    receipt_dir.mkdir(parents=True,exist_ok=True)
    body=json.dumps(dict(marker,quality_state=quality['quality_state']),ensure_ascii=False,sort_keys=True).encode()
    receipt=receipt_dir/(hashlib.sha256(body).hexdigest()+'.json')
    if receipt.exists() and receipt.read_bytes()!=body:raise ValueError('Immutable quality receipt differs')
    if not receipt.exists():receipt.write_bytes(body)
    marker['written']=False
    marker['append_only_receipt']=str(receipt)
    marker['write_error']='historical runner report.state.json preserved; new local receipt is separate'
    if built.get('fallback_attributions'):
        outcome['conclusions']['process']['stage_record_attribution'] = {
            'single_payload_fallback': built['fallback_attributions'],
            'note': ('these stage records were adopted without the job directory naming its stage (no '
                     'request.json and no plan entry). They are reported, not trusted blindly: the run predates '
                     'the recorded plan.')}
    outcome['conclusions']['engineering']['study_conclusions'] = [
        {'operation': item['operation'], 'sensitivity_status': item['sensitivity_status'],
         'records_cited': item['records_cited'], 'records_expected': item['records_expected'],
         'measured_in_this_run': item['measured_in_this_run'], 'reused_levels': item['reused_levels']}
        for item in (built.get('studies') or [])]
    outcome['run_record'] = marker
    outcome['conclusions']['delivery']['post_operations_declared'] = marker['declared_post_operations']
    outcome['conclusions']['delivery']['post_operations_performed'] = marker['performed_post_operations']
    outcome['conclusions']['delivery']['run_record_written'] = marker['written']
    # ②R14 review concern 1: how each stage record was attributed is part of the process conclusion, so a
    # fallback attribution can never be invisible. (The first version of this line referenced an undefined
    # name and crashed the report step AFTER it had already written the report - found by the round's own main
    # run, and now covered by a test that calls report_run end to end.)
    attributions = {stage['key']: stage.get('record_attribution') for stage in built['stages']}
    existing = outcome['conclusions']['process'].get('stage_record_attribution')
    if existing:
        existing['per_stage'] = attributions
    else:
        outcome['conclusions']['process']['stage_record_attribution'] = {'per_stage': attributions}
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, default=None,
                        help='an already-downloaded run directory; skips retrieval (used by tests)')
    parser.add_argument('--case-label', default=None)
    parser.add_argument('--no-sim', action='store_true', help='retrieve without .sim checkpoints')
    parser.add_argument('--strict', action='store_true')
    arguments = parser.parse_args()
    outcome = report_run(arguments.run_id, arguments.evidence, run_dir=arguments.run_dir,
                         case_label=arguments.case_label, strict=arguments.strict,
                         include_sim=not arguments.no_sim)
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    if arguments.strict and (outcome['report'].get('verdict') or '') != 'REPORT_OK':
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
