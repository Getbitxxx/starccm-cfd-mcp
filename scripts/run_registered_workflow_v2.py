"""Version 2 registration adaptation. Reuses existing plans, gates, controller and reporter.

Candidate creation is offline and never grants approval. The restricted finite
execution scope is separate from the full engineering plan's missing studies.
"""
from resource_contract import make_contract, validate_pipeline_resources
from pathlib import Path
import argparse
import copy
import json
import math
import re
import shutil
import sys
from run_registered_workflow import ROOT, digest, load
from casespec import resolve_operations, validate_case_spec
from part_classification import classify_parts
from stage_plan import plan_pipeline
from run_record_gate import assert_citable, find_run_state, run_verdict
from air3b_pipeline_trial import check_review, support_hashes

PLAN = ['classify_parts', 'audit_static_body', 'build_external_domain',
        'build_external_flow_region_physics_mesh', 'audit_external_flow_mesh',
        'run_external_flow_solve', 'audit_external_flow_convergence', 'report', 'export_evidence']


def prepare_static_external_flow(source_sim, case_spec, workflow_id):
    """Agent-facing intake: discover trusted provenance; write inputs and all study calls offline.

    The user supplies a source and engineering intent, never runner commands or evidence paths.
    Unknown sources stop for the existing reviewed audit, which this offline call cannot run.
    """
    registration_path(workflow_id)
    for suffix in ('-mesh-2', '-domain-2', '-base-rc1'):
        registration_path(workflow_id + suffix)
    source = repo_path(source_sim)
    source_sha = digest(source)
    matches = []
    paths = set((ROOT / 'cases').rglob('*contract.json')) | set((ROOT / 'cases').rglob('*source.json'))
    for path in sorted(paths):
        contract = load(path)
        if contract.get('source_sim_sha256') != source_sha:
            continue
        audit = contract.get('audit_record')
        if audit:
            if contract.get('audit_record_sha256') and contract['audit_record_sha256'] != digest(repo_path(audit)):
                raise ValueError('Trusted contract audit hash differs; preserve prior approval')
            try:
                assert_citable(repo_path(audit))
            except (ValueError, FileNotFoundError):
                continue
            if contract.get('geometry_contract'):
                geometry_contract = load(repo_path(contract['geometry_contract']))
                contract = {**geometry_contract, **contract}
                if contract.get('source_kind') == 'step_cylinder':
                    contract['source_kind'] = 'step_cad'
            matches.append((path, repo_path(audit), contract))
    if not matches:
        # New audited sources need no manually authored contract. Read the existing
        # geometry audit and provenance artifacts, using the same central citation gate.
        for record in sorted((ROOT / 'test-evidence').rglob('static_body_audit.json')):
            actual = load(record)
            if actual.get('input_sim_sha256') != source_sha:
                continue
            try:
                assert_citable(record)
            except (ValueError, FileNotFoundError):
                continue
            contract = {'shape': actual.get('body_shape'), 'body_part_name': actual.get('body_part_name'),
                'origin_m': actual.get('body_origin_m'), 'radius_m': actual.get('body_radius_m'),
                'length_m': actual.get('body_length_m'), 'axis': actual.get('body_axis'),
                'source_sim_sha256': source_sha, 'audit_record': str(record.relative_to(ROOT))}
            if contract['shape'] == 'cylinder' and actual.get('cad_retained') is True:
                step_sha = case_spec.get('input_identity', {}).get('step_sha256')
                step_paths = [p for p in (ROOT / 'cad').rglob('*.step') if step_sha and digest(p) == step_sha]
                if not step_paths:
                    continue
                contract.update(source_kind='step_cad', step_sha256=step_sha,
                    step_path=str(step_paths[0].relative_to(ROOT)))
            elif contract['shape'] == 'sphere':
                generations = []
                for generation in (ROOT / 'test-evidence').rglob('static_body_geometry.json'):
                    if load(generation).get('output_sha256') != source_sha:
                        continue
                    try:
                        assert_citable(generation)
                    except (ValueError, FileNotFoundError):
                        continue
                    generations.append(generation)
                if not generations:
                    continue
                contract.update(source_kind='generated_sphere', generation_record=str(generations[0].relative_to(ROOT)),
                    generation_record_sha256=digest(generations[0]))
            else:
                continue
            matches.append((record, record, contract))
    if not matches:
        return {'state': 'NEEDS_TRUSTED_SOURCE_AUDIT', 'source_sha256': source_sha,
                'next_tool': 'register_case / inspect_case with independent review',
                'star_started': False, 'engineering_verified': False}
    # Multiple historical contracts for the same bytes must agree on physical identity.
    identities = {json.dumps({k: c.get(k) for k in
        ('source_kind', 'shape', 'body_part_name', 'origin_m', 'radius_m', 'length_m', 'axis')},
        sort_keys=True) for _, _, c in matches}
    if len(identities) != 1:
        raise ValueError('Trusted source contracts disagree; source role needs clarification')
    if case_spec.get('model_length_unit') != 'm':
        return {'state': 'UNSUPPORTED_FULL_EXPORT_UNIT', 'source_sha256': source_sha,
                'reason': 'Installed ExportExternalFlowResults requires SI metre CaseSpec; no audited unit-conversion handoff exists',
                'star_started': False, 'engineering_verified': False}
    contract_path, record, contract = matches[0]
    evidence = repo_path(ROOT / 'test-evidence/static-release-candidate' / workflow_id, 'test-evidence')
    if evidence.exists():
        raise ValueError('Use a new workflow version; candidate inputs are immutable')
    evidence.mkdir(parents=True)
    spec = copy.deepcopy(case_spec)
    spec['case_ref'] = 'library/' + source_sha + '.sim'
    spec.setdefault('input_identity', {})['source_sim_sha256'] = source_sha
    spec_path = evidence / 'case_spec.json'
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + '\n')
    contract.update(source_sim_sha256=source_sha, audit_record=str(record.relative_to(ROOT)),
                    audit_record_sha256=digest(record))
    own_contract = evidence / 'source-contract.json'
    own_contract.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + '\n')
    candidate = prepare_candidate(source, spec_path, workflow_id, record, own_contract, evidence)
    return prepare_candidate_studies(candidate, own_contract)


