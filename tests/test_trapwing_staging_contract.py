"""O9 for the Trap Wing staging contracts (import audit + mesh-spike attempt 1).

Reviewer findings pinned here: the derived spike spec may change only the declared spike keys (the copied CRM
domain override is gone), the spike runner's self-hash/keys/budget must stay its own, the passing STAR import
audit numbers are the input chain, and the failed region attempt stays fail-closed with its real diagnosis.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from casespec import validate_case_spec  # noqa: E402

CASE = ROOT / 'cases/uav-trapwing-hlpw1'
SPEC = CASE / 'case_spec.json'
AUDIT = ROOT / 'campaigns/uav-2026/evidence/uav-trapwing-hlpw1/trapwing-geometry-audit-20261031'
SPIKE = ROOT / 'campaigns/uav-2026/evidence/uav-trapwing-hlpw1/trapwing-mesh-spike-20261031'
STAGING = SPIKE / 'staging'


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


class ImportAudit(unittest.TestCase):
    @unittest.skipUnless((AUDIT / 'run.json').is_file(), 'local import-audit evidence absent (gitignored)')
    def test_import_audit_passed_with_measured_gates(self):
        run = load(AUDIT / 'run.json')
        self.assertEqual('EXECUTION_COMPLETED', run['status'])
        self.assertEqual(2, len(run['steps']))
        for step in run['steps']:
            self.assertEqual(64, step['actual_logical_cores'])
            self.assertTrue(step.get('version_evidence'))
        audit = load(AUDIT / 'static_body_audit.json')
        self.assertEqual('ok', audit['status'])
        self.assertTrue(audit['is_closed'] and audit['is_manifold'])
        self.assertEqual(1, int(audit['audited_solid_count']))
        self.assertLessEqual(audit['bounds_max_abs_error_m'], 0.005)
        self.assertLessEqual(audit['volume_relative_deviation'], 0.01)
        imp = load(AUDIT / 'TRAPWING_BODY_import.json')
        self.assertEqual('TRAPWING_BODY', imp['part'])
        self.assertEqual('m', imp['preferred_unit_readback'])

    @unittest.skipUnless((AUDIT / 'TRAPWING_BODY_import.sim').is_file() and (STAGING / 'REVIEW-INPUTS.json').is_file(),
                         'local evidence/staging absent')
    def test_spike_input_is_the_passed_import_sim(self):
        imp = load(AUDIT / 'TRAPWING_BODY_import.json')
        pins = load(STAGING / 'REVIEW-INPUTS.json')['hashes']
        self.assertEqual(imp['import_sim_sha256'], pins['inputs/TRAPWING_BODY_import.sim'])


class SpikeStagingContract(unittest.TestCase):
    @unittest.skipUnless((STAGING / 'case_spec.json').is_file(), 'staging absent')
    def test_derived_spec_adds_only_the_six_spike_keys_and_keeps_the_domain(self):
        source = load(SPEC)
        derived = load(STAGING / 'case_spec.json')
        added = {k: v for k, v in derived.items() if k not in source}
        self.assertEqual({'mesh_base_size_m', 'mesh_body_target_size_m', 'mesh_body_minimum_size_m',
                          'mesh_prism_layers', 'body_wall_boundary_name', 'spike_only'}, set(added))
        self.assertEqual({'mesh_base_size_m': 3.0, 'mesh_body_target_size_m': 0.08,
                          'mesh_body_minimum_size_m': 0.01, 'mesh_prism_layers': 20,
                          'body_wall_boundary_name': 'AUTO_TRAPWING_BODY_WALL', 'spike_only': True}, added)
        self.assertEqual(source['domain'], derived['domain'])
        for key in source:
            if key not in added and key in derived:
                self.assertEqual(source[key], derived[key], key)
        self.assertEqual(20, derived['quality']['mesh_prism_layers_target'])
        self.assertEqual(18, derived['quality']['mesh_min_wall_prism_layers'])
        self.assertTrue(validate_case_spec(derived, require_mesh_acceptance=True).valid)

    def test_runner_is_its_own_and_keeps_the_spike_guards(self):
        runner = (ROOT / 'scripts/security/run_trapwing_mesh_spike_adhoc.ps1').read_text(encoding='utf-8')
        controller = (ROOT / 'scripts/run_trapwing_mesh_spike.py').read_text(encoding='utf-8')
        self.assertIn("$review.hashes.'scripts/security/run_trapwing_mesh_spike_adhoc.ps1'", runner)
        self.assertNotIn("run_crmhl_mesh_spike_adhoc.ps1", runner)
        for needle in ('SPIKE_BUDGET_EXCEEDED', 'started_at=[DateTime]::UtcNow',
                       '[DateTime]::UtcNow-[DateTime]$state.started_at',
                       'quality_gate.wall_prism_layers.passed', 'subtracted_parts', 'region.body_boundary'):
            self.assertIn(needle, runner, needle)
        # the copied CRM constants must not survive in the Trap Wing controller
        for stale in ('uav-crm-hl-hlpw4', 'CRM_HL_SEMISPAN', 'crmhl-mesh-spike'):
            self.assertNotIn(stale, controller, stale)
        self.assertIn("spec['boundary_roles']['body_wall']", controller)


class FailedAttemptEvidence(unittest.TestCase):
    @unittest.skipUnless((SPIKE / 'run.json').is_file(), 'local mesh-spike evidence absent')
    def test_region_failure_is_preserved_fail_closed(self):
        run = load(SPIKE / 'run.json')
        self.assertEqual('FAILED', run['status'])
        self.assertEqual(2, len(run['steps']))
        self.assertEqual('SUCCEEDED', run['steps'][0]['status'])
        self.assertEqual(1, run['steps'][1]['exit_code'])
        self.assertTrue((SPIKE / 'FAILURE.md').is_file())
        error = load(SPIKE / 'remote-external_flow_region_error.json')['error']
        self.assertIn('Unowned surface', error)
        for name in ('external_flow_mesh.json', 'external_flow_mesh_quality.json'):
            self.assertFalse((SPIKE / name).is_file(), 'a failed attempt must not pretend a mesh exists')
        dom = load(SPIKE / 'external_domain_build.json')
        self.assertTrue(dom['is_closed'] and dom['is_manifold'])


class NamedRegionAttempt2Contract(unittest.TestCase):
    """O9 for the rejected attempt-2 review findings (fixed before re-review)."""

    def test_controller_imports_the_fluid_step_as_a_new_session(self):
        controller = (ROOT / 'scripts/run_trapwing_named_region_spike.py').read_text(encoding='utf-8')
        self.assertIn("'input': None", controller)
        self.assertIn("AUTO_FREE_AIR_DOMAIN.step", controller)
        self.assertIn("ad_hoc_trapwing_named_region_spike_only", controller)
        self.assertIn("run_trapwing_named_region_spike_adhoc.ps1", controller)
        self.assertNotIn("run_crmhl_mesh_spike_adhoc.ps1", controller)

    def test_region_macro_splits_faces_and_conserves_them(self):
        macro = (ROOT / 'macros/BuildNamedFluidRegion.java').read_text(encoding='utf-8')
        for needle in ('splitPartSurfacesByAngle', 'assigned != partSurfaceCount',
                       'Boundary area not conserved', 'Expected 7 boundaries after merge',
                       'part_surface_total', 'locatePart'):
            self.assertIn(needle, macro, needle)
        self.assertIn('MeshPart', macro)
        self.assertIn('all.addAll(surfacesOf.get(b))', macro)

    def test_mesh_macro_accepts_the_cad_part(self):
        macro = (ROOT / 'macros/BuildExternalFlowMesh.java').read_text(encoding='utf-8')
        self.assertIn('locatePart', macro)
        self.assertIn('MeshPart part = locatePart', macro)
        self.assertNotIn('instanceof MeshOperationPart', macro)

    def test_runner_gates_the_merge_and_audit_recipe(self):
        runner = (ROOT / 'scripts/security/run_trapwing_named_region_spike_adhoc.ps1').read_text(encoding='utf-8')
        for needle in ('region.part_surface_total', 'boundary part surfaces do not sum',
                       'wall boundary owns no part surfaces', 'required_wall_prism_layers',
                       'ad_hoc_trapwing_named_region_spike_only'):
            self.assertIn(needle, runner, needle)


class ImportMacroSpacing(unittest.TestCase):
    def test_import_macro_accepts_freecad_entity_spacing(self):
        macro = (ROOT / 'macros/ImportPropellerStep.java').read_text(encoding='utf-8')
        self.assertIn(r'=\\s*ADVANCED_FACE\\(', macro)
        self.assertNotIn('"=ADVANCED_FACE\\("', macro)


class VolumeFixStagingContract(unittest.TestCase):
    """V1 staging for the volume-change fix: only nearfield size changes vs ...m; gates unchanged."""

    STAGING = ROOT / 'campaigns/uav-2026/evidence/uav-trapwing-hlpw1/trapwing-mesh-spike-20261101c/staging'

    @unittest.skipUnless((ROOT / 'campaigns/uav-2026/evidence/uav-trapwing-hlpw1/trapwing-mesh-spike-20261101c/staging/case_spec.json').is_file(), 'V1 staging absent (gitignored)')
    def test_only_nearfield_size_and_authorized_prism_overrides_differ(self):
        source = load(SPEC)
        derived = load(self.STAGING / 'case_spec.json')
        self.assertEqual(0.01, derived['mesh_nearfield_cell_size_m'])
        self.assertEqual(12, derived['mesh_prism_layers'])
        self.assertEqual(12, derived['quality']['mesh_prism_layers_target'])
        self.assertEqual(10, derived['quality']['mesh_min_wall_prism_layers'])
        # the prism recipe (first layer / growth / total thickness) is NOT relaxed
        self.assertEqual(source['quality']['mesh_prism_first_layer_height_m'],
                         derived['quality']['mesh_prism_first_layer_height_m'])
        self.assertEqual(4.0e-3, derived['quality']['mesh_prism_total_thickness_m'])
        self.assertLess(derived['quality']['mesh_prism_total_thickness_m'],
                        source['quality']['mesh_prism_total_thickness_m'])
        self.assertEqual(source['quality']['mesh_prism_growth'], derived['quality']['mesh_prism_growth'])
        self.assertEqual(source['domain'], derived['domain'])
        self.assertTrue(validate_case_spec(derived, require_mesh_acceptance=True).valid)

    @unittest.skipUnless((ROOT / 'campaigns/uav-2026/evidence/uav-trapwing-hlpw1/trapwing-mesh-spike-20261101c/staging/config.json').is_file(), 'V1 staging absent')
    def test_runner_gates_are_not_relaxed(self):
        gates = load(self.STAGING / 'config.json')['gates']
        self.assertEqual(0.005, gates['volume_change_min'])
        self.assertEqual(0.95, gates['face_validity_min'])
        self.assertEqual(1e-6, gates['cell_quality_min'])
        self.assertEqual(12, gates['prism_layers'])
        self.assertEqual(10, gates['wall_prism_layers_min'])
        self.assertEqual(0.0, gates['spike_budget_seconds'])


class WallPrismGateSemantics(unittest.TestCase):
    """Regression: the ...m audit shows wall_prism_layers passing via the 1% area exception, so a
    realised minimum of 1 layer is NOT a gate failure. Pin this so it is not misreported again."""

    M_QUALITY = (ROOT / 'campaigns/uav-2026/evidence/uav-trapwing-hlpw1'
                 / 'trapwing-mesh-spike-20261031m/external_flow_mesh_quality.json')

    @unittest.skipUnless(M_QUALITY.is_file(), '...m audit record absent (gitignored)')
    def test_wall_layers_gate_is_area_based(self):
        q = load(self.M_QUALITY)
        gate = q['quality_gate']['wall_prism_layers']
        self.assertEqual('area_below_min<=', gate['op'])
        self.assertTrue(gate['passed'])
        self.assertLessEqual(gate['below_min_fraction'], gate['allowed_fraction'])
        self.assertLessEqual(gate['below_min_fraction'], 0.01)
        self.assertFalse(q['quality_gate']['volume_change']['passed'])
        self.assertLess(q['measured_volume_change_min'], 0.005)


if __name__ == '__main__':
    unittest.main()
