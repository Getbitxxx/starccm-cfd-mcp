"""Guard the curated knowledge index against unsourced claims.

Why this exists: the project's most expensive findings (mesh gates, API probes) used to
live only in per-run evidence directories, so a later session either re-ran the work or
cited a number with no path. This test makes a `project_measurement` entry impossible to
add without (a) a validation label that starts with `measured` and (b) evidence paths that
actually exist on disk. External index entries keep their weaker, honest labels.
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / 'knowledge' / 'index.json'


class KnowledgeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.entries = json.loads(INDEX.read_text(encoding='utf-8'))['entries']

    def test_entries_have_unique_titles_and_required_fields(self):
        titles = [e['title'] for e in self.entries]
        self.assertEqual(len(titles), len(set(titles)))
        for entry in self.entries:
            with self.subTest(title=entry['title']):
                self.assertTrue(entry['title'].strip())
                self.assertTrue(entry['tags'].strip())
                self.assertTrue(entry['source'].strip())
                self.assertTrue(entry['validation'].strip())

    def test_project_measurements_cite_existing_evidence(self):
        measured = [e for e in self.entries if e['source'] == 'project_measurement']
        self.assertGreaterEqual(len(measured), 9, 'measured entries must not disappear silently')
        for entry in measured:
            with self.subTest(title=entry['title']):
                self.assertTrue(entry['validation'].startswith('measured'),
                                'a project measurement must be labelled measured*')
                evidence = entry.get('evidence') or []
                self.assertTrue(evidence, 'a project measurement must cite its evidence')
                for relative in evidence:
                    path = ROOT / relative
                    self.assertTrue(path.is_file(), 'missing evidence file: ' + relative)

    def test_external_index_entries_do_not_overclaim(self):
        # Regression: a login-gated page may only be cited by title and link.
        for entry in self.entries:
            if entry.get('access') == 'login_required':
                with self.subTest(title=entry['title']):
                    self.assertEqual(entry['validation'], 'title_and_link_only')

    def test_measured_entries_do_not_claim_a_gate_passed(self):
        # A waiver or a failing arm is a measurement, not a pass. Keep the wording honest.
        for entry in self.entries:
            if entry['source'] != 'project_measurement':
                continue
            body = entry.get('description', '')
            with self.subTest(title=entry['title']):
                self.assertNotIn('已通过全部', body)
                self.assertNotIn('Q3 成立', body)


if __name__ == '__main__':
    unittest.main()
