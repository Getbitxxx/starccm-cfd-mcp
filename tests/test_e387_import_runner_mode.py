"""Pin the single-step import-diagnostic runner contract.

The six-step runner was designed for the old surface_count field and would continue into
domain/region after a passing import. This test pins the StopAfterImport path, the new
part_surface_count/patch_count/native fields, and the fact that a gate_failed import is only
accepted when the runner is explicitly stopping after the import.
"""
from pathlib import Path
import json
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'scripts' / 'security' / 'run_e387_roles_adhoc.ps1'
EXPECTATIONS = (ROOT / 'campaigns' / 'uav-2026' / 'evidence' / 'uav-airfoil-e387' /
                'role-readback' / 'e387-import-map-20261024c' / 'role_expectations.json')


class ImportDiagnosticRunner(unittest.TestCase):
    def test_runner_has_the_single_step_switch_and_stops_before_domain(self):
        text = RUNNER.read_text()
        self.assertIn('[switch]$StopAfterImport', text)
        stop = text.index('if($StopAfterImport){')
        cross = text.index('# Independent SURFACE cross-read')
        self.assertLess(stop, cross)
        self.assertIn("IMPORT_DIAGNOSTIC_COMPLETE", text)
        self.assertIn("IMPORT_DIAGNOSTIC_GATE_FAILED", text)
        self.assertIn("$state.role_gate='NOT_ESTABLISHED'", text)
        self.assertIn('exit 0', text[stop:cross])

    def test_import_gate_uses_part_surface_patch_and_native_fields(self):
        text = RUNNER.read_text()
        self.assertIn('$o.part_surface_count', text)
        self.assertIn('$o.patch_count', text)
        self.assertIn('$o.native_count_route_available', text)
        self.assertIn('$expect.import_expected_patches', text)
        self.assertIn('$expect.import_expected_native_faces', text)
        self.assertNotIn('$o.surface_count', text)
        self.assertIn("$o.status -eq 'gate_failed' -and !$StopAfterImport", text)

    def test_runner_still_has_the_security_gates(self):
        text = RUNNER.read_text()
        for marker in ('Global\\CodexCFD2502Worker', 'Owned STAR has no log progress',
                       'Existing STAR process detected', 'review bytes differ',
                       'config bytes differ', 'review.approved', 'review.independent'):
            self.assertIn(marker, text)
        self.assertIn('function Resources', text)
        self.assertIn('function Hash', text)


    def test_cap_mode_match_uses_all_four_fields_and_breaks(self):
        text = RUNNER.read_text()
        start = text.index('$mode=$null')
        end = text.index("if($null -eq $mode", start)
        block = text[start:end]
        self.assertIn('$o.removed_caps -eq $p.Value.removed_caps', block)
        self.assertIn('$o.retained_caps -eq $p.Value.retained_caps', block)
        self.assertIn('$o.part_surfaces -eq $p.Value.domain_surface_count', block)
        self.assertIn('$o.body_surface_count -eq $p.Value.wall_surface_count', block)
        self.assertIn('break', block)


    def test_audit_gate_uses_casespec_tolerances(self):
        text = RUNNER.read_text()
        self.assertIn('expected_bounds_tolerance_m', text)
        self.assertIn('expected_volume_relative_tolerance', text)
        self.assertIn('$o.bounds_max_abs_error_m -gt $boundsTol', text)
        self.assertIn('$o.volume_relative_deviation -gt $volTol', text)
        self.assertNotIn('bounds_max_abs_error_m -gt 1e-6', text)
        self.assertNotIn('volume_relative_deviation -gt 1e-3', text)

    def test_role_expectations_use_the_new_import_fields(self):
        data = json.loads(EXPECTATIONS.read_text())
        self.assertEqual(data['import_expected_patches'], 63)
        self.assertEqual(data['import_expected_native_faces'], 63)
        self.assertNotIn('import_surface_count', data)


if __name__ == '__main__':
    unittest.main()
