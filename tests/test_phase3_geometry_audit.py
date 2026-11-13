"""O9 for the P3.2 geometry-audit gate (W1) of uav-crm-hl-hlpw4.

The audit is an ad hoc, review-gated STAR run (import -> static-body audit, no mesh/physics/solve).  This
test does not run STAR; it pins the acceptance of the downloaded records when the local evidence store has
them (the store is gitignored by convention, so the test skips on a clone without it).  It refuses
incomplete evidence: the controller record must include the audit readback, the runner must report
EXECUTION_COMPLETED with 64 processes, and the audit record itself must be closed+manifold with one solid
and the measured bounds/volume inside the CaseSpec tolerances.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

CASE = ROOT / 'cases/uav-crm-hl-hlpw4'
SPEC = CASE / 'case_spec.json'
EVIDENCE = ROOT / 'campaigns/uav-2026/evidence/uav-crm-hl-hlpw4/crmhl-geometry-audit-20261031b'
ATTEMPT1 = ROOT / 'campaigns/uav-2026/evidence/uav-crm-hl-hlpw4/crmhl-geometry-audit-20261031'


def sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


@unittest.skipUnless((EVIDENCE / 'CONTROLLER-RUN.json').is_file() and (EVIDENCE / 'run.json').is_file(), 'geometry-audit run records absent (not run yet, or local evidence store gitignored)')
class GeometryAuditRecords(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = load(SPEC)
        cls.controller = load(EVIDENCE / 'CONTROLLER-RUN.json') if (EVIDENCE / 'CONTROLLER-RUN.json').is_file() else None
        cls.run_record = load(EVIDENCE / 'run.json') if (EVIDENCE / 'run.json').is_file() else None
        cls.audit = load(EVIDENCE / 'static_body_audit.json') if (EVIDENCE / 'static_body_audit.json').is_file() else None
        cls.import_record = (load(EVIDENCE / 'CRM_HL_SEMISPAN_import.json')
                             if (EVIDENCE / 'CRM_HL_SEMISPAN_import.json').is_file() else None)

    def test_the_run_actually_happened_and_completed_on_64_processes(self):
        self.assertIsNotNone(self.controller, 'missing CONTROLLER-RUN.json')
        self.assertIsNotNone(self.run_record, 'missing run.json')
        self.assertEqual('EXECUTION_COMPLETED', self.run_record['status'])
        self.assertEqual(2, len(self.run_record['steps']))
        self.assertEqual(['import', 'audit'], [s['step'] for s in self.run_record['steps']])
        for step in self.run_record['steps']:
            self.assertEqual('SUCCEEDED', step['status'])
            self.assertEqual(64, step['actual_logical_cores'])
            self.assertTrue(step.get('version_evidence'))
        self.assertGreater(self.run_record['cumulative_star_seconds'], 0.0)
        self.assertEqual(2, self.run_record['star_processes_started'])
        self.assertFalse(self.run_record['wall_clock_stop_enabled'])

    def test_import_contract_is_one_closed_manifold_solid(self):
        self.assertIsNotNone(self.import_record, 'missing import record')
        self.assertEqual('ok', self.import_record['status'])
        self.assertEqual('CRM_HL_SEMISPAN', self.import_record['part'])
        self.assertEqual(1, self.import_record['solid_count'])
        self.assertTrue(self.import_record['is_closed'])
        self.assertTrue(self.import_record['is_manifold'])
        self.assertEqual('m', self.import_record['preferred_unit_readback'])

    def test_audit_measures_the_registered_geometry_inside_the_casespec_tolerances(self):
        self.assertIsNotNone(self.audit, 'missing audit record')
        self.assertEqual('ok', self.audit['status'])
        self.assertTrue(self.audit['usable'])
        self.assertTrue(self.audit['is_closed'])
        self.assertTrue(self.audit['is_manifold'])
        self.assertEqual(1, int(self.audit['audited_solid_count']))
        geo = self.spec['geometry']
        self.assertEqual(0.02, geo['expected_bounds_tolerance_m'])
        self.assertLessEqual(float(self.audit['bounds_max_abs_error_m']), float(geo['expected_bounds_tolerance_m']))
        self.assertEqual(1.0, self.audit['preferred_length_unit_conversion_to_m'])
        self.assertLessEqual(float(self.audit['volume_relative_deviation']),
                             float(geo['expected_volume_relative_tolerance']))
        # the controller re-derives the same numbers from the downloaded record
        readback = self.controller['audit_readback']
        self.assertEqual('ok', readback['status'])
        self.assertTrue(readback['is_closed'])
        self.assertTrue(readback['is_manifold'])
        self.assertEqual(1, int(readback['audited_solid_count']))
        for key in ('bounds_max_abs_error_m', 'volume_relative_deviation',
                    'measured_bbox_si_m', 'measured_enclosed_volume_si_m3'):
            self.assertEqual(self.audit[key], readback[key], key)

    def test_geometry_bytes_are_the_pinned_published_geometry(self):
        record = load(EVIDENCE / 'CONTROLLER-RUN.json')
        self.assertEqual('79b4f85ae2f00399bd3d77090e5ad9ffc3f8447324cab340cd734915f0f00616',
                         self.spec['geometry']['geometry_sha256'])
        staging_spec = EVIDENCE / 'staging' / 'case_spec.json'
        if staging_spec.is_file():
            # the audit executed against the pre-prose-revision CaseSpec bytes; the revision is prose-only
            import_record = load(EVIDENCE / 'CRM_HL_SEMISPAN_import.json')
            self.assertEqual(import_record['casespec_sha256'], sha(staging_spec))
            self.assertEqual(self.spec['geometry']['geometry_sha256'], import_record['step_sha256'])

    def test_no_mesh_physics_or_solve_was_part_of_the_audit(self):
        cfg = load(EVIDENCE / 'staging' / 'config.json') if (EVIDENCE / 'staging' / 'config.json').is_file() else None
        if cfg is None:
            self.skipTest('staging is absent; the run record already proves two audit steps only')
        self.assertEqual(['import', 'audit'], [s['name'] for s in cfg['steps']])
        macros = ' '.join(s['macro'] for s in cfg['steps'])
        self.assertNotIn('Mesh', macros)
        self.assertNotIn('Solve', macros)
        self.assertNotIn('Physics', macros)


@unittest.skipUnless((ATTEMPT1 / 'CONTROLLER-RUN.json').is_file(), 'attempt-1 evidence absent')
class Attempt1FailClosed(unittest.TestCase):
    """Attempt 1 must stay recorded as a FAILED geometry audit (fail-closed), not deleted or rewritten."""

    def test_attempt1_is_a_failed_run_with_the_bounds_gate_as_the_cause(self):
        run_record = load(ATTEMPT1 / 'run.json')
        audit = load(ATTEMPT1 / 'static_body_audit.json')
        self.assertEqual('FAILED', run_record['status'])
        self.assertEqual(2, len(run_record['steps']))
        self.assertEqual('SUCCEEDED', run_record['steps'][0]['status'])
        self.assertEqual(1, run_record['steps'][1]['exit_code'])
        self.assertEqual('error', audit['status'])
        self.assertFalse(audit['usable'])
        self.assertGreater(audit['bounds_max_abs_error_m'], 0.005)
        self.assertTrue(audit['is_closed'] and audit['is_manifold'])
        self.assertEqual(1, int(audit['audited_solid_count']))
        self.assertEqual(1.0, audit['preferred_length_unit_conversion_to_m'])
        self.assertLessEqual(audit['volume_relative_deviation'], 0.01)
        self.assertIn('bbox deviates', audit['error'])


if __name__ == '__main__':
    unittest.main()
