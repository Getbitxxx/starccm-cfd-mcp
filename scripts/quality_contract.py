"""Single executable result-quality contract. Never starts or changes a simulation.

Missing measurements stay null and cap quality. A PASS certificate must be a
hash-bound, centrally CITABLE record; prose and caller-supplied PASS flags are
never numerical criteria. Existing process/delivery fields remain independent.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

from run_record_gate import CITABLE, citation_status

ROOT = Path(__file__).resolve().parents[1]
STATES = ('Q0_FAILED', 'Q1_EXECUTED', 'Q2_NUMERICALLY_VERIFIED', 'Q3_ENGINEERING_VERIFIED')
HEX = re.compile(r'^[0-9a-f]{64}$')

# C1 (user-signed 2026-10-23). Family admission is the REGISTERED gate of
# docs/ruling-family-admission-gates-20261022.md: each adjacent ratio measured on its
# own and inside the window, not one uniform ratio for the whole family. The registered
# GCI tool (scripts/grid_convergence.py) already computes a given pair and never
# averages, so a non-uniform family is computed per pair with its own measured ratio.
MESH_RATIO_GATES = {'cell_max': 2.2, 'h_min': 1.26, 'h_max': 1.32}

# C2 (user-signed 2026-10-23). APPROVED_A_LEG_RATIO is a user-signed verified status of
# the reference registration: the A leg is sourced and arithmetically reproducible, legs
# B and C are declared outstanding enhancements rather than missing verification.
REFERENCE_VERIFIED_STATUSES = ('ESTABLISHED', 'VERIFIED', 'APPROVED_A_LEG_RATIO')

# C3 (user-signed 2026-10-23). A second legal shape for execution authority: the
# ONE-SHOT AUTHORIZATION COLLECTION. One-shot admission was the sanctioned mechanism for
# probe and diagnostic work, so a multi-level campaign assembled from per-job
# authorizations has no root request, no ordered steps graph and no runtime manifest
# snapshot - not because anything was skipped, but because the contract never anticipated
# that shape. This clause gives it a machine-readable one.
#
# It accepts NO retroactively authored authorization file, steps graph or manifest
# snapshot. It defines a machine-readable summary of facts that already happened, not a
# channel for manufacturing them afterwards. Every property below is checked against a
# record, and the timestamps come from the commits that introduced those records, so a
# file written after the fact cannot satisfy it.
COLLECTION_FORM = 'ONE_SHOT_AUTHORIZATION_COLLECTION'
COLLECTION_NO_RETROFIT = ('本形态不接受事后补写的授权文件、steps 图或 manifest 快照；'
                          '它定义的是已发生事实的机器可读汇总，不是事后补造的通道。')
COLLECTION_FORWARD_LOOKING = ('未来战役优先用 pipeline 执行并保留 runtime manifest 快照；'
                              '本形态是为既有 one-shot 记录定义的可机检汇总形态，'
                              '不作为新战役的授权通道。')
# The deployment review the active pointer names. Read, never assumed: the pointer is a
# hash-bound record, so the digest below is a measurement of it, not a constant.
ACTIVE_DEPLOYMENT_POINTER = 'review/formal-worker-deployment-active.json'


class QualityContractError(ValueError):
    pass


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def finite(v):
    return type(v) in (float, int) and math.isfinite(v)


def pointer(value, path):
    for key in path.strip('/').split('/') if path.strip('/') else []:
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def _same(a, b):
    try:
        return json.dumps(a, sort_keys=True, allow_nan=False) == json.dumps(b, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return False


def _evidence(records):
    verified = {}
    for item in records.get('evidence', []):
        if not isinstance(item, dict) or not item.get('path') or not HEX.fullmatch(str(item.get('sha256', ''))):
            raise QualityContractError('Evidence requires an actual path and SHA256')
        p = Path(item['path'])
        if not p.is_file() or sha256(p) != item['sha256']:
            raise QualityContractError('Evidence SHA256 differs: ' + str(p))
        state = citation_status(p)['state']
        if item.get('certificate') is True and state != CITABLE:
            raise QualityContractError('Uncitable PASS certificate: %s (%s)' % (p, state))
        verified[str(p)] = dict(item, citation_state=state)
    return verified


def _bound(records, verified, group):
    """A canonical certificate binds exact field values, not merely a nearby file name."""
    source = records.get('field_sources', {}).get(group)
    if not isinstance(source, dict): return False
    item = verified.get(source.get('path'))
    if not item or item['citation_state'] != CITABLE or not runner_measurement(records,item): return False
    try:
        a=copy.deepcopy(pointer(read(item['path']), source['pointer'])); b=copy.deepcopy(pointer(records, group))
        if group=='numerics/mesh_quality':
            a.pop('state',None); b.pop('state',None)
        return _same(a,b)
    except (KeyError, IndexError, TypeError, ValueError):
        return False


def _assessment(records):
    if not isinstance(records, dict): raise QualityContractError('Quality inputs must be an object')
    evidence = _evidence(records)
    if (records.get('engineering') or {}).get('e_phys')=='PASS' and not (records.get('engineering') or {}).get('reference'):
        raise QualityContractError('E-phys PASS lacks a reference')
    ex = records.get('execution') or {}
    stages = ex.get('stages') or []
    artifacts = ex.get('required_artifacts') or []
    failures = []
    complete = ex.get('state') in ('EXECUTION_COMPLETED', 'AIR3B_PIPELINE_COMPLETE', 'SUCCEEDED')
    if not complete: failures.append('execution:not_completed')
    if not stages or any(s.get('state') != 'ok' for s in stages): failures.append('execution:stage_not_ok')
    declared = ex.get('expected_artifacts')
    # The full stage graph and real manifest files, never a caller-shortened list.
    expected = set()
    plan = ex.get('plan')
    if not isinstance(plan,list) or not plan or set(plan) != {s.get('key',s.get('name')) for s in stages}:
        failures.append('execution:complete_plan_missing')
    authority, authority_errors = execution_authority(ex)
    failures.extend(authority_errors)
    for stage in stages:
        manifest = stage.get('manifest') or {}
        try:
            path = Path(manifest['path'])
            if sha256(path) != manifest['sha256']:raise QualityContractError('Manifest SHA256 differs')
            data = read(path);entries = data['stages']
            entry = entries[stage['name']] if isinstance(entries,dict) else next(x for x in entries if x['name']==stage['name'])
            names = set(entry['required']) | set(stage.get('runner_required_artifacts', []))
            expected.update(str(stage.get('key',stage['name']))+'/'+name for name in names)
        except (KeyError, OSError, StopIteration, TypeError):
            failures.append('execution:stage_manifest_missing')
    if expected != {a.get('identity') for a in artifacts}:
        failures.append('execution:artifact_manifest_incomplete')
    if declared is not None and set(declared) != {a.get('identity', a.get('name')) for a in artifacts}:
        failures.append('execution:artifact_manifest_incomplete')
    if any(not artifact_proof(a) for a in artifacts):
        failures.append('execution:artifact_bytes_or_runner_binding_differ')
    if any(a.get('state') != 'VERIFIED' or not HEX.fullmatch(str(a.get('sha256', ''))) for a in artifacts):
        failures.append('execution:required_artifact_missing_or_unverified')
    if failures:
        return STATES[0], 'NOT_ESTABLISHED', 'NOT_APPLICABLE', failures, evidence, {}

    n = records.get('numerics') or {}; m = n.get('mesh_quality') or {}
    c = n.get('convergence') or {}; s = n.get('sensitivity') or {}
    g = m.get('gates') or {}
    checks = {}
    checks['mesh_quality'] = (type(m.get('cells')) is int and m['cells'] > 0 and all(
        finite(m.get(k)) and finite(g.get(gate)) and g[gate] > 0 and m[k] >= g[gate]
        for k, gate in [('min_cell_quality','cell_quality'), ('min_face_validity','face_validity'),
                        ('min_volume_change','volume_change'), ('min_wall_prism_layers','prism_layers')]))
    residuals = c.get('residual_gates') or {}
    checks['convergence'] = (c.get('residuals_met') is True and bool(residuals) and all(
        isinstance(v, dict) and finite(v.get('final')) and finite(v.get('gate'))
        and 0 <= v['final'] <= v['gate'] and v['gate'] > 0 and v.get('met') is True
        for v in residuals.values()))
    checks['conservation'] = (finite(c.get('mass_imbalance_ratio')) and finite(c.get('mass_imbalance_gate'))
        and 0 <= c['mass_imbalance_ratio'] < c['mass_imbalance_gate'] and c['mass_imbalance_gate'] > 0)
    checks['target_stability'] = (c.get('target_quantities_steady') is True
        and finite(c.get('target_window_relative_change')) and finite(c.get('target_window_gate'))
        and 0 <= c['target_window_relative_change'] < c['target_window_gate'] and c['target_window_gate'] > 0)
    for kind in ('mesh_levels', 'domain_levels'):
        level = s.get(kind) or {}; required = level.get('required'); executed = level.get('executed')
        valid = (type(required) is int and required >= 3 and type(executed) is int and executed >= required
                 and level.get('all_levels_qualified') is True)
        if kind == 'mesh_levels':
            valid = (valid and level.get('monotonic') is True and level.get('Fs') == 1.25
                and finite(level.get('gci_32')) and finite(level.get('limit'))
                and 0 <= level['gci_32'] < level['limit'] <= .02)
        else:
            valid = (valid and finite(level.get('relative_change')) and finite(level.get('limit'))
                and 0 <= level['relative_change'] < level['limit'] <= .02)
        checks[kind] = bool(valid)
    dt = s.get('time_step') or {}
    checks['time_step_sensitivity'] = dt.get('required') is False or (
        dt.get('required') is True and type(dt.get('executed')) is int and dt['executed'] >= 2
        and dt.get('all_levels_qualified') is True and finite(dt.get('relative_change'))
        and finite(dt.get('limit')) and 0 <= dt['relative_change'] < dt['limit'] <= .02)
    prov = records.get('provenance') or {}
    checks['sensitivity_measurements']=sensitivity_proof(records)
    checks['casespec_binding'] = criteria_binding(records, authority)
    checks['provenance'] = all(HEX.fullmatch(str(prov.get(k, ''))) for k in
        ('release_id','runtime_digest','case_spec_sha256','input_sha256'))
    checks['provenance'] = bool(checks['provenance'] and isinstance(prov.get('macro_sha256'), dict)
        and prov['macro_sha256'] and all(HEX.fullmatch(str(v)) for v in prov['macro_sha256'].values()))
    groups = ('numerics/mesh_quality', 'numerics/convergence', 'numerics/sensitivity')
    checks['citable_field_bindings'] = all(_bound(records, evidence, group) for group in groups)
    eng = records.get('engineering') or {}; ref = eng.get('reference') or {}
    source = ref.get('source')
    ref_ok = (ref.get('verified') is True and ref.get('status') in REFERENCE_VERIFIED_STATUSES
        and isinstance(source, str) and bool(source.strip()) and not any(word in source.lower()
        for word in ('placeholder','not_established','no verified','source not named'))
        and finite(ref.get('value')) and ref['value'] != 0 and finite(ref.get('measured_value'))
        and finite(ref.get('tolerance')) and ref['tolerance'] > 0
        and finite(ref.get('relative_error')) and ref['relative_error'] >= 0
        and math.isclose(abs(ref['measured_value']-ref['value'])/abs(ref['value']),
                         ref['relative_error'], rel_tol=1e-8, abs_tol=1e-12)
        and _bound(records, evidence, 'engineering/reference')
        and reference_proof(records,ref,authority)
        and isinstance(eng.get('scope_statement'),str) and bool(eng['scope_statement'].strip()))
    phys = ('PASS' if ref['relative_error'] <= ref['tolerance'] else 'FAIL') if ref_ok else 'NOT_APPLICABLE'
    if eng.get('e_phys') == 'PASS' and phys != 'PASS':
        raise QualityContractError('E-phys PASS lacks verified reference/value/tolerance/field binding')
    # e_num is always recomputed. Caller PASS cannot replace a missing subcriterion.
    num = 'PASS' if all(checks.values()) else ('FAIL' if any(v is False for k,v in checks.items()
        if k not in ('provenance','citable_field_bindings')) else 'NOT_ESTABLISHED')
    state = STATES[3] if num == 'PASS' and phys == 'PASS' else STATES[2] if num == 'PASS' else STATES[1]
    if records.get('engineering_verified') is True and state != STATES[3]:
        raise QualityContractError('engineering_verified=true requires Q3')
    return state, num, phys, [k for k,v in checks.items() if not v], evidence, checks


Q0_CODES = frozenset({'UNBOUND_HISTORICAL_EVIDENCE', 'NO_DELIVERY_RECORD',
                      'STAGE_FAILED', 'EXECUTION_NOT_COMPLETED', 'ARTIFACT_UNVERIFIED'})


def q0_reasons(records, limitations):
    """Only actual failed stages mean STAGE_FAILED; missing authority stays unbound."""
    reasons = set()
    ex = records.get('execution') or {}
    if any(s.get('state') in ('failed', 'FAILED') for s in ex.get('stages', [])):
        reasons.add('STAGE_FAILED')
    if 'execution:not_completed' in limitations:
        reasons.add('EXECUTION_NOT_COMPLETED' if ex.get('state') else 'UNBOUND_HISTORICAL_EVIDENCE')
    if any('artifact' in x for x in limitations):
        reasons.add('ARTIFACT_UNVERIFIED')
    if not ex.get('required_artifacts'):
        delivery=ex.get('legacy_delivery_record')
        try:
            data=checked_file(delivery)
            present=isinstance(data.get('artifacts'),list) and bool(data['artifacts'])
        except (QualityContractError,ValueError,TypeError,OSError):present=False
        if not present:reasons.add('NO_DELIVERY_RECORD')
    if any('authority' in x or 'manifest' in x or 'plan' in x or 'identity' in x
           or 'request' in x or 'runtime' in x for x in limitations) or not ex.get('request'):
        reasons.add('UNBOUND_HISTORICAL_EVIDENCE')
    return sorted(reasons or {'UNBOUND_HISTORICAL_EVIDENCE'})


def presentation(block):
    return {'quality_state': block['quality_state'], 'scope_statement': block['claims']['required_statement'],
            'forbidden_uses': list(block['claims']['forbidden']), 'Q0_REASON': list(block['Q0_REASON'])}


def compute_quality_state(records):
    return _assessment(records)[0]


def render_quality_block(records):
    state, num, phys, missing, evidence, checks = _assessment(records)
    block = copy.deepcopy(records)
    block['quality_state'] = state
    block.setdefault('execution', {})
    for k,v in {'run_id':None,'stages':[],'required_artifacts':[]}.items(): block['execution'].setdefault(k,v)
    block.setdefault('numerics', {})
    defaults = {
        'mesh_quality':dict(cells=None,min_cell_quality=None,min_face_validity=None,min_volume_change=None,
                           min_wall_prism_layers=None,gates=dict(cell_quality=None,face_validity=None,volume_change=None,prism_layers=None),state='NOT_ESTABLISHED'),
        'convergence':dict(verdict=None,residual_gates={},residuals_met=None,mass_imbalance_ratio=None,
                          mass_imbalance_gate=None,target_window_relative_change=None,target_window_gate=None,target_quantities_steady=None),
        'sensitivity':dict(mesh_levels=dict(required=None,executed=None,gci_32=None),
                           domain_levels=dict(required=None,executed=None,relative_change=None),
                           time_step=dict(required=None,executed=None,relative_change=None))}
    for key, values in defaults.items():
        dest = block['numerics'].setdefault(key,{})
        for k,v in values.items():
            if isinstance(v,dict):
                dest.setdefault(k,{})
                for a,b in v.items():dest[k].setdefault(a,b)
            else:dest.setdefault(k,v)
    block['numerics']['mesh_quality']['state'] = 'PASS' if checks.get('mesh_quality') else 'NOT_ESTABLISHED'
    block.setdefault('engineering',{}).update(e_num=num,e_phys=phys)
    ref = block['engineering'].setdefault('reference',{})
    for k in ('status','source','value','tolerance','relative_error'):ref.setdefault(k,None)
    block['engineering'].setdefault('scope_statement','适用范围未建立')
    block.setdefault('provenance',{})
    for k in ('release_id','runtime_digest','case_spec_sha256','input_sha256','macro_sha256','run_record_citation'):
        block['provenance'].setdefault(k,None)
    block['evidence'] = list(evidence.values())
    block['checks'] = checks;block['limitations'] = missing
    phrases = {
        STATES[0]:('失败；仅用于诊断',['失败诊断'],['成功','工程用途']),
        STATES[1]:('仅证明流程执行；数值可信度未建立/未通过；不得用于工程判断',['流程执行观察','失败诊断'],['ENGINEERING_PASS_CLAIM','CONVERGENCE_CLAIM','RELIABILITY_CLAIM','DESIGN_USE']),
        STATES[2]:('数值上自洽；'+('已比对但参考一致性未通过' if phys=='FAIL' else '未与实验/基准比对')+'；可用于相对比较，不得用于绝对设计定值',['同一模型内相对比较','趋势'],['ENGINEERING_PASS_CLAIM','DESIGN_USE']),
        STATES[3]:('E-num PASS；E-phys PASS；适用范围：%s；容差：%s' % (block['engineering']['scope_statement'],ref.get('tolerance')),['声明范围及容差内工程判断'],['超出适用范围推广'])}
    phrase, allowed, forbidden = phrases[state]
    block['claims'] = {'required_statement':phrase,'allowed':allowed,'forbidden':forbidden}
    block['Q0_REASON'] = q0_reasons(records, missing) if state == STATES[0] else []
    block.update(presentation(block))
    return block


def validate_report_claims(text, state, block):
    if state not in STATES or block.get('quality_state') != state:raise QualityContractError('Missing/mismatched quality_state')
    required = {'execution':('run_id','stages','required_artifacts'), 'numerics':('mesh_quality','convergence','sensitivity'),
                'engineering':('e_num','e_phys','reference','scope_statement'), 'claims':('allowed','forbidden','required_statement'),
                'provenance':('release_id','runtime_digest','case_spec_sha256','input_sha256','macro_sha256','run_record_citation')}
    for section, fields in required.items():
        if not isinstance(block.get(section),dict) or any(k not in block[section] for k in fields):
            raise QualityContractError('Missing quality block fields: '+section)
    nested={'numerics/mesh_quality':('cells','min_cell_quality','min_face_validity','min_volume_change','min_wall_prism_layers','gates','state'),
            'numerics/mesh_quality/gates':('cell_quality','face_validity','volume_change','prism_layers'),
            'numerics/convergence':('verdict','residual_gates','residuals_met','mass_imbalance_ratio','mass_imbalance_gate','target_window_relative_change','target_window_gate','target_quantities_steady'),
            'numerics/sensitivity':('mesh_levels','domain_levels','time_step'),
            'engineering/reference':('status','source','value','tolerance','relative_error')}
    for path,fields in nested.items():
        try: value=pointer(block,path)
        except (KeyError,TypeError):raise QualityContractError('Missing quality fields: '+path)
        if not isinstance(value,dict) or any(k not in value for k in fields):raise QualityContractError('Missing quality fields: '+path)
    if state == STATES[0] and (not block.get('Q0_REASON') or any(x not in Q0_CODES for x in block['Q0_REASON'])):
        raise QualityContractError('Q0 requires machine-readable Q0_REASON')
    if block.get('Q0_REASON') != (q0_reasons(block, _assessment(block)[3]) if state == STATES[0] else []):
        raise QualityContractError('Q0_REASON differs from computed failures')
    if any(block.get(k) != v for k,v in presentation(block).items()):
        raise QualityContractError('Missing/mismatched quality presentation fields')
    if compute_quality_state(block) != state:raise QualityContractError('Quality block overstates computed state')
    claim_values=json.dumps({'scope':block['engineering']['scope_statement'],'allowed':block['claims']['allowed']},ensure_ascii=False)
    if state!=STATES[3] and any(x in claim_values for x in ('可用于设计','工程验证通过','结果可信')):
        raise QualityContractError('Structured quality claims exceed '+state)
    def true_flag(value):
        if isinstance(value,dict):return any((k=='engineering_verified' and v is True) or true_flag(v) for k,v in value.items())
        if isinstance(value,list):return any(true_flag(x) for x in value)
        return False
    if state != STATES[3] and (true_flag(block) or re.search(
        r'engineering_verified["\s]*[:=]\s*true',text,re.I)):
        raise QualityContractError('engineering_verified=true requires Q3')
    if block['claims']['required_statement'] not in text:raise QualityContractError('Missing mandatory quality statement')
    # Structured raw facts are not prose claims. Never paste a raw verdict into the narrative.
    prose = re.sub(r'```.*?```','',text,flags=re.S)
    forbidden = ['工程验证通过','可用于设计','结果可信'] if state != STATES[3] else []
    if state in STATES[:2]:forbidden += ['收敛','验证通过']
    if state == STATES[0]:forbidden += ['成功','已完成','已执行']
    positive_prose=re.sub(r'(?:未|不|没有|不得|不能|尚未)(?:达到|实现|用于)?(?:工程验证通过|可用于设计|结果可信|验证通过|收敛)', '', prose)
    if any(word in positive_prose for word in forbidden):raise QualityContractError('Report makes a claim above '+state)
    prose=re.sub(r'\b(?:not|never|not yet|not numerically)\s+(?:engineering verified|design ready|reliable|converged)\b','',prose,flags=re.I)
    if state != STATES[3] and re.search(r'\b(engineering verified|design ready|results? (?:are )?reliable|converged)\b',prose,re.I):
        raise QualityContractError('Report makes an English claim above '+state)
    return True


def records_from_directory(directory, *, run_id=None, execution_state=None):
    """Adapt actual runner files; absent fields remain absent, never inferred PASS."""
    directory = Path(directory)
    state_path = directory/'state.json'
    state = read(state_path) if state_path.is_file() else {}
    if not state and (directory/'case-probe.json').is_file():state=read(directory/'case-probe.json')
    request = read(directory/'request.json') if (directory/'request.json').is_file() else {}
    stages = []; artifacts = []; evidence = []; payloads = {}
    stage_files = list(directory.glob('stages/*/stage-*.json')) or list(directory.glob('stage-*.json'))
    own = {}
    for path in stage_files:
        data = read(path);name=data.get('stage',path.stem.removeprefix('stage-'))
        job_request=path.parent/'request.json'
        req=read(job_request) if job_request.is_file() else {}
        if req.get('stage') and name!=req['stage']:continue  # inherited stage record is not this job
        key=path.parent.name if path.parent!=directory else name
        own.setdefault(key, (path,data,req))
    config = None
    for p in [directory.parent/(directory.name+'-config.json'),directory/'reviewed-pipeline.json']:
        if p.is_file():config=read(p);break
    for key,(path,data,req) in own.items():
        name=data.get('stage',path.stem.removeprefix('stage-'))
        workflow=req.get('workflow_id')
        manifest=None
        if workflow:
            for p in [directory/'_runtime_manifests'/(workflow+'.json'),ROOT/'workflows'/workflow/'manifest.json']:
                if p.is_file():manifest={'path':str(p),'sha256':sha256(p)};break
        if manifest is None and config:
            p=directory.parent/(directory.name+'-config.json')
            if not p.is_file():p=directory/'reviewed-pipeline.json'
            manifest={'path':str(p),'sha256':sha256(p)}
        macro_ok=bool(data.get('macros')) and all(m.get('status')=='ok' and m.get('ran') is not False for m in data.get('macros',[]))
        stages.append({'key':key,'name':name,'state':('ok' if data.get('state')=='ok' and macro_ok else 'failed' if data.get('state') in ('failed','FAILED','error') or any(m.get('status') in ('failed','error') for m in data.get('macros',[])) else 'unknown'),
                       'job_id':req.get('job_id') or (read(path.parent/'state.json').get('job_id') if (path.parent/'state.json').is_file() else None),
                       'manifest':manifest,'workflow_id':workflow,'record':file_ref(path), 'request':file_ref(path.parent/'request.json'), 'runner_required_artifacts':data.get('artifacts') or []})
        names=set(data.get('artifacts') or [])
        if manifest:
            m=read(manifest['path']);entries=m['stages']
            try:
                entry=entries[name] if isinstance(entries,dict) else next(x for x in entries if x['name']==name)
                names.update(entry.get('required') or [])
            except (KeyError,StopIteration):pass
        for name_ in sorted(names):
            p=path.parent/name_;h=(data.get('artifact_hashes') or {}).get(name_)
            # Historic trial records may record sim hashes at macro/output scope.
            if not h:
                for macro in data.get('macros',[]):
                    if macro.get('output')==name_:h=macro.get('output_sha256')
            snapshot=directory/'_remote_artifacts.json'
            remote=read(snapshot).get(str(p.relative_to(directory))) if snapshot.is_file() else None
            actual=sha256(p) if p.is_file() else remote
            artifacts.append({'identity':key+'/'+name_,'name':name_,'state':'VERIFIED' if h and actual==h else 'MISSING_OR_UNVERIFIED',
                              'sha256':actual,'runner_sha256':h,'path':str(p) if p.is_file() else None,'runner_record':file_ref(path),'remote_proof':file_ref(snapshot) if snapshot.is_file() else None,'remote_key':str(p.relative_to(directory))})
        for p in path.parent.glob('*.json'):
            if p.name.startswith('stage-') or p.name in ('state.json','request.json','checkpoint.json','case_spec.json'):continue
            try:payload=read(p)
            except (ValueError,OSError):continue
            payloads[p.name]=(payload,p)
    for name,(data,p) in payloads.items():
        if isinstance(data,dict) and data.get('status') in ('ok','gate_failed','error'):
            evidence.append({'path':str(p),'sha256':sha256(p),'certificate':False})
    mesh,_=payloads.get('external_flow_mesh_quality.json',({},None))
    valid,_=payloads.get('external_flow_validation.json',({},None))
    specpath=next(iter(directory.glob('stages/*/case_spec.json')),directory/'case_spec.json')
    spec=read(specpath) if specpath.is_file() else request.get('case_spec') or {}
    q=spec.get('quality') or {}
    macros={}
    for _,_,req in own.values():macros.update(req.get('macro_hashes') or {})
    plan=[s.get('key') for s in request.get('steps',[])] if request.get('steps') else [s.get('name') for s in (config or {}).get('stages',[])]
    if not plan:plan=state.get('completed') or []
    result={'execution':{'run_id':run_id or state.get('run_id') or directory.name,'state':execution_state or state.get('state'),
             'request':file_ref(directory/'request.json'),'state_record':file_ref(state_path),'runtime_manifest':file_ref(directory/'_runtime_manifests'/'runtime-manifest.json'),'stages':stages,'plan':plan,'required_artifacts':artifacts,'expected_artifacts':[a['identity'] for a in artifacts]},
        'numerics':{'mesh_quality':{'cells':mesh.get('cells'),'min_cell_quality':mesh.get('measured_cell_quality_min'),
              'min_face_validity':mesh.get('measured_face_validity_min'),'min_volume_change':mesh.get('measured_volume_change_min'),
              'min_wall_prism_layers':mesh.get('measured_wall_prism_layers_min'),
              'gates':{'cell_quality':q.get('mesh_min_cell_quality'),'face_validity':q.get('mesh_min_face_validity'),
                       'volume_change':q.get('mesh_min_volume_change'),'prism_layers':q.get('mesh_min_wall_prism_layers')}},
            'convergence':{'verdict':valid.get('validation_verdict'),'residual_gates':valid.get('residual_gates',{}),
                'residuals_met':valid.get('residuals_met'),'mass_imbalance_ratio':valid.get('measured_mass_imbalance_ratio'),
                'mass_imbalance_gate':valid.get('measured_mass_imbalance_gate'),'target_window_relative_change':valid.get('drag_window_relative_deviation'),
                'target_window_gate':valid.get('target_stability_gate'),'target_quantities_steady':valid.get('target_quantities_steady')},
            'sensitivity':{'mesh_levels':{'required':q.get('mesh_levels'),'executed':None,'gci_32':None},
                'domain_levels':{'required':3 if q.get('domain_sensitivity') is True else None,'executed':None,'relative_change':None},
                'time_step':{'required':q.get('time_step_sensitivity'),'executed':None,'relative_change':None}}},
        'engineering':{'reference':{'status':valid.get('reference_status'),'source':valid.get('reference_policy'),
            'value':valid.get('reference_value'),'measured_value':valid.get('measured_drag_coefficient_estimate'),
            'tolerance':valid.get('reference_acceptance_tolerance'),'relative_error':valid.get('relative_error_against_reference'),
            'verified':valid.get('reference_source_verified')},'scope_statement':spec.get('objective') or '适用范围未建立'},
        'provenance':{'release_id':request.get('release_id'),'runtime_digest':request.get('runtime_digest') or state.get('runtime_digest'),
            'case_spec_record':file_ref(specpath),'case_spec_sha256':sha256(specpath) if specpath.is_file() else None,'input_sha256':request.get('input_sha256'),
            'macro_sha256':macros,'input_record':file_ref(directory/'input.sim'),'run_record_citation':CITABLE if evidence and all(citation_status(x['path'])['state']==CITABLE for x in evidence) else 'NOT_ESTABLISHED'},
        'evidence':evidence,'field_sources':{}}
    # Measurement supplements may never replace runner execution/provenance.
    for payload,p in payloads.values():
        if isinstance(payload,dict) and isinstance(payload.get('quality_inputs'),dict):
            supplement=payload['quality_inputs']
            if citation_status(p)['state']!=CITABLE:
                raise QualityContractError('Failed/uncitable stage supplied quality certificate')
            for key in ('execution','provenance'):
                if key in supplement and not _same(supplement[key],result[key]):
                    raise QualityContractError('Quality certificate conflicts with runner '+key)
            for key in ('numerics','engineering'):
                if key in supplement:result[key]=copy.deepcopy(supplement[key])
            if finite(payload.get('target_mean')):
                result['engineering']['target_source']=dict(file_ref(p),pointer='target_mean',quantity=payload.get('target_quantity'),units=payload.get('target_units'),statistic=payload.get('target_statistic'))
            result['evidence'].append({'path':str(p),'sha256':sha256(p),'certificate':True})
            result['field_sources']={key:{'path':str(p),'pointer':'quality_inputs/'+key} for key in
                ('execution','numerics/mesh_quality','numerics/convergence','numerics/sensitivity','provenance','engineering/reference','engineering/measured_value')}
            break
    return result



def legacy_inventory(directory):
    """Inventory old sidecars without translating approvals into new execution records."""
    directory=Path(directory);prefix=directory.parent/directory.name
    paths={'config':Path(str(prefix)+'-config.json'),'review':directory.parent/'pipeline-review.json',
           'delivery':Path(str(prefix)+'-checkpoints.json'),'index':Path(str(prefix)+'-evidence-index.json')}
    data={key:read(path) for key,path in paths.items() if path.is_file()}
    return {'sidecars':{key:file_ref(path) for key,path in paths.items() if path.is_file()},
            'legacy_input_hashes':data.get('config',{}).get('input_hashes'),
            'legacy_macro_hashes':data.get('config',{}).get('macro_hashes'),
            'legacy_runner_sha256':data.get('review',{}).get('runner_sha256'),
            'legacy_stage_order':[x.get('name') for x in data.get('config',{}).get('stages',[])],
            'delivery_record_present':isinstance(data.get('delivery',{}).get('artifacts'),list) and bool(data['delivery']['artifacts']) if 'delivery' in data else False,
            'historical_runtime_manifest_bound':False}


def legacy_diagnostic_packet(directory):
    inv=legacy_inventory(directory)
    path=Path(directory)/'case-probe.json'
    state=read(path).get('state') if path.is_file() else None
    return {'execution':{'state':state,'legacy_delivery_record':inv['sidecars'].get('delivery') if inv['delivery_record_present'] else None},
            'historical_inventory':inv}


# --- Gate waivers ---------------------------------------------------------------
# A waiver records a gate the result does NOT meet, together with the evidence that the
# failure is small, localised and bounded, and an explicit signature state. It is
# deliberately NOT a member of STATES: STATES is index-ranked (support_matrix selects a
# scenario's certificate with max()), and inserting a state there would let a waived
# artifact outrank a genuine Q3 certificate and would silently relabel the numeric and
# engineering columns. A waiver therefore never moves quality_state; it only makes the gap
# citable and machine-checkable. Nothing here can turn a failure into a pass.
WAIVER_KIND = 'mesh_gate_waiver'
WAIVER_SCHEMA_VERSION = 1
WAIVER_STATUSES = ('PENDING_USER_SIGNATURE', 'SIGNED', 'REVOKED')
# metric -> (comparison operator, CaseSpec key, measured key inside the waiver)
WAIVER_METRICS = {'volume_change': ('>', 'mesh_min_volume_change', 'volume_change_min'),
                  'cell_quality': ('>=', 'mesh_min_cell_quality', 'cell_quality_min'),
                  'face_validity': ('>=', 'mesh_min_face_validity', 'face_validity_min')}


def _number_or_none(value):
    """A finite number or None. NaN/inf are refused: they pass every comparison silently."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(float(value)) else None


