"""Deployed PAT counterexamples. All evidence is synthetic and no STAR is launched."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tests.test_product_acceptance import case, pat, probe, release_gate, run_pat, write_json

HISTORICAL = 'historical_evidence_readback'
DEPLOYED = 'deployed_worker_execution'
PREFIX = run_pat.DEPLOYED_EVIDENCE.as_posix()


class DeployedPatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pat_path = self.root / 'PAT-DEPLOYED-20261018.json'
        self.result_path = self.root / 'PAT-DEPLOYED-20261018-RESULT.json'

    def historical(self, cid):
        item = case(cid, evidence=PREFIX + '/history/' + cid)
        item.update(verification_basis=HISTORICAL, simulation_rerun=False)
        return item

    def fresh(self, include_full_chain=True):
        items = []
        for cid, expected, kind in [
                ('PAT-13-occupied-slot', 'FAIL_CLOSED', 'desktop_refusal'),
                ('PAT-14-bad-mesh', 'FAIL_CLOSED', 'threshold_injection'),
                ('PAT-16-checkpoint-delivery', 'SUCCESS', 'continuation'),
                ('PAT-17-deployed-smoke', 'SUCCESS', None)]:
            item = case(cid, expected=expected, evidence=PREFIX + '/' + cid)
            item['verification_basis'] = DEPLOYED
            if kind:
                item['test_kind'] = kind
            items.append(item)
        if include_full_chain:
            item = case('PAT-18-deployed-full-chain', evidence=PREFIX + '/fresh-full-chain')
            item.update(verification_basis=DEPLOYED, test_kind='full_chain')
            items.append(item)
        return items

    def definition(self, cases):
        return {**pat(cases), 'release_context': 'deployed'}

    def evaluate(self, cases):
        write_json(self.pat_path, self.definition(cases))
        for item in cases:
            if item['evidence'] is not None:
                failed = item['expected'] == 'FAIL_CLOSED'
                probe(self.root / item['evidence'], state='FAILED' if failed else 'SUCCEEDED',
                      stages=[('test', 'FAILED' if failed else 'OK')],
                      error='intentional refusal' if failed else None)
        result = run_pat.evaluate(self.pat_path, self.root)
        write_json(self.result_path, result)
        return result

    def gate(self, extra=()):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = release_gate.main(['--pat', str(self.pat_path), *extra])
        return code, output.getvalue()

    def test_all_historical_14_of_14_is_not_a_deployed_release(self):
        result = self.evaluate([self.historical('PAT-%02d-history' % i) for i in range(1, 15)])
        self.assertEqual(('PASS', 14, 14),
                         (result['overall'], result['v1_required_passed'], result['v1_required_total']))
        self.assertEqual({'total': 14, 'passed': 14}, result['evidence_identity'])
        self.assertEqual({'total': 0, 'passed': 0}, result['deployed_execution'])
        code, output = self.gate()
        self.assertEqual(1, code, output)
        for message in ('need at least 4', 'fresh success', 'fresh fail-closed', 'PAT-16'):
            self.assertIn(message, output)

    def test_complete_fresh_set_releases_and_shows_test_kinds(self):
        cases = [self.historical('PAT-01-history'), *self.fresh()]
        result = self.evaluate(cases)
        self.assertEqual({'total': 1, 'passed': 1}, result['evidence_identity'])
        self.assertEqual({'total': 5, 'passed': 5}, result['deployed_execution'])
        self.assertEqual([c.get('test_kind') for c in cases],
                         [c.get('test_kind') for c in result['cases']])
        code, output = self.gate()
        self.assertEqual(0, code, output)
        for message in ('证据身份: 1/1', '部署路径: 5/5', 'full_chain: 1/1',
                        'threshold_injection', 'continuation', 'desktop_refusal'):
            self.assertIn(message, output)

    def test_four_stage_cases_without_full_chain_are_blocked(self):
        result = self.evaluate(self.fresh(include_full_chain=False))
        self.assertEqual(('PASS', 4, 4),
                         (result['overall'], result['v1_required_passed'], result['v1_required_total']))
        code, output = self.gate()
        self.assertEqual(1, code, output)
        self.assertIn('missing passing required v1 full_chain', output)
        self.assertIn('full_chain: 0/0', output)

    def test_full_chain_not_pass_is_blocked(self):
        for outcome in ('NOT_RUN', 'WRONG_OUTCOME', 'FALSE_PASS'):
            with self.subTest(outcome=outcome):
                self.evaluate(self.fresh())
                result = json.loads(self.result_path.read_text())
                result['cases'][-1]['result'] = outcome
                write_json(self.result_path, result)
                code, output = self.gate()
                self.assertEqual(1, code, output)
                self.assertIn('missing passing required v1 full_chain', output)
                self.assertIn('full_chain: 0/1', output)

    def test_historical_full_chain_cannot_supply_deployed_coverage(self):
        cases = self.fresh()
        cases[-1].update(verification_basis=HISTORICAL, simulation_rerun=False,
                         evidence=PREFIX + '/history/historical-full-chain')
        self.evaluate(cases)
        code, output = self.gate()
        self.assertEqual(1, code, output)
        self.assertIn('full_chain: 0/1', output)
        self.assertIn('missing passing required v1 full_chain', output)

    def test_beta_or_optional_full_chain_cannot_supply_required_v1_coverage(self):
        for change in ({'release_channel': 'beta', 'required_for_release': False},
                       {'required_for_release': False}):
            with self.subTest(change=change):
                cases = self.fresh()
                cases[-1].update(change)
                self.evaluate(cases)
                code, output = self.gate()
                self.assertEqual(1, code, output)
                self.assertIn('missing passing required v1 full_chain', output)

    def test_result_cannot_relabel_a_stage_case_full_chain(self):
        self.evaluate(self.fresh())
        result = json.loads(self.result_path.read_text())
        result['cases'][0]['test_kind'] = 'full_chain'
        write_json(self.result_path, result)
        code, output = self.gate()
        self.assertEqual(1, code, output)
        self.assertIn('test_kind does not match', output)

    def test_historical_mode_does_not_require_full_chain(self):
        self.evaluate(self.fresh(include_full_chain=False))
        payload = json.loads(self.pat_path.read_text())
        del payload['release_context']
        self.replace_definition_without_stale_result(payload)
        code, output = self.gate()
        self.assertEqual(0, code, output)
        self.assertNotIn('full_chain:', output)

    def test_every_deployed_case_requires_a_legal_basis(self):
        for basis in (None, 'fresh', '', [], {}):
            for channel in ('v1', 'beta'):
                with self.subTest(basis=basis, channel=channel):
                    item = self.fresh()[0]
                    item['release_channel'] = channel
                    item['verification_basis'] = basis
                    with self.assertRaisesRegex(run_pat.PatError, 'verification_basis'):
                        run_pat.validate_pat(self.definition([item]), self.root)
        item = self.fresh()[0]
        del item['verification_basis']
        with self.assertRaisesRegex(run_pat.PatError, 'verification_basis'):
            run_pat.validate_pat(self.definition([item]), self.root)

    def test_gate_checks_missing_basis_even_if_result_was_forged(self):
        self.evaluate(self.fresh())
        payload = json.loads(self.pat_path.read_text())
        del payload['cases'][0]['verification_basis']
        self.replace_definition_without_stale_result(payload)
        code, output = self.gate()
        self.assertEqual(1, code, output)
        self.assertIn('verification_basis', output)

    def test_duplicate_cases_cannot_supply_the_fourth_execution(self):
        self.evaluate(self.fresh())
        payload = json.loads(self.pat_path.read_text())
        payload['cases'][3] = dict(payload['cases'][0])
        self.replace_definition_without_stale_result(payload)
        code, output = self.gate()
        self.assertEqual(1, code, output)
        self.assertIn('Duplicate PAT case id', output)

    def replace_definition_without_stale_result(self, payload):
        write_json(self.pat_path, payload)
        result = json.loads(self.result_path.read_text())
        result['pat_sha256'] = run_pat.sha256(self.pat_path)
        write_json(self.result_path, result)

    def test_historical_rerun_is_rejected_by_evaluator_and_gate(self):
        self.evaluate([self.historical('H'), *self.fresh()])
        payload = json.loads(self.pat_path.read_text())
        payload['cases'][0]['simulation_rerun'] = True
        with self.assertRaisesRegex(run_pat.PatError, 'simulation_rerun=false'):
            run_pat.validate_pat(payload, self.root)
        self.replace_definition_without_stale_result(payload)
        code, output = self.gate()
        self.assertEqual(1, code, output)
        self.assertIn('simulation_rerun=false', output)

    def test_historical_result_cannot_claim_rerun_against_definition(self):
        self.evaluate([self.historical('H'), *self.fresh()])
        result = json.loads(self.result_path.read_text())
        result['cases'][0]['simulation_rerun'] = True
        write_json(self.result_path, result)
        code, output = self.gate()
        self.assertEqual(1, code, output)
        self.assertIn('PAT-RESULT historical_evidence_readback requires simulation_rerun=false', output)

    def test_historical_evidence_cannot_be_relabelled_deployed(self):
        for path in ('test-evidence/old-run', PREFIX + '/history/copied-run',
                     PREFIX + '/../historical-run', PREFIX + '/smoke/../../old-run',
                     '/tmp/not-deployed'):
            with self.subTest(path=path):
                self.evaluate(self.fresh())
                payload = json.loads(self.pat_path.read_text())
                payload['cases'][0]['evidence'] = path
                with self.assertRaisesRegex(run_pat.PatError, 'deployed'):
                    run_pat.validate_pat(payload, self.root)
                self.replace_definition_without_stale_result(payload)
                code, output = self.gate()
                self.assertEqual(1, code, output)
                self.assertIn('deployed', output)

    def test_symlink_cannot_escape_deployed_directory(self):
        outside = self.root / 'old-run'
        outside.mkdir()
        alias = self.root / PREFIX / 'alias'
        alias.parent.mkdir(parents=True)
        alias.symlink_to(outside, target_is_directory=True)
        item = self.fresh()[0]
        item['evidence'] = PREFIX + '/alias'
        with self.assertRaisesRegex(run_pat.PatError, 'deployed_worker_execution evidence'):
            run_pat.validate_pat(self.definition([item]), self.root)

    def test_result_cannot_relabel_historical_evidence_or_forge_counters(self):
        self.evaluate([self.historical('H')])
        result = json.loads(self.result_path.read_text())
        result['cases'][0]['verification_basis'] = DEPLOYED
        result['deployed_execution'] = {'total': 4, 'passed': 4}
        write_json(self.result_path, result)
        code, output = self.gate()
        self.assertEqual(1, code, output)
        self.assertIn('verification_basis does not match', output)
        self.assertIn('部署路径: 0/0', output)

    def test_beta_execution_does_not_satisfy_v1_minimum(self):
        cases = self.fresh()
        cases.pop(3)  # Keep the passing full chain while checking the independent minimum of four.
        cases[0].update(release_channel='beta', required_for_release=False)
        self.evaluate(cases)
        code, output = self.gate()
        self.assertEqual(1, code, output)
        self.assertIn('need at least 4', output)

    def test_optional_v1_execution_does_not_satisfy_minimum(self):
        cases = self.fresh()
        cases.pop(3)
        cases[0]['required_for_release'] = False
        self.evaluate(cases)
        self.assertEqual(1, self.gate()[0])

    def test_four_fresh_cases_still_need_success_failure_and_checkpoint(self):
        for mutation, message in [('success', 'fresh success'), ('failure', 'fresh fail-closed'),
                                  ('checkpoint', 'PAT-16-checkpoint-delivery')]:
            with self.subTest(mutation=mutation):
                cases = self.fresh()
                if mutation == 'checkpoint':
                    cases[2]['id'] = 'OTHER-SUCCESS'
                else:
                    for item in cases:
                        item['expected'] = 'FAIL_CLOSED' if mutation == 'success' else 'SUCCESS'
                self.evaluate(cases)
                code, output = self.gate()
                self.assertEqual(1, code, output)
                self.assertIn(message, output)

    def test_failed_fresh_case_does_not_satisfy_minimum(self):
        cases = self.fresh()
        cases.pop(3)
        self.evaluate(cases)
        result = json.loads(self.result_path.read_text())
        result['cases'][0]['result'] = 'NOT_RUN'
        write_json(self.result_path, result)
        code, output = self.gate()
        self.assertEqual(1, code, output)
        self.assertIn('need at least 4', output)

    def test_null_beta_evidence_is_not_counted_and_require_beta_still_blocks(self):
        beta = case('PAT-15-restart', channel='beta', required=False)
        beta['verification_basis'] = DEPLOYED
        result = self.evaluate([*self.fresh(), beta])
        self.assertEqual('PASS', result['overall'])
        self.assertEqual({'total': 5, 'passed': 5}, result['deployed_execution'])
        self.assertEqual('NOT_RUN', result['cases'][-1]['result'])
        self.assertEqual(0, self.gate()[0])
        code, output = self.gate(['--require-beta'])
        self.assertEqual(1, code, output)
        self.assertIn('beta required', output)

    def test_stale_deployed_pat_and_false_pass_still_block(self):
        for mutation in ('stale', 'false_pass'):
            with self.subTest(mutation=mutation):
                self.evaluate(self.fresh())
                result = json.loads(self.result_path.read_text())
                if mutation == 'stale':
                    result['pat_sha256'] = '0' * 64
                else:
                    result['cases'][0]['false_pass'] = True
                write_json(self.result_path, result)
                self.assertEqual(1, self.gate()[0])

    def test_formal_smoke_uses_original_state_and_stage_contract(self):
        item = self.fresh(include_full_chain=False)[-1]
        item['required_artifacts'] = ['automation_preflight.json']
        evidence = self.root / item['evidence']
        write_json(evidence / 'state.json', {'state': 'SUCCEEDED', 'engineering_verified': False})
        write_json(evidence / 'stage-preflight.json', {'state': 'ok', 'stage': 'preflight'})
        write_json(evidence / 'automation_preflight.json', {'status': 'ok'})
        self.assertEqual('PASS', run_pat.classify(self.root, item)['result'])
        write_json(evidence / 'stage-preflight.json', {'state': 'FAILED'})
        self.assertEqual('WRONG_OUTCOME', run_pat.classify(self.root, item)['result'])
        write_json(evidence / 'stage-preflight.json', {'state': 'ok'})
        (evidence / 'automation_preflight.json').unlink()
        self.assertEqual('MISSING_ARTIFACTS', run_pat.classify(self.root, item)['result'])

    def test_default_stem_result_wins_over_stale_legacy(self):
        self.evaluate(self.fresh())
        legacy = self.root / 'PAT-RESULT.json'
        write_json(legacy, {'pat_sha256': '0' * 64, 'cases': []})
        before = legacy.read_bytes()
        code, output = self.gate()
        self.assertEqual(0, code, output)
        self.assertNotIn('falling back', output)
        self.assertEqual(before, legacy.read_bytes())

    def test_result_file_wins_over_stem_and_legacy(self):
        cases = self.fresh()
        self.evaluate(cases)
        payload = self.definition(cases)
        payload['result_file'] = 'selected-RESULT.json'
        write_json(self.pat_path, payload)
        selected = self.root / payload['result_file']
        write_json(selected, run_pat.evaluate(self.pat_path, self.root))
        for path in (self.result_path, self.root / 'PAT-RESULT.json'):
            write_json(path, {'pat_sha256': '0' * 64, 'cases': []})
        code, output = self.gate()
        self.assertEqual(0, code, output)
        self.assertNotIn('falling back', output)

    def test_legacy_fallback_warns_and_still_enforces_stale_check(self):
        self.evaluate(self.fresh())
        self.result_path.rename(self.root / 'PAT-RESULT.json')
        code, output = self.gate()
        self.assertEqual(0, code, output)
        self.assertIn('falling back to legacy', output)
        write_json(self.root / 'PAT-RESULT.json', {'pat_sha256': '0' * 64, 'cases': []})
        code, output = self.gate()
        self.assertEqual(1, code, output)
        self.assertIn('stale', output)

    def test_explicit_missing_result_does_not_fallback(self):
        self.evaluate(self.fresh())
        code, output = self.gate(['--result', str(self.root / 'missing.json')])
        self.assertEqual(1, code, output)
        self.assertIn('result missing', output)
        self.assertNotIn('falling back', output)

    def test_existing_repository_evidence_is_unchanged_when_full_chain_is_added(self):
        root = Path(__file__).resolve().parents[1]
        path = root / 'test-evidence/product-acceptance/PAT-DEPLOYED-20261018.json'
        payload = json.loads(path.read_text())
        # The prior 17 cases retain their own acceptance; PAT-18's real outcome is independent.
        payload['cases'] = [item for item in payload['cases'] if item['id'] != 'PAT-18-deployed-full-chain']
        baseline = self.root / 'PAT-PRIOR-17.json'
        write_json(baseline, payload)
        result = run_pat.evaluate(baseline, root)
        self.assertEqual(('PASS', 15, 15, 1, 2, []),
                         (result['overall'], result['v1_required_passed'], result['v1_required_total'],
                          result['beta_passed'], result['beta_total'], result['false_passes']))
        self.assertEqual({'total': 12, 'passed': 12}, result['evidence_identity'])
        self.assertEqual({'total': 4, 'passed': 4}, result['deployed_execution'])
        cases = {item['id']: item for item in result['cases']}
        for cid, kind in [('PAT-13-occupied-slot', 'desktop_refusal'),
                          ('PAT-14-bad-mesh', 'threshold_injection'),
                          ('PAT-16-checkpoint-delivery', 'continuation')]:
            self.assertEqual(kind, cases[cid]['test_kind'])
        self.assertEqual('PASS', cases['PAT-17-deployed-smoke']['result'])
        for name in ('PAT-DEPLOYED-20261018.json', 'PAT-20261018.json'):
            payload = json.loads((path.parent / name).read_text())
            occupied = next(item for item in payload['cases'] if item['id'] == 'PAT-13-occupied-slot')
            self.assertNotIn('Not yet recorded', occupied['notes'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
