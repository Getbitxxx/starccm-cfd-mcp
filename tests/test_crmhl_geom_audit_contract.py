"""O9 for the P3.2 W1 geometry-audit staging of uav-crm-hl-hlpw4.

The first independent review (2026-10-31) REJECTED the staging because the runner's self-hash check still
read the review key of the Phase-2 prop runner (`.../run_prop_geom_audit_adhoc.ps1`) while REVIEW-INPUTS
pinned `.../run_crmhl_geom_audit_adhoc.ps1`.  Every controller-valid review therefore produced $null and
the runner aborted before STAR.  That defect (and the strict review binding around it) is pinned here, so
a copied runner can never again pass with the wrong self-hash key.

The review also found the face-count claim (329) did not match the STEP (414 ADVANCED_FACE); the audit
gate does not use face count, but the CaseSpec/admission claim is corrected and pinned too.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_crmhl_geom_audit as audit  # noqa: E402

CASE = ROOT / 'cases/uav-crm-hl-hlpw4'
SPEC = CASE / 'case_spec.json'
STAGING = audit.STAGING
RUNNER = ROOT / audit.RUNNER_REL
ADMISSION = ROOT / 'campaigns/uav-2026/evidence/phase3-p32-admission-20261031'


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path: Path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


class RunnerBinding(unittest.TestCase):
    """The defect the independent review rejected the staging for."""

    def test_runner_self_hash_reads_its_own_review_key(self):
        text = RUNNER.read_text(encoding='utf-8')
        self.assertIn("$review.hashes.'scripts/security/run_crmhl_geom_audit_adhoc.ps1'", text)
        self.assertNotIn("$review.hashes.'scripts/security/run_prop_geom_audit_adhoc.ps1'", text)

    def test_runner_keeps_the_reviewed_guards(self):
        text = RUNNER.read_text(encoding='utf-8')
        for needle in ('Independent approval required', 'Ad hoc runner self hash differs',
                       'no log progress for 900 seconds', 'Global\\CodexCFD2502Worker',
                       '-np 64 -noretry', 'wall_clock_stop_enabled=$false',
                       'Acceptance field missing', 'Trial already started', 'Input differs',
                       'Macro differs'):
            self.assertIn(needle, text, needle)

    def test_runner_diff_against_the_prop_runner_is_only_name_scope_and_comments(self):
        prop = (ROOT / 'scripts/security/run_prop_geom_audit_adhoc.ps1').read_text(encoding='utf-8')
        import difflib
        changed = [line for line in difflib.unified_diff(prop.splitlines(), RUNNER.read_text(encoding='utf-8').splitlines(), lineterm='')
                   if line.startswith(('+', '-')) and not line.startswith(('+++', '---'))]
        code_changes = [line for line in changed
                        if not line.lstrip('+-').lstrip().startswith('#')]
        # exactly two changed STATEMENTS: the self-hash key and the record_scope string (old+new lines)
        self.assertEqual(4, len(code_changes), code_changes)
        self.assertEqual(2, sum(1 for line in code_changes if 'runner self hash' in line))
        self.assertEqual(2, sum(1 for line in code_changes if 'record_scope=' in line))
        for line in code_changes:
            self.assertTrue('runner self hash' in line or 'record_scope=' in line, line)
        joined = '\n'.join(code_changes)
        self.assertIn('run_crmhl_geom_audit_adhoc.ps1', joined)
        self.assertIn('ad_hoc_crmhl_geometry_audit_only', joined)


class ReviewBinding(unittest.TestCase):
    def _staging_copy(self, tmp: Path) -> Path:
        """Copy the staged review surface (hard-link the 63 MB STEP) into a throwaway directory."""
        target = tmp / 'staging'
        (target / 'sources').mkdir(parents=True)
        for name in ('config.json', 'REVIEW-INPUTS.json', 'case_spec.json'):
            shutil.copyfile(STAGING / name, target / name)
        for name in (audit.BODY + '.step',):
            os.link(STAGING / name, target / name)
        for macro in audit.MACROS:
            shutil.copyfile(STAGING / 'sources' / macro, target / 'sources' / macro)
        return target

    def _review(self, staging: Path, **override) -> dict:
        expected = load(staging / 'REVIEW-INPUTS.json')['hashes']
        review = {'schema_version': 1, 'run_id': audit.RUN_ID, 'reviewed_at': '2026-10-31T00:00:00Z',
                  'reviewer': 'unit-test', 'independent': True, 'approved': True,
                  'hashes': dict(expected), 'findings': []}
        review.update(override)
        (staging / 'review.json').write_text(json.dumps(review), encoding='utf-8')
        return review

    @unittest.skipUnless(STAGING.is_dir(), 'staging not prepared')
    def test_valid_review_passes_and_exactly_pins_the_review_inputs(self):
        with tempfile.TemporaryDirectory() as name:
            staging = self._staging_copy(Path(name))
            self._review(staging)
            # the executed staging is bound to the PRE-prose-revision CaseSpec; verify the historical
            # binding against that staged copy (the repo CaseSpec has a documented prose-only revision)
            saved_spec = audit.SPEC
            audit.SPEC = staging / 'case_spec.json'
            try:
                cfg, review_path = audit.verify_review(staging)
            finally:
                audit.SPEC = saved_spec
            self.assertEqual(cfg['run_id'], audit.RUN_ID)
            self.assertTrue(review_path.is_file())

    @unittest.skipUnless(STAGING.is_dir(), 'staging not prepared')
    def test_review_with_the_prop_runner_key_is_refused(self):
        """Regression for the rejected first staging: the prop key must not satisfy the CRM binding."""
        with tempfile.TemporaryDirectory() as name:
            staging = self._staging_copy(Path(name))
            self._review(staging)
            review = load(staging / 'review.json')
            hashes = review['hashes']
            hashes['scripts/security/run_prop_geom_audit_adhoc.ps1'] = hashes.pop(
                'scripts/security/run_crmhl_geom_audit_adhoc.ps1')
            (staging / 'review.json').write_text(json.dumps(review), encoding='utf-8')
            with self.assertRaises(SystemExit):
                audit.verify_review(staging)

    @unittest.skipUnless(STAGING.is_dir(), 'staging not prepared')
    def test_missing_runner_key_is_refused(self):
        with tempfile.TemporaryDirectory() as name:
            staging = self._staging_copy(Path(name))
            review = self._review(staging)
            review['hashes'].pop('scripts/security/run_crmhl_geom_audit_adhoc.ps1')
            (staging / 'review.json').write_text(json.dumps(review), encoding='utf-8')
            with self.assertRaises(SystemExit):
                audit.verify_review(staging)

    @unittest.skipUnless(STAGING.is_dir(), 'staging not prepared')
    def test_not_independent_or_wrong_hash_is_refused(self):
        with tempfile.TemporaryDirectory() as name:
            staging = self._staging_copy(Path(name))
            self._review(staging, independent=False)
            with self.assertRaises(SystemExit):
                audit.verify_review(staging)
            self._review(staging)
            review = load(staging / 'review.json')
            review['hashes']['config.json'] = '0' * 64
            (staging / 'review.json').write_text(json.dumps(review), encoding='utf-8')
            with self.assertRaises(SystemExit):
                audit.verify_review(staging)


class AuditContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = load(SPEC)
        cls.cfg = load(STAGING / 'config.json') if (STAGING / 'config.json').is_file() else None

    @unittest.skipUnless(STAGING.is_dir(), 'staging not prepared')
    def test_config_is_audit_only_and_gates_are_derived_from_the_casespec(self):
        spec = self.spec
        cfg = self.cfg
        self.assertEqual(['import', 'audit'], [s['name'] for s in cfg['steps']])
        self.assertEqual(['ImportPropellerStep.java', 'AuditStaticBodyGeometry.java'],
                         [s['macro'] for s in cfg['steps']])
        self.assertEqual(cfg['steps'][0]['output'], cfg['steps'][1]['input'])
        self.assertEqual('CRM_HL_SEMISPAN', cfg['gates']['part'])
        self.assertEqual('m', cfg['gates']['model_length_unit'])
        self.assertEqual(float(spec['geometry']['expected_bounds_tolerance_m']),
                         cfg['gates']['bounds_tolerance_m'])
        self.assertEqual(float(spec['geometry']['expected_volume_relative_tolerance']),
                         cfg['gates']['volume_relative_tolerance'])
        for step in cfg['steps']:
            self.assertNotIn('mesh', step['name'].lower())
            self.assertNotIn('solve', step['name'].lower())

    @unittest.skipUnless(STAGING.is_dir(), 'staging not prepared')
    def test_review_inputs_pin_every_staged_byte(self):
        hashes = load(STAGING / 'REVIEW-INPUTS.json')['hashes']
        self.assertEqual(8, len(hashes))
        for rel in (audit.RUNNER_REL, audit.CONTROLLER_REL, 'inputs/case_spec.json',
                    'inputs/%s.step' % audit.BODY, 'macros/ImportPropellerStep.java',
                    'macros/AuditStaticBodyGeometry.java', 'config.json', 'source_case_spec.json'):
            self.assertIn(rel, hashes)
        self.assertEqual(sha(STAGING / 'config.json'), hashes['config.json'])
        self.assertEqual(sha(ROOT / audit.RUNNER_REL), hashes[audit.RUNNER_REL])
        self.assertEqual(sha(ROOT / audit.CONTROLLER_REL), hashes[audit.CONTROLLER_REL])

    def test_face_count_claim_matches_the_step_definition(self):
        """Corrected 2026-10-31: 414 ADVANCED_FACE in the STEP, 329 body faces reported by the CAD kernel."""
        brep = self.spec['geometry']['brep_summary']
        self.assertEqual(414, brep['step_advanced_faces'])
        self.assertEqual(329, brep['solidcraft_body_faces'])
        self.assertEqual(414, brep['solidcraft_kernel_faces'])
        self.assertEqual(1, brep['solid_count'])
        self.assertEqual(1, brep['shell_count'])
        self.assertEqual('79b4f85ae2f00399bd3d77090e5ad9ffc3f8447324cab340cd734915f0f00616',
                         self.spec['geometry']['geometry_sha256'])
        step = STAGING / (audit.BODY + '.step')
        if not step.is_file():
            self.skipTest('local pinned STEP absent (gitignored evidence store)')
        text = step.read_text(encoding='utf-8', errors='ignore')
        self.assertEqual(414, text.count('ADVANCED_FACE'))
        self.assertEqual(1, text.count('CLOSED_SHELL'))

    def test_measurement_basis_rebase_is_pinned_and_no_signed_gate_changed(self):
        """O9 for the W1 re-base: 0.005 -> 0.020 m bounds, with evidence, and nothing else loosened."""
        geo = self.spec['geometry']
        self.assertEqual(0.02, geo['expected_bounds_tolerance_m'])
        basis = geo.get('expected_bounds_tolerance_basis', '')
        self.assertIn('MEASUREMENT-BASIS RE-BASE', basis)
        self.assertIn('0.011678', basis)
        self.assertEqual(0.01, geo['expected_volume_relative_tolerance'])
        quality = self.spec['quality']
        self.assertAlmostEqual(1e-6, quality['mesh_min_cell_quality'], places=18)
        self.assertAlmostEqual(0.95, quality['mesh_min_face_validity'], places=12)
        self.assertAlmostEqual(0.005, quality['mesh_min_volume_change'], places=15)
        acceptance = self.spec['acceptance']
        self.assertEqual('PROPOSED_NOT_SIGNED', acceptance['reference_tolerance']['status'])
        self.assertEqual('PROPOSED_NOT_SIGNED', acceptance['numerical_criterion']['status'])
        if not (ADMISSION / 'ADMISSION.json').is_file():
            self.skipTest('local admission evidence absent')
        admission = load(ADMISSION / 'ADMISSION.json')
        rebase = admission['measurement_basis_rebases'][-1]
        self.assertEqual(0.005, rebase['before_m'])
        self.assertEqual(0.02, rebase['after_m'])
        self.assertFalse(rebase['signed_gate_changed'])
        attempt1 = ROOT / 'campaigns/uav-2026/evidence/uav-crm-hl-hlpw4/crmhl-geometry-audit-20261031/static_body_audit.json'
        if attempt1.is_file():
            measured = load(attempt1)['bounds_max_abs_error_m']
            self.assertAlmostEqual(measured, rebase['measured']['bounds_max_abs_error_m'], places=15)
            self.assertLess(measured, 0.02)
            self.assertGreater(measured, 0.005)

    def test_expected_values_are_the_independent_cad_measure(self):
        if not (ADMISSION / 'ADMISSION.json').is_file():
            self.skipTest('local admission evidence absent (gitignored)')
        admission = load(ADMISSION / 'ADMISSION.json')
        measure = admission['independent_cad_measure']
        self.assertEqual(measure['volume_m3'], self.spec['geometry']['expected_volume_m3'])
        self.assertEqual(measure['bbox_m']['min'] + measure['bbox_m']['max'],
                         self.spec['geometry']['expected_bounds_m'])
        self.assertEqual(measure['step_advanced_faces'], self.spec['geometry']['brep_summary']['step_advanced_faces'])


if __name__ == '__main__':
    unittest.main()
