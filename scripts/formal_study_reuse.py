# -*- coding: utf-8 -*-
"""The formal path's study step: RE-VERIFY an existing sensitivity study and report its own verdict.

Why this file exists (②R13)
---------------------------
Window 1 (branch 1) implemented and RAN the mesh/domain sensitivity studies
(`scripts/run_sensitivity_study.py`), producing per-level run evidence and a study report whose own
conclusion is `NOT_ESTABLISHED` on scenario 1. R13 registers those capabilities as operations, and the
formal plan must actually INVOKE them - naming them in `operations/catalog.json` is not an invocation.

What this module does, and what it refuses to do
------------------------------------------------
It does NOT recompute a level and it invents no threshold:

  * the minimum level count comes from the existing reviewed rule
    `scripts/evaluate_sensitivity.py::MIN_LEVELS[kind]` (domain legitimately needs 2, mesh 3);
  * the sensitivity verdict is the STUDY's own (`<kind>-study-report.json`), carried verbatim;
  * every record it relies on is re-hashed and re-cited through `scripts/run_record_gate.py`, the single
    citation judge (a record from a failed stage is not citable, so it does not count);
  * it records the consistency of inputs, conditions, parameters, macro versions and provenance so a reader
    can tell a REUSED historical level from one measured in this run.

A `NOT_ESTABLISHED` study is a statement about the physics, NOT a failure of this step: the step succeeds
when it has honestly re-verified the evidence and reported the study's own verdict. Turning "the study did
not establish sensitivity" into "the run failed" would be exactly the conflation this project keeps
punishing, and so would turning it into a pass.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'scripts') not in sys.path:
    sys.path.insert(0, str(ROOT / 'scripts'))

# The measurement records a level's quantities come from. Kept as a tuple of names because the study report
# and the level directories are window-1-owned formats this module only reads.
LEVEL_RECORDS = ('external_flow_mesh_quality.json', 'external_flow_solve.json',
                 'external_flow_validation.json')
# Fields copied verbatim out of a level CaseSpec for the consistency record: these are the inputs the level
# macros read (mesh sizes, domain bounds, flow condition). Extra sections are recorded by hash, not guessed.
CONSISTENCY_SECTIONS = ('mesh', 'domain', 'flow')
STUDY_STATUSES = frozenset({'ESTABLISHED', 'NOT_ESTABLISHED', 'NOT_APPLICABLE', 'UNKNOWN'})


def sha256(path: Path) -> str:
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def minimum_levels(kind: str) -> tuple[int, str]:
    """The existing reviewed rule. Imported, never re-declared."""
    from evaluate_sensitivity import MIN_LEVELS
    if kind not in MIN_LEVELS:
        raise ValueError('unknown study kind: %r' % (kind,))
    return int(MIN_LEVELS[kind]), 'scripts/evaluate_sensitivity.py::MIN_LEVELS[%r]' % kind


def citation(path: Path) -> dict:
    """The shared gate's verdict on one record. Never re-implemented here."""
    from run_record_gate import citation_status
    return citation_status(Path(path))