def validate_waiver(document, *, case_spec=None, mesh_sha256=None, base_dir=None):
    """Return (summary, errors, checks) for one waiver document.

    Every check is named so a report can say which one failed instead of quoting prose.
    `case_spec` is optional: without it the threshold equality against the CaseSpec cannot
    be tested, and the summary says so rather than assuming agreement.
    """
    checks = []

    def need(condition, code, detail=''):
        checks.append({'check': code, 'ok': bool(condition), 'detail': detail})
        if not condition:
            errors.append(code)

    errors = []
    need(document.get('schema_version') == WAIVER_SCHEMA_VERSION, 'waiver:schema_version')
    need(document.get('kind') == WAIVER_KIND, 'waiver:kind', str(document.get('kind')))

    gate = document.get('gate') if isinstance(document.get('gate'), dict) else {}
    metric = gate.get('metric')
    need(metric in WAIVER_METRICS, 'waiver:gate_metric', str(metric))
    operator, spec_key, measured_key = WAIVER_METRICS.get(metric, ('', '', ''))
    need(gate.get('op') == operator, 'waiver:gate_op', 'expected ' + str(operator))
    threshold = _number_or_none(gate.get('threshold'))
    need(threshold is not None and threshold > 0, 'waiver:gate_threshold', str(gate.get('threshold')))
    need(gate.get('unchanged') is True, 'waiver:gate_not_declared_unchanged')

    if case_spec is not None and metric in WAIVER_METRICS:
        quality = case_spec.get('quality') if isinstance(case_spec.get('quality'), dict) else {}
        spec_value = _number_or_none(quality.get(spec_key))
        need(spec_value is not None and threshold is not None
             and abs(threshold - spec_value) <= 1e-12, 'waiver:threshold_differs_from_case_spec',
             '%r vs %r' % (gate.get('threshold'), quality.get(spec_key)))

    measured_doc = document.get('measured') if isinstance(document.get('measured'), dict) else {}
    measured = _number_or_none(measured_doc.get(measured_key))
    need(measured is not None, 'waiver:measured_missing', measured_key)
    if measured is not None and threshold is not None:
        need(measured < threshold, 'waiver:gate_not_failed',
             'measured %r is not below threshold %r' % (measured, threshold))

    mesh = document.get('mesh') if isinstance(document.get('mesh'), dict) else {}
    need(bool(HEX.fullmatch(str(mesh.get('sha256') or ''))), 'waiver:mesh_sha_missing')
    if mesh_sha256 is not None:
        need(str(mesh.get('sha256')) == str(mesh_sha256), 'waiver:mesh_sha_differs')

    localization = measured_doc.get('localization') if isinstance(measured_doc.get('localization'), dict) else {}
    record = localization.get('record')
    digest = localization.get('sha256')
    need(bool(record) and bool(HEX.fullmatch(str(digest or ''))), 'waiver:localization_missing')
    if record and base_dir is not None:
        # The record is usually a bare file name that lives in the arm's directory, so match
        # by CONTENT anywhere in the waiver's subtree: a same-named file with other bytes
        # must not satisfy the check.
        name = str(record)
        if '/' in name or '\\' in name:
            candidates = [Path(base_dir) / name]
        else:
            candidates = [Path(base_dir) / name, Path(base_dir).parent / name]
            candidates += sorted(Path(base_dir).rglob(name))
        matched = [item for item in candidates if item.is_file() and sha256(item) == digest]
        need(bool(matched), 'waiver:localization_sha_differs', name)

    status = document.get('signature_status')
    need(status in WAIVER_STATUSES, 'waiver:signature_status', str(status))
    if status == 'SIGNED':
        need(bool(str(document.get('signed_by') or '').strip()), 'waiver:signed_by_missing')
        need(bool(str(document.get('signed_on') or '').strip()), 'waiver:signed_on_missing')
    limits = document.get('scope_limits')
    limits_ok = ((isinstance(limits, list) and bool(limits)
                  and all(isinstance(item, str) and item.strip() for item in limits))
                 or (isinstance(limits, str) and bool(limits.strip())))
    need(limits_ok, 'waiver:scope_limits_missing')

    case_id = document.get('case_id')
    need(bool(str(case_id or '').strip()), 'waiver:case_id_missing')
    if case_spec is not None:
        spec_case = case_spec.get('case_id') or case_spec.get('case_ref')
        need(str(case_id or '') == str(spec_case or ''), 'waiver:case_id_differs_from_case_spec',
             '%r vs %r' % (case_id, spec_case))

    # Conditions are not structural errors: the document is readable and self-consistent, but
    # it is not yet strong enough to be cited. Frozen-ness belongs here because a threshold
    # taken from a draft CaseSpec is the CaseSpec's own declaration, not evidence.
    conditions = []
    if case_spec is not None and case_spec.get('case_spec_frozen') is not True:
        conditions.append('waiver:case_spec_not_frozen')

    summary = {'case_id': case_id, 'metric': metric, 'threshold': threshold,
               'measured': measured,
               'margin': (measured / threshold) if (measured is not None and threshold) else None,
               'status': status, 'conditions': conditions,
               'mesh_sha256': mesh.get('sha256'), 'run_id': mesh.get('run_id'),
               'cells': mesh.get('cells'), 'signed_by': document.get('signed_by'),
               'signed_on': document.get('signed_on'), 'checked_against_case_spec': case_spec is not None}
    summary['valid'] = not errors
    # A citation right needs ALL of: a signed record, a cross-checked CaseSpec, no structural
    # error, and no binding condition. `valid` alone (or `SIGNED` alone) is never enough.
    summary['usable'] = status == 'SIGNED' and case_spec is not None and not errors and not conditions
    return summary, errors, checks


