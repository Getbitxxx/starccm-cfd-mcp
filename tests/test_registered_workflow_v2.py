"""R23 independent registration counterexamples; no STAR, SSH or production review."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_registered_workflow_v2 as adapter
import registered_workflow_mcp_v2 as api


class ReviewBoundaryTests(unittest.TestCase):
    def test_unknown_source_requires_trusted_audit_without_creating_candidate(self):
        result = adapter.prepare_static_external_flow(self.source, {}, 'new-source-offline')
        self.assertEqual(result['state'], 'NEEDS_TRUSTED_SOURCE_AUDIT')
        self.assertFalse(result['star_started'])
        self.assertFalse((self.root / 'test-evidence/static-release-candidate/new-source-offline').exists())

    def test_new_audited_source_contract_is_generated_from_existing_certificate(self):
        audit = {'status': 'ok', 'input_sim_sha256': adapter.digest(self.source),
            'body_shape': 'cylinder', 'body_part_name': 'RENAMED_BODY',
            'body_origin_m': [1, 2, 3], 'body_radius_m': .03,
            'body_length_m': .1, 'body_axis': [0, 0, 1], 'cad_retained': True}
        self.write('test-evidence/new-audit/static_body_audit.json', audit)
        self.write('cad/new.step', 'source STEP', raw=True)
        spec = {'model_length_unit': 'm', 'input_identity': {'step_sha256': adapter.digest(self.root / 'cad/new.step')}}
        with patch.object(adapter, 'assert_citable', return_value={'state': 'CITABLE'}), \
             patch.object(adapter, 'prepare_candidate', return_value={'candidate': 'offline'}) as prepare, \
             patch.object(adapter, 'prepare_candidate_studies', return_value={'star_started': False}):
            adapter.prepare_static_external_flow(self.source, spec, 'new-audited-source')
        contract = adapter.load(self.root / 'test-evidence/static-release-candidate/new-audited-source/source-contract.json')
        self.assertEqual(contract['body_part_name'], 'RENAMED_BODY')
        self.assertEqual(contract['origin_m'], [1, 2, 3])
        self.assertEqual(contract['source_sim_sha256'], adapter.digest(self.source))
        self.assertEqual(contract['source_kind'], 'step_cad')
        self.assertEqual(prepare.call_args.args[3], self.root / 'test-evidence/new-audit/static_body_audit.json')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.workflow = 'new-cylinder-fixture'
        self.evidence = self.root / 'test-evidence/candidate'
        self.evidence.mkdir(parents=True)
        self.source = self.root / 'user.sim'
        self.source.write_bytes(b'new geometry, never old approval')
        self.spec = self.root / 'case.json'
        self.spec.write_text('{}')
        self.write('present.py', '# current presentation', raw=True)
        self.pipeline = {'stages': [{'name': 'audit-body', 'timeout_seconds': 480, 'macros': ['AuditStaticBodyGeometry.java'], 'input': 'static_body_geometry.sim', 'macro_outputs': [None], 'output': None}], 'macro_hashes': {},
            'input_hashes': {'static_body_geometry.sim': adapter.digest(self.source),
                             'case_spec.json': adapter.digest(self.spec)}}
        self.manifest = {'id': self.workflow, 'mechanism': 'restricted_trial',
            'mcp_evidence': 'test-evidence/candidate', 'pipeline': 'pipeline.json',
            'star_seconds_cap': 480, 'source_input_name': 'static_body_geometry.sim',
            'adapter_files': ['pipeline.json', 'present.py'], 'presentation_entry': 'present.py'}
        self.manifest_path = 'registered-workflows-v2/' + self.workflow + '/manifest.json'
        self.write('pipeline.json', self.pipeline)
        self.sign()
        self.patches = [patch.object(adapter, 'ROOT', self.root), patch.object(api, 'ROOT', self.root),
                        patch.object(adapter, 'check_review', return_value=None)]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def write(self, path, value, raw=False):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(value if raw else json.dumps(value))

    def sign(self):
        self.write(self.manifest_path, self.manifest)
        self.write('test-evidence/candidate/pipeline-review.json', {
            'approved': True, 'independent': True,
            'registration_hashes': {p: adapter.digest(self.root / p) for p in
                                   self.manifest['adapter_files'] + [self.manifest_path]}})
        # Dummy reviewed runner bytes: isolate the additional registration cap/presentation gates.
        self.write('scripts/security/trial_air3b_pipeline.ps1', '# runner', raw=True)
        self.write('scripts/air3b_pipeline_trial.py', '# controller', raw=True)

    def prepare(self):
        return adapter.prepare(self.workflow, self.source, self.spec, self.evidence, 'r23-fixture')

    def test_actual_stage_deadline_cannot_exceed_reviewed_registration_cap(self):
        self.pipeline['stages'][0]['timeout_seconds'] = 481
        self.write('pipeline.json', self.pipeline)
        self.sign()
        with self.assertRaises(ValueError): self.prepare()

    def test_presentation_must_be_in_reviewed_adapter_files(self):
        self.manifest['adapter_files'].remove('present.py')
        self.sign()
        with self.assertRaises(ValueError): self.prepare()

    def test_presentation_must_exist_even_if_registration_is_reviewed(self):
        self.manifest['adapter_files'].remove('present.py')
        (self.root / 'present.py').unlink()
        self.sign()
        with self.assertRaises(ValueError): self.prepare()

    def test_changed_source_cannot_inherit_prior_review(self):
        self.source.write_bytes(b'changed geometry')
        with self.assertRaisesRegex(ValueError, 'Submitted input differs'): self.prepare()

    def test_actual_candidate_plan_reports_studies_without_false_classification_gap(self):
        from casespec import validate_case_spec
        spec = json.loads((ROOT / 'cases/static-cylinder-step/r25/case_spec.json').read_text())
        record = json.loads((ROOT / 'test-evidence/static-cylinder-step/r26-positive/static-cylinder-step-r26-positive/static_body_audit.json').read_text())
        audit = {'status': 'ok', 'geometryParts': [{'name': record['body_part_name'],
            'isClosed': record['is_closed'], 'isManifold': record['is_manifold'],
            'boundingBoxRaw': record['measured_bbox_si_m']}], 'regions': []}
        result = validate_case_spec(spec, audit=audit, case=spec['case'],
                                    plan_operations=adapter.PLAN).as_dict()
        self.assertTrue(result['valid'])
        self.assertFalse(result['executable'])
        self.assertNotIn('classify_parts', result['unavailable_operations'])
        self.assertIn('mesh_study', result['unavailable_operations'])
        self.assertIn('domain_study', result['unavailable_operations'])

    def test_changed_casespec_cannot_inherit_prior_review(self):
        self.spec.write_text('{"new_inflow": 20}')
        with self.assertRaisesRegex(ValueError, 'Submitted input differs'):
            self.prepare()

    def test_changed_registration_bytes_refused(self):
        self.write('present.py', '# different presentation', raw=True)
        with self.assertRaisesRegex(ValueError, 'Reviewed registration bytes changed'): self.prepare()

    def candidate_fixture(self, bad=None, finite=None, generated=False, unit='m'):
        from types import SimpleNamespace
        spec = json.loads((ROOT / 'cases/static-cylinder-step/case_spec.json').read_text())
        spec['case_ref'] = 'library/' + adapter.digest(self.source) + '.sim'
        spec['model_length_unit'] = unit
        if finite is not None: spec['finite_execution'].update(finite)
        if bad == 'role': spec['geometry']['body_role'] = 'fluid_domain'
        if bad == 'flow_axis': spec['flow']['velocity_m_s'] = [15, 1, 0]
        if bad == 'negative_flow': spec['flow']['velocity_m_s'] = [-15, 0, 0]
        if bad == 'cylinder_axis': spec['geometry']['body_axis'] = [1, 0, 0]
        if bad == 'geometry_radius': spec['geometry']['body_radius_m'] *= 1.2
        if bad == 'geometry_origin': spec['geometry']['body_origin_m'] = [1, 2, 3]
        if bad == 'geometry_name':
            spec['geometry']['body_part_name'] = 'DIFFERENT_BODY'
            spec['geometry']['body_parts'] = ['DIFFERENT_BODY']
        self.write('case.json', spec)
        contract = json.loads((ROOT / 'cases/static-cylinder-step/geometry-contract.json').read_text())
        self.write('cylinder.step', 'actual STEP fixture', raw=True)
        contract['step_path'] = 'cylinder.step'
        contract['step_sha256'] = adapter.digest(self.root / 'cylinder.step')
        spec['input_identity']['step_sha256'] = contract['step_sha256']
        self.write('case.json', spec)
        self.write('contract.json', contract)
        audit = {'body_part_name': spec['geometry']['body_part_name'], 'model_length_unit': unit,
            'regions': 0, 'is_closed': True, 'is_manifold': True,
            'input_sim_sha256': adapter.digest(self.source), 'iteration': 0, 'cad_retained': True, 'measured_bbox_si_m': spec['geometry']['expected_bounds_m']}
        if bad in ('cad_retained', 'iteration', 'is_closed', 'is_manifold', 'regions', 'input_sim_sha256'):
            audit[bad] = {'cad_retained':False, 'iteration':1, 'is_closed':False, 'is_manifold':False, 'regions':1, 'input_sim_sha256':'wrong'}[bad]
        if bad == 'step_bytes': self.write('cylinder.step', 'changed STEP', raw=True)
        if bad == 'step_declaration':
            spec['input_identity']['step_sha256'] = 'wrong'
            self.write('case.json', spec)
        if generated:
            spec['geometry']['body_shape'] = contract['shape'] = 'sphere'
            spec['geometry']['body_surface_count'] = 1
            spec.pop('input_identity', None)
            contract = {k:v for k,v in contract.items() if not k.startswith('step_')}
            contract.update(source_kind='generated_sphere', source_sim_sha256=adapter.digest(self.source),
                generation_record='generation.json')
            audit.update({k:spec['geometry'][k] for k in
                ('body_shape', 'body_origin_m', 'body_radius_m')})
            generation = {**audit, 'part_count': 1, 'output_sha256': adapter.digest(self.source)}
            if unit == 'mm':
                for measured in (audit, generation):
                    measured.update(model_length_unit_readback=' (mm)',
                        preferred_length_unit_conversion_to_m=0.001,
                        measured_vertex_radius_error_ratio=0.01, measured_centre_offset_ratio=0,
                        measured_volume_relative_deviation=0.001)
                if bad == 'unit_conversion': audit['preferred_length_unit_conversion_to_m'] = 1
                if bad == 'unit_readback': audit['model_length_unit_readback'] = ' (m)'
                if bad == 'unit_missing': generation.pop('model_length_unit_readback')
                if bad == 'unit_si_geometry': audit['measured_vertex_radius_error_ratio'] = 1000
                if bad == 'unit_declaration': audit['model_length_unit'] = 'm'
            audit.pop('input_sim_sha256')
            self.write('audit-stage.json', {'stage':'audit-body', 'state':'ok', 'macros':[
                {'macro':'AuditStaticBodyGeometry.java', 'ran':True, 'status':'ok',
                 'exit_code':0, 'input_sha256':adapter.digest(self.source)}]})
            if bad == 'generated_source': contract['source_sim_sha256'] = 'wrong'
            if bad == 'generated_output': generation['output_sha256'] = 'wrong'
            if bad == 'generated_role': spec['geometry']['body_role'] = 'unknown'
            if bad == 'generated_parts': generation['part_count'] = 2
            if bad == 'generated_shape': generation['body_shape'] = 'cylinder'
            if bad == 'generated_audit_hash':
                self.write('audit-stage.json', {'macros':[{'macro':'AuditStaticBodyGeometry.java',
                    'ran':True,'status':'ok','exit_code':0,'input_sha256':'wrong'}]})
            self.write('generation.json', generation)
            contract['generation_record_sha256'] = adapter.digest(self.root / 'generation.json')
            if bad == 'generated_evidence_hash': contract['generation_record_sha256'] = 'wrong'
            if bad == 'generated_missing': (self.root / 'generation.json').unlink()
            self.write('case.json', spec)
            self.write('contract.json', contract)
        self.write('audit.json', audit)
        template = json.loads((ROOT / 'cases/static-sphere-external-flow/pipeline-e2e-geometry-to-validation.json').read_text())
        self.write('cases/static-sphere-external-flow/pipeline-e2e-geometry-to-validation.json', template)
        for name in {n for st in template['stages'] for n in st['macros']} | {'ExportExternalFlowResults.java'}:
            self.write('macros/' + name, '// uncompiled candidate fixture', raw=True)
        with patch.object(adapter, 'assert_citable', return_value={'fixture': True,
                'stage_record': str(self.root / 'audit-stage.json')} if generated else {'fixture': True}), \
             patch.object(adapter, 'resolve_operations', return_value={'unresolved': [], 'operations': {}}), \
             patch.object(adapter, 'validate_case_spec', return_value=SimpleNamespace(as_dict=lambda: {
                 'valid': True, 'executable': False, 'plan_blocker': 'missing engineering studies'})):
            result = adapter.prepare_candidate(self.source, self.spec, 'fresh-candidate',
                self.root / 'audit.json', self.root / 'contract.json', self.root / 'test-evidence/fresh')
        return result

    def test_generated_sphere_reuses_same_prepare_without_step(self):
        result = self.candidate_fixture(generated=True)
        self.assertEqual(result['source_kind'], 'generated_sphere')
        self.assertFalse(result['approved'])
        manifest = adapter.load(self.root / 'registered-workflows-v2/fresh-candidate/manifest.json')
        self.assertIn('generation.json', manifest['adapter_files'])
        self.assertIn('audit-stage.json', manifest['adapter_files'])

    def test_mm_admission_requires_measured_units_and_preserves_si_bounds(self):
        # The actual mm geometry certificate is still valid. It cannot authorize
        # a new full pipeline whose installed result exporter requires metre CaseSpec.
        with self.assertRaisesRegex(ValueError, 'ExportExternalFlowResults requires SI metre'):
            self.candidate_fixture(generated=True, unit='mm')
        self.assertEqual(adapter.load(self.root / 'audit.json')['model_length_unit'], 'mm')
        self.assertFalse((self.root / 'registered-workflows-v2/fresh-candidate/manifest.json').exists())

    def test_mm_wrong_readback_conversion_or_si_geometry_rejected(self):
        for bad in ('unit_conversion','unit_readback','unit_missing','unit_si_geometry','unit_declaration'):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, 'unit|SI geometry'):
                    self.candidate_fixture(generated=True, unit='mm', bad=bad)
                self.assertFalse((self.root / 'registered-workflows-v2/fresh-candidate/manifest.json').exists())

    def test_generated_source_identity_evidence_and_role_refused_before_registration(self):
        for bad in ('generated_source','generated_output','generated_role','generated_parts',
                    'generated_shape','generated_audit_hash','generated_evidence_hash','generated_missing'):
            with self.subTest(bad=bad):
                with self.assertRaises((ValueError, FileNotFoundError)):
                    self.candidate_fixture(bad=bad, generated=True)
                self.assertFalse((self.root / 'registered-workflows-v2/fresh-candidate/manifest.json').exists())

    def test_explicit_finite_allocation_reuses_six_stages_and_input_hashes(self):
        deadlines = dict(zip(('audit-body', 'build-domain', 'region-physics-mesh',
                              'mesh-quality-audit', 'solve', 'validation'), (30, 30, 70, 30, 50, 40)))
        result = self.candidate_fixture(finite={'max_iterations': 150,
            'solver_wall_budget_seconds': 35, 'star_seconds_cap': 250,
            'stage_timeout_seconds': deadlines})
        pipeline = adapter.load(self.root / 'registered-workflows-v2/fresh-candidate/pipeline.json')
        self.assertEqual(sum(s['timeout_seconds'] for s in pipeline['stages']), 250)
        self.assertEqual(pipeline['env']['EXTERNAL_FLOW_MAX_ITERATIONS'], '150')
        self.assertEqual(pipeline['input_hashes']['static_body_geometry.sim'], adapter.digest(self.source))
        self.assertEqual(pipeline['input_hashes']['case_spec.json'], adapter.digest(self.spec))
        self.assertFalse(result['approved'])
        self.assertFalse(result['star_started'])

    def test_invalid_finite_deadlines_refused_before_candidate_creation(self):
        valid = dict(zip(('audit-body', 'build-domain', 'region-physics-mesh',
                         'mesh-quality-audit', 'solve', 'validation'), (30, 30, 70, 30, 50, 40)))
        for bad in ({**valid, 'solve': 29}, {**valid, 'solve': True},
                    {k:v for k,v in valid.items() if k != 'solve'}, {**valid, 'extra': 30}):
            with self.subTest(deadlines=bad):
                with self.assertRaises(ValueError):
                    self.candidate_fixture(finite={'stage_timeout_seconds': bad})
                self.assertFalse((self.root / 'registered-workflows-v2/fresh-candidate/manifest.json').exists())
        with self.assertRaisesRegex(ValueError, 'below'):
            self.candidate_fixture(finite={'solver_wall_budget_seconds': 50,
                'stage_timeout_seconds': valid})
        with self.assertRaisesRegex(ValueError, 'exceed'):
            self.candidate_fixture(finite={'star_seconds_cap': 249,
                'solver_wall_budget_seconds': 35, 'stage_timeout_seconds': valid})

    def test_candidate_certificate_identity_counterexamples(self):
        for bad in ('cad_retained', 'iteration', 'is_closed', 'is_manifold', 'regions', 'input_sim_sha256', 'step_bytes', 'step_declaration'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError): self.candidate_fixture(bad)
                self.assertFalse((self.root / 'registered-workflows-v2/fresh-candidate/manifest.json').exists())

    def test_candidate_scope_rejects_unsupported_role_and_axes(self):
        for bad in ('role', 'flow_axis', 'negative_flow', 'cylinder_axis'):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, 'scope'): self.candidate_fixture(bad)

    def test_wrong_size_position_or_name_cannot_create_candidate(self):
        for bad in ('geometry_radius','geometry_origin','geometry_name'):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, 'contract'): self.candidate_fixture(bad)
                self.assertFalse((self.root / 'registered-workflows-v2/fresh-candidate/manifest.json').exists())

    def test_renderer_uses_current_citable_physics_not_constant_speed(self):
        import importlib.util
        module_spec = importlib.util.spec_from_file_location('r23_renderer_test', ROOT / 'cases/static-cylinder-step/render_report.py')
        renderer = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(renderer)
        run = self.evidence / 'render-fixture'
        run.mkdir()
        def write(path, obj): path.write_text(json.dumps(obj))
        write(self.evidence / 'render-fixture-report.json', {'report_verdict': 'REPORT_OK', 'converged': False})
        write(self.evidence / 'render-fixture-exports.json', {'download_succeeded': True})
        write(self.evidence / 'render-fixture-config.json', {'case_label': 'Renderer fixture'})
        (self.evidence / 'render-fixture-report.md').write_text('Honest report')
        write(run / 'case_spec.json', {'geometry': {'body_shape': 'cylinder'}, 'finite_execution': {'max_iterations': 600}})
        write(run / 'external_flow_export.json', {'status': 'ok'})
        write(run / 'external_flow_physics.json', {'status': 'ok', 'actual_inlet_speed_m_s': 17.5, 'inlet_direction_option': 'BoundaryNormal'})
        write(run / 'stage-export.json', {'stage': 'export', 'state': 'ok', 'artifacts': ['external_flow_export.json']})
        physics_stage = run / 'stage-physics.json'
        write(physics_stage, {'stage': 'physics', 'state': 'ok', 'artifacts': ['external_flow_physics.json']})
        with patch.object(sys, 'argv', ['renderer', '--run-dir', str(run)]), patch('builtins.print'):
            renderer.main()
        self.assertIn('17.5 m/s', (run / 'report.html').read_text())
        (run / 'report.html').unlink()
        write(physics_stage, {'stage': 'physics', 'state': 'failed', 'artifacts': ['external_flow_physics.json']})
        with patch.object(sys, 'argv', ['renderer', '--run-dir', str(run)]):
            with self.assertRaises(Exception): renderer.main()
        self.assertFalse((run / 'report.html').exists())

    def test_offline_candidate_is_pending_not_compiled_or_engineering_executable(self):
        result = self.candidate_fixture()
        self.assertEqual(result['state'], 'PENDING_INDEPENDENT_REVIEW')
        self.assertFalse(result['approved'])
        self.assertFalse(result['star_started'])
        self.assertFalse(result['full_engineering_plan_validation']['executable'])
        manifest = adapter.load(self.root / 'registered-workflows-v2/fresh-candidate/manifest.json')
        self.assertIn('scripts/run_registered_workflow.py', manifest['adapter_files'])
        self.assertIn('scripts/registered_workflow_mcp.py', manifest['adapter_files'])
        self.assertFalse(manifest['capability_levels']['compiled'])
        self.assertFalse(manifest['capability_levels']['general_operation_acceptance'])
        self.assertFalse(manifest['capability_levels']['engineering_validated'])
        with self.assertRaises(FileNotFoundError): api.checked_registration('fresh-candidate')

    def test_submit_warns_on_insufficient_window2_even_with_total_budget(self):
        """2026-10-15：AGENTS.md §6 的用户裁决 A 把台账改为"只记账、不拦提交"。原断言要求窗口二不足即
        拒绝提交；裁决后该拒绝作废——但两个 key 仍必须被比较，且不足必须落成 budget_warning。
        提交必须真的发生（invoke 被调用一次），warning 必须写进 handle 文件。"""
        self.manifest['budget_case'] = 'new-cylinder'
        self.sign()
        review = api.load(self.evidence / 'pipeline-review.json')
        review['registered_request_ids'] = ['new-request-0001']
        self.write('test-evidence/candidate/pipeline-review.json', review)
        self.write('test-evidence/candidate/candidate.json', {'source_sim': str(self.source), 'case_spec_path': str(self.spec), 'input_hashes': self.pipeline['input_hashes']})
        adapter.publish_candidate(self.workflow)
        self.write('progress.json', {'budget': {'per_case': {'new-cylinder': {
            'remaining_authorized_star_seconds': 600, 'window2_remaining_seconds': 479}}}})
        with patch.object(api, 'invoke', return_value='{"trial_id":"new-request-0001"}') as launch:
            handle = api.submit_registered_workflow(self.workflow, self.source, self.spec, 'new-request-0001')
        self.assertEqual(1, launch.call_count)
        self.assertEqual('SUBMITTED', handle['state'])
        self.assertIn('window2_remaining_seconds', handle['budget_warning'])
        self.assertNotIn('remaining_authorized_star_seconds', handle['budget_warning'])
        self.assertTrue((self.evidence / 'mcp-handles/new-request-0001.json').is_file())
        recorded = api.load(self.evidence / 'mcp-handles/new-request-0001.json')
        self.assertEqual(handle['budget_warning'], recorded['budget_warning'])

    def test_approved_but_unregistered_candidate_cannot_submit(self):
        with patch.object(api, 'invoke') as launch:
            with self.assertRaisesRegex(ValueError, 'must be registered'):
                api.submit_registered_workflow(self.workflow, self.source, self.spec, 'new-request-0001')
            launch.assert_not_called()

    def test_publish_receipt_is_hash_bound_and_never_starts_star(self):
        self.write('test-evidence/candidate/candidate.json', {'source_sim': str(self.source),
            'case_spec_path': str(self.spec), 'input_hashes': self.pipeline['input_hashes']})
        with patch.object(api, 'invoke') as launch:
            receipt = adapter.publish_candidate(self.workflow)
            launch.assert_not_called()
        self.assertEqual(receipt['state'], 'REGISTERED_CURRENT_BYTES')
        self.assertFalse(receipt['star_started'])
        self.assertEqual(receipt['manifest_sha256'], adapter.digest(self.root / self.manifest_path))
        self.assertEqual(receipt['review_sha256'], adapter.digest(self.evidence / 'pipeline-review.json'))
        review_path = self.evidence / 'pipeline-review.json'
        review_path.write_text(review_path.read_text() + '\n')
        with patch.object(api, 'invoke') as launch:
            with self.assertRaisesRegex(ValueError, 'does not bind current reviewed bytes'):
                api.submit_registered_workflow(self.workflow, self.source, self.spec, 'new-request-0001')
            launch.assert_not_called()

    def test_real_import_c_failure_is_uncitable_and_cannot_seed_candidate(self):
        from run_record_gate import citation_status
        record = ROOT / 'test-evidence/static-cylinder-step/import/static-cylinder-step-import-20261005c/static_body_geometry.json'
        actual = adapter.load(record)
        stage = adapter.load(record.parent / 'stage-import-body.json')
        self.assertEqual(stage['state'], 'failed')
        self.assertTrue(stage['macros'][0]['ran'])
        self.assertEqual(stage['macros'][0]['seconds'], 13.83)
        self.assertEqual(actual['case_spec_fields_read']['cad_body_count'], '1')
        self.assertEqual(actual['case_spec_fields_read']['cad_face_count'], '4')
        self.assertEqual(actual['case_spec_fields_read']['part_class'], 'star.cadmodeler.SolidModelCompositePart')
        self.assertFalse(actual['output_sim_exists'])
        self.assertFalse((record.parent / 'static_body_geometry.sim').exists())
        result = citation_status(record)
        self.assertNotEqual(result['state'], 'CITABLE')
        fixture_record = self.root / 'real-import-error.json'
        fixture_record.write_bytes(record.read_bytes())
        (self.root / 'stage-import-body.json').write_text(json.dumps({
            'state': 'failed', 'stage': 'import-body', 'artifacts': [fixture_record.name]}))
        self.write('contract.json', adapter.load(ROOT / 'cases/static-cylinder-step/geometry-contract.json'))
        with self.assertRaises(Exception):
            adapter.prepare_candidate(self.source, self.spec, 'failed-source-candidate',
                fixture_record, self.root / 'contract.json', self.root / 'test-evidence/refused')
        self.assertFalse((self.root / 'registered-workflows-v2/failed-source-candidate/manifest.json').exists())

    def test_old_query_and_fetch_delegate_without_new_start(self):
        for name in ('get_registered_workflow_run', 'fetch_registered_workflow_results'):
            with patch.object(api.legacy, name, return_value={'old': True}) as old:
                self.assertEqual(getattr(api, name)('old-request-0001'), {'old': True})
                old.assert_called_once_with('old-request-0001')

class ReleaseIndependentIntakeTests(unittest.TestCase):
    """Release intake must reject identifiers before any candidate write."""
    def test_invalid_workflow_id_cannot_write_outside_candidate_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / 'cases').mkdir()
            source = root / 'source.sim'; source.write_bytes(b'trusted input')
            audit = root / 'audit.json'; audit.write_text('{}')
            (root / 'cases/source-contract.json').write_text(json.dumps({
                'source_sim_sha256': adapter.digest(source), 'audit_record': 'audit.json',
                'source_kind': 'generated_sphere', 'shape': 'sphere',
                'body_part_name': 'body', 'origin_m': [0, 0, 0], 'radius_m': 1}))
            escaped = root / 'test-evidence/escaped'
            with patch.object(adapter, 'ROOT', root), \
                    patch.object(adapter, 'assert_citable', return_value={}), \
                    patch.object(adapter, 'prepare_candidate', side_effect=ValueError('invalid workflow')):
                with self.assertRaises(ValueError):
                    adapter.prepare_static_external_flow(source, {}, '../escaped')
            self.assertFalse(escaped.exists(), 'Invalid ID wrote candidate inputs before rejection')

class ReleaseIndependentProvenanceTests(unittest.TestCase):
    def test_existing_contract_audit_pin_cannot_be_silently_refreshed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); (root / 'cases').mkdir()
            source = root / 'source.sim'; source.write_bytes(b'trusted source')
            audit = root / 'audit.json'; audit.write_text('{"changed": true}')
            (root / 'cases/source-contract.json').write_text(json.dumps({
                'source_sim_sha256': adapter.digest(source), 'audit_record': 'audit.json',
                'audit_record_sha256': '0' * 64, 'source_kind': 'generated_sphere',
                'shape': 'sphere', 'body_part_name': 'body', 'origin_m': [0, 0, 0], 'radius_m': 1}))
            with patch.object(adapter, 'ROOT', root), \
                    patch.object(adapter, 'assert_citable', return_value={}), \
                    patch.object(adapter, 'prepare_candidate', return_value={'state': 'ACCEPTED'}) as create, \
                    patch.object(adapter, 'prepare_candidate_studies', side_effect=lambda candidate, contract: candidate):
                try:
                    result = adapter.prepare_static_external_flow(source, {}, 'pin-counterexample')
                except ValueError:
                    result = {'state': 'REFUSED'}
            self.assertNotEqual(result['state'], 'ACCEPTED')
            create.assert_not_called()

class ReleaseIndependentActualPlanTests(unittest.TestCase):
    workflow = 'static-rc1-sphere-20261006final'

    def setUp(self):
        self.manifest, self.evidence, self.path = api.registration(self.workflow)
        self.candidate = api.load(self.evidence / 'candidate.json')
        self.spec = api.load(self.candidate['case_spec_path'])
        # Frozen fixture describes its original runtime, not today's changed solve macro.
        pipeline = api.load(ROOT / self.manifest['pipeline'])
        original_digest = api.digest
        pins = pipeline['macro_hashes']
        def frozen_macro_digest(path):
            file = Path(path)
            return pins[file.name] if file.parent == ROOT / 'macros' and file.name in pins else original_digest(path)
        runtime = patch.object(api, 'digest', side_effect=frozen_macro_digest)
        runtime.start(); self.addCleanup(runtime.stop)

    def test_prepared_real_candidate_has_calls_but_no_submission_or_run_claim(self):
        result = api.validate_registered_workflow_plan(self.workflow, self.spec)
        self.assertTrue(result['call_plan_executable'])
        self.assertFalse(result['submission_ready'])
        self.assertFalse(result['star_started'])
        self.assertFalse(result['engineering_verified'])
        self.assertEqual({kind: len(calls) for kind, calls in result['actual_study_calls'].items()},
                         {'mesh': 3, 'domain': 3})

    def test_omitted_research_operations_cannot_turn_complete_plan_green(self):
        result = api.validate_registered_workflow_plan(self.workflow, self.spec, adapter.PLAN)
        self.assertFalse(result['executable'])
        self.assertFalse(result['call_plan_executable'])

    def test_missing_actual_research_manifest_cannot_use_capability_union(self):
        manifest = json.loads(json.dumps(self.manifest)); manifest['studies'].pop('mesh')
        original = api.registration
        def selected(workflow):
            return (manifest, self.evidence, self.path) if workflow == self.workflow else original(workflow)
        with patch.object(api, 'registration', side_effect=selected):
            result = api.validate_registered_workflow_plan(self.workflow, self.spec)
        self.assertFalse(result['call_plan_executable'])
        self.assertTrue(result['study_call_blockers'])

    def test_changed_casespec_identity_rejected(self):
        spec = json.loads(json.dumps(self.spec)); spec['input_identity']['source_sim_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'Selected CaseSpec differs'):
            api.validate_registered_workflow_plan(self.workflow, spec)

    def test_changed_base_macro_cannot_be_executable(self):
        original = api.digest
        def changed(path):
            return '0' * 64 if Path(path).parent == ROOT / 'macros' else original(path)
        with patch.object(api, 'digest', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'Base macro bytes differ'):
                api.validate_registered_workflow_plan(self.workflow, self.spec)

class ReleaseIndependentLedgerCannotBlockAdmissionTests(unittest.TestCase):
    """F1（第一轮）＋ C1/C3（第二、三轮）：STAR 秒数台账不得成为提交门（AGENTS §6 裁决 A）。

    判据必须**逐分支**有分辨力：审查者证明，只要求"至少一个分支在台账充足时 submission_ready=True"时，
    对 studies/readback/checkpoint 三个分支单独把 budget_ready 折回 submission_ready 仍能存活。
    因此本类现在要求**四个分支全部**在台账充足时 submission_ready=True，然后清零台账，
    断言 budget_ready 翻为 False、出现 budget_warning、而 submission_ready 逐位不变。
    这样任何一个分支单独折回都会被抓住。

    两个门被**有意**打桩隔离（它们各有自己的测试）：基础 pipeline 的宏字节新鲜度
    （test_real_stale_registration_is_refused_with_a_recorded_reason）、运行时审查
    `air3b_pipeline_trial.check_review`（test_registered_adapter_hashes_cannot_replace_runtime_review_gate），
    以及"该 run_id 是否在审查分配里"这一**非预算**门（它必须仍然拒绝；本测试用注入分配来隔离它）。
    """

    workflows = {
        'readback': 'static-eng-u12-props-20261008',
        'checkpoint': 'static-eng-u12-segment1-20261007',
        'geometry_finite': 'resource64-combined-registered-20261009a',
        'studies': 'static-rc1-sphere-20261006final',
    }

    def _plan_with_ledger(self, workflow, fill):
        import air3b_pipeline_trial
        manifest, evidence, _ = api.registration(workflow)
        candidate = api.load(evidence / 'candidate.json')
        spec = api.load(candidate['case_spec_path'])
        pins = dict(api.load(ROOT / manifest['pipeline'])['macro_hashes'])
        # only the non-budget allocation gate is isolated here, by declaring the ids the plan binds
        ids = {workflow}
        if candidate.get('base_call'):
            ids.add(candidate['base_call'].get('request_id'))
        for info in (manifest.get('studies') or {}).values():
            for level in info['levels']:
                ids.update({level.get('workflow_id'), level.get('request_id'), level.get('trial_id')})
        original_digest, original_load = api.digest, api.load

        def digest(path):
            candidate_path = Path(path)
            if candidate_path.parent == ROOT / 'macros' and candidate_path.name in pins:
                return pins[candidate_path.name]
            return original_digest(path)

        def loaded(path):
            value = original_load(path)
            target = Path(path)
            if target.name == 'progress.json':
                value = json.loads(json.dumps(value))
                for account in value['budget']['per_case'].values():
                    account['remaining_authorized_star_seconds'] = fill
                    account['window2_remaining_seconds'] = fill
            elif target.name == 'pipeline-review.json':
                value = dict(value)
                value['registered_request_ids'] = sorted(
                    set(value.get('registered_request_ids') or []) | ids)
            return value

        with patch.object(api, 'digest', side_effect=digest), \
                patch.object(api, 'load', side_effect=loaded), \
                patch.object(air3b_pipeline_trial, 'check_review', lambda *args, **kwargs: None):
            return api.validate_registered_workflow_plan(workflow, spec)

    def test_every_branch_keeps_submission_readiness_independent_of_the_ledger(self):
        from registered_workflow_mcp_v2 import studies_plan_submission_blocker
        for kind, workflow in sorted(self.workflows.items()):
            with self.subTest(branch=kind):
                sufficient = self._plan_with_ledger(workflow, 10 ** 6)
                exhausted = self._plan_with_ledger(workflow, 0)
                self.assertTrue(sufficient['call_plan_executable'], kind)
                self.assertTrue(sufficient['budget_ready'],
                                kind + ': 灌满台账后 budget_ready 仍为 False，该分支没有分辨力')
                self.assertIsNone(sufficient['budget_warning'], kind)
                self.assertTrue(sufficient['submission_ready'],
                                kind + ': 灌满台账后 submission_ready 仍为 False，'
                                       '该分支的判据会退化成同义反复（独立审查 C3）')
                self.assertFalse(exhausted['budget_ready'], kind)
                self.assertTrue(exhausted['budget_warning'], kind)
                self.assertEqual(sufficient['submission_ready'], exhausted['submission_ready'], kind)
                self.assertEqual(sufficient['required_STAR_cap_seconds'],
                                 exhausted['required_STAR_cap_seconds'], kind)
        self.assertIsNone(studies_plan_submission_blocker(
            {'call_plan_executable': True, 'submission_ready': True,
             'budget_ready': False, 'budget_warning': 'ledger below cap'}))
        not_ready = studies_plan_submission_blocker({'call_plan_executable': True,
                                                     'submission_ready': False,
                                                     'budget_ready': False})
        self.assertIn('reviewed registrations', not_ready)
        self.assertNotIn('budget', not_ready)


class ReleaseIndependentReadinessTests(unittest.TestCase):
    workflow = ReleaseIndependentActualPlanTests.workflow
    setUp = ReleaseIndependentActualPlanTests.setUp
    def test_registered_adapter_hashes_cannot_replace_runtime_review_gate(self):
        original = api.registration
        with patch.object(api, 'checked_registration', side_effect=original), \
                patch('air3b_pipeline_trial.check_review', side_effect=ValueError('runtime hash differs')) as runtime:
            result = api.validate_registered_workflow_plan(self.workflow, self.spec)
        self.assertTrue(result['call_plan_executable'])
        self.assertFalse(result['submission_ready'])
        self.assertEqual(runtime.call_count, 7)
        self.assertTrue(all(not call['approved_current_bytes']
            for calls in result['actual_study_calls'].values() for call in calls))

    def test_complete_plan_budget_must_include_base_and_all_nonreused_levels(self):
        result = api.validate_registered_workflow_plan(self.workflow, self.spec)
        required = self.manifest['star_seconds_cap'] + sum(call['star_seconds_cap']
            for calls in result['actual_study_calls'].values() for call in calls)
        self.assertEqual(result['required_STAR_cap_seconds'], required)
        self.assertFalse(result['budget_ready'])
        self.assertFalse(result['submission_ready'])

class ReleaseIndependentHistoricalR22Tests(unittest.TestCase):
    run_id = 'original-r22-20261005a'
    workflow = 'original-geometry-mrf-mcp-2502'

    def setUp(self):
        self.manifest, self.evidence, self.manifest_path = api.legacy.registration(self.workflow)
        self.handle_path = self.evidence / 'mcp-handles' / (self.run_id + '.json')
        self.handle = api.load(self.handle_path)
        self.review_path = self.evidence / 'pipeline-review.json'
        self.review_digest = api.digest(self.review_path)

    def test_real_catalog_drift_keeps_old_query_identity_available_without_resigning(self):
        review = api.load(self.review_path)
        self.assertNotEqual(review['registration_hashes']['operations/catalog.json'],
                            api.digest(ROOT / 'operations/catalog.json'))
        self.assertFalse((self.evidence / 'registration.json').exists())
        # Current controller drift requires the independently pinned read-only migration.
        migration_path = self.evidence / 'historical-read-review.json'
        migration = api.load(migration_path) if migration_path.exists() else {}
        migration_current = bool(migration) and all(api.digest(ROOT / name) == sha
            for name, sha in migration.get('current_reader_hashes', {}).items())
        if migration_current:
            manifest, evidence, handle = api.legacy.lookup(self.run_id)
            self.assertEqual(manifest['id'], self.workflow)
            self.assertEqual(handle['existing_start_result']['trial_id'], self.run_id)
        else:
            with self.assertRaisesRegex(ValueError, 'Historical (query/report implementation differs|read support bytes changed)|Independent read-only migration does not bind'):
                api.legacy.lookup(self.run_id)
        self.assertEqual(api.digest(self.review_path), self.review_digest)
        with self.assertRaises(ValueError):
            api.legacy.checked_registration(self.workflow)

    def test_historical_r22_handle_identity_pollution_is_refused(self):
        original = api.legacy.load
        for section, key in [('intent', 'source_sha256'), ('intent', 'case_spec_sha256'),
                             ('intent', 'workflow_id'), ('existing_start_result', 'pipeline_sha256'),
                             ('existing_start_result', 'runner_sha256'),
                             ('existing_start_result', 'controller_sha256'),
                             ('existing_start_result', 'trial_id')]:
            handle = json.loads(json.dumps(self.handle)); handle[section][key] = 'changed'
            def polluted(path):
                return handle if Path(path) == self.handle_path else original(path)
            with self.subTest(section=section, key=key), \
                    patch.object(api.legacy, 'load', side_effect=polluted):
                with self.assertRaises(ValueError):
                    api.legacy.lookup(self.run_id)
        self.assertEqual(api.digest(self.review_path), self.review_digest)

    def test_historical_r22_start_and_review_inputs_must_match_manifest_identity(self):
        original = api.legacy.load
        handle = json.loads(json.dumps(self.handle))
        review = api.load(self.review_path)
        handle['existing_start_result']['input_hashes']['source_input.sim'] = '0' * 64
        review['input_hashes']['source_input.sim'] = '0' * 64
        def polluted(path):
            if Path(path) == self.handle_path: return handle
            if Path(path) == self.review_path: return review
            return original(path)
        with patch.object(api.legacy, 'load', side_effect=polluted):
            with self.assertRaises(ValueError):
                api.legacy.lookup(self.run_id)

    def test_historical_r22_current_query_controller_drift_is_refused(self):
        original = api.legacy.digest
        def changed(path):
            return '0' * 64 if Path(path) == ROOT / 'scripts/air3b_pipeline_trial.py' else original(path)
        with patch.object(api.legacy, 'digest', side_effect=changed):
            with self.assertRaises(ValueError):
                api.legacy.lookup(self.run_id)

class ReleaseIndependentDesktopEvidenceTests(unittest.TestCase):
    directory = ROOT / 'test-evidence/static-release-candidate/desktop-20261007'

    @staticmethod
    def unpack(value):
        if 'value' in value: return ReleaseIndependentDesktopEvidenceTests.unpack(value['value'])
        if 'result' in value: return ReleaseIndependentDesktopEvidenceTests.unpack(value['result'])
        if 'content' in value:
            text = value['content'][0]['text']
            try: return json.loads(text)
            except ValueError: return {'error': text}
        return value

    def evidence(self, name):
        return self.unpack(json.loads((self.directory / (name + '.json')).read_text()))

    def test_real_desktop_parent_budget_is_base_plus_four_actual_subcalls(self):
        result = self.evidence('full-plan')
        calls = [call for group in result['actual_study_calls'].values() for call in group
                 if 'workflow_id' in call]
        self.assertEqual(len(calls), 4)
        self.assertEqual(sum(call['star_seconds_cap'] for call in calls), 1020)
        self.assertEqual(self.evidence('positive-intake')['base_call']['star_seconds_cap'], 255)
        self.assertEqual(result['required_STAR_cap_seconds'], 1275)
        self.assertTrue(result['call_plan_executable'])
        self.assertFalse(result['submission_ready'])
        self.assertFalse(result['budget_ready'])

    def test_real_desktop_omitted_studies_and_missing_results_do_not_pass(self):
        omitted = self.evidence('omitted-studies')
        self.assertFalse(omitted['executable'])
        self.assertFalse(omitted['call_plan_executable'])
        studies = self.evidence('studies')
        for kind in ('mesh', 'domain'):
            self.assertEqual(studies['studies'][kind]['sensitivity_status'], 'NOT_ESTABLISHED')
            self.assertFalse(studies['studies'][kind]['sensitivity_passed'])

    def test_real_desktop_loaded_hashes_and_historical_identity_are_explicit(self):
        health = self.evidence('health')['desktop_runtime']
        self.assertTrue(health['loaded_matches_disk'])
        self.assertEqual(health['loaded_sha256'], health['disk_sha256'])
        self.assertEqual(self.evidence('positive-intake')['workflow_id'], 'static-rc1-desktop-20261007')
        for name, run in [('r22-query', 'original-r22-20261005a'),
                          ('u12-query', 'static-reusable-u12-20261006a'),
                          ('u12-fetch', 'static-reusable-u12-20261006a')]:
            result = self.evidence(name)
            self.assertEqual(result['run_id'], run)
            self.assertFalse(result['engineering_verified'])
        self.assertEqual(self.evidence('unknown-source')['state'], 'NEEDS_TRUSTED_SOURCE_AUDIT')
        self.assertIn('Invalid candidate workflow id', self.evidence('invalid-id')['error'])

    def test_real_u12_run_cannot_supply_new_desktop_parent_completion_report(self):
        with patch('run_sensitivity_study.assemble', return_value={}), \
                patch('run_sensitivity_study.completion_report') as report, \
                patch.object(api, 'validate_registered_workflow_plan', return_value={'call_plan_executable': True}), \
                patch.object(api, 'lookup', return_value=({'id': 'static-reusable-flow-u12-window1-draft'}, None, None)):
            with self.assertRaisesRegex(ValueError, 'belongs to another workflow'):
                api.assemble_registered_workflow_studies('static-rc1-desktop-20261007',
                                                        'static-reusable-u12-20261006a')
            report.assert_not_called()

class ReleaseIndependentRealDomainTimeoutTests(unittest.TestCase):
    run_id = 'static-rc1-desktop-20261007-domain-d1p25-rc1'
    directory = ROOT / 'test-evidence/static-release-candidate/static-rc1-desktop-20261007/domain/d1p25' / run_id

    def test_real_ok_solve_record_from_failed_timeout_stage_cannot_certify_success(self):
        from run_record_gate import citation_status, assert_citable, RecordNotCitable
        record = self.directory / 'external_flow_solve.json'
        actual = api.load(record)
        self.assertEqual(actual['status'], 'ok')
        self.assertEqual(actual['end_iteration'], 150)
        stage = api.load(self.directory / 'stage-solve.json')
        self.assertEqual(stage['state'], 'failed')
        self.assertIn('Owned STAR timeout after 49 s', stage['error'])
        self.assertEqual(stage['macros'][0]['status'], 'failed')
        self.assertIsNone(stage['macros'][0]['exit_code'])
        verdict = citation_status(record)
        self.assertEqual(verdict['state'], 'NOT_CITABLE_FAILED_RUN')
        self.assertEqual(verdict['stage'], 'solve')
        with self.assertRaises(RecordNotCitable): assert_citable(record)

    def test_real_prior_successful_stages_remain_citable_despite_later_failure(self):
        from run_record_gate import assert_citable
        self.assertEqual(api.load(self.directory / 'case-probe.json')['state'], 'FAILED')
        for name, stage in [('static_body_audit.json', 'audit-body'),
                            ('external_domain_build.json', 'build-domain'),
                            ('external_flow_physics.json', 'region-physics-mesh'),
                            ('external_flow_mesh_quality.json', 'mesh-quality-audit')]:
            with self.subTest(record=name):
                verdict = assert_citable(self.directory / name)
                self.assertEqual(verdict['state'], 'CITABLE')
                self.assertEqual(verdict['stage'], stage)
                self.assertEqual(verdict['stage_state'], 'ok')

    def test_real_unexecuted_validation_and_absent_checkpoint_cannot_claim_completion(self):
        from run_record_gate import assert_citable, RecordNotCitable
        self.assertFalse((self.directory / 'stage-validation.json').exists())
        for name in ['external_flow_convergence.json', 'external_flow_export.json']:
            self.assertFalse((self.directory / name).exists())
            with self.assertRaises(RecordNotCitable): assert_citable(self.directory / name)
        stage = api.load(self.directory / 'stage-solve.json')
        self.assertIsNone(stage['output_hash'])
        self.assertIsNone(stage['macros'][0]['output_sha256'])

class ReleaseEngineeringResourceTests(unittest.TestCase):
    setUp = ReviewBoundaryTests.setUp
    tearDown = ReviewBoundaryTests.tearDown
    write = ReviewBoundaryTests.write
    sign = ReviewBoundaryTests.sign
    candidate_fixture = ReviewBoundaryTests.candidate_fixture

    def test_limits64_propagates_to_reviewed_pipeline(self):
        result = self.candidate_fixture(generated=True)
        pipeline = api.load(result['manifest'])
        pipeline = api.load(self.root / pipeline['pipeline'])
        self.assertEqual(api.load(self.spec)['limits']['memory_gb'], 64)
        self.assertEqual(pipeline['memory_gb'], 64)
        self.assertEqual(api.load(self.spec)['limits']['logical_cores'], 8)
        self.assertEqual(pipeline['logical_cores'], 64)
        self.assertEqual(pipeline['resource_contract']['logical_cores'], 64)

    def test_explicit_new_contract_core_override_preserves_source_spec_identity(self):
        result = self.candidate_fixture(generated=True, finite={'resource_contract': {'logical_cores': 16}})
        manifest = api.load(result['manifest'])
        pipeline = api.load(self.root / manifest['pipeline'])
        self.assertEqual(api.load(self.spec)['limits']['logical_cores'], 8)
        self.assertEqual(pipeline['logical_cores'], 16)
        self.assertEqual(pipeline['resource_contract']['logical_cores'], 16)
        self.assertEqual(pipeline['input_hashes']['case_spec.json'], adapter.digest(self.spec))

    def test_new_timing_split_cannot_fit_old50_second_process_deadline(self):
        from stage_plan import plan_stage, StagePlanError
        stage = {'name': 'solve', 'input': 'mesh.sim', 'output': 'solved.sim',
                 'macros': ['Solve.java'], 'macro_outputs': ['solved.sim'], 'timeout_seconds': 50,
                 'timing_contract': {'load_seconds': 15, 'macro_seconds': 30,
                     'chunk_overrun_seconds': 15, 'save_seconds': 10, 'exit_seconds': 10}}
        with self.assertRaises(StagePlanError): plan_stage(stage)
        stage['timeout_seconds'] = 80
        plan_stage(stage)


class ReleaseEngineeringCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve()
        self.runtime = patch.object(adapter, 'ROOT', self.root); self.runtime.start()
        self.source = self.root / 'checkpoint.sim'; self.source.write_bytes(b'certified checkpoint')
        self.evidence = self.root / 'test-evidence/new'
        self.workflow = 'new-checkpoint'
        self.spec = self.root / 'spec.json'
        self.record = self.root / 'test-evidence/old/oldrun/external_flow_solve.json'
        self.stage = self.record.parent / 'stage-solve.json'
        self.config = self.record.parent.parent / 'oldrun-config.json'
        self.old_spec = self.root / 'original-spec.json'
        self.contract = self.root / 'checkpoint-contract.json'
        physical = {'geometry': {'shape': 'sphere'}, 'flow': {'velocity_m_s': [12,0,0]},
            'model_length_unit': 'm', 'finite_execution': {'expected_start_iteration': 150,
                'max_iterations': 1000, 'star_seconds_cap': 100,
                'stage_timeout_seconds': {name: 30 for name in ['audit-body','build-domain',
                    'region-physics-mesh','mesh-quality-audit','solve','validation']}}}
        self.write(self.spec, physical); self.write(self.old_spec, physical)
        self.write(self.record, {'status': 'ok', 'end_iteration': 150})
        self.write(self.stage, {'stage': 'solve', 'state': 'ok', 'job_dir': 'old-owned-job',
             'artifacts': [self.record.name], 'output_sha256': adapter.digest(self.source),
             'macros': [{'ran': True, 'status': 'ok', 'exit_code': 0}]})
        config = {'job_dir': 'old-owned-job', 'input_hashes': {'case_spec.json': adapter.digest(self.old_spec)},
                  'macro_hashes': {'OldSolve.java': 'oldhash'}}
        self.write(self.config, config)
        self.write(self.record.parent.parent / 'pipeline-review.json', dict(config, approved=True, independent=True))
        self.write(self.contract, {'input_kind': 'successful_solve_checkpoint',
            'checkpoint_record': str(self.record), 'executed_case_spec': str(self.old_spec),
            'executed_config': str(self.config), 'geometry_source_sim': str(self.root / 'geometry.sim'),
            'geometry_source_contract': str(self.root / 'geometry-contract.json')})

    def tearDown(self): self.runtime.stop(); self.temp.cleanup()
    def write(self, path, value): path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value))
    def prepare(self):
        return adapter.prepare_checkpoint_candidate(self.source, self.spec, self.workflow,
            self.root / 'audit.json', self.contract, self.evidence)

    def test_real_failed_domain_record_cannot_be_checkpoint_source(self):
        from run_record_gate import RecordNotCitable
        real = ReleaseIndependentRealDomainTimeoutTests.directory
        for name in ['external_flow_solve.json', 'stage-solve.json', 'case-probe.json']:
            (self.record.parent / name).write_bytes((real / name).read_bytes())
        with self.assertRaises(RecordNotCitable): self.prepare()
        self.assertFalse(adapter.registration_path(self.workflow).exists())

    def test_checkpoint_start_mismatch_refused(self):
        spec = api.load(self.spec); spec['finite_execution']['expected_start_iteration'] = 0; self.write(self.spec, spec)
        with self.assertRaisesRegex(ValueError, 'source/start'): self.prepare()

    def test_self_consistent_foreign_config_cannot_explain_checkpoint(self):
        foreign = self.root / 'foreign-config.json'; foreign.write_bytes(self.config.read_bytes())
        contract = api.load(self.contract); contract['executed_config'] = str(foreign); self.write(self.contract, contract)
        with self.assertRaisesRegex(ValueError, 'certified run'): self.prepare()

    def test_original_executed_spec_pin_cannot_change(self):
        spec = api.load(self.old_spec); spec['flow']['velocity_m_s'] = [99,0,0]; self.write(self.old_spec, spec)
        with self.assertRaisesRegex(ValueError, 'original execution approval'): self.prepare()

    def test_checkpoint_cap_rejected_before_geometry_candidate_creation(self):
        spec = api.load(self.spec); spec['finite_execution']['star_seconds_cap'] = 59; self.write(self.spec, spec)
        with patch.object(adapter, 'prepare_candidate') as geometry:
            with self.assertRaisesRegex(ValueError, 'before candidate creation'): self.prepare()
            geometry.assert_not_called()
        self.assertFalse(adapter.registration_path(self.workflow).exists())

    def test_existing_orphan_pipeline_is_preserved_not_deleted_on_failure(self):
        path = adapter.registration_path(self.workflow).parent / 'pipeline.json'
        path.parent.mkdir(parents=True); path.write_bytes(b'previous immutable pipeline')
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(path.read_bytes(), b'previous immutable pipeline')

    def test_partial_geometry_candidate_is_removed_after_conversion_failure(self):
        def fail(*args):
            manifest = adapter.registration_path(self.workflow); manifest.parent.mkdir(parents=True)
            manifest.write_text('{}'); (manifest.parent / 'pipeline.json').write_text('{}')
            self.evidence.mkdir(parents=True); (self.evidence / 'candidate.json').write_text('{}')
            raise ValueError('conversion failed after geometry preparation')
        with patch.object(adapter, '_prepare_checkpoint_candidate', side_effect=fail):
            with self.assertRaisesRegex(ValueError, 'conversion failed'): self.prepare()
        self.assertFalse(adapter.registration_path(self.workflow).exists())
        self.assertFalse((self.evidence / 'candidate.json').exists())

class ReleaseFiniteGeometryPlanTests(unittest.TestCase):
    setUp = ReviewBoundaryTests.setUp
    tearDown = ReviewBoundaryTests.tearDown
    write = ReviewBoundaryTests.write
    sign = ReviewBoundaryTests.sign
    candidate_fixture = ReviewBoundaryTests.candidate_fixture

    def test_finite_geometry_level_does_not_claim_complete_studies(self):
        candidate = self.candidate_fixture(generated=True)
        spec = api.load(self.spec)
        self.write('progress.json', {'budget': {'per_case': {spec['case']: {
            'remaining_authorized_star_seconds': 3600, 'window2_remaining_seconds': 3600}}}})
        with patch('run_record_gate.assert_citable', return_value={'state': 'CITABLE'}):
            plan = api.validate_registered_workflow_plan(candidate['workflow_id'], spec)
            wrong = api.validate_registered_workflow_plan(candidate['workflow_id'], spec, adapter.PLAN + ['mesh_study'])
        self.assertTrue(plan['call_plan_executable'])
        self.assertEqual(plan['plan_scope'], 'FINITE_AUDITED_GEOMETRY_LEVEL')
        self.assertFalse(plan['complete_engineering_plan']); self.assertFalse(plan['submission_ready'])
        self.assertFalse(wrong['call_plan_executable'])


class ReleaseSolvedReadbackTests(ReleaseEngineeringCheckpointTests):
    def setUp(self):
        super().setUp()
        spec = api.load(self.spec)
        spec.update(scenario='external_static', case='readback-account')
        self.write(self.spec, spec); self.write(self.old_spec, spec)
        config = api.load(self.config)
        config['input_hashes']['case_spec.json'] = adapter.digest(self.old_spec)
        self.write(self.config, config)
        self.write(self.record.parent.parent / 'pipeline-review.json', dict(config, approved=True, independent=True))
        self.write(self.record.parent / 'case-probe.json', {'state': 'AIR3B_PIPELINE_COMPLETE'})
        self.write(self.record.parent / 'external_flow_validation.json', {'status': 'ok', 'iteration': 150,
            'cells': 100, 'input_sim_sha256': adapter.digest(self.source)})
        self.write(self.record.parent / 'stage-validation.json', {'stage': 'validation', 'state': 'ok',
            'artifacts': ['external_flow_validation.json'], 'macros': [{'ran': True, 'status': 'ok', 'exit_code': 0}]})
        contract = api.load(self.contract); contract['input_kind'] = 'successful_solved_readback'; self.write(self.contract, contract)

    def validate_readback(self):
        return adapter.validate_solved_readback_source(self.source, self.spec, self.contract)

    def test_real_current_u12_readback_preserves_executed_spec_without_start_rewrite(self):
        real = ROOT / 'test-evidence/static-release-candidate/engineering-contract-20261007/u12-continuation'
        run = real / 'static-eng-u12-segment1-20261007'
        contract = {'input_kind': 'successful_solved_readback', 'checkpoint_record': str(run / 'external_flow_solve.json'),
            'executed_case_spec': str(run / 'case_spec.json'),
            'executed_config': str(real / 'static-eng-u12-segment1-20261007-config.json')}
        self.runtime.stop()
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', dir=real, delete=False) as f:
                json.dump(contract, f); path = Path(f.name)
            result = adapter.validate_solved_readback_source(run / 'external_flow_solved.sim', run / 'case_spec.json', path)
            self.assertEqual(result[7]['end_iteration'], 1150)
            self.assertEqual(result[4]['finite_execution']['expected_start_iteration'], 150)
        finally:
            path.unlink(); self.runtime.start()

    def test_successful_solve_inside_overall_failed_run_is_refused(self):
        self.write(self.record.parent / 'case-probe.json', {'state': 'FAILED', 'error': 'later stage failed'})
        adapter.assert_citable(self.record)
        with self.assertRaisesRegex(ValueError, 'successful terminal'): self.validate_readback()

    def test_failed_validation_cannot_supply_readback_identity(self):
        from run_record_gate import RecordNotCitable
        path = self.record.parent / 'stage-validation.json'; stage = api.load(path); stage['state'] = 'failed'; self.write(path, stage)
        with self.assertRaises(RecordNotCitable): self.validate_readback()

    def test_physically_equal_but_reencoded_spec_is_refused(self):
        self.spec.write_text(self.old_spec.read_text() + '\n')
        with self.assertRaisesRegex(ValueError, 'CaseSpec bytes'): self.validate_readback()

    def test_u15_source_cannot_establish_current_u12_readback(self):
        spec = api.load(self.spec); spec['flow']['velocity_m_s'] = [15,0,0]; self.write(self.spec, spec)
        with self.assertRaisesRegex(ValueError, 'identity changed'): self.validate_readback()

    def test_unrecorded_source_hash_is_refused(self):
        self.source.write_bytes(b'different downloaded file')
        with self.assertRaisesRegex(ValueError, 'source/start'): self.validate_readback()

    def test_geometry_only_zero_iteration_is_refused(self):
        self.write(self.record, {'status': 'ok', 'end_iteration': 0})
        self.write(self.record.parent / 'external_flow_validation.json', {'status': 'ok', 'iteration': 0,
            'cells': 100, 'input_sim_sha256': adapter.digest(self.source)})
        with self.assertRaisesRegex(ValueError, 'solved static SI identity'): self.validate_readback()

    def test_readback_candidate_is_exact_single_readonly_macro_60s(self):
        geometry = self.root / 'geometry.sim'; geometry.write_bytes(b'geometry')
        audit = self.record.parent / 'static_body_audit.json'; self.write(audit, {'status': 'ok'})
        self.write(self.record.parent / 'stage-audit-body.json', {'stage': 'audit-body', 'state': 'ok',
            'artifacts': ['static_body_audit.json'], 'macros': [{'ran': True, 'status': 'ok', 'exit_code': 0}]})
        config = api.load(self.config); config['input_hashes']['static_body_geometry.sim'] = adapter.digest(geometry)
        self.write(self.config, config); self.write(self.record.parent.parent / 'pipeline-review.json', dict(config, approved=True, independent=True))
        self.write(self.root / 'geometry-contract.json', {'source_sim_sha256': adapter.digest(geometry)})
        template = self.root / 'cases/static-cylinder-step/engineering-preparation-20261007/property-pipeline-template.json'
        template.parent.mkdir(parents=True); template.write_bytes((ROOT / template.relative_to(self.root)).read_bytes())
        macro = self.root / 'macros/ExportExternalFlowResults.java'; macro.parent.mkdir(); macro.write_text('existing macro')
        parent_candidate = self.root / 'certified-parent/candidate.json'
        self.write(parent_candidate, {'source_kind': 'successful_solve_checkpoint'})
        with patch('run_sensitivity_study.checkpoint_preprocessing', return_value={
                'verified_dependency_paths': [str(parent_candidate)]}):
            result = adapter.prepare_solved_readback_candidate(self.source, self.spec, self.workflow, audit, self.contract, self.evidence)
        manifest = api.load(result['manifest']); pipeline = api.load(self.root / manifest['pipeline'])
        self.assertEqual(manifest['star_seconds_cap'], 60)
        self.assertEqual(pipeline['input_hashes']['case_spec.json'], adapter.digest(self.old_spec))
        # 2026-10-15 归因：模板已由 remediation-20261008 研究轮升级为**已审查的诊断契约**
        # （env 增加 STATIC_FLOW_DIAGNOSTICS；required 增加 9 个诊断产物）。旧断言只认基础变体，
        # 与生产码的 valid_readback_environment_artifacts 支持的两个精确变体不一致。
        # 这里改为钉住**当前模板所属的那个精确变体**，仍不允许任何第三种 env/产物组合。
        self.assertEqual(pipeline['env'], {'PROPERTY_NEARWALL_READBACK_ONLY': '1',
                                           'STATIC_FLOW_DIAGNOSTICS': '1'})
        self.assertTrue(adapter.valid_readback_environment_artifacts(pipeline))
        self.assertEqual(set(pipeline['stages'][0]['required']),
                         set(adapter.READBACK_DIAGNOSTIC_ARTIFACTS))
        self.assertNotEqual(set(pipeline['stages'][0]['required']),
                            set(adapter.READBACK_BASE_ARTIFACTS))
        self.assertEqual(len(pipeline['stages']), 1)
        stage = pipeline['stages'][0]
        self.assertEqual(stage['macros'], ['ExportExternalFlowResults.java'])
        self.assertEqual(stage['macro_outputs'], [None]); self.assertIsNone(stage['output'])
        self.assertEqual(stage['timeout_seconds'], 60)
        # Exercise the actual submission boundary after the helper's consumed dependency is pinned.
        manifest_path = Path(result['manifest'])
        for relative in manifest['adapter_files']:
            path = self.root / relative
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture support bytes')
        self.write(self.evidence / 'pipeline-review.json', {'approved': True, 'independent': True,
            'registration_hashes': {relative: adapter.digest(self.root / relative)
                for relative in manifest['adapter_files'] + [str(manifest_path.relative_to(self.root))]}})
        with patch.object(api, 'ROOT', self.root):
            api.checked_registration(self.workflow)
            self.write(self.evidence / 'registration.json', {'state': 'REGISTERED_CURRENT_BYTES'})
            with patch.object(api.legacy, 'list_registered_workflows', return_value=[]), \
                    patch.object(api, 'historical_registration', return_value=(manifest, self.evidence, manifest_path)):
                listing = api.list_registered_workflows()
            self.assertEqual(listing[0]['id'], self.workflow)
            self.assertTrue(listing[0]['current_adapter_review_matches'])
            self.write(parent_candidate, {'source_kind': 'step_cad'})
            with self.assertRaisesRegex(ValueError, 'Reviewed registration bytes changed'):
                api.checked_registration(self.workflow)


class ReleaseEngineeringReadMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve()
        self.patch = patch.object(api, 'ROOT', self.root); self.patch.start()
        self.legacy_patch = patch.object(api.legacy, 'ROOT', self.root); self.legacy_patch.start()
        self.workflow = 'old-execution'; self.run = 'old-execution-0001'
        self.evidence = self.root / 'test-evidence/old'
        self.manifest_path = self.root / 'registered-workflows-v2' / self.workflow / 'manifest.json'
        self.inputs = {'source.sim': 'sourcehash', 'case_spec.json': 'spechash'}
        self.manifest = {'id': self.workflow, 'mechanism': 'restricted_trial',
            'mcp_evidence': 'test-evidence/old', 'source_input_name': 'source.sim',
            'presentation_entry': 'present.py', 'adapter_files': ['pipeline.json','output-contract.json'], 'pipeline': 'pipeline.json'}
        self.write(self.manifest_path, self.manifest)
        for name in ['pipeline.json', 'output-contract.json', 'present.py', 'scripts/stage_plan.py',
            'scripts/air3b_pipeline_trial.py', 'scripts/registered_workflow_mcp_v2.py',
            'scripts/registered_workflow_mcp.py', 'scripts/run_registered_workflow_v2.py', 'scripts/server_v2.py']:
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('current bytes')
        self.review_path = self.evidence / 'pipeline-review.json'
        self.review = {'approved': True, 'independent': True, 'registered_request_ids': [self.run],
            'pipeline_sha256': 'oldpipeline', 'controller_sha256': 'oldcontroller', 'runner_sha256': 'oldrunner',
            'macro_hashes': {'Solve.java': 'oldmacro'}, 'input_hashes': self.inputs,
            'support_hashes': {'scripts/stage_plan.py': 'oldstageplan'},
            'registration_hashes': {'pipeline.json': 'oldpipeline', 'present.py': 'oldpresent', 'output-contract.json': 'oldcontract'}}
        self.write(self.review_path, self.review)
        self.write(self.evidence / 'registration.json', {'state': 'REGISTERED_CURRENT_BYTES',
            'workflow_id': self.workflow, 'manifest_sha256': api.digest(self.manifest_path),
            'review_sha256': api.digest(self.review_path), 'input_hashes': self.inputs})
        self.handle = {'run_id': self.run, 'intent': {'workflow_id': self.workflow,
            'source_sha256': 'sourcehash', 'case_spec_sha256': 'spechash'},
            'existing_start_result': {k: self.review[k] for k in ['pipeline_sha256','controller_sha256',
                'runner_sha256','macro_hashes','input_hashes','support_hashes']}}
        self.handle['existing_start_result']['trial_id'] = self.run
        self.handle_path = self.evidence / 'mcp-handles' / (self.run + '.json'); self.write(self.handle_path, self.handle)
        required = set(self.review['support_hashes']) | {'scripts/air3b_pipeline_trial.py',
            'scripts/registered_workflow_mcp_v2.py','scripts/registered_workflow_mcp.py',
            'scripts/run_registered_workflow_v2.py','scripts/server_v2.py','present.py','output-contract.json'}
        self.migration = {'approved': True, 'independent': True, 'execution_authorized': False,
            'registered_request_ids': [], 'allowed_actions': ['query','fetch'],
            'historical_review_sha256': api.digest(self.review_path),
            'handle_hashes': {self.run: api.digest(self.handle_path)},
            'current_reader_hashes': {name: api.digest(self.root / name) for name in required}}
        self.migration_path = self.evidence / 'historical-read-review.json'
        self.write(self.migration_path, self.migration)

    def tearDown(self): self.legacy_patch.stop(); self.patch.stop(); self.temp.cleanup()
    def write(self, path, value): path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value))

    def test_exact_read_migration_accepts_history_but_does_not_allow_submit(self):
        api.historical_registration(self.workflow, self.handle)
        with patch.object(api, 'invoke') as launch:
            with self.assertRaisesRegex(ValueError, 'Reviewed registration bytes changed'):
                api.submit_registered_workflow(self.workflow, 'source.sim', 'case_spec.json', self.run)
            launch.assert_not_called()

    def test_read_migration_cannot_authorize_submit_action_or_request_allocation(self):
        for field, value in [('allowed_actions', ['query','fetch','submit']),
                             ('registered_request_ids', [self.run]), ('execution_authorized', True)]:
            changed = dict(self.migration); changed[field] = value; self.write(self.migration_path, changed)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'read-only migration'):
                api.historical_registration(self.workflow, self.handle)

    def test_read_migration_cannot_ignore_changed_reader_or_handle(self):
        (self.root / 'scripts/stage_plan.py').write_text('unreviewed reader bytes')
        with self.assertRaisesRegex(ValueError, 'read-only migration'):
            api.historical_registration(self.workflow, self.handle)
        (self.root / 'scripts/stage_plan.py').write_text('current bytes')
        self.handle_path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'read-only migration'):
            api.historical_registration(self.workflow, self.handle)

    def test_read_migration_must_pin_current_output_contract(self):
        self.migration['current_reader_hashes'].pop('output-contract.json')
        self.write(self.migration_path, self.migration)
        with self.assertRaisesRegex(ValueError, 'read-only migration'):
            api.historical_registration(self.workflow, self.handle)

    def test_cylinder_presentation_migration_pins_imported_study_readers(self):
        self.manifest['presentation_entry'] = 'cases/static-cylinder-step/render_report.py'
        names = [self.manifest['presentation_entry'], 'scripts/run_sensitivity_study.py',
                 'scripts/evaluate_sensitivity.py', 'scripts/grid_convergence.py']
        for name in names:
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('current bytes')
            self.migration['current_reader_hashes'][name] = api.digest(path)
        self.write(self.migration_path, self.migration)
        api.legacy.verify_historical_reader_review(self.manifest, self.evidence, self.handle, self.review)
        (self.root / 'scripts/run_sensitivity_study.py').write_text('unreviewed changed lineage reader')
        with self.assertRaisesRegex(ValueError, 'read-only migration'):
            api.legacy.verify_historical_reader_review(self.manifest, self.evidence, self.handle, self.review)

class ReleaseIndependentLineageSchemaTests(unittest.TestCase):
    def test_real_review_manifest_pin_schema_is_accepted(self):
        import test_sensitivity_study as sensitivity_tests
        from unittest.mock import patch
        driver = sensitivity_tests.driver
        with tempfile.TemporaryDirectory(dir=ROOT / 'test-evidence') as tmp:
            root, _, _ = sensitivity_tests.CheckpointStudyLineage().fixture(tmp)
            review_path = root / 'pipeline-review.json'
            review = json.loads(review_path.read_text())
            review.pop('manifest_sha256', None)
            manifest_path = root / 'manifest.json'
            review['registration_hashes'][str(manifest_path.relative_to(ROOT))] = driver.sha256(manifest_path)
            review_path.write_text(json.dumps(review))
            result = driver.checkpoint_preprocessing(root, 'segment')
            self.assertFalse(result['engineering_conclusion_reused'])

class ReleaseIndependentFiniteCheckpointPlanTests(unittest.TestCase):
    workflow = 'static-eng-u12-segment1-20261007'
    def setUp(self):
        self.manifest, self.evidence, _ = api.registration(self.workflow)
        self.candidate = api.load(self.evidence / 'candidate.json')
        self.spec = api.load(self.candidate['case_spec_path'])
        self.pipeline = api.load(ROOT / self.manifest['pipeline'])

    def pinned_macro_bytes(self):
        """Isolate the "registered macro bytes are still the live bytes" gate for the SHAPE tests.

        2026-10-15（全量回归逐条归因）：`validate_registered_workflow_plan` 第 58 行按 `pipeline['macro_hashes']`
        逐字节比对宏，任一不符即抛 `Base macro bytes differ from actual executable call`。**这条门是对的**：
        本注册所钉的三个宏（RunExternalFlowSolve / AuditExternalFlowConvergence / ExportExternalFlowResults）
        被后续已授权轮次修过，于是这份历史注册**不得**再执行——loud refusal 正是 §6 五重哈希门要求的。
        本类下面两条测试考的是 checkpoint 分支的**形状**（stage 集合、source_input_name、cap、operation 列表），
        与字节新鲜度无关；因此只在宏路径上让 digest 报出该 pipeline 自己钉的值，其余 digest 全部走真实实现。
        “真实注册已被拒绝、且拒绝可归因到已登记的 supersession”由
        test_real_stale_registration_is_refused_with_a_recorded_reason 单独判定——判据没有被放宽。
        """
        pinned = dict(self.pipeline['macro_hashes'])
        original = api.digest
        macros = (ROOT / 'macros').resolve()

        def digest(path):
            resolved = Path(path).resolve()
            if resolved.parent == macros:
                return pinned.get(resolved.name, original(path))
            return original(path)

        return patch.object(api, 'digest', side_effect=digest)

    def test_real_stale_registration_is_refused_with_a_recorded_reason(self):
        """A post-registration macro repair must refuse the old registration, loudly and attributably."""
        from supersession_ledger import documented_after
        documented = documented_after(ROOT / 'test-evidence')
        mismatched = {name: pinned for name, pinned in self.pipeline['macro_hashes'].items()
                      if api.digest(ROOT / 'macros' / name) != pinned}
        self.assertTrue(mismatched, 'this real registration is expected to be stale by now')
        for name, pinned in mismatched.items():
            live = api.digest(ROOT / 'macros' / name)
            recorded = documented.get('macros/' + name, set()) | documented.get(name, set())
            self.assertIn(live, recorded,
                          'macro drifted with no recorded supersession: ' + name)
            self.assertEqual(64, len(pinned))
        with self.assertRaisesRegex(ValueError, 'Base macro bytes differ from actual executable call'):
            api.validate_registered_workflow_plan(self.workflow, self.spec)

    def test_real_checkpoint_source_key_validates_without_geometry_key(self):
        self.assertNotIn('static_body_geometry.sim', self.candidate['input_hashes'])
        with self.pinned_macro_bytes():
            result = api.validate_registered_workflow_plan(self.workflow, self.spec)
        self.assertTrue(result['call_plan_executable'])
        self.assertEqual(result['required_STAR_cap_seconds'], 1200)
        self.assertFalse(result['complete_engineering_plan'])
        self.assertFalse(result['star_started'])
        self.assertFalse(result['engineering_verified'])
    def test_checkpoint_cannot_claim_full_studies_or_omit_validation(self):
        with self.pinned_macro_bytes():
            for operations in [adapter.PLAN + ['mesh_study', 'domain_study'], ['run_external_flow_solve']]:
                result = api.validate_registered_workflow_plan(self.workflow, self.spec, operations)
                self.assertFalse(result['call_plan_executable'])
                self.assertFalse(result['submission_ready'])
    def test_real_checkpoint_changed_spec_is_refused(self):
        spec = json.loads(json.dumps(self.spec))
        spec['finite_execution']['expected_start_iteration'] = 0
        with self.assertRaisesRegex(ValueError, 'Selected CaseSpec differs'):
            api.validate_registered_workflow_plan(self.workflow, spec)
    def test_changed_checkpoint_bytes_refused_before_finite_branch(self):
        original = api.digest
        source = Path(self.candidate['source_sim']).resolve()
        pinned = dict(self.pipeline['macro_hashes'])
        macros = (ROOT / 'macros').resolve()

        def digest(path):
            resolved = Path(path).resolve()
            if resolved == source:
                return '0' * 64
            if resolved.parent == macros:
                # same isolation as pinned_macro_bytes: this test is about the SOURCE identity check
                return pinned.get(resolved.name, original(path))
            return original(path)

        with patch.object(api, 'digest', side_effect=digest):
            with self.assertRaisesRegex(ValueError, 'Candidate source'):
                api.validate_registered_workflow_plan(self.workflow, self.spec)

if __name__ == '__main__':
    unittest.main()
