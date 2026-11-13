# -*- coding: utf-8 -*-
"""Three-level mesh and domain sensitivity studies for scenario ①, reusing the existing chain.

WHAT THIS ADDS (and what it deliberately does not)
--------------------------------------------------
`domain_study` and `mesh_study` were the two operations scenario ① declared but never had. This driver
supplies them as ORCHESTRATION ONLY:

  * it reuses the reviewed macros unchanged - BuildExternalDomain, BuildExternalFlowRegion,
    ConfigureExternalFlowPhysics, BuildExternalFlowMesh, AuditExternalFlowMesh, RunExternalFlowSolve,
    AuditExternalFlowConvergence, AuditStaticBodyGeometry - by generating a per-level `case_spec.json`
    (the only thing a level changes) and a per-level pipeline whose STAGES ARE COPIED VERBATIM from the
    reviewed baseline pipeline;
  * it adds NO macro, NO audit macro and NO new validator: the numerical judgement is delegated to the
    already-existing, already-tested scripts/evaluate_sensitivity.py (relative changes) and
    scripts/grid_convergence.py (three-grid GCI, which itself refuses to certify when its applicability
    conditions fail);
  * every gate value it compares against is READ FROM THE CASESPEC (`quality.*`). This file contains no
    threshold of its own, and it never converts an unfavourable result into a favourable one. Levels that
    fail a quality gate or do not converge are CLOSED and EXCLUDED from the sensitivity conclusion.

CONFIGURATION COMES FROM THE CASESPEC: `quality.mesh_study` and `quality.domain_study` declare the levels
(name, the parameter overrides, the refinement/extent factor, and - crucially - whether a level may REUSE
an existing citable run instead of being computed again). The baseline level is reused, and the reuse is
verified field-by-field against the parameters the macros actually read before it is accepted.

INTERFACE (for the operation-catalogue work in the other window)
---------------------------------------------------------------
    python scripts/run_sensitivity_study.py --kind mesh   --prepare      # write level specs + pipelines
    python scripts/run_sensitivity_study.py --kind mesh   --submit       # submit + retrieve + report
    python scripts/run_sensitivity_study.py --kind mesh   --assemble     # rebuild record/report offline
    python scripts/run_sensitivity_study.py --kind domain --all          # prepare + submit + assemble

  --spec            study CaseSpec (default cases/static-sphere-studies/case_spec.json)
  --geometry        geometry-only .sim, identical for every level (default the round-3 build-body output)
  --baseline-evidence / --baseline-trial   the citable run reused for the baseline level
  --evidence        root under which each level gets <level>/ with its own evidence + review record
  --timeout         poll timeout per level, seconds

  Inputs per level:  <level>/case_spec.json (generated), the pinned geometry .sim, and
                     <level>/pipeline-review.json (an approved independent review of that level's bytes).
  Outputs per level: <level>/pipeline.json, the trial records, <trial>-report.md/json,
                     <trial>-checkpoints.json.
  Study outputs:     <evidence>/<kind>-study.json, <evidence>/<kind>-study.csv,
                     <evidence>/<kind>-study-report.md, <evidence>/<kind>-study-report.json,
                     <evidence>/<kind>-study-report-sha256.txt

  Failure semantics: a level that cannot be started is reported as NOT_RUN; a level whose transfer or hash
  verification failed has no usable evidence and is NOT_RUN/EXCLUDED; a level that ran but failed a
  CaseSpec quality gate or did not converge is EXCLUDED with the failing gate named. The study verdict is
  ESTABLISHED only when at least `evaluate_sensitivity.MIN_LEVELS[kind]` levels are valid; otherwise it is
  NOT_ESTABLISHED with the reason. `mesh_independent` additionally requires the GCI to be applicable
  (valid) and inside the limit_pct that grid_convergence.py itself applies.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from stage_plan import plan_pipeline                      # noqa: E402
from evaluate_sensitivity import MIN_LEVELS, compare_study  # noqa: E402
from grid_convergence import three_grid_gci               # noqa: E402
import run_record_gate                                     # noqa: E402

# The records a level's measurements are read from. Their citation state is decided by run_record_gate, the
# repository's single implementation of "a record may be cited" (AGENTS.md section 2-O8), never re-derived.
MEASUREMENT_RECORDS = ('external_flow_mesh_quality.json', 'external_flow_solve.json',
                       'external_flow_validation.json')

E2E = ROOT / 'scripts' / 'run_static_external_flow_e2e.py'
DEFAULT_SPEC = ROOT / 'cases' / 'static-sphere-studies' / 'case_spec.json'
DEFAULT_GEOMETRY = (ROOT / 'test-evidence' / 'static-external-flow' / 'build-body-positive'
                    / 's1-static-sphere-body-20261003a' / 'static_body_geometry.sim')
DEFAULT_BASELINE_EVIDENCE = ROOT / 'test-evidence' / 'static-external-flow' / 'round10-e2e'
DEFAULT_BASELINE_TRIAL = 's1-static-sphere-e2e-20261003a'
BASELINE_PIPELINE = (ROOT / 'cases' / 'static-sphere-external-flow'
                     / 'pipeline-e2e-geometry-to-validation.json')
XKEY = {'mesh': 'cells', 'domain': 'domain_scale'}
# The quantities fed to evaluate_sensitivity.compare_study. They are the target quantities of the case,
# plus the density (a physical invariant that must not drift when only the mesh or the domain changes).
QUANTITIES = ('drag_coefficient_estimate', 'drag_x_n', 'density_kg_m3')


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def entry_module():
    """The reviewed entry command, imported so the study uses ITS transfer and report code."""
    spec = importlib.util.spec_from_file_location('e2e_entry', E2E)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------------------------------
# level generation (pure functions: the tests exercise these without STAR)
# --------------------------------------------------------------------------------------------------

def study_levels(spec: dict, kind: str) -> list[dict]:
    key = '%s_study' % kind
    block = (spec.get('quality') or {}).get(key)
    if not isinstance(block, dict) or not block.get('levels'):
        raise ValueError('CaseSpec quality.%s.levels is required' % key)
    levels = block['levels']
    names = [level.get('name') for level in levels]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
        raise ValueError('%s levels need distinct names' % key)
    return levels


def level_case_spec(spec: dict, kind: str, level: dict) -> dict:
    """The study CaseSpec with ONLY this level's parameter overrides applied.

    Mesh levels override the three mesh sizes (and nothing else); domain levels override
    domain.outer_bounds_m (and nothing else). Everything else - geometry, flow, numerics, quality gates,
    region and boundary names - is copied unchanged, which is what makes the study a one-parameter study.
    """
    out = copy.deepcopy(spec)
    # The declared level keys deliberately AVOID the macro-visible names (see quality.*._key_naming): the
    # macros read the CaseSpec with a first-match regex, so reusing a macro-visible name inside a nested
    # block hijacks the read. Here they are mapped onto the top-level macro-visible keys, which each occur
    # exactly once in the generated file (pinned by tests/test_sensitivity_study.py).
    if kind == 'mesh':
        for declared, target in (('base_size_m', 'mesh_base_size_m'),
                                 ('body_target_size_m', 'mesh_body_target_size_m'),
                                 ('body_minimum_size_m', 'mesh_body_minimum_size_m')):
            if declared not in level:
                raise ValueError('mesh level %s lacks %s' % (level['name'], declared))
            out[target] = float(level[declared])
        if 'prism_layers' in level:
            out['mesh_prism_layers'] = int(level['prism_layers'])
        for declared, target in (
                ('nearfield_bounds_m', 'mesh_nearfield_bounds_m'),
                ('nearfield_cell_size_m', 'mesh_nearfield_cell_size_m'),
                ('wake_bounds_m', 'mesh_wake_bounds_m'),
                ('wake_cell_size_m', 'mesh_wake_cell_size_m'),
                ('prism_first_layer_height_m', 'mesh_prism_first_layer_height_m'),
                ('prism_total_thickness_m', 'mesh_prism_total_thickness_m')):
            if declared in level: out[target] = copy.deepcopy(level[declared])
    elif kind == 'domain':
        bounds = level.get('bounds_m')
        if not (isinstance(bounds, list) and len(bounds) == 6):
            raise ValueError('domain level %s needs bounds_m[6]' % level['name'])
        out.setdefault('domain', {})['outer_bounds_m'] = [float(value) for value in bounds]
    else:
        raise ValueError('kind must be mesh or domain')
    out['_study_level'] = {'study': kind, 'name': level['name'],
                           'of_spec': spec.get('_study_id') or spec.get('case') or 'study'}
    from run_registered_workflow_v2 import validate_local_mesh_controls
    validate_local_mesh_controls(out)
    return out


def level_pipeline(base_pipeline: dict, spec: dict, kind: str, level: dict, case_spec_path: Path,
                   geometry_path: Path, trial_id: str, macro_hashes: dict) -> dict:
    """A per-level pipeline whose STAGES are the reviewed baseline pipeline's stages, verbatim."""
    doc = copy.deepcopy(base_pipeline)
    from resource_contract import make_contract, validate_pipeline_resources
    allocation = make_contract(spec.get('limits', {}), spec.get('finite_execution', {}).get('resource_contract'))
    doc.update(resource_contract=allocation, logical_cores=allocation['logical_cores'], memory_gb=allocation['memory_gb'])
    doc.setdefault('task_budget_seconds', sum(s['timeout_seconds'] for s in doc['stages']) + 60)
    validate_pipeline_resources(doc)
    doc['pipeline_id'] = trial_id
    doc['case_label'] = '场景① %s敏感性研究 %s（%s）' % (
        '网格' if kind == 'mesh' else '计算域', level['name'], spec.get('_study_id', 'study'))
    doc['status'] = 'NOT_EXECUTED'
    doc['description'] = (
        'Current-source %s sensitivity study, level %r. Stages are copied verbatim from the explicitly '
        'selected pipeline. This level CaseSpec declares %s; reuse is allowed only after identity, '
        'parameters, provenance, citation and quality checks.'
        % (kind, level['name'],
           'mesh sizes' if kind == 'mesh' else 'outer domain bounds'))
    doc['macro_hashes'] = dict(macro_hashes)
    doc['input_hashes'] = {'case_spec.json': sha256(case_spec_path),
                           geometry_path.name: sha256(geometry_path)}
    return doc