def waiver_paths(root=ROOT, pattern='campaigns/*/evidence/**/MESH-GATE-WAIVER-*.json'):
    return sorted(Path(root).glob(pattern))


def declared_case_spec_path(document, root=ROOT):
    """The CaseSpec path a waiver claims, returned only when it is a real project file."""
    gate = document.get('gate') if isinstance(document.get('gate'), dict) else {}
    relative = str(gate.get('source') or '').split(':', 1)[0].strip()
    if not relative.endswith('.json'):
        return None
    candidate = Path(root) / relative
    return candidate if candidate.is_file() else None


def declared_case_spec(document, root=ROOT):
    """The CaseSpec a waiver claims to check against, resolved from gate.source only if real.

    The claim is a pointer, not an authority: the path must exist and its threshold must
    then match, which validate_waiver tests. Without a resolvable CaseSpec the waiver is
    structurally readable but cannot become `usable` (the threshold would be self-declared).
    """
    candidate = declared_case_spec_path(document, root)
    if candidate is None:
        return None
    try:
        return read(candidate)
    except (OSError, ValueError):
        return None


def load_waivers(paths=None, *, root=ROOT, case_spec_path=None, mesh_sha256=None):
    """Validate every waiver found (or listed). Discovery is bounded to the campaign tree."""
    explicit = read(Path(root) / case_spec_path) if case_spec_path else None
    items = []
    for path in (waiver_paths(root) if paths is None else [Path(item) for item in paths]):
        try:
            document = read(path)
        except (OSError, ValueError):
            continue
        spec = explicit if explicit is not None else declared_case_spec(document, root)
        summary, errors, checks = validate_waiver(document, case_spec=spec, mesh_sha256=mesh_sha256,
                                                   base_dir=Path(path).parent)
        # `gate.unchanged: true` is a claim. When the waiver also records the CaseSpec file's
        # sha256, the claim becomes checkable here instead of being taken on trust.
        gate = document.get('gate') if isinstance(document.get('gate'), dict) else {}
        recorded = gate.get('case_spec_sha256')
        spec_path = declared_case_spec_path(document, root)
        if recorded is not None:
            same = spec_path is not None and sha256(spec_path) == str(recorded)
            checks.append({'check': 'waiver:case_spec_sha_differs', 'ok': bool(same),
                           'detail': str(recorded)})
            if not same:
                errors.append('waiver:case_spec_sha_differs')
                summary['valid'] = False
                summary['usable'] = False
        try:
            shown = str(Path(path).resolve().relative_to(Path(root).resolve()))
        except ValueError:
            shown = str(path)
        items.append({'path': shown, 'sha256': sha256(path), 'summary': summary,
                      'errors': errors, 'checks': checks})
    return items