def prepare_candidate_studies(candidate, source_contract):
    """Bind every new level to the existing registration and persistent submission entry."""
    import run_sensitivity_study as study
    workflow = candidate['workflow_id']
    manifest_path = registration_path(workflow)
    manifest = load(manifest_path)
    evidence = ROOT / manifest['mcp_evidence']
    spec_path, source = repo_path(candidate['case_spec_path']), repo_path(candidate['source_sim'])
    spec = load(spec_path)
    pipeline_path = ROOT / manifest['pipeline']
    pipeline = load(pipeline_path)
    studies = {}
    for kind in ('mesh', 'domain'):
        root = evidence / kind
        descriptors = study.prepare(spec_path, kind, source, root, workflow + '-' + kind,
            pipeline['macro_hashes'], trial_suffix='rc1', pipeline_path=pipeline_path,
            source_contract=repo_path(source_contract))
        for index, descriptor in enumerate(descriptors):
            if descriptor.get('reuse'):
                continue
            level_id = workflow + '-' + kind + '-' + str(index)
            level = prepare_candidate(source, descriptor['case_spec'], level_id,
                candidate['audited_view_source'], source_contract, descriptor['dir'])
            descriptor.update(workflow_id=level_id, pipeline=load(level['manifest'])['pipeline'],
                pipeline_sha256=level['pipeline_sha256'],
                candidate=str(Path(descriptor['dir']) / 'candidate.json'),
                submit_tool='submit_registered_workflow', request_id=descriptor['trial_id'],
                star_seconds_cap=load(level['manifest'])['star_seconds_cap'])
            descriptor['pipeline'] = str(ROOT / descriptor['pipeline'])
        levels_path = root / (kind + '-levels.json')
        levels = load(levels_path); levels['levels'] = descriptors
        levels_path.write_text(json.dumps(levels, ensure_ascii=False, indent=2) + '\n')
        studies[kind] = {'levels_manifest': str(levels_path), 'sha256': digest(levels_path),
            'prepare_tool': 'prepare_static_external_flow',
            'assemble_tool': 'assemble_registered_workflow_studies', 'levels': descriptors}
    manifest['studies'] = studies
    manifest['adapter_files'] = list(dict.fromkeys(manifest['adapter_files'] +
        ['scripts/run_sensitivity_study.py', 'scripts/evaluate_sensitivity.py', 'scripts/grid_convergence.py'] +
        [str(Path(v['levels_manifest']).relative_to(ROOT)) for v in studies.values()] +
        [str(repo_path(path).relative_to(ROOT)) for info in studies.values() for level in info['levels']
         if not level.get('reuse') for path in (level['case_spec'], level['pipeline'],
             str(registration_path(level['workflow_id'])), level['candidate'])]))
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    candidate['studies'] = studies
    candidate['base_call'] = {'tool': 'submit_registered_workflow', 'workflow_id': workflow,
        'source_sim': str(source), 'case_spec_path': str(spec_path),
        'request_id': workflow + '-base-rc1', 'star_seconds_cap': manifest['star_seconds_cap']}
    candidate['complete_plan_state'] = 'PREPARED_NOT_REVIEWED_NOT_RUN'
    (evidence / 'candidate.json').write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + '\n')
    return candidate


def repo_path(value, prefix=None):
    path = Path(value)
    path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise ValueError('Candidate material must be within the repository')
    if prefix and not path.is_relative_to((ROOT / prefix).resolve()):
        raise ValueError('Candidate evidence must be repository test-evidence')
    return path


def registration_path(workflow):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', workflow):
        raise ValueError('Invalid candidate workflow id')
    return ROOT / 'registered-workflows-v2' / workflow / 'manifest.json'


def validate_local_mesh_controls(spec):
    """Validate optional explicit SI boxes and wall-normal controls; never infer them."""
    local = ('mesh_nearfield_bounds_m', 'mesh_nearfield_cell_size_m',
             'mesh_wake_bounds_m', 'mesh_wake_cell_size_m')
    prism = ('mesh_prism_first_layer_height_m', 'mesh_prism_total_thickness_m')
    def positive(value):
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0
    def bounds(value):
        return (isinstance(value, list) and len(value) == 6 and
                all(not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x) for x in value) and
                all(value[i] < value[i+3] for i in range(3)))
    if any(k in spec for k in local):
        if not all(k in spec for k in local): raise ValueError('All four local mesh fields are required')
        outer = spec.get('domain', {}).get('outer_bounds_m'); base = spec.get('mesh_base_size_m')
        if not bounds(outer) or not positive(base): raise ValueError('Local mesh needs finite outer bounds and positive base size')
        for box_key, size_key in ((local[0], local[1]), (local[2], local[3])):
            box, size = spec[box_key], spec[size_key]
            if not bounds(box) or not positive(size) or size > base: raise ValueError('Invalid local mesh box or cell size')
            if any(box[i] < outer[i] or box[i+3] > outer[i+3] for i in range(3)):
                raise ValueError('Local mesh box lies outside the domain')
    if any(k in spec for k in prism):
        if not all(k in spec for k in prism): raise ValueError('Explicit prism height and total thickness must be paired')
        first, total = spec[prism[0]], spec[prism[1]]; layers = spec.get('mesh_prism_layers')
        if (not positive(first) or not positive(total) or isinstance(layers, bool) or
                not isinstance(layers, int) or not 3 <= layers <= 20 or total <= layers * first):
            raise ValueError('Explicit prism total thickness must exceed layers times first height')
    return True


