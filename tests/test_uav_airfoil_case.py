"""E387 airfoil CaseSpec and geometry/reference consistency (offline, no STAR)."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from casespec import validate_case_spec  # noqa: E402

CASE = ROOT / 'cases' / 'uav-airfoil-e387'
SPEC = CASE / 'case_spec.json'
GEOM = CASE / 'geometry' / 'GEOMETRY.json'
REF = CASE / 'reference.json'


class AirfoilCase(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads(SPEC.read_text(encoding='utf-8'))

    def test_case_spec_is_valid(self):
        result = validate_case_spec(self.spec)
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.missing, [])

    def test_arbitrary_requires_expected_bounds_and_volume(self):
        spec = copy.deepcopy(self.spec)
        spec['geometry'].pop('expected_bounds_m')
        result = validate_case_spec(spec)
        self.assertFalse(result.valid)
        self.assertTrue(any('expected_bounds_m' in error for error in result.errors), result.errors)

        spec = copy.deepcopy(self.spec)
        spec['geometry']['expected_volume_m3'] = 0
        result = validate_case_spec(spec)
        self.assertFalse(result.valid)
        self.assertTrue(any('expected_volume_m3' in error for error in result.errors), result.errors)

    def test_box_face_roles_need_six_known_roles_and_an_outlet(self):
        spec = copy.deepcopy(self.spec)
        spec['boundary_roles']['box_face_roles'] = ['inlet', 'outlet', 'symmetry']
        result = validate_case_spec(spec)
        self.assertFalse(result.valid)
        self.assertTrue(any('box_face_roles' in error for error in result.errors), result.errors)

        spec = copy.deepcopy(self.spec)
        spec['boundary_roles']['box_face_roles'] = ['inlet', 'symmetry', 'symmetry', 'symmetry', 'farfield', 'farfield']
        result = validate_case_spec(spec)
        self.assertFalse(result.valid)
        self.assertTrue(any('outlet' in error for error in result.errors), result.errors)

    def test_unknown_body_shape_is_rejected(self):
        spec = copy.deepcopy(self.spec)
        spec['geometry']['body_shape'] = 'blob'
        result = validate_case_spec(spec)
        self.assertFalse(result.valid)
        self.assertTrue(any('body_shape' in error for error in result.errors), result.errors)

    def test_geometry_record_matches_the_case_spec_and_step(self):
        geometry = json.loads(GEOM.read_text(encoding='utf-8'))
        self.assertEqual(geometry['geometry']['bbox_m'], self.spec['geometry']['expected_bounds_m'])
        self.assertEqual(geometry['geometry']['volume_m3'], self.spec['geometry']['expected_volume_m3'])
        self.assertEqual(geometry['cad']['product'], self.spec['geometry']['body_part_name'])
        step = ROOT / geometry['cad']['step']
        self.assertTrue(step.is_file())
        self.assertEqual(hashlib.sha256(step.read_bytes()).hexdigest(), geometry['cad']['step_sha256'])
        self.assertEqual(geometry['trailing_edge']['treatment'].split()[0], '0.5')

    def test_reference_record_matches_the_case_spec(self):
        reference = json.loads(REF.read_text(encoding='utf-8'))
        self.assertEqual(reference['data']['reynolds'], self.spec['reference']['reynolds'])
        self.assertEqual(reference['data']['drag_sha256'], self.spec['reference']['source_sha256'])
        self.assertEqual(reference['geometry']['coordinates_sha256'],
                         self.spec['reference']['coordinates_sha256'])
        self.assertEqual(reference['acceptance']['tolerance']['status'], 'AWAITING_USER_SIGNATURE')


if __name__ == '__main__':
    unittest.main()