def _strip_time_budget(pipeline: dict) -> dict:
    """写出的层级 pipeline 不得声明**启用墙钟停止**的那个键（用户 2026-10-14 撤销全部时间预算）。

    独立审查 2026-10-14 指出两件事，本实现按第二版处理：
      ① 历史源（如本算例的 pipeline-domain-mesh.json）仍带 EXTERNAL_FLOW_BUDGET_SECONDS=480，层级生成器一旦
         原样继承就把已撤销的时间预算带回来；
      ② 上一版按"键名含 BUDGET"删除，口径过宽——EXTERNAL_FLOW_BUDGET_BASIS 是**功能键**（选 macro_elapsed /
         iterator 两种时钟基准，被 RunExternalFlowSolve.java 与 run_registered_workflow_v2.py 消费），不该被静默剥掉。
    现在只删 EXTERNAL_FLOW_BUDGET_SECONDS（正是它把墙钟变成停止依据）；其余键原样保留，并在记录里说明。
    """
    env = dict(pipeline.get('env') or {})
    if 'EXTERNAL_FLOW_BUDGET_SECONDS' not in env:
        return pipeline
    kept = sorted(key for key in env if 'BUDGET' in key.upper()
                  and key != 'EXTERNAL_FLOW_BUDGET_SECONDS')
    env.pop('EXTERNAL_FLOW_BUDGET_SECONDS', None)
    out = dict(pipeline)
    out['env'] = env
    out['_time_budget_stripped'] = (
        '层级生成器移除了 EXTERNAL_FLOW_BUDGET_SECONDS（用户 2026-10-14 撤销全部时间预算；停止依据是数值门/'
        '迭代上限/诊断，不是墙钟）；其余 BUDGET 相关键原样保留：%r（它们是功能键，不是停止依据）。' % kept)
    return out