def waivers_for_run(run_id, *, root=ROOT, case_spec_path=None):
    return [item for item in load_waivers(root=root, case_spec_path=case_spec_path)
            if item['summary'].get('run_id') == run_id]


def render_waiver_block(waivers):
    """Markdown for reports. A waiver line is never a pass line; the wording says so."""
    if not waivers:
        return ''
    rows = []
    for item in waivers:
        summary = item['summary']
        rows.append('| %s | %s | %s | %s | %s | %s | %s | [record](%s) `%s` |' % (
            summary.get('case_id'), summary.get('metric'), summary.get('threshold'),
            summary.get('measured'),
            '—' if summary.get('margin') is None else '%.3f' % summary['margin'],
            summary.get('status'),
            '可引用' if summary.get('usable') else '不可引用（%s）' % ('; '.join(
                ([] if summary.get('status') == 'SIGNED' else ['未签字'])
                + ([] if summary.get('valid') else ['记录不成立'])
                + ([] if summary.get('checked_against_case_spec') else ['未与 CaseSpec 交叉核对'])
                + summary.get('conditions', [])) or '不满足引用条件'),
            item['path'], item['sha256']))
    return ('## 门豁免（记录的是未通过项，不是通过）\n\n'
            '| 案例 | 门 | 门限 | 实测 | 占比 | 签名状态 | 引用资格 | 记录 |\n'
            '|---|---|---|---|---|---|---|---|\n' + '\n'.join(rows) + '\n')


