"""O9 for the W2 mesh-spike staging (uav-crm-hl-hlpw4).

The independent review REJECTED the first spike staging (2026-10-31) for four real defects, all pinned here:
  1. the derived CaseSpec carried DUPLICATE explicit prism keys (the macros throw on duplicate fields);
  2. the audit macro needs a flat `body_wall_boundary_name` that the CaseSpec did not define;
  3. the copied runner still gated the geometry-audit fields (solid_count / bounds tolerance / volume
     tolerance) instead of the spike criteria (cells, quality minima, realised wall prism layers);
  4. the spike-budget arithmetic compared a 'Z' string cast to Local-kind DateTime, so the F2 <=40 min bound
     was off by the worker timezone offset.
A future silent regression in any of these must fail this test, not just a review.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_crmhl_mesh_spike as spike  # noqa: E402
from casespec import validate_case_spec  # noqa: E402

CASE = ROOT / 'cases/uav-crm-hl-hlpw4'
SPEC = CASE / 'case_spec.json'
STAGING = spike.STAGING
ATTEMPT1 = ROOT / 'campaigns/uav-2026/evidence/uav-crm-hl-hlpw4/crmhl-mesh-spike-20261031'
ATTEMPT2 = ROOT / 'campaigns/uav-2026/evidence/uav-crm-hl-hlpw4/crmhl-mesh-spike-20261031b'
RUNNER = ROOT / spike.RUNNER_REL


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path: Path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


class DerivedSpikeSpec(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = load(SPEC)
        cls.derived, _ = spike.derived_spec()
        cls.text = json.dumps(cls.derived)

    def test_only_the_declared_keys_are_added_and_each_appears_once(self):
        added = {k: v for k, v in self.derived.items() if k not in self.source}
        expected = {'mesh_base_size_m', 'mesh_body_target_size_m', 'mesh_body_minimum_size_m',
                    'mesh_prism_layers', 'body_wall_boundary_name', 'spike_only'}
        self.assertEqual(expected, set(added))
        for key in added:
            self.assertEqual(1, len(re.findall(r'"%s"' % re.escape(key), self.text)),
                             'duplicate CaseSpec field would make the macros throw: ' + key)
        # no flat override was added for the nested explicit-prism keys
        for key in ('mesh_prism_first_layer_height_m', 'mesh_prism_total_thickness_m'):
            self.assertEqual(1, len(re.findall(r'"%s"' % re.escape(key), self.text)), key)
            self.assertNotIn(key, added)

    def test_every_other_field_is_unchanged_except_the_documented_cut_plane(self):
        # source domain starts at y=0; the spike starts it at the measured OCCT cut plane and records why
        self.assertEqual(0.0, self.source['domain']['outer_bounds_m'][1])
        self.assertEqual(self.source['geometry']['expected_bounds_m'][1],
                         self.derived['domain']['outer_bounds_m'][1])
        for key in self.source:
            if key in ('domain',):
                continue
            if key in self.derived:
                self.assertEqual(self.source[key], self.derived[key], key)
        self.assertEqual(self.source['domain']['outer_bounds_m'][0],
                         self.derived['domain']['outer_bounds_m'][0])
        self.assertEqual(self.source['domain']['outer_bounds_m'][2:],
                         self.derived['domain']['outer_bounds_m'][2:])

    def test_validation_and_recipe_consistency(self):
        result = validate_case_spec(self.derived, require_mesh_acceptance=True)
        self.assertTrue(result.valid, result.errors)
        quality = self.derived['quality']
        self.assertEqual(quality['mesh_prism_layers_target'], self.derived['mesh_prism_layers'])
        self.assertGreaterEqual(self.derived['mesh_prism_layers'], quality['mesh_min_wall_prism_layers'])
        self.assertLessEqual(self.derived['mesh_prism_layers'], 20)
        self.assertEqual(2.8222e-05, quality['mesh_prism_first_layer_height_m'])
        self.assertEqual(0.0052687, quality['mesh_prism_total_thickness_m'])
        self.assertEqual(1.2, quality['mesh_prism_growth'])
        self.assertEqual(self.source['quality']['mesh_min_cell_quality'], 1e-06)
        self.assertEqual(self.source['quality']['mesh_min_face_validity'], 0.95)
        self.assertEqual(self.source['quality']['mesh_min_volume_change'], 0.005)
        self.assertEqual(self.source['boundary_roles']['body_wall'],
                         self.derived['body_wall_boundary_name'])

    @unittest.skipUnless(STAGING.is_dir(), 'staging not prepared')
    def test_prepared_spec_carries_the_unique_keys(self):
        staged = load(STAGING / 'case_spec.json')
        for key in ('mesh_base_size_m', 'mesh_body_target_size_m', 'mesh_body_minimum_size_m',
                    'mesh_prism_layers', 'body_wall_boundary_name'):
            self.assertIn(key, staged)
            self.assertEqual(1, len(re.findall(r'"%s"' % re.escape(key),
                                               (STAGING / 'case_spec.json').read_text(encoding='utf-8'))))
        self.assertEqual('AUTO_CRM_HL_SEMISPAN_WALL', staged['body_wall_boundary_name'])


class SpikeRunner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = RUNNER.read_text(encoding='utf-8')

    def test_self_hash_reads_its_own_review_key(self):
        self.assertIn("$review.hashes.'scripts/security/run_crmhl_mesh_spike_adhoc.ps1'", self.text)
        self.assertNotIn("run_crmhl_geom_audit_adhoc.ps1", self.text)

    def test_acceptance_gates_the_spike_not_the_geometry_audit(self):
        for needle in ('mesh_cells_min', 'measured_cell_quality_min', 'measured_face_validity_min',
                       'measured_volume_change_min', 'measured_wall_prism_layers_min',
                       'quality_gate.wall_prism_layers.passed', 'quality_gate.wall_prism_free_area.passed',
                       'subtracted_parts', 'region.body_boundary'):
            self.assertIn(needle, self.text, needle)
        for stale in ('Import solid_count', 'bounds_tolerance_m', 'volume_relative_tolerance',
                      'import_preferred_unit'):
            self.assertNotIn(stale, self.text, stale)

    def test_budget_clock_is_utc_kind_safe_and_keeps_an_iso_string(self):
        self.assertIn('started_at=[DateTime]::UtcNow', self.text)
        self.assertNotIn("started_at=[DateTime]::UtcNow.ToString('o')", self.text)
        self.assertIn('[DateTime]::UtcNow-[DateTime]$state.started_at', self.text)
        self.assertIn('started_at_iso=([DateTime]::UtcNow.ToString(', self.text)
        self.assertIn('SPIKE_BUDGET_EXCEEDED', self.text)
        self.assertIn('spike_budget_seconds', self.text)

    def test_controller_readback_uses_the_record_field_names(self):
        source = (ROOT / spike.CONTROLLER_REL).read_text(encoding='utf-8')
        for needle in ("mesh.get('mesh_seconds')", "audit.get('measured_wall_prism_layers_min')",
                       "audit.get('measured_wall_prism_free_area_fraction_max')"):
            self.assertIn(needle, source, needle)
        for stale in ("mesh.get('elapsed_seconds')", "audit.get('min_wall_prism_layers')",
                      "audit.get('prism_free_area_fraction')"):
            self.assertNotIn(stale, source, stale)

    def test_failed_first_attempt_is_preserved_with_its_duplicate_body_diagnosis(self):
        if not (ATTEMPT1 / 'run.json').is_file():
            self.skipTest('attempt-1 evidence absent (gitignored)')
        run = load(ATTEMPT1 / 'run.json')
        self.assertEqual('FAILED', run['status'])
        self.assertEqual('SUCCEEDED', run['steps'][0]['status'])
        self.assertEqual(1, run['steps'][1]['exit_code'])
        self.assertTrue((ATTEMPT1 / 'FAILURE.md').is_file())
        error = load(ATTEMPT1 / 'remote-external_flow_region_error.json')['error']
        self.assertIn('Duplicate body name', error)
        macro = (ROOT / 'macros/BuildExternalFlowRegion.java').read_text(encoding='utf-8')
        for needle in ('candidates', 'uniqueMesh', 'Duplicate body name'):
            self.assertIn(needle, macro)
        self.assertNotEqual(spike.RUN_ID, 'crmhl-mesh-spike-20261031')
        self.assertNotEqual(spike.DEFAULT_JOB_DIR, 'C:\\Users\\chen\\Documents\\CodexCFD\\service_v1\\adhoc\\crmhl-mesh-spike-20261031')

    @unittest.skipUnless(STAGING.is_dir(), 'staging not prepared')
    def test_review_binding_requires_the_exact_key_set(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'work') as tmp:
            target = Path(tmp) / 'staging'
            (target / 'sources').mkdir(parents=True)
            for name in ('config.json', 'REVIEW-INPUTS.json', 'case_spec.json'):
                shutil.copyfile(STAGING / name, target / name)
            os.link(STAGING / (spike.BODY + '_import.sim'), target / (spike.BODY + '_import.sim'))
            for macro in spike.MACROS:
                shutil.copyfile(STAGING / 'sources' / macro, target / 'sources' / macro)
            expected = load(target / 'REVIEW-INPUTS.json')['hashes']
            for override, why in (({'approved': False}, 'not approved'),
                                  ({'independent': False}, 'not independent')):
                review = {'schema_version': 1, 'run_id': spike.RUN_ID, 'approved': True, 'independent': True,
                          'hashes': dict(expected), 'findings': []}
                review.update(override)
                (target / 'review.json').write_text(json.dumps(review), encoding='utf-8')
                with self.assertRaises(SystemExit, msg=why):
                    spike.verify_review(target)
            review = {'schema_version': 1, 'run_id': spike.RUN_ID, 'approved': True, 'independent': True,
                      'hashes': dict(expected), 'findings': []}
            review['hashes']['config.json'] = '0' * 64
            (target / 'review.json').write_text(json.dumps(review), encoding='utf-8')
            with self.assertRaises(SystemExit):
                spike.verify_review(target)


    def test_second_failure_is_the_symmetry_cut_face_gap_and_no_mesh_exists(self):
        if not (ATTEMPT2 / 'run.json').is_file():
            self.skipTest('attempt-2 evidence absent (gitignored)')
        run = load(ATTEMPT2 / 'run.json')
        self.assertEqual('FAILED', run['status'])
        self.assertEqual(1, run['steps'][1]['exit_code'])
        self.assertEqual(2, len(run['steps']), 'mesh/audit steps must not run after the region failure')
        self.assertTrue((ATTEMPT2 / 'FAILURE.md').is_file())
        error = load(ATTEMPT2 / 'remote-external_flow_region_error.json')['error']
        self.assertIn('partially owned', error)
        self.assertIn('BELLYFAIRING-1', error)
        for name in ('external_flow_mesh.json', 'external_flow_mesh_quality.json'):
            self.assertFalse((ATTEMPT2 / name).is_file(), 'a failed attempt must not pretend a mesh exists')
        self.assertTrue(load(ATTEMPT2 / 'external_domain_build.json')['is_manifold'])


if __name__ == '__main__':
    unittest.main()