def prepare_candidate(source_sim, case_spec_path, workflow_id, geometry_record,
                      geometry_contract, evidence, _checkpoint_only=False):
    checkpoint_contract = load(repo_path(geometry_contract))
    if checkpoint_contract.get('input_kind') == 'successful_solve_checkpoint':
        return prepare_checkpoint_candidate(source_sim, case_spec_path, workflow_id, geometry_record, geometry_contract, evidence)
    if checkpoint_contract.get('input_kind') == 'successful_solved_readback':
        return prepare_solved_readback_candidate(source_sim, case_spec_path, workflow_id, geometry_record, geometry_contract, evidence)
    source, spec_path, record, contract_path = [repo_path(v) for v in
        (source_sim, case_spec_path, geometry_record, geometry_contract)]
    evidence = repo_path(evidence, 'test-evidence')
    manifest_path = registration_path(workflow_id)
    if manifest_path.exists():
        raise ValueError('Candidate already exists; use a new version, never overwrite approved bytes')
    spec, contract = load(spec_path), load(contract_path)
    validate_local_mesh_controls(spec)
    citation = assert_citable(record)
    actual = load(record)
    unit = spec.get('model_length_unit')
    if spec.get('scenario') != 'external_static' or unit not in ('m', 'mm'):
        raise ValueError('Version 2 candidate supports audited m/mm static external flow only')
    unit_scale = {'m': 1.0, 'mm': 0.001}[unit]
    geometry = spec['geometry']
    velocity = spec['flow']['velocity_m_s']
    if (len(velocity) != 3 or velocity[0] <= 0 or velocity[1:] != [0, 0] or
            geometry.get('body_role') != 'solid_static_obstacle' or
            (geometry['body_shape'] == 'cylinder' and geometry.get('body_axis') != [0, 0, 1])):
        raise ValueError('Candidate exceeds existing static +X / declared-solid / +Z-cylinder scope')
    if geometry['body_parts'] != [geometry['body_part_name']]:
        raise ValueError('This chain requires one explicitly selected solid body')
    if (geometry['body_shape'] != contract['shape'] or
            geometry['body_part_name'] != contract['body_part_name'] or
            geometry['body_origin_m'] != contract['origin_m'] or
            geometry['body_radius_m'] != contract['radius_m'] or
            (geometry['body_shape'] == 'cylinder' and
             (geometry['body_length_m'] != contract['length_m'] or
              geometry['body_axis'] != contract['axis']))):
        raise ValueError('New CaseSpec geometry contradicts its independently prepared contract')
    if (actual.get('body_part_name') != geometry['body_part_name'] or
            actual.get('regions') != 0 or actual.get('is_closed') is not True or
            actual.get('is_manifold') is not True or actual.get('iteration') != 0 or
            (geometry['body_shape'] == 'cylinder' and actual.get('cad_retained') is not True)):
        raise ValueError('Geometry certificate does not establish this geometry-only selected body')
    source_sha = digest(source)
    source_kind = contract.get('source_kind', 'step_cad')
    identity = spec.get('input_identity', {})
    if (('source_kind' in identity and identity['source_kind'] != source_kind) or
            ('source_sim_sha256' in identity and identity['source_sim_sha256'] != source_sha) or
            ('source_sim_sha256' in contract and contract['source_sim_sha256'] != source_sha) or
            ('audit_record' in contract and repo_path(contract['audit_record']) != record) or
            ('audit_record_sha256' in contract and contract['audit_record_sha256'] != digest(record))):
        raise ValueError('Declared source identity or audit evidence differs')
    proof_paths = []
    if citation.get('stage_record'):
        proof_paths.append(repo_path(citation['stage_record']))
    source_citations = {'audit': citation}
    audit_source_sha = actual.get('input_sim_sha256')
    if source_kind == 'step_cad':
        if geometry['body_shape'] != 'cylinder' or actual.get('cad_retained') is not True:
            raise ValueError('STEP source requires the audited retained CAD cylinder')
        if (spec.get('input_identity', {}).get('step_sha256') != contract.get('step_sha256') or
                digest(repo_path(contract['step_path'])) != contract['step_sha256']):
            raise ValueError('STEP identity differs from the new geometry contract')
    elif source_kind == 'generated_sphere':
        if geometry['body_shape'] != 'sphere' or contract.get('source_sim_sha256') != source_sha:
            raise ValueError('Generated sphere contract source hash or shape differs')
        generation_path = repo_path(contract['generation_record'])
        if digest(generation_path) != contract['generation_record_sha256']:
            raise ValueError('Generated sphere evidence hash differs')
        source_citations['generation'] = assert_citable(generation_path)
        generated = load(generation_path)
        if (generated.get('output_sha256') != source_sha or generated.get('part_count') != 1 or
                generated.get('regions') != 0 or generated.get('iteration') != 0 or
                generated.get('is_closed') is not True or generated.get('is_manifold') is not True or
                any(generated.get(key) != geometry[key] or actual.get(key) != geometry[key]
                    for key in ('body_shape', 'body_part_name', 'body_origin_m', 'body_radius_m'))):
            raise ValueError('Generated sphere evidence does not establish this selected geometry')
        proof_paths.append(generation_path)
        generation_state = source_citations['generation'].get('stage_record') or find_run_state(generation_path.parent)[0]
        if generation_state:
            proof_paths.append(repo_path(generation_state))
        # Older audited records lack an in-record input hash. Use only the centrally
        # authenticated owning stage's unique successful audit macro input binding.
        if audit_source_sha is None:
            if not citation.get('stage_record'):
                raise ValueError('Generated sphere audit lacks a runner input identity')
            audit_stage = load(repo_path(citation['stage_record']))
            audits = [m for m in audit_stage.get('macros', [])
                      if m.get('macro') == 'AuditStaticBodyGeometry.java']
            if (len(audits) != 1 or audits[0].get('ran') is not True or
                    audits[0].get('status') != 'ok' or audits[0].get('exit_code') != 0):
                raise ValueError('Generated sphere audit runner identity is ambiguous or failed')
            audit_source_sha = audits[0].get('input_sha256')
    else:
        raise ValueError('Unsupported geometry source kind')
    # Both source types must bind the actual submitted simulation, never just a name.
    if audit_source_sha != source_sha:
        raise ValueError('Geometry certificate source hash differs from the submitted .sim')
    if actual.get('model_length_unit') != unit:
        raise ValueError('Declared model unit differs from the trusted geometry audit')
    if unit == 'mm':
        # These are measured preferred units and SI geometry, not a relabelled spec.
        unit_records = [actual] + ([generated] if source_kind == 'generated_sphere' else [])
        for measured in unit_records:
            if (measured.get('model_length_unit') != unit or
                    str(measured.get('model_length_unit_readback', '')).strip() != '(mm)' or
                    measured.get('preferred_length_unit_conversion_to_m') != unit_scale):
                raise ValueError('mm source lacks matching actual unit readback/conversion')
            for key, limit in (('measured_vertex_radius_error_ratio', 1),
                               ('measured_centre_offset_ratio', 1),
                               ('measured_volume_relative_deviation', 0.01)):
                value = measured.get(key)
                if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                        not math.isfinite(value) or not 0 <= value <= limit):
                    raise ValueError('mm source internal SI geometry is not verified: ' + key)
    if spec.get('case_ref') != 'library/' + digest(source) + '.sim':
        raise ValueError('New CaseSpec must name its own source content identity')
    resolved = resolve_operations(PLAN, case=spec.get('case'))
    if resolved['unresolved'] or any(v['implementation'] != 'RESOLVED'
                                    for v in resolved['operations'].values()):
        raise ValueError('Selected operations have no installed executable call path')
    audited_view = {'status': 'ok', 'geometryParts': [{
        'name': actual['body_part_name'], 'isClosed': actual['is_closed'],
        'isManifold': actual['is_manifold'],
        'boundingBoxRaw': [x / unit_scale for x in actual['measured_bbox_si_m']]}], 'regions': []}
    classification = classify_parts(audited_view, geometry, unit)
    if classification['errors']:
        raise ValueError('Existing role classification refused new input: ' + repr(classification['errors']))
    validation = validate_case_spec(spec, audit=audited_view, case=spec.get('case'), plan_operations=PLAN).as_dict()
    if not validation['valid']:
        raise ValueError('New CaseSpec failed existing validation: ' + repr(validation))
    if unit != 'm':
        raise ValueError('Installed ExportExternalFlowResults requires SI metre CaseSpec; mm geometry audit is not full export compatibility')
    finite = spec['finite_execution']
    resource_contract = make_contract(spec.get('limits', {}), finite.get('resource_contract'))
    allocation_max = resource_contract['outer_timeout_seconds'] - 60
    if (not isinstance(finite['star_seconds_cap'], int) or isinstance(finite['star_seconds_cap'], bool) or not 0 < finite['star_seconds_cap'] <= allocation_max or
            isinstance(finite['max_iterations'], bool) or
            not isinstance(finite['max_iterations'], int) or not 150 <= finite['max_iterations'] <= 1000000 or
            (isinstance(finite['chunk_iterations'], bool) or not isinstance(finite['chunk_iterations'], int) or not 1 <= finite['chunk_iterations'] <= 500) or isinstance(finite['solver_wall_budget_seconds'], bool) or not isinstance(finite['solver_wall_budget_seconds'], int) or not 20 <= finite['solver_wall_budget_seconds'] < allocation_max):
        raise ValueError('Finite execution allocation differs from this version scope')
    pipeline = load(ROOT / 'cases/static-sphere-external-flow/pipeline-e2e-geometry-to-validation.json')
    pipeline = {key: copy.deepcopy(pipeline[key]) for key in ('stages', 'logical_cores')}
    # New input allocations use the same stages/runner, with their own reviewed deadlines.
    # Omitted deadlines preserve the previously verified 480-second registration.
    deadlines = ([35, 40, 90, 35, 220, 60] if finite['star_seconds_cap'] <= 1740 else [600, 900, 7200, 900, 144000, 3600])
    declared = finite.get('stage_timeout_seconds')
    if declared is not None:
        stage_names = [stage['name'] for stage in pipeline['stages']]
        if not isinstance(declared, dict) or set(declared) != set(stage_names):
            raise ValueError('Finite stage_timeout_seconds must name exactly the existing six stages')
        deadlines = [declared[name] for name in stage_names]
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 30 for v in deadlines):
            raise ValueError('Finite stage deadlines must be integer seconds at least 30')
    solve_index = next(i for i, stage in enumerate(pipeline['stages']) if stage['name'] == 'solve')
    if finite['solver_wall_budget_seconds'] >= deadlines[solve_index]:
        raise ValueError('Solver wall budget must be below its reviewed solve stage deadline')
    pipeline.update(pipeline_id=workflow_id, subplan_id='STATIC-EXPLICIT-CAD',
                    case_label=spec['objective'], production_executable=False,
                    engineering_verified=False, task_budget_seconds=finite['star_seconds_cap'] + 60,
                    env={'EXTERNAL_FLOW_CHUNK': str(finite['chunk_iterations']),
                         'EXTERNAL_FLOW_MAX_ITERATIONS': str(finite['max_iterations']),
                         'EXTERNAL_FLOW_BUDGET_SECONDS': str(finite['solver_wall_budget_seconds'])})
    resources = spec.get('limits', {})
    cores = resource_contract['logical_cores']
    memory = resource_contract['memory_gb']
    if (isinstance(cores, bool) or not isinstance(cores, int) or not 1 <= cores <= 64 or
            isinstance(memory, bool) or not isinstance(memory, (int, float)) or not 1 <= memory <= 240):
        raise ValueError('Invalid per-request resource cap')
    pipeline.update(logical_cores=cores, memory_gb=memory, resource_contract=resource_contract)
    basis = finite.get('budget_basis', 'macro_elapsed')
    expected_start = finite.get('expected_start_iteration', 0)
    every = finite.get('checkpoint_every_chunks', 0)
    if (basis not in ('macro_elapsed', 'iterator') or isinstance(expected_start, bool) or
            not isinstance(expected_start, int) or expected_start < 0 or isinstance(every, bool) or
            not isinstance(every, int) or not 0 <= every <= 1000):
        raise ValueError('Invalid segmented solve/checkpoint parameters')
    pipeline['env'].update(EXTERNAL_FLOW_BUDGET_BASIS=basis,
        EXTERNAL_FLOW_EXPECTED_START_ITERATION=str(expected_start),
        EXTERNAL_FLOW_CHECKPOINT_EVERY_CHUNKS=str(every))
    pipeline['input_hashes'] = {'static_body_geometry.sim': digest(source), 'case_spec.json': digest(spec_path)}
    # Stage semantics and quality gates are reused; replace old sample-only declarations.
    for stage, deadline in zip(pipeline['stages'], deadlines):
        stage['timeout_seconds'] = deadline
        if stage['name'] == 'solve' and finite.get('solve_timing_contract') is not None:
            stage['timing_contract'] = copy.deepcopy(finite['solve_timing_contract'])
            if stage['timing_contract'].get('macro_seconds') != finite['solver_wall_budget_seconds']:
                raise ValueError('Solver timing macro_seconds must equal its declared wall budget')
        stage['description'] = 'Existing operation, parameters/identity from this new CaseSpec; no engineering promotion.'
    geom = pipeline['stages'][0]
    geom['require_numeric'] = ['static_body_audit.json::measured_volume_relative_deviation<=0.01',
                              'static_body_audit.json::measured_centre_offset_ratio<=1',
                              'static_body_audit.json::measured_vertex_radius_error_ratio<=1']
    region = pipeline['stages'][2]
    region['require_contains'] = [v for v in region['require_contains'] if 'part_surfaces_after_split' not in v]
    region['require_numeric'] = [v for v in region['require_numeric'] if 'part_surfaces_after_split' not in v]
    region['require_numeric'] += ['external_flow_region.json::body_surface_count>=' + str(geometry['body_surface_count'])]
    pipeline['stages'][2]['require_numeric'] += [
        'external_flow_physics.json::actual_inlet_speed_m_s>=' + str(spec['flow']['velocity_m_s'][0]),
        'external_flow_physics.json::actual_inlet_speed_m_s<=' + str(spec['flow']['velocity_m_s'][0])]
    pipeline['stages'][4]['require_numeric'] = [v for v in pipeline['stages'][4]['require_numeric']
                                               if 'measured_drag_x_n_abs' not in v]
    last = pipeline['stages'][-1]
    last['macros'].append('ExportExternalFlowResults.java'); last['macro_outputs'].append(None)
    last['required'].append('external_flow_export.json')
    last['marker'] = 'EXTERNAL_FLOW_RESULTS_EXPORTED'
    last['require_contains'] += ['external_flow_export.json::"status":"ok"']
    last['require_numeric'] += ['external_flow_export.json::section_rows>=1']
    names = {n for s in pipeline['stages'] for n in s['macros']}
    pipeline['macro_hashes'] = {n: digest(ROOT / 'macros' / n) for n in sorted(names)}
    planning_pipeline = copy.deepcopy(pipeline)
    if _checkpoint_only:
        planning_pipeline['stages'] = planning_pipeline['stages'][-2:]
    if not _checkpoint_only and sum(deadlines) > finite['star_seconds_cap']:
        raise ValueError('Declared stage limits exceed finite STAR allocation')
    plan_pipeline(planning_pipeline)
    evidence.mkdir(parents=True, exist_ok=True); manifest_path.parent.mkdir(parents=True, exist_ok=True)
    pipeline_path = manifest_path.parent / 'pipeline.json'
    pipeline_path.write_text(json.dumps(pipeline, ensure_ascii=False, indent=2) + '\n')
    relative_pipeline = str(pipeline_path.relative_to(ROOT))
    manifest = {'schema_version': 2, 'id': workflow_id, 'mechanism': 'restricted_trial',
        'title': spec['objective'], 'scope': 'Exact independently reviewed source/CaseSpec; finite STEP cylinder or generated sphere chain. Not arbitrary CFD or engineering validation.',
        'source_kind': source_kind,
        'input_kind': 'audited_static_geometry_finite',
        'pipeline': relative_pipeline, 'star_seconds_cap': finite['star_seconds_cap'],
        'poll_timeout_seconds': pipeline['task_budget_seconds'], 'budget_case': spec['case'], 'mcp_evidence': str(evidence.relative_to(ROOT)),
        'input_identity_sha256': digest(source), 'case_spec_sha256': digest(spec_path),
        'source_input_name': 'static_body_geometry.sim',
        'presentation_entry': 'cases/static-cylinder-step/render_report.py',
        'adapter_files': ['scripts/server_v2.py', 'scripts/registered_workflow_mcp_v2.py',
            'scripts/run_registered_workflow_v2.py', 'scripts/resource_contract.py', 'scripts/run_registered_workflow.py',
            'scripts/registered_workflow_mcp.py', 'scripts/casespec.py', 'scripts/part_classification.py',
            'scripts/run_sensitivity_study.py', 'scripts/evaluate_sensitivity.py', 'scripts/grid_convergence.py',
            'operations/catalog.json', 'cases/static-cylinder-step/render_report.py',
            'cases/static-sphere-external-flow/pipeline-e2e-geometry-to-validation.json',
            str(spec_path.relative_to(ROOT)), str(contract_path.relative_to(ROOT)),
            str(record.relative_to(ROOT)), relative_pipeline,
            str((evidence / 'candidate.json').relative_to(ROOT))],
        'capability_levels': {'interface_exists': True, 'compiled': False, 'isolated_trial_run': False,
                              'general_operation_acceptance': False, 'engineering_validated': False},
        'paired_counterexample_scope': 'Geometry mismatch must be independently evidenced; no global generalization claim.'}
    manifest['adapter_files'] = list(dict.fromkeys(manifest['adapter_files'] +
        [str(p.relative_to(ROOT)) for p in proof_paths]))
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    material = {'state': 'PENDING_INDEPENDENT_REVIEW', 'approved': False, 'workflow_id': workflow_id,
        'source_sim': str(source), 'case_spec_path': str(spec_path), 'geometry_citation': citation,
        'source_kind': source_kind, 'source_citations': source_citations,
        'input_hashes': pipeline['input_hashes'], 'pipeline_sha256': digest(pipeline_path),
        'resolved_operations': resolved, 'part_classification': classification,
        'audited_view_source': str(record), 'full_engineering_plan_validation': validation,
        'finite_scope': 'Authorized finite process chain only; missing engineering studies are not waived.',
        'star_started': False, 'manifest': str(manifest_path), 'macro_plan': plan_pipeline(pipeline)}
    (evidence / 'candidate.json').write_text(json.dumps(material, ensure_ascii=False, indent=2) + '\n')
    return material