def support_matrix(progress_path=ROOT/'progress.json'):
    progress=read(progress_path);rows=[]
    for scenario,entry in progress.get('scenarios',{}).items():
        candidates=[]
        for item in progress.get('quality_contract',{}).get('records',[]):
            if item.get('scenario')!=scenario:continue
            try:
                packet=records_from_directory(ROOT/item['directory']);block=render_quality_block(packet)
                p=Path(item['citation']['path'])
                if not p.is_absolute():p=ROOT/p
                if sha256(p)!=item['citation']['sha256'] or citation_status(p)['state']!=CITABLE:continue
                candidates.append((STATES.index(block['quality_state']),p.stat().st_mtime,block,p))
            except (ValueError,KeyError,OSError,TypeError):continue
        seen=set()
        for stage in entry.get('stages',{}).values():
            p=ROOT/str(stage.get('record') or '')
            if p.is_file() and citation_status(p)['state']==CITABLE:
                if p.parent in seen:continue
                seen.add(p.parent)
                try:
                    if not (p.parent/'request.json').is_file():
                        block=render_quality_block(legacy_diagnostic_packet(p.parent))
                    else:
                        packet=records_from_directory(p.parent);block=render_quality_block(packet)
                    candidates.append((STATES.index(block['quality_state']),p.stat().st_mtime,block,p))
                except (ValueError,KeyError,OSError,TypeError):pass
        if candidates:
            _,_,block,p=max(candidates,key=lambda x:(x[0],x[1]));state=block['quality_state']
            certificate={'path':str(p.relative_to(ROOT)),'sha256':sha256(p),'citation_state':CITABLE}
        else:
            state=STATES[0];block={'engineering':{'e_num':'NOT_ESTABLISHED','e_phys':'NOT_APPLICABLE'},'Q0_REASON':['UNBOUND_HISTORICAL_EVIDENCE','NO_DELIVERY_RECORD']};certificate=None
        rows.append({'scenario':scenario,'sample':entry.get('case') or entry.get('name'),
            'P':entry.get('pipeline'),'quality_state':state,'Q0_REASON':block.get('Q0_REASON',[]) if state==STATES[0] else [],'E-num':block['engineering']['e_num'],
            'E-phys':block['engineering']['e_phys'],'numeric_label':'NUMERICALLY_UNVERIFIED' if STATES.index(state)<2 else 'NUMERICALLY_VERIFIED',
            'engineering_label':'ENGINEERING_UNVERIFIED' if state!=STATES[3] else 'ENGINEERING_VERIFIED',
            'scope': '失败诊断' if state==STATES[0] else '流程观察' if state==STATES[1] else '同一模型内相对比较' if state==STATES[2] else block['engineering']['scope_statement'],
            'certificate':certificate,'limitations':block.get('limitations') or entry.get('blocking_reason')})
    return rows


def render_matrix(rows):
    lines=['## 支持范围矩阵','','| 场景 / 样本 | P | 最高状态 | E-num | E-phys | 标签 / 范围 | Q0_REASON | 记录 |',
           '|---|---|---|---|---|---|---|---|']
    for row in rows:
        source=row.get('certificate');link=('[record](%s) `%s`' % (source['path'],source['sha256'])) if source else '无可引用证书'
        lines.append('| %s / %s | %s | %s | %s | %s | %s / %s / %s | %s | %s |' %
            (row['scenario'],row['sample'],row['P'],row['quality_state'],row['E-num'],row['E-phys'],row['numeric_label'],row['engineering_label'],row['scope'],', '.join(row.get('Q0_REASON') or []) or '—',link))
    return '\n'.join(lines)+'\n'


def render_report(block, *, title='结果质量报告', process=None, delivery=None, matrix=None):
    # Never interpolate uncontrolled case labels or raw narrative verdicts as claims.
    if matrix is not None and matrix != support_matrix():
        raise QualityContractError('Support matrix differs from canonical matrix')
    text='# 结果质量报告\n\nquality_state: `'+block['quality_state']+'`\n\n'+'scope_statement: '+block['scope_statement']+'\n\nforbidden_uses:\n```json\n'+json.dumps(block['forbidden_uses'],ensure_ascii=False)+'\n```\n\nQ0_REASON: `'+json.dumps(block['Q0_REASON'])+'`\n\n'
    text+='## 结构化质量记录\n\n```json\n'+json.dumps(block,ensure_ascii=False,indent=2,allow_nan=False)+'\n```\n\n'
    text+='E-num: `'+block['engineering']['e_num']+'`；E-phys: `'+block['engineering']['e_phys']+'`；PAT: 独立发布门。\n\n'
    text+='## 分列状态\n\n```json\n'+json.dumps({'process':process,'delivery':delivery,'engineering':block['engineering']},ensure_ascii=False,indent=2,allow_nan=False)+'\n```\n\n'
    text+=render_matrix(support_matrix() if matrix is None else matrix)
    validate_report_claims(text,block['quality_state'],block)
    return text


def file_ref(path):
    p=Path(path)
    return {'path':str(p.resolve()),'sha256':sha256(p)} if p.is_file() else None


def checked_file(ref):
    if not isinstance(ref,dict) or not ref.get('path') or not HEX.fullmatch(str(ref.get('sha256',''))):
        raise QualityContractError('Missing hash-bound authority file')
    p=Path(ref['path'])
    if not p.is_file() or sha256(p)!=ref['sha256']:raise QualityContractError('Authority file SHA differs: '+str(p))
    return read(p)


def artifact_proof(a):
    try:
        record=checked_file(a['runner_record']);h=record['artifact_hashes'][a['name']]
        if a['sha256']!=h or a.get('runner_sha256')!=h:return False
        if a.get('path'):return sha256(a['path'])==h
        return checked_file(a['remote_proof'])[a['remote_key']]==h
    except (KeyError,TypeError,OSError,ValueError):return False


def git_introduced(path):
    """Committer timestamp of the commit that first added this path, ISO-8601 or None.

    This is the only timestamp the collection form trusts: it is produced by the commit
    that put the record in history, not by a field inside the record, so it cannot be
    back-dated by writing a file.
    """
    try:
        out=subprocess.run(['git','-C',str(ROOT),'log','--diff-filter=A','--format=%cI','--',
                            str(path)],capture_output=True,text=True)
    except OSError:
        return None
    if out.returncode!=0:return None
    lines=[line.strip() for line in out.stdout.splitlines() if line.strip()]
    return lines[-1] if lines else None


def active_runtime_pointer():
    """The runtime digest the active deployment review pins, read from the pointer file."""
    try:
        active=read(ROOT/ACTIVE_DEPLOYMENT_POINTER)
        ref=ROOT/active['review_ref']
        if sha256(ref)!=active['review_sha256']:return None
        return read(ref).get('runtime_digest')
    except (KeyError,TypeError,OSError,ValueError):
        return None


def admission_names_request(admission, member):
    """Does this admission name this request? Batch admissions list their requests.

    A one-shot admission pins one request by hash; a batch admission covers several and
    names each inside its requests block. Both are real, and neither is inferred: the
    member's request hash or request id must literally appear in the admission.
    """
    wanted=member.get('request') or {}
    if admission.get('request_sha256')==wanted.get('sha256'):return True
    requests=admission.get('requests')
    if isinstance(requests,dict):requests=list(requests.values())
    if not isinstance(requests,list):return False
    rid=member.get('request_id')
    for item in requests:
        if not isinstance(item,dict):continue
        if item.get('request_sha256')==wanted.get('sha256'):return True
        if item.get('sha256')==wanted.get('sha256'):return True
        if rid and item.get('request_id')==rid:return True
        inner=item.get('request')
        if isinstance(inner,dict) and rid and inner.get('request_id')==rid:return True
    return False


