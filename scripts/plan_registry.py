"""Plan registry: PURE loader for the ad-hoc MRF chain plans (P2.3 G5 / phase 3 P30).

Why this module exists (measured defect, 2026-10-30): the 87 MRF plans lived in
``scripts/run_mrf_chain.py`` as a literal dict plus ~40 ``copy.deepcopy`` builders plus a per-plan
contract ternary chain inside ``prepare()``.  Adding or editing a plan therefore changed the
CONTROLLER bytes, and every reviewed staging pins ``scripts/run_mrf_chain.py`` by sha256, so a plan
edit invalidated the review pins even though no reviewed behaviour had changed ("bytes changed after
review").  Plans are now data: one JSON file per plan under
``campaigns/uav-2026/plans/<campaign>/``; the engine is ``PLANS = plan_registry.load_all(<campaign>)``.

Design rules enforced here (all fail closed with :class:`PlanRegistryError`):

* the loader contains NO campaign plan ids; the campaign manifest (``_campaign.json``) lists the
  legacy plan ids, so "is this a new plan?" is data, not code;
* ``extends`` template inheritance: a plan file may inherit from a template (deep merge, explicit
  override wins) so a new plan is one small JSON file, never a controller edit;
* the 87 legacy plans are fully materialised (no inheritance) and their contracts are frozen;
* F2 spike gate: a NEW plan must carry ``spike_evidence`` = a *spike record* (existing file, matching
  sha256, duration <= the manifest budget, verdict PASS).  ``prepare()`` calls
  :func:`assert_preparable` -> :func:`assert_spike_evidence` BEFORE any staging write, so a plan
  without a cheap pre-spend spike is refused without touching evidence bytes;
* F1 gate derivation: a NEW plan must not inherit literal ``ct_tolerance``/``cp_tolerance`` bands;
  those bands are DERIVED from the staged CaseSpec with :func:`derive_signed_tolerance_bands`
  (exact IEEE double ``max(abs_floor, rel * ref)``, pinned as a +/-eps band).  Legacy plans keep
  their literal frozen gates byte-identically.

The loader is pure: it reads plan JSON, the campaign manifest, and (only for a new plan's derived
gates) the CaseSpec.  It writes nothing.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLANS_REL = 'campaigns/uav-2026/plans'
MANIFEST_NAME = '_campaign.json'
TOLERANCE_FIELDS = ('ct_tolerance', 'cp_tolerance')
REFERENCE_FIELDS = ('ct_ref', 'cp_ref')
DERIVATION_RULES = ('signed_tolerance_bands',)
DEFAULT_SPIKE_POLICY = {'max_duration_min': 40, 'required_verdict': 'PASS'}

# Keys a plan file (after template merge) may carry.  Unknown keys are refused so a typo cannot
# silently change nothing.
_ALLOWED_PLAN_KEYS = {
    'schema_version', 'plan', 'extends', 'run_id', 'job_dir', 'macros', 'inputs', 'steps',
    'record_gates', 'required_downloads', 'optional_downloads', 'scope_note', 'contract',
    'spike_evidence', 'gates_derivation',
}
_REQUIRED_PLAN_KEYS = {
    'plan', 'run_id', 'job_dir', 'macros', 'inputs', 'steps', 'record_gates',
    'required_downloads', 'optional_downloads', 'contract',
}
_RESERVED_KEYS = {'legacy', 'spike_gate'}  # loader-owned, never read from plan data
_STEP_REQUIRED = ('name', 'macro', 'input', 'output', 'records')


class PlanRegistryError(RuntimeError):
    """A plan registry / plan policy violation.  Callers fail closed (engine converts to SystemExit)."""


def _read_json(path: Path, what: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise PlanRegistryError('%s unreadable/unparsable: %s (%s)' % (what, path, exc))
    if not isinstance(value, dict):
        raise PlanRegistryError('%s is not a JSON object: %s' % (what, path))
    return value


def _deep_merge(base, override):
    """Template inheritance: dicts merge recursively; every other type (incl. lists) is replaced."""
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _deep_merge(merged[key], value) if key in merged else value
        return merged
    return override


def _campaign_dir(campaign: str, root: Path | None, plans_dir: Path | None) -> Path:
    if plans_dir is not None:
        return Path(plans_dir)
    return (Path(root) if root is not None else REPO_ROOT) / PLANS_REL / campaign


def _resolve_extends(value, plans_dir: Path, stack: tuple) -> dict:
    """Resolve an ``extends`` chain to the merged template data (template file -> dict)."""
    if not isinstance(value, str) or not value:
        raise PlanRegistryError('extends must be a non-empty template file name, got %r' % (value,))
    name = value if value.endswith('.json') else value + '.json'
    if Path(name).is_absolute() or '..' in Path(name).parts:
        raise PlanRegistryError('extends must stay inside the plans directory: %r' % value)
    path = plans_dir / name
    if path in stack:
        raise PlanRegistryError('extends cycle: %s' % ' -> '.join(str(p.name) for p in stack + (path,)))
    template = _read_json(path, 'plan template')
    parent = template.pop('extends', None)
    merged = _resolve_extends(parent, plans_dir, stack + (path,)) if parent is not None else {}
    return _deep_merge(merged, template)


def _rel_input_path(value, plan_id: str, name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise PlanRegistryError('%s input %s must be a repo-relative path or null' % (plan_id, name))
    path = Path(value)
    if path.is_absolute() or '..' in path.parts:
        raise PlanRegistryError('%s input %s must stay inside the repo: %r' % (plan_id, name, value))
    return path


def _check_plan_structure(plan_id: str, data: dict) -> None:
    unknown = sorted(set(data) - _ALLOWED_PLAN_KEYS - {'extends'})
    if unknown:
        raise PlanRegistryError('%s has unknown plan keys %r' % (plan_id, unknown))
    missing = sorted(_REQUIRED_PLAN_KEYS - set(data))
    if missing:
        raise PlanRegistryError('%s is missing required keys %r' % (plan_id, missing))
    if data.get('schema_version') not in (None, 1):
        raise PlanRegistryError('%s unsupported schema_version %r' % (plan_id, data.get('schema_version')))
    for key in ('run_id', 'job_dir'):
        if not isinstance(data[key], str) or not data[key]:
            raise PlanRegistryError('%s.%s must be a non-empty string' % (plan_id, key))
    if not isinstance(data['macros'], list) or not data['macros'] or \
            not all(isinstance(m, str) and m for m in data['macros']):
        raise PlanRegistryError('%s.macros must be a non-empty list of file names' % plan_id)
    if not isinstance(data['steps'], list) or not data['steps']:
        raise PlanRegistryError('%s.steps must be a non-empty list' % plan_id)
    for index, step in enumerate(data['steps']):
        if not isinstance(step, dict):
            raise PlanRegistryError('%s.steps[%d] is not an object' % (plan_id, index))
        for key in _STEP_REQUIRED:
            if key not in step:
                raise PlanRegistryError('%s.steps[%d] is missing %r' % (plan_id, index, key))
        for key in ('name', 'macro'):
            if not isinstance(step[key], str) or not step[key]:
                raise PlanRegistryError('%s.steps[%d].%s must be a non-empty string' % (plan_id, index, key))
        for key in ('input', 'output'):
            if step[key] is not None and not isinstance(step[key], str):
                raise PlanRegistryError('%s.steps[%d].%s must be a string or null' % (plan_id, index, key))
        if not isinstance(step['records'], list):
            raise PlanRegistryError('%s.steps[%d].records must be a list' % (plan_id, index))
    if [step['macro'] for step in data['steps']] != data['macros']:
        raise PlanRegistryError('%s.macros must equal the step macros (chain-of-custody)' % plan_id)
    if not isinstance(data['inputs'], dict):
        raise PlanRegistryError('%s.inputs must be an object' % plan_id)
    if 'case_spec.json' not in data['inputs']:
        raise PlanRegistryError('%s.inputs must declare case_spec.json (null = the live CaseSpec)' % plan_id)
    for name, value in data['inputs'].items():
        if value is not None:
            _rel_input_path(value, plan_id, name)
    if not isinstance(data['record_gates'], list):
        raise PlanRegistryError('%s.record_gates must be a list' % plan_id)
    for gate in data['record_gates']:
        if not isinstance(gate, dict) or not isinstance(gate.get('record'), str) or \
                not isinstance(gate.get('fields'), list):
            raise PlanRegistryError('%s has a malformed record gate: %r' % (plan_id, gate))
        for field in gate['fields']:
            if not isinstance(field, dict) or set(field) != {'field', 'op', 'value'}:
                raise PlanRegistryError('%s gate %s has a malformed field %r'
                                        % (plan_id, gate['record'], field))
            if field['op'] not in ('eq', 'ge', 'le', 'gt', 'lt', 'ne'):
                raise PlanRegistryError('%s gate %s uses unknown op %r'
                                        % (plan_id, gate['record'], field['op']))
    for key in ('required_downloads', 'optional_downloads'):
        if not isinstance(data[key], list) or not all(isinstance(x, str) for x in data[key]):
            raise PlanRegistryError('%s.%s must be a list of file names' % (plan_id, key))
    if 'scope_note' in data and not isinstance(data['scope_note'], str):
        raise PlanRegistryError('%s.scope_note must be a string' % plan_id)
    contract = data['contract']
    if not isinstance(contract, dict):
        raise PlanRegistryError('%s.contract must be an object' % plan_id)
    if contract.get('plan') != plan_id:
        raise PlanRegistryError('%s.contract.plan is %r' % (plan_id, contract.get('plan')))
    pairs = [[step['name'], step['macro']] for step in data['steps']]
    if [list(pair) if isinstance(pair, (list, tuple)) else pair for pair in contract.get('steps', [])] != pairs:
        raise PlanRegistryError('%s.contract.steps does not match the plan steps' % plan_id)


def derive_signed_tolerance_bands(gate: dict, rule: dict, eps: float = 1e-12) -> None:
    """Replace a gate's CT/CP tolerance fields with +/-eps bands holding the exact IEEE double.

    Mirrors the reviewed engine pattern (``_tolerance_band``/``_apply_signed_tolerance_bands``): the
    audit macro emits ``max(abs_floor, rel * ref)`` in double arithmetic (0.08*0.0656 =
    0.005248000000000001); an ``eq`` gate would reject a 1-ulp difference after the solve.  The band
    is derived, never inherited.
    """
    refs = {field['field']: field['value'] for field in gate['fields']
            if field['field'] in REFERENCE_FIELDS}
    missing = [name for name in REFERENCE_FIELDS if name not in refs]
    if missing:
        raise PlanRegistryError('gate %s cannot derive %s without %r'
                                % (gate.get('record'), TOLERANCE_FIELDS, missing))
    ct_tol = max(rule['ct_abs_floor'], rule['ct_rel'] * refs['ct_ref'])
    cp_tol = max(rule['cp_abs_floor'], rule['cp_rel'] * refs['cp_ref'])
    band = [{'field': 'ct_tolerance', 'op': 'ge', 'value': ct_tol - eps},
            {'field': 'ct_tolerance', 'op': 'le', 'value': ct_tol + eps},
            {'field': 'cp_tolerance', 'op': 'ge', 'value': cp_tol - eps},
            {'field': 'cp_tolerance', 'op': 'le', 'value': cp_tol + eps}]
    gate['fields'] = [field for field in gate['fields'] if field['field'] not in TOLERANCE_FIELDS]
    gate['fields'] += band


def _case_spec_path(spec: dict, derivation: dict, root: Path, manifest: dict) -> Path:
    declared = derivation.get('case_spec')
    if not isinstance(declared, str) or not declared:
        raise PlanRegistryError('%s.gates_derivation.case_spec must be a repo-relative path'
                                % spec['plan'])
    path = _rel_input_path(declared, spec['plan'], 'gates_derivation.case_spec')
    staged = spec['inputs'].get('case_spec.json')
    if staged is not None:
        if Path(staged).resolve() != (root / path).resolve():
            raise PlanRegistryError(
                '%s stages case_spec %s but derives its tolerance bands from %s'
                % (spec['plan'], staged, declared))
    else:
        default = manifest.get('default_case_spec')
        if default is not None and Path(default) != path:
            raise PlanRegistryError(
                '%s declares no staged case_spec and its derivation %s differs from the campaign '
                'default %s' % (spec['plan'], declared, default))
    return root / path


def _apply_gate_derivation(plan_id: str, spec: dict, root: Path, manifest: dict) -> None:
    """F1: a NEW plan's signed-tolerance bands must be derived, never inherited as literals."""
    derivation = spec.get('gates_derivation')
    literals = sorted({field['field'] for gate in spec['record_gates'] for field in gate['fields']
                       if field['field'] in TOLERANCE_FIELDS})
    if literals:
        raise PlanRegistryError(
            '%s is a NEW plan and must not carry literal %r: declare gates_derivation so the band is '
            'derived from the staged CaseSpec' % (plan_id, literals))
    if derivation is None:
        references = sorted({field['field'] for gate in spec['record_gates'] for field in gate['fields']
                             if field['field'] in REFERENCE_FIELDS})
        if references:
            raise PlanRegistryError(
                '%s is a NEW plan whose gates declare %r but no gates_derivation: the signed tolerance '
                'band must be derived from the staged CaseSpec, never inherited' % (plan_id, references))
        return
    if not isinstance(derivation, dict):
        raise PlanRegistryError('%s.gates_derivation must be an object' % plan_id)
    unknown = sorted(set(derivation) - {'rule', 'case_spec', 'records', 'eps'})
    if unknown:
        raise PlanRegistryError('%s.gates_derivation has unknown keys %r' % (plan_id, unknown))
    if derivation.get('rule') not in DERIVATION_RULES:
        raise PlanRegistryError('%s.gates_derivation.rule %r is not implemented (%r)'
                                % (plan_id, derivation.get('rule'), DERIVATION_RULES))
    records = derivation.get('records')
    if not isinstance(records, list) or not records or not all(isinstance(r, str) for r in records):
        raise PlanRegistryError('%s.gates_derivation.records must be a non-empty list of record names'
                                % plan_id)
    eps = derivation.get('eps', 1e-12)
    if not isinstance(eps, (int, float)) or isinstance(eps, bool) or eps < 0:
        raise PlanRegistryError('%s.gates_derivation.eps must be a non-negative number' % plan_id)
    case_spec_path = _case_spec_path(spec, derivation, root, manifest)
    try:
        case_spec = json.loads(Path(case_spec_path).read_text(encoding='utf-8'))
        rule = case_spec['acceptance']['reference_tolerance']
    except (OSError, ValueError, KeyError) as exc:
        raise PlanRegistryError('%s derives gates from an unreadable CaseSpec %s (%s)'
                                % (plan_id, case_spec_path, exc))
    gates = {gate['record']: gate for gate in spec['record_gates']}
    for record in records:
        gate = gates.get(record)
        if gate is None:
            raise PlanRegistryError('%s.gates_derivation names %r but the plan has no such gate'
                                    % (plan_id, record))
        derive_signed_tolerance_bands(gate, rule, eps)


