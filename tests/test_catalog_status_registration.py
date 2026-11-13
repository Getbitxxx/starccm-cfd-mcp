"""Status registration must preserve history and cannot create execution approval."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('register_status', ROOT / 'docs/catalog-status-20261021/register_status_append.py')
registration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registration)


class StatusRegistration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'operations').mkdir()
        self.path = self.root / 'operations/production-catalog.json'
        self.source = {'id': 'original', 'version': '1', 'pipeline': 'pipeline.json', 'release_channel': 'v1',
                       'sample_specific': False, 'required_input_preflight': True, 'review_record': 'original-review.json'}
        self.original = json.dumps({'schema_version': 1, 'entries': [self.source], 'policy': 'unchanged'}, indent=2).encode()
        self.path.write_bytes(self.original)
        old_review = json.dumps({'approved': True, 'independent': True, 'approved_hashes': {'runtime_digest': 'a'*64}}).encode()
        (self.root / 'original-review.json').write_bytes(old_review)
        self.entry = dict(self.source, id='status-new', source_entry_id='original',
                          approved_for_autonomous_run=False, approved_hashes={}, maximum_submissions=0,
                          registered_request_ids=[], engineering_verified=False,
                          status='HISTORICAL_REVIEW_APPROVED_CURRENT_RUNTIME_NOT_APPROVED',
                          source_review_ref='original-review.json', source_review_sha256=registration.digest(old_review),
                          historical_runtime_digest='a'*64)
        self.candidate_path = self.root / 'candidate.json'
        self.approval_path = self.root / 'approval.json'
        self.receipt = self.root / 'receipt.json'

    def prepare(self, entry=None):
        candidate = {'previous_catalog_sha256': registration.digest(self.original), 'entries': [entry or self.entry]}
        self.candidate_path.write_text(json.dumps(candidate))
        self.approval_path.write_text(json.dumps({'approved_for_registration': True, 'independent': True,
            'scope': 'APPEND_HISTORICAL_REVIEW_STATUS_ONLY_NO_EXECUTION_AUTHORIZATION',
            'candidate_sha256': registration.digest(self.candidate_path.read_bytes()),
            'previous_catalog_sha256': candidate['previous_catalog_sha256']}))

    def register(self):
        return registration.register(self.root, self.candidate_path, self.approval_path, self.receipt)

    def test_append_preserves_prior_bytes_and_old_entries_and_records_chain(self):
        self.prepare()
        result = self.register()
        updated = self.path.read_bytes()
        close = self.original.index(b'\n  ],') + len(b'\n  ')
        self.assertEqual(self.original[:close], updated[:close])
        self.assertEqual(self.source, json.loads(updated)['entries'][0])
        self.assertEqual('unchanged', json.loads(updated)['policy'])
        self.assertEqual(registration.digest(self.original), result['before_sha256'])
        self.assertEqual(registration.digest(updated), result['after_sha256'])
        self.assertEqual(0, result['execution_authorizations_added'])
        self.assertTrue(self.receipt.with_suffix('.intent.json').is_file())
        with self.assertRaisesRegex(ValueError, 'stale or repeated'):
            self.register()

    def test_changed_candidate_or_catalog_is_refused_without_mutation(self):
        self.prepare()
        self.candidate_path.write_text(self.candidate_path.read_text() + ' ')
        with self.assertRaisesRegex(ValueError, 'approval'):
            self.register()
        self.assertEqual(self.original, self.path.read_bytes())
        self.prepare()
        self.path.write_bytes(self.original + b'\n')
        with self.assertRaisesRegex(ValueError, 'stale or repeated'):
            self.register()
        self.assertEqual(self.original + b'\n', self.path.read_bytes())

    def test_registration_refuses_new_execution_privileges_even_with_review(self):
        for field, value in [('approved_for_autonomous_run', True), ('maximum_submissions', 1),
                             ('registered_request_ids', ['new-run']), ('formal', {'approved_for_autonomous_run': True}),
                             ('approved_hashes', {'runner_sha256': 'b'*64}), ('engineering_verified', True)]:
            entry = copy.deepcopy(self.entry)
            entry[field] = value
            self.prepare(entry)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.register()
            self.assertEqual(self.original, self.path.read_bytes())

    def test_historical_review_tamper_and_duplicate_identity_are_refused(self):
        self.prepare()
        (self.root / 'original-review.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'review bytes'):
            self.register()
        duplicate = dict(self.entry, id='original')
        self.prepare(duplicate)
        with self.assertRaisesRegex(ValueError, 'replace an entry'):
            self.register()
        self.assertEqual(self.original, self.path.read_bytes())

    def test_change_during_temporary_write_is_preserved_and_publication_refused(self):
        self.prepare()
        real_fsync = registration.os.fsync
        calls = []
        external = self.original + b'\n'
        def fsync_and_interleave(descriptor):
            real_fsync(descriptor)
            calls.append(descriptor)
            if len(calls) == 2:
                self.path.write_bytes(external)
        with patch.object(registration.os, 'fsync', side_effect=fsync_and_interleave):
            with self.assertRaisesRegex(ValueError, 'changed while preparing'):
                self.register()
        self.assertEqual(external, self.path.read_bytes())
        self.assertFalse(self.receipt.exists())
        self.assertTrue(self.receipt.with_suffix('.intent.json').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