def prepare(study_spec_path: Path, kind: str, geometry: Path, out_root: Path, trial_prefix: str,
            macro_hashes: dict, trial_suffix: str = 'a',
            pipeline_path: Path | None = None, source_contract: Path | None = None) -> list[dict]:
    """Write <out_root>/<level>/case_spec.json and pipeline.json for every level. Returns descriptors."""
    spec = read_json(study_spec_path)
    base_pipeline = read_json(pipeline_path or BASELINE_PIPELINE)
    # 2026-10-14（独立审查）：层级链会继承源的 env 与阶段描述。历史上的源里有
    # EXTERNAL_FLOW_BUDGET_SECONDS=480 与写死 480/540 的描述，新链一旦复制就会把**已撤销**的
    # 时间预算带回来——那正是花了三轮才止住的复发。
    # 处置：不拒绝读取历史源（那些 release 夹具各自被记录 pin 住，改写字节会作废历史证据），
    # 而是在**生成端**把它清掉：下面 _strip_time_budget() 保证写出的层级 pipeline 不含任何
    # BUDGET 键；时间值若需要，只作记录字段出现。
    source = read_json(source_contract) if source_contract else None
    if source is not None:
        source_hash = source.get('source_sim_sha256')
        if not source_hash or source_hash != sha256(geometry):
            raise ValueError('source contract geometry SHA-256 mismatch or missing')
        identity_hash = (spec.get('input_identity') or {}).get('source_sim_sha256')
        if identity_hash != source_hash:
            raise ValueError('CaseSpec input_identity.source_sim_sha256 mismatch or missing')
        for key, actual in (('body_part_name', spec.get('geometry', {}).get('body_part_name')),
                            ('shape', spec.get('geometry', {}).get('body_shape'))):
            if source.get(key) is not None and source[key] != actual:
                raise ValueError('source contract %s does not match CaseSpec' % key)
    made = []
    for level in study_levels(spec, kind):
        name = level['name']
        level_dir = out_root / name
        level_dir.mkdir(parents=True, exist_ok=True)
        case_spec = level_case_spec(spec, kind, level)
        case_spec_path = level_dir / 'case_spec.json'
        case_spec_path.write_text(json.dumps(case_spec, ensure_ascii=False, indent=2) + '\n',
                                  encoding='utf-8')
        if level.get('reuse'):
            made.append({'kind': kind, 'level': name, 'dir': str(level_dir), 'reuse': level['reuse'],
                         'case_spec': str(case_spec_path), 'pipeline': None,
                         'case_spec_sha256': sha256(case_spec_path)})
            continue
        trial_id = '%s-%s-%s' % (trial_prefix, name.replace('.', 'p'), trial_suffix)
        pipeline = level_pipeline(base_pipeline, spec, kind, level, case_spec_path, geometry, trial_id,
                                  macro_hashes)
        pipeline = _strip_time_budget(pipeline)
        generated_pipeline_path = level_dir / 'pipeline.json'
        generated_pipeline_path.write_text(json.dumps(pipeline, ensure_ascii=False, indent=2) + '\n',
                                 encoding='utf-8')
        plan_pipeline(pipeline)          # refuse a level whose chain cannot be isolated, before upload
        made.append({'kind': kind, 'level': name, 'dir': str(level_dir), 'reuse': None,
                     'case_spec': str(case_spec_path), 'pipeline': str(generated_pipeline_path),
                     'trial_id': trial_id, 'case_spec_sha256': sha256(case_spec_path),
                     'pipeline_sha256': sha256(generated_pipeline_path)})
    (out_root / ('%s-levels.json' % kind)).write_text(
        json.dumps({'kind': kind, 'spec': str(study_spec_path), 'spec_sha256': sha256(study_spec_path),
                    'geometry': str(geometry), 'geometry_sha256': sha256(geometry),
                    'pipeline': str(pipeline_path or BASELINE_PIPELINE),
                    'pipeline_sha256': sha256(pipeline_path or BASELINE_PIPELINE),
                    'source_contract': str(source_contract) if source_contract else None,
                    'source_contract_sha256': sha256(source_contract) if source_contract else None,
                    'levels': made},
                   ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return made


# --------------------------------------------------------------------------------------------------
# measurement collection and the CaseSpec gates
# --------------------------------------------------------------------------------------------------

def reused_reference(baseline_evidence: Path, baseline_trial: str, level: dict,
                     case_spec: dict, geometry: Path) -> dict:
    """Verify that a reused level really is the same configuration, field by field.

    A byte-identical CaseSpec is NOT required (the generated level spec carries study metadata); what is
    required is that every parameter the chain's macros read has the same VALUE as the run being reused,
    and that the geometry the reused run consumed is the same file. Both are checked against that run's
    own inputs, not against a claim in the study spec.
    """
    ref = level['reuse']
    evidence = Path(ref.get('evidence', baseline_evidence))
    if not evidence.is_absolute():
        evidence = ROOT / evidence
    trial_dir = evidence / ref.get('trial_id', baseline_trial)
    if not trial_dir.is_dir():
        return {'ok': False, 'differences': ['reuse evidence directory missing'], 'reason': 'reuse evidence directory missing: %s' % trial_dir}
    if not (trial_dir / 'case_spec.json').is_file():
        return {'ok': False, 'differences': ['reused CaseSpec missing'], 'reason': 'reused CaseSpec missing'}
    baseline_spec = read_json(trial_dir / 'case_spec.json')
    config_path = evidence / ('%s-config.json' % ref.get('trial_id', baseline_trial))
    config = read_json(config_path) if config_path.is_file() else {}
    baseline_inputs = config.get('input_hashes') or {}
    compared, differences = {}, []
    candidate_path = evidence / 'candidate.json'
    candidate = read_json(candidate_path) if candidate_path.is_file() else {}
    is_checkpoint = candidate.get('source_kind') == 'successful_solve_checkpoint'
    lineage = {}
    if is_checkpoint:
        try:
            lineage = checkpoint_preprocessing(evidence, ref.get('trial_id', baseline_trial))
            for producer in ('AuditStaticBodyGeometry.java', 'BuildExternalDomain.java', 'BuildExternalFlowRegion.java', 'BuildExternalFlowMesh.java', 'AuditExternalFlowMesh.java', 'ConfigureExternalFlowPhysics.java'):
                if lineage.get('original_preprocessing_macro_hashes', {}).get(producer) != sha256(ROOT / 'macros' / producer):
                    differences.append('checkpoint preprocessing producer revision differs: ' + producer)
            review = read_json(evidence / 'pipeline-review.json')
            if any(config.get(k) != review.get(k) for k in ('input_hashes', 'macro_hashes')):
                differences.append('checkpoint current execution differs from independent review')
        except (ValueError, KeyError, OSError, TypeError, run_record_gate.RecordNotCitable) as exc:
            differences.append('checkpoint lineage: ' + str(exc))
    if baseline_inputs.get('case_spec.json') != sha256(trial_dir / 'case_spec.json'):
        differences.append('reused CaseSpec SHA-256 does not match executed input pin')
    for field in ('mesh_base_size_m', 'mesh_body_target_size_m', 'mesh_body_minimum_size_m',
                  'mesh_prism_layers', 'mesh_nearfield_bounds_m', 'mesh_nearfield_cell_size_m', 'mesh_wake_bounds_m', 'mesh_wake_cell_size_m', 'mesh_prism_first_layer_height_m', 'mesh_prism_total_thickness_m'):
        left, right = case_spec.get(field), baseline_spec.get(field)
        compared[field] = [left, right]
        if left != right or (left is None and field in ('mesh_base_size_m', 'mesh_body_target_size_m', 'mesh_body_minimum_size_m', 'mesh_prism_layers')):
            differences.append(field)
    left_bounds = (case_spec.get('domain') or {}).get('outer_bounds_m')
    right_bounds = (baseline_spec.get('domain') or {}).get('outer_bounds_m')
    compared['domain.outer_bounds_m'] = [left_bounds, right_bounds]
    if left_bounds != right_bounds:
        differences.append('domain.outer_bounds_m')
    for block in ('geometry', 'flow', 'numerics', 'model_length_unit', 'geometry_source_format',
                  'region_name', 'body_wall_boundary_name', 'boundary_roles'):
        compared[block] = [case_spec.get(block) == baseline_spec.get(block)]
        if case_spec.get(block) != baseline_spec.get(block):
            differences.append(block)
    geometry_sha = sha256(geometry)
    reused_geometry_pin = (lineage.get('geometry_sha256') if is_checkpoint
                           else baseline_inputs.get(Path(geometry).name))
    compared['reused_run_input_hashes'] = [geometry_sha, reused_geometry_pin]
    if reused_geometry_pin != geometry_sha:
        differences.append('geometry input hash: study %s vs reused run %s'
                           % (geometry_sha[:16], (reused_geometry_pin or 'absent')[:16]))
    # The macros whose RECORDS this level's measurements are read from must be the live revisions; a
    # differing revision elsewhere in the reused run is recorded, not silently ignored.
    macro_hashes = config.get('macro_hashes') or {}
    live = {name: sha256(ROOT / 'macros' / name) if (ROOT / 'macros' / name).is_file() else None
            for name in macro_hashes} if macro_hashes else {}
    measured_by = ('BuildExternalFlowRegion.java', 'ConfigureExternalFlowPhysics.java',
                   'BuildExternalFlowMesh.java', 'AuditExternalFlowMesh.java',
                   'RunExternalFlowSolve.java', 'AuditExternalFlowConvergence.java')
    if is_checkpoint:
        # Preprocessing producers are checked by the certified parent chain; these records are current.
        measured_by = ('RunExternalFlowSolve.java', 'AuditExternalFlowConvergence.java')
    compared['reused_run_macro_hashes'] = {name: [macro_hashes.get(name), live.get(name)]
                                           for name in sorted(set(macro_hashes) | set(live))}
    for name in measured_by:
        if not macro_hashes.get(name) or not live.get(name) or macro_hashes.get(name) != live.get(name):
            differences.append('macro revision that produced a recorded quantity differs: %s' % name)
    if not macro_hashes:
        # The round-13 review measured this: with no macro_hashes in the reused config the comparison was
        # None == None and the reuse was ACCEPTED. Absent provenance is a refusal, not an agreement.
        differences.append('reused run config carries no macro_hashes: macro provenance cannot be checked')
    other_differing = sorted(name for name in set(macro_hashes) | set(live)
                             if macro_hashes.get(name) != live.get(name) and name not in measured_by)
    return {'ok': not differences, 'differences': differences, 'compared': compared,
            'reused_trial_dir': str(trial_dir), 'checkpoint_lineage': lineage,
            'reused_case_spec_sha256': sha256(trial_dir / 'case_spec.json'),
            'reused_config': str(config_path),
            'reused_macros_measured_by_this_level': list(measured_by),
            'reused_macro_revisions_differing_elsewhere': other_differing,
            'note': ('a macro revision differing OUTSIDE measured_by is recorded but does not invalidate the '
                     'reuse, because none of this level\'s measured quantities come from that stage')}


def checkpoint_preprocessing(level_dir: Path, trial_id: str, seen=None) -> dict:
    """Follow the registered checkpoint contract; keep original certificate paths."""
    seen = set() if seen is None else seen
    candidate_path = level_dir / 'candidate.json'
    candidate = read_json(candidate_path) if candidate_path.is_file() else {}
    early_dependencies = []
    if candidate_path.is_file():
        early_review_path = level_dir / 'pipeline-review.json'
        early_review = read_json(early_review_path)
        candidate_pin = (early_review.get('registration_hashes', {}).get(str(candidate_path.relative_to(ROOT)))
                         or early_review.get('candidate_sha256'))
        if (early_review.get('approved') is not True or early_review.get('independent') is not True
                or not candidate_pin or candidate_pin != sha256(candidate_path)):
            raise ValueError('checkpoint candidate content binding missing or changed before source-kind selection')
        early_dependencies = [str(candidate_path), str(early_review_path)]
    if candidate.get('source_kind') != 'successful_solve_checkpoint':
        return {'verified_dependency_paths': early_dependencies} if early_dependencies else {}
    key = str((level_dir / trial_id).resolve())
    if key in seen:
        raise ValueError('checkpoint lineage cycle')
    seen.add(key)
    def resolve(value):
        path = Path(value)
        return path if path.is_absolute() else ROOT / path
    manifest = read_json(resolve(candidate['manifest']))
    contract_path = resolve(manifest['checkpoint_contract'])
    if str(contract_path.relative_to(ROOT)) not in manifest['adapter_files']:
        raise ValueError('checkpoint contract not in reviewed adapter files')
    review = read_json(level_dir / 'pipeline-review.json')
    if review.get('approved') is not True or review.get('independent') is not True:
        raise ValueError('checkpoint lineage lacks independent approval')
    if review.get('registration_hashes', {}).get(str(resolve(candidate['manifest']).relative_to(ROOT))) != sha256(resolve(candidate['manifest'])) or review.get('registration_hashes', {}).get(str(contract_path.relative_to(ROOT))) != sha256(contract_path):
        raise ValueError('checkpoint manifest or contract review hash differs')
    pins = dict(review.get('registration_hashes') or {})
    dependency_paths = {str(candidate_path), str(resolve(candidate['manifest'])), str(contract_path),
                        str(level_dir / 'pipeline-review.json')}
    content_review_path = level_dir / 'checkpoint-evidence-review.json'
    if content_review_path.is_file():
        content_review = read_json(content_review_path)
        if (content_review.get('approved') is not True or content_review.get('independent') is not True
                or content_review.get('scope') != 'INHERITED_CERTIFICATE_CONTENT_ONLY'
                or content_review.get('manifest_sha256') != sha256(resolve(candidate['manifest']))
                or content_review.get('pipeline_review_sha256') != sha256(level_dir / 'pipeline-review.json')):
            raise ValueError('checkpoint inherited content review identity differs')
        for key, value in (content_review.get('registration_hashes') or {}).items():
            if key in pins and pins[key] != value:
                raise ValueError('checkpoint inherited content review conflicts with execution approval')
            pins[key] = value
        dependency_paths.add(str(content_review_path))
    def bound(path):
        path = resolve(path)
        if pins.get(str(path.relative_to(ROOT))) != sha256(path):
            raise ValueError('checkpoint certificate content binding missing or changed: ' + str(path))
        dependency_paths.add(str(path))
        return path
    bound(candidate_path)
    contract = read_json(contract_path)
    if contract.get('input_kind') != 'successful_solve_checkpoint':
        raise ValueError('checkpoint contract input kind differs')
    record_path = bound(contract['checkpoint_record'])
    run_record_gate.assert_citable(record_path)
    record = read_json(record_path)
    stage = read_json(bound(record_path.parent / 'stage-solve.json'))
    config = read_json(level_dir / ('%s-config.json' % trial_id))
    if any(config.get(key) != review.get(key) for key in ('input_hashes', 'macro_hashes')):
        raise ValueError('checkpoint current execution differs from independent review')
    current_spec_path = level_dir / trial_id / 'case_spec.json'
    current = read_json(current_spec_path)
    parent_spec_path = bound(contract['executed_case_spec'])
    parent = read_json(parent_spec_path)
    parent_config_path = bound(contract['executed_config'])
    if parent_config_path.resolve() != (record_path.parent.parent / (record_path.parent.name + '-config.json')).resolve():
        raise ValueError('checkpoint parent config is not canonical for certified run')
    parent_config = read_json(parent_config_path)
    parent_review = read_json(bound(record_path.parent.parent / 'pipeline-review.json'))
    if parent_review.get('approved') is not True or parent_review.get('independent') is not True or any(parent_config.get(k) != parent_review.get(k) for k in ('input_hashes', 'macro_hashes')) or parent_config.get('job_dir') != stage.get('job_dir'):
        raise ValueError('checkpoint parent execution differs from independent review or stage job')
    if parent_config.get('input_hashes', {}).get('case_spec.json') != sha256(parent_spec_path):
        raise ValueError('parent CaseSpec hash differs')
    if config.get('input_hashes', {}).get('case_spec.json') != sha256(current_spec_path):
        raise ValueError('current CaseSpec hash differs')
    checkpoint_hash = stage.get('output_sha256')
    if not checkpoint_hash or config.get('input_hashes', {}).get('solve_input_checkpoint.sim') != checkpoint_hash:
        raise ValueError('checkpoint input does not match successful stage output')
    current_solve = read_json(level_dir / trial_id / 'external_flow_solve.json')
    if current_solve.get('start_iteration') != record.get('end_iteration'):
        raise ValueError('checkpoint start iteration differs')
    physical = ('geometry', 'flow', 'numerics', 'domain', 'mesh', 'model_length_unit', 'region_name',
                'body_wall_boundary_name', 'boundary_roles', 'reference_area_m2',
                'mesh_base_size_m', 'mesh_body_target_size_m', 'mesh_body_minimum_size_m', 'mesh_prism_layers', 'mesh_nearfield_bounds_m', 'mesh_nearfield_cell_size_m', 'mesh_wake_bounds_m', 'mesh_wake_cell_size_m', 'mesh_prism_first_layer_height_m', 'mesh_prism_total_thickness_m')
    if any(current.get(k) != parent.get(k) for k in physical):
        raise ValueError('checkpoint physical or mesh identity differs')
    inherited = checkpoint_preprocessing(record_path.parent.parent, record_path.parent.name, seen)
    dependency_paths.update(inherited.get('verified_dependency_paths', []))
    paths = inherited.get('record_paths', {})
    for name in ('static_body_audit.json', 'external_flow_mesh.json',
                 'external_flow_mesh_quality.json', 'external_domain_build.json', 'external_flow_physics.json'):
        paths.setdefault(name, str(record_path.parent / name))
        path = bound(paths[name])
        citation = run_record_gate.assert_citable(path)
        if citation.get('stage_record'):
            bound(citation['stage_record'])
    current_quality = read_json(Path(paths['external_flow_mesh_quality.json']))
    validation = read_json(level_dir / trial_id / 'external_flow_validation.json')
    if validation.get('cells') != current_quality.get('cells'):
        raise ValueError('checkpoint validation cell count differs from original mesh')
    geometry_hash = inherited.get('geometry_sha256') or (parent.get('input_identity') or {}).get('source_sim_sha256')
    if not geometry_hash or (current.get('input_identity') or {}).get('source_sim_sha256') != geometry_hash:
        raise ValueError('checkpoint geometry identity differs')
    if not inherited.get('record_paths') and geometry_hash not in parent_config.get('input_hashes', {}).values():
        raise ValueError('original geometry hash not bound to parent execution')
    dependency_paths.update(str(path) for path in (level_dir / ('%s-config.json' % trial_id), current_spec_path,
        level_dir / trial_id / 'external_flow_solve.json', level_dir / trial_id / 'external_flow_validation.json'))
    return {'original_preprocessing_macro_hashes': inherited.get('original_preprocessing_macro_hashes') or parent_config.get('macro_hashes', {}),
            'record_paths': paths, 'geometry_sha256': geometry_hash,
            'checkpoint_record': str(record_path), 'checkpoint_sha256': checkpoint_hash,
            'verified_dependency_paths': sorted(dependency_paths),
            'engineering_conclusion_reused': False}


def collect_metrics(level_dir: Path, trial_id: str) -> dict:
    """Read a level's own records. Missing keys are recorded as None, never invented."""
    trial = level_dir / trial_id
    out = {'trial_id': trial_id, 'trial_dir': str(trial), 'records_present': trial.is_dir()}
    if not trial.is_dir():
        return out
    try:
        lineage = checkpoint_preprocessing(level_dir, trial_id)
        out['checkpoint_lineage'] = lineage
        out['measurement_record_paths'] = lineage.get('record_paths', {})
    except (ValueError, KeyError, OSError, TypeError, run_record_gate.RecordNotCitable) as exc:
        out['checkpoint_lineage_error'] = str(exc)
    def rec(name):
        path = Path(out.get('measurement_record_paths', {}).get(name, trial / name))
        return read_json(path) if path.is_file() else None
    audit, mesh, quality = rec('static_body_audit.json'), rec('external_flow_mesh.json'), \
        rec('external_flow_mesh_quality.json')
    solve, validation, domain = rec('external_flow_solve.json'), rec('external_flow_validation.json'), \
        rec('external_domain_build.json')
    result = level_dir / ('%s-result.json' % trial_id)
    checkpoints = level_dir / ('%s-checkpoints.json' % trial_id)
    report = level_dir / ('%s-report.json' % trial_id)
    out.update({
        'audit': audit, 'mesh': mesh, 'quality': quality, 'solve': solve,
        'validation': validation, 'domain': domain,
        'result': read_json(result) if result.is_file() else None,
        'checkpoints': read_json(checkpoints) if checkpoints.is_file() else None,
        'report': read_json(report) if report.is_file() else None,
        'stage_records': sorted(p.name for p in trial.glob('stage-*.json')),
    })
    residuals = trial / 'external_flow_residuals.tsv'
    finals = {}
    if residuals.is_file():
        with residuals.open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream)
            for row in reader:
                try:
                    finals[row['name']] = float(row['value'])
                except (KeyError, TypeError, ValueError):
                    continue
        out['residuals_sha256'] = sha256(residuals)
    out['residuals_final'] = finals
    return out


def domain_scale(metrics: dict, baseline_bounds: list) -> float | None:
    """The measured domain extent factor: the x-extent of this level over the baseline's x-extent."""
    domain = metrics.get('domain') or {}
    bounds = domain.get('outer_bounds_m')
    if not (isinstance(bounds, list) and len(bounds) == 6 and isinstance(baseline_bounds, list)):
        return None
    baseline_extent = baseline_bounds[3] - baseline_bounds[0]
    if baseline_extent <= 0:
        return None
    return (bounds[3] - bounds[0]) / baseline_extent


def level_validity(metrics: dict, quality: dict, reused: bool = False) -> dict:
    """The per-level gates, every bound read from the CaseSpec's own quality block."""
    failures = []
    if metrics.get('checkpoint_lineage_error'):
        failures.append('checkpoint lineage: ' + metrics['checkpoint_lineage_error'])
    quality_record = metrics.get('quality') or {}
    solve = metrics.get('solve') or {}
    validation = metrics.get('validation') or {}
    checkpoints = metrics.get('checkpoints') or {}
    report = metrics.get('report') or {}

    def gate(name, value, bound, op):
        if (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value)
                or not isinstance(bound, (int, float)) or isinstance(bound, bool) or not math.isfinite(bound)):
            failures.append('%s missing or nonfinite measurement/CaseSpec bound' % name)
            return
        ok = value >= bound if op == '>=' else value <= bound
        if not ok:
            failures.append('%s=%r violates %s %r' % (name, value, op, bound))

    gate('mesh.measured_cell_quality_min', quality_record.get('measured_cell_quality_min'),
         quality.get('mesh_min_cell_quality'), '>=')
    gate('mesh.measured_face_validity_min', quality_record.get('measured_face_validity_min'),
         quality.get('mesh_min_face_validity'), '>=')
    gate('mesh.measured_volume_change_min', quality_record.get('measured_volume_change_min'),
         quality.get('mesh_min_volume_change'), '>=')
    gate('mesh.measured_wall_prism_layers_min', quality_record.get('measured_wall_prism_layers_min'),
         quality.get('mesh_min_wall_prism_layers'), '>=')
    gate('validation.measured_mass_imbalance_ratio',
         validation.get('measured_mass_imbalance_ratio'),
         quality.get('mass_imbalance_ratio_limit'), '<=')
    gate('validation.drag_window_relative_deviation',
         validation.get('drag_window_relative_deviation'),
         quality.get('force_window_rel_change'), '<=')
    for flag, source in (('solve.finite_state', solve), ('solve.residuals_met', solve),
                         ('solve.target_quantities_steady', solve),
                         ('validation.read_only_verified', validation),
                         ('validation.stage1_agrees', validation),
                         ('validation.mass_balanced', validation)):
        if source.get(flag.split('.')[1]) is not True:
            failures.append('%s is not true' % flag)
    trial_dir = Path(metrics.get('trial_dir', ''))
    for name in MEASUREMENT_RECORDS:
        path = Path(metrics.get('measurement_record_paths', {}).get(name, trial_dir / name))
        if not path.is_file() or run_record_gate.citation_status(path).get('state') != run_record_gate.CITABLE:
            failures.append('measurement record not citable: %s' % name)
    if checkpoints:
        if (checkpoints.get('download_succeeded') is not True
                or checkpoints.get('checkpoint_delivered') is not True):
            failures.append('checkpoint delivery not verified')
    elif reused:
        # A REUSED run that predates the round-12 automatic delivery record (the round-10 baseline) has no
        # checkpoints file. Its delivery evidence is the citation state of the records this level is measured
        # from, decided by the shared gate rather than by a check written here. This fallback is limited to
        # reuse: a COMPUTED level whose retrieval wrote no delivery record has not been verified at all.
        trial_dir = Path(metrics.get('trial_dir', ''))
        delivery = {}
        for name in MEASUREMENT_RECORDS:
            path = Path(metrics.get('measurement_record_paths', {}).get(name, trial_dir / name))
            if not path.is_file():
                failures.append('reused evidence missing: %s' % name)
                delivery[name] = 'MISSING'
                continue
            state = run_record_gate.citation_status(path).get('state')
            delivery[name] = state
            if state != run_record_gate.CITABLE:
                failures.append('reused evidence %s is not citable: %s' % (name, state))
        metrics['delivery_by_citation_state'] = delivery
    else:
        failures.append('checkpoint delivery record missing for a computed level')
    if (metrics.get('result') or {}).get('state') != 'AIR3B_PIPELINE_COMPLETE':
        failures.append('run state is not AIR3B_PIPELINE_COMPLETE')
    if report.get('report_verdict') != 'REPORT_OK':
        failures.append('run report verdict is %r' % report.get('report_verdict'))
    return {'valid': not failures, 'failures': failures}