def _expected_derived_bands(spec: dict, root: Path, manifest: dict) -> dict:
    """Recompute the derived tolerance fields (used by assert_preparable on an already-loaded spec)."""
    derivation = spec['gates_derivation']
    eps = derivation.get('eps', 1e-12)
    path = _case_spec_path(spec, derivation, root, manifest)
    try:
        rule = json.loads(Path(path).read_text(encoding='utf-8'))['acceptance']['reference_tolerance']
    except (OSError, ValueError, KeyError) as exc:
        raise PlanRegistryError('%s derives gates from an unreadable CaseSpec %s (%s)'
                                % (spec['plan'], path, exc))
    expected = {}
    gates = {gate['record']: gate for gate in spec['record_gates']}
    for record in derivation['records']:
        gate = gates[record]
        probe = {'record': record, 'fields': [dict(field) for field in gate['fields']
                                              if field['field'] not in TOLERANCE_FIELDS]}
        derive_signed_tolerance_bands(probe, rule, eps)
        expected[record] = [(field['field'], field['op'], field['value']) for field in probe['fields']
                            if field['field'] in TOLERANCE_FIELDS]
    return expected


def assert_spike_evidence(plan_id: str, spec: dict, root=None) -> dict:
    """F2 gate (pure, unit-testable): return the spike record, or refuse a plan that has none.

    Legacy plans (manifest-listed) are grandfathered with an honest ``legacy_pre_split`` marker: their
    validation is the plan's own executed run/staging record, not a pre-spend spike.  A NEW plan must
    prove a cheap spike happened (file exists, sha256 matches, duration <= the campaign budget, verdict
    PASS).  ``prepare()`` calls this before any mkdir/write, so a refused plan leaves no bytes behind.
    """
    root = Path(root) if root is not None else REPO_ROOT
    evidence = spec.get('spike_evidence')
    if spec.get('legacy') is True:
        if not isinstance(evidence, dict) or evidence.get('kind') != 'legacy_pre_split':
            raise PlanRegistryError(
                '%s is a legacy plan but has no legacy_pre_split spike_evidence marker' % plan_id)
        if not isinstance(evidence.get('reason'), str) or not evidence['reason'].strip():
            raise PlanRegistryError('%s legacy_pre_split marker needs an honest reason' % plan_id)
        return dict(evidence)
    policy = dict(DEFAULT_SPIKE_POLICY)
    policy.update(spec.get('spike_gate') or {})
    if not isinstance(evidence, dict):
        raise PlanRegistryError(
            '%s is a NEW plan and has no spike_evidence: a FAILED/absent spike must stop the plan '
            'before any staging write' % plan_id)
    if evidence.get('kind') != 'spike_record':
        raise PlanRegistryError('%s.spike_evidence.kind must be spike_record for a new plan, got %r'
                                % (plan_id, evidence.get('kind')))
    relative = evidence.get('path')
    if not isinstance(relative, str) or not relative:
        raise PlanRegistryError('%s.spike_evidence.path must be a repo-relative file path' % plan_id)
    record_path = _rel_input_path(relative, plan_id, 'spike_evidence.path')
    digest = evidence.get('sha256')
    if not isinstance(digest, str) or len(digest) != 64:
        raise PlanRegistryError('%s.spike_evidence.sha256 must be 64 hex characters' % plan_id)
    duration = evidence.get('duration_min')
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration < 0:
        raise PlanRegistryError('%s.spike_evidence.duration_min must be a non-negative number'
                                % plan_id)
    max_minutes = policy['max_duration_min']
    if duration > max_minutes:
        raise PlanRegistryError('%s spike took %r min > budget %r min: split the spike, do not spend '
                                'the full run on an unproven plan'
                                % (plan_id, duration, max_minutes))
    if evidence.get('verdict') != policy['required_verdict']:
        raise PlanRegistryError('%s spike verdict %r != %r: a failed spike must stop the plan'
                                % (plan_id, evidence.get('verdict'), policy['required_verdict']))
    source = root / record_path
    if not source.is_file():
        raise PlanRegistryError('%s spike record missing: %s' % (plan_id, record_path))
    import hashlib
    actual = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual != digest.lower():
        raise PlanRegistryError('%s spike record sha256 %s != declared %s: the spike evidence changed '
                                'after the plan pinned it' % (plan_id, actual, digest))
    record = dict(evidence)
    record['resolved_path'] = str(source)
    record['actual_sha256'] = actual
    return record