def prepare_checkpoint_candidate(source_sim, case_spec_path, workflow_id, geometry_record, geometry_contract, evidence):
    manifest_path = registration_path(workflow_id)
    candidate_path = repo_path(evidence, 'test-evidence') / 'candidate.json'
    if manifest_path.exists() or (manifest_path.parent / 'pipeline.json').exists() or candidate_path.exists():
        raise ValueError('Checkpoint candidate already exists; preserve prior bytes')
    try:
        return _prepare_checkpoint_candidate(source_sim, case_spec_path, workflow_id, geometry_record, geometry_contract, evidence)
    except Exception:
        # Only this new, unpublished candidate is removed; historical runs/approvals are never touched.
        for created in (manifest_path, manifest_path.parent / 'pipeline.json', candidate_path):
            if created.exists():
                created.unlink()
        raise


def validate_checkpoint_source(source_sim, case_spec_path, geometry_contract):
    """Reuse preparation's existing immutable checkpoint identity gate without writes."""
    return _validate_solve_source(source_sim, case_spec_path, geometry_contract, readback=False)


def validate_solved_readback_source(source_sim, case_spec_path, geometry_contract):
    """A read-only request uses the parent's exact executed CaseSpec, without continuation rewrites."""
    result = _validate_solve_source(source_sim, case_spec_path, geometry_contract, readback=True)
    source, spec_path, _, contract, spec, record, _, measurement, original_spec_path = result
    if contract.get('input_kind') != 'successful_solved_readback':
        raise ValueError('Readback input kind differs')
    if digest(spec_path) != digest(original_spec_path):
        raise ValueError('Readback CaseSpec bytes must equal the parent executed CaseSpec')
    state_path, state, error = find_run_state(record)
    if error or run_verdict(state)[0] != 'SUCCESS':
        raise ValueError('Readback parent must have a successful terminal run state')
    validation_path = record.parent / 'external_flow_validation.json'
    validation_citation = assert_citable(validation_path)
    validation = load(validation_path)
    iteration = measurement.get('end_iteration')
    if (validation_citation.get('stage') != 'validation' or isinstance(iteration, bool) or
            not isinstance(iteration, int) or iteration <= 0 or
            validation.get('iteration') != iteration or validation.get('cells', 0) <= 0 or
            validation.get('input_sim_sha256') != digest(source) or
            spec.get('scenario') != 'external_static' or spec.get('model_length_unit') != 'm'):
        raise ValueError('Readback parent validation or solved static SI identity differs')
    return result


