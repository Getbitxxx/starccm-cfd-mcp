"""O9 ratchet for the P3.2 admission gate of uav-crm-hl-hlpw4 (2026-10-31, zero STAR).

The admission gate is "the signed case has a registered reference, an independently measured geometry pin,
and a CaseSpec that passes validate_case_spec(require_mesh_acceptance=True) with a declared mesh family".
It must NOT pass by asserting Q3: the tolerances and the Q3 point set are deliberately unsigned, and no
STAR has run.  The local evidence store is gitignored by convention, so the evidence-only checks skip when
the admission directory is absent; the case files themselves are committed and always checked.
"""
from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from casespec import validate_case_spec  # noqa: E402

CASE = ROOT / 'cases/uav-crm-hl-hlpw4'
REF = CASE / 'reference.json'
SPEC = CASE / 'case_spec.json'
STATUS = ROOT / 'campaigns/uav-2026/STATUS.json'
ADMISSION = ROOT / 'campaigns/uav-2026/evidence/phase3-p32-admission-20261031'


def sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


class ReferenceRegistration(unittest.TestCase):
    """HLPW-4 v15 reference quantities and data, registered from the primary source."""

    @classmethod
    def setUpClass(cls):
        cls.ref = load(REF)

    def test_conditions_and_reference_quantities_are_the_official_values(self):
        c = self.ref['conditions']
        q = self.ref['reference_quantities']
        self.assertEqual(0.2, c['Mach'])
        self.assertEqual(5.49e6, c['Re_MAC'])
        self.assertAlmostEqual(521.0, c['T_ref_R'], places=12)
        self.assertAlmostEqual(24.67, c['p_ref_psi'], places=12)
        # the registered SI values are conversions of the official inch/psi/degR numbers, not new numbers
        self.assertAlmostEqual(275.8 * 0.0254, q['MAC_m'], places=12)
        self.assertAlmostEqual(297360.0 * 0.0254 ** 2, q['Sref_half_m2'], places=9)
        self.assertAlmostEqual(1325.9 * 0.0254, q['MRC_m'][0], places=9)
        self.assertAlmostEqual(177.95 * 0.0254, q['MRC_m'][2], places=9)
        self.assertAlmostEqual(c['T_ref_R'] * 5.0 / 9.0, c['T_ref_K'], places=9)
        self.assertAlmostEqual(c['p_ref_psi'] * 6894.757293168, c['p_ref_Pa'], places=6)
        # the registered velocity is Ma * sound speed of the registered temperature
        self.assertAlmostEqual(0.2 * c['sound_speed_m_s'], c['velocity_m_s'], places=9)

    def test_alpha_sweeps_are_registered_and_their_files_match_the_hashes(self):
        files = self.ref['data_files']
        corrected = files['corrected_alpha_sweep']
        uncorrected = files['uncorrected_alpha_sweep']
        self.assertEqual(23, corrected['rows'])
        self.assertEqual(23, uncorrected['rows'])
        self.assertEqual(23, len(self.ref['alpha_sweep']['corrected']))
        self.assertEqual(23, len(self.ref['alpha_sweep']['uncorrected']))
        for entry in (corrected, uncorrected, files['data_readme']):
            path = ROOT / entry['path']
            self.assertTrue(path.is_file(), entry['path'])
            self.assertEqual(entry['sha256'], sha(path), entry['path'])
        # the corrected sweep is the free-air reference (per the data file header)
        self.assertIn('FREE-AIR', corrected['use'].upper())

    def test_re_matching_is_declared_not_assumed(self):
        rm = self.ref['reynolds_matching']
        self.assertGreater(rm['sutherland_scale_factor'], 1.0)
        self.assertAlmostEqual(rm['required_dynamic_viscosity_pa_s'],
                               self.ref['conditions']['rho_kg_m3'] * self.ref['conditions']['velocity_m_s']
                               * self.ref['reference_quantities']['MAC_m'] / self.ref['conditions']['Re_MAC'],
                               places=18)
        self.assertEqual(rm['required_dynamic_viscosity_pa_s'],
                         self.ref['reynolds_matching']['required_dynamic_viscosity_pa_s'])

    def test_tolerance_and_point_set_are_proposals_and_unsigned(self):
        self.assertEqual('PROPOSED_NOT_SIGNED', self.ref['tolerance_proposal']['status'])
        self.assertEqual('PROPOSED_NOT_SIGNED', self.ref['point_set_for_q3_proposal']['status'])
        # an unsigned proposal must not be able to masquerade as a signed tolerance
        self.assertNotEqual('SIGNED', self.ref['tolerance_proposal']['status'])


