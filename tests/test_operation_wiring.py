# -*- coding: utf-8 -*-
"""②R11 regression tests: the operation wiring, availability by CALL PATH, and the four levels.

Why these exist (AGENTS.md section 2-O9: a review finding, a handover or a measured defect becomes an
executable test in the round that found it). Branch 1's R11 handover
(test-evidence/static-external-flow/round11-operation-integration.json) named the exact defect this module
pins: "casespec.py treats an operation as available purely by catalog id and would then report
validate_plan executable=true for a pipeline that is only reachable through the restricted-trial runner,
which is a false-green this branch must not create." So the tests are:

  1. availability is resolved from real call paths, and a catalog entry whose stage or macro does not
     exist is UNRESOLVED and can never make a plan executable;
  2. an AIR3B fixed-name capability cannot satisfy a generic plan requirement;
  3. evidence from a FAILED run never verifies the current case, and engineering is never claimed by
     wiring an operation;
  4. the reserved interfaces (mesh_study / domain_study) are reported with their contract and are NOT
     available, while time_step_study has no entry at all;
  5. stage parameters are refused unless the stage declares them and the name is not process-critical,
     on both the macOS side and the Windows side;
  6. the formal MCP path refuses unknown operations, operations owned by the restricted-trial entry, and
     any run whose CaseSpec does not pass the full plan gate - all WITHOUT touching the host.

Everything is offline: no STAR, no SSH. Where a host call would happen, a Backend whose ps() raises is
used, and the test asserts it was never called.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import backend as backend_module                                            # noqa: E402
import casespec                                                             # noqa: E402
from backend import Backend, validate_stage_parameters                      # noqa: E402
from casespec import (installed_operations, required_operations, resolve_operations,  # noqa: E402
                      validate_case_spec)
from tests.formal_worker_test_helpers import (current_runtime_review_pins,
                                             mock_approved_request_pins)

CATALOG = ROOT / 'operations' / 'catalog.json'


def read(path: Path) -> str:
    return path.read_text(encoding='utf-8', errors='replace')


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def spec(scenario: str = 'external_static') -> dict:
    base = {
        'schema_version': 1, 'scenario': scenario, 'case_ref': 'library/' + 'a' * 64 + '.sim',
        'objective': 'operation wiring test', 'model_length_unit': 'm', 'medium': 'air',
        'geometry': {'body_parts': ['body']},
        'domain': {'outer_bounds_m': [-10, -10, -10, 10, 10, 10]},
        'flow': {'velocity_m_s': [10, 0, 0], 'pressure_pa': 101325, 'temperature_k': 293.15},
    }
    if scenario == 'propeller_mrf':
        base['geometry'] = {'body_parts': ['body'], 'rotor_parts': ['rotor']}
        base['rotation'] = {'axis': [0, 0, 1], 'center_m': [0, 0, 0], 'rpm': 1000}
    return base


class AvailabilityComesFromCallPaths(unittest.TestCase):
    def test_every_declared_operation_resolves(self):
        registry = resolve_operations()
        self.assertEqual([], registry['unresolved'], 'a declared operation does not resolve')
        self.assertGreaterEqual(len(registry['operations']), 15)
        for op_id, entry in registry['operations'].items():
            with self.subTest(operation=op_id):
                self.assertEqual('RESOLVED', entry['implementation'])
                self.assertIsNotNone(entry['call_path'], 'a resolved operation must name its call path')
                self.assertEqual('NOT_CLAIMED_BY_WIRING', entry['engineering'])

    def test_workflow_stage_call_paths_name_a_stage_that_really_exists(self):
        from backend import workflow_catalog
        catalog = workflow_catalog()
        for op_id, entry in resolve_operations()['operations'].items():
            if entry['mechanism'] != 'workflow_stage':
                continue
            path = entry['call_path']
            with self.subTest(operation=op_id):
                stage = catalog[path['workflow_id']]['stages'][path['stage']]
                self.assertEqual(list(stage['macros']), path['macros'])
                for macro in path['macros']:
                    self.assertTrue((ROOT / 'macros' / macro).is_file())

    def test_an_entry_whose_stage_vanishes_is_unresolved_and_cannot_make_a_plan_executable(self):
        # The exact false-green branch 1 refused to create: a catalog id with no reachable stage.
        with tempfile.TemporaryDirectory() as tmp:
            bogus = json.loads(CATALOG.read_text(encoding='utf-8'))
            bogus['operations'] = [
                {'id': 'build_external_domain', 'title': 'bogus', 'mechanism': 'workflow_stage',
                 'granularity': 'stage', 'workflow_id': 'external-domain-2502',
                 'stage': 'a-stage-that-does-not-exist', 'provides': ['build_external_domain'],
                 'scope_class': 'generic', 'general_acceptance': 'GENERAL_OPERATION',
                 'depends_on_fixed_names': False}]
            path = Path(tmp) / 'catalog.json'
            path.write_text(json.dumps(bogus), encoding='utf-8')
            with patch.object(casespec, 'OPERATION_INDEX', path):
                registry = resolve_operations()
                self.assertEqual('UNRESOLVED', registry['operations']['build_external_domain']['implementation'])
                self.assertEqual([{'id': 'build_external_domain',
                                   'reason': "stage 'a-stage-that-does-not-exist' is not in workflow "
                                             "'external-domain-2502'"}], registry['unresolved'])
                result = validate_case_spec(spec())
                self.assertFalse(result.executable,
                                 'an id whose stage does not exist must not make a plan executable')
                self.assertIn('build_external_domain', result.unavailable_operations)

    def test_a_declared_macro_missing_from_disk_is_unresolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            bogus = json.loads(CATALOG.read_text(encoding='utf-8'))
            for entry in bogus['operations']:
                if entry['id'] == 'build_static_body':
                    entry['stage'] = 'build-body'
            path = Path(tmp) / 'catalog.json'
            path.write_text(json.dumps(bogus), encoding='utf-8')
            original = backend_module.workflow_catalog

            def catalog_with_missing_macro():
                data = json.loads(json.dumps(original()))
                data['static-external-flow-2502']['stages']['build-body']['macros'] = ['NotOnDisk.java']
                return data

            with patch.object(casespec, 'OPERATION_INDEX', path), \
                    patch.object(backend_module, 'workflow_catalog', catalog_with_missing_macro):
                registry = resolve_operations(['build_static_body'])
            self.assertEqual('UNRESOLVED', registry['operations']['build_static_body']['implementation'])
            self.assertIn('missing from disk', registry['operations']['build_static_body']['reason'])


class LevelsAreKeptApart(unittest.TestCase):
    def test_engineering_is_never_claimed_by_wiring(self):
        for op_id, entry in resolve_operations()['operations'].items():
            with self.subTest(operation=op_id):
                self.assertEqual('NOT_CLAIMED_BY_WIRING', entry['engineering'])
        result = validate_case_spec(spec())
        for operation, detail in result.operations_detail.items():
            with self.subTest(operation=operation):
                self.assertEqual('NOT_CLAIMED_BY_WIRING', detail['engineering'])

    def test_the_convergence_operation_says_it_is_an_audit_not_a_converged_solve(self):
        entry = resolve_operations(['audit_external_flow_convergence'])['operations'][
            'audit_external_flow_convergence']
        self.assertIn('CHECKS', entry['scope'])
        self.assertIn('does not make a solution converge', entry['scope'])
        self.assertIn('NOT_CLAIMED_BY_WIRING', entry['engineering'])

    def test_implementation_and_verification_are_reported_separately(self):
        # A fresh case has implementations available but no case-bound verification: the two levels must
        # differ, not collapse into one boolean.
        result = validate_case_spec(spec(), case='a-case-with-no-evidence')
        self.assertTrue(result.operations_detail['audit_geometry']['served_by'])
        self.assertFalse(result.operations_detail['audit_geometry']['verified_on_current_case'])
        self.assertIn('no evidence entry names case', 
                      result.operations_detail['audit_geometry']['verified_on_current_case_reason'])

    def test_case_bound_verification_requires_a_citable_record(self):
        # The scenario-1 chain's evidence names static-sphere-external-flow and its stage records are ok.
        result = validate_case_spec(spec(), case='static-sphere-external-flow')
        served = result.operations_detail['mesh']
        self.assertTrue(served['served_by'])
        self.assertTrue(served['verified_on_current_case'],
                        served.get('verified_on_current_case_reason'))

    def test_evidence_from_a_failed_run_cannot_verify_a_case(self):
        # A catalog entry that cites a FAILED/GATE_FAILED record must report verified_on_current_case
        # false, naming the citation state. The record below is the R10 measured-defect run.
        failed = 'test-evidence/air-three-blade/p-e2e/r10-sim-entry-b/air3b-mrf-sim-entry-20261004b-result.json'
        self.assertTrue((ROOT / failed).is_file())
        with tempfile.TemporaryDirectory() as tmp:
            bogus = json.loads(CATALOG.read_text(encoding='utf-8'))
            for entry in bogus['operations']:
                if entry['id'] == 'audit_geometry':
                    entry['evidence'] = [{'case': 'failed-case', 'trial_id': 'x', 'record': failed,
                                          'kind': 'run', 'verdict': 'GATE_FAILED',
                                          'citation': 'checked_by_scripts/run_record_gate.py'}]
            path = Path(tmp) / 'catalog.json'
            path.write_text(json.dumps(bogus), encoding='utf-8')
            with patch.object(casespec, 'OPERATION_INDEX', path):
                entry = resolve_operations(['audit_geometry'], case='failed-case')['operations']['audit_geometry']
        self.assertFalse(entry['verified_on_current_case'])
        self.assertIn('not citable', entry['verified_on_current_case_reason'])


class SampleSpecificCannotMasquerade(unittest.TestCase):
    def test_the_air3b_chains_are_sample_specific_and_provide_nothing_generic(self):
        for op_id in ('air3b_steady_mrf_chain', 'air3b_transient_chain'):
            entry = resolve_operations([op_id])['operations'][op_id]
            with self.subTest(operation=op_id):
                self.assertEqual('sample_specific', entry['scope_class'])
                self.assertTrue(entry['depends_on_fixed_names'])
                self.assertTrue(entry['fixed_names'])
                self.assertEqual([], entry['provides'],
                                 'a fixed-name capability must not satisfy generic requirements')
                self.assertTrue(entry['provides_on_own_case'])
                self.assertEqual('NOT_ACCEPTED', entry['general_acceptance'])
                self.assertEqual('restricted_trial', entry['mechanism'])

    def test_a_propeller_casespec_cannot_be_served_by_the_air3b_chain(self):
        result = validate_case_spec(spec('propeller_mrf'))
        # The AIR-3B chain provides these ON ITS OWN CASE only; the generic requirements stay unserved.
        for operation in ('build_rotating_region', 'configure_rotation'):
            with self.subTest(operation=operation):
                self.assertIn(operation, result.unavailable_operations)
                self.assertIn('no implementation is registered', result.missing_reasons[operation])
        self.assertFalse(result.executable)

    def test_generic_operations_keep_their_generic_scope(self):
        for op_id in ('audit_geometry', 'build_external_domain', 'build_external_flow_region_physics_mesh',
                      'run_external_flow_solve', 'static_external_flow_chain', 'classify_parts'):
            entry = resolve_operations([op_id])['operations'][op_id]
            with self.subTest(operation=op_id):
                self.assertEqual('generic', entry['scope_class'])
                self.assertFalse(entry['depends_on_fixed_names'])


class ReservedInterfacesAndRealGaps(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads(CATALOG.read_text(encoding='utf-8'))

    def test_the_studies_are_registered_as_post_steps_with_a_verified_invocation(self):
        # ②R13: window 1 delivered the study implementations and their run evidence, so these two are no
        # longer reserved interfaces - they are registered operations. What that does NOT mean is also
        # pinned here: trialled acceptance only (not general), no engineering claim, and the invocation is
        # a REAL symbol on disk (the resolver checks it).
        self.assertEqual([], list(self.catalog.get('reserved_interfaces') or []),
                         'nothing is reserved any more: both studies are registered')
        entries = {entry['id']: entry for entry in self.catalog['operations']}
        for op_id in ('mesh_study', 'domain_study'):
            with self.subTest(study=op_id):
                entry = entries[op_id]
                self.assertEqual('offline_tool', entry['mechanism'])
                self.assertEqual('post', entry['plan_role'])
                binding = entry['invoked_by'][0]
                self.assertEqual('scripts/formal_study_reuse.py', binding['file'])
                self.assertEqual('summarize_study', binding['symbol'])
                self.assertTrue((ROOT / binding['file']).is_file())
                self.assertEqual('TRIALED_POSITIVE', entry['general_acceptance'])
                self.assertEqual('NOT_CLAIMED_BY_WIRING', entry['engineering'])
                # the measured records are real files, and the study verdict is carried, not invented
                records = [item['record'] for item in entry['evidence'] if item['kind'] == 'measurement']
                # two MEASURED levels per study x three records = six; the two studies together are the
                # twelve citable measurement records of the four repaired levels
                self.assertEqual(6, len(records))
                for record in records:
                    self.assertTrue((ROOT / record).is_file(), record)
                self.assertIn('gci_fine_fraction', json.dumps(entry['registration_basis'], ensure_ascii=False),
                              'the corrected contract must record the non-existent key it replaced')
                self.assertIn('MIN_LEVELS', json.dumps(entry, ensure_ascii=False))

    def test_a_full_plan_naming_both_studies_is_executable_and_dropping_one_is_not(self):
        # ②R13: with the studies registered AND named as plan members, the full scenario-1 plan passes the
        # gate. Dropping a study member must make it fail again - the coverage rule is what makes the first
        # statement meaningful rather than a union false green.
        audit = TheSelectedPlanDecidesCoverage.AUDIT
        full = ['classify_parts', 'audit_static_body', 'build_external_domain',
                'build_external_flow_region_physics_mesh', 'audit_external_flow_mesh',
                'run_external_flow_solve', 'audit_external_flow_convergence', 'export_evidence', 'report',
                'mesh_study', 'domain_study']
        result = validate_case_spec(spec(), None, audit, plan_operations=full)
        self.assertEqual([], result.missing)
        self.assertEqual([], result.errors)
        self.assertEqual([], result.unavailable_operations)
        self.assertEqual([], result.plan_unresolved_operations)
        self.assertEqual('workflow_stage', result.plan_mechanism)
        self.assertTrue(result.executable, result.plan_blocker)
        without = [item for item in full if item != 'mesh_study']
        short = validate_case_spec(spec(), None, audit, plan_operations=without)
        self.assertFalse(short.executable)
        self.assertIn('mesh_study', short.plan_blocker)

    def test_time_step_study_has_no_entry_and_stays_missing(self):
        ids = {entry['id'] for entry in self.catalog['operations']}
        self.assertNotIn('time_step_study', ids)
        self.assertNotIn('time_step_study',
                         {item['id'] for item in self.catalog['reserved_interfaces']},
                         'no owner handover exists, so it must not even be reserved')
        result = validate_case_spec(spec('rotating_transient'))
        self.assertIn('time_step_study', result.unavailable_operations)
        self.assertIn('no implementation is registered', result.missing_reasons['time_step_study'])

    def test_mechanism_coverage_names_the_real_remaining_gap(self):
        result = validate_case_spec(spec())
        formal = result.mechanism_coverage['workflow_stage']
        trial = result.mechanism_coverage['restricted_trial']
        # R12 changed two of these numbers DELIBERATELY, and the change is the point of the round:
        #  * the formal path now really produces the Chinese report (scripts/formal_run_report.py, reached
        #    through mcp report_run), so `report` is no longer a formal-path gap - the coverage comes from
        #    a code binding whose file AND symbol are checked on disk, not from an `applies_to` string;
        #  * `classify_parts` declared `restricted_trial` while NO restricted-trial entry calls it, so it
        #    is now an honest gap there instead of a free declaration (see the catalog's invocation_note).
        # ②R13: the studies are registered post steps of the FORMAL mechanism, so the formal path now
        # covers every generic requirement - and still has NO single covering operation (a full plan is a
        # graph, so `executable` without an explicit plan stays false). The restricted-trial mechanism
        # still cannot classify parts, and it cannot serve the studies either: they are bound to
        # workflow_stage only.
        self.assertEqual([], formal['missing'])
        self.assertEqual(['classify_parts', 'domain_study', 'mesh_study'], trial['missing'])
        self.assertFalse(formal['executable'])
        self.assertFalse(trial['executable'])
        self.assertIsNone(formal['complete_plan'])
        self.assertIsNone(trial['complete_plan'])

    def test_an_offline_step_counts_only_through_a_verified_invocation(self):
        # `applies_to_run_mechanisms` is a declaration; `invoked_by` names a file and a symbol, and the
        # resolver checks both on disk. Break the binding and the requirement stops being covered.
        with tempfile.TemporaryDirectory() as tmp:
            bogus = json.loads(CATALOG.read_text(encoding='utf-8'))
            for entry in bogus['operations']:
                if entry['id'] == 'report':
                    entry['invoked_by'] = [{'mechanism': 'workflow_stage',
                                            'file': 'scripts/formal_run_report.py',
                                            'symbol': 'a_symbol_that_does_not_exist'}]
            path = Path(tmp) / 'catalog.json'
            path.write_text(json.dumps(bogus), encoding='utf-8')
            with patch.object(casespec, 'OPERATION_INDEX', path):
                registry = resolve_operations(['report'])
                steps = validate_case_spec(spec())
        self.assertEqual([], registry['operations']['report']['invoked_by'],
                         'a binding whose symbol is absent must not be reported as an invocation')
        self.assertIn('coverage_note', registry['operations']['report'])
        self.assertIn('report', steps.mechanism_coverage['workflow_stage']['missing'],
                      'the formal mechanism must lose the capability it never really invoked')


class TheRunGraphCountsTheWholePlanNotOnlyItsStages(unittest.TestCase):
    """②R13 regression for a REAL defect, found by dry-running the submission path.

    `submit_run` built its `provided` set from the staged steps alone, so a plan that is complete by
    construction - the six STAR stages PLUS the offline pre/post members (classify_parts, export_evidence,
    report, mesh_study, domain_study) - was refused with "Run graph omits required CaseSpec operations:
    classify_parts, domain_study, export_evidence, mesh_study, report" immediately after `validate_plan` had
    called that same plan executable. Two gates disagreeing about the same plan is exactly the kind of
    false refusal that would have hidden the ②R13 acceptance run. These tests drive the REAL `submit_run`
    with a real review record and a stubbed host, and assert what the request would contain.
    """

    PLAN = ['classify_parts', 'audit_static_body', 'build_external_domain',
            'build_external_flow_region_physics_mesh', 'audit_external_flow_mesh',
            'run_external_flow_solve', 'audit_external_flow_convergence', 'export_evidence', 'report',
            'mesh_study', 'domain_study']

    def review_record(self, tmp: Path, unresolved: bool = False) -> Path:
        """A record whose every pin matches the live bytes (the shape review/README.md documents)."""
        workflows = {'static-external-flow-2502': ['audit-body', 'region-physics-mesh', 'mesh-quality-audit',
                                                   'solve', 'validation'],
                     'external-domain-2502': ['build']}
        macros = set()
        manifests = {}
        for workflow_id, stages in workflows.items():
            manifest = json.loads((ROOT / 'workflows' / workflow_id / 'manifest.json')
                                  .read_text(encoding='utf-8'))
            manifests[workflow_id] = hashlib.sha256(
                (ROOT / 'workflows' / workflow_id / 'manifest.json').read_bytes()).hexdigest()
            for stage in stages:
                macros.update(manifest['stages'][stage]['macros'])
        record = {'schema_version': 1, 'approved': True, 'independent': True,
                  'reviewer': 'wiring test', 'reviewed_at': '2026-10-04',
                  'pipeline_hashes': manifests,
                  'controller_sha256': hashlib.sha256((ROOT / 'scripts' / 'backend.py').read_bytes()).hexdigest(),
                  'runner_sha256': hashlib.sha256((ROOT / 'worker' / 'run_job.ps1').read_bytes()).hexdigest(),
                  'pipeline_runner_sha256': hashlib.sha256(
                      (ROOT / 'worker' / 'run_pipeline.ps1').read_bytes()).hexdigest(),
                  'macro_hashes': {name: hashlib.sha256((ROOT / 'macros' / name).read_bytes()).hexdigest()
                                   for name in sorted(macros)},
                  'input_hashes': {'input.sim': 'a' * 64},
                  # 资源合同也要按当前字节固定（check_resource_review 要求）；
                  # 2026-10-14 由 HEAD 回滚误删，此处按当时的语义重建。
                  'resource_contract_sha256': hashlib.sha256(
                      (ROOT / 'scripts' / 'resource_contract.py').read_bytes()).hexdigest()}
        record.update(current_runtime_review_pins())
        path = Path(tmp) / 'review.json'
        path.write_text(json.dumps(record), encoding='utf-8')
        return path

    def backend(self, payloads: list, unresolved: list | None = None):
        assessment = casespec.validate_case_spec(spec(), None, TheSelectedPlanDecidesCoverage.AUDIT,
                                                 plan_operations=self.PLAN)
        self.assertTrue(assessment.executable, assessment.plan_blocker)
        b = Backend.__new__(Backend)                      # no __init__: no connection.json is read
        b.remote_root = 'C:\\nowhere'
        b.config = {'star_launcher': 'x'}
        b.validate_plan = lambda *a, **k: {'valid': True, 'executable': True, 'plan_blocker': None,
                                           'required_operations': list(assessment.required_operations),
                                           'plan_unresolved_operations': list(unresolved or [])}
        b.classify_case_parts = lambda *a, **k: {'roles': {'body': 'FLUID'}, 'source_sha256': 'a' * 64,
                                                 'errors': []}

        def ps(script, *a, **k):
            if 'FromBase64String' in script:
                payloads.append(script)
                return {'run_id': 'wiring-plan-0001', 'state': 'PENDING'}
            return {'status': {'state': 'PENDING'}, 'artifacts': [], 'scheduler_state': 'Running'}
        b.ps = ps
        return b

    def test_the_complete_plan_is_accepted_and_the_request_names_every_member(self):
        payloads: list = []
        # Only the installation result is mocked; review byte comparisons remain real.
        with tempfile.TemporaryDirectory() as tmp, patch(
                'formal_worker.request_pins', side_effect=mock_approved_request_pins):
            plan_spec = spec()
            plan_spec['case_ref'] = 'library/' + 'a' * 64 + '.sim'
            result = self.backend(payloads).submit_run(
                plan_spec['case_ref'], 'wiring-plan-0001', operations=self.PLAN, case_spec=plan_spec,
                inspect_job_id='wiring-preflight', review_ref=str(self.review_record(Path(tmp))))
        self.assertEqual('PENDING', result['state'])
        self.assertEqual(1, len(payloads), 'the submission is written exactly once')
        import base64
        import re as _re
        body = json.loads(base64.b64decode(_re.search(r"FromBase64String\('([^']+)'\)",
                                                      payloads[0]).group(1)).decode('utf-8'))
        self.assertEqual(self.PLAN, body['plan_operations'])
        # a pre step is recorded with the digest of what it really returned, so the record proves it ran
        self.assertEqual(['classify_parts'], sorted(body['pre_operations']))
        self.assertEqual('a' * 64, body['pre_operations']['classify_parts']['source_sha256'])
        self.assertEqual({'export_evidence', 'report', 'mesh_study', 'domain_study'},
                         set(body['post_operations']))
        self.assertEqual(['audit_static_body', 'build_external_domain',
                          'build_external_flow_region_physics_mesh', 'audit_external_flow_mesh',
                          'run_external_flow_solve', 'audit_external_flow_convergence'],
                         [step['key'] for step in body['steps']])
        self.assertEqual(hashlib.sha256((ROOT / 'worker' / 'run_job.ps1').read_bytes()).hexdigest(),
                         body['runner_sha256'])
        self.assertEqual(hashlib.sha256((ROOT / 'worker' / 'run_pipeline.ps1').read_bytes()).hexdigest(),
                         body['pipeline_runner_sha256'])

    def test_a_member_the_gate_could_not_resolve_is_still_refused(self):
        # The other direction of the same line: counting the whole plan must not become "anything the
        # caller lists counts". A member the gate reports as unresolved is still omitted.
        with tempfile.TemporaryDirectory() as tmp:
            plan_spec = spec()
            plan_spec['case_ref'] = 'library/' + 'a' * 64 + '.sim'
            with self.assertRaisesRegex(ValueError, 'Run graph omits required CaseSpec operations'):
                self.backend([], unresolved=['mesh_study']).submit_run(
                    plan_spec['case_ref'], 'wiring-plan-0002', operations=self.PLAN, case_spec=plan_spec,
                    inspect_job_id='wiring-preflight', review_ref=str(self.review_record(Path(tmp))))

class ParametersCannotReconfigureTheWorker(unittest.TestCase):
    def test_undeclared_and_process_critical_names_are_refused(self):
        declared = backend_module.stage_parameter_declarations('static-external-flow-2502', 'solve')
        # 2026-10-14 撤销时间预算后，求解阶段不再声明 EXTERNAL_FLOW_BUDGET_SECONDS；改用仍然声明的迭代上限
        # 作为"已声明参数"的正例，测试目的不变：已声明可通过，进程关键/未声明/名字错/类型错一律拒绝。
        self.assertIn('EXTERNAL_FLOW_MAX_ITERATIONS', declared)
        self.assertNotIn('EXTERNAL_FLOW_BUDGET_SECONDS', declared)
        self.assertEqual({'EXTERNAL_FLOW_MAX_ITERATIONS': '600'},
                         validate_stage_parameters({'EXTERNAL_FLOW_MAX_ITERATIONS': 600}, declared))
        for bad, why in (({'PATH': '/tmp'}, 'critical'),
                         ({'LD_PRELOAD': 'x'}, 'undeclared'),
                         ({'external_flow_max_iterations': '1'}, 'name'),
                         ({'EXTERNAL_FLOW_MAX_ITERATIONS': {'a': 1}}, 'value'),
                         ({'EXTERNAL_FLOW_BUDGET_SECONDS': '480'}, 'undeclared-after-revocation')):
            with self.subTest(parameters=bad):
                with self.assertRaises(ValueError):
                    validate_stage_parameters(bad, declared)
        with self.assertRaises(ValueError):
            validate_stage_parameters({'EXTERNAL_FLOW_MAX_ITERATIONS': '600'},
                                     backend_module.stage_parameter_declarations('core', 'preflight')
                                     if backend_module.stage_parameter_declarations('core', 'preflight')
                                     else {})

    def test_the_worker_applies_the_same_rules_and_scopes_them(self):
        runner = read(ROOT / 'worker' / 'run_job.ps1')
        self.assertIn('$request.env', runner)
        self.assertIn("'^[A-Z][A-Z0-9_]{0,63}$'", runner)
        self.assertIn('Refusing a process-critical stage parameter', runner)
        self.assertIn('Stage does not declare the parameter', runner)
        # Successful and failed stages both restore parent values; absent originals are removed.
        self.assertIn("$previousEnv[$name]=[Environment]::GetEnvironmentVariable($name,'Process')", runner)
        restoration = runner[runner.rindex('}finally {'):]
        self.assertIn("[Environment]::SetEnvironmentVariable($name,$previousEnv[$name],'Process')", restoration)
        pipeline = read(ROOT / 'worker' / 'run_pipeline.ps1')
        self.assertIn('env=$item.env', pipeline)

    def test_only_the_declared_stage_carries_parameters(self):
        catalog = json.loads((ROOT / 'workflows' / 'static-external-flow-2502' /
                              'manifest.json').read_text(encoding='utf-8'))
        solve = catalog['stages']['solve']['parameters']['env']
        names = {item['name'] for item in solve}
        # 2026-10-14 用户撤销全部时间预算；本轮执行层落地。求解阶段不再声明时间预算键。
        self.assertEqual({'EXTERNAL_FLOW_CHUNK', 'EXTERNAL_FLOW_MAX_ITERATIONS'}, names)
        macro = read(ROOT / 'macros' / 'RunExternalFlowSolve.java')
        for name in names:
            self.assertIn(name, macro)
        # 宏缺省必须是 0（不以墙钟停止），且按墙钟停只在显式设了正预算时才可能发生
        self.assertIn('EXTERNAL_FLOW_BUDGET_SECONDS", 0.0', macro)
        self.assertIn('budgetSeconds > 0 && budgetClock >= budgetSeconds', macro)
        # 任何阶段的 env 都不得再声明时间预算键（撤销后的政策判据）
        for stage, entry in catalog['stages'].items():
            for item in (entry.get('parameters') or {}).get('env') or []:
                self.assertNotIn('BUDGET', item['name'].upper(),
                                 '%s 仍声明时间预算参数 %s' % (stage, item['name']))
        for stage, entry in catalog['stages'].items():
            env = (entry.get('parameters') or {}).get('env') or []
            if stage == 'solve-transient':
                self.assertEqual({item['name'] for item in env}, {
                    'EXTERNAL_FLOW_DT_S', 'EXTERNAL_FLOW_STEPS',
                    'EXTERNAL_FLOW_INNER_ITER', 'EXTERNAL_FLOW_CHECKPOINT_EVERY'})
                transient = read(ROOT / 'macros' / 'RunExternalFlowTransient.java')
                for item in env:
                    self.assertIn(item['name'], transient)
            elif stage != 'solve':
                self.assertEqual([], env, '%s declares parameters it does not need' % stage)


class FormalPathRefusesWhatItCannotRun(unittest.TestCase):
    def backend(self):
        b = Backend.__new__(Backend)                 # no __init__: no connection.json is read
        b.ps = lambda *a, **k: (_ for _ in ()).throw(AssertionError('the host must not be touched'))
        b.remote_root = 'C:\\nowhere'
        b.config = {'star_launcher': 'x'}
        return b

    def test_unknown_operation_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'Unknown operation'):
            self.backend().submit_run('library/' + 'a' * 64 + '.sim', 'wiring-test-0001',
                                      operations=['not_an_operation'])

    def test_an_operation_owned_by_the_trial_entry_is_refused_with_its_command(self):
        with self.assertRaisesRegex(ValueError, 'restricted_trial mechanism'):
            self.backend().submit_run('library/' + 'a' * 64 + '.sim', 'wiring-test-0002',
                                      operations=['air3b_steady_mrf_chain'])

    def test_both_steps_and_operations_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'either steps or operations'):
            self.backend().submit_run('library/' + 'a' * 64 + '.sim', 'wiring-test-0003',
                                      steps=[{'key': 'a', 'workflow_id': 'core', 'stage': 'preflight'}],
                                      operations=['audit_geometry'])

    def test_parameters_for_an_unrequested_operation_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'parameters names an operation'):
            self.backend().submit_run('library/' + 'a' * 64 + '.sim', 'wiring-test-0004',
                                      operations=['audit_geometry'],
                                      parameters={'build_external_domain': {'X': '1'}})

    def test_a_casespec_that_fails_the_plan_gate_is_refused_before_anything_is_uploaded(self):
        # The full plan gate is NOT bypassed by using operations: with a CaseSpec supplied, an
        # incomplete/une executable plan stops the run. The review record is real and valid so that the
        # refusal provably comes from the PLAN gate, not from the review gate.
        with tempfile.TemporaryDirectory() as tmp:
            review = Path(tmp) / 'review.json'
            review.write_text(json.dumps({
                'schema_version': 1, 'approved': True, 'independent': True, 'reviewer': 'wiring test',
                'reviewed_at': '2026-10-04', 'controller_sha256': sha256(ROOT / 'scripts' / 'backend.py'),
                # R12: submit_run runs TWO runner layers, so the record must pin each layer's own hash.
                'runner_sha256': sha256(ROOT / 'worker' / 'run_job.ps1'),
                'pipeline_runner_sha256': sha256(ROOT / 'worker' / 'run_pipeline.ps1'),
                'pipeline_sha256': sha256(ROOT / 'workflows' / 'static-external-flow-2502' / 'manifest.json'),
                'macro_hashes': {name: sha256(ROOT / 'macros' / name)
                                 for name in json.loads((ROOT / 'workflows' / 'static-external-flow-2502' /
                                                         'manifest.json').read_text(encoding='utf-8')
                                                        )['stages']['audit-body']['macros']},
                'input_hashes': {'input.sim': 'a' * 64},
                'resource_contract_sha256': sha256(ROOT / 'scripts' / 'resource_contract.py'),
                **current_runtime_review_pins()}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'SELECTED plan is not executable'):
                self.backend().submit_run('library/' + 'a' * 64 + '.sim', 'wiring-test-0005',
                                          operations=['audit_static_body'],
                                          case_spec=spec(), review_ref=str(review))

    def test_the_studies_are_no_longer_a_gap_but_a_plan_that_omits_them_still_fails(self):
        # ②R13: registering the studies removed the last generic gap, so `unavailable_operations` is now
        # empty - and the gate still does not become executable by itself (no explicit plan, no audit). A
        # plan that omits a registered study is refused, which is what keeps the coverage rule honest.
        result = validate_case_spec(spec())
        self.assertFalse(result.executable)
        self.assertEqual([], sorted(result.unavailable_operations))
        self.assertEqual([], result.unresolved_operations)
        audit = TheSelectedPlanDecidesCoverage.AUDIT
        partial = ['classify_parts', 'audit_static_body', 'build_external_domain',
                   'build_external_flow_region_physics_mesh', 'audit_external_flow_mesh',
                   'run_external_flow_solve', 'audit_external_flow_convergence', 'export_evidence',
                   'report', 'mesh_study']
        short = validate_case_spec(spec(), None, audit, plan_operations=partial)
        self.assertFalse(short.executable)
        self.assertIn('domain_study', short.unavailable_operations)


class TheSelectedPlanDecidesCoverage(unittest.TestCase):
    """R12: `executable` follows the plan that would be SUBMITTED, never the union of capabilities.

    Every case below is a shape that used to be able to look green (or that a weaker rule would let
    through): a capability the plan does not contain must not be credited to it, and two mechanisms'
    capabilities must never add up to an executable path.

    The independent review of R12 found the first version of these tests VACUOUS: they passed no audit, so
    `executable` was false for an unrelated reason and a mutation that dropped the mechanism-coverage
    requirement survived them. Every assertion here therefore uses a trusted audit whose parts MATCH the
    CaseSpec, so a false green has to come from the coverage rule itself.
    """

    AUDIT = {'status': 'ok',
             'geometryParts': [{'qualifiedName': 'body', 'name': 'body', 'isClosed': True,
                                'isManifold': True,
                                'boundingBoxRaw': [-0.1, -0.1, -0.1, 0.1, 0.1, 0.1]}],
             'regions': []}

    def plan(self, *operation_ids: str) -> 'casespec.Validation':
        return validate_case_spec(spec(), None, self.AUDIT, plan_operations=list(operation_ids))

    def test_the_audit_alone_does_not_make_this_fixture_fail(self):
        # Guard against the vacuity the review found: with the audit and a plan that really covers
        # everything the operations can cover, the ONLY thing standing in the way must be the studies.
        plan = ['classify_parts', 'audit_static_body', 'build_external_domain',
                'build_external_flow_region_physics_mesh', 'audit_external_flow_mesh',
                'run_external_flow_solve', 'audit_external_flow_convergence', 'export_evidence', 'report']
        result = self.plan(*plan)
        self.assertEqual([], result.missing)
        self.assertEqual([], result.errors)
        self.assertEqual(['domain_study', 'mesh_study'], sorted(result.unavailable_operations))
        self.assertIn('does not provide: domain_study, mesh_study', result.plan_blocker)
        self.assertEqual('workflow_stage', result.plan_mechanism)
        self.assertFalse(result.executable)

    def test_a_plan_naming_everything_except_the_studies_names_the_real_gap(self):
        plan = ['classify_parts', 'audit_static_body', 'build_external_domain',
                'build_external_flow_region_physics_mesh', 'audit_external_flow_mesh',
                'run_external_flow_solve', 'audit_external_flow_convergence', 'export_evidence', 'report']
        result = self.plan(*plan)
        self.assertEqual(plan, result.plan_operations)
        self.assertEqual('workflow_stage', result.plan_mechanism)
        self.assertEqual(['domain_study', 'mesh_study'], sorted(result.unavailable_operations))
        self.assertIn('does not provide: domain_study, mesh_study', result.plan_blocker)
        self.assertFalse(result.executable)

    def test_a_plan_that_omits_the_post_steps_cannot_borrow_them_from_the_mechanism(self):
        # `report` / `export_evidence` exist for the formal mechanism, but if the plan does not name them
        # they are NOT in the plan. This is the "must really be invoked" rule.
        result = self.plan('audit_static_body', 'build_external_domain',
                           'build_external_flow_region_physics_mesh', 'audit_external_flow_mesh',
                           'run_external_flow_solve', 'audit_external_flow_convergence')
        self.assertIn('report', result.unavailable_operations)
        self.assertIn('export_evidence', result.unavailable_operations)
        self.assertIn('classify_parts', result.unavailable_operations)
        self.assertFalse(result.executable)

    def test_a_plan_that_omits_a_stage_is_not_credited_with_that_stage(self):
        # `audit_external_flow_convergence` provides solve_converged; leaving it out must leave the
        # capability uncovered even though the catalog has an operation for it.
        result = self.plan('audit_static_body', 'build_external_domain',
                           'build_external_flow_region_physics_mesh', 'audit_external_flow_mesh',
                           'run_external_flow_solve', 'export_evidence', 'report', 'classify_parts')
        self.assertIn('solve_converged', result.unavailable_operations)
        self.assertFalse(result.executable)

    def test_one_plan_cannot_mix_two_executors(self):
        result = self.plan('static_external_flow_chain', 'audit_static_body')
        self.assertIsNone(result.plan_mechanism)
        self.assertIn('mixes', result.plan_blocker)
        self.assertFalse(result.executable)
        self.assertFalse(result.mechanism_coverage['workflow_stage']['executable'])
        self.assertFalse(result.mechanism_coverage['restricted_trial']['executable'])

    def test_a_plan_naming_an_unresolved_operation_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            bogus = json.loads(CATALOG.read_text(encoding='utf-8'))
            bogus['operations'].append({'id': 'a_phantom_stage', 'title': 'phantom',
                                        'mechanism': 'workflow_stage', 'granularity': 'stage',
                                        'workflow_id': 'static-external-flow-2502',
                                        'stage': 'a-stage-that-does-not-exist', 'provides': ['mesh'],
                                        'scope_class': 'generic', 'general_acceptance': 'COMPILED_ONLY',
                                        'depends_on_fixed_names': False})
            path = Path(tmp) / 'catalog.json'
            path.write_text(json.dumps(bogus), encoding='utf-8')
            with patch.object(casespec, 'OPERATION_INDEX', path):
                result = validate_case_spec(spec(), None, self.AUDIT,
                                            plan_operations=['a_phantom_stage'])
        self.assertIn('a_phantom_stage', result.plan_operations)
        self.assertEqual(['a_phantom_stage'], result.plan_unresolved_operations)
        self.assertFalse(result.executable)
        self.assertIn('do not resolve', result.plan_blocker)

    def test_an_unresolved_plan_member_is_not_covered_by_a_mechanism_that_could_do_it_all(self):
        # THE REVIEW'S BLOCKER 1, reproduced as a test: a plan that names an unresolved operation AND an
        # operation that by itself provides everything must still be refused. Before the fix the
        # "one operation covers everything" fallback was consulted even when a plan WAS selected, so
        # `plan_unresolved` was silently ignored and `executable` came back True.
        with tempfile.TemporaryDirectory() as tmp:
            bogus = json.loads(CATALOG.read_text(encoding='utf-8'))
            everything = list(required_operations('external_static'))
            bogus['operations'].append({'id': 'a_phantom_stage', 'title': 'phantom',
                                        'mechanism': 'workflow_stage', 'granularity': 'stage',
                                        'workflow_id': 'static-external-flow-2502',
                                        'stage': 'a-stage-that-does-not-exist', 'provides': ['mesh'],
                                        'scope_class': 'generic', 'general_acceptance': 'COMPILED_ONLY',
                                        'depends_on_fixed_names': False})
            # A RESOLVED single operation that provides every requirement: the strongest possible
            # temptation for the fallback to report green.
            bogus['operations'].append({'id': 'a_covering_stage', 'title': 'covering',
                                        'mechanism': 'workflow_stage', 'granularity': 'stage',
                                        'workflow_id': 'static-external-flow-2502', 'stage': 'validation',
                                        'provides': everything, 'scope_class': 'generic',
                                        'general_acceptance': 'COMPILED_ONLY',
                                        'depends_on_fixed_names': False})
            path = Path(tmp) / 'catalog.json'
            path.write_text(json.dumps(bogus), encoding='utf-8')
            with patch.object(casespec, 'OPERATION_INDEX', path):
                mixed = validate_case_spec(spec(), None, self.AUDIT,
                                           plan_operations=['a_covering_stage', 'a_phantom_stage'])
                solo = validate_case_spec(spec(), None, self.AUDIT, plan_operations=['a_covering_stage'])
        self.assertEqual(['a_phantom_stage'], mixed.plan_unresolved_operations)
        self.assertFalse(mixed.executable, 'an unresolved plan member must never be covered by a fallback')
        self.assertIn('do not resolve', mixed.plan_blocker)
        self.assertTrue(solo.executable,
                        'without the unresolved member the same plan must be executable, so the refusal '
                        'above is really about the plan and not about the fixture')
        # The fallback still works when NO plan was selected: one operation covers everything.
        with tempfile.TemporaryDirectory() as tmp:
            bogus = json.loads(CATALOG.read_text(encoding='utf-8'))
            bogus['operations'].append({'id': 'a_covering_stage', 'title': 'covering',
                                        'mechanism': 'workflow_stage', 'granularity': 'stage',
                                        'workflow_id': 'static-external-flow-2502', 'stage': 'validation',
                                        'provides': list(required_operations('external_static')),
                                        'scope_class': 'generic', 'general_acceptance': 'COMPILED_ONLY',
                                        'depends_on_fixed_names': False})
            path = Path(tmp) / 'catalog.json'
            path.write_text(json.dumps(bogus), encoding='utf-8')
            with patch.object(casespec, 'OPERATION_INDEX', path):
                fallback = validate_case_spec(spec(), None, self.AUDIT)
        self.assertTrue(fallback.executable)
        self.assertEqual('workflow_stage', fallback.plan_mechanism)

    def test_two_offline_tools_of_the_same_mechanism_do_not_add_up_to_a_path(self):
        # Even with every offline tool present, no SINGLE operation covers the five-phase plan, so
        # without an explicit plan the conclusion stays false - and the blocker says why.
        result = validate_case_spec(spec(), None, self.AUDIT)
        self.assertIsNone(result.mechanism_coverage['workflow_stage']['complete_plan'])
        self.assertTrue(result.plan_blocker)
        self.assertFalse(result.executable)

    def test_two_partial_operations_of_one_mechanism_never_add_up_to_a_path(self):
        # The union shape INSIDE one mechanism, with an audit supplied: two operations that together
        # provide every requirement, neither of them alone. No plan is selected, so `executable` must stay
        # false - the union of capabilities is not an execution path. This is the case that a mutation
        # replacing the covering-operation test with a constant would turn green.
        requirements = list(required_operations('external_static'))
        half = len(requirements) // 2
        with tempfile.TemporaryDirectory() as tmp:
            bogus = json.loads(CATALOG.read_text(encoding='utf-8'))
            bogus['operations'] = [entry for entry in bogus['operations']
                                   if entry.get('mechanism') != 'workflow_stage']
            for index, chunk in enumerate((requirements[:half], requirements[half:])):
                bogus['operations'].append({'id': 'partial_%d' % index, 'title': 'partial %d' % index,
                                            'mechanism': 'workflow_stage', 'granularity': 'stage',
                                            'workflow_id': 'static-external-flow-2502',
                                            'stage': 'validation' if index == 0 else 'solve',
                                            'provides': chunk, 'scope_class': 'generic',
                                            'general_acceptance': 'COMPILED_ONLY',
                                            'depends_on_fixed_names': False})
            path = Path(tmp) / 'catalog.json'
            path.write_text(json.dumps(bogus), encoding='utf-8')
            with patch.object(casespec, 'OPERATION_INDEX', path):
                result = validate_case_spec(spec(), None, self.AUDIT)
        coverage = result.mechanism_coverage['workflow_stage']
        self.assertEqual([], coverage['missing'], 'together they do cover every requirement')
        self.assertIsNone(coverage['covering_operation'],
                          'no SINGLE operation provides the whole requirement')
        self.assertIsNone(coverage['complete_plan'])
        self.assertFalse(coverage['executable'])
        self.assertFalse(result.executable)


if __name__ == '__main__':
    unittest.main(verbosity=2)