def _validate_solve_source(source_sim, case_spec_path, geometry_contract, *, readback):
    source, spec_path, contract_path = map(repo_path, (source_sim, case_spec_path, geometry_contract))
    contract, spec = load(contract_path), load(spec_path)
    record = repo_path(contract['checkpoint_record'])
    citation = assert_citable(record)
    measurement = load(record)
    stage_record = load(repo_path(citation['stage_record']))
    if (citation.get('stage') != 'solve' or stage_record.get('output_sha256') != digest(source) or
            (not readback and measurement.get('end_iteration') != spec['finite_execution'].get('expected_start_iteration'))):
        raise ValueError('Checkpoint source/start does not bind certified solve output')
    original_spec_path = repo_path(contract['executed_case_spec'])
    original = load(original_spec_path)
    config_path = repo_path(contract['executed_config'])
    owned_config = record.parent.parent / (record.parent.name + '-config.json')
    if config_path != owned_config.resolve():
        raise ValueError('Checkpoint config must belong to the certified run')
    config = load(config_path)
    original_review = load(record.parent.parent / 'pipeline-review.json')
    if (original_review.get('approved') is not True or original_review.get('independent') is not True or
            original_review.get('input_hashes') != config.get('input_hashes') or
            original_review.get('macro_hashes') != config.get('macro_hashes') or
            original_review['input_hashes'].get('case_spec.json') != digest(original_spec_path) or
            config.get('job_dir') != stage_record.get('job_dir')):
        raise ValueError('Checkpoint config/spec does not bind original execution approval')
    if config['input_hashes']['case_spec.json'] != digest(original_spec_path):
        raise ValueError('Checkpoint executed CaseSpec differs from original config')
    for key in ('geometry', 'flow', 'numerics', 'domain', 'mesh', 'model_length_unit',
                'region_name', 'body_wall_boundary_name', 'reference_area_m2', 'boundary_roles'):
        if original.get(key) != spec.get(key):
            raise ValueError('Checkpoint physical/mesh identity changed: ' + key)
    return source, spec_path, contract_path, contract, spec, record, citation, measurement, original_spec_path


