# -*- coding: utf-8 -*-
"""Tests for the product acceptance gate (E label + PAT release gate).

These tests do not need STAR. They prove that the gate distinguishes:
  * a completed run from an honest negative,
  * an honest negative from a false pass,
  * a fail-closed case from a success,
  * a missing v1 case from a pass,
and that a stale PAT result cannot release a package.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import release_gate  # noqa: E402
import run_pat  # noqa: E402


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def case(cid='CASE-1', expected='SUCCESS', channel='v1', required=True, evidence=None,
         artifacts=None, label=None):
    item = {
        'id': cid,
        'scenario': '1_static_external_flow',
        'title': cid,
        'release_channel': channel,
        'required_for_release': required,
        'expected': expected,
        'evidence': evidence,
        'required_artifacts': artifacts or [],
    }
    if expected == 'HONEST_NEGATIVE':
        item.update(label or {'label_record': 'label.json', 'label_key': 'converged',
                              'label_expected': False})
    return item


def pat(cases):
    return {'schema_version': 1, 'release': 'test-release', 'cases': cases}


def probe(directory: Path, state='AIR3B_PIPELINE_COMPLETE', stages=None, error=None,
          engineering_verified=False):
    payload = {'state': state, 'engineering_verified': engineering_verified}
    if stages is not None:
        payload['stages'] = [{'name': name, 'status': status} for name, status in stages]
    if error is not None:
        payload['error'] = error
    write_json(directory / 'case-probe.json', payload)


class PatSchemaTests(unittest.TestCase):
    def test_real_pat_is_valid(self):
        real = json.loads((ROOT / 'test-evidence' / 'product-acceptance'
                           / 'PAT-20261018.json').read_text(encoding='utf-8'))
        run_pat.validate_pat(real)

    def test_duplicate_ids_are_rejected(self):
        with self.assertRaises(run_pat.PatError):
            run_pat.validate_pat(pat([case('X'), case('X')]))

    def test_unknown_expected_is_rejected(self):
        bad = case()
        bad['expected'] = 'LOOKS_GOOD'
        with self.assertRaises(run_pat.PatError):
            run_pat.validate_pat(pat([bad]))

    def test_honest_negative_requires_label(self):
        bad = case(expected='HONEST_NEGATIVE')
        for key in ('label_record', 'label_key', 'label_expected'):
            bad.pop(key)
        with self.assertRaises(run_pat.PatError):
            run_pat.validate_pat(pat([bad]))


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_success(self):
        evidence = self.root / 'run'
        probe(evidence, stages=[('audit', 'OK'), ('solve', 'OK')])
        write_json(evidence / 'validation.json', {'status': 'ok'})
        item = case(evidence=str(evidence), artifacts=['validation.json'])
        result = run_pat.classify(self.root, item)
        self.assertEqual('PASS', result['result'], result['detail'])

    def test_honest_negative(self):
        evidence = self.root / 'run'
        probe(evidence, stages=[('solve', 'OK'), ('validation', 'OK')])
        write_json(evidence / 'label.json', {'converged': False})
        item = case(expected='HONEST_NEGATIVE', evidence=str(evidence))
        result = run_pat.classify(self.root, item)
        self.assertEqual('PASS', result['result'], result['detail'])

    def test_label_mismatch_is_not_a_pass(self):
        evidence = self.root / 'run'
        probe(evidence, stages=[('solve', 'OK')])
        write_json(evidence / 'label.json', {'converged': True})
        item = case(expected='HONEST_NEGATIVE', evidence=str(evidence))
        result = run_pat.classify(self.root, item)
        self.assertEqual('LABEL_MISMATCH', result['result'], result['detail'])

    def test_fail_closed(self):
        evidence = self.root / 'run'
        probe(evidence, state='FAILED', error='geometry audit refused')
        item = case(expected='FAIL_CLOSED', evidence=str(evidence))
        result = run_pat.classify(self.root, item)
        self.assertEqual('PASS', result['result'], result['detail'])

    def test_expected_fail_closed_but_completed_is_wrong_outcome(self):
        evidence = self.root / 'run'
        probe(evidence, stages=[('audit', 'OK')])
        item = case(expected='FAIL_CLOSED', evidence=str(evidence))
        result = run_pat.classify(self.root, item)
        self.assertEqual('WRONG_OUTCOME', result['result'], result['detail'])

    def test_engineering_verified_true_is_a_false_pass(self):
        evidence = self.root / 'run'
        probe(evidence, stages=[('solve', 'OK')])
        write_json(evidence / 'validation.json', {'engineering_verified': True})
        item = case(evidence=str(evidence))
        result = run_pat.classify(self.root, item)
        self.assertEqual('FALSE_PASS', result['result'])
        self.assertTrue(result['false_pass'])

    def test_missing_artifact_is_not_a_pass(self):
        evidence = self.root / 'run'
        probe(evidence, stages=[('solve', 'OK')])
        item = case(evidence=str(evidence), artifacts=['missing.json'])
        result = run_pat.classify(self.root, item)
        self.assertEqual('MISSING_ARTIFACTS', result['result'])

    def test_no_evidence_is_not_run(self):
        result = run_pat.classify(self.root, case(evidence=None))
        self.assertEqual('NOT_RUN', result['result'])


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pat_path = self.root / 'PAT.json'
        self.result_path = self.root / 'PAT-RESULT.json'

    def tearDown(self):
        self.temp.cleanup()

    def run_gate(self, cases, results, pat_payload=None):
        definition = pat_payload or pat(cases)
        write_json(self.pat_path, definition)
        v1_ids = {c['id'] for c in cases if c['release_channel'] == 'v1'}
        beta_ids = {c['id'] for c in cases if c['release_channel'] == 'beta'}
        write_json(self.result_path, {
            'pat_sha256': run_pat.sha256(self.pat_path),
            'release': definition['release'],
            'v1_required_total': len(v1_ids),
            'v1_required_passed': sum(1 for r in results
                                      if r.get('id') in v1_ids and r.get('result') == 'PASS'),
            'beta_total': len(beta_ids),
            'beta_passed': sum(1 for r in results
                               if r.get('id') in beta_ids and r.get('result') == 'PASS'),
            'false_passes': [r['id'] for r in results if r.get('false_pass')],
            'cases': results,
        })
        return release_gate.main(['--pat', str(self.pat_path), '--result', str(self.result_path)])

    def test_all_v1_pass_releases(self):
        cases = [case('A'), case('B')]
        results = [{'id': 'A', 'result': 'PASS'}, {'id': 'B', 'result': 'PASS'}]
        self.assertEqual(0, self.run_gate(cases, results))

    def test_missing_v1_case_blocks(self):
        cases = [case('A'), case('B')]
        results = [{'id': 'A', 'result': 'PASS'}, {'id': 'B', 'result': 'NOT_RUN'}]
        self.assertEqual(1, self.run_gate(cases, results))

    def test_false_pass_blocks(self):
        cases = [case('A')]
        results = [{'id': 'A', 'result': 'PASS', 'false_pass': True,
                    'detail': 'engineering_verified=true'}]
        self.assertEqual(1, self.run_gate(cases, results))

    def test_beta_not_run_warns_but_releases(self):
        cases = [case('A'), case('B', channel='beta', required=False)]
        results = [{'id': 'A', 'result': 'PASS'}, {'id': 'B', 'result': 'NOT_RUN'}]
        self.assertEqual(0, self.run_gate(cases, results))

    def test_stale_result_blocks(self):
        cases = [case('A')]
        definition = pat(cases)
        write_json(self.pat_path, definition)
        write_json(self.result_path, {
            'pat_sha256': '0' * 64, 'release': definition['release'],
            'v1_required_total': 1, 'v1_required_passed': 1,
            'beta_total': 0, 'beta_passed': 0, 'false_passes': [],
            'cases': [{'id': 'A', 'result': 'PASS'}],
        })
        self.assertEqual(1, release_gate.main(['--pat', str(self.pat_path),
                                               '--result', str(self.result_path)]))


if __name__ == '__main__':
    unittest.main(verbosity=2)
