#!/usr/bin/env python3
"""Phase-2 STAR cost ledger, stop-loss helper and the four P3 KPI baselines.

ZERO STAR: everything here is recomputed from archived JSON on disk.  Nothing starts, contacts or
estimates a solver; a metric that cannot be measured from the archive is reported as
NOT_MEASURABLE / NOT_INSTRUMENTED with the reason, never as 0 and never as a made-up ratio.

THE LEDGER RECOMPUTE RULE (verified against the archived 17.75 h closeout ledger):

    scan  campaigns/uav-2026/evidence/uav-prop-apc10x7e/**/run.json   (sorted path order)
    dedupe by config_sha256, keep the FIRST path in sorted order       -> 84 unique runs
    star_seconds(run) = sum(step.elapsed_seconds for step in run.steps) -> 63 907.100 119 s
    failed(run)       = run.status != "EXECUTION_COMPLETED"             -> 36 failed
    hours             = sum(star_seconds) / 3600                        -> 17.75197225525 h
    failed time share = failed_star_seconds / star_seconds              -> 0.3958788860256529
    failed count share= failed_runs / unique_runs                       -> 0.42857142857142855

13 of the 97 run.json files are the same config recorded again under a nested audit dir; that is
why the file count (97) is not the run count (84).  ``tests/test_kpi_ledger.py`` asserts the whole
identity to 1e-9 so the ledger cannot drift silently.

THE FOUR KPIs (baselines, not claims of success):

    failed_run_star_share     target <= 0.20   measured 0.395879 (time) / 0.428571 (count)  ABOVE
    review_rounds_per_new_plan target <= 0.50   measured 2.873563 (250 review*.json / 87 plans) ABOVE
    wall_clock_over_star      target <= 2.0    NOT_MEASURABLE: no archived run carries a wall or
                                               end timestamp (run.json has started_at only)
    report_number_traceability target = 1.0    NOT_INSTRUMENTED: no claims manifest exists under
                                               the case root (a linter alone traces nothing)

The review-round definition is mechanical and reported with every component, because the brief's
historical "~1.5" figure does NOT reproduce under any single definition (see review_rounds_metric).
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CASE_EVIDENCE = ROOT / 'campaigns' / 'uav-2026' / 'evidence' / 'uav-prop-apc10x7e'
PLANS_DATA_DIR = ROOT / 'campaigns' / 'uav-2026' / 'plans'
DEFAULT_OUT = ROOT / 'campaigns' / 'uav-2026' / 'evidence' / 'phase3-p30-20261030' / 'KPI-BASELINE.json'

EXECUTION_COMPLETED = 'EXECUTION_COMPLETED'
LEGACY_PLAN_COUNT = 87           # fallback when scripts/plan_registry.py (task-1) has not landed
WALL_SECONDS_FIELDS = ('wall_clock_seconds', 'wall_seconds', 'elapsed_wall_seconds')
END_TIMESTAMP_FIELDS = ('ended_at', 'completed_at', 'finished_at', 'ended_utc', 'completed_utc')

TARGETS = {
    'failed_run_star_share': 0.20,
    'review_rounds_per_new_plan': 0.5,
    'wall_clock_over_star': 2.0,
    'report_number_traceability': 1.0,
}

STOP_LOSS_LIMIT = 2  # consecutive fail-closed runs on new plans before a stop + diagnosis


# --------------------------------------------------------------------------- helpers


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:  # a temp fixture outside the repo
        return str(path)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _loads(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def _star_seconds(run: dict) -> float:
    total = 0.0
    for step in run.get('steps') or []:
        value = step.get('elapsed_seconds') if isinstance(step, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            if value is not None:
                raise ValueError('non-numeric step elapsed_seconds: %r' % (value,))
            continue
        total += float(value)
    return total


def _find_wall_seconds(run: dict, run_dir: Path) -> tuple[float | None, str | None]:
    """Return (seconds, source) when a record actually carries wall time, else (None, None)."""
    for field in WALL_SECONDS_FIELDS:
        value = run.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return float(value), 'run.json:%s' % field
    controller = run_dir / 'CONTROLLER-RUN.json'
    if controller.is_file():
        payload = _loads(controller)
        for field in WALL_SECONDS_FIELDS:
            value = payload.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                return float(value), 'CONTROLLER-RUN.json:%s' % field
    return None, None


# --------------------------------------------------------------------------- STAR ledger


def discover_run_records(case_root: str | Path = CASE_EVIDENCE) -> list[dict]:
    """Read every run.json once, keep the first path per config_sha256 (sorted order)."""
    root = Path(case_root)
    records: list[dict] = []
    seen: dict[str, str] = {}
    for path in sorted(root.glob('**/run.json')):
        run = _loads(path)
        config = run.get('config_sha256')
        key = config if isinstance(config, str) and config else 'path:%s' % path
        wall_seconds, wall_source = _find_wall_seconds(run, path.parent)
        record = {
            'path': _rel(path),
            'config_sha256': config,
            'status': run.get('status'),
            'star_seconds': _star_seconds(run),
            'started_at': run.get('started_at'),
            'wall_clock_seconds': wall_seconds,
            'wall_clock_source': wall_source,
            'duplicate_of': seen.get(key),
        }
        if key not in seen:
            seen[key] = record['path']
        records.append(record)
    return records


def star_ledger(records: list[dict]) -> dict:
    """Aggregate the deduplicated ledger; every component a reader needs is exposed."""
    unique = [r for r in records if not r.get('duplicate_of')]
    duplicates = [r for r in records if r.get('duplicate_of')]
    total = sum(r['star_seconds'] for r in unique)
    failed = [r for r in unique if r['status'] != EXECUTION_COMPLETED]
    failed_seconds = sum(r['star_seconds'] for r in failed)
    return {
        'definition': ('scan <case>/**/run.json, dedupe by config_sha256 keeping the first path in '
                       'sorted order, star_seconds = sum(step.elapsed_seconds), '
                       'failed = status != "%s"' % EXECUTION_COMPLETED),
        'case_evidence_root': _rel(Path(CASE_EVIDENCE)),
        'record_files': len(records),
        'unique_runs': len(unique),
        'duplicate_files': len(duplicates),
        'duplicate_paths': sorted(r['path'] for r in duplicates),
        'star_seconds_total': total,
        'star_hours_total': total / 3600.0,
        'failed_runs': len(failed),
        'completed_runs': len(unique) - len(failed),
        'failed_star_seconds': failed_seconds,
        'failed_run_count_share': (len(failed) / len(unique)) if unique else None,
        'failed_run_time_share': (failed_seconds / total) if total else None,
        'runs': sorted(({k: r[k] for k in ('path', 'config_sha256', 'status', 'star_seconds',
                                           'started_at', 'wall_clock_seconds', 'wall_clock_source')}
                        for r in unique), key=lambda r: r['path']),
        'failed_run_paths': sorted(r['path'] for r in failed),
    }


def kpi_failed_run_star_share(ledger: dict) -> dict:
    time_share = ledger['failed_run_time_share']
    count_share = ledger['failed_run_count_share']
    target = TARGETS['failed_run_star_share']
    verdict = 'ABOVE_TARGET' if (time_share is not None and time_share > target) else (
        'AT_OR_BELOW_TARGET' if time_share is not None else 'NOT_MEASURABLE')
    return {
        'id': 'failed_run_star_share',
        'target': target,
        'comparison': '<=',
        'definition': ('STAR time spent in runs whose record status is not "%s" divided by total '
                       'STAR time, both from the deduplicated ledger; the count share is the same '
                       'ratio over run counts. Time share is the primary/cost measure, count share '
                       'is secondary.' % EXECUTION_COMPLETED),
        'status': 'MEASURED',
        'measured': {'time_share': time_share, 'count_share': count_share},
        'components': {
            'failed_star_seconds': ledger['failed_star_seconds'],
            'star_seconds_total': ledger['star_seconds_total'],
            'failed_runs': ledger['failed_runs'],
            'unique_runs': ledger['unique_runs'],
        },
        'verdict': verdict,
    }


# --------------------------------------------------------------------------- review rounds


def _plan_registry_count() -> tuple[int, str, str]:
    """Registered new-plan count: task-1's registry data if it has landed, else the legacy 87.

    The registry module is only half of task-1: the plans themselves are JSON data under
    campaigns/uav-2026/plans/<campaign>/.  A module without its data cannot count anything, so the
    fallback is taken and the reason says which half was missing.
    """
    module_path = ROOT / 'scripts' / 'plan_registry.py'
    if not module_path.is_file():
        return LEGACY_PLAN_COUNT, 'legacy_fallback_87', (
            'scripts/plan_registry.py is absent at measurement time; using the legacy registry '
            'count 87 from the P2 plan list')
    try:
        spec = importlib.util.spec_from_file_location('_phase3_plan_registry', module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001 - a registry that will not import is not a count
        return LEGACY_PLAN_COUNT, 'legacy_fallback_87', (
            'scripts/plan_registry.py present but failed to import (%s: %s); using the legacy '
            'count 87' % (type(exc).__name__, exc))
    load_all = getattr(module, 'load_all', None)
    manifests = sorted(PLANS_DATA_DIR.glob('*/_campaign.json')) if PLANS_DATA_DIR.is_dir() else []
    if not callable(load_all) or not manifests:
        return LEGACY_PLAN_COUNT, 'legacy_fallback_87', (
            'scripts/plan_registry.py is present but its plans data (%s/*/_campaign.json) is not; '
            'using the legacy count 87' % _rel(PLANS_DATA_DIR))
    failures = []
    for manifest in manifests:
        campaign = manifest.parent.name
        try:
            plans = load_all(campaign)
        except Exception as exc:  # noqa: BLE001 - an unloadable campaign is not a count
            failures.append('%s (%s: %s)' % (campaign, type(exc).__name__, exc))
            continue
        if isinstance(plans, dict):
            return len(plans), 'plan_registry_load_all:%s' % campaign, (
                'counted by plan_registry.load_all(%r) over %s'
                % (campaign, _rel(manifest)))
    return LEGACY_PLAN_COUNT, 'legacy_fallback_87', (
        'scripts/plan_registry.py is present and the data directory exists, but no campaign would '
        'load (%s); using the legacy count 87' % '; '.join(failures))


def review_inventory(case_root: str | Path = CASE_EVIDENCE) -> dict:
    """Count review records mechanically; every component is named so the ratio is recomputable."""
    root = Path(case_root)
    files = sorted(root.glob('**/review*.json'))
    staging = [p for p in files if p.parent.name == 'staging']
    non_staging = [p for p in files if p.parent.name != 'staging']
    numbered = [p for p in non_staging
                if p.name.startswith('review-') and p.name[len('review-'):].split('.')[0][:1].isdigit()]
    rounds: dict[str, list[str]] = {}
    re_reviews = []
    for path in numbered:
        stem = path.name[len('review-'):].rsplit('.json', 1)[0]
        digits = ''.join(ch for ch in stem if ch.isdigit())
        suffix = stem[len(digits):]
        rounds.setdefault(digits, []).append(path.name)
        if suffix:
            re_reviews.append(path.name)
    return {
        'case_evidence_root': _rel(root),
        'glob': '%s/**/review*.json' % _rel(root),
        'review_record_files_total': len(files),
        'staging_review_records': len(staging),
        'non_staging_review_records': len(non_staging),
        'numbered_review_round_files': len(numbered),
        'distinct_review_round_numbers': len(rounds),
        'review_round_numbers': sorted(rounds, key=int),
        're_review_files': len(re_reviews),
        'run_dirs': len(sorted(root.glob('**/run.json'))),
    }


def kpi_review_rounds_per_new_plan(case_root: str | Path = CASE_EVIDENCE) -> dict:
    inventory = review_inventory(case_root)
    new_plans, plan_source, plan_note = _plan_registry_count()
    total = inventory['review_record_files_total']

    def ratio(numerator: int) -> float | None:
        return (numerator / new_plans) if new_plans else None

    measured = ratio(total)
    components = dict(inventory)
    components.update({
        'new_plans': new_plans,
        'plan_registry_source': plan_source,
        'plan_registry_note': plan_note,
        'review_records_per_plan': measured,
        'non_staging_review_records_per_plan': ratio(inventory['non_staging_review_records']),
        'staging_review_records_per_plan': ratio(inventory['staging_review_records']),
        'distinct_review_rounds_per_plan': ratio(inventory['distinct_review_round_numbers']),
        're_review_files_per_plan': ratio(inventory['re_review_files']),
        'review_records_per_staging': (total / inventory['staging_review_records']
                                       if inventory['staging_review_records'] else None),
        're_review_files_per_run_dir': (inventory['re_review_files'] / inventory['run_dirs']
                                        if inventory['run_dirs'] else None),
    })
    target = TARGETS['review_rounds_per_new_plan']
    verdict = 'ABOVE_TARGET' if (measured is not None and measured > target) else (
        'AT_OR_BELOW_TARGET' if measured is not None else 'NOT_MEASURABLE')
    return {
        'id': 'review_rounds_per_new_plan',
        'target': target,
        'comparison': '<=',
        'definition': ('PRIMARY = every review record under the case evidence root (files matching '
                       'review*.json, i.e. independent review records, staging reviews and re-review '
                       'rounds) divided by the registered new-plan count. Secondary definitions use '
                       'the same numerator families with explicit denominators; the ratio is NOT '
                       'redefined to hit the historical "~1.5" figure.'),
        'status': 'MEASURED',
        'measured': {'review_records_per_plan': measured,
                     'non_staging_review_records_per_plan':
                         components['non_staging_review_records_per_plan'],
                     'distinct_review_rounds_per_plan':
                         components['distinct_review_rounds_per_plan'],
                     're_review_files_per_plan': components['re_review_files_per_plan'],
                     'staging_review_records_per_plan':
                         components['staging_review_records_per_plan']},
        'components': components,
        'verdict': verdict,
        'note': ('The brief\'s historical "~1.5" does not reproduce under any single definition '
                 'tried: all review*.json/87 = %.3f; non-staging/87 = %.3f; distinct round '
                 'numbers/87 = %.3f; re-review files/87 = %.3f; re-review files/97 run dirs = %.3f '
                 'only by mixing denominators, so it is not claimed. The discrepancy is recorded, '
                 'not resolved by picking the definition that lands nearest 1.5.'
                 % (measured or 0.0,
                    components['non_staging_review_records_per_plan'] or 0.0,
                    components['distinct_review_rounds_per_plan'] or 0.0,
                    components['re_review_files_per_plan'] or 0.0,
                    components['re_review_files_per_run_dir'] or 0.0)),
    }


# --------------------------------------------------------------------------- wall clock


def kpi_wall_clock_over_star(records: list[dict]) -> dict:
    """Wall-clock seconds over STAR seconds. Measurable only where a record actually carries wall time."""
    unique = [r for r in records if not r.get('duplicate_of')]
    covered = [r for r in unique if r['wall_clock_seconds'] is not None]
    missing = [r for r in unique if r['wall_clock_seconds'] is None]
    target = TARGETS['wall_clock_over_star']
    searched = {
        'wall_seconds_fields': list(WALL_SECONDS_FIELDS),
        'end_timestamp_fields': list(END_TIMESTAMP_FIELDS),
        'records_searched': ['run.json', 'CONTROLLER-RUN.json (same run dir)'],
    }
    if not covered:
        return {
            'id': 'wall_clock_over_star',
            'target': target,
            'comparison': '<=',
            'definition': ('sum(wall-clock seconds) / sum(STAR seconds) over runs that record both. '
                           'Consumes wall_clock_seconds from run.json or CONTROLLER-RUN.json; a run '
                           'without a wall/end timestamp cannot enter the ratio.'),
            'status': 'NOT_MEASURABLE',
            'measured': None,
            'reason': ('none of the %d unique run records carries a wall-clock or end timestamp: '
                       'run.json has started_at but no end field, and neither run.json nor the '
                       'sibling CONTROLLER-RUN.json carries any of %s. A ratio here would be '
                       'fabricated, so it is not reported.' % (len(unique),
                                                              list(WALL_SECONDS_FIELDS))),
            'components': {'runs_with_wall_clock': 0, 'runs_considered': len(unique),
                           'field_search': searched},
            'verdict': 'NOT_MEASURABLE',
        }
    wall_total = sum(r['wall_clock_seconds'] for r in covered)
    star_total = sum(r['star_seconds'] for r in covered)
    if star_total <= 0:
        return {
            'id': 'wall_clock_over_star', 'target': target, 'comparison': '<=',
            'status': 'NOT_MEASURABLE', 'measured': None,
            'reason': ('wall clock is present for %d runs but their archived STAR seconds sum to '
                       'zero, so the ratio has no denominator' % len(covered)),
            'components': {'runs_with_wall_clock': len(covered), 'runs_considered': len(unique),
                           'wall_seconds_covered': wall_total, 'star_seconds_covered': star_total,
                           'field_search': searched},
            'verdict': 'NOT_MEASURABLE',
        }
    ratio = wall_total / star_total
    return {
        'id': 'wall_clock_over_star',
        'target': target,
        'comparison': '<=',
        'definition': ('sum(wall-clock seconds) / sum(STAR seconds) over the runs that record both, '
                       'as found in run.json or CONTROLLER-RUN.json.'),
        'status': 'MEASURED',
        'measured': {'wall_clock_over_star': ratio},
        'components': {
            'runs_with_wall_clock': len(covered),
            'runs_considered': len(unique),
            'runs_without_wall_clock': len(missing),
            'wall_seconds_covered': wall_total,
            'star_seconds_covered': star_total,
            'wall_clock_sources': sorted({r['wall_clock_source'] for r in covered if r['wall_clock_source']}),
            'field_search': searched,
        },
        'verdict': 'ABOVE_TARGET' if ratio > target else 'AT_OR_BELOW_TARGET',
    }


# --------------------------------------------------------------------------- traceability


def _claim_lint_module():
    path = ROOT / 'scripts' / 'claim_lint.py'
    if not path.is_file():
        return None, 'scripts/claim_lint.py is absent'
    try:
        spec = importlib.util.spec_from_file_location('_phase3_claim_lint', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001
        return None, 'scripts/claim_lint.py failed to import (%s: %s)' % (type(exc).__name__, exc)
    if not callable(getattr(module, 'lint', None)):
        return None, 'scripts/claim_lint.py imports but exposes no lint() entry point'
    return module, None


# error codes that mean "this number does not equal its record field" (a claim_lint verdict)
UNTRACEABLE_CLAIM_CODES = ('NUMBER_MISMATCH', 'FIELD_MISSING', 'HASH_MISMATCH',
                           'HASH_MISSING_RECORD', 'RECORD_UNPARSEABLE', 'SCHEMA_INVALID')


def _claim_manifest_paths(case_root: Path) -> list[Path]:
    found: list[Path] = []
    for pattern in ('**/*claim*.json', '**/*CLAIM*.json'):
        found.extend(case_root.glob(pattern))
    return sorted(set(found))


def _load_claim_manifest(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if isinstance(payload, dict) and payload.get('schema_version') == 1 \
            and isinstance(payload.get('claims'), list):
        return payload
    return None


def kpi_report_number_traceability(case_root: str | Path = CASE_EVIDENCE) -> dict:
    """Every report number must equal the record field it cites.

    Delegated to ``scripts/claim_lint.py`` (one linter, no second implementation): a claim is
    traceable when linting its manifest produces no value/hash/record error for that claim.
    ``claim_lint`` lints a manifest; it cannot invent one.  With no manifest the metric is
    NOT_INSTRUMENTED and the reports stay explicitly unchecked -- never 1.0 by assumption.

    SCOPE IS DYNAMIC, not hard-coded: a manifest may name its report text with a top-level
    ``"report"`` key, and ``claim_lint.lint(manifest, root)`` then performs the text checks too
    (HASH_TRUNCATED / UNBACKED_HASH / RENDER_STALE).  The record therefore reports, per manifest,
    whether report text was checked (``lint_mode`` / ``report_text_checked`` /
    ``manifests_with_report_text`` / ``manifests_without_report_text`` / ``declared_report_paths``)
    and keeps ``text_codes_out_of_scope`` empty as soon as any manifest declares a report.  The
    text-level codes stay unreachable only for manifests that declare no report, and they always
    carry no claim id (kept out of the claim-level counts).

    A declared report that cannot be read is ``REPORT_UNREADABLE``: the pairing is broken, so every
    claim in that manifest is counted untraceable (``manifests_with_unreadable_report``) rather than
    silently skipped.  Warnings stay separate from errors: a value citation of a record that claims
    ok but cannot certify success is a WARNING (O8 R3 corroboration boundary), so the ratio can be
    1.0 with warnings and is then flagged ``uncitable_warning_only`` instead of reading as clean.
    """
    root = Path(case_root)
    module, module_error = _claim_lint_module()
    candidates = _claim_manifest_paths(root)
    reports = sorted(root.glob('**/*.md'))
    definition = ('fraction of report numbers whose cited record re-renders the same value '
                  '(record-rendered claims). Delegated to scripts/claim_lint.py: a claim is '
                  'traceable when linting its manifest yields no %s error for it; a manifest that '
                  'declares a "report" also gets its report text checked by the same lint, so '
                  'HASH_TRUNCATED/UNBACKED_HASH/RENDER_STALE are in scope exactly for those '
                  'manifests. With no manifest the metric is NOT instrumented, not assumed 1.0. '
                  'Errors decide the ratio; warnings are reported separately.'
                  % '/'.join(UNTRACEABLE_CLAIM_CODES[:3]))
    components = {
        'claim_lint_present': module is not None,
        'claim_lint_path': 'scripts/claim_lint.py',
        'claim_manifest_candidates': [_rel(path) for path in candidates],
        'claims_manifest_files': 0,
        'invalid_manifest_files': [],
        'markdown_reports_in_scope': len(reports),
        'claims_total': 0,
        'claims_untraceable': 0,
        'lint_mode': 'manifest_only',
        'report_text_checked': False,
        'manifests_with_report_text': 0,
        'manifests_without_report_text': 0,
        'manifests_with_unreadable_report': 0,
        'declared_report_paths': [],
        'text_codes_out_of_scope': ['HASH_TRUNCATED', 'UNBACKED_HASH', 'RENDER_STALE'],
    }
    base = {'id': 'report_number_traceability', 'target': TARGETS['report_number_traceability'],
            'comparison': '==', 'definition': definition, 'components': components}

    manifests: list[tuple[Path, dict]] = []
    for path in candidates:
        payload = _load_claim_manifest(path)
        if payload is None:
            components['invalid_manifest_files'].append(_rel(path))
        else:
            manifests.append((path, payload))
    components['claims_manifest_files'] = len(manifests)

    if module is None:
        base.update({'status': 'NOT_INSTRUMENTED', 'measured': None,
                     'verdict': 'NOT_INSTRUMENTED',
                     'reason': ('%s; %d candidate claim manifest(s) and %d markdown reports under '
                                '%s are therefore UNCHECKED. Reporting 1.0 or 0.0 here would be '
                                'invented.' % (module_error, len(candidates), len(reports),
                                               _rel(root)))})
        return base
    if not manifests:
        base.update({'status': 'NOT_INSTRUMENTED', 'measured': None,
                     'verdict': 'NOT_INSTRUMENTED',
                     'reason': ('scripts/claim_lint.py is present, but no claims manifest exists '
                                'under %s (0 candidate files); %d markdown reports are therefore '
                                'unchecked. claim_lint lints a manifest, it cannot invent one. Once '
                                'a manifest exists it is linted as-is, including the report text it '
                                'declares via its "report" key.'
                                % (_rel(root), len(reports)))})
        return base

    claims_checked = 0
    untraceable: set[tuple[str, str]] = set()
    with_any_error: set[tuple[str, str]] = set()
    lint_errors: list[dict] = []
    lint_warnings: list[dict] = []
    with_any_warning: set[tuple[str, str]] = set()
    error_codes: dict[str, int] = {}
    warning_codes: dict[str, int] = {}
    text_level_errors = 0
    text_level_warnings = 0
    for path, payload in manifests:
        claims_in_manifest = [claim for claim in (payload.get('claims') or [])
                              if isinstance(claim, dict)]
        claims_checked += len(claims_in_manifest)
        declared_report = payload.get('report')
        if isinstance(declared_report, str) and declared_report.strip():
            components['manifests_with_report_text'] += 1
            components['declared_report_paths'].append(declared_report.strip())
        else:
            components['manifests_without_report_text'] += 1
        try:
            result = module.lint(payload, ROOT)
        except Exception as exc:  # noqa: BLE001 - linter refusal is not a traceability value
            base.update({'status': 'NOT_INSTRUMENTED', 'measured': None,
                         'verdict': 'NOT_INSTRUMENTED',
                         'reason': 'claim_lint.lint() raised for %s: %s: %s'
                                   % (_rel(path), type(exc).__name__, exc)})
            return base
        for finding in result.get('warnings') or []:
            lint_warnings.append({'manifest': _rel(path), **finding})
            code = str(finding.get('code'))
            warning_codes[code] = warning_codes.get(code, 0) + 1
            label = finding.get('claim')
            if label:
                with_any_warning.add((_rel(path), str(label)))
            else:
                text_level_warnings += 1
        manifest_level_failure = False
        for finding in result.get('errors') or []:
            lint_errors.append({'manifest': _rel(path), **finding})
            code = str(finding.get('code'))
            error_codes[code] = error_codes.get(code, 0) + 1
            label = finding.get('claim')
            if label:
                with_any_error.add((_rel(path), str(label)))
                if code in UNTRACEABLE_CLAIM_CODES:
                    untraceable.add((_rel(path), str(label)))
            else:
                # text-level / manifest-level codes carry no claim id: never attributed to a claim
                text_level_errors += 1
                if code == 'REPORT_UNREADABLE':
                    manifest_level_failure = True
        if manifest_level_failure:
            # the declared pairing is broken: every claim in this manifest is unverifiable
            components['manifests_with_unreadable_report'] += 1
            for claim in claims_in_manifest:
                label = claim.get('id')
                if isinstance(label, str) and label:
                    untraceable.add((_rel(path), label))
                    with_any_error.add((_rel(path), label))
    components['claims_total'] = claims_checked
    components['claims_checked'] = claims_checked
    components['claims_with_errors'] = len(with_any_error)
    components['claims_with_zero_errors'] = claims_checked - len(with_any_error)
    components['claims_untraceable'] = len(untraceable)
    components['claim_lint_error_codes'] = error_codes
    components['claim_lint_error_count'] = len(lint_errors)
    components['claim_lint_warning_codes'] = warning_codes
    components['claim_lint_warning_count'] = len(lint_warnings)
    components['claims_with_warnings'] = len(with_any_warning)
    components['text_level_error_count'] = text_level_errors
    components['text_level_warning_count'] = text_level_warnings
    components['claim_lint_errors'] = lint_errors[:20]
    components['claim_lint_warnings'] = lint_warnings[:20]
    # scope follows the manifests actually discovered: never hard-code manifest_only
    if components['manifests_with_report_text']:
        components['lint_mode'] = 'manifest_and_report_text'
        components['report_text_checked'] = True
        components['text_codes_out_of_scope'] = []
    if claims_checked == 0:
        base.update({'status': 'NOT_INSTRUMENTED', 'measured': None,
                     'verdict': 'NOT_INSTRUMENTED',
                     'reason': 'the claims manifest(s) declare no claims, so there is nothing to '
                               'trace; not a 1.0'})
        return base
    ratio = (claims_checked - len(untraceable)) / claims_checked
    base.update({'status': 'MEASURED',
                 'measured': {'traceability': ratio,
                              'claims_checked': claims_checked,
                              'claims_with_zero_errors': components['claims_with_zero_errors'],
                              'claims_untraceable': len(untraceable),
                              'claim_lint_warnings': len(lint_warnings),
                              # 1.0 with zero errors and non-zero warnings: the numbers are
                              # traceable, but at least one cited record cannot certify success
                              # (O8 R3). Surfaced so it cannot read as clean.
                              'uncitable_warning_only': bool(lint_warnings) and not lint_errors},
                 'verdict': 'AT_TARGET' if ratio >= TARGETS['report_number_traceability']
                 and not lint_errors else 'BELOW_TARGET'})
    return base


# --------------------------------------------------------------------------- stop loss


def consecutive_fail_closed(records: list[dict], new_plan_ids: list[str],
                            limit: int = STOP_LOSS_LIMIT) -> dict:
    """Stop-loss: ``limit`` consecutive fail-closed new-plan runs require a stop + diagnosis.

    ``records`` are per-plan-run dicts in chronological order:
        {'plan_id': str, 'fail_closed': bool}
    Only runs whose plan_id is in ``new_plan_ids`` are examined. Returns a stop record.
    """
    allowed = list(new_plan_ids)
    allowed_set = set(allowed)
    examined = [r for r in records if r.get('plan_id') in allowed_set]
    run = 0
    worst = 0
    for record in examined:
        if bool(record.get('fail_closed')):
            run += 1
            worst = max(worst, run)
            if run >= limit:
                return {
                    'stop': True,
                    'code': 'STOP_LOSS_CONSECUTIVE_FAIL_CLOSED',
                    'limit': limit,
                    'consecutive_fail_closed': run,
                    'at_plan_id': record.get('plan_id'),
                    'diagnosis_required': True,
                    'examined_runs': len(examined),
                    'rule': ('%d consecutive fail-closed runs on new plans => stop the sample, '
                             'write a root-cause diagnosis before the next attempt' % limit),
                }
        else:
            run = 0
    return {
        'stop': False,
        'code': 'STOP_LOSS_NOT_TRIGGERED',
        'limit': limit,
        'consecutive_fail_closed': worst,
        'at_plan_id': None,
        'diagnosis_required': False,
        'examined_runs': len(examined),
        'rule': ('%d consecutive fail-closed runs on new plans => stop the sample, write a '
                 'root-cause diagnosis before the next attempt' % limit),
    }


def stop_loss_status(records: list[dict], new_plan_ids: list[str] | None = None) -> dict:
    """Apply the stop-loss rule to a plan-run log; with no log the status is honestly unevaluated."""
    if not records:
        return {'status': 'NOT_EVALUATED',
                'rule': consecutive_fail_closed([], [])['rule'],
                'limit': STOP_LOSS_LIMIT,
                'reason': ('no phase-3 plan-run log exists yet (this round is ZERO STAR); the helper '
                           'is shipped and pinned by tests/test_kpi_ledger.py'),
                'helper': 'scripts/kpi_ledger.py::consecutive_fail_closed'}
    return {'status': 'EVALUATED', 'limit': STOP_LOSS_LIMIT,
            'new_plan_ids': list(new_plan_ids or []),
            'result': consecutive_fail_closed(records, list(new_plan_ids or []))}


# --------------------------------------------------------------------------- wave plan (F6)


WAVE_STATUSES = ('CLOSED', 'ACTIVE', 'NOT_ADMITTED')
STOP_RULE_LIMIT = 2            # "consecutive 2 fail-closed => stop and write a diagnosis"


def _finite_number(value: object, what: str, errors: list, wave: str) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append({'code': 'WAVE_PLAN_NUMBER', 'wave': wave,
                       'message': '%s must be a number, got %r' % (what, value)})
        return None
    value = float(value)
    if value != value or value in (float('inf'), float('-inf')) or value < 0:
        errors.append({'code': 'WAVE_PLAN_NUMBER', 'wave': wave,
                       'message': '%s must be finite and >= 0, got %r' % (what, value)})
        return None
    return value


def _integer(value: object, what: str, errors: list, wave: str) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        errors.append({'code': 'WAVE_PLAN_NUMBER', 'wave': wave,
                       'message': '%s must be an integer >= 0, got %r' % (what, value)})
        return None
    return value


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _stop_condition_ok(condition: object) -> bool:
    return (isinstance(condition, dict)
            and condition.get('consecutive_fail_closed') == STOP_RULE_LIMIT
            and _nonempty(condition.get('on_stop')))


def validate_wave_plan(document: object) -> list[dict]:
    """Validate a wave plan: every wave declares cost/benefit (STAR hours + rounds) and the stop rule.

    F6 wording: "each wave's plan writes cost/benefit (estimated STAR hours, rounds) and the stop
    condition (2 consecutive fail-closed => stop and write a diagnosis)".  A NOT_ADMITTED wave may
    carry a PROPOSED estimate (that is what a budget decision needs) but must say its basis, list the
    signed blockers, and show zero spend.  Returns [] when valid; never raises on a malformed doc.
    """
    errors: list[dict] = []

    def err(code: str, message: str, wave: str | None = None) -> None:
        item = {'code': code, 'message': message}
        if wave is not None:
            item['wave'] = wave
        errors.append(item)

    if not isinstance(document, dict):
        return [{'code': 'WAVE_PLAN_INVALID', 'message': 'wave plan must be a JSON object'}]
    if document.get('schema_version') != 1:
        err('WAVE_PLAN_SCHEMA', 'schema_version must be 1')
    policy = document.get('policy')
    if not isinstance(policy, dict):
        err('WAVE_PLAN_POLICY_MISSING', 'policy must be an object')
    else:
        if policy.get('consecutive_fail_closed') != STOP_RULE_LIMIT:
            err('WAVE_PLAN_STOP_RULE',
                'policy.consecutive_fail_closed must be %d (the F6 stop-loss rule)' % STOP_RULE_LIMIT)
        if not _nonempty(policy.get('on_stop')):
            err('WAVE_PLAN_STOP_RULE', 'policy.on_stop must say what happens when the stop rule trips')
    waves = document.get('waves')
    if not isinstance(waves, list) or not waves:
        err('WAVE_PLAN_WAVES_MISSING', 'waves must be a non-empty list')
        return errors
    seen: set[str] = set()
    for index, wave in enumerate(waves):
        if not isinstance(wave, dict):
            err('WAVE_PLAN_INVALID', 'waves[%d] is not an object' % index)
            continue
        name = wave.get('wave')
        if not _nonempty(name):
            err('WAVE_PLAN_NAME', 'waves[%d].wave must be a non-empty string' % index)
            continue
        if name in seen:
            err('WAVE_PLAN_NAME', 'duplicate wave name %r' % name, name)
        seen.add(name)
        status = wave.get('status')
        if status not in WAVE_STATUSES:
            err('WAVE_PLAN_STATUS', 'status must be one of %r, got %r' % (WAVE_STATUSES, status), name)
        cost = wave.get('cost_benefit')
        if not isinstance(cost, dict):
            err('WAVE_PLAN_COST_BENEFIT_MISSING',
                'cost_benefit must declare estimated/actual STAR hours and rounds', name)
        else:
            estimated = _finite_number(cost.get('estimated_star_hours'),
                                       'cost_benefit.estimated_star_hours', errors, name)
            actual = _finite_number(cost.get('actual_star_hours'),
                                    'cost_benefit.actual_star_hours', errors, name)
            _integer(cost.get('estimated_rounds'), 'cost_benefit.estimated_rounds', errors, name)
            _integer(cost.get('actual_rounds'), 'cost_benefit.actual_rounds', errors, name)
            if status in ('CLOSED', 'ACTIVE') and not _nonempty(cost.get('benefit')):
                err('WAVE_PLAN_BENEFIT_MISSING',
                    'a CLOSED/ACTIVE wave must state the benefit it bought', name)
            if status == 'NOT_ADMITTED':
                if not _nonempty(cost.get('basis')):
                    err('WAVE_PLAN_ESTIMATE_BASIS',
                        'a NOT_ADMITTED wave must say what its proposed estimate is based on', name)
                if actual not in (0.0, None) and actual:
                    err('WAVE_PLAN_NOT_ADMITTED_HAS_SPEND',
                        'a NOT_ADMITTED wave cannot have spent STAR hours (got %r)' % actual, name)
                blockers = wave.get('blocked_by')
                if not isinstance(blockers, list) or not blockers:
                    err('WAVE_PLAN_NOT_ADMITTED_NEEDS_BLOCKERS',
                        'a NOT_ADMITTED wave must list the signed decisions it waits for', name)
            if status == 'CLOSED' and estimated is None:
                err('WAVE_PLAN_COST_BENEFIT_MISSING',
                    'a CLOSED wave must record its estimate next to the actual', name)
            if status == 'CLOSED' and not _nonempty(wave.get('evidence')):
                err('WAVE_PLAN_EVIDENCE_MISSING', 'a CLOSED wave must name its evidence', name)
        if not _stop_condition_ok(wave.get('stop_condition')):
            err('WAVE_PLAN_STOP_CONDITION',
                'stop_condition must declare consecutive_fail_closed=%d and on_stop'
                % STOP_RULE_LIMIT, name)
    return errors


def load_wave_plan(path: str | Path) -> dict:
    return _loads(Path(path))


# --------------------------------------------------------------------------- aggregate


def compute_kpis(case_root: str | Path = CASE_EVIDENCE) -> dict:
    records = discover_run_records(case_root)
    ledger = star_ledger(records)
    kpis = [
        kpi_failed_run_star_share(ledger),
        kpi_review_rounds_per_new_plan(case_root),
        kpi_wall_clock_over_star(records),
        kpi_report_number_traceability(case_root),
    ]
    return {
        'schema_version': 1,
        'record': 'phase3-p30 STAR cost ledger + stop-loss + KPI baseline',
        'generated_at': _iso_now(),
        'generated_by': 'scripts/kpi_ledger.py',
        'compute': 'ZERO_STAR: recomputed from archived JSON only; no STAR process was started or contacted',
        'inputs': {
            'case_evidence_root': _rel(Path(case_root)),
            'run_record_glob': '%s/**/run.json' % _rel(Path(case_root)),
            'review_record_glob': '%s/**/review*.json' % _rel(Path(case_root)),
            'plan_registry_source': _plan_registry_count()[1],
            'plan_registry_note': _plan_registry_count()[2],
            'scripts': {
                'kpi_ledger.py': sha256_file(Path(__file__).resolve()),
                'status_writer.py': (sha256_file(ROOT / 'scripts' / 'status_writer.py')
                                     if (ROOT / 'scripts' / 'status_writer.py').is_file() else None),
            },
        },
        'star_ledger': ledger,
        'kpis': kpis,
        'stop_loss': stop_loss_status([]),
        'notes': [
            ('The archived closeout ledger 17.75 h / 84 unique runs reproduces exactly '
             '(%.11f h) from the %d archived run.json files deduplicated by config_sha256; %d files '
             'are the same config recorded again under nested audit dirs.'
             % (ledger['star_hours_total'], ledger['record_files'], ledger['duplicate_files'])),
            ('failed_run_star_share baseline is measured, NOT passed: %.6f (time) / %.6f (count) '
             'against the <=0.20 target; the per-run components are in star_ledger.runs and '
             'star_ledger.failed_run_paths so the ratio can be re-summed by hand.'
             % (ledger['failed_run_time_share'] or 0.0, ledger['failed_run_count_share'] or 0.0)),
            ('review_rounds_per_new_plan is %.6f under the literal mechanical definition and the '
             'historical ~1.5 does not reproduce under any single definition; see the KPI note. '
             'The component counts that make it recomputable are in kpis[1].components: %d review '
             'records, %d staging reviews, %d non-staging records, %d distinct review-round numbers, '
             '%d letter-suffixed re-review records, %d new plans from %s. A defensible target needs '
             'a denominator policy decision, not another redefinition of the numerator.'
             % (kpis[1]['measured']['review_records_per_plan'],
                kpis[1]['components']['review_record_files_total'],
                kpis[1]['components']['staging_review_records'],
                kpis[1]['components']['non_staging_review_records'],
                kpis[1]['components']['distinct_review_round_numbers'],
                kpis[1]['components']['re_review_files'],
                kpis[1]['components']['new_plans'],
                kpis[1]['components']['plan_registry_source'])),
            ('wall_clock_over_star is %s: %s'
             % (kpis[2].get('status'), kpis[2].get('reason') or kpis[2].get('measured'))),
            ('report_number_traceability is %s: %s'
             % (kpis[3].get('status'), kpis[3].get('reason') or kpis[3].get('measured'))),
            ('The registry/claims tools (scripts/plan_registry.py, scripts/claim_lint.py) landed '
             'while this record was being built: what was actually available is named per component '
             '(plan_registry_source, claim_lint_present), never assumed.'),
        ],
    }


def write_kpi_record(record: dict, path: str | Path, *, force: bool = False) -> dict:
    """Write the record; an existing file is only replaced after its sha256 is captured."""
    target = Path(path)
    previous = None
    if target.exists():
        if not force:
            raise FileExistsError('%s exists; read it first, or pass --force after recording its '
                                  'sha256' % target)
        previous = {'file_sha256': sha256_file(target)}
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record, indent=1, ensure_ascii=False).encode('utf-8') + b'\n'
    target.write_bytes(payload)
    return {'path': _rel(target), 'file_sha256': hashlib.sha256(payload).hexdigest(),
            'bytes': len(payload), 'previous': previous}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--case-root', default=str(CASE_EVIDENCE),
                        help='case evidence root to scan (default: %(default)s)')
    parser.add_argument('--json', action='store_true', help='print the full record as JSON')
    parser.add_argument('--out', help='write the record to this path (refuses an existing file '
                                      'unless --force)')
    parser.add_argument('--force', action='store_true',
                        help='overwrite --out after recording the previous file sha256')
    parser.add_argument('--wave-plan', help='validate a wave plan (cost/benefit + stop-loss) and exit')
    args = parser.parse_args(argv)

    if args.wave_plan:
        try:
            document = load_wave_plan(args.wave_plan)
        except Exception as exc:                     # noqa: BLE001 - a broken record is not a pass
            print(json.dumps({'path': args.wave_plan, 'valid': False,
                              'errors': [{'code': 'WAVE_PLAN_UNPARSEABLE',
                                          'message': str(exc)}]}, indent=1, ensure_ascii=False))
            return 1
        errors = validate_wave_plan(document)
        waves = [{'wave': w.get('wave'), 'status': w.get('status'),
                  'estimated_star_hours': (w.get('cost_benefit') or {}).get('estimated_star_hours'),
                  'actual_star_hours': (w.get('cost_benefit') or {}).get('actual_star_hours')}
                 for w in document.get('waves') or [] if isinstance(w, dict)]
        print(json.dumps({'path': args.wave_plan, 'valid': not errors, 'waves': waves,
                          'errors': errors}, indent=1, ensure_ascii=False))
        return 0 if not errors else 1

    record = compute_kpis(args.case_root)
    if args.out:
        try:
            written = write_kpi_record(record, args.out, force=args.force)
        except FileExistsError as exc:
            print('REFUSED %s' % exc, file=sys.stderr)
            return 1
        record['written'] = written
        print('wrote %s (%d bytes) sha256=%s'
              % (written['path'], written['bytes'], written['file_sha256']))
    if args.json:
        print(json.dumps(record, indent=1, ensure_ascii=False))
    else:
        ledger = record['star_ledger']
        print('STAR ledger: %d record files -> %d unique runs, %.11f h, %d failed '
              '(count share %.6f, time share %.6f)' % (
                  ledger['record_files'], ledger['unique_runs'], ledger['star_hours_total'],
                  ledger['failed_runs'], ledger['failed_run_count_share'],
                  ledger['failed_run_time_share']))
        for kpi in record['kpis']:
            print('%-28s target %-5s measured %-s  [%s]' % (
                kpi['id'], kpi['target'],
                json.dumps(kpi.get('measured'), sort_keys=True) if kpi.get('measured') is not None
                else (kpi.get('reason') or kpi.get('status')),
                kpi.get('verdict') or kpi.get('status')))
        for note in record['notes']:
            print('NOTE %s' % note)
    return 0


if __name__ == '__main__':
    sys.exit(main())