READBACK_BASE_ARTIFACTS = {'property_nearwall_readback.json', 'nearwall-face-values.csv'}
READBACK_DIAGNOSTIC_ARTIFACTS = READBACK_BASE_ARTIFACTS | {
    'available-native-fields.csv', 'diagnostic-fixed-points.csv', 'diagnostic-volume-cells.csv',
    *('diagnostic-boundary-%d.csv' % index for index in range(6))}


def valid_readback_environment_artifacts(pipeline):
    """Only the existing read-only export and exact optional diagnostic contract."""
    env = pipeline.get('env')
    if env == {'PROPERTY_NEARWALL_READBACK_ONLY': '1'}:
        return set(pipeline['stages'][0].get('required', [])) == READBACK_BASE_ARTIFACTS
    if env == {'PROPERTY_NEARWALL_READBACK_ONLY': '1', 'STATIC_FLOW_DIAGNOSTICS': '1'}:
        return set(pipeline['stages'][0].get('required', [])) == READBACK_DIAGNOSTIC_ARTIFACTS
    return False


def readback_deadline(contract):
    allocation = contract.get('resource_contract')
    if allocation is not None:
        from resource_contract import validate_contract
        validate_contract(allocation)
    maximum = allocation['outer_timeout_seconds'] - 60 if allocation else 90
    value = contract.get('readback_deadline_seconds', 600 if allocation else 60)
    if isinstance(value, bool) or not isinstance(value, int) or not 30 <= value <= maximum:
        raise ValueError('Readback deadline must fit the source resource contract or historical 30–90 seconds')
    return value


def validate_readback_deadline_contract(manifest, pipeline):
    contract = load(repo_path(manifest['checkpoint_contract']))
    deadline = readback_deadline(contract)
    allocation = validate_pipeline_resources(pipeline, deadline)
    if contract.get('resource_contract') is not None and allocation != contract['resource_contract']:
        raise ValueError('Readback source and pipeline resource contracts differ')
    if (len(pipeline['stages']) != 1 or
            pipeline['stages'][0]['timeout_seconds'] != deadline or
            manifest['star_seconds_cap'] != deadline or
            pipeline['task_budget_seconds'] != deadline + 60 or
            manifest['poll_timeout_seconds'] != deadline + 60 or
            not valid_readback_environment_artifacts(pipeline)):
        raise ValueError('Readback deadline, task protection or artifact/environment contract differs')
    return deadline