def level_row(metrics: dict, kind: str, baseline_bounds: list, quality: dict) -> dict:
    quality_record = metrics.get('quality') or {}
    solve = metrics.get('solve') or {}
    validation = metrics.get('validation') or {}
    row = {
        'level': metrics.get('level'),
        'trial_id': metrics.get('trial_id'),
        'cells': quality_record.get('cells'),
        'wall_prism_layers_min': quality_record.get('measured_wall_prism_layers_min'),
        'wall_first_layer_height_m_min': quality_record.get('measured_wall_first_layer_height_m_min'),
        'mesh_cell_quality_min': quality_record.get('measured_cell_quality_min'),
        'mesh_face_validity_min': quality_record.get('measured_face_validity_min'),
        'mesh_volume_change_min': quality_record.get('measured_volume_change_min'),
        'iterations_run': solve.get('iterations_run'),
        'stop_reason': solve.get('stop_reason'),
        'residuals_met': solve.get('residuals_met'),
        'finite_state': solve.get('finite_state'),
        'drag_x_n': solve.get('measured_drag_x_n_final'),
        'drag_coefficient_estimate': solve.get('measured_drag_coefficient_estimate'),
        'mass_imbalance_final': solve.get('measured_mass_imbalance_final'),
        'mass_imbalance_ratio': validation.get('measured_mass_imbalance_ratio'),
        'density_kg_m3': solve.get('measured_density_volume_average_kg_m3'),
        'drag_window_relative_deviation': validation.get('drag_window_relative_deviation'),
        'validation_verdict': validation.get('validation_verdict'),
        'reference_status': validation.get('reference_status'),
        'base_size_m': (metrics.get('mesh') or {}).get('base_size_m'),
        'outer_bounds_m': (metrics.get('domain') or {}).get('outer_bounds_m'),
    }
    row['residual_continuity_final'] = (metrics.get('residuals_final') or {}).get('Continuity')
    row['residual_x_momentum_final'] = (metrics.get('residuals_final') or {}).get('X-momentum')
    row['residual_energy_final'] = (metrics.get('residuals_final') or {}).get('Energy')
    if kind == 'domain':
        row['domain_scale'] = domain_scale(metrics, baseline_bounds)
    return row


