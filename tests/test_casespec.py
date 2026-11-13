import sys
import unittest
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from casespec import validate_case_spec, required_operations
from knowledge import search_knowledge


class CaseSpecTests(unittest.TestCase):
    def test_explicit_boundary_roles_and_turbulence_ranges(self):
        spec = json.loads((Path(__file__).resolve().parents[1] / 'cases' /
                           'free-air-propeller-360.json').read_text())
        self.assertTrue(validate_case_spec(spec).valid)
        spec['boundary_roles']['outlet'] = spec['boundary_roles']['inlet']
        spec['flow']['turbulence_intensity'] = 1.0
        result = validate_case_spec(spec)
        self.assertFalse(result.valid)
        self.assertTrue(any('boundary_roles.inlet and outlet' in error for error in result.errors))
        self.assertTrue(any('turbulence_intensity must be less than 1' in error for error in result.errors))

    def test_missing_physics_is_returned_as_questions(self):
        result = validate_case_spec({'schema_version': 1, 'scenario': 'propeller_mrf'})
        self.assertFalse(result.valid)
        self.assertIn('rotation.rpm', result.missing)
        self.assertIn('flow.velocity_m_s', result.missing)

    def test_valid_geometry_and_flow_still_need_installed_operations(self):
        spec = {
            'schema_version': 1, 'scenario': 'external_static',
            'case_ref': 'library/example.sim', 'objective': 'drag',
            'model_length_unit': 'm', 'medium': 'air',
            'geometry': {'body_parts': ['body']},
            'domain': {'outer_bounds_m': [-10, -10, -10, 10, 10, 10]},
            'flow': {'velocity_m_s': [10, 0, 0], 'pressure_pa': 101325, 'temperature_k': 293.15},
        }
        result = validate_case_spec(spec)
        self.assertTrue(result.valid)
        self.assertFalse(result.executable)
        self.assertIn('build_external_domain', result.required_operations)
        # R11: availability is resolved from REAL call paths (operations/catalog.json + the live workflow
        # manifests + the files on disk), so phases 2 and 3 are genuinely servable. ②R13: window 1
        # delivered the two study implementations, they are registered, and the LAST generic gap is gone -
        # `unavailable_operations` is empty. The plan is STILL not executable on its own, and that is the
        # point of the selected-plan rule: a full plan is a graph, so it must be named explicitly (and it
        # needs a trusted audit) rather than inferred from "every capability exists somewhere".
        self.assertEqual(result.phase_readiness['2_general_preprocessing']['missing'], [])
        self.assertEqual(result.phase_readiness['3_persistent_execution']['missing'], [])
        self.assertEqual([], sorted(result.unavailable_operations))
        self.assertEqual([], result.unresolved_operations)
        self.assertFalse(result.executable)
        self.assertTrue(result.plan_blocker)
        # ②R13: the scenario-acceptance phase has no gap left either - both studies are registered post
        # steps (their entries state that the sensitivity conclusion itself is NOT_ESTABLISHED, which is a
        # different statement from "the operation is missing").
        self.assertEqual(result.phase_readiness['4_scenario_acceptance']['missing'], [])

    def test_phase_readiness_covers_all_three_scenarios_without_false_acceptance(self):
        cases = {
            'external_static': {
                'geometry': {'body_parts': ['body']},
                'rotation': {},
            },
            'propeller_mrf': {
                'geometry': {'body_parts': ['body'], 'rotor_parts': ['rotor']},
                'rotation': {'axis': [0, 0, 1], 'center_m': [0, 0, 0], 'rpm': 1000},
            },
            'rotating_transient': {
                'geometry': {'body_parts': ['body'], 'rotor_parts': ['rotor']},
                'rotation': {'axis': [0, 0, 1], 'center_m': [0, 0, 0], 'rpm': 1000},
                'transient': {'time_step_s': 0.0012, 'physical_duration_s': 0.5,
                              'steps_per_revolution': 50, 'stability_window_revolutions': 3,
                              'target_quantities': ['thrust', 'torque']},
            },
        }
        installed = {'audit_geometry', 'build_external_domain'}
        for scenario, extra in cases.items():
            spec = {
                'schema_version': 1, 'scenario': scenario, 'case_ref': 'library/example.sim',
                'objective': 'readiness audit', 'model_length_unit': 'm', 'medium': 'air',
                'geometry': extra['geometry'], 'rotation': extra['rotation'],
                **({'transient': extra['transient']} if 'transient' in extra else {}),
                'domain': {'outer_bounds_m': [-10, -10, -10, 10, 10, 10]},
                'flow': {'velocity_m_s': [10, 0, 0], 'pressure_pa': 101325, 'temperature_k': 293.15},
            }
            result = validate_case_spec(spec, installed)
            self.assertTrue(result.valid, scenario)
            self.assertFalse(result.executable, scenario)
            self.assertEqual(result.phase_readiness['1_plan_and_knowledge']['installed'], ['audit_geometry'])
            self.assertTrue(result.phase_readiness['2_general_preprocessing']['missing'])
            self.assertTrue(result.phase_readiness['3_persistent_execution']['missing'])
            self.assertTrue(result.phase_readiness['4_scenario_acceptance']['missing'])

    def test_transient_requires_time_resolution_duration_and_stability_targets(self):
        spec = {
            'schema_version': 1, 'scenario': 'rotating_transient',
            'case_ref': 'library/transient.sim', 'objective': 'periodic thrust',
            'model_length_unit': 'm', 'medium': 'air',
            'geometry': {'body_parts': ['hub'], 'rotor_parts': ['rotor']},
            'rotation': {'axis': [0, 0, 1], 'center_m': [0, 0, 0], 'rpm': 1200},
            'domain': {'outer_bounds_m': [-2, -2, -3, 2, 2, 3]},
            'flow': {'velocity_m_s': [0, 0, -10], 'pressure_pa': 101325, 'temperature_k': 300},
        }
        result = validate_case_spec(spec)
        self.assertIn('transient.time_step_s', result.missing)
        self.assertIn('transient.physical_duration_s', result.missing)
        self.assertIn('transient.steps_per_revolution', result.missing)
        self.assertIn('transient.stability_window_revolutions', result.missing)
        self.assertIn('transient.target_quantities', result.missing)

    def test_transient_time_step_must_match_rotation_resolution(self):
        spec = {
            'schema_version': 1, 'scenario': 'rotating_transient',
            'case_ref': 'library/transient.sim', 'objective': 'periodic thrust',
            'model_length_unit': 'm', 'medium': 'air',
            'geometry': {'body_parts': ['hub'], 'rotor_parts': ['rotor']},
            'rotation': {'axis': [0, 0, 1], 'center_m': [0, 0, 0], 'rpm': 1200},
            'domain': {'outer_bounds_m': [-2, -2, -3, 2, 2, 3]},
            'flow': {'velocity_m_s': [0, 0, -10], 'pressure_pa': 101325, 'temperature_k': 300},
            'transient': {'time_step_s': 0.001, 'physical_duration_s': 0.5,
                          'steps_per_revolution': 60, 'stability_window_revolutions': 3,
                          'target_quantities': ['thrust', 'torque']},
        }
        result = validate_case_spec(spec)
        self.assertTrue(any('time_step_s must equal' in error for error in result.errors))
        spec['transient']['time_step_s'] = 60 / (1200 * 60)
        result = validate_case_spec(spec)
        self.assertFalse(any('time_step_s must equal' in error for error in result.errors))

    def test_no_wall_tunnel_in_free_air_or_quality_shortcuts(self):
        spec = {
            'schema_version': 1, 'scenario': 'external_static', 'case_ref': 'library/example.sim',
            'objective': 'drag', 'model_length_unit': 'm', 'medium': 'air',
            'geometry': {'body_parts': ['body']},
            'domain': {'outer_bounds_m': [-10, -10, -10, 10, 10, 10]},
            'flow': {'velocity_m_s': [10, 0, 0], 'pressure_pa': 101325, 'temperature_k': 293.15},
            'boundary_assumption': 'wall_tunnel', 'quality': {'mesh_levels': 1},
        }
        result = validate_case_spec(spec)
        self.assertFalse(result.valid)
        self.assertTrue(any('wall-tunnel' in error for error in result.errors))

    def test_cylinder_dimensions_match_enclosing_bounds(self):
        spec = {
            'schema_version': 1, 'scenario': 'external_static', 'case_ref': 'library/example.sim',
            'objective': 'geometry trial', 'model_length_unit': 'm', 'medium': 'air',
            'geometry': {'body_parts': ['blade']},
            'domain': {'shape': 'cylinder', 'cylinder_center_xy_m': [0, 0],
                       'cylinder_radius_m': 2.05, 'cylinder_z_min_m': -6.15,
                       'cylinder_z_max_m': 2.05,
                       'outer_bounds_m': [-2.05, -2.05, -6.15, 2.05, 2.05, 2.05]},
            'flow': {'velocity_m_s': [0, 0, -30], 'pressure_pa': 101325, 'temperature_k': 300},
        }
        audit = {'status': 'ok', 'geometryParts': [{'name': 'blade', 'isClosed': True,
                 'isManifold': True, 'boundingBoxRaw': [-0.2, -0.2, -0.02, 0.2, 0.2, 0.02]}],
                 'regions': []}
        self.assertTrue(validate_case_spec(spec, set(required_operations('external_static')), audit).valid)
        spec['domain']['cylinder_radius_m'] = 1
        self.assertTrue(any('cylinder bounding box' in error for error in
                            validate_case_spec(spec, audit=audit).errors))

    def test_knowledge_index_does_not_claim_login_content(self):
        results = search_knowledge('Surface Repair')
        self.assertTrue(results)
        self.assertEqual(results[0]['validation'], 'title_and_link_only')

    def test_open_selected_part_blocks_plan(self):
        spec = {
            'schema_version': 1, 'scenario': 'external_static', 'case_ref': 'library/example.sim',
            'objective': 'drag', 'model_length_unit': 'm', 'medium': 'air',
            'geometry': {'body_parts': ['body']},
            'domain': {'outer_bounds_m': [-10, -10, -10, 10, 10, 10]},
            'flow': {'velocity_m_s': [10, 0, 0], 'pressure_pa': 101325, 'temperature_k': 293.15},
        }
        audit = {'status': 'ok', 'geometryParts': [
            {'name': 'body', 'isClosed': False, 'isManifold': True}], 'regions': []}
        result = validate_case_spec(spec, {'audit_geometry'}, audit)
        self.assertFalse(result.valid)
        self.assertTrue(any('open' in error for error in result.errors))

    def test_missing_topology_evidence_blocks_execution(self):
        spec = {
            'schema_version': 1, 'scenario': 'external_static', 'case_ref': 'library/example.sim',
            'objective': 'drag', 'model_length_unit': 'm', 'medium': 'air',
            'geometry': {'body_parts': ['body']},
            'flow': {'velocity_m_s': [10, 0, 0], 'pressure_pa': 101325, 'temperature_k': 293.15},
        }
        audit = {'status': 'ok', 'geometryParts': [{'name': 'body'}], 'regions': []}
        result = validate_case_spec(spec, {'audit_geometry'}, audit)
        self.assertIn('geometry.topology_evidence.body', result.missing)

    def test_inlet_owner_is_not_accepted_as_a_body(self):
        spec = {
            'schema_version': 1, 'scenario': 'external_static', 'case_ref': 'library/example.sim',
            'objective': 'drag', 'model_length_unit': 'm', 'medium': 'air',
            'geometry': {'body_parts': ['fluid-box']},
            'domain': {'outer_bounds_m': [-10, -10, -10, 10, 10, 10]},
            'flow': {'velocity_m_s': [10, 0, 0], 'pressure_pa': 101325, 'temperature_k': 293.15},
        }
        audit = {'status': 'ok', 'geometryParts': [
            {'name': 'fluid-box', 'isClosed': True, 'isManifold': True}],
            'regions': [{'name': 'fluid', 'boundaries': [{'name': 'in',
                'boundaryTypeClassSimpleName': 'InletBoundary',
                'partSurfaceOwners': ['fluid-box']}]}]}
        result = validate_case_spec(spec, {'audit_geometry'}, audit)
        self.assertFalse(result.valid)
        self.assertTrue(any('fluid domain' in error for error in result.errors))

    def test_interfaced_fluid_region_cannot_be_selected_as_solid(self):
        spec = {
            'schema_version': 1, 'scenario': 'external_static', 'case_ref': 'library/example.sim',
            'objective': 'geometry role check', 'model_length_unit': 'm', 'medium': 'air',
            'geometry': {'body_parts': ['solid-looking'], 'existing_wall_roles': {
                'rotor/face': 'other_wall', 'rotor/wall': 'body_wall'}},
            'domain': {'outer_bounds_m': [-1, -1, -1, 1, 1, 1]},
            'flow': {'velocity_m_s': [0, 0, -10], 'pressure_pa': 101325, 'temperature_k': 300},
        }
        audit = {'status': 'ok', 'geometryParts': [{'name': 'solid-looking', 'isClosed': True,
                 'isManifold': True, 'boundingBoxRaw': [-0.2, -0.2, -0.02, 0.2, 0.2, 0.02]}],
                 'regions': [{'name': 'rotor', 'physicsContinuum': 'air', 'boundaries': [
                     {'name': 'face', 'boundaryTypeClassSimpleName': 'WallBoundary',
                      'partSurfaceOwners': ['solid-looking']},
                     {'name': 'wall', 'boundaryTypeClassSimpleName': 'WallBoundary',
                      'partSurfaceOwners': ['solid-looking']},
                     {'name': 'interface', 'boundaryTypeClassSimpleName': 'InternalBoundary',
                      'partSurfaceOwners': []}]}]}
        errors = validate_case_spec(spec, audit=audit).errors
        self.assertTrue(any('interfaced fluid Region' in error for error in errors))
        audit['regions'][0]['boundaries'][1]['partSurfaceOwners'] = ['other-part']
        self.assertFalse(any('interfaced fluid Region' in error for error in
                             validate_case_spec(spec, audit=audit).errors))

    def test_selected_part_requires_measured_non_degenerate_bounds(self):
        spec = {
            'schema_version': 1, 'scenario': 'external_static', 'case_ref': 'library/example.sim',
            'objective': 'drag', 'model_length_unit': 'm', 'medium': 'air',
            'geometry': {'body_parts': ['body']},
            'domain': {'outer_bounds_m': [-10, -10, -10, 10, 10, 10]},
            'flow': {'velocity_m_s': [10, 0, 0], 'pressure_pa': 101325, 'temperature_k': 293.15},
        }
        part = {'name': 'body', 'isClosed': True, 'isManifold': True}
        audit = {'status': 'ok', 'geometryParts': [part], 'regions': []}
        result = validate_case_spec(spec, set(required_operations('external_static')), audit)
        self.assertIn('geometry.bounding_box_evidence.body', result.missing)
        self.assertFalse(result.executable)
        part['boundingBoxRaw'] = [0, 0, 0, 1, 0, 1]
        result = validate_case_spec(spec, set(required_operations('external_static')), audit)
        self.assertTrue(any('invalid bounding-box' in error for error in result.errors))
        part['boundingBoxRaw'] = [0, 0, 0, 1, 2, 3]
        result = validate_case_spec(spec, set(required_operations('external_static')), audit)
        self.assertTrue(result.executable)

    def test_outer_domain_bounds_must_contain_audited_parts(self):
        spec = {
            'schema_version': 1, 'scenario': 'external_static', 'case_ref': 'library/example.sim',
            'objective': 'drag', 'model_length_unit': 'm', 'medium': 'air',
            'geometry': {'body_parts': ['body']},
            'domain': {'outer_bounds_m': [-0.1, -1, -1, 1, 1, 1]},
            'flow': {'velocity_m_s': [10, 0, 0], 'pressure_pa': 101325, 'temperature_k': 293.15},
        }
        audit = {'status': 'ok', 'geometryParts': [{'name': 'body', 'isClosed': True,
            'isManifold': True, 'boundingBoxRaw': [-0.2, -0.1, -0.1, 0.2, 0.1, 0.1]}], 'regions': []}
        result = validate_case_spec(spec, set(required_operations('external_static')), audit)
        self.assertIn('domain.outer_bounds_m does not contain selected geometry: body', result.errors)

    def test_outer_domain_bounds_requires_six_finite_coordinates(self):
        spec = {
            'schema_version': 1, 'scenario': 'external_static', 'case_ref': 'library/example.sim',
            'objective': 'drag', 'model_length_unit': 'm', 'medium': 'air',
            'geometry': {'body_parts': ['body']},
            'domain': {'outer_bounds_m': [0, 0, 0, float('inf'), 1, 1]},
            'flow': {'velocity_m_s': [10, 0, 0], 'pressure_pa': 101325, 'temperature_k': 293.15},
        }
        result = validate_case_spec(spec)
        self.assertTrue(any('six finite SI coordinates' in error for error in result.errors))


if __name__ == '__main__':
    unittest.main()
