# -*- coding: utf-8 -*-
"""Pre-reviewed operation catalog and runtime review materialization.

Decision of 2026-10-18: a production operation is reviewed ONCE, when it enters the catalog.
At run time the catalog entry mechanically materializes the `pipeline-review.json` that the
existing gates already consume. Nothing is bypassed: the controller and the Windows runner
still recompute every hash, and any byte drift makes the materialized record fail.

This module does not approve anything by itself. `approved_for_autonomous_run` is set only by
an independent catalog review, and `verify` refuses an unapproved entry.

Usage:
    python scripts/production_catalog.py validate
    python scripts/production_catalog.py list
    python scripts/production_catalog.py verify --entry <id>
    python scripts/production_catalog.py materialize --entry <id> \
        --inputs case.json input.sim --evidence <dir> [--preflight <record.json>]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from air3b_pipeline_trial import SUPPORT_FILES  # noqa: E402

CATALOG = ROOT / 'operations' / 'production-catalog.json'
CHANNELS = frozenset({'v1', 'beta'})
IDENTITY_FIELDS = ('id', 'version', 'pipeline', 'scope', 'release_channel', 'sample_specific',
                   'allowed_case_ids', 'required_input_preflight', 'preflight_sha256')
ADMISSION_FILES = ('scripts/production_catalog.py', 'worker/run_pipeline.ps1', 'worker/run_job.ps1')
FORMAL_SUPPORT_FILES = tuple(dict.fromkeys((*SUPPORT_FILES,
    'scripts/formal_worker.py', 'scripts/casespec.py', 'scripts/part_classification.py',
    'scripts/formal_run_report.py', 'scripts/formal_study_reuse.py', 'operations/catalog.json')))
FORMAL_HASH_FIELDS = ('controller_sha256', 'runner_sha256', 'pipeline_runner_sha256',
                     'pipeline_hashes', 'macro_hashes', 'support_hashes',
                     'resource_contract_sha256', 'runtime_digest',
                     'runtime_manifest_sha256', 'runtime_guard_sha256', 'source_pipeline_sha256')


def entry_identity(entry: dict) -> dict:
    return {key: entry.get(key) for key in IDENTITY_FIELDS}


def repository_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root.resolve()) or not path.is_file():
        raise CatalogError('missing or outside-repository artifact: ' + str(relative))
    return path



class CatalogError(ValueError):
    pass


class CatalogNotApproved(CatalogError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def load_catalog(path: Path = CATALOG) -> dict:
    catalog = read_json(path)
    if not isinstance(catalog, dict) or catalog.get('schema_version') != 1:
        raise CatalogError('Unsupported production catalog schema')
    entries = catalog.get('entries')
    if not isinstance(entries, list):
        raise CatalogError('entries must be a list')
    seen = set()
    for entry in entries:
        validate_entry(entry)
        if entry['id'] in seen:
            raise CatalogError('Duplicate catalog id: ' + entry['id'])
        seen.add(entry['id'])
    return catalog


def validate_entry(entry: dict) -> None:
    required = ('id', 'version', 'pipeline', 'scope', 'release_channel', 'sample_specific',
                'required_input_preflight', 'approved_for_autonomous_run', 'approved_hashes',
                'review_record')
    missing = [key for key in required if key not in entry]
    if missing:
        raise CatalogError('catalog entry missing %s' % missing)
    if not isinstance(entry['id'], str) or not entry['id']:
        raise CatalogError('catalog id must be a non-empty string')
    if entry['release_channel'] not in CHANNELS:
        raise CatalogError('%s: invalid release_channel' % entry['id'])
    if any(not isinstance(entry[key], bool) for key in ('required_input_preflight', 'approved_for_autonomous_run')):
        raise CatalogError('catalog approval/preflight flags must be booleans')
    if not isinstance(entry['sample_specific'], bool):
        raise CatalogError('%s: sample_specific must be boolean' % entry['id'])
    if entry['sample_specific']:
        ids = entry.get('allowed_case_ids')
        if not isinstance(ids, list) or not ids or not all(isinstance(x, str) and x for x in ids):
            raise CatalogError('%s: sample_specific requires allowed_case_ids' % entry['id'])
    if entry['approved_for_autonomous_run']:
        hashes = entry.get('approved_hashes')
        if not isinstance(hashes, dict):
            raise CatalogError('%s: approved_hashes must be an object' % entry['id'])
        for key in ('pipeline_sha256', 'macro_hashes', 'controller_sha256',
                    'runner_sha256', 'support_hashes', 'resource_contract_sha256'):
            if key not in hashes:
                raise CatalogError('%s: approved_hashes lacks %s' % (entry['id'], key))
        if not entry.get('review_record'):
            raise CatalogError('%s: approved entry needs a review_record' % entry['id'])


def verify_entry(entry: dict, root: Path = ROOT) -> dict:
    """Verify the approved bytes against disk; return the actual hashes."""
    validate_entry(entry)
    if entry.get('approved_for_autonomous_run') is not True:
        raise CatalogNotApproved('%s is not approved for autonomous run' % entry['id'])
    hashes = entry['approved_hashes']
    try:
        review = read_json(repository_file(root, entry['review_record']))
    except (OSError, ValueError, TypeError) as exc:
        raise CatalogError('cannot read catalog review: ' + str(exc)) from exc
    if (review.get('approved') is not True or review.get('independent') is not True or
            review.get('catalog_entry_identity') != entry_identity(entry) or
            review.get('approved_hashes') != hashes):
        raise CatalogError('independent catalog review does not approve this exact entry and hashes')
    checks = {}
    pipeline = root / entry['pipeline']
    if not pipeline.is_file():
        raise CatalogError('%s: pipeline missing: %s' % (entry['id'], entry['pipeline']))
    checks['pipeline_sha256'] = sha256(pipeline)
    payload = read_json(pipeline)
    from stage_plan import plan_pipeline
    plan_pipeline(payload)
    from resource_contract import validate_pipeline_resources
    validate_pipeline_resources(payload)
    stage_macros = {name for stage in payload['stages'] for name in stage['macros']}
    if (set(hashes.get('macro_hashes') or {}) != stage_macros or
            payload.get('macro_hashes') != hashes.get('macro_hashes')):
        raise CatalogError('catalog/pipeline/stage macro coverage differs')
    for key in ('EXTERNAL_FLOW_BUDGET_SECONDS', 'AIR3B_SEGMENT_SECONDS'):
        if str(payload.get('env', {}).get(key, '0')) != '0':
            raise CatalogError('production pipeline enables wall-clock stopping: ' + key)
    macro_hashes = {}
    for name, pinned in (hashes.get('macro_hashes') or {}).items():
        path = root / 'macros' / name
        if not path.is_file():
            raise CatalogError('%s: macro missing: %s' % (entry['id'], name))
        actual = sha256(path)
        if actual != pinned:
            raise CatalogError('%s: macro drift: %s' % (entry['id'], name))
        macro_hashes[name] = actual
    checks['macro_hashes'] = macro_hashes
    for label, relative, key in (
            ('controller', 'scripts/air3b_pipeline_trial.py', 'controller_sha256'),
            ('runner', 'scripts/security/trial_air3b_pipeline.ps1', 'runner_sha256'),
            ('resource contract', 'scripts/resource_contract.py', 'resource_contract_sha256')):
        path = root / relative
        if not path.is_file():
            raise CatalogError('%s: %s missing: %s' % (entry['id'], label, relative))
        actual = sha256(path)
        if actual != hashes.get(key):
            raise CatalogError('%s: %s drift' % (entry['id'], label))
        checks[key] = actual
    support = {}
    for relative in SUPPORT_FILES:
        path = root / relative
        if not path.is_file():
            raise CatalogError('%s: support file missing: %s' % (entry['id'], relative))
        actual = sha256(path)
        if actual != (hashes.get('support_hashes') or {}).get(relative):
            raise CatalogError('%s: support drift: %s' % (entry['id'], relative))
        support[relative] = actual
    checks['support_hashes'] = support
    if checks['pipeline_sha256'] != hashes['pipeline_sha256']:
        raise CatalogError('%s: pipeline drift' % entry['id'])
    admission = hashes.get('admission_hashes') or {}
    if set(admission) != set(ADMISSION_FILES):
        raise CatalogError('catalog approval must bind admission tool and both formal runner layers')
    for name, expected in admission.items():
        if sha256(repository_file(root, name)) != expected:
            raise CatalogError('admission artifact drift: ' + name)
    checks['admission_hashes'] = admission
    return checks


def materialize(entry: dict, inputs: list[Path], evidence: Path,
                preflight: Path | None = None, root: Path = ROOT) -> Path:
    checks = verify_entry(entry, root)
    evidence = evidence if evidence.is_absolute() else root / evidence
    input_hashes = {}
    for path in inputs:
        path = path if path.is_absolute() else root / path
        if not path.is_file():
            raise CatalogError('input missing: %s' % path)
        if path.name in input_hashes:
            raise CatalogError('duplicate input basename: ' + path.name)
        input_hashes[path.name] = sha256(path)
    pipeline = read_json(repository_file(root, entry['pipeline']))
    if not input_hashes or input_hashes != pipeline.get('input_hashes'):
        raise CatalogError('submitted inputs differ from the exact approved pipeline input set')
    if entry.get('required_input_preflight'):
        if preflight is None:
            raise CatalogError('%s requires a preflight record' % entry['id'])
        preflight = preflight if preflight.is_absolute() else root / preflight
        if not entry.get('preflight_sha256') or sha256(preflight) != entry['preflight_sha256']:
            raise CatalogError('preflight differs from the independently approved exact-input preflight')
        payload = read_json(preflight)
        if payload.get('status') != 'ok':
            raise CatalogError('preflight record is not ok: %s' % preflight)
        recorded = payload.get('input_hashes')
        if recorded != input_hashes:
            raise CatalogError('preflight input hashes differ from the submitted inputs')
        if payload.get('case_id') != pipeline.get('case_id'):
            raise CatalogError('preflight identity differs from pipeline case_id')
        if entry['sample_specific'] and payload.get('case_id') not in entry['allowed_case_ids']:
            raise CatalogError('case_id is outside the catalog sample scope')
        from run_record_gate import assert_citable
        sources = payload.get('source_records')
        if not isinstance(sources, list) or not sources:
            raise CatalogError('preflight must name its original evidence records')
        for source in sources:
            path = repository_file(root, source['path'])
            if sha256(path) != source.get('sha256'):
                raise CatalogError('preflight source hash drift')
            try:
                citation = assert_citable(path)
                context = citation.get('stage_record') or citation.get('run_state_file')
                if not context or Path(context).resolve() != repository_file(root, source.get('context_path', '')).resolve():
                    raise CatalogError('preflight source attribution differs from approved context')
                if sha256(Path(context)) != source.get('context_sha256'):
                    raise CatalogError('preflight source context hash drift')
            except Exception as exc:
                raise CatalogError('preflight source is not citable: ' + str(exc)) from exc
    elif entry['sample_specific']:
        raise CatalogError('sample-specific entries require an identity-bound preflight')
    evidence.mkdir(parents=True, exist_ok=True)
    record = {
        'schema_version': 1,
        'approved': True,
        'independent': True,
        'reviewer': 'catalog:%s@%s' % (entry['id'], entry['version']),
        'reviewed_at': _dt.datetime.now(_dt.timezone.utc).isoformat(),
        'pipeline_sha256': checks['pipeline_sha256'],
        'controller_sha256': checks['controller_sha256'],
        'runner_sha256': checks['runner_sha256'],
        'macro_hashes': checks['macro_hashes'],
        'input_hashes': input_hashes,
        'support_hashes': checks['support_hashes'],
        'resource_contract_sha256': checks['resource_contract_sha256'],
        'catalog_entry_identity': entry_identity(entry),
        'catalog_review_sha256': sha256(repository_file(root, entry['review_record'])),
        'engineering_verified': False,
        'notes': ['materialized from the pre-reviewed production catalog',
                  'entry=%s version=%s' % (entry['id'], entry['version']),
                  'review_record=%s' % entry.get('review_record')],
    }
    out = evidence / 'pipeline-review.json'
    if out.exists():
        raise CatalogError('refusing to overwrite existing runtime review: ' + str(out))
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return out


def canonicalize(value, algorithm: str = 'semantic-v1'):
    """Typed JSON meaning, with exact finite binary-number ratios (no tolerance)."""
    if algorithm != 'semantic-v1':
        raise CatalogError('unsupported canonicalization algorithm: ' + str(algorithm))
    if value is None:
        return ['null']
    if type(value) is bool:
        return ['bool', value]
    if type(value) in (int, float):
        if type(value) is float and not math.isfinite(value):
            raise CatalogError('non-finite JSON number is not permitted')
        numerator, denominator = value.as_integer_ratio()
        return ['number', str(numerator), str(denominator)]
    if type(value) is str:
        return ['string', value]
    if type(value) is list:
        return ['list', [canonicalize(item, algorithm) for item in value]]
    if type(value) is dict:
        if not all(type(key) is str for key in value):
            raise CatalogError('JSON object keys must be strings')
        return ['dict', [[key, canonicalize(value[key], algorithm)] for key in sorted(value)]]
    raise CatalogError('unsupported JSON value type: ' + type(value).__name__)


def semantic_equal(left, right) -> bool:
    return canonicalize(left) == canonicalize(right)


def canonical_digest(value, algorithm: str = 'legacy') -> str:
    if algorithm == 'semantic-v1':
        value = canonicalize(value, algorithm)
    elif algorithm != 'legacy':
        raise CatalogError('unsupported digest algorithm: ' + str(algorithm))
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def scope_equal(left, right, algorithm):
    if algorithm == 'semantic-v1':
        return semantic_equal(left, right)
    if algorithm == 'legacy':
        return left == right
    raise CatalogError('unsupported digest algorithm: ' + str(algorithm))


def formal_parameter_environment(parameters, declared):
    """semantic-v1 numbers that compare equal must also launch identical env bytes."""
    from backend import validate_stage_parameters
    if not isinstance(parameters, dict):
        raise CatalogError('formal parameters must be an object')
    canonicalize(parameters)
    values = {key: int(value) if type(value) is float and value.is_integer() else value
              for key, value in parameters.items()}
    return validate_stage_parameters(values, declared)


def formal_identity(entry: dict) -> dict:
    return {'catalog_entry_identity': entry_identity(entry), 'formal': entry.get('formal')}


def formal_runtime_pins(root: Path = ROOT) -> dict:
    from backend import build_runtime_manifest, runtime_manifest_bytes, runtime_source_files
    # Same source of truth as Backend.runtime_files(): the declared manifest. Only an isolated fixture
    # root (one that is not this repository and carries no manifest) keeps the old glob, so catalog
    # tests can build throwaway trees; the real repository must never take that branch, or a deleted
    # manifest would silently widen the runtime instead of failing.
    is_repository = root.resolve() == ROOT.resolve()
    if (root / 'runtime-manifest.txt').is_file():
        files = sorted(runtime_source_files(root))
    elif is_repository:
        raise ValueError('runtime-manifest.txt is missing from the repository; the runtime set must be '
                         'declared, not inferred')
    else:
        files = sorted([*(root/'macros').glob('*.java'), *(root/'worker').glob('*.ps1'),
                        *(root/'workflows').glob('*/manifest.json'), root/'scripts/resource_contract.py'])
    manifest = build_runtime_manifest(files, root)
    return {'runtime_digest': manifest['runtime_digest'],
            'runtime_manifest_sha256': hashlib.sha256(runtime_manifest_bytes(manifest)).hexdigest(),
            'runtime_guard_sha256': sha256(root/'worker/runtime_guard.ps1')}


def normalize_formal_steps(steps: list[dict], root: Path = ROOT,
                           algorithm: str = 'legacy') -> list[dict]:
    from backend import validate_stage_parameters
    if not isinstance(steps, list) or not steps:
        raise CatalogError('formal steps must be a nonempty ordered list')
    result = []
    keys = set()
    for step in steps:
        if not isinstance(step, dict):
            raise CatalogError('formal step must be an object')
        if algorithm == 'semantic-v1' and 'env' in step:
            raise CatalogError('semantic-v1 steps use parameters; env is runner-internal')
        workflow, stage = step.get('workflow_id'), step.get('stage')
        if not all(isinstance(x, str) and re.fullmatch(r'[a-z0-9][a-z0-9_-]*', x)
                   for x in (workflow, stage)):
            raise CatalogError('invalid formal workflow/stage')
        if 'key' in step:
            key = step['key']
            if not isinstance(key, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', key) or key in keys:
                raise CatalogError('invalid or duplicate formal step key')
            keys.add(key)
        manifest = read_json(repository_file(root, 'workflows/%s/manifest.json' % workflow))
        if manifest.get('id') != workflow or stage not in (manifest.get('stages') or {}):
            raise CatalogError('formal step is absent from its actual workflow manifest')
        spec = manifest['stages'][stage]
        declared = {item['name']: item for item in (spec.get('parameters') or {}).get('env', [])}
        parameters = step.get('parameters', step.get('env', {}))
        if 'parameters' in step and 'env' in step and not scope_equal(step['parameters'], step['env'], algorithm):
            raise CatalogError('conflicting formal step parameters/env')
        try:
            normalized = (formal_parameter_environment(parameters, declared) if algorithm == 'semantic-v1'
                          else validate_stage_parameters(parameters, declared))
        except ValueError as exc:
            raise CatalogError(str(exc)) from exc
        if algorithm == 'semantic-v1':
            if not isinstance(parameters, dict):
                raise CatalogError('formal parameters must be an object')
            canonicalize(parameters)  # Validate types/finiteness before any env string conversion.
        elif algorithm == 'legacy':
            parameters = normalized
        else:
            raise CatalogError('unsupported digest algorithm: ' + str(algorithm))
        result.append({'workflow_id': workflow, 'stage': stage, 'parameters': parameters})
    return result


def verify_formal_entry(entry: dict, root: Path = ROOT) -> dict:
    """Verify a separately approved formal scope; never reinterpret a trial approval."""
    validate_entry(entry)
    formal = entry.get('formal')
    if not isinstance(formal, dict) or formal.get('approved_for_autonomous_run') is not True:
        raise CatalogNotApproved('independent formal catalog approval is required')
    review = read_json(repository_file(root, formal.get('review_record', '')))
    hashes = {key: formal.get(key) for key in FORMAL_HASH_FIELDS}
    algorithm = formal.get('case_spec_digest_algorithm', 'legacy')
    canonical_digest(None, algorithm)  # Unknown or null algorithms never fall back to legacy.
    if (review.get('approved') is not True or review.get('independent') is not True or
            not scope_equal(review.get('formal_identity'), formal_identity(entry), algorithm) or
            not scope_equal(review.get('approved_hashes'), hashes, algorithm)):
        raise CatalogError('formal independent review does not approve this exact scope and hashes')
    if formal.get('input_ref_policy') != 'library/<sha256>.sim':
        raise CatalogError('formal input_ref_policy must require immutable library references')
    if not isinstance(formal.get('input_sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', formal['input_sha256']):
        raise CatalogError('formal input SHA is missing')
    source = repository_file(root, formal.get('input_source', ''))
    if source.suffix.lower() != '.sim' or sha256(source) != formal['input_sha256']:
        raise CatalogError('formal geometry input drift')
    spec = read_json(repository_file(root, formal.get('case_spec_source', '')))
    if entry['sample_specific'] and spec.get('case_id') not in entry['allowed_case_ids']:
        raise CatalogError('formal CaseSpec case_id is outside the sample-specific allowed_case_ids')
    if canonical_digest(spec, algorithm) != formal.get('case_spec_digest'):
        raise CatalogError('formal CaseSpec template drift')
    from backend import case_ref_digest
    if case_ref_digest(spec.get('case_ref', '')) != formal['input_sha256']:
        raise CatalogError('formal CaseSpec template input differs')
    steps = normalize_formal_steps(formal.get('steps'), root, algorithm)
    if not scope_equal(steps, formal.get('steps'), algorithm):
        raise CatalogError('formal approved steps must be canonical')
    workflows = {step['workflow_id'] for step in steps}
    paths = formal.get('workflow_manifests') or {}
    if set(paths) != workflows or set(formal.get('pipeline_hashes') or {}) != workflows:
        raise CatalogError('formal workflow manifest coverage differs')
    macros = {}
    for workflow, relative in paths.items():
        if relative != 'workflows/%s/manifest.json' % workflow:
            raise CatalogError('formal workflow manifest must use the installed canonical path')
        path = repository_file(root, relative)
        if sha256(path) != formal['pipeline_hashes'][workflow]:
            raise CatalogError('formal workflow manifest drift: ' + workflow)
        manifest = read_json(path)
        for step in steps:
            if step['workflow_id'] != workflow:
                continue
            for name in manifest['stages'][step['stage']]['macros']:
                if not re.fullmatch(r'[A-Za-z0-9_]+\.java', name):
                    raise CatalogError('invalid formal macro name')
                macros[name] = sha256(repository_file(root, 'macros/' + name))
    if macros != formal.get('macro_hashes'):
        raise CatalogError('formal stage macro coverage or bytes differ')
    for name, relative in [('controller_sha256', 'scripts/backend.py'),
                           ('runner_sha256', 'worker/run_job.ps1'),
                           ('pipeline_runner_sha256', 'worker/run_pipeline.ps1'),
                           ('resource_contract_sha256', 'scripts/resource_contract.py')]:
        if sha256(repository_file(root, relative)) != formal.get(name):
            raise CatalogError('formal artifact drift or missing hash: ' + name)
    support = {name: sha256(repository_file(root, name)) for name in FORMAL_SUPPORT_FILES}
    if support != formal.get('support_hashes'):
        raise CatalogError('formal support hashes drift or coverage differs')
    for key, actual in formal_runtime_pins(root).items():
        if actual != formal.get(key):
            raise CatalogError('formal runtime pin drift: ' + key)
    pipeline = read_json(repository_file(root, entry['pipeline']))
    if sha256(root/entry['pipeline']) != formal.get('source_pipeline_sha256'):
        raise CatalogError('formal source pipeline drift')
    if pipeline.get('macro_hashes') != macros:
        raise CatalogError('formal chain differs from the approved source pipeline macros')
    if not formal.get('plan_operations') or len(set(formal['plan_operations'])) != len(formal['plan_operations']):
        raise CatalogError('formal plan_operations must be a nonempty unique ordered list')
    if entry.get('required_input_preflight'):
        preflight = repository_file(root, formal.get('preflight_record', ''))
        if sha256(preflight) != entry.get('preflight_sha256'):
            raise CatalogError('formal approved preflight drift')
        payload = read_json(preflight)
        if (payload.get('status') != 'ok' or payload.get('case_id') != pipeline.get('case_id') or
                payload.get('input_hashes') != pipeline.get('input_hashes')):
            raise CatalogError('formal preflight input/identity differs')
        from run_record_gate import assert_citable
        sources = payload.get('source_records')
        if not isinstance(sources, list) or not sources:
            raise CatalogError('formal preflight lacks original source records')
        for item in sources:
            path = repository_file(root, item['path'])
            citation = assert_citable(path)
            context = citation.get('stage_record') or citation.get('run_state_file')
            expected_context = repository_file(root, item.get('context_path', ''))
            if (sha256(path) != item.get('sha256') or not context or
                    Path(context).resolve() != expected_context or
                    sha256(expected_context) != item.get('context_sha256')):
                raise CatalogError('formal preflight source/context drift')
    return hashes


def formal_review_payload(entry: dict, root: Path = ROOT) -> dict:
    hashes = verify_formal_entry(entry, root)
    formal = entry['formal']
    return {'schema_version': 1, 'approved': True, 'independent': True,
            'reviewer': 'catalog:%s:formal@%s' % (entry['id'], formal['version']),
            **{key: value for key, value in hashes.items() if key != 'source_pipeline_sha256'},
            'pipeline_sha256': hashes['source_pipeline_sha256'],
            'input_hashes': {'input.sim': formal['input_sha256']},
            'case_ref': 'library/%s.sim' % formal['input_sha256'],
            'case_spec_digest': formal['case_spec_digest'], 'approved_steps': formal['steps'],
            **({'case_spec_digest_algorithm': formal['case_spec_digest_algorithm']}
               if 'case_spec_digest_algorithm' in formal else {}),
            'plan_operations': formal['plan_operations'],
            'catalog_entry_identity': entry_identity(entry),
            'formal_catalog': {'entry_id': entry['id'], 'identity': formal_identity(entry),
                               'review_sha256': sha256(repository_file(root, formal['review_record']))},
            'engineering_verified': False}


def verify_formal_review_record(record: dict, root: Path = ROOT) -> bool:
    catalog_origin = (str(record.get('reviewer', '')).startswith('catalog:') or
                      any(key in record for key in ('formal_catalog', 'catalog_entry_identity',
                                                    'catalog_review_sha256')))
    if not catalog_origin:
        return False
    marker = record.get('formal_catalog')
    if not isinstance(marker, dict):
        raise CatalogError('formal catalog lineage is missing; trial reviews cannot authorize formal runs')
    entries = load_catalog(root/'operations/production-catalog.json')['entries']
    entry = next((entry for entry in entries if entry['id'] == marker.get('entry_id')), None)
    if entry is None:
        raise CatalogError('formal catalog entry is missing')
    expected = formal_review_payload(entry, root)
    for key, value in expected.items():
        if key not in record or not scope_equal(record[key], value, expected.get('case_spec_digest_algorithm', 'legacy')):
            raise CatalogError('formal materialized review differs: ' + key)
    return True


def validate_formal_submission(review_ref, case_ref, steps, case_spec, plan_operations,
                               root: Path = ROOT) -> str | None:
    if not isinstance(review_ref, str) or not review_ref.strip():
        return  # The backend's ordinary review loader supplies the missing-review error.
    path = Path(review_ref)
    if not path.is_absolute():
        path = root/path
    if not path.is_file():
        return
    record = read_json(path)
    if not verify_formal_review_record(record, root):
        return  # Independent, non-catalog reviews retain their original admission contract.
    algorithm = record.get('case_spec_digest_algorithm', 'legacy')
    if (case_ref != record['case_ref'] or canonical_digest(case_spec, algorithm) != record['case_spec_digest'] or
            not scope_equal(normalize_formal_steps(steps, root, algorithm), record['approved_steps'], algorithm) or
            not scope_equal(plan_operations, record['plan_operations'], algorithm)):
        raise CatalogError('formal submitted CaseSpec/input/steps/parameters/plan differ from approved scope')
    return algorithm


def materialize_formal(entry: dict, case_ref: str, steps: list[dict], case_spec: dict,
                       evidence: Path, root: Path = ROOT) -> Path:
    from backend import case_ref_digest
    record = formal_review_payload(entry, root)
    algorithm = record.get('case_spec_digest_algorithm', 'legacy')
    if (case_ref_digest(case_ref) != entry['formal']['input_sha256'] or
            case_ref != record['case_ref'] or canonical_digest(case_spec, algorithm) != record['case_spec_digest'] or
            not scope_equal(normalize_formal_steps(steps, root, algorithm), record['approved_steps'], algorithm)):
        raise CatalogError('formal actual input/CaseSpec/steps/parameters differ from approved scope')
    evidence = evidence if evidence.is_absolute() else root/evidence
    evidence.mkdir(parents=True, exist_ok=True)
    out = evidence/'pipeline-review.json'
    if out.exists():
        raise CatalogError('refusing to overwrite existing formal runtime review')
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Production operation catalog.')
    parser.add_argument('command', choices=('validate', 'list', 'verify', 'materialize',
                                           'verify-formal', 'materialize-formal'))
    parser.add_argument('--catalog', type=Path, default=CATALOG)
    parser.add_argument('--entry')
    parser.add_argument('--inputs', nargs='+', type=Path, default=[])
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--preflight', type=Path)
    parser.add_argument('--case-ref')
    parser.add_argument('--steps', type=Path)
    parser.add_argument('--case-spec', type=Path)
    args = parser.parse_args(argv)
    catalog_path = args.catalog if args.catalog.is_absolute() else ROOT / args.catalog
    catalog = load_catalog(catalog_path)
    entries = {entry['id']: entry for entry in catalog['entries']}
    if args.command == 'validate':
        print('catalog OK: %d entries' % len(entries))
        return 0
    if args.command == 'list':
        for entry in entries.values():
            print('%-40s %-8s approved=%s sample_specific=%s'
                  % (entry['id'], entry['release_channel'],
                     entry['approved_for_autonomous_run'], entry['sample_specific']))
        return 0
    if not args.entry or args.entry not in entries:
        print('unknown entry: %r' % args.entry, file=sys.stderr)
        return 1
    entry = entries[args.entry]
    try:
        if args.command == 'verify-formal':
            verify_formal_entry(entry)
            print('formal entry verified: ' + entry['id'])
            return 0
        if args.command == 'materialize-formal':
            if not all((args.evidence, args.case_ref, args.steps, args.case_spec)):
                raise CatalogError('--evidence, --case-ref, --steps and --case-spec are required')
            out = materialize_formal(entry, args.case_ref, read_json(args.steps),
                                     read_json(args.case_spec), args.evidence)
            print('wrote ' + str(out))
            return 0
        if args.command == 'verify':
            verify_entry(entry)
            print('entry verified: ' + entry['id'])
            return 0
        if args.command == 'materialize':
            if not args.evidence:
                print('--evidence is required for materialize', file=sys.stderr)
                return 1
            out = materialize(entry, args.inputs, args.evidence, args.preflight)
            print('wrote ' + str(out))
            return 0
    except CatalogError as exc:
        print('REFUSED: %s' % exc, file=sys.stderr)
        return 1
    return 1


if __name__ == '__main__':
    sys.exit(main())