def study_rows(rows: list[dict], kind: str) -> list[dict]:
    """Only VALID levels may enter the numerical comparison; returns the evaluator's row shape."""
    usable = []
    for row in rows:
        if not row.get('valid'):
            continue
        parameter = row.get(XKEY[kind])
        if not isinstance(parameter, (int, float)) or isinstance(parameter, bool) or parameter <= 0:
            continue
        converted = {XKEY[kind]: float(parameter)}
        for name in QUANTITIES:
            value = row.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                converted[name] = float(value)
        usable.append(converted)
    return usable


def assemble_study(spec: dict, kind: str, rows: list[dict], baseline_bounds: list) -> dict:
    """Delegate the numerical judgement; add no threshold and no favourable reinterpretation."""
    quality = spec.get('quality') or {}
    limit = quality.get('sensitivity_rel_change')
    if not isinstance(limit, (int, float)) or isinstance(limit, bool):
        raise ValueError('CaseSpec quality.sensitivity_rel_change is required for a study')
    # Conservation is enforced as a per-level GATE (quality.mass_imbalance_ratio_limit, applied in
    # level_validity), NOT as a convergence quantity: a relative change of a ~1e-9 quantity would need an
    # explicit normalization scale, and inventing one here would be inventing a threshold. The evaluator's
    # floors argument therefore stays empty and is recorded as such.
    floors: dict = {}
    rows = copy.deepcopy(rows)
    for row in rows:
        if row.get('valid'):
            missing = [key for key in (XKEY[kind],) + QUANTITIES
                       if not isinstance(row.get(key), (int, float)) or isinstance(row.get(key), bool)
                       or not math.isfinite(row[key]) or (key == XKEY[kind] and row[key] <= 0)]
            if missing:
                row['valid'] = False
                row.setdefault('validity', {}).setdefault('failures', []).append(
                    'missing/nonfinite study measurements: %s' % ', '.join(missing))
    valid = [row for row in rows if row.get('valid')]
    record = {
        'schema_version': 1,
        'study': kind,
        'valid_levels': [row['level'] for row in valid],
        'excluded_levels': [{'level': row['level'], 'failures': row.get('validity', {}).get('failures')}
                            for row in rows if not row.get('valid')],
        'relative_change_limit': limit,
        'absolute_scale_floors': floors,
        'quantities': list(QUANTITIES),
        'physical_validation': False,
    }
    minimum = MIN_LEVELS[kind]
    require_declared = (quality.get(kind + '_study') or {}).get('require_all_declared_levels') is True
    expected_levels = [item['name'] for item in study_levels(spec, kind)] if require_declared else []
    if require_declared:
        minimum = max(minimum, len(expected_levels))
    record['declared_levels'] = [row['level'] for row in rows]
    record['declared_level_count'] = len(rows)
    record['valid_level_count'] = len(valid)
    record['minimum_levels_required'] = minimum
    record['minimum_levels_source'] = ('CaseSpec requires every declared level; evaluator minimum retained'
                                      if require_declared else 'evaluate_sensitivity.MIN_LEVELS - the existing reviewed rule, not a number chosen here')
    record['required_declared_levels'] = expected_levels
    record['all_declared_levels_valid'] = len(valid) == len(rows)
    if len(valid) != len(rows):
        record['partial_closure_note'] = (
            '%d of the %d DECLARED levels are valid; the conclusion rests on those %d and the closed levels '
            'are named in excluded_levels' % (len(valid), len(rows), len(valid)))
    declared_incomplete = require_declared and sorted(row['level'] for row in valid) != sorted(expected_levels)
    if require_declared:
        record['all_declared_levels_valid'] = not declared_incomplete
    if len(valid) < minimum or declared_incomplete:
        record.update({
            'sensitivity_passed': False,
            'sensitivity_status': 'NOT_ESTABLISHED',
            'reason': ('required declared levels are missing, duplicated or invalid' if declared_incomplete else
                       'only %d of the required %d levels are valid; the remaining levels are closed and '
                       'must not enter the sensitivity conclusion' % (len(valid), minimum)),
            'comparison': None, 'gci': None,
            'gci_note': 'not attempted: fewer valid levels than the study requires',
            'mesh_independent': False, 'domain_independent': False})
        record['_not_claimed'] = [
            'any sensitivity or independence conclusion: the study is NOT_ESTABLISHED because fewer than '
            'the required number of levels are valid']
        return record
    usable = study_rows(rows, kind)
    comparison = compare_study(kind, usable, list(QUANTITIES), limit, floors)
    record['comparison'] = comparison
    record['sensitivity_passed'] = bool(comparison['passed'])
    record['sensitivity_status'] = 'ESTABLISHED' if comparison['passed'] else 'FAILED'
    if kind == 'mesh':
        ordered = sorted(valid, key=lambda row: row['cells'])
        record['gci'] = {}
        record['mesh_independent'] = False
        declared = (quality.get('mesh_study') or {}).get('refinement_ratio')
        if len(ordered) != 3:
            record['gci_note'] = 'GCI needs exactly three valid levels; %d are valid' % len(ordered)
        elif not isinstance(declared, (int, float)) or isinstance(declared, bool) or declared <= 1:
            counts = [row['cells'] for row in ordered]
            record['gci_ratio_from_cells'] = (counts[2] / counts[0]) ** (1.0 / 3.0)
            record['gci_ratio_from_cells_basis'] = 'coarse-to-fine span, not an adjacent refinement ratio'
            record['gci_adjacent_ratios_from_cells'] = [(counts[1] / counts[0]) ** (1.0 / 3.0),
                                                       (counts[2] / counts[1]) ** (1.0 / 3.0)]
            record['gci_note'] = 'quality.mesh_study.refinement_ratio must be > 1 to run the GCI'
        else:
            counts = [row['cells'] for row in ordered]
            # r is the DECLARED refinement ratio of the mesh family (the CaseSpec scales all three size
            # fields by it), because that is the representative cell-size ratio the family was built with.
            # The ratio implied by the measured cell counts is recorded as a self-similarity diagnostic: if
            # the two disagree materially the family is not geometrically self-similar and the GCI's own
            # applicability note applies.
            ratio = float(declared)
            record['gci_ratio_declared'] = ratio
            record['gci_ratio_from_cells'] = (counts[2] / counts[0]) ** (1.0 / 3.0)
            record['gci_ratio_from_cells_basis'] = 'coarse-to-fine span, not an adjacent refinement ratio'
            record['gci_adjacent_ratios_from_cells'] = [(counts[1] / counts[0]) ** (1.0 / 3.0),
                                                       (counts[2] / counts[1]) ** (1.0 / 3.0)]
            record['gci_ratio_consistency'] = record['gci_ratio_from_cells'] / ratio
            for name in ('drag_coefficient_estimate', 'drag_x_n'):
                values = [row.get(name) for row in ordered]
                if any(not isinstance(value, (int, float)) or isinstance(value, bool)
                       for value in values):
                    record['gci_note'] = 'level value %s is not a finite number at every level' % name
                    continue
                record['gci'][name] = three_grid_gci(*values, ratio)
        record['domain_independent'] = False
        if not record['gci']:
            record['gci'] = None
        else:
            record['mesh_independent'] = bool(
                record.get('sensitivity_passed')
                and all(item.get('valid') and item.get('converged') for item in record['gci'].values()))
    else:
        record['domain_independent'] = bool(record.get('sensitivity_passed'))
        record['mesh_independent'] = False
        record['gci'] = None
        record['gci_note'] = ('the three-grid GCI is NOT applied to a domain study: grid_convergence.py '
                              'states that grid-family similarity is required and that independent domain '
                              'studies must be checked outside its numerical calculation')
    if kind == 'mesh':
        record['gci_numeric_diagnostic_converged'] = record['mesh_independent']
        record['mesh_independent'] = False
        record['gci_applicability_missing'] = ['measured mesh-family similarity', 'asymptotic applicability']
    record['_not_claimed'] = [
        'engineering validation: no verified drag-coefficient reference exists in this repository '
        '(reference_status NOT_ESTABLISHED) and the CaseSpec declares no acceptance threshold, so no '
        'engineering pass/fail follows from this study',
        'that a three-level study proves grid or domain independence in general: the conclusion is scoped '
        'to this case, this quantity set and these levels',
        'that the levels are converged in a physical sense: residuals_met is the CaseSpec process gate',
    ]
    return record


