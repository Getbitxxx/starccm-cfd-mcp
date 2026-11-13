"""Formal catalog admission: actual materializer and backend, without SSH or STAR."""
import json
import unittest
from unittest.mock import patch

from tests import test_production_catalog as catalog_tests
from tests.test_production_catalog import write, pc
import backend


class FormalCatalogTests(unittest.TestCase):
    def setUp(self):
        self.fixture = catalog_tests.CatalogTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root, self.entry = self.fixture.root, self.fixture.entry
        for name in pc.FORMAL_SUPPORT_FILES:
            if not (self.root/name).exists():
                write(self.root/name, 'support '+name)
        write(self.root/'worker/runtime_guard.ps1', '# guard')
        self.spec = {'case_ref': 'library/%s.sim' % pc.sha256(self.fixture.input), 'case_id': 'synthetic'}
        write(self.root/'spec.json', json.dumps(self.spec))
        manifest = {'schema_version': 1, 'id': 'synthetic', 'stages': {'one': {
            'macros': ['One.java'], 'markers': ['DONE'], 'required': ['one.json'],
            'postcheck': 'artifacts_v1', 'parameters': {'env': [{'name': 'TEST_VALUE'}]}}}}
        write(self.root/'workflows/synthetic/manifest.json', json.dumps(manifest))
        self.steps = [{'workflow_id': 'synthetic', 'stage': 'one', 'parameters': {'TEST_VALUE': '1'}}]
        f = dict(version='formal-1', approved_for_autonomous_run=True,
            review_record='formal-approval.json', input_ref_policy='library/<sha256>.sim',
            input_sha256=pc.sha256(self.fixture.input), input_source='input.sim',
            case_spec_source='spec.json', case_spec_digest=pc.canonical_digest(self.spec),
            steps=self.steps, plan_operations=['one'], preflight_record='preflight.json',
            workflow_manifests={'synthetic': 'workflows/synthetic/manifest.json'},
            pipeline_hashes={'synthetic': pc.sha256(self.root/'workflows/synthetic/manifest.json')},
            macro_hashes=self.fixture.pipeline['macro_hashes'],
            source_pipeline_sha256=pc.sha256(self.root/'pipeline.json'),
            support_hashes={n: pc.sha256(self.root/n) for n in pc.FORMAL_SUPPORT_FILES},
            **pc.formal_runtime_pins(self.root))
        for key, name in [('controller_sha256','scripts/backend.py'),
                          ('runner_sha256','worker/run_job.ps1'),
                          ('pipeline_runner_sha256','worker/run_pipeline.ps1'),
                          ('resource_contract_sha256','scripts/resource_contract.py')]:
            f[key] = pc.sha256(self.root/name)
        self.entry['formal'] = f
        self.sign()

    def sign(self):
        write(self.root/'formal-approval.json', json.dumps({'approved': True, 'independent': True,
            'formal_identity': pc.formal_identity(self.entry),
            'approved_hashes': {k: self.entry['formal'].get(k) for k in pc.FORMAL_HASH_FIELDS}}))
        write(self.root/'operations/production-catalog.json', json.dumps({'schema_version':1, 'entries':[self.entry]}))

    def materialize(self):
        return pc.materialize_formal(self.entry, self.spec['case_ref'], self.steps,
                                     self.spec, self.root/'evidence', self.root)

    def load(self, path):
        files = sorted([*(self.root/'macros').glob('*.java'), *(self.root/'worker').glob('*.ps1'),
            *(self.root/'workflows').glob('*/manifest.json'), self.root/'scripts/resource_contract.py'])
        # Production enumeration is the same; patch only fixture location and file inventory.
        with patch.object(backend, 'PLUGIN', self.root), \
             patch.object(backend.Backend, 'runtime_files', return_value=files), \
             patch.object(backend, 'build_runtime_manifest', side_effect=lambda fs, root=None: original_manifest(fs, self.root)):
            return backend.load_review_record(str(path), 'synthetic', 'one', self.spec['case_ref'],
                ['run_pipeline.ps1','run_job.ps1'], ['One.java'])

    def test_materialized_formal_review_is_accepted_by_backend(self):
        path = self.materialize()
        self.assertEqual(self.entry['formal']['pipeline_runner_sha256'], self.load(path)['pipeline_runner_sha256'])
        pc.validate_formal_submission(str(path), self.spec['case_ref'], self.steps, self.spec, ['one'], self.root)

    def test_missing_runner_manifest_input_and_macro_tampering_are_rejected(self):
        path = self.materialize()
        record = json.loads(path.read_text())
        changes = [('pipeline_runner_sha256', None), ('pipeline_hashes', {'synthetic':'0'*64}),
                   ('input_hashes', {'input.sim':'0'*64}), ('macro_hashes', {'One.java':'0'*64})]
        for key, value in changes:
            with self.subTest(key=key):
                changed = dict(record)
                if value is None: changed.pop(key)
                else: changed[key] = value
                write(path, json.dumps(changed))
                with self.assertRaises(ValueError): self.load(path)

    def test_actual_macro_or_support_drift_is_rejected(self):
        path = self.materialize()
        for name in ['macros/One.java', 'scripts/casespec.py']:
            with self.subTest(name=name):
                target = self.root/name
                old = target.read_text()
                write(target, old+' drift')
                with self.assertRaises(ValueError): self.load(path)
                write(target, old)

    def test_materializer_refuses_changed_actual_scope_and_overwrite(self):
        for steps, spec in [([], self.spec), (self.steps, dict(self.spec, case_id='changed')),
                            ([dict(self.steps[0], parameters={'TEST_VALUE':'2'})], self.spec)]:
            with self.assertRaises(ValueError):
                pc.materialize_formal(self.entry, self.spec['case_ref'], steps, spec, self.root/'evidence', self.root)
        self.materialize()
        with self.assertRaises(ValueError): self.materialize()

    def test_submission_rechecks_scope_and_lineage(self):
        path = self.materialize()
        for steps, spec, ops in [(self.steps, None, ['one']),
                                 (self.steps, dict(self.spec, changed=True), ['one']),
                                 ([], self.spec, ['one']), (self.steps, self.spec, []),
                                 ([dict(self.steps[0], parameters={'TEST_VALUE':'2'})], self.spec, ['one'])]:
            with self.assertRaises(ValueError):
                pc.validate_formal_submission(str(path), self.spec['case_ref'], steps, spec, ops, self.root)
        record = json.loads(path.read_text())
        for key in ['formal_catalog', 'approved_steps', 'case_spec_digest']:
            changed = dict(record); changed.pop(key)
            write(path, json.dumps(changed))
            with self.assertRaises(ValueError): self.load(path)

    def test_backend_submission_rejects_scope_before_remote_action(self):
        path = self.materialize()
        client = object.__new__(backend.Backend)
        stage = {'macros':['One.java'], 'parameters': {'env':[{'name':'TEST_VALUE'}]}}
        with patch.object(backend, 'PLUGIN', self.root), \
             patch.object(backend, 'workflow_catalog', return_value={'synthetic':{'stages':{'one':stage}}}), \
             patch.object(backend, 'stage_parameter_declarations', return_value={'TEST_VALUE':{}}), \
             patch.object(client, 'ps') as remote, patch.object(client, 'get_job') as job:
            with self.assertRaisesRegex(ValueError, 'approved scope'):
                client.submit_job('one', self.spec['case_ref'], 'formal-negative-001', workflow_id='synthetic',
                    case_spec=self.spec, review_ref=str(path), parameters={'TEST_VALUE':'1'})
            steps = [dict(self.steps[0], key='one', parameters={'TEST_VALUE':'2'})]
            with patch.object(backend, 'load_review_record', return_value={'macro_hashes':{'One.java':'0'*64}}):
                with self.assertRaises(ValueError):
                    client.submit_run(self.spec['case_ref'], 'formal-negative-002', steps=steps,
                        case_spec=self.spec, review_ref=str(path))
            remote.assert_not_called(); job.assert_not_called()

    def test_trial_approval_cannot_be_reinterpreted_as_formal(self):
        trial = pc.materialize(self.entry, [self.fixture.input], self.root/'trial', self.fixture.preflight, self.root)
        with self.assertRaisesRegex(ValueError, 'trial reviews'):
            pc.verify_formal_review_record(json.loads(trial.read_text()), self.root)

    def test_formal_sample_scope_cannot_expand_to_an_unlisted_case(self):
        self.entry.update(sample_specific=True, allowed_case_ids=['different-case'])
        self.sign()
        with self.assertRaisesRegex(ValueError, 'allowed_case_ids'):
            self.materialize()


original_manifest = backend.build_runtime_manifest
