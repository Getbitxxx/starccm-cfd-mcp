"""O9 ratchets for the three-mesh GCI at J=0.548 (Q3 report section 15; q3_requires p>1 and GCI_fine<=5%).

The math must recover a known order exactly, and every degenerate case must fail closed: a zero or
sign-reversing difference, an observed order <= 1, or an unconverged level can never produce a PASS.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import gci  # noqa: E402

CELLS = {'fine': 4.8e6, 'medium': 2.2e6, 'coarse': 1.0e6}


def synthetic(order: float, phi0: float = 0.05, amplitude: float = 0.002) -> dict:
    """Exact solution phi = phi0 + amplitude * h^order with h ~ N^(-1/3)."""
    return {level: {'cells': cells, 'value': phi0 + amplitude * cells ** (-order / 3.0)}
            for level, cells in CELLS.items()}


def levels(coarse: dict, medium: dict, fine: dict) -> dict:
    return {'coarse': coarse, 'medium': medium, 'fine': fine}


class GciMathRatchet(unittest.TestCase):
    def _levels(self, order: float = 1.8, amplitude: float = 0.002) -> dict:
        """Exact four-level ladder: phi = 0.05 + amplitude * N^(-order/3), N x1.77 per level."""
        cells = {'coarse': 1.0e6, 'medium': 1.77e6, 'fine': 3.13e6, 'finer': 5.54e6}
        levels = {}
        for level, n in cells.items():
            value = 0.05 + amplitude * (n / 1e6) ** (-order / 3.0)
            levels[level] = {'cells': n, 'ct': value, 'cp_abs': value * 0.8,
                             'converged': True, 'yplus_gate_passed': True}
        return levels

    def test_exact_order_is_recovered_from_the_finest_three(self):
        levels = self._levels(1.8)
        result = gci.evaluate(levels)
        self.assertEqual('PASS', result['gate'], result)
        for quantity in ('ct', 'cp'):
            self.assertAlmostEqual(1.8, result['quantities'][quantity]['observed_order'], delta=1e-6, msg=quantity)
        quantity = result['quantities']['ct']
        analytic = gci.FS * abs(levels['coarse']['ct'] - 0.05) / levels['coarse']['ct']
        self.assertAlmostEqual(analytic, quantity['gci_production'], delta=1e-12)
        self.assertAlmostEqual(1.8, quantity['diagnostic_three_coarsest']['observed_order'], delta=1e-6)
        self.assertTrue(quantity['diagnostic_three_coarsest']['valid'])

    def test_production_gate_is_stricter_than_the_finest_level(self):
        levels = self._levels(1.8, amplitude=0.0024)   # prod GCI ~6% while the finest level is ~2%
        result = gci.evaluate(levels)
        quantity = result['quantities']['ct']
        self.assertLess(quantity['gci_fine'], 0.05)
        self.assertGreater(quantity['gci_production'], 0.05)
        self.assertEqual('FAIL', result['gate'])

    def test_zero_difference_sub_first_order_and_sign_reversal_fail(self):
        base = self._levels(1.8)
        flat = {k: dict(v) for k, v in base.items()}
        for level in flat:
            flat[level].update({'ct': 0.05, 'cp_abs': 0.04})
        result = gci.evaluate(flat)
        self.assertEqual('FAIL', result['gate'])
        self.assertIn('not recoverable', result['quantities']['ct']['reason'])
        # sub-first order: order 0.5 must not be accepted
        slow = self._levels(0.5)
        self.assertEqual('FAIL', gci.evaluate(slow)['gate'])
        # sign reversal between the two finest levels must not be accepted
        rev = self._levels(1.8)
        rev['finer'] = dict(rev['finer'])
        rev['finer']['ct'] = rev['fine']['ct'] + 1e-4
        rev['finer']['cp_abs'] = rev['fine']['cp_abs'] + 8e-5
        result = gci.evaluate(rev)
        self.assertEqual('FAIL', result['gate'])

    def test_three_level_math_still_recovers_exact_order(self):
        values = synthetic(1.8)
        result = gci.gci_from_levels(values['coarse'], values['medium'], values['fine'])
        self.assertAlmostEqual(1.8, result['observed_order'], delta=1e-6)
        analytic = 1.25 * 0.002 * CELLS['fine'] ** (-1.8 / 3.0)
        self.assertAlmostEqual(analytic, result['gci_fine'], delta=analytic * 1e-6)
        self.assertEqual('PASS', result['gate'])

    def test_threshold_is_the_signed_five_percent(self):
        self.assertEqual(0.05, gci.GCI_LIMIT)
        self.assertEqual(1.0, gci.P_MIN)
        self.assertEqual(1.25, gci.FS)


class GciEvidenceRatchet(unittest.TestCase):
    def test_ladder_is_the_registered_upward_revision(self):
        self.assertEqual(gci.GATE_LEVEL, 'coarse')
        self.assertIn('mrf-resigned-j0548-20261030a', str(gci.LEVELS['coarse']))
        self.assertIn('mrf-gci-fine-j0548-20261030a', str(gci.LEVELS['medium']))
        self.assertIn('mrf-gci-finer2-j0548-20261030a', str(gci.LEVELS['fine']))

    def test_committed_evidence_matches_a_fresh_run(self):
        evidence = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/GCI-J0548-NLEVELS-20261030a.json'
        if not evidence.is_file():
            self.skipTest('GCI evidence not written yet (levels not all run)')
        missing = [name for name, path in gci.LEVELS.items()
                   if not (path / 'mrf_trial_audit.json').is_file()
                   or not (path / 'mrf_physics_readback.json').is_file()]
        if missing:
            self.skipTest('levels not solved yet: %s' % missing)
        levels = {name: gci.read_level(path) for name, path in gci.LEVELS.items()}
        fresh = gci.evaluate(levels)
        committed = json.loads(evidence.read_text(encoding='utf-8'))
        if committed['gci'].get('ladder_schema') != fresh.get('ladder_schema'):
            self.skipTest('committed GCI evidence predates the current ladder (%s vs %s levels); regenerate'
                          % (committed['gci'].get('ladder_schema'), fresh.get('ladder_schema')))
        self.assertEqual(fresh['gate'], committed['gci']['gate'])
        self.assertEqual(committed['gci'], json.loads(json.dumps(fresh)))




class GciCliWiringRatchet(unittest.TestCase):
    """O9 for the measured review-9f finding: 3b874e3 changed evaluate() to take a level dict but left main()
    calling the old four-positional form, so the CLI would have crashed just when the B17 records appear.
    This test drives main() end-to-end on fabricated four-level records."""

    def _fake_levels(self, tmp: Path) -> dict:
        cells = {'coarse': 1.0e6, 'medium': 1.77e6, 'fine': 3.13e6, 'finer': 5.54e6}
        levels = {}
        for name, n in cells.items():
            directory = tmp / name
            directory.mkdir(parents=True, exist_ok=True)
            value = 0.05 + 0.002 * (n / 1e6) ** (-1.8 / 3.0)
            (directory / 'mrf_trial_audit.json').write_text(json.dumps({
                'ct': value, 'cp_absorbed': value * 0.8, 'yplus_gate_passed': True,
                'yplus_max': 1.0, 'iterations': 400}), encoding='utf-8')
            (directory / 'mrf_physics_readback.json').write_text(json.dumps({'measured_cells': int(n)}),
                                                                 encoding='utf-8')
            (directory / 'mrf_segment_solve.json').write_text(json.dumps({
                'stop_reason': 'CONVERGED', 'converged': True}), encoding='utf-8')
            levels[name] = directory
        return levels

    def test_cli_writes_the_four_level_evidence(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            levels = self._fake_levels(tmp)
            out = tmp / 'GCI.json'
            import contextlib
            import io
            with mock.patch.object(gci, 'LEVELS', levels), mock.patch('sys.argv', ['gci.py', '--out', str(out)]), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, gci.main())
            written = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(4, written['gci']['ladder_schema'])  # the fake ladder has four levels
            self.assertEqual('PASS', written['gci']['gate'])
            self.assertAlmostEqual(1.8, written['gci']['quantities']['ct']['observed_order'], delta=1e-6)
            self.assertEqual(set(written['levels']), {'coarse', 'medium', 'fine', 'finer'})
            self.assertIn('N-level', written['scope'])



    def test_cli_writes_the_five_level_evidence(self):
        """The five-level CLI path (B18 records) must work too, not only the four-level one."""
        import contextlib
        import io
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            levels = self._fake_levels(tmp)
            # add a fifth (finer) level: cells x1.77, exact p=1.8 value
            fifth = tmp / 'finer2'
            fifth.mkdir()
            cells = int(5.54e6 * 1.77)
            value = 0.05 + 0.002 * (cells / 1e6) ** (-1.8 / 3.0)
            (fifth / 'mrf_trial_audit.json').write_text(json.dumps({
                'ct': value, 'cp_absorbed': value * 0.8, 'yplus_gate_passed': True,
                'yplus_max': 1.0, 'iterations': 400}), encoding='utf-8')
            (fifth / 'mrf_physics_readback.json').write_text(json.dumps({'measured_cells': cells}), encoding='utf-8')
            (fifth / 'mrf_segment_solve.json').write_text(json.dumps({
                'stop_reason': 'CONVERGED', 'converged': True}), encoding='utf-8')
            levels['finer2'] = fifth
            out = tmp / 'GCI5.json'
            with mock.patch.object(gci, 'LEVELS', levels), mock.patch('sys.argv', ['gci.py', '--out', str(out)]), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, gci.main())
            written = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(5, written['gci']['ladder_schema'])
            self.assertEqual(list(levels), written['gci']['levels_used'])
            self.assertEqual('PASS', written['gci']['gate'])
            self.assertAlmostEqual(1.8, written['gci']['quantities']['ct']['observed_order'], delta=1e-6)
            self.assertEqual(['fine', 'finer', 'finer2'], written['gci']['quantities']['ct']['finest_triple'])


if __name__ == '__main__':
    unittest.main()