def execution_authority_collection(ex):
    """C3: the one-shot authorization collection. Five properties, each machine-checked.

    Every property is checked against a record and, where order matters, against the
    commit that introduced that record. A file authored after the job ran cannot pass:
    its commit is later than the commit of the submission record it would have to precede.
    """
    errors=[]
    try:
        collection=checked_file(ex['collection'])
    except (QualityContractError,KeyError,TypeError,OSError,ValueError):
        return {},['execution:collection_binding_unverified']
    if collection.get('form')!=COLLECTION_FORM:
        return {},['execution:collection_form_not_declared']
    members=collection.get('members')
    if not isinstance(members,list) or not members:
        return {},['execution:collection_members_missing']
    pointer=active_runtime_pointer()
    if not HEX.fullmatch(str(pointer or '')):
        return {},['execution:collection_runtime_pointer_unresolved']
    # Property 4, part two: the member list is complete. Every job the collection
    # declares must be a member, and no member may appear twice.
    declared=collection.get('declared_jobs')
    jobs=[m.get('job') for m in members]
    if len(set(jobs))!=len(jobs):errors.append('execution:collection_member_duplicated')
    if not isinstance(declared,list) or not declared or set(declared)!=set(jobs):
        errors.append('execution:collection_members_incomplete')
    steps=[]
    for member in members:
        job=member.get('job')
        try:
            admission=checked_file(member['admission'])
            checked_file(member['request'])
            checked_file(member['submission_record'])
        except (QualityContractError,KeyError,TypeError,OSError,ValueError):
            errors.append('execution:collection_member_unverified:'+str(job))
            continue
        # Property 1: the admission was committed before the job was submitted.
        admitted=git_introduced(member['admission']['path'])
        submitted=git_introduced(member['submission_record']['path'])
        if not admitted or not submitted:
            errors.append('execution:collection_order_unproven:'+str(job))
        elif admitted>=submitted:
            errors.append('execution:collection_order_inverted:'+str(job))
        # Property 2: the admission pins this job's request, CaseSpec, source/macro bytes
        # and the runtime digest observation it was admitted under.
        if not admission_names_request(admission,member):
            errors.append('execution:collection_pin_request_differs:'+str(job))
        spec=member.get('case_spec')
        if not isinstance(spec,dict):
            errors.append('execution:collection_pin_casespec_missing:'+str(job))
        elif admission.get('spec_sha256')!=spec.get('sha256'):
            errors.append('execution:collection_pin_casespec_differs:'+str(job))
        if not any(HEX.fullmatch(str(admission.get(k,'')))
                   for k in ('source_sha256','macro_sha256','mesh_sha256')):
            errors.append('execution:collection_pin_source_or_macro_missing:'+str(job))
        if not HEX.fullmatch(str(admission.get('runtime_digest',''))):
            errors.append('execution:collection_pin_runtime_missing:'+str(job))
        elif admission.get('runtime_digest')!=member.get('runtime_digest'):
            errors.append('execution:collection_pin_runtime_differs:'+str(job))
        # Property 3: an independent review of this admission, named by the admission
        # itself by hash, and not written after the job was submitted.
        review=member.get('independent_review')
        if not isinstance(review,dict):
            errors.append('execution:collection_member_review_missing:'+str(job))
        else:
            try:
                doc=checked_file(review)
            except (QualityContractError,KeyError,TypeError,OSError,ValueError):
                doc=None
                errors.append('execution:collection_review_unverified:'+str(job))
            if doc is not None:
                if not isinstance(doc,dict) or not doc.get('reviewer') or not (
                        doc.get('independent') is True or doc.get('review_passed') is True):
                    errors.append('execution:collection_review_not_independent:'+str(job))
                if str(review['sha256']) not in {str(v) for v in admission.values()
                                                 if isinstance(v,str)}:
                    errors.append('execution:collection_review_not_named_by_admission:'
                                  +str(job))
                reviewed=git_introduced(review['path'])
                if not reviewed or not submitted:
                    errors.append('execution:collection_review_order_unproven:'+str(job))
                elif reviewed>submitted:
                    errors.append('execution:collection_review_retrofitted:'+str(job))
        # Property 5: the runtime digest observed by this job's own record agrees with the
        # active deployment pointer. There is no manifest snapshot to pin instead.
        observed=member.get('runtime_digest')
        if not HEX.fullmatch(str(observed or '')):
            errors.append('execution:collection_runtime_digest_missing:'+str(job))
        elif observed!=pointer:
            errors.append('execution:collection_runtime_digest_differs:'+str(job))
        steps.append({'key':job,'stage':member.get('stage'),
                      'macro_hashes':member.get('macro_hashes') or {}})
    authority={'input_sha256':collection.get('input_sha256'),'runtime_digest':pointer,
               'release_id':collection.get('release_id'),'steps':steps,
               'form':COLLECTION_FORM,'collection_members':len(members),
               'no_retrofit':COLLECTION_NO_RETROFIT,
               'forward_looking':COLLECTION_FORWARD_LOOKING}
    spec=collection.get('case_spec')
    if isinstance(spec,dict):
        try:
            authority['case_spec']=checked_file(spec)
        except (QualityContractError,KeyError,TypeError,OSError,ValueError):
            errors.append('execution:collection_casespec_unverified')
    # A collection that fails any property yields nothing a caller could use.
    return ({},errors) if errors else (authority,[])


def execution_authority(ex):
    """One run's pipeline authority, or (C3) a one-shot authorization collection."""
    authority,errors=_execution_authority_single(ex)
    if not errors:
        return authority,errors
    if not isinstance(ex.get('collection'),dict):
        return authority,errors
    collection_authority,collection_errors=execution_authority_collection(ex)
    if not collection_errors:
        return collection_authority,[]
    return {},errors+collection_errors


def _execution_authority_single(ex):
    errors=[]
    try:
        request=checked_file(ex['request']); state=checked_file(ex['state_record'])
        if state.get('state')!=ex.get('state'):errors.append('execution:state_conflict')
        plan=request.get('steps')
        if not isinstance(plan,list) or not plan:raise QualityContractError('Missing root request graph')
        if request.get('run_id')!=ex.get('run_id'):errors.append('execution:run_identity_conflict')
        keys=[x['key'] for x in plan]
        if len(keys)!=len(set(keys)) or keys!=ex.get('plan'):errors.append('execution:ordered_plan_conflict')
        runtime=checked_file(ex['runtime_manifest'])
        if ex['runtime_manifest']['sha256']!=request.get('runtime_manifest_sha256') or runtime.get('runtime_digest')!=request.get('runtime_digest'):
            errors.append('execution:runtime_pin_conflict')
        runtime_files={x['path']:x['sha256'] for x in runtime['files']}
        bykey={s['key']:s for s in ex['stages']}
        if len(bykey)!=len(ex['stages']) or set(bykey)!=set(keys):errors.append('execution:graph_conflict')
        expected=set()
        for step in plan:
            stage=bykey[step['key']]; sr=checked_file(stage['record']); req=checked_file(stage['request'])
            if stage['name']!=step['stage'] or stage['workflow_id']!=step['workflow_id'] or req.get('stage')!=step['stage'] or req.get('workflow_id')!=step['workflow_id']:
                errors.append('execution:stage_identity_conflict')
            if sr.get('state')!='ok' or not sr.get('macros') or any(m.get('status')!='ok' or m.get('ran') is False for m in sr['macros']):
                errors.append('execution:runner_stage_failed')
            if any(runtime_files.get('macros/'+name)!=h for name,h in step.get('macro_hashes',{}).items()):errors.append('execution:macro_runtime_pin_conflict')
            pin=runtime_files['workflows/'+step['workflow_id']+'/manifest.json']
            if sr.get('stage')!=step['stage']:errors.append('execution:runner_stage_identity_conflict')
            if req.get('job_id')!=request['run_id']+'-'+step['key'] or stage.get('job_id')!=req.get('job_id'):errors.append('execution:job_identity_conflict')
            if not HEX.fullmatch(str(request.get('request_digest',''))) or req.get('request_digest')!=request.get('request_digest'):errors.append('execution:request_digest_conflict')
            input_hash=req.get('source_sha256') or req.get('input_sha256')
            if sr.get('input_sha256')!=input_hash or not HEX.fullmatch(str(input_hash or '')):errors.append('execution:stage_input_conflict')
            if not sr.get('macros') or sr['macros'][0].get('input_sha256')!=input_hash:errors.append('execution:macro_input_conflict')
            stage_definition=checked_file(stage['manifest'])['stages'][step['stage']]
            if [m.get('macro') for m in sr.get('macros',[])]!=stage_definition['macros'] or set(req.get('macro_hashes',{}))!=set(stage_definition['macros']):errors.append('execution:macro_order_conflict')
            if any(type(m.get('exit_code')) is not int or m['exit_code']!=0 for m in sr['macros']):errors.append('execution:macro_exit_conflict')
            expected.update(step['key']+'/'+name for name in stage_definition['required'])
            if stage['manifest']['sha256']!=pin:errors.append('execution:manifest_pin_conflict')
            if req.get('runtime_digest')!=request.get('runtime_digest') or req.get('macro_hashes')!=step.get('macro_hashes'):
                errors.append('execution:stage_request_conflict')
        for a in ex['required_artifacts']:
            key,name=a['identity'].split('/',1);stage=bykey[key]
            if a['name']!=name or not _same(a.get('runner_record'),stage['record']):errors.append('execution:artifact_runner_identity_conflict')
            parent=Path(stage['record']['path']).parent;expected_path=parent/name
            if a.get('path') and Path(a['path']).resolve()!=expected_path.resolve():errors.append('execution:artifact_path_conflict')
            if a.get('path') is None:
                proof=a.get('remote_proof') or {};root=Path(ex['request']['path']).parent
                if Path(proof.get('path','')).resolve()!=(root/'_remote_artifacts.json').resolve():errors.append('execution:remote_context_conflict')
                meta=checked_file(proof);context=meta.get('_context',{})
                if context.get('request_sha256')!=ex['request']['sha256'] or context.get('run_id')!=ex['run_id'] or context.get('runtime_digest')!=request.get('runtime_digest'):errors.append('execution:remote_context_conflict')
                if a.get('remote_key')!=str(expected_path.relative_to(root)):errors.append('execution:remote_key_conflict')
        if expected!={a['identity'] for a in ex['required_artifacts']}:errors.append('execution:manifest_artifact_set_conflict')
        return request,errors
    except (KeyError,TypeError,OSError,ValueError):
        return {},errors+['execution:authority_missing_or_different']


