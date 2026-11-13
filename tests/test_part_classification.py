import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from part_classification import classify_parts
from backend import Backend
from unittest.mock import Mock


class PartClassificationTests(unittest.TestCase):
    def audit(self):
        return {'status': 'ok', 'geometryParts': [
            {'name': 'misleading-solid-name', 'isClosed': True, 'isManifold': True,
             'boundingBoxRaw': [-10, -10, -5, 10, 10, 5]}],
            'regions': [{'name': 'renamed-region', 'physicsContinuum': 'air',
                         'boundaries': [
                             {'boundaryTypeClassSimpleName': 'WallBoundary',
                              'partSurfaceOwners': ['misleading-solid-name']},
                             {'boundaryTypeClassSimpleName': 'WallBoundary',
                              'partSurfaceOwners': ['misleading-solid-name']},
                             {'boundaryTypeClassSimpleName': 'InternalBoundary',
                              'partSurfaceOwners': []}]}]}

    def test_rotating_fluid_sector_rejects_solid_intent_without_inlet(self):
        result = classify_parts(self.audit(), {'rotor_parts': ['misleading-solid-name']}, 'mm')
        self.assertFalse(result['intent_consistent'])
        self.assertEqual(result['parts'][0]['role'], 'fluid_domain_candidate')

    def test_closed_geometry_alone_does_not_prove_solid(self):
        audit = self.audit()
        audit['regions'] = []
        self.assertEqual(classify_parts(audit)['parts'][0]['role'], 'unresolved')
        result = classify_parts(audit, {'body_parts': ['misleading-solid-name']}, 'mm')
        self.assertEqual(result['parts'][0]['role'], 'declared_solid_candidate')
        self.assertFalse(result['parts'][0]['solid_identity_verified'])
        self.assertEqual(result['parts'][0]['bounds_m'], [-.01, -.01, -.005, .01, .01, .005])

    def test_mixed_owners_do_not_establish_fluid_volume(self):
        audit = self.audit()
        audit['regions'][0]['boundaries'][1]['partSurfaceOwners'] = ['another-part']
        self.assertEqual(classify_parts(audit)['parts'][0]['role'], 'unresolved')

    def test_thermal_solid_interface_is_not_a_fluid_volume(self):
        audit = self.audit()
        audit['regions'][0]['regionTypeClass'] = 'star.common.SolidRegion'
        self.assertEqual(classify_parts(audit)['parts'][0]['role'], 'unresolved')

    def test_open_boundary_owner_is_detected_even_without_interface(self):
        audit = self.audit()
        audit['regions'][0]['boundaries'] = [
            {'boundaryTypeClassSimpleName': 'PressureBoundary',
             'partSurfaceOwners': ['misleading-solid-name']}]
        self.assertEqual(classify_parts(audit)['parts'][0]['role'], 'fluid_domain_candidate')

    def test_duplicate_selectors_and_missing_targets_fail_closed(self):
        audit = self.audit()
        audit['geometryParts'] *= 2
        result = classify_parts(audit, {'body_parts': ['absent']})
        self.assertEqual(len(result['errors']), 2)

    def test_missing_or_defective_topology_keeps_intent_unresolved(self):
        for value in [False, None, 'true']:
            audit = self.audit()
            audit['regions'] = []
            audit['geometryParts'][0]['isManifold'] = value
            result = classify_parts(audit, {'body_parts': ['misleading-solid-name']}, 'm')
            self.assertEqual(result['parts'][0]['role'], 'unresolved')

    def test_no_mutation_and_invalid_input_rejected(self):
        audit = self.audit()
        original = copy.deepcopy(audit)
        classify_parts(audit)
        self.assertEqual(audit, original)
        for value in [{}, {'status': 'failed'}, {'status': 'ok', 'geometryParts': [] , 'regions': None}]:
            with self.assertRaises(ValueError):
                classify_parts(value)

    def backend(self, stage='preflight', digest='a' * 64):
        backend = Backend(str(Path(__file__).resolve().parents[1] / 'connection.example.json'))
        backend.get_job = Mock(return_value={'status': {
            'state': 'SUCCEEDED', 'workflow_id': 'core', 'stage': stage,
            'source_sha256': digest}})
        backend.ps = Mock(side_effect=['a' * 64, self.audit()])
        return backend

    def test_backend_binds_classification_to_source_hash(self):
        backend = self.backend()
        result = backend.classify_case_parts('library/example.sim', 'inspect-test-123')
        self.assertEqual(result['source_sha256'], 'a' * 64)
        backend = self.backend(digest='b' * 64)
        with self.assertRaisesRegex(ValueError, 'input hash'):
            backend.classify_case_parts('library/example.sim', 'inspect-test-123')
        self.assertEqual(backend.ps.call_count, 1)

    def test_backend_rejects_other_successful_core_stages(self):
        backend = self.backend(stage='audit-mrf-volume-mesh-quality')
        with self.assertRaisesRegex(ValueError, 'core preflight'):
            backend.classify_case_parts('library/example.sim', 'inspect-test-123')
        backend.ps.assert_not_called()


if __name__ == '__main__':
    unittest.main()
