"""Versioned engineering intent and fail-closed plan validation.

The model may propose a CaseSpec, but only installed operations may execute it.
Units are explicit SI values so an imported part's display units cannot silently
change the physical problem.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 2026-10-30 family-gate regime (F3).  plan_mesh_gate only imports mesh_acceptance, so there is no
# import cycle; the rule implementation lives there, this module only exposes the opt-in switch.
import plan_mesh_gate

SCENARIOS = {"external_static", "propeller_mrf", "rotating_transient"}
DEFAULT_LIMITS = {"logical_cores": 64, "memory_gb": 64, "wall_hours": 48}
DEFAULT_QUALITY = {
    "mesh_levels": 3,
    "domain_sensitivity": True,
    "time_step_sensitivity": True,
    "force_window_rel_change": 0.01,
    "sensitivity_rel_change": 0.02,
}

# Keep plan blockers aligned with the project roadmap so a valid but
# non-executable CaseSpec points to reusable work, not another case-specific
# workaround.
OPERATION_PHASES = {
    "1_plan_and_knowledge": ("audit_geometry",),
    "2_general_preprocessing": (
        "classify_parts", "build_external_domain", "build_rotating_region", "configure_rotation",
        "assign_boundaries", "configure_physics", "mesh", "mesh_qa",
    ),
    "3_persistent_execution": ("solve_pilot", "solve_converged", "export_evidence", "report"),
    "4_scenario_acceptance": ("domain_study", "mesh_study", "time_step_study"),
    "5_macro_and_release": (),
}

PLUGIN = Path(__file__).resolve().parents[1]
OPERATION_INDEX = PLUGIN / "operations" / "catalog.json"
SUPPORTED_CATALOG_VERSIONS = (1, 2)

# A RUN mechanism is a real executor: it can be handed a whole plan and drive STAR itself. An
# offline_tool is a pre/post step of a plan, never an executor, so it can contribute capabilities to a
# mechanism but can never by itself make a plan executable.
RUN_MECHANISMS = ("workflow_stage", "restricted_trial")


def _code_bindings(entry: dict) -> list[dict]:
    """The run mechanisms an offline tool is REALLY invoked by, verified against the files on disk.

    R11 let a tool declare `applies_to_run_mechanisms`, and that free declaration was enough to count the
    tool as part of a mechanism's plan. R12 found the difference matters: `classify_parts` declared
    `restricted_trial` while no restricted-trial entry calls it at all, so a plan could look complete
    through a step nothing ever runs. A binding now names a file AND a symbol, and the symbol must
    really be defined there; a renamed or deleted entry point stops covering the requirement instead of
    staying green. This is not a new gate: it is the resolver deciding availability, as before.
    """
    bindings = []
    for item in entry.get('invoked_by') or []:
        if not isinstance(item, dict):
            continue
        mechanism, rel, symbol = item.get('mechanism'), item.get('file'), item.get('symbol')
        if mechanism not in RUN_MECHANISMS or not isinstance(rel, str) or not isinstance(symbol, str):
            continue
        path = PLUGIN / rel
        if not path.is_file():
            continue
        if re.search(r'^\s*def\s+%s\b' % re.escape(symbol), path.read_text(encoding='utf-8',
                                                                           errors='replace'), re.M):
            bindings.append({'mechanism': mechanism, 'file': rel, 'symbol': symbol})
    return bindings


def load_catalog() -> dict:
    """The operation registry as written on disk. Schema 1 is the pre-R11 flat list."""
    data = json.loads(OPERATION_INDEX.read_text(encoding='utf-8'))
    if data.get('schema_version') not in SUPPORTED_CATALOG_VERSIONS:
        raise ValueError('Unsupported operation catalog version')
    return data


def installed_operations() -> list[dict]:
    """The declared operations.

    Kept as the public listing entry point (the MCP tool `list_operations` returns it), but it is
    deliberately NO LONGER the availability rule: R11 moved that to resolve_operations(), which checks
    every entry against the live workflow catalog and the files on disk. A name in this file is a
    DECLARATION, not a capability.
    """
    return load_catalog()['operations']


def _workflow_catalog() -> dict:
    """The live installed workflows, imported lazily so casespec stays importable on its own."""
    from backend import workflow_catalog
    return workflow_catalog()


def resolve_operations(operation_ids: list[str] | None = None, case: str | None = None) -> dict:
    """Resolve declared operations to REAL call paths, with the four capability levels kept apart.

    Why this exists: `validate_plan` used to treat an operation as available when its id appeared in the
    catalog, so adding a name was enough to make a plan look executable. The branch-1 handover for R11
    (test-evidence/static-external-flow/round11-operation-integration.json) named that as the reason it
    could not register the executed counterparts itself. Availability is now decided here, by code:

      implementation        the (workflow_id, stage) resolves in the live manifest AND every macro named
                            by that stage exists on disk, or the named tool/script/pipeline exists
      verified_on_case      an evidence entry names THIS case and its record is CITABLE by
                            scripts/run_record_gate.py; a record from a FAILED run never counts
      general_acceptance    taken from the catalog's own classification (positive/negative examples)
      engineering           ALWAYS NOT_CLAIMED_BY_WIRING: wiring proves an operation runs, never that the
                            physics is valid

    Nothing here is cached: an entry whose stage or macro disappears becomes UNRESOLVED immediately.
    """
    data = load_catalog()
    catalog_error = None
    try:
        workflows = _workflow_catalog()
    except Exception as exc:                                            # noqa: BLE001 - fail closed
        workflows = {}
        catalog_error = '%s: %s' % (type(exc).__name__, exc)
    wanted = set(operation_ids) if operation_ids is not None else None
    resolved: dict[str, dict] = {}
    unresolved: list[dict] = []
    for entry in data['operations']:
        op_id = entry.get('id')
        if not isinstance(op_id, str):
            unresolved.append({'id': op_id, 'reason': 'catalog entry has no id'})
            continue
        if wanted is not None and op_id not in wanted:
            continue
        mechanism = entry.get('mechanism', 'workflow_stage')
        status = {'id': op_id, 'title': entry.get('title'), 'mechanism': mechanism,
                  'granularity': entry.get('granularity'), 'scope_class': entry.get('scope_class', 'generic'),
                  'provides': list(entry.get('provides') or []),
                  'provides_on_own_case': list(entry.get('provides_on_own_case') or []),
                  'invoked_by': [],
                  # Where this operation sits in a plan: 'pre' (before the graph), 'post' (after it), or
                  # None for an executor. A tool with no role cannot be placed in a plan.
                  'plan_role': entry.get('plan_role'),
                  'depends_on_fixed_names': bool(entry.get('depends_on_fixed_names')),
                  'fixed_names': sorted(entry.get('fixed_names') or []),
                  'general_acceptance': entry.get('general_acceptance', 'COMPILED_ONLY'),
                  'engineering': 'NOT_CLAIMED_BY_WIRING',
                  'scope': entry.get('scope'), 'applicable_when': entry.get('applicable_when'),
                  'inputs': entry.get('inputs'), 'outputs': list(entry.get('outputs') or []),
                  'parameters': entry.get('parameters') or {},
                  'call_path': None, 'verified_on_current_case': False,
                  'verified_on_current_case_reason': None, 'evidence': list(entry.get('evidence') or []),
                  'implementation': 'RESOLVED'}
        if catalog_error is not None:
            status['implementation'] = 'UNRESOLVED'
            status['reason'] = 'the installed workflow catalog could not be read: ' + catalog_error
        elif mechanism == 'workflow_stage':
            workflow_id, stage = entry.get('workflow_id'), entry.get('stage')
            manifest = workflows.get(workflow_id)
            if manifest is None:
                status['implementation'] = 'UNRESOLVED'
                status['reason'] = 'workflow is not installed: %r' % (workflow_id,)
            elif stage not in (manifest.get('stages') or {}):
                status['implementation'] = 'UNRESOLVED'
                status['reason'] = 'stage %r is not in workflow %r' % (stage, workflow_id)
            else:
                spec = manifest['stages'][stage]
                macros = list(spec.get('macros') or [])
                absent = [name for name in macros if not (PLUGIN / 'macros' / name).is_file()]
                if not macros:
                    status['implementation'] = 'UNRESOLVED'
                    status['reason'] = 'stage %r declares no macro' % (stage,)
                elif absent:
                    status['implementation'] = 'UNRESOLVED'
                    status['reason'] = 'macro(s) missing from disk: ' + ', '.join(absent)
                else:
                    outputs = spec.get('macro_outputs')
                    status['call_path'] = {'workflow_id': workflow_id, 'stage': stage, 'macros': macros,
                                           'markers': list(spec.get('markers') or []),
                                           'required': list(spec.get('required') or []),
                                           'macro_outputs': list(outputs) if outputs else None,
                                           'output_sim': spec.get('output_sim'),
                                           'postcheck': spec.get('postcheck')}
        elif mechanism == 'offline_tool':
            entry_point = entry.get('entry') or {}
            files = list(entry_point.get('files') or [])
            absent = [name for name in files if not (PLUGIN / name).is_file()]
            if not files:
                status['implementation'] = 'UNRESOLVED'
                status['reason'] = 'offline tool entry names no file to check'
            elif absent:
                status['implementation'] = 'UNRESOLVED'
                status['reason'] = 'file(s) missing: ' + ', '.join(absent)
            else:
                bindings = _code_bindings(entry)
                status['invoked_by'] = bindings
                status['call_path'] = {'tool': entry_point.get('mcp_tool'), 'files': files,
                                       # `applies_to_run_mechanisms` is kept for display, but it no longer
                                       # decides coverage: `invoked_by` is the verified list.
                                       'applies_to_run_mechanisms': list(entry_point.get('applies_to_run_mechanisms') or []),
                                       'invoked_by': bindings}
                if not bindings:
                    status['coverage_note'] = ('no run mechanism really invokes this tool: every declared '
                                               'invoked_by entry is missing from disk, so it cannot '
                                               'complete a plan step')
        elif mechanism == 'restricted_trial':
            entry_point = entry.get('entry') or {}
            files = [entry_point.get('pipeline'), entry_point.get('inputs'),
                     entry_point.get('controller'), entry_point.get('runner')]
            absent = [name for name in files if not name or not (PLUGIN / name).is_file()]
            if absent:
                status['implementation'] = 'UNRESOLVED'
                status['reason'] = 'trial entry file(s) missing: ' + ', '.join(str(x) for x in absent)
            else:
                status['call_path'] = {'command': entry_point.get('command'), 'pipeline': entry_point.get('pipeline'),
                                       'inputs': entry_point.get('inputs'), 'controller': entry_point.get('controller'),
                                       'runner': entry_point.get('runner')}
        else:
            status['implementation'] = 'UNRESOLVED'
            status['reason'] = 'unknown mechanism %r' % (mechanism,)
        # verified_on_current_case: the evidence must name this case AND be citable.
        if status['implementation'] == 'RESOLVED':
            if not case:
                status['verified_on_current_case_reason'] = (
                    'no case identity was supplied, so no evidence can be bound to the current case')
            else:
                from run_record_gate import CITABLE, citation_status
                named = [item for item in status['evidence'] if isinstance(item, dict) and item.get('case') == case]
                if not named:
                    status['verified_on_current_case_reason'] = 'no evidence entry names case %r' % (case,)
                else:
                    citable, problems = [], []
                    for item in named:
                        record = item.get('record')
                        path = PLUGIN / record if isinstance(record, str) else None
                        if path is None or not path.is_file():
                            problems.append('%s (record missing)' % (record,))
                        elif path.suffix.lower() == '.json':
                            verdict = citation_status(path)
                            if verdict.get('state') == CITABLE:
                                citable.append(record)
                            else:
                                problems.append('%s (%s)' % (record, verdict.get('state')))
                        else:
                            citable.append(record)
                    status['verified_on_current_case'] = bool(citable)
                    status['verified_on_current_case_reason'] = (
                        'citable evidence for this case: ' + ', '.join(citable[:3]) if citable
                        else 'evidence for this case is not citable: ' + ', '.join(problems[:3]))
        resolved[op_id] = status
        if status['implementation'] != 'RESOLVED':
            unresolved.append({'id': op_id, 'reason': status.get('reason')})
    if wanted is not None:
        missing_ids = [op_id for op_id in operation_ids if op_id not in resolved]
        for op_id in missing_ids:
            unresolved.append({'id': op_id, 'reason': 'no catalog entry declares this operation'})
    return {'schema_version': data.get('schema_version'), 'operations': resolved,
            'unresolved': unresolved, 'reserved_interfaces': list(data.get('reserved_interfaces') or []),
            'capabilities': dict(data.get('capabilities') or {}),
            'not_registered_on_purpose': dict(data.get('_not_registered_on_purpose') or {})}


@dataclass(frozen=True)
class Validation:
    valid: bool
    executable: bool
    digest: str | None
    missing: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    available_operations: list[str] = field(default_factory=list)
    required_operations: list[str] = field(default_factory=list)
    phase_readiness: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    # R11: the four capability levels are reported SEPARATELY and never collapsed into `executable`.
    #   operations_detail        per required operation: who serves it, by which mechanism, and the level
    #   missing_reasons          why an unserved operation is unserved (reserved / sample-only / none)
    #   mechanism_coverage       per RUN mechanism, what it can and cannot cover end to end
    #   reserved_interfaces      the mesh_study / domain_study contracts window-1 has not delivered yet
    #   unresolved_operations    catalog entries that do NOT resolve to a real call path (fail closed)
    operations_detail: dict[str, dict] = field(default_factory=dict)
    missing_reasons: dict[str, str] = field(default_factory=dict)
    mechanism_coverage: dict[str, dict] = field(default_factory=dict)
    reserved_interfaces: list[dict] = field(default_factory=list)
    unresolved_operations: list[dict] | None = None
    # The required operations that have NO available call path. Kept separate from `missing`, which is the
    # CaseSpec's own missing input list: conflating them is how a plan gap used to look like a data gap.
    unavailable_operations: list[str] = field(default_factory=list)
    # R12: `executable` is decided by the execution plan that would actually be submitted, never by the
    # union of every mechanism's capabilities. `plan_operations` is that plan (empty = "no plan selected
    # yet"); `plan_mechanism` names the single executor it uses; `plan_blocker` says why it is not
    # executable, in terms of a real execution path rather than a list of names.
    plan_operations: list[str] = field(default_factory=list)
    plan_mechanism: str | None = None
    plan_blocker: str | None = None
    # The plan members that did NOT resolve to a generic call path. Surfaced as its own field (not only
    # inside `plan_blocker`) because a caller must be able to see the offending plan ids even when the
    # plan was also refused for another reason.
    plan_unresolved_operations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return vars(self)


def _finite(value: Any, name: str, errors: list[str], positive: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        errors.append(f"{name} must be a finite number")
    elif positive and value <= 0:
        errors.append(f"{name} must be greater than zero")


def _vector(value: Any, name: str, errors: list[str], nonzero: bool = False) -> None:
    if not isinstance(value, list) or len(value) != 3:
        errors.append(f"{name} must contain three SI components")
        return
    for index, item in enumerate(value):
        _finite(item, f"{name}[{index}]", errors)
    if nonzero and not errors and sum(float(x) ** 2 for x in value) == 0:
        errors.append(f"{name} must be nonzero")


def required_operations(scenario: str) -> list[str]:
    base = ["audit_geometry", "classify_parts", "build_external_domain",
            "assign_boundaries", "configure_physics", "mesh", "mesh_qa",
            "solve_pilot", "solve_converged", "domain_study", "mesh_study",
            "export_evidence", "report"]
    if scenario != "external_static":
        base[3:3] = ["build_rotating_region", "configure_rotation"]
    if scenario == "rotating_transient":
        base[base.index("export_evidence"):base.index("export_evidence")] = ["time_step_study"]
    return base


def physics_branch(numerics: dict) -> str:
    """Explicit model selection; never infer a model from a missing/unknown token."""
    if not isinstance(numerics, dict):
        raise ValueError('numerics must be an object')
    pair = (numerics.get('density_model'), numerics.get('turbulence_model'))
    if pair == ('constant', 'laminar'):
        return 'constant_laminar'
    if pair == ('ideal_gas', 'SST (Menter) k-omega'):
        return 'ideal_gas_sst'
    raise ValueError('Unsupported or missing numerics.density_model/turbulence_model combination')


def validate_case_spec(spec: dict, installed_operations: set[str] | None = None,
                       audit: dict | None = None, case: str | None = None,
                       operation_registry: dict | None = None,
                       plan_operations: list[str] | None = None,
                       require_mesh_acceptance: bool = False) -> Validation:
    """Validate a CaseSpec and say SEPARATELY what is implemented, verified, generally accepted and
    engineering-valid.

    `require_mesh_acceptance` (2026-10-30 family standard, F3) is an EXPLICIT opt-in: when True a
    CaseSpec that is not byte-identical to the grandfathered split snapshot must declare
    quality.mesh_acceptance, and a missing/incomplete block becomes a validation ERROR.  The rule is
    implemented once in scripts/plan_mesh_gate.py (require_declared_acceptance); default False keeps
    every existing caller's behaviour, so the frozen 2026-10-30 CaseSpec is unaffected.

    `installed_operations` is an explicit caller-supplied override used only for offline what-if
    analysis; when it is None (the MCP path) availability comes from resolve_operations(), which
    requires a real call path and refuses to let a sample-specific capability serve a generic
    requirement.

    `plan_operations` is the execution plan that WOULD be submitted (the operations of one run graph).
    When it is given, coverage is judged on that plan: every named operation must resolve, the plan must
    use exactly ONE run mechanism, and every required capability must be provided by a member of it.
    When it is not given, a mechanism counts as executable only if a SINGLE operation of it covers the
    whole requirement (an explicit plan is what covers a multi-stage formal run). Coverage is never the
    union of different mechanisms' capabilities - that is the false green R12 removes.
    """
    missing: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(spec, dict):
        return Validation(False, False, None, errors=["CaseSpec must be a JSON object"])
    if spec.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    scenario = spec.get("scenario")
    if scenario not in SCENARIOS:
        errors.append("scenario must be external_static, propeller_mrf, or rotating_transient")
    for key in ("case_ref", "objective", "model_length_unit", "medium"):
        if not spec.get(key):
            missing.append(key)
    if spec.get("model_length_unit") and spec["model_length_unit"] not in {"m", "mm", "cm", "in"}:
        errors.append("model_length_unit must be m, mm, cm, or in")
    liquid = spec.get('medium') == 'single_component_liquid'
    s1 = (spec.get('validation_scope') == 'S1_CAPABILITY_PILOT'
          and spec.get('sample_role') == 'independent_calibration'
          and scenario == 'external_static' and liquid)
    if spec.get('validation_scope') == 'S1_CAPABILITY_PILOT' and not s1:
        errors.append('S1_CAPABILITY_PILOT requires external_static independent_calibration single_component_liquid')
    if spec.get("medium") and spec["medium"] != "air" and not liquid:
        errors.append("first release supports air only")
    numerics = spec.get('numerics', {})
    if 'numerics' in spec and not isinstance(numerics, dict):
        errors.append('numerics must be an object')
    if liquid or (isinstance(numerics, dict) and
                  any(key in numerics for key in ('density_model', 'turbulence_model'))):
        try:
            branch = physics_branch(numerics)
            if liquid and branch != 'constant_laminar':
                errors.append('single_component_liquid requires constant/laminar')
            if not liquid and branch != 'ideal_gas_sst':
                errors.append('air requires ideal_gas/SST (Menter) k-omega')
        except ValueError as exc:
            errors.append(str(exc))
    if liquid:
        if scenario != 'external_static' or spec.get('model_length_unit') != 'm':
            errors.append('constant/laminar requires external_static SI metre geometry')
        if not isinstance(numerics, dict) or numerics.get('energy_equation') is not False:
            errors.append('constant/laminar requires numerics.energy_equation=false')
    flow = spec.get("flow", {})
    if not isinstance(flow, dict):
        errors.append("flow must be an object")
        flow = {}
    if "velocity_m_s" not in flow:
        missing.append("flow.velocity_m_s")
    else:
        _vector(flow["velocity_m_s"], "flow.velocity_m_s", errors)
    if "pressure_pa" not in flow:
        missing.append("flow.pressure_pa")
    else:
        _finite(flow["pressure_pa"], "flow.pressure_pa", errors, positive=True)
    if "temperature_k" not in flow and not liquid:
        missing.append("flow.temperature_k")
    elif "temperature_k" in flow:
        _finite(flow["temperature_k"], "flow.temperature_k", errors, positive=True)
    if liquid:
        for key in ('rho_kg_m3', 'mu_pa_s'):
            if key not in flow:
                missing.append('flow.' + key)
            else:
                _finite(flow[key], 'flow.' + key, errors, positive=True)
        geo = spec.get('geometry') if isinstance(spec.get('geometry'), dict) else {}
        diam, area = geo.get('characteristic_length_m'), geo.get('reference_area_m2')
        _finite(diam, 'geometry.characteristic_length_m', errors, positive=True)
        _finite(area, 'geometry.reference_area_m2', errors, positive=True)
        v, rho, mu = flow.get('velocity_m_s'), flow.get('rho_kg_m3'), flow.get('mu_pa_s')
        quantities = [diam, area, rho, mu]
        numeric = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0
        if all(numeric(x) for x in quantities) and isinstance(v, list) and len(v) == 3 and all(
                isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in v):
            reynolds = rho * math.sqrt(sum(x*x for x in v)) * diam / mu
            if not math.isclose(reynolds, 100.0, rel_tol=1e-10, abs_tol=1e-10):
                errors.append('constant/laminar requires recomputed Re_D=100')
            if 'Re_D' in flow and (not numeric(flow['Re_D']) or not math.isclose(flow['Re_D'], 100.0, rel_tol=1e-10)):
                errors.append('flow.Re_D must agree with recomputed Re_D=100')
            if not math.isclose(area, math.pi*diam*diam/4, rel_tol=1e-10):
                errors.append('sphere reference_area_m2 must equal pi*D^2/4')
            if geo.get('body_shape') != 'sphere' or not numeric(geo.get('body_radius_m')) or not math.isclose(
                    diam, 2*geo['body_radius_m'], rel_tol=1e-10):
                errors.append('constant/laminar requires sphere D=2*body_radius_m')
    if "turbulence_intensity" in flow:
        _finite(flow["turbulence_intensity"], "flow.turbulence_intensity", errors, positive=True)
        if (isinstance(flow["turbulence_intensity"], (int, float)) and
                not isinstance(flow["turbulence_intensity"], bool) and
                math.isfinite(flow["turbulence_intensity"]) and flow["turbulence_intensity"] >= 1):
            errors.append("flow.turbulence_intensity must be less than 1")
    if "turbulent_viscosity_ratio" in flow:
        _finite(flow["turbulent_viscosity_ratio"], "flow.turbulent_viscosity_ratio", errors, positive=True)
    boundary_roles = spec.get("boundary_roles")
    if boundary_roles is not None:
        if not isinstance(boundary_roles, dict):
            errors.append("boundary_roles must be an object")
        else:
            for key in ("region", "inlet", "outlet"):
                if not isinstance(boundary_roles.get(key), str) or not boundary_roles[key].strip():
                    errors.append(f"boundary_roles.{key} must be a nonempty name")
            if (isinstance(boundary_roles.get("inlet"), str) and
                    boundary_roles.get("inlet") == boundary_roles.get("outlet")):
                errors.append("boundary_roles.inlet and outlet must differ")
            box_face_roles = boundary_roles.get("box_face_roles")
            if box_face_roles is not None:
                allowed = {"inlet", "outlet", "symmetry", "farfield"}
                if not isinstance(box_face_roles, list) or len(box_face_roles) != 6:
                    errors.append("boundary_roles.box_face_roles must be a list of six roles")
                elif any(not isinstance(role, str) or role not in allowed for role in box_face_roles):
                    errors.append("boundary_roles.box_face_roles entries must be inlet/outlet/symmetry/farfield")
                elif box_face_roles.count("inlet") != 1 or box_face_roles.count("outlet") != 1:
                    errors.append("boundary_roles.box_face_roles needs exactly one inlet and one outlet")
            declared_geometry = spec.get("geometry")
            declared_body = (declared_geometry.get("body_part_name", "BODY")
                             if isinstance(declared_geometry, dict) else "BODY")
            expected_names = {
                "region": spec.get("region_name", "AUTO_EXTERNAL_FLOW"),
                "inlet": "AUTO_INLET", "outlet": "AUTO_OUTLET",
                "body_wall": spec.get("body_wall_boundary_name", "AUTO_" + str(declared_body) + "_WALL"),
            }
            for key, expected in expected_names.items():
                if scenario == "external_static" and key in boundary_roles and boundary_roles[key] != expected:
                    errors.append(f"boundary_roles.{key} must equal implemented name {expected}")
    geometry = spec.get("geometry", {})
    if not isinstance(geometry, dict):
        errors.append("geometry must be an object")
        geometry = {}
    if not isinstance(geometry.get("body_parts"), list) or not geometry["body_parts"]:
        missing.append("geometry.body_parts")
    elif not all(isinstance(name, str) and name.strip() for name in geometry["body_parts"]):
        errors.append("geometry.body_parts must contain nonempty part names")
    body_shape = geometry.get("body_shape", "sphere")
    if body_shape not in {"sphere", "cylinder", "arbitrary"}:
        errors.append("geometry.body_shape must be sphere, cylinder, or arbitrary")
    if body_shape == "arbitrary":
        if isinstance(geometry.get("body_parts"), list) and len(geometry["body_parts"]) != 1:
            errors.append("geometry.body_parts arbitrary audit currently requires exactly one body")
        expected_bounds = geometry.get("expected_bounds_m")
        if (not isinstance(expected_bounds, list) or len(expected_bounds) != 6
                or any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) for value in expected_bounds)):
            errors.append("geometry.expected_bounds_m must contain six finite SI coordinates")
        elif any(expected_bounds[axis] >= expected_bounds[axis + 3] for axis in range(3)):
            errors.append("geometry.expected_bounds_m must have positive extent on every axis")
        _finite(geometry.get("expected_volume_m3"), "geometry.expected_volume_m3", errors, positive=True)
        for key in ("expected_bounds_tolerance_m", "expected_volume_relative_tolerance"):
            if key in geometry:
                _finite(geometry[key], "geometry." + key, errors, positive=True)
    if scenario in {"propeller_mrf", "rotating_transient"}:
        rotation = spec.get("rotation", {})
        if not isinstance(rotation, dict):
            errors.append("rotation must be an object")
            rotation = {}
        if "axis" not in rotation:
            missing.append("rotation.axis")
        else:
            _vector(rotation["axis"], "rotation.axis", errors, nonzero=True)
        if "center_m" not in rotation:
            missing.append("rotation.center_m")
        else:
            _vector(rotation["center_m"], "rotation.center_m", errors)
        if "rpm" not in rotation:
            missing.append("rotation.rpm")
        else:
            _finite(rotation["rpm"], "rotation.rpm", errors)
            if rotation["rpm"] == 0:
                errors.append("rotation.rpm must be nonzero")
        if not isinstance(geometry.get("rotor_parts"), list) or not geometry["rotor_parts"]:
            missing.append("geometry.rotor_parts")
        elif not all(isinstance(name, str) and name.strip() for name in geometry["rotor_parts"]):
            errors.append("geometry.rotor_parts must contain nonempty part names")
    elif "rotor_parts" in geometry and not all(isinstance(name, str) and name.strip() for name in geometry["rotor_parts"]):
        errors.append("geometry.rotor_parts must contain nonempty part names")
    domain = spec.get("domain", {})
    if not isinstance(domain, dict):
        errors.append("domain must be an object")
        domain = {}
    outer_bounds_m = domain.get("outer_bounds_m")
    if outer_bounds_m is None:
        missing.append("domain.outer_bounds_m")
    elif (not isinstance(outer_bounds_m, list) or len(outer_bounds_m) != 6
          or any(isinstance(value, bool) or not isinstance(value, (int, float))
                 or not math.isfinite(value) for value in outer_bounds_m)):
        errors.append("domain.outer_bounds_m must contain six finite SI coordinates")
    elif any(outer_bounds_m[axis] >= outer_bounds_m[axis + 3] for axis in range(3)):
        errors.append("domain.outer_bounds_m must have positive extent on every axis")
    shape = domain.get("shape", "box")
    if shape not in {"box", "cylinder"}:
        errors.append("domain.shape must be box or cylinder")
    if shape == "cylinder":
        radius = domain.get("cylinder_radius_m")
        z_min = domain.get("cylinder_z_min_m")
        z_max = domain.get("cylinder_z_max_m")
        center = domain.get("cylinder_center_xy_m")
        for key, value in (("cylinder_radius_m", radius), ("cylinder_z_min_m", z_min),
                           ("cylinder_z_max_m", z_max)):
            _finite(value, "domain." + key, errors, positive=key == "cylinder_radius_m")
        if not isinstance(center, list) or len(center) != 2:
            errors.append("domain.cylinder_center_xy_m must contain two SI coordinates")
        else:
            for axis, value in enumerate(center):
                _finite(value, f"domain.cylinder_center_xy_m[{axis}]", errors)
        if (isinstance(z_min, (int, float)) and not isinstance(z_min, bool)
                and isinstance(z_max, (int, float)) and not isinstance(z_max, bool)
                and math.isfinite(z_min) and math.isfinite(z_max) and z_min >= z_max):
            errors.append("domain cylinder must have positive axial extent")
        if (isinstance(radius, (int, float)) and not isinstance(radius, bool) and math.isfinite(radius)
                and isinstance(center, list) and len(center) == 2
                and all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                        for value in center)
                and isinstance(z_min, (int, float)) and not isinstance(z_min, bool) and math.isfinite(z_min)
                and isinstance(z_max, (int, float)) and not isinstance(z_max, bool) and math.isfinite(z_max)
                and isinstance(outer_bounds_m, list) and len(outer_bounds_m) == 6
                and all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                        for value in outer_bounds_m)):
            expected = [center[0] - radius, center[1] - radius, z_min,
                        center[0] + radius, center[1] + radius, z_max]
            if any(abs(float(outer_bounds_m[i]) - expected[i]) > 1e-9 for i in range(6)):
                errors.append("domain.outer_bounds_m must equal the cylinder bounding box")
    rotation_zone = spec.get("rotation_zone")
    if rotation_zone is not None:
        if scenario not in {"propeller_mrf", "rotating_transient"} or not isinstance(rotation_zone, dict):
            errors.append("rotation_zone requires a rotating scenario and an object")
        else:
            if rotation_zone.get("shape") != "cylinder":
                errors.append("rotation_zone.shape must be cylinder")
            zone_center = rotation_zone.get("center_xy_m")
            zone_radius = rotation_zone.get("radius_m")
            zone_zmin = rotation_zone.get("z_min_m")
            zone_zmax = rotation_zone.get("z_max_m")
            if not isinstance(zone_center, list) or len(zone_center) != 2:
                errors.append("rotation_zone.center_xy_m must contain two SI coordinates")
            else:
                for axis, value in enumerate(zone_center):
                    _finite(value, f"rotation_zone.center_xy_m[{axis}]", errors)
            for key, value in (("radius_m", zone_radius), ("z_min_m", zone_zmin),
                               ("z_max_m", zone_zmax)):
                _finite(value, "rotation_zone." + key, errors, positive=key == "radius_m")
            if (all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                    for v in (zone_zmin, zone_zmax)) and zone_zmin >= zone_zmax):
                errors.append("rotation_zone must have positive axial extent")
            if (shape == "cylinder" and isinstance(center, list) and len(center) == 2 and
                    isinstance(zone_center, list) and len(zone_center) == 2 and
                    all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                        for v in center + zone_center + [radius, zone_radius, z_min, z_max, zone_zmin, zone_zmax])):
                center_offset = math.hypot(zone_center[0] - center[0], zone_center[1] - center[1])
                if (zone_radius <= 0 or center_offset + zone_radius >= radius or
                        zone_zmin <= z_min or zone_zmax >= z_max):
                    errors.append("rotation_zone must lie strictly inside the outer cylinder")
    mesh = spec.get("mesh")
    if mesh is not None:
        if not isinstance(mesh, dict):
            errors.append("mesh must be an object")
        else:
            size_keys = ("base_size_m", "blade_target_size_m", "blade_min_size_m",
                         "interface_target_size_m", "interface_min_size_m")
            for key in size_keys:
                _finite(mesh.get(key), "mesh." + key, errors, positive=True)
            if all(isinstance(mesh.get(key), (int, float)) and not isinstance(mesh[key], bool)
                   and math.isfinite(mesh[key]) and mesh[key] > 0 for key in size_keys):
                if (not mesh["blade_min_size_m"] < mesh["blade_target_size_m"] < mesh["base_size_m"] or
                        not mesh["interface_min_size_m"] < mesh["interface_target_size_m"] < mesh["base_size_m"]):
                    errors.append("mesh minimum, target and base sizes must increase in order")
            layers = mesh.get("prism_layers")
            if isinstance(layers, bool) or not isinstance(layers, int) or not 3 <= layers <= 20:
                errors.append("mesh.prism_layers must be an integer from 3 to 20")
            rotating_layers = mesh.get("rotating_prism_layers")
            if rotating_layers is not None:
                if (isinstance(rotating_layers, bool) or not isinstance(rotating_layers, int) or
                        not 3 <= rotating_layers <= 20):
                    errors.append("mesh.rotating_prism_layers must be an integer from 3 to 20")
    limits_input = spec.get("limits", {})
    if not isinstance(limits_input, dict):
        errors.append("limits must be an object")
        limits_input = {}
    limits = dict(DEFAULT_LIMITS)
    limits.update(limits_input)
    for key, ceiling in {"logical_cores": 64, "memory_gb": 256, "wall_hours": 48}.items():
        value = limits[key]
        _finite(value, f"limits.{key}", errors, positive=True)
        if isinstance(value, (int, float)) and value > ceiling:
            errors.append(f"limits.{key} exceeds configured maximum {ceiling}")
    quality_input = spec.get("quality", {})
    if not isinstance(quality_input, dict):
        errors.append("quality must be an object")
        quality_input = {}
    quality = dict(DEFAULT_QUALITY)
    quality.update(quality_input)
    if s1:
        if type(quality.get('mesh_levels')) is not int or quality['mesh_levels'] != 1:
            errors.append('S1_CAPABILITY_PILOT requires mesh_levels=1')
        if quality.get('domain_sensitivity') is not False or quality.get('time_step_sensitivity') is not False:
            errors.append('S1_CAPABILITY_PILOT requires domain_sensitivity=false and time_step_sensitivity=false')
    elif isinstance(quality.get("mesh_levels"), bool) or not isinstance(quality.get("mesh_levels"), int) or quality["mesh_levels"] < 3:
        errors.append("engineering verification requires at least three mesh levels")
    if not s1 and not quality.get("domain_sensitivity"):
        errors.append("engineering verification requires domain sensitivity")
        if scenario == "rotating_transient" and not quality.get("time_step_sensitivity"):
            errors.append("transient engineering verification requires time-step sensitivity")
    if scenario == "rotating_transient":
        transient = spec.get("transient", {})
        if not isinstance(transient, dict):
            errors.append("transient must be an object")
            transient = {}
        for key in ("time_step_s", "physical_duration_s", "steps_per_revolution",
                    "stability_window_revolutions"):
            if key not in transient:
                missing.append("transient." + key)
        for key in ("time_step_s", "physical_duration_s", "stability_window_revolutions"):
            if key in transient:
                _finite(transient[key], "transient." + key, errors, positive=True)
        if "steps_per_revolution" in transient:
            steps = transient["steps_per_revolution"]
            if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
                errors.append("transient.steps_per_revolution must be a positive integer")
        if (isinstance(transient.get("steps_per_revolution"), int) and
                not isinstance(transient.get("steps_per_revolution"), bool) and
                isinstance(transient.get("time_step_s"), (int, float)) and
                not isinstance(transient.get("time_step_s"), bool) and
                isinstance(spec.get("rotation"), dict) and
                isinstance(spec["rotation"].get("rpm"), (int, float)) and
                not isinstance(spec["rotation"].get("rpm"), bool) and
                spec["rotation"]["rpm"] != 0):
            expected_dt = 60.0 / (abs(spec["rotation"]["rpm"]) * transient["steps_per_revolution"])
            if not math.isclose(transient["time_step_s"], expected_dt, rel_tol=1e-6, abs_tol=1e-12):
                errors.append("transient.time_step_s must equal 60/(abs(rotation.rpm)*steps_per_revolution)")
        if (isinstance(transient.get("time_step_s"), (int, float)) and
                not isinstance(transient.get("time_step_s"), bool) and
                isinstance(transient.get("physical_duration_s"), (int, float)) and
                not isinstance(transient.get("physical_duration_s"), bool) and
                transient["time_step_s"] > 0 and transient["physical_duration_s"] > 0 and
                transient["physical_duration_s"] / transient["time_step_s"] < 1):
            errors.append("transient.physical_duration_s must include at least one time step")
        targets = transient.get("target_quantities")
        if not isinstance(targets, list) or not targets or not all(
                isinstance(name, str) and name.strip() for name in targets):
            missing.append("transient.target_quantities")
    for key in ("mesh_min_cell_quality", "mesh_min_face_validity", "mesh_min_volume_change"):
        if key in quality:
            value = quality[key]
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not 0 < value <= 1):
                errors.append(f"quality.{key} must be a finite number in (0, 1]")
    # 2026-10-30 user decision: mesh acceptance is a FAMILY property (family passes the UNCHANGED hard gates),
    # not a single-mesh property.  The block is OPTIONAL so that already-frozen CaseSpecs stay byte-identical, but
    # when present it must be complete: a partial block would let a family be declared without a rule to judge it.
    if "mesh_acceptance" in quality:
        acceptance = quality["mesh_acceptance"]
        if not isinstance(acceptance, dict):
            errors.append("quality.mesh_acceptance must be an object")
        else:
            family = acceptance.get("family")
            if not isinstance(family, dict):
                errors.append("quality.mesh_acceptance.family must be an object")
            else:
                if family.get("rule") != "ALL_PASS":
                    errors.append("quality.mesh_acceptance.family.rule must be ALL_PASS (the only supported family rule)")
                members = family.get("members")
                if not isinstance(members, list) or not members:
                    errors.append("quality.mesh_acceptance.family.members must be a non-empty list")
                else:
                    seen = set()
                    for index, member in enumerate(members):
                        if not isinstance(member, dict):
                            errors.append(f"quality.mesh_acceptance.family.members[{index}] must be an object")
                            continue
                        member_id = member.get("id")
                        if not isinstance(member_id, str) or not member_id.strip():
                            errors.append(f"quality.mesh_acceptance.family.members[{index}].id must be a non-empty string")
                        elif member_id in seen:
                            errors.append(f"quality.mesh_acceptance.family.members id duplicated: {member_id}")
                        else:
                            seen.add(member_id)
                        if not isinstance(member.get("role"), str) or not member.get("role").strip():
                            errors.append(f"quality.mesh_acceptance.family.members[{index}].role must be a non-empty string")
            margin = acceptance.get("margin")
            if not isinstance(margin, dict):
                errors.append("quality.mesh_acceptance.margin must be an object")
            else:
                fragile = margin.get("fragile_below")
                target = margin.get("target")
                for key, value in (("fragile_below", fragile), ("target", target)):
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 1:
                        errors.append(f"quality.mesh_acceptance.margin.{key} must be a finite number > 1")
                if (isinstance(fragile, (int, float)) and not isinstance(fragile, bool)
                        and isinstance(target, (int, float)) and not isinstance(target, bool) and fragile > target):
                    errors.append("quality.mesh_acceptance.margin.fragile_below must not exceed margin.target")
            if acceptance.get("localization_required_on_failure") is not True:
                errors.append("quality.mesh_acceptance.localization_required_on_failure must be true")
            bands = acceptance.get("distribution_bands")
            if not isinstance(bands, list) or not bands:
                errors.append("quality.mesh_acceptance.distribution_bands must be a non-empty list")
            else:
                previous = 0.0
                for index, band in enumerate(bands):
                    if isinstance(band, bool) or not isinstance(band, (int, float)) or not math.isfinite(band) or band <= previous:
                        errors.append(f"quality.mesh_acceptance.distribution_bands[{index}] must be a finite number strictly greater than the previous one")
                        break
                    previous = band
            extra = set(acceptance) - {"family", "margin", "localization_required_on_failure", "distribution_bands"}
            if extra:
                errors.append("quality.mesh_acceptance has unknown keys: " + ", ".join(sorted(extra)))
    # 2026-10-30 family standard (F3): NOT a silent pass.  When the caller opts in, a CaseSpec that is
    # not byte-identical to the grandfathered split snapshot must declare the block; the rule lives in
    # scripts/plan_mesh_gate.py (one implementation) and its stable code is prefixed to the message.
    if require_mesh_acceptance:
        try:
            plan_mesh_gate.require_declared_acceptance(spec)
        except plan_mesh_gate.MeshRegimeError as exc:
            errors.append(str(exc))
    if spec.get("boundary_assumption") == "wall_tunnel":
        errors.append("free-air workflows cannot inherit a wall-tunnel outer boundary")
    if audit is not None:
        if audit.get('status') not in {'ok', 'partial'}:
            errors.append('Case audit has not completed successfully')
        parts = audit.get('geometryParts')
        if not isinstance(parts, list):
            errors.append('Case audit lacks geometryParts')
            parts = []
        names = [part.get('qualifiedName') or part.get('name') for part in parts if isinstance(part, dict)]
        selected = set()
        for role in ('body_parts', 'rotor_parts'):
            for requested in geometry.get(role, []) if isinstance(geometry.get(role), list) else []:
                if requested not in names:
                    errors.append(f'geometry.{role} selector is absent from audited CAD parts: {requested}')
                else:
                    selected.add(requested)
        for part in parts:
            if not isinstance(part, dict) or (part.get('qualifiedName') or part.get('name')) not in selected:
                continue
            name = part.get('qualifiedName') or part.get('name')
            if part.get('isClosed') is False:
                errors.append(f'geometry part is open and requires repair or an explicit fluid-opening treatment: {name}')
            if part.get('isManifold') is False:
                errors.append(f'geometry part is non-manifold and requires repair: {name}')
            if part.get('isClosed') is None or part.get('isManifold') is None:
                missing.append(f'geometry.topology_evidence.{name}')
            bounds = part.get('boundingBoxRaw')
            if bounds is None:
                missing.append(f'geometry.bounding_box_evidence.{name}')
            elif (not isinstance(bounds, list) or len(bounds) != 6
                  or any(isinstance(value, bool) or not isinstance(value, (int, float))
                         or not math.isfinite(value) for value in bounds)
                  or any(bounds[axis] >= bounds[axis + 3] for axis in range(3))):
                errors.append(f'geometry part has invalid bounding-box evidence: {name}')
            elif (isinstance(outer_bounds_m, list) and len(outer_bounds_m) == 6
                  and all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                          for value in outer_bounds_m)
                  and spec.get("model_length_unit") in {"m", "mm", "cm", "in"}):
                scale_to_m = {"m": 1.0, "mm": 0.001, "cm": 0.01, "in": 0.0254}[spec["model_length_unit"]]
                raw_bounds_m = [float(value) * scale_to_m for value in bounds]
                if any(raw_bounds_m[axis] < outer_bounds_m[axis] or
                       raw_bounds_m[axis + 3] > outer_bounds_m[axis + 3]
                       for axis in range(3)):
                    errors.append(f'domain.outer_bounds_m does not contain selected geometry: {name}')
                if (shape == "cylinder" and isinstance(radius, (int, float)) and not isinstance(radius, bool)
                        and math.isfinite(radius) and radius > 0 and isinstance(center, list) and len(center) == 2
                        and all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                                for value in center)):
                    x_far = max(abs(raw_bounds_m[0] - center[0]), abs(raw_bounds_m[3] - center[0]))
                    y_far = max(abs(raw_bounds_m[1] - center[1]), abs(raw_bounds_m[4] - center[1]))
                    if math.hypot(x_far, y_far) >= radius:
                        errors.append(f'domain cylinder does not conservatively contain selected geometry: {name}')
                if (rotation_zone is not None and name in geometry.get('rotor_parts', []) and
                        isinstance(rotation_zone, dict) and
                        isinstance(rotation_zone.get('center_xy_m'), list) and
                        len(rotation_zone['center_xy_m']) == 2 and
                        isinstance(rotation_zone.get('radius_m'), (int, float)) and
                        isinstance(rotation_zone.get('z_min_m'), (int, float)) and
                        isinstance(rotation_zone.get('z_max_m'), (int, float))):
                    zone_center = rotation_zone['center_xy_m']
                    zone_x_far = max(abs(raw_bounds_m[0] - zone_center[0]),
                                     abs(raw_bounds_m[3] - zone_center[0]))
                    zone_y_far = max(abs(raw_bounds_m[1] - zone_center[1]),
                                     abs(raw_bounds_m[4] - zone_center[1]))
                    if (math.hypot(zone_x_far, zone_y_far) >= rotation_zone['radius_m'] or
                            raw_bounds_m[2] <= rotation_zone['z_min_m'] or
                            raw_bounds_m[5] >= rotation_zone['z_max_m']):
                        errors.append(f'rotation_zone does not conservatively contain rotor geometry: {name}')
        regions = audit.get('regions')
        if isinstance(regions, list) and regions:
            open_boundary_owners = set()
            for region in regions:
                if not isinstance(region, dict):
                    continue
                boundaries = [item for item in region.get('boundaries', []) if isinstance(item, dict)]
                physical = [item for item in boundaries if item.get('boundaryTypeClassSimpleName') != 'InternalBoundary']
                # A CAD part supplying every physical boundary of an interfaced Region
                # is the Region's fluid volume, even if its name sounds like a solid.
                if (len(physical) >= 2 and region.get('physicsContinuum') and
                        any(item.get('boundaryTypeClassSimpleName') == 'InternalBoundary' for item in boundaries)):
                    owners = [set(item.get('partSurfaceOwners', [])) for item in physical]
                    if all(len(item) == 1 for item in owners) and len(set.union(*owners)) == 1:
                        source_part = next(iter(owners[0]))
                        if source_part in selected:
                            errors.append(f'geometry target part supplies an interfaced fluid Region, not a solid: {source_part}')
                for boundary in boundaries:
                    boundary_type = boundary.get('boundaryTypeClassSimpleName') or ''
                    if ('Inlet' in boundary_type or 'Outlet' in boundary_type
                            or boundary_type == 'PressureBoundary'):
                        owners = boundary.get('partSurfaceOwners')
                        if isinstance(owners, list):
                            open_boundary_owners.update(x for x in owners if isinstance(x, str))
            for name in selected & open_boundary_owners:
                errors.append(f'geometry target part owns an inlet/outlet boundary and may be a fluid domain: {name}')
            wall_boundaries = [f"{region.get('name')}/{boundary.get('name')}"
                               for region in regions if isinstance(region, dict)
                               for boundary in region.get('boundaries', []) if isinstance(boundary, dict)
                               and boundary.get('boundaryTypeClassSimpleName') == 'WallBoundary']
            if wall_boundaries:
                roles = geometry.get('existing_wall_roles')
                if not isinstance(roles, dict):
                    missing.append('geometry.existing_wall_roles')
                else:
                    for boundary in wall_boundaries:
                        if roles.get(boundary) not in {'body_wall', 'farfield_to_replace', 'other_wall'}:
                            missing.append('geometry.existing_wall_roles.'+boundary)
                warnings.append('Existing wall boundaries require body/farfield classification: '+', '.join(wall_boundaries[:12]))
    else:
        warnings.append('No trusted case audit supplied; physical plan cannot be executable')
    operations = required_operations(scenario) if scenario in SCENARIOS else []
    if s1:
        operations = [op for op in operations if op not in ('mesh_study', 'domain_study')]
    selected = [op for op in (plan_operations or []) if isinstance(op, str)]
    registry = None
    plan_mechanism: str | None = None
    plan_blocker: str | None = None
    # Defined for the caller-supplied override path too: `unresolved` is only computed by the resolver.
    plan_unresolved: list[str] = []
    if installed_operations is not None:
        # Explicit override, kept for offline "what if these were available" analysis. The MCP path never
        # uses it: Backend.validate_plan calls with None so availability is resolved from real call paths.
        installed = set(installed_operations)
        servable: dict[str, list[str]] = {operation: ['<supplied_by_caller>'] for operation in operations
                                          if operation in installed}
        by_mechanism: dict[str, dict] = {}
        operations_detail: dict[str, dict] = {}
    else:
        registry = operation_registry if operation_registry is not None else resolve_operations(case=case)
        resolved = (registry or {}).get('operations') or {}
        # A GENERIC plan requirement may only be served by a GENERIC operation. A sample-specific capability
        # (fixed AIR3B names) can never satisfy it - that is the "must not masquerade" rule of R11.
        servable = {}
        # Per mechanism: which capabilities its own executor operations provide, and which SINGLE
        # operation of it covers the whole requirement (a complete execution path for that mechanism).
        mechanism_servable: dict[str, dict[str, list[str]]] = {name: {} for name in RUN_MECHANISMS}
        covering_operation: dict[str, str | None] = {name: None for name in RUN_MECHANISMS}
        for op_id, entry in sorted(resolved.items()):
            if entry.get('implementation') != 'RESOLVED' or entry.get('scope_class') != 'generic':
                continue
            caps = list(entry.get('provides') or [])
            for cap in caps:
                servable.setdefault(cap, []).append(op_id)
            mechanism = entry.get('mechanism')
            if mechanism in RUN_MECHANISMS:
                targets = [mechanism]
            elif mechanism == 'offline_tool':
                # Only a VERIFIED invocation counts. `applies_to_run_mechanisms` is a declaration and is
                # deliberately not consulted here: a pre/post step that nothing calls must not complete a
                # plan. `classify_parts` is the live example (it declared restricted_trial and no
                # restricted-trial entry calls it). A tool with no `plan_role` cannot be placed in a plan
                # at all, so it does not count either.
                targets = ([item.get('mechanism') for item in (entry.get('invoked_by') or [])]
                           if entry.get('plan_role') else [])
            else:
                targets = []
            for target in targets:
                if target not in RUN_MECHANISMS:
                    continue
                for cap in caps:
                    mechanism_servable[target].setdefault(cap, []).append(op_id)
                if mechanism in RUN_MECHANISMS and set(operations) <= set(caps):
                    covering_operation[target] = covering_operation[target] or op_id
        # The plan that would really be submitted decides coverage. Name the mechanism it uses and say
        # exactly what stops it; never report the union of two mechanisms as an executable path.
        plan_caps: dict[str, list[str]] = {}
        plan_mechanisms: set[str] = set()
        plan_unresolved: list[str] = []
        for op_id in selected:
            entry = resolved.get(op_id)
            if (entry is None or entry.get('implementation') != 'RESOLVED'
                    or entry.get('scope_class') != 'generic'):
                plan_unresolved.append(op_id)
                continue
            for cap in entry.get('provides') or []:
                plan_caps.setdefault(cap, []).append(op_id)
            if entry.get('mechanism') in RUN_MECHANISMS:
                plan_mechanisms.add(entry['mechanism'])
        if selected:
            # STRICT: a selected plan covers exactly what its OWN members provide. Nothing is topped up
            # from the mechanism's other operations - a capability that is not a member of the plan is not
            # in the plan, and "the mechanism could cover it somewhere" is the union false-green this round
            # removes. Pre/post steps therefore have to be NAMED in plan_operations to count; and because
            # they are named, submit_run really executes the pre ones and report_run performs the post ones.
            servable = plan_caps
            plan_mechanism = next(iter(plan_mechanisms)) if len(plan_mechanisms) == 1 else None
        else:
            plan_mechanism = next((name for name in RUN_MECHANISMS if covering_operation.get(name)), None)
        installed = {op_id for op_id, entry in resolved.items() if entry.get('implementation') == 'RESOLVED'}
        by_mechanism = {}
        for target in RUN_MECHANISMS:
            caps = mechanism_servable.get(target) or {}
            if selected:
                missing_for_plan = [operation for operation in operations if operation not in plan_caps]
                # R12 review blocker 1: when a plan WAS selected, "one operation somewhere covers
                # everything" is NOT a substitute for that plan. The fallback below is therefore only
                # consulted when no plan was named, and it can never paper over an unresolved plan member
                # or a plan that mixes two executors.
                covers = (target == plan_mechanism and not plan_unresolved)
                covering = None if not covers else selected
            else:
                missing_for_plan = [operation for operation in operations if operation not in caps]
                covering = covering_operation.get(target)
                covers = bool(covering)
            by_mechanism[target] = {
                'required': list(operations),
                'can_serve': sorted(caps),
                'missing': missing_for_plan,
                'covering_operation': covering_operation.get(target),
                'complete_plan': (list(covering) if isinstance(covering, (list, tuple)) else
                                  ([covering] if covering else None)),
                'executable': bool(not missing_for_plan and audit is not None and covers),
            }
        operations_detail = {}
        for operation in operations:
            if operation in servable:
                serving = servable[operation][0]
                entry = resolved.get(serving, {})
                operations_detail[operation] = {
                    'served_by': servable[operation], 'mechanism': entry.get('mechanism'),
                    'implementation': 'RESOLVED',
                    'verified_on_current_case': bool(entry.get('verified_on_current_case')),
                    'verified_on_current_case_reason': entry.get('verified_on_current_case_reason'),
                    'general_acceptance': entry.get('general_acceptance'),
                    'engineering': 'NOT_CLAIMED_BY_WIRING',
                    'parameters': entry.get('parameters'),
                    'missing_reason': None}
    unavailable = [operation for operation in operations if operation not in servable]
    missing_reasons = {}
    reserved = {item.get('id'): item for item in ((registry or {}).get('reserved_interfaces') or [])
                if isinstance(item, dict)}
    for operation in unavailable:
        if operation in reserved:
            item = reserved[operation]
            missing_reasons[operation] = ('a reserved interface owned by %s, status %s: it is not registered as '
                                          'available until its contract is met'
                                          % (item.get('owner'), item.get('status')))
            continue
        sample_only = []
        for op_id, entry in ((registry or {}).get('operations') or {}).items():
            if entry.get('implementation') != 'RESOLVED' or entry.get('scope_class') == 'generic':
                continue
            if operation in (entry.get('provides') or []) or operation in (entry.get('provides_on_own_case') or []):
                sample_only.append(op_id)
        if sample_only:
            missing_reasons[operation] = ('only SAMPLE-SPECIFIC implementations exist (%s); a fixed-name capability '
                                          'cannot satisfy a generic plan requirement' % ', '.join(sorted(sample_only)))
        else:
            note = ((registry or {}).get('not_registered_on_purpose') or {}).get(operation)
            missing_reasons[operation] = ('no implementation is registered for this operation'
                                          + (' (%s)' % note if note else ''))
        operations_detail[operation] = {
            'served_by': [], 'mechanism': None, 'implementation': 'NOT_IMPLEMENTED',
            'verified_on_current_case': False, 'general_acceptance': 'NOT_ACCEPTED',
            'engineering': 'NOT_CLAIMED_BY_WIRING', 'parameters': None,
            'missing_reason': missing_reasons[operation]}
    phase_readiness = {}
    for phase, phase_operations in OPERATION_PHASES.items():
        required = [operation for operation in phase_operations if operation in operations]
        if not required and phase != "5_macro_and_release":
            continue
        phase_readiness[phase] = {
            "required": required,
            "installed": [operation for operation in required if operation in servable],
            "missing": [operation for operation in required if operation not in servable],
        }
    if unavailable:
        if selected:
            warnings.append('The selected execution plan does not cover: '
                            + ', '.join('%s (%s)' % (operation, missing_reasons.get(operation, ''))
                                        for operation in unavailable))
        else:
            warnings.append("Required operations have no available call path: "
                            + "; ".join('%s (%s)' % (operation, missing_reasons.get(operation, ''))
                                        for operation in unavailable))
    # R12: the CONCLUSION comes from the execution plan, never from the union of every mechanism's
    # capabilities. `unavailable_operations` stays as the informational "no call path anywhere" list;
    # it is no longer what sets `executable`. A mechanism is executable only when a single path really
    # covers the whole requirement: the named plan, or one operation that provides everything.
    if installed_operations is not None:
        executable = not missing and not errors and not unavailable and audit is not None
        if executable:
            plan_blocker = None
        elif missing or errors:
            plan_blocker = ('the CaseSpec itself is incomplete (missing: %s; errors: %s)'
                            % (', '.join(missing) or 'none', ', '.join(errors) or 'none'))
        elif audit is None:
            plan_blocker = 'no trusted case audit was supplied, so no physical plan is executable'
        else:
            plan_blocker = 'the caller-supplied operation set does not cover: ' + ', '.join(unavailable)
    else:
        complete = [name for name in RUN_MECHANISMS if (by_mechanism.get(name) or {}).get('executable')]
        executable = not missing and not errors and audit is not None and bool(complete)
        if executable:
            plan_blocker = None
        elif missing or errors:
            plan_blocker = ('the CaseSpec itself is incomplete (missing: %s; errors: %s)'
                            % (', '.join(missing) or 'none', ', '.join(errors) or 'none'))
        elif selected and plan_unresolved:
            plan_blocker = ('the selected plan names operations that do not resolve to a generic call '
                            'path: ' + ', '.join(plan_unresolved))
        elif selected and len(plan_mechanisms) > 1:
            plan_blocker = ('the selected plan mixes %s; one run has exactly one executor'
                            % ' and '.join(sorted(plan_mechanisms)))
        elif selected:
            # Name the plan's own gap first: that is the actionable fact. The audit requirement is added
            # only as a second reason, so a plan gap is never hidden behind "no audit supplied".
            parts = []
            gap = sorted(set(operations) - set(plan_caps))
            if gap:
                parts.append('the selected plan does not provide: ' + ', '.join(gap))
            if audit is None:
                parts.append('no trusted case audit was supplied, so no physical plan is executable')
            plan_blocker = '; '.join(parts) or 'the selected plan is not executable'
        elif audit is None:
            plan_blocker = 'no trusted case audit was supplied, so no physical plan is executable'
        else:
            best = min(RUN_MECHANISMS,
                       key=lambda name: len((by_mechanism.get(name) or {}).get('missing') or operations))
            gap = (by_mechanism[best] or {}).get('missing') or []
            if gap:
                plan_blocker = ('no single execution path covers the plan: the closest mechanism %r is '
                                'missing %s; submit an explicit plan_operations graph that includes every '
                                'required operation (pre steps, the stages, post steps)'
                                % (best, ', '.join(gap)))
            else:
                plan_blocker = ('no SINGLE operation of %r covers the whole plan (its operations cover it '
                                'only collectively, which is not an execution path); submit an explicit '
                                'plan_operations graph that includes every required operation'
                                % (best,))
    digest = hashlib.sha256(json.dumps(spec, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    return Validation(not missing and not errors, executable,
                      digest, missing, errors, warnings, sorted(installed), operations, phase_readiness,
                      operations_detail, missing_reasons, by_mechanism,
                      list((registry or {}).get('reserved_interfaces') or []) if registry else [],
                      (registry or {}).get('unresolved') if registry else None, list(unavailable),
                      selected, plan_mechanism, plan_blocker, list(plan_unresolved))