def _manifest_for(plan_id: str, root: Path) -> dict:
    """Find the campaign manifest owning ``plan_id`` (data-driven; no plan ids in code)."""
    plans_root = root / PLANS_REL
    if not plans_root.is_dir():
        return {}
    for manifest_path in sorted(plans_root.glob('*/' + MANIFEST_NAME)):
        if not (manifest_path.parent / (plan_id + '.json')).is_file():
            continue
        return _read_json(manifest_path, 'campaign manifest')
    return {}


def assert_preparable(plan_id: str, spec: dict, root=None) -> None:
    """Fail-closed validation run by ``prepare()`` before it creates/writes anything.

    F2 (spike evidence) plus the F1 derivation consistency check for NEW plans: a spec that was not
    loaded through :func:`load_all` (or was edited afterwards) must not reach a staging write.
    """
    root = Path(root) if root is not None else REPO_ROOT
    assert_spike_evidence(plan_id, spec, root)
    if spec.get('legacy') is True:
        return  # legacy plans keep their literal frozen gates (identity)
    derivation = spec.get('gates_derivation')
    literals = sorted({field['field'] for gate in spec['record_gates'] for field in gate['fields']
                       if field['field'] in TOLERANCE_FIELDS})
    references = sorted({field['field'] for gate in spec['record_gates'] for field in gate['fields']
                         if field['field'] in REFERENCE_FIELDS})
    if derivation is None and not literals:
        if references:
            raise PlanRegistryError(
                '%s is a NEW plan whose gates declare %r but no gates_derivation: refuse before staging'
                % (plan_id, references))
        return  # no signed CT/CP band to derive
    if not isinstance(derivation, dict):
        raise PlanRegistryError('%s is a NEW plan with CT/CP tolerance gates but no gates_derivation'
                                % plan_id)
    expected = _expected_derived_bands(spec, root, _manifest_for(plan_id, root))
    for record, fields in expected.items():
        gate = next(g for g in spec['record_gates'] if g['record'] == record)
        actual = sorted((f['field'], f['op'], f['value']) for f in gate['fields']
                        if f['field'] in TOLERANCE_FIELDS)
        if actual != sorted(fields):
            raise PlanRegistryError(
                '%s gate %s tolerance band is not the CaseSpec-derived band (expected %r, got %r): '
                're-run plan_registry.load_all instead of hand-editing the band'
                % (plan_id, record, sorted(fields), actual))