def criteria_binding(records, request):
    try:
        spec=request['case_spec']; q=spec['quality'];n=records['numerics'];c=n['convergence'];s=n['sensitivity'];m=n['mesh_quality']
        if m['gates']!={k:q[v] for k,v in [('cell_quality','mesh_min_cell_quality'),('face_validity','mesh_min_face_validity'),('volume_change','mesh_min_volume_change'),('prism_layers','mesh_min_wall_prism_layers')]}:return False
        gates={k:v for k,v in q['residual_gates'].items() if not k.startswith('_')}
        if {k:v['gate'] for k,v in c['residual_gates'].items()}!=gates:return False
        if c['mass_imbalance_gate']!=q['mass_imbalance_ratio_limit'] or c['target_window_gate']!=q['force_window_rel_change']:return False
        if s['mesh_levels']['required']!=q['mesh_levels'] or s['domain_levels']['required']!=3 or q['domain_sensitivity'] is not True:return False
        if s['time_step']['required'] is not q['time_step_sensitivity']:return False
        if s['mesh_levels']['limit']!=q['sensitivity_rel_change'] or s['domain_levels']['limit']!=q['sensitivity_rel_change']:return False
        if s['time_step']['required'] and s['time_step']['limit']!=q['sensitivity_rel_change']:return False
        prov=records['provenance']
        original=prov.get('input_record')
        if original:
            if sha256(original['path'])!=original['sha256'] or original['sha256']!=request['input_sha256']:return False
        else:
            first=records['execution']['stages'][0]
            key=request['steps'][0]['key'];first=next(x for x in records['execution']['stages'] if x['key']==key)
            source=checked_file(first['request'])
            if (source.get('input_sha256') or source.get('source_sha256'))!=request['input_sha256']:return False
        specfile=checked_file(prov['case_spec_record'])
        if not _same(specfile,spec) or prov['case_spec_sha256']!=prov['case_spec_record']['sha256']:return False
        macros={k:v for step in request['steps'] for k,v in step['macro_hashes'].items()}
        return prov['input_sha256']==request['input_sha256'] and prov['runtime_digest']==request['runtime_digest'] and prov['macro_sha256']==macros and prov['release_id']==request.get('release_id')
    except (KeyError,TypeError,OSError,ValueError):return False


def reference_proof(records, ref, request):
    try:
        approval=checked_file(ref['review']);source=ref['source_artifact']
        if sha256(source['path'])!=source['sha256']:return False
        return (approval.get('independent') is True and approval.get('approved') is True
            and approval['reference_sha256']==source['sha256']
            and approval['case_spec_sha256']==records['provenance']['case_spec_sha256']
            and all(_same(approval[k],ref[k]) for k in ('source','value','tolerance','scope'))
            and ref['scope']==records['engineering']['scope_statement']
            and records['engineering'].get('measured_value')==ref['measured_value']
            and measured_target(records)==ref['measured_value']
            and all(approval[k]==records['engineering']['target_source'][k] for k in ('quantity','units','statistic')))
    except (KeyError,TypeError,OSError,ValueError):return False


def remote_snapshot(backend, run_id, *, include_sim=False):
    """Read immutable historical bytes/hashes without changing any run or launching STAR."""
    from backend import valid_id, ps_quote
    from result_storage_policy import require_results_capacity
    require_results_capacity(backend.config['local_results'], additional_roots=[ROOT/'work/quality-contract/readbacks'])
    run_id=valid_id(run_id);run=backend.remote_root+'\\runs\\'+run_id
    script=r'''
$run=RUN_PATH
$request=Get-Content -LiteralPath (Join-Path $run 'request.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$files=@(Get-ChildItem -LiteralPath $run -Recurse -File | Where-Object {$_.Name -ne 'input.sim' -and $_.Extension -eq '.json' -and $_.Length -lt 2000000} | ForEach-Object {
 [pscustomobject]@{path=$_.FullName.Substring($run.Length+1).Replace([char]92,[char]47);data=[Convert]::ToBase64String([IO.File]::ReadAllBytes($_.FullName))}
})
$hashes=@(Get-ChildItem -LiteralPath $run -Recurse -File | Where-Object {$_.Name -ne 'input.sim' -and $_.Extension -notin '.log','.json'} | ForEach-Object {
 [pscustomobject]@{path=$_.FullName.Substring($run.Length+1).Replace([char]92,[char]47);sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
})
$runtime=Join-Path ROOT_PATH ('runtime\'+$request.runtime_digest.Substring(0,16))
$manifests=@(@{name='runtime-manifest.json';file=(Join-Path $runtime 'runtime-manifest.json')})
foreach($wf in @($request.steps.workflow_id | Select-Object -Unique)){$manifests+=@{name=($wf+'.json');file=(Join-Path $runtime ('workflows\'+$wf+'\manifest.json'))}}
foreach($m in $manifests){$files+= [pscustomobject]@{path=('_runtime_manifests/'+$m.name);data=[Convert]::ToBase64String([IO.File]::ReadAllBytes($m.file))}}
[pscustomobject]@{files=$files;hashes=$hashes;context=@{run_id=$request.run_id;runtime_digest=$request.runtime_digest;request_sha256=(Get-FileHash -LiteralPath (Join-Path $run 'request.json') -Algorithm SHA256).Hash.ToLowerInvariant()}} | ConvertTo-Json -Depth 8
'''.replace('RUN_PATH',ps_quote(run)).replace('ROOT_PATH',ps_quote(backend.remote_root))
    packet=backend.ps(script,timeout=60)
    digest=hashlib.sha256(json.dumps(packet,sort_keys=True).encode()).hexdigest()
    dest=ROOT/'work/quality-contract/readbacks'/run_id/digest
    for item in packet['files']:
        relative=Path(item['path'])
        if relative.is_absolute() or '..' in relative.parts:raise QualityContractError('Unsafe snapshot path')
        path=dest/relative;data=base64.b64decode(item['data']);path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists() and path.read_bytes()!=data:raise QualityContractError('Immutable snapshot differs')
        if not path.exists():path.write_bytes(data)
    hashes={i['path']:i['sha256'] for i in packet['hashes']}
    hashes['_context']=packet['context']
    path=dest/'_remote_artifacts.json';data=json.dumps(hashes,sort_keys=True,indent=2).encode()
    if path.exists() and path.read_bytes()!=data:raise QualityContractError('Immutable hash readback differs')
    if not path.exists():path.write_bytes(data)
    if include_sim:
        for rel,h in hashes.items():
            if rel=='_context':continue
            path=dest/rel;path.parent.mkdir(parents=True,exist_ok=True)
            if not path.exists():backend.scp(path,run+'\\'+rel.replace('/','\\'),download=True)
            if sha256(path)!=h:raise QualityContractError('Downloaded checkpoint SHA differs')
    return dest


def attach_run_quality(result, backend, run_id, *, directory=None):
    directory=directory or remote_snapshot(backend,run_id)
    block=render_quality_block(records_from_directory(directory,run_id=run_id))
    out=dict(result,**presentation(block),quality=block)
    # Additive and only when a waiver names this run: a waiver never edits quality_state and
    # absence must leave every existing output byte-identical.
    waivers=waivers_for_run(run_id)
    if waivers:
        block['waivers']=waivers
        out['waivers']=waivers
        out['waived_gates']=sorted({w['summary']['metric'] for w in waivers if w['summary']['usable']})
    return out


def legacy_label_observations(records: dict, index: dict) -> dict:
    """Quality labels consume citable measurements, never process/PAT success."""
    def measurement(key, names):
        observed = []
        for name in names:
            payload = records.get(name) or {}
            if index.get(name, {}).get('citation_state') == CITABLE and isinstance(payload.get(key), bool):
                observed.append({'record': name, 'sha256': index[name]['sha256'],
                                 'key': key, 'value': payload[key]})
        status = ('FAIL' if any(x['value'] is False for x in observed) else
                  'PASS' if observed else 'NOT_ESTABLISHED')
        return {'status': status, 'evidence': observed,
                'reason': '实测判据未满足' if status == 'FAIL' else
                          '没有可引用的显式判定' if not observed else '可引用记录中的显式判定'}

    numeric = {
        'convergence': measurement('residuals_met', ['external_flow_validation.json', 'external_flow_solve.json']),
        'conservation': measurement('mass_balanced', ['external_flow_validation.json']),
        'target_stability': measurement('target_quantities_steady', ['external_flow_validation.json', 'external_flow_solve.json']),
        'GCI': measurement('gci_passed', list(records)),
        'time_step_sensitivity': measurement('time_step_sensitivity_passed', list(records)),
    }
    # MRF's explicit negative is useful even when its postprocessed gate has no stage certificate.
    if numeric['convergence']['status'] == 'NOT_ESTABLISHED':
        numeric['convergence'] = measurement('convergence_verified', ['air3b_segment_verdict.json'])
    if numeric['conservation']['status'] == 'NOT_ESTABLISHED':
        numeric['conservation'] = measurement('mass_imbalance_within_limit', ['air3b_massbalance.json'])
    states = [value['status'] for value in numeric.values()]
    numerical_status = ('FAIL' if 'FAIL' in states else
                        'NOT_ESTABLISHED')
    valid = records.get('external_flow_validation.json') or {}
    physical = {'status': 'NOT_APPLICABLE', 'reference_source': None, 'reference_value': None,
                'tolerance': None, 'relative_error': None, 'evidence': [],
                'reason': '本次证据没有参考值；不作实验或基准一致性主张'}
    if valid.get('reference_status') == 'ESTABLISHED' or valid.get('reference_value') is not None:
        physical.update(status='NOT_ESTABLISHED', reason='参考比较的可引用性、来源、误差或容差尚未完整建立')
        if index.get('external_flow_validation.json', {}).get('citation_state') == CITABLE:
            physical.update(reference_source=valid.get('reference_policy'),
                            reference_value=valid.get('reference_value'),
                            tolerance=valid.get('reference_acceptance_tolerance'),
                            relative_error=valid.get('relative_error_against_reference'),
                            evidence=[{'record': 'external_flow_validation.json',
                                       'sha256': index['external_flow_validation.json']['sha256']}])
            def finite(value):
                return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            source = str(physical['reference_source'] or '')
            source_established = bool(source) and not any(placeholder in source.lower() for placeholder in
                ('source not named', 'not_established', 'no verified', 'placeholder', 'no reference'))
            tol, error = physical['tolerance'], physical['relative_error']
            if (source_established and finite(physical['reference_value']) and
                    finite(tol) and tol > 0 and finite(error) and error >= 0):
                physical['status'] = 'NOT_ESTABLISHED' if error <= tol else 'FAIL'
                physical['reason'] = '参考误差在容差内' if error <= tol else '参考误差超出容差'
    return {'E-num': {'status': numerical_status, 'checks': numeric}, 'E-phys': physical}