def prepare_solved_readback_candidate(source_sim, case_spec_path, workflow_id, geometry_record, geometry_contract, evidence):
    """Register the existing 60-second property exporter on a certified successful solution."""
    source, spec_path, contract_path, contract, spec, record, citation, measurement, original_spec_path = validate_solved_readback_source(source_sim, case_spec_path, geometry_contract)
    manifest_path = registration_path(workflow_id)
    evidence = repo_path(evidence, 'test-evidence')
    if manifest_path.exists() or (manifest_path.parent / 'pipeline.json').exists() or (evidence / 'candidate.json').exists():
        raise ValueError('Readback candidate already exists; preserve prior bytes')
    from run_sensitivity_study import checkpoint_preprocessing
    lineage = checkpoint_preprocessing(record.parent.parent, record.parent.name)
    geometry_source = repo_path(contract['geometry_source_sim'])
    geometry_sha = digest(geometry_source)
    geometry_path = repo_path(geometry_record)
    expected_audit = Path(lineage.get('record_paths', {}).get('static_body_audit.json', record.parent / 'static_body_audit.json')).resolve()
    geometry_citation = assert_citable(geometry_path)
    original_config_path = repo_path(contract['executed_config'])
    original_config = load(original_config_path)
    if (geometry_path != expected_audit or
            geometry_sha != (lineage.get('geometry_sha256') or original_config['input_hashes'].get('static_body_geometry.sim'))):
        raise ValueError('Readback geometry does not bind the certified preprocessing lineage')
    template_path = ROOT / 'cases/static-cylinder-step/engineering-preparation-20261007/property-pipeline-template.json'
    pipeline = load(template_path)
    stages = pipeline['stages']
    if (len(stages) != 1 or stages[0]['name'] != 'property-nearwall' or
            stages[0]['input'] != 'external_flow_solved.sim' or stages[0]['output'] is not None or
            stages[0]['macros'] != ['ExportExternalFlowResults.java'] or stages[0]['macro_outputs'] != [None] or
            stages[0]['timeout_seconds'] != 60 or not valid_readback_environment_artifacts(pipeline) or
            pipeline.get('logical_cores') != 8 or pipeline.get('memory_gb') != 64):
        raise ValueError('Readback template differs from the existing finite read-only contract')
    deadline = readback_deadline(contract)
    stages[0]['timeout_seconds'] = deadline
    allocation = make_contract(spec.get('limits', {}), contract.get('resource_contract'))
    pipeline.update(resource_contract=allocation, logical_cores=allocation['logical_cores'], memory_gb=allocation['memory_gb'])
    pipeline['task_budget_seconds'] = deadline + 60
    pipeline.update(pipeline_id=workflow_id, case_label='当前认证解物性与近壁只读回读；不证明工程验证', engineering_verified=False)
    pipeline.pop('state', None)
    pipeline['input_hashes'] = {'external_flow_solved.sim': digest(source), 'case_spec.json': digest(spec_path)}
    pipeline['macro_hashes'] = {'ExportExternalFlowResults.java': digest(ROOT / 'macros/ExportExternalFlowResults.java')}
    stages[0]['require_contains'] += [
        'property_nearwall_readback.json::"input_sim_sha256":"' + digest(source) + '"',
        'property_nearwall_readback.json::"input_sim_sha256_after":"' + digest(source) + '"',
        'property_nearwall_readback.json::"case_spec_sha256":"' + digest(spec_path) + '"']
    stages[0]['require_numeric'] += [
        'property_nearwall_readback.json::start_iteration>=' + str(measurement['end_iteration']),
        'property_nearwall_readback.json::start_iteration<=' + str(measurement['end_iteration']),
        'property_nearwall_readback.json::end_iteration>=' + str(measurement['end_iteration']),
        'property_nearwall_readback.json::end_iteration<=' + str(measurement['end_iteration'])]
    macro_plan = plan_pipeline(pipeline)
    evidence.mkdir(parents=True, exist_ok=True); manifest_path.parent.mkdir(parents=True, exist_ok=True)
    pipeline_path = manifest_path.parent / 'pipeline.json'
    relative_pipeline = str(pipeline_path.relative_to(ROOT))
    dependencies = ['scripts/server_v2.py', 'scripts/registered_workflow_mcp_v2.py',
        'scripts/run_registered_workflow_v2.py', 'scripts/resource_contract.py', 'scripts/run_registered_workflow.py',
        'scripts/registered_workflow_mcp.py', 'scripts/run_sensitivity_study.py',
        'scripts/evaluate_sensitivity.py', 'scripts/grid_convergence.py', 'scripts/resource_contract.py',
        'cases/static-cylinder-step/render_report.py', str(template_path.relative_to(ROOT)),
        relative_pipeline, str((evidence / 'candidate.json').relative_to(ROOT))]
    proof_paths = [spec_path, contract_path, geometry_path, original_spec_path, original_config_path,
        record, record.parent / 'external_flow_validation.json', repo_path(citation['stage_record']),
        record.parent / 'stage-validation.json', record.parent / 'case-probe.json',
        record.parent.parent / 'pipeline-review.json', repo_path(contract['geometry_source_contract'])]
    proof_paths += [repo_path(p) for p in lineage.get('record_paths', {}).values()]
    proof_paths += [repo_path(p) for p in lineage.get('verified_dependency_paths', [])]
    supplemental_review = record.parent.parent / 'checkpoint-evidence-review.json'
    if supplemental_review.is_file():
        proof_paths.append(supplemental_review)
    dependencies += [str(p.relative_to(ROOT)) for p in proof_paths]
    manifest = {'schema_version': 2, 'id': workflow_id, 'mechanism': 'restricted_trial',
        'title': pipeline['case_label'], 'scope': 'Exact independently reviewed solved static SI input; read-only property/nearwall export, no solve or save.',
        'source_kind': 'successful_solved_readback', 'input_kind': 'successful_solved_readback',
        'pipeline': relative_pipeline, 'star_seconds_cap': deadline, 'poll_timeout_seconds': deadline + 60,
        'budget_case': spec['case'], 'mcp_evidence': str(evidence.relative_to(ROOT)),
        'input_identity_sha256': digest(source), 'case_spec_sha256': digest(spec_path),
        'source_input_name': 'external_flow_solved.sim', 'checkpoint_contract': str(contract_path.relative_to(ROOT)),
        'presentation_entry': 'cases/static-cylinder-step/render_report.py', 'adapter_files': list(dict.fromkeys(dependencies)),
        'paired_counterexample_scope': 'Exact solved input and executed CaseSpec bytes, successful whole-run source, and certified lineage; changed or failed sources are refused.',
        'capability_levels': {'interface_exists': True, 'compiled': False, 'isolated_trial_run': False,
            'general_operation_acceptance': False, 'engineering_validated': False}}
    candidate = {'state': 'PENDING_INDEPENDENT_REVIEW', 'approved': False, 'workflow_id': workflow_id,
        'source_sim': str(source), 'case_spec_path': str(spec_path), 'audited_view_source': str(geometry_path),
        'source_kind': 'successful_solved_readback', 'readback_deadline_seconds': deadline, 'geometry_citation': geometry_citation,
        'checkpoint_citation': citation, 'checkpoint_preprocessing': lineage,
        'input_hashes': pipeline['input_hashes'], 'macro_plan': macro_plan, 'star_started': False,
        'manifest': str(manifest_path), 'finite_scope': 'Read-only certified solution; no continuation, solve or engineering promotion'}
    pipeline_path.write_text(json.dumps(pipeline, ensure_ascii=False, indent=2) + '\n')
    candidate['pipeline_sha256'] = digest(pipeline_path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    (evidence / 'candidate.json').write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + '\n')
    return candidate