def _load_one(plan_id: str, path: Path, plans_dir: Path, manifest: dict, legacy: set, root: Path) -> dict:
    data = _read_json(path, 'plan %s' % plan_id)
    parent = data.pop('extends', None)
    resolved = _resolve_extends(parent, plans_dir, (path,)) if parent is not None else {}
    data = _deep_merge(resolved, data)
    data['plan'] = plan_id
    _check_plan_structure(plan_id, data)
    reserved = sorted(set(data) & _RESERVED_KEYS)
    if reserved:
        raise PlanRegistryError('%s cannot set loader-owned keys %r' % (plan_id, reserved))
    spec = {key: value for key, value in data.items() if key != 'schema_version'}
    spec['inputs'] = {name: (root / _rel_input_path(value, plan_id, name)) if value is not None else None
                      for name, value in data['inputs'].items()}
    spec['legacy'] = plan_id in legacy
    spec['spike_gate'] = dict(DEFAULT_SPIKE_POLICY)
    spec['spike_gate'].update(manifest.get('spike_gate') or {})
    if not spec['legacy']:
        _apply_gate_derivation(plan_id, spec, root, manifest)
    elif 'gates_derivation' in spec:
        raise PlanRegistryError('%s is a legacy plan: it must keep literal frozen gates, not '
                                'gates_derivation' % plan_id)
    override = manifest.get('legacy_contract_overrides', {}).get(plan_id)
    if override is not None:
        expected = dict({'plan': plan_id,
                         'steps': [[step['name'], step['macro']] for step in spec['steps']]}, **override)
        if expected != spec['contract']:
            raise PlanRegistryError('%s.contract diverges from the pinned legacy contract override'
                                    % plan_id)
    return spec