class CaseSpecAdmission(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = load(SPEC)

    def test_casespec_passes_with_the_mesh_acceptance_requirement(self):
        result = validate_case_spec(self.spec, require_mesh_acceptance=True)
        self.assertTrue(result.valid, result.errors)
        self.assertEqual([], result.errors)
        self.assertEqual([], result.missing)

    def test_declared_family_block_is_the_family_gate_contract(self):
        block = self.spec['quality']['mesh_acceptance']
        self.assertEqual('ALL_PASS', block['family']['rule'])
        self.assertEqual(['coarse', 'medium', 'fine'], [m['id'] for m in block['family']['members']])
        self.assertTrue(block['localization_required_on_failure'])
        self.assertGreater(block['margin']['target'], block['margin']['fragile_below'])
        self.assertEqual(sorted(block['distribution_bands']), block['distribution_bands'])
        # declared before meshing: no fabricated mesh records
        self.assertTrue(all(m['mesh_sha256'] is None for m in block['family']['members']))

    def test_half_model_and_no_empennage_scope_are_explicit(self):
        self.assertEqual('y = 0', self.spec['geometry']['symmetry']['plane'])
        self.assertEqual(1, len(self.spec['geometry']['body_parts']))
        joined = json.dumps(self.spec).lower()
        # the signed scope is "no empennage"; the official configuration sentence lives in reference.json
        self.assertIn('no empennage', joined)
        self.assertIn('fully turbulent', joined)
        reference = load(REF)
        official = reference['source_primary']['quotes']['geometry_scope'].lower()
        self.assertIn('no landing gear, horizontal or vertical tail', official)

    def test_geometry_pin_matches_the_admission_record(self):
        geo = self.spec['geometry']
        expected = '79b4f85ae2f00399bd3d77090e5ad9ffc3f8447324cab340cd734915f0f00616'
        self.assertEqual(expected, geo['geometry_sha256'])
        self.assertEqual(63686234, geo['geometry_bytes'])
        self.assertGreater(geo['expected_volume_m3'], 0)
        self.assertAlmostEqual(898.7922674351045, geo['expected_volume_m3'], places=9)
        bounds = geo['expected_bounds_m']
        self.assertTrue(all(bounds[i] < bounds[i + 3] for i in range(3)))

    def test_casespec_is_not_frozen_and_declares_no_solution(self):
        self.assertFalse(self.spec['case_spec_frozen'])
        self.assertEqual('PROPOSED_NOT_SIGNED', self.spec['acceptance']['numerical_criterion']['status'])
        self.assertEqual('PROPOSED_NOT_SIGNED', self.spec['acceptance']['reference_tolerance']['status'])
        self.assertEqual(20, self.spec['limits']['wall_hours'])
        self.assertFalse(self.spec['limits']['wall_clock_stop_enabled'])
        self.assertEqual(3, self.spec['quality']['mesh_levels'])
        self.assertTrue(self.spec['quality']['domain_sensitivity'])
        for key in ('mesh_min_cell_quality', 'mesh_min_face_validity', 'mesh_min_volume_change'):
            self.assertIn(key, self.spec['quality'])
        # the campaign hard gates are unchanged
        self.assertAlmostEqual(1e-6, self.spec['quality']['mesh_min_cell_quality'], places=18)
        self.assertAlmostEqual(0.95, self.spec['quality']['mesh_min_face_validity'], places=12)
        self.assertAlmostEqual(0.005, self.spec['quality']['mesh_min_volume_change'], places=15)


class CampaignRegistration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.status = load(STATUS)

    def test_signed_case_is_registered_at_q0_with_target_q3(self):
        case = next(c for c in self.status['cases'] if c['case_id'] == 'uav-crm-hl-hlpw4')
        self.assertEqual('Q0', case['q'])
        self.assertEqual('Q3', case['target_q'])
        self.assertFalse(case['tolerance_signed'])
        self.assertEqual(['D1', 'D2', 'D3'], case['signed_decisions'])
        self.assertEqual(20.0, case['budget_approved_hours'])
        self.assertTrue(case['budget_approved'])
        self.assertEqual('cases/uav-crm-hl-hlpw4/case_spec.json', case['case_spec'])
        self.assertEqual(sha(SPEC), case['case_spec_sha256'])
        self.assertEqual(sha(REF), case['reference_sha256'])
        # registered BEFORE any run: no record / no evidence claim
        self.assertIsNone(case['record'])
        self.assertFalse(case['q3_claim'])

    def test_q_counts_include_every_q0_case(self):
        # 2026-10-31: the user signed the case switch to Trap Wing, so two Q0 cases now exist
        # (the deferred CRM-HL record and the newly registered Trap Wing); the count must tally the list.
        self.assertEqual(sum(1 for c in self.status['cases'] if c['q'] == 'Q0'),
                         self.status['q_counts']['Q0'])
        self.assertTrue(any(c['case_id'] == 'uav-trapwing-hlpw1' for c in self.status['cases']))


@unittest.skipUnless(ADMISSION.is_dir(), 'local P3.2 admission evidence store is absent (gitignored)')
class AdmissionEvidence(unittest.TestCase):
    """Evidence-only checks: run in this workspace, skipped when the local store is not present."""

    @classmethod
    def setUpClass(cls):
        cls.adm = load(ADMISSION / 'ADMISSION.json')

    def test_admission_was_built_zero_star_and_claims_no_q3(self):
        # the package itself was built with zero STAR; the W1 geometry audit that followed used STAR
        self.assertTrue(self.adm.get('zero_star_at_package_build'))
        self.assertFalse(self.adm.get('zero_star'))
        self.assertIn('geometry audit', self.adm.get('zero_star_note', ''))
        self.assertEqual(0, self.adm['star_processes_started'])
        self.assertEqual('PASS', self.adm['geometry_audit']['status'])
        self.assertFalse(self.adm['q3_claimed'])
        self.assertFalse(self.adm['case_spec_frozen'])
        self.assertEqual('PROPOSED_NOT_SIGNED', self.adm['tolerance_and_point_set']['status'])
        self.assertEqual(0, len(list(ADMISSION.rglob('run.json'))))

    def test_geometry_files_and_manifest_verify(self):
        pin = self.adm['geometry_pin']
        for rel, sha_expected in ((pin['local_files']['zip'], pin['zip']['sha256']),
                                  (pin['local_files']['step'], pin['step']['sha256'])):
            path = ADMISSION / rel
            self.assertTrue(path.is_file(), rel)
            self.assertEqual(sha_expected, sha(path), rel)
        files = load(ADMISSION / 'SHA256SUMS.json')['files']
        for rel, entry in files.items():
            path = ADMISSION / rel
            self.assertTrue(path.is_file(), rel)
            self.assertEqual(entry['sha256'], sha(path), rel)
            self.assertEqual(entry['bytes'], path.stat().st_size, rel)
        # the independent CAD measurement is the CaseSpec's expected value
        self.assertAlmostEqual(self.adm['independent_cad_measure']['volume_m3'],
                               load(SPEC)['geometry']['expected_volume_m3'], places=9)

    def test_casespec_validation_was_recorded_from_the_current_bytes(self):
        recorded = self.adm['casespec']
        self.assertTrue(recorded['validate_case_spec']['valid'])
        self.assertEqual(sha(SPEC), recorded['sha256'])
        self.assertEqual(sha(REF), self.adm['reference_registration']['sha256'])


if __name__ == '__main__':
    unittest.main()
