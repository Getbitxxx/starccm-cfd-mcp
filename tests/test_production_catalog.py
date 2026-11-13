# -*- coding: utf-8 -*-
"""Tests for the pre-reviewed production catalog and runtime materialization."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import production_catalog as pc  # noqa: E402


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input = write(self.root / 'input.sim', 'sim bytes\n')
        self.pipeline = {'case_id': 'synthetic', 'input_hashes': {'input.sim': pc.sha256(self.input)},
                         'macro_hashes': {}, 'stages': [{'name': 'one', 'input': 'input.sim', 'output': None,
                          'macros': ['One.java'], 'marker': 'ONE_COMPLETE', 'required': []}]}
        write(self.root / 'pipeline.json', '{}')
        write(self.root / 'macros' / 'One.java', 'class One {}\n')
        write(self.root / 'scripts' / 'air3b_pipeline_trial.py', '# controller\n')
        write(self.root / 'scripts' / 'security' / 'trial_air3b_pipeline.ps1', '# runner\n')
        write(self.root / 'scripts' / 'resource_contract.py', '# contract\n')
        support = {}
        for relative in pc.SUPPORT_FILES:
            path = write(self.root / relative, 'support: %s\n' % relative)
            support[relative] = pc.sha256(path)
        self.pipeline['macro_hashes'] = {'One.java': pc.sha256(self.root / 'macros/One.java')}
        write(self.root / 'pipeline.json', json.dumps(self.pipeline))
        admission = {name: pc.sha256(write(self.root/name, 'admission '+name)) for name in pc.ADMISSION_FILES}
        self.source = write(self.root/'source/source.json', '{"status":"ok"}')
        write(self.root/'source/state.json', '{"state":"AIR3B_PIPELINE_COMPLETE"}')
        self.preflight_payload = {'status': 'ok', 'case_id': 'synthetic',
                                  'input_hashes': self.pipeline['input_hashes'],
                                  'source_records': [{'path': 'source/source.json', 'sha256': pc.sha256(self.source), 'context_path': 'source/state.json',
                                     'context_sha256': pc.sha256(self.root/'source/state.json')}]}
        self.preflight = write(self.root/'preflight.json', json.dumps(self.preflight_payload))
        self.entry = {
            'id': 'synthetic-op',
            'version': '1',
            'pipeline': 'pipeline.json',
            'scope': 'synthetic test only',
            'release_channel': 'v1',
            'sample_specific': False,
            'required_input_preflight': True,
            'approved_for_autonomous_run': True,
            'approved_hashes': {
                'pipeline_sha256': pc.sha256(self.root / 'pipeline.json'),
                'macro_hashes': {'One.java': pc.sha256(self.root / 'macros' / 'One.java')},
                'controller_sha256': pc.sha256(self.root / 'scripts' / 'air3b_pipeline_trial.py'),
                'runner_sha256': pc.sha256(self.root / 'scripts' / 'security'
                                           / 'trial_air3b_pipeline.ps1'),
                'support_hashes': {name: pc.sha256(self.root/name) for name in pc.SUPPORT_FILES},
                'admission_hashes': admission,
                'resource_contract_sha256': pc.sha256(self.root / 'scripts'
                                                      / 'resource_contract.py'),
            },
            'preflight_sha256': pc.sha256(self.preflight),
            'review_record': 'review.json',
            'notes': 'synthetic',
        }
        self.sign()

    def sign(self):
        write(self.root/'review.json', json.dumps({'approved': True, 'independent': True,
               'catalog_entry_identity': pc.entry_identity(self.entry), 'approved_hashes': self.entry['approved_hashes']}))

    def tearDown(self):
        self.temp.cleanup()

    def test_unapproved_entry_is_refused(self):
        entry = dict(self.entry, approved_for_autonomous_run=False)
        with self.assertRaises(pc.CatalogNotApproved):
            pc.verify_entry(entry, self.root)

    def test_approved_entry_verifies(self):
        checks = pc.verify_entry(self.entry, self.root)
        self.assertEqual(self.entry['approved_hashes']['pipeline_sha256'],
                         checks['pipeline_sha256'])

    def test_macro_drift_is_refused(self):
        write(self.root / 'macros' / 'One.java', 'class One { /* changed */ }\n')
        with self.assertRaises(pc.CatalogError):
            pc.verify_entry(self.entry, self.root)

    def test_sample_specific_requires_allowed_ids(self):
        entry = dict(self.entry, sample_specific=True)
        with self.assertRaises(pc.CatalogError):
            pc.validate_entry(entry)

    def test_materialize_writes_review_record(self):
        inputs = write(self.root / 'input.sim', 'sim bytes\n')
        preflight = self.preflight
        evidence = self.root / 'evidence'
        out = pc.materialize(self.entry, [inputs], evidence, preflight, root=self.root)
        record = json.loads(out.read_text(encoding='utf-8'))
        self.assertTrue(record['approved'])
        self.assertTrue(record['independent'])
        self.assertEqual('catalog:synthetic-op@1', record['reviewer'])
        self.assertEqual({'input.sim': pc.sha256(inputs)}, record['input_hashes'])
        self.assertEqual(self.entry['approved_hashes']['macro_hashes'], record['macro_hashes'])

    def test_materialize_refuses_without_required_preflight(self):
        inputs = write(self.root / 'input.sim', 'sim bytes\n')
        with self.assertRaises(pc.CatalogError):
            pc.materialize(self.entry, [inputs], self.root / 'evidence', None, root=self.root)

    def test_preflight_hash_mismatch_is_refused(self):
        inputs = write(self.root / 'input.sim', 'sim bytes\n')
        preflight = write(self.root / 'preflight.json',
                          json.dumps({'status': 'ok',
                                      'input_hashes': {'input.sim': '0' * 64}}))
        with self.assertRaises(pc.CatalogError):
            pc.materialize(self.entry, [inputs], self.root / 'evidence', preflight,
                           root=self.root)

    def test_empty_catalog_validates(self):
        catalog = write(self.root / 'catalog.json',
                        json.dumps({'schema_version': 1, 'entries': []}))
        self.assertEqual([], pc.load_catalog(catalog)['entries'])


class IndependentCounterexamples(CatalogTests):
    def test_review_record_must_exist_and_be_independently_approved(self):
        (self.root / 'review.json').unlink()
        with self.assertRaises(pc.CatalogError):
            pc.verify_entry(self.entry, self.root)

    def test_false_review_cannot_grant_authority(self):
        write(self.root / 'review.json', '{"approved": false, "independent": false}')
        with self.assertRaises(pc.CatalogError):
            pc.verify_entry(self.entry, self.root)

    def test_review_hashes_must_bind_exact_catalog_approval(self):
        write(self.root / 'review.json', '{"approved": true, "independent": true, "approved_hashes": {}}')
        with self.assertRaises(pc.CatalogError):
            pc.verify_entry(self.entry, self.root)

    def test_stage_macros_must_be_covered_by_approval(self):
        pipeline = {'input_hashes': {'input.sim': '0' * 64}, 'macro_hashes': {'One.java': pc.sha256(self.root/'macros/One.java')},
                    'stages': [{'name': 'test', 'macros': ['One.java'], 'input': 'input.sim', 'output': None, 'required': []}]}
        write(self.root / 'pipeline.json', json.dumps(pipeline))
        self.entry['approved_hashes']['pipeline_sha256'] = pc.sha256(self.root / 'pipeline.json')
        self.entry['approved_hashes']['macro_hashes'] = {}
        self.sign()
        with self.assertRaises(pc.CatalogError):
            pc.verify_entry(self.entry, self.root)

    def test_preflight_must_have_input_binding(self):
        inputs = write(self.root / 'input.sim', 'sim bytes\n')
        preflight = write(self.root / 'preflight.json', '{"status": "ok"}')
        self.entry['preflight_sha256'] = pc.sha256(preflight)
        self.sign()
        with self.assertRaises(pc.CatalogError):
            pc.materialize(self.entry, [inputs], self.root/'ev', preflight, self.root)

    def test_preflight_from_failed_stage_is_not_success(self):
        write(self.root/'source/state.json', '{"state":"FAILED"}')
        with self.assertRaises(pc.CatalogError):
            pc.materialize(self.entry, [self.input], self.root/'ev', self.preflight, self.root)

    def test_sample_scope_must_be_enforced(self):
        self.entry.update(sample_specific=True, allowed_case_ids=['air-three-blade'])
        self.preflight_payload['case_id']='synthetic'
        self.preflight.write_text(json.dumps(self.preflight_payload))
        self.entry['preflight_sha256']=pc.sha256(self.preflight)
        self.sign()
        with self.assertRaisesRegex(pc.CatalogError,'outside the catalog sample scope'):
            pc.materialize(self.entry,[self.input],self.root/'ev',self.preflight,self.root)

    def test_preflight_context_byte_drift_is_refused(self):
        write(self.root/'source/state.json','{"state":"AIR3B_PIPELINE_COMPLETE","changed":true}')
        with self.assertRaisesRegex(pc.CatalogError,'context hash drift'):
            pc.materialize(self.entry,[self.input],self.root/'ev',self.preflight,self.root)

    def test_admission_tool_drift_is_refused(self):
        write(self.root/'scripts/production_catalog.py','tampered materializer')
        with self.assertRaises(pc.CatalogError):
            pc.verify_entry(self.entry,self.root)

    def test_approval_must_be_literal_boolean_true(self):
        self.entry['approved_for_autonomous_run'] = 'false'
        with self.assertRaises(pc.CatalogError):
            pc.validate_entry(self.entry)

class TimePolicyCounterexamples(unittest.TestCase):
    def test_resource_contract_cannot_reject_for_wall_clock_relations(self):
        import resource_contract as rc
        pipeline = {'logical_cores': 64, 'memory_gb': 64, 'task_budget_seconds': 120,
                    'resource_contract': {'schema_version': 1, 'logical_cores': 64, 'memory_gb': 64, 'memory_reserve_gb': 16, 'outer_timeout_seconds': 60},
                    'stages': [{'timeout_seconds': 60}]}
        rc.validate_pipeline_resources(pipeline)


if __name__ == '__main__':
    unittest.main(verbosity=2)