def summarize_study(kind: str, levels_manifest: Path, study_report: Path,
                    case_spec: Path | None = None, *, measured_in_this_run: list | None = None,
                    evidence_root: Path | None = None, current_run_dir: Path | None = None) -> dict:
    """Re-verify one existing study and return the summary the formal report prints.

    `measured_in_this_run` names the level run directories whose evidence came from the CURRENT submission;
    everything else is a REUSED historical level and is labelled as such.
    """
    measured = {str(item) for item in (measured_in_this_run or [])}
    manifest_path = Path(levels_manifest)
    report_path = Path(study_report)
    manifest = read_json(manifest_path)
    report = read_json(report_path)
    if manifest.get('kind') != kind:
        raise ValueError('levels manifest is for %r, not %r' % (manifest.get('kind'), kind))
    required, required_source = minimum_levels(kind)
    summary = {
        'schema_version': 1,
        'kind': kind,
        'mode': 'REUSE_OF_EXISTING_STUDY_EVIDENCE',
        'levels_manifest': {'path': str(manifest_path), 'sha256': sha256(manifest_path)},
        'study_report': {'path': str(report_path), 'sha256': sha256(report_path)},
        'minimum_levels_required': required,
        'minimum_levels_source': required_source,
        'sensitivity_status': report.get('sensitivity_status'),
        'sensitivity_passed': bool(report.get('sensitivity_passed')),
        'valid_levels': list(report.get('valid_levels') or []),
        'declared_level_count': len(manifest.get('levels') or []),
        'mesh_independent': report.get('mesh_independent'),
        'domain_independent': report.get('domain_independent'),
    }
    # 1. inputs: the study spec and the geometry, hash-bound.
    spec = Path(manifest.get('spec')) if manifest.get('spec') else None
    geometry = Path(manifest.get('geometry')) if manifest.get('geometry') else None
    summary['inputs'] = {
        'study_spec': ({'path': str(spec), 'sha256': sha256(spec)} if spec and spec.is_file() else None),
        'study_spec_sha256_declared': manifest.get('spec_sha256'),
        'study_spec_hash_matches': bool(spec and spec.is_file() and sha256(spec) == manifest.get('spec_sha256')),
        'geometry': ({'path': str(geometry), 'sha256': sha256(geometry)} if geometry and geometry.is_file() else None),
        'geometry_sha256_declared': manifest.get('geometry_sha256'),
        'geometry_hash_matches': bool(geometry and geometry.is_file()
                                     and sha256(geometry) == manifest.get('geometry_sha256')),
        'base_case_spec': ({'path': str(case_spec), 'sha256': sha256(case_spec)}
                           if case_spec and Path(case_spec).is_file() else None),
    }
    # 2. per level: hash the level CaseSpec/pipeline, re-cite each measurement record, and record the
    #    conditions the level's macros read plus the macro bytes that produced it.
    levels, cited, uncitable = [], 0, []
    for level in manifest.get('levels') or []:
        name = level.get('level')
        trial_id = level.get('trial_id') or (level.get('reuse') or {}).get('trial_id')
        entry = {'level': name, 'trial_id': trial_id,
                 'reused': bool(level.get('reuse')), 'reuse_from': (level.get('reuse') or {}).get('evidence'),
                 'measured_in_this_run': False,
                 'case_spec_sha256_matches': None, 'pipeline_sha256_matches': None,
                 'conditions': None, 'records': {}, 'macro_hashes': {}}
        spec_path = Path(level.get('case_spec') or '')
        # A PINNED hash with no readable file is a mismatch, not a "nothing to check": review ②R13 round 2 (M7)
        # showed a level whose `pipeline` file was gone still passing because `..._matches` stayed None.
        pinned_spec = level.get('case_spec_sha256')
        pinned_pipeline = level.get('pipeline_sha256')
        entry['pinned_but_unreadable'] = []
        if pinned_spec and not spec_path.is_file():
            entry['pinned_but_unreadable'].append('case_spec')
        if spec_path.is_file():
            entry['case_spec_sha256'] = sha256(spec_path)
            entry['case_spec_sha256_matches'] = entry['case_spec_sha256'] == level.get('case_spec_sha256')
            try:
                lspec = read_json(spec_path)
                entry['conditions'] = {section: lspec.get(section) for section in CONSISTENCY_SECTIONS}
            except ValueError:
                entry['conditions'] = 'unreadable'
        pipeline = Path(level['pipeline']) if level.get('pipeline') else None
        if pinned_pipeline and not (pipeline and pipeline.is_file()):
            entry['pinned_but_unreadable'].append('pipeline')
        if pipeline and pipeline.is_file():
            entry['pipeline_sha256'] = sha256(pipeline)
            entry['pipeline_sha256_matches'] = entry['pipeline_sha256'] == pinned_pipeline
        declared_valid = name in (report.get('valid_levels') or [])
        entry['valid_by_the_study_gates'] = declared_valid
        excluded = [item for item in (report.get('excluded_levels') or [])
                    if isinstance(item, dict) and item.get('level') == name]
        if excluded:
            entry['exclusion_reasons'] = list(excluded[0].get('failures') or [])
        # the level's run directory: <level dir>/<trial id>
        run_dir = None
        if trial_id and spec_path.is_file() and not level.get('reuse'):
            candidate = spec_path.parent / str(trial_id)
            run_dir = candidate if candidate.is_dir() else None
        if spec_path.is_file() and level.get('reuse'):
            # a reused level points at its own evidence directory instead. The manifest stores that path the
            # way window 1's driver wrote it (repo-relative), so try it as given, then against the repo root.
            reuse = level.get('reuse') or {}
            for base in (Path(str(reuse.get('evidence') or '')), ROOT / str(reuse.get('evidence') or '')):
                candidate = base / str(reuse.get('trial_id') or '')
                if candidate.is_dir():
                    run_dir = candidate
                    break
        entry['run_dir'] = str(run_dir) if run_dir else None
        # Provenance is DERIVED: a level counts as measured by THIS submission only when its evidence
        # directory really lives inside the run being reported (or the caller names it). Review ②R13 round 1
        # found the field hardwired to [] - right for this round, but a claim rather than a derivation.
        inside_current = False
        if run_dir and current_run_dir:
            try:
                inside_current = Path(run_dir).resolve().is_relative_to(Path(current_run_dir).resolve())
            except (OSError, ValueError):
                inside_current = False
        entry['measured_in_this_run'] = bool(run_dir and (inside_current or str(run_dir) in measured))
        if run_dir is None:
            # A level whose evidence directory cannot be resolved is NOT verified: say so per record
            # instead of leaving an empty gap that a reader could mistake for "nothing to check".
            for record_name in LEVEL_RECORDS:
                entry['records'][record_name] = {'state': 'NO_RUN_DIRECTORY'}
                uncitable.append('%s/%s (NO_RUN_DIRECTORY)' % (name, record_name))
        if run_dir:
            for record_name in LEVEL_RECORDS:
                path = run_dir / record_name
                if not path.is_file():
                    entry['records'][record_name] = {'state': 'MISSING'}
                    continue
                verdict = citation(path)
                state = verdict.get('state')
                entry['records'][record_name] = {'sha256': sha256(path), 'state': state,
                                                 'decided_by': verdict.get('decided_by')}
                if state == 'CITABLE':
                    cited += 1
                else:
                    uncitable.append('%s/%s (%s)' % (name, record_name, state))
            # macro bytes that produced this level: from its own pipeline/config when available
            for config_name in ('%s-config.json' % str(trial_id),):
                config = run_dir / config_name
                if config.is_file():
                    try:
                        entry['macro_hashes'] = read_json(config).get('macro_hashes') or {}
                    except ValueError:
                        pass
        levels.append(entry)
    summary['levels'] = levels
    summary['records_cited'] = cited
    summary['records_expected'] = len(levels) * len(LEVEL_RECORDS)
    summary['records_not_citable'] = uncitable
    summary['provenance'] = {
        'measured_in_this_run': [item['level'] for item in levels if item['measured_in_this_run']],
        'reused_from_named_evidence': [{'level': item['level'], 'evidence': item['reuse_from'],
                                        'trial_id': item['trial_id']}
                                       for item in levels if item['reused']],
    }
    # The study's own claim must be consistent with the study's own level count. This is NOT a new
    # threshold: the minimum comes from the existing evaluate_sensitivity.MIN_LEVELS. A NOT_ESTABLISHED
    # study with too few valid levels is exactly what the rule predicts and stays ok; an ESTABLISHED claim
    # that its own level list cannot support is a contradiction this step refuses to certify. Review ②R13
    # round 1 built that case (one level + a report claiming ESTABLISHED) and it survived.
    valid_count = len(summary['valid_levels'])
    # The two verdict fields must also AGREE with each other (review ②R13 round 2, M6): relaying
    # `sensitivity_passed: true` beside `NOT_ESTABLISHED` launders a contradiction into a green step.
    # A status outside the declared vocabulary is not a verdict at all (review ②R13 round 3, N2: STUDY_STATUSES
    # was declared and never used, so "PASSED" or null with passed=false was certified and relayed verbatim).
    status_known = summary['sensitivity_status'] in STUDY_STATUSES
    status_passed_agree = bool(summary['sensitivity_passed']) == (summary['sensitivity_status'] == 'ESTABLISHED')
    count_consistent = summary['sensitivity_status'] != 'ESTABLISHED' or valid_count >= required
    verdict_consistent = status_known and status_passed_agree and count_consistent
    inputs_consistent = bool(summary['inputs']['study_spec_hash_matches']
                             and summary['inputs']['geometry_hash_matches'])
    mismatched_levels = [item['level'] for item in levels if item.get('case_spec_sha256_matches') is False
                         or item.get('pipeline_sha256_matches') is False
                         or item.get('pinned_but_unreadable')]
    summary['consistency'] = {
        'study_spec_hash_matches': summary['inputs']['study_spec_hash_matches'],
        'geometry_hash_matches': summary['inputs']['geometry_hash_matches'],
        'level_hashes_matching': [item['level'] for item in levels if item.get('case_spec_sha256_matches')],
        'levels_with_mismatched_hashes': mismatched_levels,
        'status_in_the_declared_vocabulary': status_known,
        'verdict_status_and_passed_agree': status_passed_agree,
        'level_count_supports_the_verdict': count_consistent,
        'verdict_consistent_with_the_level_count': count_consistent,
        'valid_levels': valid_count,
        'minimum_levels_required': required,
        'note': ('the mismatches are ENFORCED, not merely recorded: a level or input whose pinned hash no '
                 'longer matches, or an ESTABLISHED verdict the level count cannot support, makes this step '
                 'fail closed instead of certifying the study'),
    }
    if not inputs_consistent:
        summary['input_hash_mismatch'] = ('the levels manifest pins a study spec or geometry hash that does '
                                          'not match the file on disk')
    if mismatched_levels:
        summary['level_hash_mismatch'] = '; '.join(mismatched_levels)
    if not status_known:
        summary['verdict_status_unknown'] = ('the study reports sensitivity_status=%r, which is not one of %s'
                                             % (summary['sensitivity_status'], sorted(STUDY_STATUSES)))
    if not status_passed_agree:
        summary['verdict_status_passed_mismatch'] = (
            'the study reports sensitivity_status=%s together with sensitivity_passed=%s'
            % (summary['sensitivity_status'], summary['sensitivity_passed']))
    if not count_consistent:
        summary['verdict_level_count_mismatch'] = (
            'the study reports %s with %d valid level(s), but %s requires %d'
            % (summary['sensitivity_status'], valid_count, required_source, required))
    # 3. the operation's OWN verdict, kept strictly apart from the study's.
    summary['operation'] = {
        'name': '%s_study' % kind,
        'invoked': True,
        'evidence_re_verified': bool(levels) and cited == len(levels) * len(LEVEL_RECORDS)
                                 and not uncitable and inputs_consistent and not mismatched_levels
                                 and verdict_consistent,
        'thresholds_added': False,
        'recomputed_levels': 0,
        'note': ('re-hashes and re-cites the existing level records through scripts/run_record_gate.py; it adds '
                 'no threshold and recomputes nothing'),
    }
    summary['conclusion'] = {
        'sensitivity_status': summary['sensitivity_status'],
        'sensitivity_passed': summary['sensitivity_passed'],
        'engineering_claim': 'NOT_CLAIMED_BY_WIRING',
        'reading': ('NOT_ESTABLISHED is the STUDY\'s own verdict about sensitivity on this case; it is not a '
                    'failure of the study step and not a pass. E stays 0/3.'),
    }
    # Flattened, verbatim exclusion reasons so the shared reporter (which prints scalar fields) shows them.
    for item in levels:
        reasons = item.get('exclusion_reasons') or []
        if reasons:
            summary['excluded_%s' % item['level']] = '; '.join(str(reason) for reason in reasons)
    return summary