def assemble_rows(spec: dict, kind: str, descriptors: list[dict], baseline_evidence: Path,
                  baseline_trial: str, geometry: Path, baseline_bounds: list) -> list[dict]:
    """One row per declared level, with the validity the evidence actually supports.

    Reused levels are held to BOTH the field-by-field reuse equality AND every CaseSpec gate; computed
    levels are held to the gates alone. The reuse evidence path is resolved against the repository root so
    the same command gives the same rows from any working directory.
    """
    rows = []
    quality = spec.get('quality') or {}
    for descriptor in descriptors:
        level = next(item for item in study_levels(spec, kind) if item['name'] == descriptor['level'])
        if descriptor.get('reuse'):
            equality = reused_reference(baseline_evidence, baseline_trial, {'reuse': descriptor['reuse']},
                                        read_json(Path(descriptor['case_spec'])), geometry)
            reuse_evidence = Path(descriptor['reuse'].get('evidence', baseline_evidence))
            if not reuse_evidence.is_absolute():
                reuse_evidence = ROOT / reuse_evidence
            metrics = collect_metrics(reuse_evidence, descriptor['reuse'].get('trial_id', baseline_trial))
            gates = level_validity(metrics, quality, reused=True)
            failures = ([] if equality['ok']
                        else ['reuse verification failed: %s' % equality['differences']])
            failures += list(gates['failures'])
            row = level_row(metrics, kind, baseline_bounds, quality)
            row['level'] = descriptor['level']
            row['reused'] = equality
            row['validity'] = {'valid': not failures, 'failures': failures,
                               'reuse_equality_ok': bool(equality['ok']),
                               'case_spec_gates_ok': bool(gates['valid'])}
            row['valid'] = not failures
            rows.append(row)
            continue
        metrics = collect_metrics(Path(descriptor['dir']), descriptor['trial_id'])
        metrics['level'] = descriptor['level']
        validity = level_validity(metrics, quality, reused=False)
        row = level_row(metrics, kind, baseline_bounds, quality)
        row['level'] = descriptor['level']
        row['validity'] = validity
        row['valid'] = validity['valid']
        rows.append(row)
    return rows


# --------------------------------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------------------------------

def report_markdown(spec: dict, record: dict, rows: list[dict], citations: dict | None = None) -> str:
    kind = record['study']
    title = '网格' if kind == 'mesh' else '计算域'
    lines = ['# 场景① %s敏感性研究（%s）' % (title, spec.get('_study_id', 'study')), '',
             '**本报告由 `scripts/run_sensitivity_study.py` 从本轮的逐档记录生成；所有门槛取自 CaseSpec，'
             '数值判定由既有的 `evaluate_sensitivity.py` / `grid_convergence.py` 给出，本驱动不新增判据。**', '',
             '## 1. 逐档实测', '']
    header = ['档位', '单元数' if kind == 'mesh' else '计算域倍数', '基网格尺寸/m' if kind == 'mesh' else '域范围/m',
              '近壁棱柱层/首层高 m', '残差门槛', '阻力系数估计', '阻力/N', '质量不平衡比', '阻力窗相对偏差', '档位判定']
    lines.append('| ' + ' | '.join(header) + ' |')
    lines.append('|' + '---|' * len(header))
    for row in sorted(rows, key=lambda item: (item.get('cells') or 0, item.get('domain_scale') or 0)):
        lines.append('| %s | %s | %s | %s / %s | %s | %s | %s | %s | %s | %s |' % (
            row.get('level'),
            row.get('cells') if kind == 'mesh' else row.get('domain_scale'),
            row.get('base_size_m') if kind == 'mesh' else row.get('outer_bounds_m'),
            row.get('wall_prism_layers_min'), row.get('wall_first_layer_height_m_min'),
            row.get('residuals_met'), row.get('drag_coefficient_estimate'), row.get('drag_x_n'),
            row.get('mass_imbalance_ratio'), row.get('drag_window_relative_deviation'),
            '有效' if row.get('valid') else '关闭：' + '；'.join(row.get('validity', {}).get('failures') or [])))
    lines += ['', '## 2. 敏感性判定（delegated）', '',
              '- 相对变化门槛：`quality.sensitivity_rel_change` = %s（来自 CaseSpec）' % record['relative_change_limit'],
              '- 有效档位：%s' % ('、'.join(record['valid_levels']) or '无'),
              '- 关闭档位：%s' % ('；'.join('%s（%s）' % (item['level'], '；'.join(item['failures'] or []))
                                          for item in record['excluded_levels']) or '无'),
              '- 判定：**%s**%s' % (record['sensitivity_status'],
                                  '' if record.get('sensitivity_passed') else
                                  '（%s）' % record.get('reason', '相邻档位相对变化超出 CaseSpec 门槛或有效档位不足')),
              '']
    comparison = record.get('comparison')
    if comparison:
        lines.append('| 目标量 | 相邻相对变化 | 最大 | 是否通过 |')
        lines.append('|---|---|---|---|')
        for name, item in sorted(comparison['quantity_comparisons'].items()):
            changes = '、'.join('%.3e' % change['relative_change'] for change in item['adjacent_changes'])
            lines.append('| `%s` | %s | %.3e | %s |'
                         % (name, changes, item['maximum_relative_change'], item['passed']))
        lines.append('')
    lines += ['## 3. 网格收敛指数（GCI）', '']
    if kind == 'domain':
        lines.append('- **不适用**：%s。' % record.get('gci_note'))
    elif record.get('gci'):
        lines.append('| 目标量 | 观测阶 p | GCI（最细网格）%% | 外推值 | valid | converged |')
        lines.append('|---|---|---|---|---|---|')
        for name, item in sorted(record['gci'].items()):
            lines.append('| `%s` | %s | %s | %s | %s | %s |'
                         % (name, item.get('p'), item.get('gci_fine_pct'), item.get('phi_extrapolated'),
                            item.get('valid'), item.get('converged')))
        lines.append('')
        lines.append('- 实测单元数的粗到细跨档比 = %s（不是相邻比）；CaseSpec 声明相邻 r = %s。'
                     % (record.get('gci_ratio_from_cells'), record.get('gci_ratio_declared')))
        lines.append('- 实测相邻单元尺度比：%s；配置比和单元比均不替代实测网格族/壁法向及渐近证据。'
                     % record.get('gci_adjacent_ratios_from_cells'))
        lines.append('- `mesh_independent` = **%s**。' % record.get('mesh_independent'))
        lines.append('- `grid_convergence.py` 自身声明：GCI 只是数值诊断，还要检查迭代收敛、网格族相似性与'
                     '渐近适用性；`engineering_verified` 恒为 false。')
    else:
        lines.append('- 未建立：%s' % record.get('gci_note', '有效档位不足三档'))
    lines += ['', '## 4. 当前参考依据', '']
    if citations:
        for key, value in citations.items():
            lines.append('- **%s**：%s' % (key, value))
    lines += ['', '## 5. 本轮不主张', '']
    for item in record.get('_not_claimed', []):
        lines.append('- %s' % item)
    lines += ['', '## 6. 交付给窗口二的接口', '',
              '- 当前入口：共享 `prepare_static_external_flow` → 既有逐档独审/登记/持久提交 → `assemble_registered_workflow_studies`；离线函数 `prepare/assemble` 和 CLI `--prepare/--assemble`',
              '- 输入：研究 CaseSpec（`quality.mesh_study` / `quality.domain_study` 声明档位）、'
              '几何 .sim、每档的 `pipeline-review.json`',
              '- 输出：每档 `<level>/pipeline.json` + 试跑记录；研究级 `<kind>-study.json`、'
              '`<kind>-study.csv`（可被既有 `evaluate_sensitivity.py {kind} <csv>` 独立重算）、本报告',
              '- 依赖：既有 8 个宏、`air3b_pipeline_trial`（提交/轮询）、'
              '`run_static_external_flow_e2e`（取回/报告）、`evaluate_sensitivity`、`grid_convergence`',
              '- 失败语义：档位未跑=NOT_RUN；传输/哈希失败=不可用；质量或收敛不合格=关闭并排除；'
              '有效档位少于 `MIN_LEVELS` = NOT_ESTABLISHED', '']
    return '\n'.join(lines)