def _prepare_checkpoint_candidate(source_sim, case_spec_path, workflow_id, geometry_record, geometry_contract, evidence):
    """New request on a certified checkpoint, never restart an old FAILED request."""
    source, spec_path, contract_path, contract, spec, record, citation, measurement, original_spec_path = validate_checkpoint_source(source_sim, case_spec_path, geometry_contract)
    deadlines = spec['finite_execution'].get('stage_timeout_seconds', {})
    if (set(deadlines) != {'audit-body','build-domain','region-physics-mesh','mesh-quality-audit','solve','validation'} or
            deadlines['solve'] + deadlines['validation'] > spec['finite_execution']['star_seconds_cap']):
        raise ValueError('Checkpoint stages exceed per-request cap before candidate creation')
    candidate = prepare_candidate(contract['geometry_source_sim'], spec_path, workflow_id,
        geometry_record, contract['geometry_source_contract'], evidence, _checkpoint_only=True)
    manifest_path = registration_path(workflow_id)
    manifest = load(manifest_path); pipeline_path = ROOT / manifest['pipeline']; pipeline = load(pipeline_path)
    pipeline['stages'] = pipeline['stages'][-2:]
    if sum(stage['timeout_seconds'] for stage in pipeline['stages']) > spec['finite_execution']['star_seconds_cap']:
        raise ValueError('Checkpoint stages exceed per-request cap')
    pipeline['stages'][0]['input'] = 'solve_input_checkpoint.sim'
    start = measurement['end_iteration']; maximum = spec['finite_execution']['max_iterations']
    for stage in pipeline['stages']:
        stage['require_numeric'] = [value for value in stage.get('require_numeric', [])
            if not any(k in value for k in ('::iterations_run', '::end_iteration', '::iteration>='))]
    pipeline['stages'][0]['require_numeric'] += [
        'external_flow_solve.json::iterations_run>=1',
        'external_flow_solve.json::start_iteration>=' + str(start),
        'external_flow_solve.json::start_iteration<=' + str(start),
        'external_flow_solve.json::end_iteration<=' + str(start + maximum)]
    pipeline['stages'][1]['require_numeric'] += ['external_flow_validation.json::iteration>=' + str(start + 1)]
    pipeline['input_hashes'] = {'solve_input_checkpoint.sim': digest(source), 'case_spec.json': digest(spec_path)}
    names = {m for stage in pipeline['stages'] for m in stage['macros']}
    pipeline['macro_hashes'] = {m: digest(ROOT / 'macros' / m) for m in sorted(names)}
    plan_pipeline(pipeline)
    manifest['source_input_name'] = 'solve_input_checkpoint.sim'
    manifest['input_identity_sha256'] = digest(source)
    manifest['input_kind'] = 'successful_solve_checkpoint'
    manifest['checkpoint_contract'] = str(contract_path.relative_to(ROOT))
    manifest['adapter_files'] += [str(f.relative_to(ROOT)) for f in (contract_path, record, original_spec_path,
        repo_path(contract['executed_config']), repo_path(citation['stage_record']), record.parent.parent / 'pipeline-review.json')]
    candidate.update(source_sim=str(source), input_hashes=pipeline['input_hashes'],
        pipeline_sha256='', checkpoint_citation=citation, source_kind='successful_solve_checkpoint',
        macro_plan=plan_pipeline(pipeline), finite_scope='Certified checkpoint finite continuation; not complete new geometry plan or recovery acceptance')
    pipeline_path.write_text(json.dumps(pipeline, ensure_ascii=False, indent=2) + '\n')
    candidate['pipeline_sha256'] = digest(pipeline_path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    (repo_path(evidence) / 'candidate.json').write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + '\n')
    return candidate


def prepare(workflow, source, spec, evidence, trial_id):
    manifest_path = registration_path(workflow); manifest = load(manifest_path)
    from registered_workflow_mcp_v2 import checked_registration
    checked_registration(workflow)
    pipeline = load(ROOT / manifest['pipeline'])
    validate_local_mesh_controls(load(repo_path(spec)))
    if manifest.get('source_kind') == 'successful_solved_readback':
        validate_readback_deadline_contract(manifest, pipeline)
    cap = manifest['star_seconds_cap']
    resource_contract = validate_pipeline_resources(pipeline, cap)
    cap_max = resource_contract['outer_timeout_seconds'] - 60 if resource_contract else 1740
    deadlines = [stage['timeout_seconds'] for stage in pipeline['stages']]
    if (isinstance(cap, bool) or not isinstance(cap, int) or not 0 < cap <= cap_max or
            any(isinstance(v, bool) or not isinstance(v, int) or v < 30 for v in deadlines) or
            sum(deadlines) > cap):
        raise ValueError('Registered stage deadlines exceed the candidate STAR cap')
    if (manifest.get('presentation_entry') not in manifest.get('adapter_files', []) or
            not (ROOT / manifest['presentation_entry']).is_file()):
        raise ValueError('Presentation entry is missing or outside reviewed adapter files')
    plan_pipeline(pipeline)
    supplied = {manifest['source_input_name']: Path(source).resolve(), 'case_spec.json': Path(spec).resolve()}
    for name, path in supplied.items():
        if digest(path) != pipeline['input_hashes'][name]:
            raise ValueError('Submitted input differs from independently reviewed candidate: ' + name)
    check_review(Path(evidence), pipeline['macro_hashes'], pipeline['input_hashes'],
        digest(ROOT / 'scripts/security/trial_air3b_pipeline.ps1'),
        digest(ROOT / 'scripts/air3b_pipeline_trial.py'), digest(ROOT / manifest['pipeline']))
    staged = Path(evidence) / 'registered-inputs'; staged.mkdir(exist_ok=True)
    for name, path in supplied.items():
        target = staged / name
        if not target.exists(): shutil.copyfile(path, target)
        if digest(target) != pipeline['input_hashes'][name]:
            raise ValueError('Isolated candidate input hash differs: ' + name)
    (Path(evidence) / 'submitted-inputs.json').write_text(json.dumps({'inputs':[str(staged / n) for n in supplied]},indent=2)+'\n')
    return manifest, []


def publish_candidate(workflow_id):
    from registered_workflow_mcp_v2 import checked_registration
    manifest, evidence, path = checked_registration(workflow_id)
    candidate = load(evidence / 'candidate.json')
    prepare(workflow_id, candidate['source_sim'], candidate['case_spec_path'], evidence, '')
    receipt = {'state': 'REGISTERED_CURRENT_BYTES', 'workflow_id': workflow_id,
               'manifest_sha256': digest(path), 'review_sha256': digest(evidence / 'pipeline-review.json'),
               'input_hashes': candidate['input_hashes'], 'star_started': False,
               'engineering_verified': False}
    (evidence / 'registration.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['candidate','register'])
    parser.add_argument('--workflow', required=True)
    for flag in ('source-sim','case-spec-path','geometry-record','geometry-contract','evidence'):
        parser.add_argument('--'+flag)
    a=parser.parse_args()
    result = publish_candidate(a.workflow) if a.action == 'register' else prepare_candidate(
        a.source_sim,a.case_spec_path,a.workflow,a.geometry_record,a.geometry_contract,a.evidence)
    print(json.dumps(result,ensure_ascii=False,indent=2))