def load_all(campaign: str, root=None, plans_dir=None) -> dict:
    """Load every plan of ``campaign`` -> ``{plan_id: spec}``.

    ``root`` resolves repo-relative input paths and the manifest's campaign data; ``plans_dir``
    overrides the plan directory (used by tests to load a throwaway registry).  Pure: writes nothing.
    """
    root = Path(root).resolve() if root is not None else REPO_ROOT
    campaign_dir = _campaign_dir(campaign, root, plans_dir)
    manifest = _read_json(campaign_dir / MANIFEST_NAME, 'campaign manifest')
    if manifest.get('campaign') != campaign:
        raise PlanRegistryError('manifest campaign %r != requested %r'
                                % (manifest.get('campaign'), campaign))
    legacy = set(manifest.get('legacy_plans') or [])
    plans = {}
    for path in sorted(campaign_dir.glob('*.json')):
        if path.name.startswith('_'):
            continue  # manifest + templates are not plans
        plan_id = path.stem
        if plan_id in plans:
            raise PlanRegistryError('duplicate plan id %r' % plan_id)
        plans[plan_id] = _load_one(plan_id, path, campaign_dir, manifest, legacy, root)
    if not plans:
        raise PlanRegistryError('no plans found in %s' % campaign_dir)
    return plans


def contract_overrides(campaign: str, root=None, plans_dir=None) -> dict:
    """The pinned legacy contract overrides (the reviewed ``CONTRACT_OVERRIDES`` API, incl. orphans)."""
    root = Path(root).resolve() if root is not None else REPO_ROOT
    campaign_dir = _campaign_dir(campaign, root, plans_dir)
    manifest = _read_json(campaign_dir / MANIFEST_NAME, 'campaign manifest')
    return {plan_id: dict(override)
            for plan_id, override in (manifest.get('legacy_contract_overrides') or {}).items()}