def write_outputs(evidence: Path, kind: str, spec: dict, rows: list[dict], baseline_bounds: list,
                  citations: dict | None) -> dict:
    evidence.mkdir(parents=True, exist_ok=True)
    rows = [{k: (None if isinstance(v, float) and not math.isfinite(v) else v)
             for k, v in row.items()} for row in rows]
    record = assemble_study(spec, kind, rows, baseline_bounds)
    record['levels'] = rows
    record['gci_status'] = ('ESTABLISHED' if record.get('mesh_independent')
                            else 'NOT_ESTABLISHED' if kind == 'mesh' else 'NOT_APPLICABLE')
    record['case_identity'] = {'source_sim_sha256': (spec.get('input_identity') or {}).get('source_sim_sha256'),
                               'geometry': spec.get('geometry'), 'flow': spec.get('flow'),
                               'numerics': spec.get('numerics'), 'domain': spec.get('domain'),
                               'quality': spec.get('quality'),
                               'mesh': {k: spec.get(k) for k in ('mesh_base_size_m', 'mesh_body_target_size_m',
                                                                'mesh_body_minimum_size_m', 'mesh_prism_layers', 'mesh_nearfield_bounds_m', 'mesh_nearfield_cell_size_m', 'mesh_wake_bounds_m', 'mesh_wake_cell_size_m', 'mesh_prism_first_layer_height_m', 'mesh_prism_total_thickness_m') if k in spec},
                               'study_id': spec.get('_study_id')}
    record_path = evidence / ('%s-study.json' % kind)
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    csv_path = evidence / ('%s-study.csv' % kind)
    columns = [XKEY[kind]] + list(QUANTITIES)
    usable = study_rows(rows, kind)
    with csv_path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in usable:
            writer.writerow({name: row.get(name) for name in columns})
    report_path = evidence / ('%s-study-report.md' % kind)
    report_path.write_text(report_markdown(spec, record, rows, citations) + '\n', encoding='utf-8')
    summary = {'schema_version': 1, 'study': kind, 'sensitivity_status': record['sensitivity_status'],
               'sensitivity_passed': record['sensitivity_passed'],
               'valid_levels': record['valid_levels'], 'excluded_levels': record['excluded_levels'],
               'valid_level_count': record['valid_level_count'],
               'all_declared_levels_valid': record['all_declared_levels_valid'],
               'case_identity': record['case_identity'], 'gci_status': record['gci_status'],
               'mesh_independent': record.get('mesh_independent'),
               'domain_independent': record.get('domain_independent'),
               'records': {'study': str(record_path), 'csv': str(csv_path), 'report': str(report_path)},
               'hashes': {str(record_path): sha256(record_path), str(csv_path): sha256(csv_path),
                          str(report_path): sha256(report_path)}}
    summary_path = evidence / ('%s-study-report.json' % kind)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    def rel(path: Path) -> str:
        try:
            return str(Path(path).resolve().relative_to(ROOT))
        except ValueError:
            return str(path)
    (evidence / ('%s-study-report-sha256.txt' % kind)).write_text(
        ''.join('%s  %s\n' % (sha256(Path(path)), rel(Path(path)))
                for path in (record_path, csv_path, report_path, summary_path)), encoding='utf-8')
    return summary


# --------------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------------

def assemble(study_spec_path: Path, kind: str, evidence: Path,
             baseline_evidence: Path | None = None, baseline_trial: str | None = None,
             geometry: Path | None = None) -> dict:
    """Offline post-step for the persistent entrance; never regenerates or submits levels."""
    spec = read_json(study_spec_path)
    manifest = read_json(evidence / ('%s-levels.json' % kind))
    if manifest['spec_sha256'] != sha256(study_spec_path):
        raise ValueError('study manifest CaseSpec changed after preparation')
    geometry = geometry or Path(manifest['geometry'])
    if manifest['geometry_sha256'] != sha256(geometry):
        raise ValueError('study manifest geometry changed after preparation')
    for label in ('pipeline', 'source_contract'):
        if manifest.get(label) and manifest.get(label + '_sha256') != sha256(Path(manifest[label])):
            raise ValueError('study manifest %s changed after preparation' % label)
    for descriptor in manifest['levels']:
        if descriptor.get('pipeline') and descriptor['pipeline_sha256'] != sha256(Path(descriptor['pipeline'])):
            raise ValueError('level pipeline changed after preparation: %s' % descriptor['level'])
        if descriptor['case_spec_sha256'] != sha256(Path(descriptor['case_spec'])):
            raise ValueError('level CaseSpec changed after preparation: %s' % descriptor['level'])
    bounds = (spec.get('domain') or {}).get('outer_bounds_m')
    rows = assemble_rows(spec, kind, manifest['levels'], baseline_evidence or evidence,
                         baseline_trial or '', geometry, bounds)
    return write_outputs(evidence, kind, spec, rows, bounds, spec.get('_reference_check'))