def runner_measurement(records, ref):
    """A measurement must be a required artifact hashed by THIS executed stage."""
    try:
        _,errors=execution_authority(records['execution'])
        if errors:return False
        return any(a.get('path') and Path(a['path']).resolve()==Path(ref['path']).resolve()
                   and a['sha256']==ref['sha256'] and artifact_proof(a)
                   for a in records['execution']['required_artifacts'])
    except (KeyError,TypeError,OSError,ValueError):return False


def measured_target(records):
    try:
        target=records['engineering']['target_source'];payload=checked_file(target)
        if not runner_measurement(records,target) or citation_status(target['path'])['state']!=CITABLE:return None
        if any(payload.get('target_'+k)!=target[k] for k in ('quantity','units','statistic')):return None
        value=pointer(payload,target['pointer'])
        return value if finite(value) else None
    except (KeyError,TypeError,OSError,ValueError):return None


def _level_measurements(packet, base):
    try:
        authority,errors=execution_authority(packet['execution'])
        if errors or packet['execution']['state']!='EXECUTION_COMPLETED' or any(not artifact_proof(a) for a in packet['execution']['required_artifacts']):return None
        evidence=_evidence(packet)
        if not all(_bound(packet,evidence,g) for g in ('numerics/mesh_quality','numerics/convergence')):return None
        n=packet['numerics'];m=n['mesh_quality'];c=n['convergence'];baseline=base['numerics']['convergence'];g=m['gates']
        if type(m['cells']) is not int or m['cells']<=0:return None
        if m['gates']!=base['numerics']['mesh_quality']['gates']:return None
        if any(not finite(m.get(k)) or not finite(g[v]) or m[k]<g[v] for k,v in [('min_cell_quality','cell_quality'),('min_face_validity','face_validity'),('min_volume_change','volume_change'),('min_wall_prism_layers','prism_layers')]):return None
        if c['residuals_met'] is not True or c['target_quantities_steady'] is not True:return None
        if {k:v['gate'] for k,v in c['residual_gates'].items()}!={k:v['gate'] for k,v in baseline['residual_gates'].items()}:return None
        if any(not finite(v.get('final')) or not finite(v.get('gate')) or not 0<=v['final']<=v['gate'] or v.get('met') is not True for v in c['residual_gates'].values()):return None
        for value,gate in [('mass_imbalance_ratio','mass_imbalance_gate'),('target_window_relative_change','target_window_gate')]:
            if c[gate]!=baseline[gate] or not finite(c[value]) or not 0<=c[value]<c[gate]:return None
        target=measured_target(packet)
        if target is None or target==0:return None
        return authority,target,m['cells']
    except (KeyError,TypeError,OSError,ValueError):return None


def sensitivity_proof(records):
    """Recompute from qualified, distinct executions, parameter and target source bytes."""
    try:
        from grid_convergence import three_grid_gci
        sensitivity=records['numerics']['sensitivity'];base_request=checked_file(records['execution']['request'])
        for kind in ('mesh_levels','domain_levels','time_step'):
            level=sensitivity[kind]
            if kind=='time_step' and level.get('required') is False:continue
            refs=level['records']
            if not isinstance(refs,list) or len(refs)!=level['executed'] or len({x['path'] for x in refs})!=len(refs):return False
            rows=[];run_ids=set()
            for ref in refs:
                bundle=checked_file(ref)
                if citation_status(ref['path'])['state']!=CITABLE:return False
                packet=bundle['quality_inputs'];measured=_level_measurements(packet,records)
                if measured is None:return False
                authority,target,cells=measured
                if authority['run_id'] in run_ids:return False
                run_ids.add(authority['run_id'])
                spec=copy.deepcopy(authority['case_spec']);baseline=copy.deepcopy(base_request['case_spec'])
                if kind=='mesh_levels':
                    parameter=cells
                    for d in (spec,baseline):
                        for key in ('mesh_base_size_m','mesh_body_target_size_m','mesh_body_minimum_size_m'):d.pop(key,None)
                elif kind=='domain_levels':
                    bounds=spec['domain']['outer_bounds_m']
                    if not isinstance(bounds,list) or len(bounds)!=6 or not all(finite(x) for x in bounds) or any(bounds[i+3]<=bounds[i] for i in range(3)):return False
                    parameter=math.prod(bounds[i+3]-bounds[i] for i in range(3))
                    for d in (spec,baseline):d['domain'].pop('outer_bounds_m',None)
                else:
                    parameter=spec['transient']['time_step_s']
                    for d in (spec,baseline):d['transient'].pop('time_step_s',None)
                if not _same(spec,baseline) or not finite(parameter) or parameter<=0 or bundle['parameter']!=parameter:return False
                if not math.isclose(bundle['target_mean'],target,rel_tol=1e-12):return False
                if kind=='mesh_levels' and bundle.get('cells')!=cells:return False
                rows.append((parameter,target))
            rows.sort(reverse=kind=='time_step');params=[r[0] for r in rows];values=[r[1] for r in rows]
            if len(set(params))!=len(params):return False
            if kind=='mesh_levels':
                if len(rows)!=3:return False
                # C1: each adjacent ratio is measured on its own and admitted inside the
                # registered window. Both pairs are then computed; averaging is forbidden
                # and the two results are never collapsed into one number.
                ratios=[(params[i+1]/params[i])**(1/3) for i in range(2)]
                cells=[params[i+1]/params[i] for i in range(2)]
                if any(r<=1 or not (MESH_RATIO_GATES['h_min']<=r<=MESH_RATIO_GATES['h_max'])
                       for r in ratios):return False
                if any(c<=1 or c>MESH_RATIO_GATES['cell_max'] for c in cells):return False
                for r in ratios:
                    gci=three_grid_gci(*values,r,safety_factor=1.25,limit_pct=100*level['limit'])
                    if not gci['valid'] or not gci['converged'] or not math.isclose(gci['gci_fine_pct']/100,level['gci_32'],rel_tol=1e-6):return False
            else:
                # Contract: every pair, normalized by max absolute mean in that pair.
                change=max(abs(a-b)/max(abs(a),abs(b)) for i,a in enumerate(values) for b in values[i+1:])
                if not change<level['limit'] or not math.isclose(change,level['relative_change'],rel_tol=1e-6,abs_tol=1e-12):return False
        return True
    except (KeyError,TypeError,OSError,ValueError,ZeroDivisionError):return False


def report_labels(block):
    """Projection only: compatibility report columns use the computed contract, never old PASS flags."""
    ref=block['engineering']['reference']
    return {'E-num':{'status':block['engineering']['e_num'],'checks':{
        key:{'status':'PASS' if value else 'NOT_ESTABLISHED','reason':'quality_contract computed measurement check'}
        for key,value in block['checks'].items()}},
        'E-phys':{'status':block['engineering']['e_phys'],'reference_source':ref.get('source'),
            'reference_value':ref.get('value'),'tolerance':ref.get('tolerance'),'relative_error':ref.get('relative_error'),
            'evidence':block['evidence'],'reason':'quality_contract computed reference check'}}


def audit_historical_scenario(scenario, progress_path=ROOT/'progress.json'):
    """Additive readback audit. Observing now never supplies historical execution identity."""
    entry=read(progress_path)['scenarios'][scenario]
    records=[];directories=set()
    for stage,item in entry.get('stages',{}).items():
        if not item.get('record'):continue
        path=ROOT/item['record'];directories.add(path.parent)
        records.append(dict(stage=stage,record=file_ref(path),citation_status=citation_status(path)))
    missing={};sources=[];inventories={}
    for directory in sorted(directories):
        inv=legacy_inventory(directory);inventories[str(directory.relative_to(ROOT))]=inv
        sources.extend(inv['sidecars'].values())
        request=read(directory/'request.json') if (directory/'request.json').is_file() else {}
        gaps=[key for key in ('release_id','runtime_digest','case_spec','input_sha256','macro_hashes') if not request.get(key)]
        if not request.get('steps'):gaps.append('ordered_execution_graph')
        if not inv['delivery_record_present'] and not (directory/'_remote_artifacts.json').is_file() and not (directory/'TRANSFER-VERIFY.json').is_file():
            gaps.append('bound_delivery_record')
        missing[str(directory.relative_to(ROOT))]=gaps
        # Snapshot every identity-bearing sidecar that actually exists; never invent one.
        for pattern in ('request.json','case_spec.json','case-probe.json','stage-*.json','*config.json','*delivery*.json'):
            sources.extend(file_ref(p) for p in sorted(directory.glob(pattern)))
    rows=support_matrix(progress_path);row=next(x for x in rows if x['scenario']==scenario)
    return {'schema_version':1,'scenario':scenario,'quality_state':row['quality_state'],
            'Q0_REASON':row['Q0_REASON'],'missing_bindings':missing,'legacy_inventories':inventories,'records':records,'sources':sources,
            'binding_decision':'CURRENT_OBSERVER_IS_NOT_HISTORICAL_EXECUTOR',
            'upgrade_allowed':row['quality_state']!=STATES[0]}
