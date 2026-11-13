"""O9 for the user-signed Phase 3 case switch to Trap Wing (HLPW-1 Configuration 1), 2026-10-31.

Pins: the official archive identity, the revised configuration-1 IGES bytes and units, the free-air-corrected
force/moment sweep (row count/range), the reference quantities converted from the official figure, the signed
no-empennage/no-nacelle scope, the surface-only geometry finding (repair pending) and the STATUS registration.
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
CASE = ROOT / 'cases/uav-trapwing-hlpw1'
REF = CASE / 'reference.json'


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


class TrapWingRegistration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref = load(REF)

    def test_revised_geometry_bytes_and_units_are_pinned(self):
        geo = self.ref['geometry']
        path = ROOT / geo['file']
        self.assertTrue(path.is_file())
        self.assertEqual(geo['bytes'], path.stat().st_size)
        self.assertEqual(geo['sha256'], sha(path))
        self.assertIn('INCH', geo['units'])
        self.assertTrue(geo['surface_only_finding'])
        self.assertEqual('PASS', geo['repair_status'])
        text = path.read_text(errors='ignore')
        self.assertTrue(text.lstrip().startswith('S') or 'S      1' in text[:200])
        self.assertIn('20091021.152356', text[:4000])

    def test_force_sweep_rows_and_reference_quantities(self):
        data = self.ref['data_files'][0]
        path = ROOT / data['file']
        self.assertEqual(data['sha256'], sha(path))
        rows = [line.split() for line in path.read_text().splitlines()]
        rows = [r for r in rows if len(r) == 7 and r[0].isdigit()]
        self.assertEqual(52, len(rows))
        self.assertEqual(52, data['rows'])
        self.assertAlmostEqual(-3.834, float(rows[0][1]), places=6)
        self.assertAlmostEqual(37.089, float(rows[-1][1]), places=6)
        q = self.ref['reference_quantities']
        self.assertAlmostEqual(22.028 * 0.09290304, q['Sref_m2'], places=12)
        self.assertAlmostEqual(39.634 * 0.0254, q['MAC_m'], places=12)
        self.assertAlmostEqual(85.054 * 0.0254, q['semi_span_m'], places=12)
        self.assertAlmostEqual(34.342 * 0.0254, q['moment_reference_m'][0], places=12)

    def test_signed_scope_has_no_tail_or_nacelle(self):
        scope = self.ref['signed_scope']['scope'].lower()
        self.assertIn('no empennage', scope)
        self.assertIn('no nacelle', scope)
        self.assertEqual('Q3', self.ref['target_q'])
        self.assertEqual('PROPOSED_NOT_SIGNED', self.ref['q3_reference']['tolerance']['status'])
        self.assertEqual([13.0, 28.0], self.ref['q3_reference']['alpha_points_deg'])

    def test_w1_repaired_geometry_and_case_spec_are_pinned(self):
        ref = self.ref['geometry']
        self.assertEqual('PASS', ref['repair_status'])
        self.assertEqual('PASS', self.ref['geometry_audit']['status'])
        step = ROOT / ref['repaired_step']['file']
        self.assertTrue(step.is_file())
        self.assertEqual(ref['repaired_step']['sha256'], sha(step))
        self.assertEqual(ref['repaired_step']['bytes'], step.stat().st_size)
        spec = load(CASE / 'case_spec.json')
        from casespec import validate_case_spec
        result = validate_case_spec(spec, require_mesh_acceptance=True)
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(ref['repaired_step']['sha256'], spec['geometry']['geometry_sha256'])
        self.assertAlmostEqual(0.41026729068178164, spec['geometry']['expected_volume_m3'], places=15)
        self.assertEqual(['inlet', 'outlet', 'farfield', 'symmetry', 'farfield', 'farfield'],
                         spec['boundary_roles']['box_face_roles'])
        status = load(ROOT / 'campaigns/uav-2026/STATUS.json')
        tw = next(c for c in status['cases'] if c['case_id'] == 'uav-trapwing-hlpw1')
        self.assertTrue(tw['status'].startswith('MESH_SPIKE_'))
        self.assertEqual(sha(CASE / 'case_spec.json'), tw['case_spec']['sha256'])
        self.assertEqual(result.digest, tw['case_spec']['digest'])
        self.assertTrue(status['phase_states']['P3_FULL_UAV'].startswith('P3_TRAPWING_'))

    def test_w2_fluid_domain_and_named_boundaries_are_pinned(self):
        dom = self.ref['domain_build']
        self.assertEqual('PASS', dom['status'])
        for key in ('fluid_step', 'boundaries_step'):
            step = ROOT / dom[key]['file']
            self.assertTrue(step.is_file(), key)
            self.assertEqual(dom[key]['sha256'], sha(step), key)
        self.assertAlmostEqual(42029.530827827286, dom['fluid_volume_m3'], places=6)
        self.assertEqual(1, dom['solids'])
        self.assertEqual(1, dom['closed_shells'])
        names = set(dom['boundary_names'])
        self.assertEqual({'AUTO_INLET', 'AUTO_OUTLET', 'AUTO_SYMMETRY', 'AUTO_FARFIELD_Y_MIN',
                          'AUTO_FARFIELD_Z_MIN', 'AUTO_FARFIELD_Z_MAX', 'AUTO_TRAPWING_BODY_WALL'}, names)
        text = (ROOT / dom['boundaries_step']['file']).read_text(errors='ignore')
        for name in names:
            self.assertIn(name, text)
        spec = load(CASE / 'case_spec.json')
        self.assertEqual(['inlet', 'outlet', 'farfield', 'symmetry', 'farfield', 'farfield'],
                         spec['boundary_roles']['box_face_roles'])
        status = load(ROOT / 'campaigns/uav-2026/STATUS.json')
        tw = next(c for c in status['cases'] if c['case_id'] == 'uav-trapwing-hlpw1')
        self.assertTrue(tw['status'].startswith('MESH_SPIKE_'))
        self.assertEqual(dom['fluid_step']['sha256'], tw['domain']['fluid_step']['sha256'])

    def test_w2_domain_evidence_manifest_is_self_consistent(self):
        ev = ROOT / 'campaigns/uav-2026/evidence/uav-trapwing-w2-domain-20261031'
        if not (ev / 'SHA256SUMS.json').is_file():
            self.skipTest('local evidence store absent (gitignored)')
        manifest = load(ev / 'SHA256SUMS.json')['files']
        self.assertIn('DOMAIN.json', manifest)
        for rel, meta in manifest.items():
            path = ev / rel
            self.assertTrue(path.is_file(), rel)
            self.assertEqual(meta['sha256'], sha(path), rel)

    def test_w1_repair_evidence_manifest_is_self_consistent(self):
        ev = ROOT / 'campaigns/uav-2026/evidence/uav-trapwing-w1-geometry-20261031'
        if not (ev / 'SHA256SUMS.json').is_file():
            self.skipTest('local evidence store absent (gitignored)')
        manifest = load(ev / 'SHA256SUMS.json')['files']
        self.assertIn('REPAIR.json', manifest)
        for rel, meta in manifest.items():
            path = ev / rel
            self.assertTrue(path.is_file(), rel)
            self.assertEqual(meta['sha256'], sha(path), rel)

    def test_status_registers_the_case_and_defers_crm(self):
        status = load(ROOT / 'campaigns/uav-2026/STATUS.json')
        tw = next(c for c in status['cases'] if c['case_id'] == 'uav-trapwing-hlpw1')
        self.assertEqual('Q0', tw['q'])
        self.assertEqual('Q3', tw['target_q'])
        self.assertTrue(tw['status'].startswith('MESH_SPIKE_'))
        self.assertEqual(self.ref['geometry']['repaired_step']['sha256'], tw['geometry_sha256'])
        crm = next(c for c in status['cases'] if c['case_id'] == 'uav-crm-hl-hlpw4')
        self.assertEqual('DEFERRED_BY_USER_CASE_SWITCH_20261031', crm['status'])
        self.assertTrue(status['phase_states']['P3_FULL_UAV'].startswith('P3_TRAPWING_'))

    def test_registration_evidence_manifest_is_self_consistent(self):
        ev = ROOT / 'campaigns/uav-2026/evidence/uav-trapwing-registration-20261031'
        if not (ev / 'SHA256SUMS.json').is_file():
            self.skipTest('local evidence store absent (gitignored)')
        manifest = load(ev / 'SHA256SUMS.json')['files']
        self.assertIn('SOURCES.json', manifest)
        for rel, meta in manifest.items():
            path = ev / rel
            self.assertTrue(path.is_file(), rel)
            self.assertEqual(meta['sha256'], sha(path), rel)


if __name__ == '__main__':
    unittest.main()