def completion_report(spec_path: Path, evidence: Path, trial_id: str,
                      studies: dict[str, Path], output: Path, property_record: Path | None = None) -> dict:
    """Compose existing reports plus study verdicts, retaining absent/foreign fields visibly."""
    spec = read_json(spec_path)
    metrics = collect_metrics(evidence, trial_id)
    report = metrics.get('report') or {}
    executed_spec_path = evidence / trial_id / 'case_spec.json'
    executed_spec = read_json(executed_spec_path) if executed_spec_path.is_file() else {}
    physical_keys = ('geometry', 'flow', 'numerics', 'domain', 'model_length_unit',
                     'region_name', 'body_wall_boundary_name', 'boundary_roles',
                     'mesh_base_size_m', 'mesh_body_target_size_m', 'mesh_body_minimum_size_m', 'mesh_prism_layers', 'mesh_nearfield_bounds_m', 'mesh_nearfield_cell_size_m', 'mesh_wake_bounds_m', 'mesh_wake_cell_size_m', 'mesh_prism_first_layer_height_m', 'mesh_prism_total_thickness_m',
                     'geometry_source_format', 'engineering_target')
    applicable_result = bool(executed_spec and all(spec.get(k) == executed_spec.get(k) for k in physical_keys))
    current_quality = {k: v for k, v in (spec.get('quality') or {}).items() if k not in ('mesh_study', 'domain_study')}
    executed_quality = {k: v for k, v in (executed_spec.get('quality') or {}).items() if k not in ('mesh_study', 'domain_study')}
    applicable_result = applicable_result and current_quality == executed_quality
    provenance = evidence / ('%s-config.json' % trial_id)
    config = read_json(provenance) if provenance.is_file() else {}
    source_hash = (spec.get('input_identity') or {}).get('source_sim_sha256')
    applicable_result = bool(applicable_result and source_hash and (source_hash in (config.get('input_hashes') or {}).values() or source_hash == (metrics.get('checkpoint_lineage') or {}).get('geometry_sha256'))
                             and config.get('input_hashes', {}).get('case_spec.json') == sha256(executed_spec_path))
    result_citations = {}
    for name in MEASUREMENT_RECORDS:
        path = Path(metrics.get('measurement_record_paths', {}).get(name, evidence / trial_id / name))
        result_citations[name] = run_record_gate.citation_status(path) if path.is_file() else {'state': 'MISSING'}
    applicable_result = applicable_result and not metrics.get('checkpoint_lineage_error')
    applicable_result = bool(applicable_result and all(v.get('state') == run_record_gate.CITABLE
                                                      for v in result_citations.values()))
    validation = metrics.get('validation') or {}
    study_states = {}
    for kind in ('mesh', 'domain'):
        path = studies.get(kind)
        value = read_json(path) if path and path.is_file() else {}
        identity = value.get('case_identity') or {}
        current_identity = {'source_sim_sha256': (spec.get('input_identity') or {}).get('source_sim_sha256'),
                            'geometry': spec.get('geometry'), 'flow': spec.get('flow'),
                            'numerics': spec.get('numerics'), 'domain': spec.get('domain'),
                            'quality': spec.get('quality'),
                            'mesh': {k: spec.get(k) for k in ('mesh_base_size_m', 'mesh_body_target_size_m',
                                                             'mesh_body_minimum_size_m', 'mesh_prism_layers', 'mesh_nearfield_bounds_m', 'mesh_nearfield_cell_size_m', 'mesh_wake_bounds_m', 'mesh_wake_cell_size_m', 'mesh_prism_first_layer_height_m', 'mesh_prism_total_thickness_m') if k in spec},
                            'study_id': spec.get('_study_id')}
        applicable = bool(value and identity == current_identity)
        study_states[kind] = {'status': value.get('sensitivity_status', 'NOT_ESTABLISHED') if applicable else 'NOT_ESTABLISHED',
                             'independent': bool(applicable and value.get(kind + '_independent', False)),
                             'all_declared_levels_valid': bool(applicable and value.get('all_declared_levels_valid', False)),
                             'record': str(path) if path else None,
                             'missing': not bool(value), 'applicable_to_current_case': applicable}
    export_path = evidence / trial_id / 'external_flow_export.json'
    export = read_json(export_path) if export_path.is_file() else {}
    export_gate = run_record_gate.citation_status(export_path) if export_path.is_file() else {'state': 'MISSING'}
    properties = read_json(property_record) if property_record and property_record.is_file() else {}
    property_gate = (run_record_gate.citation_status(property_record)
                     if properties else {'state': 'MISSING'})
    solve_stage_path = evidence / trial_id / 'stage-solve.json'
    solve_stage = read_json(solve_stage_path) if solve_stage_path.is_file() else {}
    solved_hash = solve_stage.get('output_sha256')
    end_iteration = (metrics.get('solve') or {}).get('end_iteration')
    matching_property = bool(properties and applicable_result and solved_hash and end_iteration
                             and property_gate.get('state') == run_record_gate.CITABLE
                             and properties.get('case_spec_sha256') == sha256(executed_spec_path)
                             and properties.get('input_sim_sha256') == solved_hash
                             and properties.get('input_sim_sha256_after') == solved_hash
                             and properties.get('start_iteration') == end_iteration
                             and properties.get('end_iteration') == end_iteration)
    result = {'schema_version': 1, 'case_spec': str(spec_path), 'case_spec_sha256': sha256(spec_path),
              'trial_id': trial_id, 'applicable_result': applicable_result, 'result_citations': result_citations,
              'process_status': report.get('report_verdict', 'NOT_RUN') if applicable_result else 'NOT_ESTABLISHED',
              'engineering_status': 'NOT_ESTABLISHED',
              'delivery_status': ('DELIVERED' if applicable_result and (metrics.get('checkpoints') or {}).get('checkpoint_delivered') is True
                                  and (metrics.get('checkpoints') or {}).get('download_succeeded') is True
                                  else 'NOT_ESTABLISHED'),
              'validation_verdict': validation.get('validation_verdict', 'MISSING'),
              'validation_verdict_basis': 'executed evidence; applies to current contract only when applicable_result=true',
              'studies': study_states, 'research_complete': all(v['independent'] and v['all_declared_levels_valid'] for v in study_states.values()),
              'export': {'record': str(export_path), 'citation': export_gate,
                         'present': bool(export), 'artifacts': export.get('artifacts', []),
                         'coefficient_basis': export.get('coefficient_basis', 'MISSING')},
              'property_nearwall': {'record': str(property_record) if property_record else None,
                                   'citation': property_gate, 'applicable_to_current_result': matching_property,
                                   'status': 'READBACK_AVAILABLE' if matching_property else 'NOT_ESTABLISHED',
                                   'missing': properties.get('missing', [{'field': 'property_nearwall', 'reason': 'not supplied'}]),
                                   'properties': properties.get('properties', {}) if matching_property else {}},
              'reference': spec.get('engineering_target', {}), 'experimental_validation': False}
    # Preserve the original reporter verdict; research alone can never create engineering validation.
    result['engineering_status'] = (report.get('engineering_verdict', 'NOT_ESTABLISHED')
                                    if applicable_result else 'NOT_ESTABLISHED')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'completion-report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# 静止外流交付与研究报告', '', '流程：%s；工程：%s；交付：%s。' %
             (result['process_status'], result['engineering_status'], result['delivery_status']), '',
             'CaseSpec SHA-256：`%s`。实际验证结论：`%s`。' % (result['case_spec_sha256'], result['validation_verdict']), '',
             '| 研究 | 状态 | 独立性成立 |', '|---|---|---|']
    lines += ['| %s | %s | %s |' % (k, v['status'], v['independent']) for k, v in study_states.items()]
    lines += ['', '物性/近壁分支：`%s`。只有输入结果与CaseSpec哈希均匹配的可引用记录才适用。' % result['property_nearwall']['status'],
              '', '缺失字段：`%s`。' % json.dumps(result['property_nearwall']['missing'], ensure_ascii=False), '',
              '系数依据：%s。' % result['export']['coefficient_basis'], '',
              '研究报告仅适用于当前来源、工况、网格族和目标量；失败、未收敛、未执行和预算不足档排除。没有实验验证。']
    (output / 'completion-report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return result


def run_level(entry, trial, backend, descriptor: dict, geometry: Path, timeout: int) -> None:
    inputs = [Path(descriptor['case_spec']), geometry]
    pipeline = Path(descriptor['pipeline'])
    evidence = Path(descriptor['dir'])
    from resource_contract import validate_pipeline_resources
    allocation = validate_pipeline_resources(read_json(pipeline))
    if allocation is not None and timeout < read_json(pipeline)['task_budget_seconds']:
        raise ValueError('Poll timeout is shorter than reviewed task budget')
    summary = trial.start(backend, descriptor['trial_id'], pipeline, inputs, evidence)
    print(json.dumps({'started': descriptor['trial_id'], 'stages': [s['name'] for s in summary['macro_plan']],
                      'job_dir': summary['job_dir']}, ensure_ascii=False, indent=2))
    result, terminal = entry.poll_until_terminal(trial, backend, descriptor['trial_id'], evidence, timeout)
    print(json.dumps({'state': result.get('state'), 'terminal': terminal}, ensure_ascii=False))
    checkpoints = entry.retrieve_checkpoints(backend, trial, descriptor['trial_id'], evidence, result)
    print(json.dumps({'download_succeeded': checkpoints['download_succeeded'],
                      'checkpoint_delivered': checkpoints['checkpoint_delivered'],
                      'failures': [record['name'] for record in checkpoints['failures']]},
                     ensure_ascii=False, indent=2))
    entry.write_report(descriptor['trial_id'], evidence, False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--kind', choices=('mesh', 'domain'), required=True)
    ap.add_argument('--spec', type=Path, default=DEFAULT_SPEC)
    ap.add_argument('--pipeline', type=Path, default=BASELINE_PIPELINE)
    ap.add_argument('--source-contract', type=Path)
    ap.add_argument('--geometry', type=Path, default=DEFAULT_GEOMETRY)
    ap.add_argument('--baseline-evidence', type=Path, default=DEFAULT_BASELINE_EVIDENCE)
    ap.add_argument('--baseline-trial', default=DEFAULT_BASELINE_TRIAL)
    ap.add_argument('--evidence', type=Path, required=True)
    ap.add_argument('--trial-prefix', default=None)
    ap.add_argument('--trial-suffix', default='a')
    ap.add_argument('--timeout', type=int, default=172800)
    ap.add_argument('--prepare', action='store_true')
    ap.add_argument('--submit', action='store_true')
    ap.add_argument('--assemble', action='store_true')
    ap.add_argument('--all', action='store_true')
    a = ap.parse_args()
    spec_path = a.spec if a.spec.is_absolute() else ROOT / a.spec
    geometry = a.geometry if a.geometry.is_absolute() else ROOT / a.geometry
    evidence = a.evidence if a.evidence.is_absolute() else ROOT / a.evidence
    baseline_evidence = (a.baseline_evidence if a.baseline_evidence.is_absolute()
                         else ROOT / a.baseline_evidence)
    if a.assemble and not (a.prepare or a.submit or a.all):
        summary = assemble(spec_path, a.kind, evidence, baseline_evidence, a.baseline_trial, geometry)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    spec = read_json(spec_path)
    prefix = a.trial_prefix or ('s1-study-%s' % a.kind)
    pipeline_path = a.pipeline if a.pipeline.is_absolute() else ROOT / a.pipeline
    source_contract = a.source_contract
    if source_contract and not source_contract.is_absolute():
        source_contract = ROOT / source_contract
    macro_hashes = {name: sha256(ROOT / 'macros' / name)
                    for name in read_json(pipeline_path)['macro_hashes']}
    descriptors = prepare(spec_path, a.kind, geometry, evidence, prefix, macro_hashes, a.trial_suffix,
                          pipeline_path, source_contract)
    print(json.dumps({'prepared': descriptors}, ensure_ascii=False, indent=2))
    if a.prepare and not (a.submit or a.assemble or a.all):
        return 0
    entry = entry_module()
    import air3b_pipeline_trial as trial
    backend = None
    if a.submit or a.all:
        from backend import Backend
        backend = Backend()
    for descriptor in descriptors:
        if descriptor.get('reuse') or not (a.submit or a.all):
            continue
        run_level(entry, trial, backend, descriptor, geometry, a.timeout)
    if a.assemble or a.all:
        baseline_bounds = (spec.get('domain') or {}).get('outer_bounds_m')
        rows = assemble_rows(spec, a.kind, descriptors, baseline_evidence, a.baseline_trial, geometry,
                             baseline_bounds)
        summary = write_outputs(evidence, a.kind, spec, rows, baseline_bounds, spec.get('_reference_check'))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
