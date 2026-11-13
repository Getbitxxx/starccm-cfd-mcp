"""Preserve the real PAT-18b false rejection; no SSH, submission, or approval changes."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import production_catalog as pc


class RealNumericTransportRefusal(unittest.TestCase):
    def test_historical_refusal_is_preserved_and_semantic_v1_accepts_equal_numbers(self):
        fixture = json.loads((ROOT / 'tests/fixtures/formal-numeric-transport-refusal-20261019.json').read_text())
        evidence = ROOT / fixture['evidence_dir']
        for name, expected in fixture['evidence_hashes'].items():
            self.assertEqual(expected, hashlib.sha256((evidence / name).read_bytes()).hexdigest())
        request = json.loads((evidence / 'REQUEST-TRANSPORT.json').read_text())
        diagnostic = json.loads((evidence / 'SCOPE-MISMATCH-DIAGNOSTIC.json').read_text())
        source = json.loads((ROOT / fixture['source_case_spec']).read_text())
        self.assertEqual(fixture['request_id'], request['request_id'])
        self.assertEqual(source, request['case_spec'])
        self.assertEqual(fixture['source_digest'], pc.canonical_digest(source))
        self.assertEqual(fixture['transport_digest'], pc.canonical_digest(request['case_spec']))
        self.assertNotEqual(pc.canonical_digest(source), pc.canonical_digest(request['case_spec']))
        self.assertEqual(fixture['typed_difference_count'], len(diagnostic['typed_differences']))
        for item in diagnostic['typed_differences']:
            value = source
            transported = request['case_spec']
            for part in item['path'].strip('/').split('/'):
                value = value[int(part)] if isinstance(value, list) else value[part]
                transported = transported[int(part)] if isinstance(transported, list) else transported[part]
            self.assertIs(type(value), float)
            self.assertIs(type(transported), int)
            self.assertEqual(value, transported)
        for key in ('case_ref_equal', 'steps_equal', 'operations_equal'):
            self.assertIs(diagnostic[key], True)
        self.assertEqual(pc.canonical_digest(source, 'semantic-v1'),
                         pc.canonical_digest(request['case_spec'], 'semantic-v1'))
        # Replay the old numeric predicate separately from today's current catalog lineage.
        with patch.object(pc,'verify_formal_review_record',return_value=True):
            with self.assertRaisesRegex(pc.CatalogError, 'approved scope'):
                pc.validate_formal_submission(request['review_ref'], request['case_ref'],
                    diagnostic['normalized_steps'], request['case_spec'], request['operations'], ROOT)
        submitted = json.loads((evidence / 'SUBMIT.json').read_text())
        self.assertIs(submitted['isError'], True)
        self.assertIn('approved scope', submitted['content'][0]['text'])
        slot = json.loads((evidence / 'SLOT-AFTER.json').read_text())
        for key in ('run_exists', 'job_exists', 'task_exists'):
            self.assertIs(slot[key], False)
        self.assertEqual(0, slot['star_count'])
        self.assertEqual(0, slot['running_managed_tasks'])
