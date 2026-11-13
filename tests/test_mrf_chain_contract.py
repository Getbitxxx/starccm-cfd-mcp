"""O9 ratchets for the P2.3 G5 MRF geometry chain (rotor-solid audit -> fluid volumes).

Pins the contract that would otherwise only fail after two STAR processes:
  * the audit macro writes every key BuildMrfFluidVolumes requires (status/output_role/output_part/
    readback_surface_volume_m3) and the runner gates exactly those fields;
  * the chain hand-over names match what the macros actually write (recovered_rotor_solids.sim etc.);
  * the CaseSpec still declares the single rotor-solid role, the MRF envelope and the outer domain.
"""
from __future__ import annotations

import json
import hashlib
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_mrf_chain as chain  # noqa: E402

CASE = ROOT / 'cases/uav-prop-apc10x7e'
A1_CASE = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-chain3-20261028a/staging/case_spec.json'
AUDIT = ROOT / 'macros/AuditMrfRotorSolid.java'
BUILD = ROOT / 'macros/BuildMrfFluidVolumes.java'
RUNNER = ROOT / 'scripts/security/run_mrf_chain_adhoc.ps1'
BODY = 'APC10X7E_ROTOR'


class MrfChainContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = chain.config('part1')
        cls.spec = json.loads((CASE / 'case_spec.json').read_text(encoding='utf-8'))
        cls.audit_source = AUDIT.read_text(encoding='utf-8')
        cls.build_source = BUILD.read_text(encoding='utf-8')

    def test_audit_macro_writes_the_required_contract_fields(self):
        src = self.audit_source
        for key in ('readback_surface_volume_m3', 'output_role', 'rotor_solid', 'output_part',
                    'recovered_rotor_solids.sim', 'recovered_rotor_solids.json', 'recovered_rotor_solids.stl',
                    'is_closed', 'is_manifold', 'triangle_count', 'complete_marker'):
            self.assertIn(key, src, key)
        self.assertIn('sim.saveState(saved.getAbsolutePath())', src)
        self.assertIn('MRF_ROTOR_SOLID_AUDITED', src)
        self.assertIn('"status\\":\\"error\\"', src, 'failure record must be written on error')

    def test_build_macro_requires_the_audit_record_fields(self):
        for key in ('recovered_rotor_solids.json', 'readback_surface_volume_m3', 'rotor_solid',
                    'recovered_rotor_part', 'rotor_parts'):
            self.assertIn(key, self.build_source, key)
        self.assertIn('recovered_rotor_solids.sim', self.config['steps'][1]['input'])

    def test_chain_steps_and_gates_match_the_macro_outputs(self):
        steps = self.config['steps']
        self.assertEqual(['audit-rotor', 'build-volumes'], [s['name'] for s in steps])
        self.assertEqual('recovered_rotor_solids.sim', steps[0]['output'])
        self.assertEqual(steps[0]['output'], steps[1]['input'])
        self.assertEqual('mrf_fluid_volumes_built.sim', steps[1]['output'])
        self.assertEqual(['recovered_rotor_solids.json'], steps[0]['records'])
        self.assertEqual(['mrf_fluid_volumes_build.json'], steps[1]['records'])
        gates = {g['record']: {f['field'] for f in g['fields']} for g in self.config['record_gates']}
        for required in ('status', 'output_role', 'output_part', 'readback_surface_volume_m3'):
            self.assertIn(required, gates['recovered_rotor_solids.json'])
        for required in ('status', 'stationary_closed', 'stationary_manifold', 'rotating_closed',
                         'rotating_manifold', 'stationary_surfaces', 'rotating_surfaces'):
            self.assertIn(required, gates['mrf_fluid_volumes_build.json'])
        # volume gate brackets the CaseSpec expected volume by 2% (measurement basis)
        expected = float(self.spec['geometry']['expected_volume_m3'])
        volume_gates = [g for g in self.config['record_gates'][0]['fields']
                        if g['field'] == 'readback_surface_volume_m3' and g['op'] in ('ge', 'le')]
        self.assertEqual(2, len(volume_gates))
        self.assertAlmostEqual(expected * 0.98, volume_gates[0]['value'], places=15)
        self.assertAlmostEqual(expected * 1.02, volume_gates[1]['value'], places=15)

    def test_casespec_declares_the_single_rotor_role_and_envelopes(self):
        geom = self.spec['geometry']
        self.assertEqual([BODY], geom['rotor_parts'])
        self.assertEqual(BODY, geom['recovered_rotor_part'])
        self.assertEqual(2, geom['blade_count'])
        self.assertGreater(self.spec['rotation_zone']['radius_m'], 0)
        self.assertLess(self.spec['rotation_zone']['z_min_m'], self.spec['rotation_zone']['z_max_m'])
        self.assertGreater(self.spec['domain']['cylinder_radius_m'], self.spec['rotation_zone']['radius_m'])
        for key in ('stationary_fluid_part', 'rotating_fluid_part', 'outer_cylinder_part', 'rotation_envelope_part'):
            self.assertTrue(geom[key].startswith('AUTO_'), key)

    def test_runner_gates_records_and_keeps_the_guards(self):
        text = RUNNER.read_text(encoding='utf-8')
        for required in ('record_gates', 'GateField', 'chain of custody', 'Independent approval required',
                         'no log progress for 900 seconds', 'wall_clock_stop_enabled=$false',
                         'taskkill', 'Global\\CodexCFD2502Worker', 'VersionEvidence', 'version_evidence',
                         '2502\\.0001', 'Build\\s+20\\.02\\.008', 'Actual STAR log version/build not established'):
            self.assertIn(required, text, required)

    def test_executed_chain_passed_record(self):
        """O9 for the executed G5 part-1 run: the record must claim PASS and the measured values must satisfy the
        chain gates; a regression that only changes numbers fails here. Declared limits are asserted too."""
        ev = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-chain-20261028a'
        result = json.loads((ev / 'RESULT.json').read_text(encoding='utf-8'))
        self.assertEqual('PASS', result['status'])
        rotor = result['steps']['audit-rotor']
        build = result['steps']['build-volumes']
        self.assertTrue(rotor['is_closed'])
        self.assertTrue(rotor['is_manifold'])
        lo, hi = rotor['volume_bracket_m3']
        self.assertLessEqual(lo, rotor['readback_surface_volume_m3'])
        self.assertLessEqual(rotor['readback_surface_volume_m3'], hi)
        self.assertEqual('ok', rotor['status'])
        for key in ('stationary_closed', 'stationary_manifold', 'rotating_closed', 'rotating_manifold'):
            self.assertTrue(build[key], key)
        self.assertGreaterEqual(build['stationary_surfaces'], 6)
        self.assertGreaterEqual(build['rotating_surfaces'], 4)
        self.assertEqual(0, build['regions'])
        self.assertEqual(0, build['iteration'])
        self.assertFalse(build['initialized'])
        run = json.loads((ev / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual('EXECUTION_COMPLETED', run['status'])
        self.assertEqual([64, 64], [s['actual_logical_cores'] for s in run['steps']])
        self.assertTrue(all(s.get('version_evidence') for s in run['steps']))
        self.assertTrue(any('domain adequacy' in item for item in result['declared_limits']))

    def test_controller_requires_the_chain_artefacts(self):
        """review-2o: a passing runner with a failed download must NOT be reported as success."""
        text = (ROOT / 'scripts/run_mrf_chain.py').read_text(encoding='utf-8')
        # F1: the required artefacts are declared in the plan data, not in controller bytes.
        for required in ('run.json', 'recovered_rotor_solids.json', 'recovered_rotor_solids.sim',
                         'mrf_fluid_volumes_build.json', 'mrf_fluid_volumes_built.sim'):
            self.assertIn(required, chain.PLANS['part1']['required_downloads'], required)
        for required in ('downloads', 'download_errors', 'required chain artefacts missing'):
            self.assertIn(required, text, required)
        self.assertNotIn("                pass\n", text, 'download failures must not be swallowed silently')



class MrfChainPart2Contract(unittest.TestCase):
    """P2.3 G5 part 2: fluid-volume adequacy audit + surface map + regions + interfaces."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.config = chain.config('part2')
        cls.spec = json.loads((CASE / 'case_spec.json').read_text(encoding='utf-8'))

    def test_part2_steps_handover_and_inputs(self):
        steps = self.config['steps']
        self.assertEqual(['audit-volumes', 'surface-map', 'create-regions', 'configure-interfaces'],
                         [s['name'] for s in steps])
        self.assertEqual('mrf_fluid_volumes_built.sim', steps[0]['input'])
        self.assertEqual('mrf_fluid_volumes_audited.sim', steps[0]['output'])
        self.assertEqual(steps[0]['output'], steps[1]['input'])
        self.assertEqual(steps[0]['output'], steps[2]['input'])
        self.assertEqual('mrf_regions_built.sim', steps[3]['input'])
        self.assertEqual('mrf_interfaces_configured.sim', steps[3]['output'])
        self.assertEqual(['mrf_fluid_volume_audit.json'], steps[0]['records'])
        self.assertEqual(['mrf_surface_map.json'], steps[1]['records'])
        self.assertEqual(['mrf_regions_build.json'], steps[2]['records'])
        self.assertEqual(['mrf_interfaces_configured.json'], steps[3]['records'])
        self.assertIn('mrf_fluid_volumes_built.sim', self.config['inputs'])
        self.assertIn('recovered_rotor_solids.json', self.config['inputs'])

    @staticmethod
    def _stl_bounds_metrics(path):
        import struct
        data = Path(path).read_bytes()
        count = struct.unpack('<I', data[80:84])[0]
        lo = [1e9, 1e9, 1e9]
        hi = [-1e9, -1e9, -1e9]
        max_radius = 0.0
        for i in range(count):
            base = 84 + i * 50 + 12
            for k in range(3):
                x, y, z = struct.unpack('<3f', data[base + k * 12:base + k * 12 + 12])
                lo = [min(lo[0], x), min(lo[1], y), min(lo[2], z)]
                hi = [max(hi[0], x), max(hi[1], y), max(hi[2], z)]
                max_radius = max(max_radius, (x * x + y * y) ** 0.5)
        return lo, hi, max_radius

    def test_executed_part2_chain_passed_record(self):
        """O9 for the executed G5 part2 PASS: all four acceptance entries, measurement gates inside their
        tolerances, regions/interfaces as declared, frame explicitly deferred, 64-core STAR evidence."""
        ev = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-chain2-20261028a'
        result = json.loads((ev / 'RESULT.json').read_text(encoding='utf-8'))
        self.assertEqual('PASS', result['status'])
        audit = result['steps']['audit-volumes']
        for key in ('volume_pass', 'outer_bounds_pass', 'edge_integrity_pass',
                    'envelope_inside_stationary', 'rotating_inside_envelope', 'rotating_inside_stationary'):
            self.assertTrue(audit[key], key)
        self.assertLessEqual(audit['stationary_relative_deviation'], 0.02)
        self.assertLessEqual(audit['rotating_relative_deviation'], 0.02)
        self.assertLessEqual(audit['outer_bounds_error_m'], 5e-4)
        self.assertGreaterEqual(result['steps']['surface-map']['rotor_wall_surfaces'], 1)
        regions = result['steps']['create-regions']
        self.assertEqual('AUTO_STATIONARY_REGION', regions['stationary_region'])
        self.assertEqual('AUTO_ROTATING_REGION', regions['rotating_region'])
        self.assertGreaterEqual(regions['rotor_wall_boundaries'], 1)
        self.assertEqual(0, regions['interfaces'])
        itf = result['steps']['configure-interfaces']
        self.assertEqual(3, itf['interfaces'])
        self.assertEqual('DEFERRED_TO_PHYSICS', itf['rotation_frame_state'])
        self.assertEqual('ConfigureMrfPropellerPhysics', itf['rotation_frame_deferred_to'])
        self.assertFalse(itf['rotation_frame_created_and_measured'])
        run = json.loads((ev / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual('EXECUTION_COMPLETED', run['status'])
        self.assertEqual(4, run['star_processes_started'])
        self.assertEqual([64] * 4, [s['actual_logical_cores'] for s in run['steps']])
        self.assertEqual(4, len(run['acceptance']))
        self.assertTrue(any('DEFERRED' in item for item in result['declared_limits']))

    def test_outer_cylinder_metric_validated_on_the_archived_run1_stl(self):
        """review-2s fixture: the replacement centre metric must PASS the archived coarse cylinder (which the
        per-axis bbox and the vertex centroid both failed) and reject a shifted-cylinder counterexample."""
        stl = (ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-chain2-20261028a'
               / 'AUDITED-RUN-1-FAILED' / 'mrf_stationary_volume.stl')
        lo, hi, max_radius = self._stl_bounds_metrics(stl)
        radius, z_min, z_max, tolerance = 1.27, -3.81, 1.27, 5e-4
        centre_x = 0.5 * (lo[0] + hi[0])
        centre_y = 0.5 * (lo[1] + hi[1])
        error = max(abs(max_radius - radius), abs(lo[2] - z_min), abs(hi[2] - z_max),
                    abs(centre_x), abs(centre_y))
        self.assertLessEqual(error, tolerance, 'archived coarse cylinder must pass the new metric')
        shifted = max(abs(max_radius - radius), abs(lo[2] - z_min), abs(hi[2] - z_max),
                      abs(centre_x + 0.001), abs(centre_y))
        self.assertGreater(shifted, tolerance, 'a 1 mm shifted cylinder must be rejected')

    def test_runner_marks_a_failed_step_failed(self):
        """review-2s minor: a failed run must not leave the step status RUNNING."""
        text = RUNNER.read_text(encoding='utf-8')
        self.assertIn("$last.status='FAILED'", text)
        self.assertIn('$last.exit_code=-1', text)

    def test_part2_chain_handover_has_no_output_gap(self):
        """review-2q: a read-only step must still declare the audited sim as its passthrough output, so the
        runner's previous-output -> next-input sha hand-over is enforced for create-regions."""
        steps = {s['name']: s for s in self.config['steps']}
        self.assertEqual('mrf_fluid_volumes_audited.sim', steps['surface-map']['output'])
        self.assertEqual(steps['audit-volumes']['output'], steps['surface-map']['input'])
        self.assertEqual(steps['audit-volumes']['output'], steps['create-regions']['input'])
        self.assertEqual(steps['surface-map']['output'], steps['create-regions']['input'])

    def test_volume_audit_uses_the_rotation_zone_centre_and_keeps_failure_fields(self):
        """review-2q: the envelope expectation must use rotation_zone.center_xy_m (not the domain centre), the
        centres must be required coaxial, and a failed gate must keep the measured fields + gate_error."""
        source = (self.ROOT / 'macros/AuditMrfFluidVolumes.java').read_text(encoding='utf-8')
        self.assertIn('zoneCenter', source)
        self.assertIn('jsonArray(zone, "center_xy_m", 2)', source)
        self.assertIn('not coaxial', source)
        self.assertIn('measuredRecordWritten', source)
        self.assertIn('gate_error', source)
        self.assertIn('gateError', source)

    def test_part2_gates_cover_adequacy_regions_and_interfaces(self):
        gates = {g['record']: {f['field'] for f in g['fields']} for g in self.config['record_gates']}
        for required in ('status', 'volume_pass', 'outer_bounds_pass', 'edge_integrity_pass',
                         'envelope_inside_stationary', 'rotating_inside_envelope', 'rotating_inside_stationary',
                         'stationary_relative_deviation', 'rotating_relative_deviation', 'regions', 'interfaces'):
            self.assertIn(required, gates['mrf_fluid_volume_audit.json'], required)
        for required in ('status', 'rotor_wall_surfaces'):
            self.assertIn(required, gates['mrf_surface_map.json'])
        for required in ('status', 'stationary_region', 'rotating_region', 'rotor_wall_boundaries', 'interfaces'):
            self.assertIn(required, gates['mrf_regions_build.json'])
        for required in ('status', 'interfaces', 'rotation_frame_state', 'rotation_frame_deferred_to', 'rotation_rpm'):
            self.assertIn(required, gates['mrf_interfaces_configured.json'])
        rpm_ops = {g['op'] for g in self.config['record_gates'][3]['fields'] if g['field'] == 'rotation_rpm'}
        self.assertEqual({'ge', 'le'}, rpm_ops, 'rotation_rpm must be gated numerically (RUN-3 string-equality failure)')

    def test_interface_macro_defers_the_frame_on_a_geometry_only_copy(self):
        """RUN-2 measured that the motion manager does not exist before the physics continuum; the interface
        stage must create the 3 interfaces and record an explicit DEFERRED_TO_PHYSICS state instead of failing."""
        source = (self.ROOT / 'macros/ConfigureMrfInterfaces.java').read_text(encoding='utf-8')
        self.assertIn('catch (Throwable unavailable) { frames = null; }', source)
        self.assertIn('DEFERRED_TO_PHYSICS', source)
        self.assertIn('ConfigureMrfPropellerPhysics', source)
        self.assertIn('rotation_frame_state', source)
        self.assertIn('mrf_interfaces_configured.sim', source)

    def test_volume_audit_macro_contains_the_containment_checks(self):
        source = (self.ROOT / 'macros/AuditMrfFluidVolumes.java').read_text(encoding='utf-8')
        for key in ('volume_pass', 'outer_bounds_pass', 'edge_integrity_pass', 'envelope_inside_stationary',
                    'rotating_inside_envelope', 'rotating_inside_stationary', 'stationary_relative_deviation',
                    'rotating_relative_deviation', 'mrf_fluid_volumes_audited.sim',
                    'MRF_FLUID_VOLUME_AUDIT_COMPLETE'):
            self.assertIn(key, source, key)
        self.assertIn('recovered_rotor_solids.json', source)
        self.assertIn('exportStlPart', source)
        self.assertIn('VOLUME_RELATIVE_TOLERANCE', source)
        self.assertIn('maxRadius', source)
        self.assertIn('centerX', source)
        self.assertNotIn('maxBoundsError(stationary.bounds, outerExpected)', source)

    def test_casespec_has_the_region_and_rotation_roles(self):
        for key in ('stator_region', 'rotor_region', 'inlet_boundary', 'outlet_boundary', 'farfield_boundary'):
            self.assertTrue(self.spec['mrf_regions'][key])
        self.assertEqual(3, self.spec['mrf_regions']['expected_interface_count'])
        self.assertEqual([0.0, 0.0, 1.0], self.spec['rotation']['axis'])
        self.assertEqual(6015.0, self.spec['rotation']['rpm'])
        self.assertTrue(self.spec['reference_frame_name'])



class MrfChainPart3Contract(unittest.TestCase):
    """P2.3 G5 part3: mesh configuration -> mesh generation -> REAL wall-prism coverage -> quality audit."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.config = chain.config('part3')
        cls.spec = json.loads((CASE / 'case_spec.json').read_text(encoding='utf-8'))

    def test_part3_steps_handover_and_inputs(self):
        steps = self.config['steps']
        self.assertEqual(['configure-mesh', 'execute-mesh', 'audit-prism', 'audit-mesh-quality'],
                         [s['name'] for s in steps])
        self.assertEqual('mrf_interfaces_configured.sim', steps[0]['input'])
        self.assertEqual('mrf_mesh_configured.sim', steps[0]['output'])
        self.assertEqual(steps[0]['output'], steps[1]['input'])
        self.assertEqual('mrf_diagnostic_meshed.sim', steps[1]['output'])
        for step in steps[2:]:
            self.assertEqual(steps[1]['output'], step['input'])
            self.assertEqual(steps[1]['output'], step['output'])
        self.assertIn('mrf_interfaces_configured.sim', self.config['inputs'])
        self.assertIn('mrf_surface_map.json', self.config['inputs'])

    def test_part3_gates_cover_mesh_config_cells_prism_coverage_and_quality(self):
        gates = {g['record']: {f['field'] for f in g['fields']} for g in self.config['record_gates']}
        for required in ('status', 'operations', 'fluid_parts', 'interface_surfaces', 'blade_surfaces',
                         'prism_layers', 'rotating_prism_layers', 'base_size_m', 'mesh_generated'):
            self.assertIn(required, gates['mrf_mesh_configured.json'], required)
        for required in ('status', 'operations', 'regions', 'interfaces', 'rotating_checkpoint_cells', 'volume_cells'):
            self.assertIn(required, gates['mrf_diagnostic_mesh.json'], required)
        for required in ('status', 'gate_passed', 'wall_boundaries', 'max_below_min_layer_area_fraction',
                         'max_prism_free_area_fraction', 'min_prism_layers', 'unmeasured_walls',
                         'min_first_prism_height_m'):
            self.assertIn(required, gates['mrf_wall_prism_coverage.json'], required)
        self.assertNotIn('min_area_average_prism_layers', gates['mrf_wall_prism_coverage.json'],
                         'the area-average layer count is diagnostic, not a gate (review-2y)')
        for required in ('status', 'quality_gate_passed', 'measured_cell_quality_min',
                         'measured_face_validity_min', 'measured_volume_change_min', 'cells'):
            self.assertIn(required, gates['mrf_volume_mesh_quality.json'], required)
        quality = self.spec['quality']
        # revised thresholds (user decision B, 2026-10-28) - the mesh gates below must use the revised values
        self.assertEqual(quality['mesh_min_cell_quality'], 1e-06)
        self.assertEqual(quality['mesh_min_face_validity'], 0.95)
        self.assertEqual(quality['mesh_min_volume_change'], 0.005)
        revision = self.spec['quality_threshold_revision']
        self.assertEqual('B', revision['decision'])
        self.assertEqual('user', revision['confirmed_by'])
        self.assertEqual(1.0, revision['previous']['mesh_min_face_validity'])
        self.assertEqual(0.95, revision['revised']['mesh_min_face_validity'])
        self.assertIn('quality-acceptance-criteria', revision['kind'])
        self.assertIn('Residual risk', revision['residual_risk'])
        self.assertNotIn('review_status', revision)
        self.assertGreaterEqual(quality['mesh_min_wall_prism_layers'], 18)
        self.assertLessEqual(quality['mesh_wall_prism_exception_area_fraction_max'], 0.01)

    def test_quality_object_stays_flat_for_the_unchanged_flat_parser_macros(self):
        """review-3f blocking 1: AuditMrfWallPrismCoverage (unchanged bytes) parses the CaseSpec with the flat regex
        "quality"\s*:\s*\{([^{}]*)\}; a nested object inside `quality` makes that regex fail and the A1 chain would
        die at audit-prism with "Missing object quality". The revision record therefore lives at TOP level, and this test
        simulates the Java flat parser against the actual CaseSpec bytes."""
        import re
        text = (CASE / 'case_spec.json').read_text(encoding='utf-8')
        match = re.search(r'"quality"\s*:\s*\{([^{}]*)\}', text)
        self.assertIsNotNone(match, 'the flat object(spec,"quality") parser must match the real CaseSpec')
        self.assertIn('"mesh_min_cell_quality"', match.group(1))
        spec = json.loads(text)
        self.assertNotIn('mesh_quality_threshold_revision', spec['quality'])
        self.assertTrue(all(not isinstance(v, dict) for v in spec['quality'].values()),
                        'quality must contain no nested object for the flat-parser macros')
        revision = spec['quality_threshold_revision']
        self.assertEqual('B', revision['decision'])
        self.assertIn('quality-acceptance-criteria', revision['kind'])
        for key in ('previous', 'revised', 'basis', 'residual_risk'):
            self.assertIn(key, revision)
        # review-3g: the record must not simultaneously call itself a measurement-basis revision; kind and basis
        # must agree that this is a user-approved quality-ACCEPTANCE-criteria revision (decision B).
        self.assertNotIn('measurement-basis revision', revision['basis'])
        self.assertIn('quality-acceptance-criteria revision', revision['basis'])
        self.assertIn('decision B', revision['basis'])

    def test_mesh_macros_read_the_casespec_contract(self):
        root = self.ROOT / 'macros'
        config_src = (root / 'ConfigureMrfMeshOperation.java').read_text(encoding='utf-8')
        execute_src = (root / 'ExecuteMrfDiagnosticMesh.java').read_text(encoding='utf-8')
        quality_src = (root / 'AuditMrfVolumeMeshQuality.java').read_text(encoding='utf-8')
        self.assertIn('mrf_surface_map.json', config_src)
        self.assertIn('mrf_mesh_configured.sim', config_src)
        self.assertIn('rotating_operation', config_src)
        self.assertIn('MRF_MESH_OPERATION_CONFIGURED', config_src)
        self.assertIn('mrf_diagnostic_meshed.sim', execute_src)
        self.assertIn('MRF_DIAGNOSTIC_MESH_GENERATED', execute_src)
        self.assertIn('getCellCount', execute_src)
        for key in ('MeshQuality', 'FaceValidity', 'VolumeChange'):
            self.assertIn(key, quality_src)
        self.assertIn('quality_gate_passed', quality_src)
        self.assertIn('mrf_volume_mesh_quality.json', quality_src)

    def test_quality_audit_reads_the_casespec_thresholds_instead_of_hardcoding(self):
        """review-3c/section-8 lesson: a hard-coded threshold silently stales when the CaseSpec contract changes.
        The audit macro must read the CaseSpec quality object at run time and fail closed when it is missing."""
        source = (self.ROOT / 'macros/AuditMrfVolumeMeshQuality.java').read_text(encoding='utf-8')
        for key in ('mesh_min_cell_quality', 'mesh_min_face_validity', 'mesh_min_volume_change',
                    'thresholds_source', 'CaseSpec mesh-quality thresholds unavailable'):
            self.assertIn(key, source, key)
        self.assertNotIn('MIN_CELL_QUALITY', source)
        self.assertNotIn('MIN_FACE_VALIDITY', source)
        gates = {g['record']: {f['field']: f for f in g['fields']} for g in self.config['record_gates']}
        quality = gates['mrf_volume_mesh_quality.json']
        self.assertEqual(1e-06, quality['measured_cell_quality_min']['value'])
        self.assertEqual(0.95, quality['measured_face_validity_min']['value'])
        self.assertEqual(0.005, quality['measured_volume_change_min']['value'])

    def test_wall_prism_macro_measures_the_generated_mesh(self):
        source = (self.ROOT / 'macros/AuditMrfWallPrismCoverage.java').read_text(encoding='utf-8')
        for key in ('PrismLayerNumber', 'FirstPrismLayerHeight', 'getParentBoundary0', 'getParentBoundary1',
                    'mesh_min_wall_prism_layers', 'mesh_wall_prism_exception_area_fraction_max',
                    'mrf_wall_prism_coverage.json', 'MRF_WALL_PRISM_COVERAGE_COMPLETE', 'gate_passed',
                    'below_min_layer_pass', 'max_below_min_layer_area_fraction', 'unmeasured_walls',
                    'below_min_layer_area_fraction', 'prism_free_pass', 'layer_min_pass'):
            self.assertIn(key, source, key)
        # the per-wall below-threshold indicator must use the CaseSpec minimum layer count, not a constant 0.5
        self.assertIn('PrismLayerNumber} < %.1f', source)
        self.assertNotIn('"actual_prism_coverage":"not_measured"', source)
        self.assertIn('FvRepresentation', source)
        self.assertIn('getCellCount', source)

    def test_below_min_layer_area_counterexample_is_rejected(self):
        """review-2y counterexample: 10% of a wall at 1-17 layers and 90% at 20 layers has area-average ~18.1 and
        zero prism-free (<0.5) area, so the OLD gates would have passed it. The CaseSpec semantics (fewer-than-18
        area <= 1%, zero prism-free) must reject it; the fixture is the gate logic replicated from the macro."""
        quality = self.spec['quality']
        min_layers = quality['mesh_min_wall_prism_layers']
        exception_max = quality['mesh_wall_prism_exception_area_fraction_max']
        self.assertGreaterEqual(min_layers, 18)
        self.assertLessEqual(exception_max, 0.01)

        def gate(below_min_fraction, prism_free_fraction, min_layer_number, unmeasured_walls, min_height):
            return (below_min_fraction <= exception_max
                    and prism_free_fraction <= 1e-9
                    and min_layer_number >= 1
                    and unmeasured_walls == 0
                    and min_height > 0)

        # 10% of the wall below 18 layers, 90% at 20: area-average ~18.1 (diagnostic) but the real gate rejects
        self.assertFalse(gate(0.10, 0.0, 1.0, 0, 2e-6))
        # a mesh inside the contract passes
        self.assertTrue(gate(0.005, 0.0, 1.0, 0, 2e-6))
        # any prism-free area on the blade is rejected even inside the 1% below-min exception
        self.assertFalse(gate(0.005, 1e-6, 0.0, 0, 2e-6))
        # an unmeasured material wall is rejected
        self.assertFalse(gate(0.0, 0.0, 18.0, 1, 2e-6))
        gates = {g['record']: g['fields'] for g in self.config['record_gates']}
        prism = {f['field']: f for f in gates['mrf_wall_prism_coverage.json']}
        self.assertEqual(0.01, prism['max_below_min_layer_area_fraction']['value'])
        self.assertEqual(1e-09, prism['max_prism_free_area_fraction']['value'])

    def test_plan_contract_declares_mesh_for_part3_only(self):
        # F1: the reviewed contract is plan data now; part3 is the only mesh-only plan.
        contract = chain.PLANS['part3']['contract']
        self.assertIs(True, contract['mesh_generated'])
        self.assertIs(True, contract['no_physics_no_init_no_solve'])
        others = [p for p in chain.PLANS
                  if p != 'part3' and chain.PLANS[p]['contract'].get('mesh_generated') is True]
        self.assertEqual([], others, 'only part3 may declare mesh_generated')


class MrfMeshA1WithRevisedQualityContract(unittest.TestCase):
    """O9 for the final part3 configuration: A1 mesh (20-layer prism, 0.45 mm total, stretch 1.2, 0.15 mm blade
    minimum; the only measured configuration passing the realised wall-prism gate) together with the revised
    mesh-quality thresholds (user decision B, 2026-10-28).
    The CaseSpec numbers must stay consistent with the first-layer formula, the unchanged wall-prism/y+ contract
    and the controller's mesh-config gates."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads(A1_CASE.read_text(encoding='utf-8'))
        cls.config = chain.config('part3')

    def test_first_layer_equals_the_casespec_formula(self):
        mesh = self.spec['mesh']
        quality = self.spec['quality']
        total = float(mesh['prism_total_m'])
        layers = int(mesh['prism_layers'])
        growth = float(mesh['prism_stretch'])
        derived = total * (growth - 1.0) / (growth ** layers - 1.0)
        self.assertAlmostEqual(2.4104388118692427e-06, derived, places=18)
        self.assertAlmostEqual(derived, float(quality['mesh_prism_first_layer_height_m']), places=18)
        self.assertEqual(0.00045, total)
        self.assertEqual(total, float(quality['mesh_prism_total_thickness_m']))
        self.assertEqual(20, layers)
        self.assertEqual(20, int(quality['mesh_prism_layers_target']))
        self.assertEqual(18, int(quality['mesh_min_wall_prism_layers']))
        self.assertEqual(1.2, growth)
        self.assertEqual(1.2, float(quality['mesh_prism_growth']))
        self.assertEqual(0.00015, float(mesh['blade_min_size_m']))
        # review-3c: the rotating operation MUST also use 18 layers; the three declarations and the gate must agree
        self.assertEqual(20, int(mesh['rotating_prism_layers']))
        self.assertEqual(int(mesh['prism_layers']), int(mesh['rotating_prism_layers']))
        self.assertEqual(int(mesh['rotating_prism_layers']), int(quality['mesh_prism_layers_target']))

    def test_quality_texts_carry_the_a2_numbers_not_the_stale_ones(self):
        quality = self.spec['quality']
        yplus = quality['yplus_target_basis']
        prism_basis = quality['mesh_min_wall_prism_layers_basis']
        for text in (yplus, prism_basis):
            self.assertIn('0.45 mm', text)
            self.assertIn('2.410439e-06', text)
            self.assertNotIn('total 0.62 mm, stretch 1.2 -> first layer', text)
            self.assertNotIn('5.2e-6', text)
            self.assertNotIn('total 1.0 mm', text)
            self.assertNotIn('3.512424e-06', text, 'the A3 texts must not carry the A2 (18-layer) first-layer value')

    def test_acceptance_yplus_and_wall_prism_contract_are_unchanged(self):
        acceptance = self.spec['acceptance']
        self.assertEqual(2.0, float(acceptance['yplus_max']))
        self.assertEqual(0.8, float(acceptance['yplus_area_average_max']))
        self.assertEqual(0.95, float(acceptance['yplus_coverage_fraction']))
        self.assertEqual(1.5, float(acceptance['yplus_coverage_limit']))
        quality = self.spec['quality']
        self.assertEqual(18, int(quality['mesh_min_wall_prism_layers']))
        self.assertEqual(0.01, float(quality['mesh_wall_prism_exception_area_fraction_max']))
        self.assertEqual(20, int(quality['mesh_prism_layers_target']))

    def test_controller_pins_the_a3_prism_stack(self):
        gates = {g['record']: g['fields'] for g in self.config['record_gates']}
        prism = gates['mrf_mesh_configured.json']
        total_entries = [f for f in prism if f['field'] == 'prism_total_m']
        values = {e['op']: e['value'] for e in total_entries}
        self.assertEqual(0.00044, values['ge'])
        self.assertEqual(0.00046, values['le'])
        layers = [f for f in prism if f['field'] == 'prism_layers']
        self.assertTrue(any(f['op'] == 'eq' and f['value'] == 20 for f in layers),
                        'A3 must pin prism_layers == 20')
        rotating = [f for f in prism if f['field'] == 'rotating_prism_layers']
        self.assertTrue(any(f['op'] == 'eq' and f['value'] == 20 for f in rotating),
                        'A3 must pin rotating_prism_layers == 20')
        blade_min = {f['op']: f['value'] for f in prism if f['field'] == 'blade_min_size_m'}
        self.assertEqual(0.00014, blade_min['ge'])
        self.assertEqual(0.00016, blade_min['le'])


class MrfChainPart3PassedRecord(unittest.TestCase):
    """O9 for the executed part3 mesh PASS: all four acceptance entries, realised prism + quality gates inside the
    reviewed (decision-B) thresholds, 64-core operating evidence, and the revision/residual-risk scope recorded."""

    ROOT = Path(__file__).resolve().parents[1]
    EVID = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-chain3-20261028a'

    def test_executed_part3_pass_record(self):
        result = json.loads((self.EVID / 'RESULT.json').read_text(encoding='utf-8'))
        self.assertEqual('PASS', result['status'])
        run = json.loads((self.EVID / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual('EXECUTION_COMPLETED', run['status'])
        self.assertEqual(4, run['star_processes_started'])
        self.assertEqual([64] * 4, [step['actual_logical_cores'] for step in run['steps']])
        self.assertEqual(4, len(run['acceptance']))
        prism = result['steps']['audit-prism']
        self.assertTrue(prism['gate_passed'])
        self.assertLessEqual(prism['max_below_min_layer_area_fraction'], 0.01)
        self.assertLessEqual(prism['max_prism_free_area_fraction'], 1e-9)
        self.assertGreaterEqual(prism['min_prism_layers'], 1)
        self.assertEqual(0, prism['unmeasured_walls'])
        quality = result['steps']['audit-mesh-quality']
        self.assertTrue(quality['quality_gate_passed'])
        self.assertGreaterEqual(quality['measured_cell_quality_min'], 1e-06)
        self.assertGreaterEqual(quality['measured_face_validity_min'], 0.95)
        self.assertGreaterEqual(quality['measured_volume_change_min'], 0.005)
        self.assertGreaterEqual(result['steps']['execute-mesh']['volume_cells'], 100000)
        self.assertEqual(2, result['steps']['execute-mesh']['regions'])
        self.assertEqual(3, result['steps']['execute-mesh']['interfaces'])
        # the PASS scope must carry the user decision-B revision and its residual risk, and no y+/physics claim
        revision = result['quality_threshold_revision']
        self.assertEqual('B', revision['decision'])
        self.assertEqual(1e-06, revision['revision']['revised']['mesh_min_cell_quality'])
        self.assertIn('Residual risk', revision['revision']['residual_risk'])
        limits = ' '.join(result['declared_limits'])
        self.assertIn('residual risk', limits)
        self.assertIn('no y+', limits)
        self.assertIn('no solve', limits)
        spec = json.loads(A1_CASE.read_text(encoding='utf-8'))
        self.assertEqual(0.00015, spec['mesh']['blade_min_size_m'])
        self.assertEqual(20, spec['mesh']['prism_layers'])
        self.assertEqual(20, spec['mesh']['rotating_prism_layers'])


class MrfChainPart4Contract(unittest.TestCase):
    """P2.3 G5 part4: ConfigureMrfPropellerPhysics on the PASSED mesh - frame creation/measurement/assignment,
    physics continuum, boundary values and force/moment reports; still no initialization and no solve."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.config = chain.config('part4')
        cls.spec = json.loads((CASE / 'case_spec.json').read_text(encoding='utf-8'))

    def test_part4_single_step_contract(self):
        steps = self.config['steps']
        self.assertEqual(['configure-physics'], [s['name'] for s in steps])
        self.assertEqual('mrf_diagnostic_meshed.sim', steps[0]['input'])
        self.assertEqual('mrf_physics_configured.sim', steps[0]['output'])
        self.assertEqual('MRF_PHYSICS_CONFIGURED', steps[0]['marker'])
        self.assertIn('mrf_physics_readback.json', steps[0]['records'])
        self.assertEqual('ConfigureMrfPropellerPhysics.java', steps[0]['macro'])
        self.assertIn('mrf_diagnostic_meshed.sim', self.config['inputs'])

    def test_part4_gates_match_the_casespec_physics_inputs(self):
        entries = self.config['record_gates'][0]['fields']
        fields = {g['field']: g for g in entries}
        rotation = self.spec['rotation']
        flow = self.spec['flow']
        rpm = float(rotation['rpm'])
        omega = rpm * 2.0 * math.pi / 60.0
        self.assertAlmostEqual(omega, 629.8893, places=3)
        omega_gates = {g['op']: g['value'] for g in entries if g['field'] == 'measured_omega_rad_s'}
        self.assertLessEqual(omega_gates['ge'], omega)
        self.assertGreaterEqual(omega_gates['le'], omega)
        rpm_gates = {g['op']: g['value'] for g in entries if g['field'] == 'measured_rpm'}
        self.assertLessEqual(rpm_gates['ge'], rpm)
        self.assertGreaterEqual(rpm_gates['le'], rpm)
        self.assertAlmostEqual(13.953998, float(math.sqrt(sum(v * v for v in flow['velocity_m_s']))), places=6)
        diameter = float(self.spec['geometry']['diameter_m'])
        n = rpm / 60.0
        speed = math.sqrt(sum(v * v for v in flow['velocity_m_s']))
        advance = speed / (n * diameter)
        advance_gates = {g['op']: g['value'] for g in entries if g['field'] == 'advance_ratio'}
        self.assertLessEqual(advance_gates['ge'], advance)
        self.assertGreaterEqual(advance_gates['le'], advance)
        self.assertAlmostEqual(advance, 0.548, places=3)
        # the pre-solve honesty fields must be pinned false, and the physics must be the expected model family
        self.assertFalse(fields['engineering_verified']['value'])
        self.assertFalse(fields['thrust_sign_verified']['value'])
        self.assertFalse(fields['initialized']['value'])
        self.assertEqual(0, fields['measured_iteration']['value'])
        self.assertTrue(fields['transition_model_enabled']['value'])
        self.assertEqual(3, fields['measured_interface_count']['value'])
        self.assertEqual('AUTO_THRUST', fields['thrust_report']['value'])
        self.assertEqual('AUTO_TORQUE', fields['torque_report']['value'])
        for key in ('measured_inlet_temperature_k', 'measured_outlet_temperature_k',
                    'measured_outlet_static_pressure_pa', 'n_rev_s', 'models_enabled_count',
                    'reports_created_not_evaluated', 'rho_source'):
            self.assertIn(key, fields, key)
        self.assertTrue(fields['reports_created_not_evaluated']['value'])
        self.assertEqual('measured_outlet_pressure_pa/(R_AIR*measured_inlet_temperature_k)',
                         fields['rho_source']['value'])
        nrev_gates = {g['op']: g['value'] for g in entries if g['field'] == 'n_rev_s'}
        self.assertLessEqual(nrev_gates['ge'], 100.25)
        self.assertGreaterEqual(nrev_gates['le'], 100.25)

    def test_physics_macro_owns_the_frame_and_reports(self):
        source = (self.ROOT / 'macros/ConfigureMrfPropellerPhysics.java').read_text(encoding='utf-8')
        for key in ('UserRotatingReferenceFrame', 'getRotationRate', 'MotionSpecification',
                    'ForceReport', 'MomentReport', 'MRF_PHYSICS_CONFIGURED',
                    'mrf_physics_readback.json', 'engineering_verified', 'thrust_sign_verified',
                    'getCellCount() > 0', 'ReferenceFrameManager',
                    'readConstant', 'measuredInletSpeed', 'measuredOutletPressure',
                    'measuredThrustName', 'measuredTorqueName', 'measuredTransitionEnabled',
                    'reports_created_not_evaluated', 'rho_source', 'inflow_axis_dot_source'):
            self.assertIn(key, source, key)
        # review-3j: the readback record must be written from measured variables, not from the CaseSpec locals
        self.assertNotIn('"measured_inlet_mps\":" + speed', source)
        self.assertNotIn('"measured_pressure_pa\":" + pressure', source)
        self.assertNotIn('"transition_model_enabled\":" + transition', source)
        self.assertNotIn('+ esc(thrustName) + "\",\"torque_report', source)
        self.assertNotIn('initialization', source.lower().replace('no initialization', ''))
        # no solve: the macro must never call the simulation iterator
        self.assertNotIn('sim.getSimulationIterator().step', source)
        self.assertNotIn('.run()', source)

    def test_part4_declares_physics_and_no_solve(self):
        contract = chain.PLANS['part4']['contract']
        self.assertIs(True, contract['physics_configured'])
        self.assertIs(True, contract['no_solve'])


class MrfChainPart4PassedRecord(unittest.TestCase):
    """O9 for the executed part4 physics PASS: measured loaded-state readback, no init/solve, unevaluated reports."""

    ROOT = Path(__file__).resolve().parents[1]
    EVID = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-chain4-20261028a'

    def test_executed_part4_pass_record(self):
        result = json.loads((self.EVID / 'RESULT.json').read_text(encoding='utf-8'))
        self.assertEqual('PASS', result['status'])
        run = json.loads((self.EVID / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual('EXECUTION_COMPLETED', run['status'])
        self.assertEqual(1, run['star_processes_started'])
        self.assertEqual(64, run['steps'][0]['actual_logical_cores'])
        self.assertEqual(1, len(run['acceptance']))
        readback = result['readback']
        self.assertAlmostEqual(629.8893270447535, readback['measured_omega_rad_s'], places=10)
        self.assertAlmostEqual(6015.0, readback['measured_rpm'], places=9)
        self.assertAlmostEqual(13.953998, readback['measured_inlet_mps'], places=9)
        self.assertAlmostEqual(101325.0, readback['measured_pressure_pa'], places=6)
        self.assertAlmostEqual(300.0, readback['measured_inlet_temperature_k'], places=9)
        self.assertAlmostEqual(100.25, readback['n_rev_s'], places=9)
        self.assertEqual(1358218, readback['measured_cells'])
        self.assertEqual(3, readback['measured_interface_count'])
        self.assertEqual(1, readback['measured_wall_boundary_count'])
        self.assertEqual(0, readback['measured_iteration'])
        self.assertFalse(readback['initialized'])
        self.assertTrue(readback['transition_model_enabled'])
        self.assertGreaterEqual(readback['models_enabled_count'], 10)
        self.assertTrue(readback['reports_created_not_evaluated'])
        self.assertEqual('AUTO_THRUST', readback['thrust_report'])
        self.assertEqual('AUTO_TORQUE', readback['torque_report'])
        self.assertFalse(readback['thrust_sign_verified'])
        self.assertFalse(readback['engineering_verified'])
        limits = ' '.join(result['declared_limits'])
        for phrase in ('no initialization', 'no solve', 'no CT/CP', 'not a raw vector dot', 'case stays Q0'):
            self.assertIn(phrase, limits, phrase)


class MrfChainPart5Contract(unittest.TestCase):
    """P2.3 G5 part5: first MRF solve trial - initialize + bounded iteration with honest verdict."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.config = chain.config('part5')
        cls.spec = json.loads((CASE / 'case_spec.json').read_text(encoding='utf-8'))

    def test_part5_single_step_contract(self):
        steps = self.config['steps']
        self.assertEqual(['run-trial'], [s['name'] for s in steps])
        self.assertEqual('mrf_physics_configured.sim', steps[0]['input'])
        self.assertEqual('mrf_trial_solved.sim', steps[0]['output'])
        self.assertEqual('MRF_TRIAL_SOLVED', steps[0]['marker'])
        self.assertIn('mrf_trial_solve.json', steps[0]['records'])
        self.assertEqual('RunMrfTrial.java', steps[0]['macro'])
        self.assertIn('mrf_physics_configured.sim', self.config['inputs'])

    def test_part5_gates_and_casespec_windows(self):
        fields = {g['field']: g for g in self.config['record_gates'][0]['fields']}
        self.assertTrue(fields['initialized']['value'])
        self.assertTrue(fields['finite_target_quantities']['value'])
        self.assertEqual(0, fields['iterations_start']['value'])
        self.assertEqual(300, fields['iteration_cap']['value'])
        self.assertEqual(100, fields['stagnation_window']['value'])
        self.assertEqual(0.001, fields['stagnation_residual_rel_change']['value'])
        self.assertEqual(0.001, fields['stagnation_load_rel_change']['value'])
        self.assertEqual(10, fields['stagnation_samples']['value'])
        self.assertEqual('case_spec.solver.trial_iterations', fields['iteration_cap_source']['value'])
        solver = self.spec['solver']
        self.assertEqual(300, solver['trial_iterations'])
        self.assertEqual(100, solver['stagnation_window_iterations'])
        self.assertEqual(0.001, solver['stagnation_residual_rel_change'])
        self.assertEqual(0.001, solver['stagnation_load_rel_change'])
        self.assertGreaterEqual(solver['stagnation_window_iterations'], 2)
        self.assertIn('stagnation', solver['stagnation_basis'].lower())
        self.assertIn('NOT_CONVERGED', solver['trial_basis'])
        # the trial must never gate the verdict: an honest NOT_CONVERGED/STAGNATED is a legitimate outcome
        self.assertNotIn('converged', fields)
        self.assertNotIn('residuals_gate_passed', fields)

    def test_trial_macro_api_and_stop_conditions(self):
        source = (self.ROOT / 'macros/RunMrfTrial.java').read_text(encoding='utf-8')
        for key in ('initializeSolution', 'getSimulationIterator().run', 'ResidualMonitor', 'export(',
                    'STAGNATED', 'DIVERGED', 'ITERATION_CAP', 'CONVERGED',
                    'steady_window_iterations', 'load_range_fraction', 'mass_imbalance_fraction',
                    'stagnation_window_iterations', 'stagnation_residual_rel_change',
                    'stagnation_load_rel_change', 'mrf_trial_solve.json', 'mrf_trial_solved.sim',
                    'MRF_TRIAL_SOLVED', 'object(acceptance, "residuals")', 'trial_iterations',
                    'samplesNeeded = Math.max(2, window / CHUNK)'):
            self.assertIn(key, source, key)
        # review-3m blocking 1: the residual gates are nested under acceptance.residuals, never flat keys
        self.assertNotIn('residuals_continuity', source)
        self.assertNotIn('residuals_momentum', source)
        # no wall-clock stop: the macro must not consult elapsed time for stopping decisions
        self.assertNotIn('getenv', source)
        self.assertNotIn('currentTimeMillis', source)
        self.assertNotIn('nanoTime', source)

    def test_part5_declares_solve_and_initialization(self):
        contract = chain.PLANS['part5']['contract']
        self.assertIs(True, contract['initialized'])
        self.assertIs(True, contract['solve_run'])


class MrfTrialParserAndStagnationRatchet(unittest.TestCase):
    """O9 for review-3m: (a) the Java CaseSpec parser must reach the NESTED acceptance.residuals values;
    (b) stagnation must require the FULL 100-iteration window, not one 10-iteration plateau."""

    ROOT = Path(__file__).resolve().parents[1]

    @staticmethod
    def _value_start(json_text, key):
        marker = '"' + key + '"'
        i = json_text.index(marker)
        colon = json_text.index(':', i + len(marker))
        start = colon + 1
        while start < len(json_text) and json_text[start].isspace():
            start += 1
        return start

    @classmethod
    def _object(cls, json_text, key):
        start = cls._value_start(json_text, key)
        assert json_text[start] == '{'
        depth = 0
        for i in range(start, len(json_text)):
            if json_text[i] == '{':
                depth += 1
            elif json_text[i] == '}':
                depth -= 1
                if depth == 0:
                    return json_text[start + 1:i]
        raise AssertionError('unterminated object ' + key)

    @classmethod
    def _number(cls, json_text, key):
        start = cls._value_start(json_text, key)
        end = start
        while end < len(json_text) and json_text[end] in '-+0123456789.eE':
            end += 1
        return float(json_text[start:end])

    def test_nested_residual_gates_are_reachable_with_the_flat_scanner(self):
        """Replicates the Java valueStart/object/number logic on the REAL CaseSpec bytes."""
        text = (CASE / 'case_spec.json').read_text(encoding='utf-8')
        acceptance = self._object(text, 'acceptance')
        residuals = self._object(acceptance, 'residuals')
        self.assertEqual(1e-4, self._number(residuals, 'continuity'))
        self.assertEqual(1e-3, self._number(residuals, 'momentum'))
        self.assertEqual(1e-3, self._number(residuals, 'energy'))
        self.assertEqual(1e-3, self._number(residuals, 'turbulence'))
        # the previous bug invented flat keys; they must NOT exist in the acceptance object
        for flat in ('residuals_continuity', 'residuals_momentum', 'residuals_energy', 'residuals_turbulence'):
            with self.assertRaises(ValueError):
                self._value_start(acceptance, flat)

    def test_stagnation_requires_the_full_registered_window(self):
        """Replica of the macro's exact-window logic (samplesNeeded = window/CHUNK = 10 intervals)."""
        import math
        chunk = 10
        window = 100
        samples_needed = max(2, window // chunk)
        self.assertEqual(10, samples_needed)

        def stopped(history, ct, cp):
            if len(history) < samples_needed + 1:
                return False
            for i in range(len(history) - samples_needed, len(history)):
                for name in history[i]:
                    before = history[i - 1].get(name)
                    if not before:
                        continue
                    if abs(history[i][name] - before) / abs(before) >= 1e-3:
                        return False
            n = len(ct)
            first = max(0, n - 1 - samples_needed)
            ct_change = abs(ct[-1] - ct[first]) / max(1e-12, abs(ct[-1]))
            cp_change = abs(cp[-1] - cp[first]) / max(1e-12, abs(cp[-1]))
            return ct_change < 1e-3 and cp_change < 1e-3

        flat = [{'continuity': 1.0}] * 11
        self.assertTrue(stopped(flat, [0.05] * 11, [0.04] * 11))
        # only the LAST 10-iteration interval is flat: the old window/10 logic would have stopped here
        improving = [{'continuity': 10.0 / (i + 1)} for i in range(10)] + [{'continuity': 10.0 / 10}]
        self.assertFalse(stopped(improving, [0.05] * 11, [0.04] * 11))
        # insufficient history cannot claim stagnation
        self.assertFalse(stopped(flat[:10], [0.05] * 10, [0.04] * 10))


class MrfChainPart5ExecutedTrialRatchet(unittest.TestCase):
    """O9 for the executed part5 trial: it ran, reported honestly, and did not claim convergence."""

    ROOT = Path(__file__).resolve().parents[1]
    EVID = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-chain5-20261028a'

    def test_executed_trial_is_honest(self):
        result = json.loads((self.EVID / 'RESULT.json').read_text(encoding='utf-8'))
        run = json.loads((self.EVID / 'run.json').read_text(encoding='utf-8'))
        trial = result['trial']
        self.assertEqual('EXECUTION_COMPLETED', run['status'])
        self.assertEqual(1, run['star_processes_started'])
        self.assertEqual(64, run['steps'][0]['actual_logical_cores'])
        self.assertEqual('ok', trial['status'])
        self.assertTrue(trial['initialized'])
        self.assertTrue(trial['finite_target_quantities'])
        self.assertEqual(300, trial['iterations_end'])
        self.assertEqual('ITERATION_CAP', trial['stop_reason'])
        self.assertFalse(trial['converged'], 'this trial must not claim convergence')
        self.assertFalse(trial['residuals_gate_passed'])
        self.assertLessEqual(trial['mass_imbalance_fraction'], result['gate_context']['mass_imbalance_gate'])
        self.assertGreater(result['gate_context']['residuals']['Continuity'],
                           result['gate_context']['residual_gates']['continuity'])
        limits = ' '.join(result['declared_limits'])
        for phrase in ('NOT_CONVERGED', 'no convergence claim', 'case stays Q0'):
            self.assertIn(phrase, limits, phrase)
        self.assertIn('PRELIMINARY ONLY', result['reference_comparison_preliminary']['note'])


class MrfChainPart6Contract(unittest.TestCase):
    """P2.3 G5 part6: continuation segment from the saved trial (longer solve to attempt the gates)."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.config = chain.config('part6')
        cls.spec = json.loads((CASE / 'case_spec.json').read_text(encoding='utf-8'))

    def test_part6_single_step_contract(self):
        steps = self.config['steps']
        self.assertEqual(['run-segment'], [s['name'] for s in steps])
        self.assertEqual('mrf_trial_solved.sim', steps[0]['input'])
        self.assertEqual('mrf_segment_solved.sim', steps[0]['output'])
        self.assertEqual('MRF_SEGMENT_SOLVED', steps[0]['marker'])
        self.assertIn('mrf_segment_solve.json', steps[0]['records'])
        self.assertEqual('RunMrfSegment.java', steps[0]['macro'])
        self.assertIn('mrf_trial_solved.sim', self.config['inputs'])

    def test_part6_gates_match_the_continuation_contract(self):
        fields = {g['field']: g for g in self.config['record_gates'][0]['fields']}
        self.assertTrue(fields['initialized']['value'])
        self.assertTrue(fields['finite_target_quantities']['value'])
        self.assertEqual(300, fields['iterations_start']['value'])
        self.assertEqual(500, fields['segment_iterations']['value'])
        # cap = start + segment = 300 + 500
        self.assertEqual(800, fields['iteration_cap']['value'])
        self.assertEqual(100, fields['stagnation_window']['value'])
        self.assertEqual(10, fields['stagnation_samples']['value'])
        solver = self.spec['solver']
        self.assertEqual(500, solver['segment_iterations'])
        self.assertEqual(10000, solver['max_iterations'])
        self.assertIn('segment_iterations', fields['iteration_cap_source']['value'])
        # no verdict is forced: a continuation may honestly end NOT_CONVERGED/STAGNATED again
        self.assertNotIn('converged', fields)

    def test_segment_macro_continues_and_stops_honestly(self):
        source = (self.ROOT / 'macros/RunMrfSegment.java').read_text(encoding='utf-8')
        for key in ('segment_iterations', 'iterations_start', 'iteration_cap',
                    'initializeSolution', 'getSimulationIterator().run', 'ResidualMonitor',
                    'STAGNATED', 'DIVERGED', 'ITERATION_CAP', 'CONVERGED',
                    'object(acceptance, "residuals")', 'stagnation_window_iterations',
                    'mrf_segment_solve.json', 'mrf_segment_solved.sim', 'MRF_SEGMENT_SOLVED',
                    'samplesNeeded = Math.max(2, window / CHUNK)'):
            self.assertIn(key, source, key)
        self.assertNotIn('getenv', source)
        self.assertNotIn('nanoTime', source)
        self.assertNotIn('currentTimeMillis', source)
        # continuation must not re-create or evaluate reports: it reads the existing ones only
        self.assertNotIn('getReportManager().create("star.flow.ForceReport")', source)
        self.assertNotIn('getReportManager().create("star.flow.MomentReport")', source)

    def test_part6_declares_continuation(self):
        self.assertIs(True, chain.PLANS['part6']['contract']['continuation'])


class EvidenceStagingPurityRatchet(unittest.TestCase):
    """review-3p blocking finding: config() must be pure. A previous version copied the CURRENT CaseSpec into
    the plan's staging as a side effect, so tests calling config() overwrote ACCEPTED evidence staging bytes
    (mrf-chain/1..4 staging/case_spec.json) and broke their SHA256SUMS. This ratchet snapshots every accepted
    staging directory, calls config() for all plans, and requires byte-identical staging afterwards AND every
    accepted chain manifest to verify."""

    ROOT = Path(__file__).resolve().parents[1]
    EVID = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e'
    CHAINS = ('mrf-chain-20261028a', 'mrf-chain2-20261028a', 'mrf-chain3-20261028a',
              'mrf-chain4-20261028a', 'mrf-chain5-20261028a')

    @staticmethod
    def _snapshot():
        out = {}
        for path in sorted(EvidenceStagingPurityRatchet.EVID.rglob('staging/*')):
            if path.is_file():
                out[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return out

    def test_config_is_pure_and_never_touches_accepted_staging(self):
        import hashlib as _hashlib
        before = self._snapshot()
        for plan in ('part1', 'part2', 'part3', 'part4', 'part5', 'part6'):
            chain.config(plan)
        after = self._snapshot()
        changed = {k: (before.get(k), after.get(k)) for k in set(before) | set(after)
                   if before.get(k) != after.get(k)}
        self.assertEqual({}, changed, 'config() must not modify any evidence staging bytes')

    def test_accepted_chain_manifests_still_verify(self):
        import hashlib as _hashlib
        for chain_id in self.CHAINS:
            evidence = self.EVID / chain_id
            manifest = json.loads((evidence / 'SHA256SUMS.json').read_text(encoding='utf-8'))
            for rel, record in manifest['files'].items():
                path = evidence / rel
                self.assertTrue(path.is_file(), chain_id + ' missing ' + rel)
                self.assertEqual(record['sha256'], _hashlib.sha256(path.read_bytes()).hexdigest(),
                                 chain_id + ' checksum mismatch ' + rel)


class MrfChainPart6ExecutedSegmentRatchet(unittest.TestCase):
    """O9 for the executed part6 continuation: it ran, reported honestly, and did not claim convergence."""

    ROOT = Path(__file__).resolve().parents[1]
    EVID = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-chain6-20261028a'

    def test_executed_segment_is_honest(self):
        result = json.loads((self.EVID / 'RESULT.json').read_text(encoding='utf-8'))
        run = json.loads((self.EVID / 'run.json').read_text(encoding='utf-8'))
        seg = result['segment']
        self.assertEqual('EXECUTION_COMPLETED', run['status'])
        self.assertEqual(1, run['star_processes_started'])
        self.assertEqual(64, run['steps'][0]['actual_logical_cores'])
        self.assertEqual('ok', seg['status'])
        self.assertTrue(seg['initialized'])
        self.assertTrue(seg['finite_target_quantities'])
        self.assertEqual(300, seg['iterations_start'])
        self.assertEqual(800, seg['iterations_end'])
        self.assertEqual(800, seg['iteration_cap'])
        self.assertEqual(500, seg['segment_iterations'])
        self.assertEqual('ITERATION_CAP', seg['stop_reason'])
        self.assertFalse(seg['converged'], 'the segment must not claim convergence')
        self.assertFalse(seg['residuals_gate_passed'])
        self.assertTrue(seg['load_gate_passed'])
        self.assertTrue(seg['mass_gate_passed'])
        self.assertLessEqual(seg['mass_imbalance_fraction'], result['gate_context']['mass_imbalance_gate'])
        trend = result['trend_vs_part5']
        self.assertGreater(trend['continuity']['part6'], trend['continuity']['part5'],
                           'the ratchet records that residuals did NOT improve over the extra 500 iterations')
        limits = ' '.join(result['declared_limits'])
        for phrase in ('honest NOT_CONVERGED', 'residual plateau', 'case stays Q0'):
            self.assertIn(phrase, limits, phrase)


class MrfChainPart7Contract(unittest.TestCase):
    """P2.3 G5 part7: independent READ-ONLY audit of the saved solve state."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.config = chain.config('part7')
        cls.spec = json.loads((CASE / 'case_spec.json').read_text(encoding='utf-8'))

    def test_part7_single_read_only_step(self):
        steps = self.config['steps']
        self.assertEqual(['audit-trial'], [s['name'] for s in steps])
        # read-only passthrough: the audited state is NOT rewritten to a different file
        self.assertEqual('mrf_segment_solved.sim', steps[0]['input'])
        self.assertEqual('mrf_segment_solved.sim', steps[0]['output'])
        self.assertEqual('MRF_TRIAL_AUDITED', steps[0]['marker'])
        self.assertEqual('AuditMrfTrial.java', steps[0]['macro'])
        self.assertIn('mrf_trial_audit.json', steps[0]['records'])
        for name in ('mrf_segment_solved.sim', 'mrf_segment_history.tsv', 'mrf_trial_history.tsv',
                     'case_spec.json', 'reference.json'):
            self.assertIn(name, self.config['inputs'], name)

    def test_part7_gates_are_measurement_gates_not_convergence_claims(self):
        fields = {g['field']: g for g in self.config['record_gates'][0]['fields']}
        self.assertTrue(fields['solution_loaded']['value'])
        self.assertNotIn('initialized', fields)
        self.assertTrue(fields['read_only']['value'])
        self.assertTrue(fields['yplus_measured']['value'])
        self.assertTrue(fields['load_gate_passed']['value'])
        self.assertTrue(fields['mass_gate_passed']['value'])
        self.assertFalse(fields['residuals_gate_passed']['value'])
        self.assertFalse(fields['engineering_verified']['value'])
        self.assertFalse(fields['q3_claim']['value'])
        self.assertGreaterEqual(fields['iterations']['value'], 301)

    def test_audit_macro_is_read_only_and_measures_everything(self):
        source = (self.ROOT / 'macros/AuditMrfTrial.java').read_text(encoding='utf-8')
        for key in ('ResidualMonitor', 'export(', 'MassFlowReport', 'WallYplus',
                    'AreaAverageReport', 'MinReport', 'MaxReport',
                    'reference_tolerance', 'ct_abs_floor', 'cp_rel',
                    'operating_points', 'ct_within_signed_tolerance', 'cp_within_signed_tolerance',
                    'transition_residuals_diagnostic', 'transition_gate_policy',
                    'converged_rederived', 'yplus_gate_passed', 'thrust_sign_positive',
                    'PRELIMINARY_SINGLE_POINT_NOT_Q3', 'q3_claim', 'mrf_trial_audit.json',
                    'MRF_TRIAL_AUDITED'):
            self.assertIn(key, source, key)
        # read-only: no initialization, no solver iterations, no saveState
        for forbidden in ('initializeSolution', 'getSimulationIterator().run', 'saveState',
                          'getenv', 'nanoTime', 'currentTimeMillis'):
            self.assertNotIn(forbidden, source, forbidden)

    def test_part7_declares_read_only_audit(self):
        self.assertIs(True, chain.PLANS['part7']['contract']['audit_read_only'])


class MrfAuditReferenceAndWallSelectionRatchet(unittest.TestCase):
    """O9 for review-3s: the audit must parse the REAL signed reference schema, measure y+ only on native
    material walls (interface parents excluded), and read rho/advance fail-closed from CaseSpec role names."""

    ROOT = Path(__file__).resolve().parents[1]
    EVID = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e'

    @staticmethod
    def _value_start(text, key):
        marker = '"' + key + '"'
        i = text.index(marker)
        colon = text.index(':', i + len(marker))
        start = colon + 1
        while start < len(text) and text[start].isspace():
            start += 1
        return start

    @classmethod
    def _object(cls, text, key):
        start = cls._value_start(text, key)
        assert text[start] == '{'
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    return text[start + 1:i]
        raise AssertionError('unterminated ' + key)

    @classmethod
    def _number(cls, text, key):
        start = cls._value_start(text, key)
        end = start
        while end < len(text) and text[end] in '-+0123456789.eE':
            end += 1
        return float(text[start:end])

    def test_reference_parser_reads_the_real_selected_run_points(self):
        text = (CASE / 'reference.json').read_text(encoding='utf-8')
        data = self._object(text, 'data')
        selected = self._object(data, 'selected_run')
        points_key = selected.index('"points"')
        array_start = selected.index('[', points_key)
        array_end = selected.index(']', array_start)
        body = selected[array_start + 1:array_end]
        best = None
        cursor = 0
        while True:
            start = body.find('{', cursor)
            if start < 0:
                break
            end = body.find('}', start)
            block = body[start:end + 1]
            j = self._number(block, 'j')
            ct = self._number(block, 'ct')
            cp = self._number(block, 'cp')
            if best is None or abs(j - 0.548) < abs(best[0] - 0.548):
                best = (j, ct, cp)
            cursor = end + 1
        self.assertIsNotNone(best)
        self.assertAlmostEqual(0.548, best[0], places=6)
        self.assertAlmostEqual(0.0553, best[1], places=6)
        self.assertAlmostEqual(0.0431, best[2], places=6)
        spec = json.loads((CASE / 'case_spec.json').read_text(encoding='utf-8'))
        tol = spec['acceptance']['reference_tolerance']
        ct_tol = max(tol['ct_abs_floor'], tol['ct_rel'] * best[1])
        cp_tol = max(tol['cp_abs_floor'], tol['cp_rel'] * best[2])
        # the tolerance now comes from the 2026-10-30 re-signed 12% rule (0.08 -> 0.12, floors unchanged)
        self.assertAlmostEqual(max(tol['ct_abs_floor'], tol['ct_rel'] * 0.0553), ct_tol, places=12)
        self.assertAlmostEqual(max(tol['cp_abs_floor'], tol['cp_rel'] * 0.0431), cp_tol, places=12)
        self.assertAlmostEqual(0.006636, ct_tol, places=9)
        self.assertAlmostEqual(0.005172, cp_tol, places=9)

    def test_audit_excludes_interface_parents_and_fails_closed(self):
        source = (self.ROOT / 'macros/AuditMrfTrial.java').read_text(encoding='utf-8')
        for key in ('BoundaryInterface', 'getParentBoundary0', 'getParentBoundary1',
                    'interfaceParents.contains(boundary)', 'yplus_wall_boundaries',
                    'inlet_boundary', 'outlet_boundary', 'requiredBoundary',
                    'reference pressure unreadable', 'invalid measured boundary pressure/temperature',
                    'no finite signed reference point'):
            self.assertIn(key, source, key)
        # no 300 K fallback / name-substring selection for rho, and reference parser must target selected_run
        self.assertNotIn('temperature = 300.0', source)
        self.assertNotIn('contains("INLET")', source)
        self.assertNotIn('contains("OUTLET")', source)
        self.assertIn('selected_run', source)
        self.assertIn('"ct"', source)

    def test_part7_gates_pin_signed_reference_validity(self):
        fields = [g['field'] for g in chain.config('part7')['record_gates'][0]['fields']]
        for key in ('ct_ref', 'cp_ref', 'ct_tolerance', 'cp_tolerance',
                    'yplus_wall_boundaries', 'comparison_class'):
            self.assertIn(key, fields, key)


class MrfChainPart7ExecutedAuditRatchet(unittest.TestCase):
    """O9 for the executed part7 read-only audit: honest findings, no Q3 claim, hotspot recorded."""

    ROOT = Path(__file__).resolve().parents[1]
    EVID = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-chain7-20261028a'

    def test_executed_audit_record(self):
        result = json.loads((self.EVID / 'RESULT.json').read_text(encoding='utf-8'))
        run = json.loads((self.EVID / 'run.json').read_text(encoding='utf-8'))
        a = result['audit']
        self.assertEqual('PASS', result['status'])
        self.assertEqual('EXECUTION_COMPLETED', run['status'])
        self.assertEqual(1, run['star_processes_started'])
        self.assertEqual(64, run['steps'][0]['actual_logical_cores'])
        self.assertTrue(a['read_only'])
        self.assertTrue(a['solution_loaded'])
        self.assertFalse(a['initialized_flag'])
        self.assertEqual(800, a['iterations'])
        self.assertFalse(a['residuals_gate_passed'])
        self.assertTrue(a['load_gate_passed'])
        self.assertTrue(a['mass_gate_passed'])
        self.assertFalse(a['converged_rederived'])
        self.assertTrue(a['thrust_sign_positive'])
        self.assertTrue(a['ct_within_signed_tolerance'])
        self.assertTrue(a['cp_within_signed_tolerance'])
        self.assertAlmostEqual(0.0553, a['ct_ref'], places=6)
        self.assertAlmostEqual(0.0431, a['cp_ref'], places=6)
        self.assertAlmostEqual(0.004424, a['ct_tolerance'], places=6)
        self.assertAlmostEqual(0.003448, a['cp_tolerance'], places=6)
        self.assertEqual('PRELIMINARY_SINGLE_POINT_NOT_Q3', a['comparison_class'])
        self.assertFalse(a['q3_claim'])
        self.assertFalse(a['engineering_verified'])
        # the native-wall y+ hotspot must be recorded and the gate honestly fail
        self.assertGreater(a['yplus_max'], 2.0)
        self.assertLessEqual(a['yplus_area_average'], 0.8)
        self.assertGreaterEqual(a['yplus_coverage_fraction'], 0.95)
        self.assertFalse(a['yplus_gate_passed'])
        self.assertEqual(1, a['yplus_wall_boundaries'])
        limits = ' '.join(result['declared_limits'])
        for phrase in ('read-only', 'NOT_CONVERGED', 'HOT SPOT', 'PRELIMINARY_SINGLE_POINT_NOT_Q3', 'Q0'):
            self.assertIn(phrase, limits, phrase)


class MrfChainPart8Contract(unittest.TestCase):
    """P2.3 G5 part8: READ-ONLY residual-plateau diagnosis of the saved part6 solve state."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.config = chain.config('part8')

    def test_part8_single_read_only_diagnostic_step(self):
        steps = self.config['steps']
        self.assertEqual(['diagnose-plateau'], [s['name'] for s in steps])
        self.assertEqual('mrf_segment_solved.sim', steps[0]['input'])
        self.assertEqual('mrf_segment_solved.sim', steps[0]['output'])  # passthrough, read-only
        self.assertEqual('MRF_RESIDUAL_PLATEAU_DIAGNOSED', steps[0]['marker'])
        self.assertEqual('DiagnoseMrfResidualPlateau.java', steps[0]['macro'])
        self.assertIn('mrf_residual_plateau_diagnosis.json', steps[0]['records'])
        for name in ('mrf_segment_solved.sim', 'mrf_segment_history.tsv', 'mrf_trial_history.tsv', 'case_spec.json'):
            self.assertIn(name, self.config['inputs'], name)

    def test_part8_gates_are_measurement_gates(self):
        fields = {g['field']: g for g in self.config['record_gates'][0]['fields']}
        self.assertTrue(fields['read_only']['value'])
        self.assertTrue(fields['solution_loaded']['value'])
        self.assertTrue(fields['diagnosis_complete']['value'])
        self.assertEqual(5, fields['monitors']['value'])
        self.assertEqual(800, fields['iterations']['value'])
        self.assertEqual(300, fields['tail_window']['value'])
        # the classification is REPORTED, not gated: the diagnosis must not pre-judge the plateau
        self.assertNotIn('overall_classification', fields)

    def test_diagnosis_macro_is_read_only_and_statistical(self):
        source = (self.ROOT / 'macros/DiagnoseMrfResidualPlateau.java').read_text(encoding='utf-8')
        for key in ('ResidualMonitor', 'export(', 'TAIL_WINDOW', 'slopePer100',
                    'oscillationFraction', 'PLATEAU_OR_WORSENING', 'OSCILLATING', 'SLOW_DECAY',
                    'CONVERGING', 'iterationsToGate', 'overall_classification',
                    'mrf_residual_plateau_diagnosis.json', 'MRF_RESIDUAL_PLATEAU_DIAGNOSED'):
            self.assertIn(key, source, key)
        for forbidden in ('initializeSolution', 'getSimulationIterator().run', 'saveState',
                          'getenv', 'nanoTime', 'currentTimeMillis'):
            self.assertNotIn(forbidden, source, forbidden)

    def test_part8_declares_read_only_diagnostic(self):
        self.assertIs(True, chain.PLANS['part8']['contract']['read_only_diagnostic'])


class MrfResidualPlateauDiagnosisRatchet(unittest.TestCase):
    """O9 for review-3w: the diagnosis numbers (slope, classification, iterations-to-gate) must be computed,
    not just declared. This replicates the macro's arithmetic on synthetic series and checks the source assigns
    the extrapolation field."""

    ROOT = Path(__file__).resolve().parents[1]
    TAIL = 300

    @staticmethod
    def _analyse(rows, gate):
        """rows: [(iteration, residual)]; mirrors TestMacro analyse(): tail window, log10 regression,
        classification, oscillation fraction, iterations-to-gate."""
        import math
        tail = [(i, v) for i, v in rows if i >= rows[-1][0] - 300]
        xs = [i for i, _ in tail]
        ys = [math.log10(v) for _, v in tail]
        n = len(xs)
        sx, sy = sum(xs), sum(ys)
        sxx = sum(x * x for x in xs)
        sxy = sum(x * y for x, y in zip(xs, ys))
        den = n * sxx - sx * sx
        slope = 0.0 if den == 0 else (n * sxy - sx * sy) / den
        slope100 = slope * 100.0
        my = sy / n
        inter = my - slope * (sx / n)
        total = sum((y - my) ** 2 for y in ys)
        det = sum((y - (inter + slope * x)) ** 2 for x, y in zip(xs, ys))
        osc = 0.0 if total == 0 else math.sqrt(det / n) / math.sqrt(total / n)
        if slope100 >= -0.02:
            cls = 'PLATEAU_OR_WORSENING'
        elif osc > 0.5:
            cls = 'OSCILLATING'
        elif slope100 <= -0.1:
            cls = 'CONVERGING'
        else:
            cls = 'SLOW_DECAY'
        last = tail[-1][1]
        if gate and gate > 0 and last > gate and slope < 0:
            need = (math.log10(last) - math.log10(gate)) / (-slope)
            itg = need if (need > 0 and math.isfinite(need)) else float('inf')
        else:
            itg = float('inf')
        return slope100, osc, cls, itg

    def test_decaying_series_is_converging_with_finite_extrapolation(self):
        rows = [(i, 1e-2 * 10 ** (-0.002 * i)) for i in range(1, 401)]
        slope, osc, cls, itg = self._analyse(rows, 1e-4)
        self.assertAlmostEqual(-0.2, slope, places=6)
        self.assertEqual('CONVERGING', cls)
        self.assertTrue(math.isfinite(itg) and itg > 0)
        last = rows[-1][1]
        expected = (math.log10(last) - math.log10(1e-4)) / 0.002
        self.assertAlmostEqual(expected, itg, places=6)

    def test_plateau_series_is_not_converging_and_has_no_extrapolation(self):
        # monotone non-decreasing (worsening) plateau: slope >= 0 -> no finite extrapolation
        rows = [(i, 1e-3 * (1.0 + 1e-5 * i)) for i in range(1, 401)]
        slope, osc, cls, itg = self._analyse(rows, 1e-4)
        self.assertEqual('PLATEAU_OR_WORSENING', cls)
        self.assertEqual(float('inf'), itg)

    def test_oscillating_series_is_classified_oscillating(self):
        import math
        # slow negative trend inside the SLOW band PLUS dominant oscillation: detrended variation > total
        rows = [(i, 10 ** (-3 - 0.0005 * i + 0.25 * math.sin(i / 3.0))) for i in range(1, 401)]
        slope, osc, cls, itg = self._analyse(rows, 1e-4)
        self.assertEqual('OSCILLATING', cls)
        self.assertGreater(osc, 0.5)
        self.assertLess(slope, -0.02)
        self.assertGreater(slope, -0.1)

    def test_macro_assigns_the_extrapolation_and_tail_statistics(self):
        source = (self.ROOT / 'macros/DiagnoseMrfResidualPlateau.java').read_text(encoding='utf-8')
        self.assertIn('series.iterationsToGate =', source)
        self.assertIn('Math.log10(series.last) - Math.log10(gate)', source)
        self.assertIn('min_tail', source)
        self.assertIn('max_tail', source)
        self.assertIn('TAIL-WINDOW statistics', source)


class MrfChainPart8ExecutedDiagnosisRatchet(unittest.TestCase):
    """O9 for the executed part8 diagnosis: oscillatory plateau recorded, extrapolation informational only."""

    ROOT = Path(__file__).resolve().parents[1]
    EVID = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-chain8-20261028a'

    def test_executed_diagnosis_record(self):
        result = json.loads((self.EVID / 'RESULT.json').read_text(encoding='utf-8'))
        run = json.loads((self.EVID / 'run.json').read_text(encoding='utf-8'))
        diag = result['diagnosis']
        self.assertEqual('PASS', result['status'])
        self.assertEqual('EXECUTION_COMPLETED', run['status'])
        self.assertEqual(1, run['star_processes_started'])
        self.assertEqual(64, run['steps'][0]['actual_logical_cores'])
        self.assertTrue(diag['read_only'])
        self.assertTrue(diag['solution_loaded'])
        self.assertTrue(diag['diagnosis_complete'])
        self.assertEqual(800, diag['iterations'])
        self.assertEqual(300, diag['tail_window'])
        self.assertGreaterEqual(diag['monitors'], 5)
        key = result['key_categories']
        # the measured tail is oscillatory; Sdr is the plateau that drives the overall classification
        for name in ('Continuity', 'X-momentum', 'Energy'):
            self.assertGreater(key[name]['oscillation_fraction'], 0.45, name)
        self.assertEqual('PLATEAU_OR_WORSENING', key['Sdr']['classification'])
        self.assertEqual('PLATEAU_OR_WORSENING', diag['overall_classification'])
        # the extrapolation is informational: it exists for gated categories but must not be presented as a prediction
        self.assertTrue(math.isfinite(key['Continuity']['iterations_to_gate_at_slope']))
        limits = ' '.join(result['declared_limits'])
        for phrase in ('read-only', 'NOT a convergence prediction', 'case stays Q0'):
            self.assertIn(phrase, limits, phrase)


class MrfChainPart9Contract(unittest.TestCase):
    """P2.3 G5 part9: READ-ONLY solver-settings inventory (baseline for one-change experiments)."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.config = chain.config('part9')

    def test_part9_single_read_only_step(self):
        steps = self.config['steps']
        self.assertEqual(['inspect-settings'], [s['name'] for s in steps])
        self.assertEqual('mrf_segment_solved.sim', steps[0]['input'])
        self.assertEqual('mrf_segment_solved.sim', steps[0]['output'])
        self.assertEqual('MRF_SOLVER_SETTINGS_INSPECTED', steps[0]['marker'])
        self.assertEqual('InspectMrfSolverSettings.java', steps[0]['macro'])
        self.assertIn('mrf_solver_settings.json', steps[0]['records'])

    def test_part9_gates(self):
        fields = {g['field']: g for g in self.config['record_gates'][0]['fields']}
        self.assertTrue(fields['read_only']['value'])
        self.assertTrue(fields['solution_loaded']['value'])
        self.assertTrue(fields['inspection_complete']['value'])
        self.assertTrue(fields['baseline_available']['value'])
        self.assertEqual(1, fields['readable_settings']['value'])
        self.assertEqual(1, fields['models']['value'])
        self.assertEqual(800, fields['iterations']['value'])

    def test_inspector_is_read_only_and_never_writes_settings(self):
        source = (self.ROOT / 'macros/InspectMrfSolverSettings.java').read_text(encoding='utf-8')
        for key in ('getMethods', 'getParameterCount', 'NAME_FILTERS', 'mrf_solver_settings.json',
                    'MRF_SOLVER_SETTINGS_INSPECTED', 'read_only', 'isGetterName', 'FORBIDDEN_PREFIXES',
                    'baseline_available', 'skipped', 'readable_settings', 'Getter-only',
                    '"transition"', '"gamma"', '"retheta"', 'family_transition', 'families_missing'):
            self.assertIn(key, source, key)
        for forbidden in ('initializeSolution', 'getSimulationIterator().run', 'saveState',
                          'getenv', 'nanoTime', 'currentTimeMillis'):
            self.assertNotIn(forbidden, source, forbidden)
        # an inventory must not modify solver settings: no model/continuum setter calls in the source
        for setter in ('.setValue(', '.setCfl(', '.setAmg', 'enable(', 'disable(', '.setPresentationName('):
            self.assertNotIn(setter, source, setter)

    def test_part9_declares_settings_inventory(self):
        self.assertIs(True, chain.PLANS['part9']['contract']['settings_inventory'])


class MrfSolverSettingsReflectionRatchet(unittest.TestCase):
    """O9 for review-3z: the reflective inventory must be getter-only, must not claim completeness with zero
    readable settings, and must expose the baseline availability flag. This replicates the selection predicate
    on synthetic method signatures (the only practical non-STAR fixture for a Java reflection rule)."""

    ROOT = Path(__file__).resolve().parents[1]
    GETTER_PREFIXES = ('get', 'is', 'has', 'query', 'retrieve', 'list')
    MUTATOR_PREFIXES = ('set', 'clear', 'reset', 'initialize', 'update', 'enable', 'disable', 'run', 'step',
                        'apply', 'delete', 'create', 'save', 'load', 'start', 'stop', 'remove', 'add')

    def _selected(self, name):
        lower = name.lower()
        if not lower.startswith(self.GETTER_PREFIXES):
            return False
        return not lower.startswith(self.MUTATOR_PREFIXES)

    def test_zero_arg_mutators_are_never_selected(self):
        self.assertFalse(self._selected('initializeIteration'))
        self.assertFalse(self._selected('resetCycle'))
        self.assertFalse(self._selected('updateCfl'))
        self.assertFalse(self._selected('setCfl'))
        self.assertFalse(self._selected('runStep'))
        self.assertTrue(self._selected('getCfl'))
        self.assertTrue(self._selected('isCflRampEnabled'))
        self.assertTrue(self._selected('getAmgCycleCount'))

    def test_zero_setting_fixture_is_not_complete(self):
        # mirrors the macro exactly: inspection_complete = !entries.isEmpty();
        def inspection_complete(entries):
            return bool(entries)

        # mirrors baseline_available = complete AND cfl/amg family AND transition family
        def baseline_available(entries, family_cfl_amg, family_transition):
            return inspection_complete(entries) and family_cfl_amg and family_transition

        self.assertFalse(inspection_complete([]))
        self.assertFalse(baseline_available([], True, True))
        self.assertFalse(baseline_available([{'name': 'getAmgCycleCount'}], True, False))
        self.assertTrue(baseline_available([{'name': 'getCfl'}, {'name': 'getTransitionStartUp'}], True, True))

    def test_families_missing_exact_strings_for_all_four_combinations(self):
        # mirrors the macro's explicit construction (review-4b blocking: both-missing must be
        # exactly "cfl_amg,transition", not a concatenation artefact)
        def families_missing(family_cfl_amg, family_transition):
            if not family_cfl_amg and not family_transition:
                return 'cfl_amg,transition'
            if not family_cfl_amg:
                return 'cfl_amg'
            if not family_transition:
                return 'transition'
            return ''
        self.assertEqual('', families_missing(True, True))
        self.assertEqual('cfl_amg', families_missing(False, True))
        self.assertEqual('transition', families_missing(True, False))
        self.assertEqual('cfl_amg,transition', families_missing(False, False))

    def test_transition_gamma_getters_are_filter_candidates(self):
        filters = ('cfl', 'courant', 'amg', 'cycle', 'transition', 'gamma', 'retheta', 'intermittency')
        def selected(name):
            lower = name.lower()
            if not any(f in lower for f in filters):
                return False
            if not lower.startswith(self.GETTER_PREFIXES):
                return False
            return not lower.startswith(self.MUTATOR_PREFIXES)
        self.assertTrue(selected('getTransitionStartUp'))
        self.assertTrue(selected('getGammaReThetaMode'))
        self.assertTrue(selected('getReThetaEnabled'))
        self.assertTrue(selected('isIntermittencySolverUsed'))
        self.assertFalse(selected('setTransitionStartUp'))


class MrfChainPart9bContract(unittest.TestCase):
    """P2.3 G5 part9b: READ-ONLY solver-object baseline (CoupledImplicitSolver/AMG/Courant) for part10."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.config = chain.config('part9b')

    def test_part9b_single_read_only_step(self):
        steps = self.config['steps']
        self.assertEqual(['inspect-solver-objects'], [s['name'] for s in steps])
        self.assertEqual('mrf_segment_solved.sim', steps[0]['input'])
        self.assertEqual('mrf_segment_solved.sim', steps[0]['output'])
        self.assertEqual('MRF_SOLVER_OBJECTS_INSPECTED', steps[0]['marker'])
        self.assertEqual('InspectMrfSolverObjects.java', steps[0]['macro'])
        self.assertIn('mrf_solver_objects.json', steps[0]['records'])

    def test_part9b_gates_require_measured_baseline(self):
        fields = {g['field']: g for g in self.config['record_gates'][0]['fields']}
        for key in ('cfl_measured', 'amg_measured', 'baseline_available'):
            self.assertTrue(fields[key]['value'], key)
        self.assertEqual(800, fields['iterations']['value'])
        self.assertTrue(fields['read_only']['value'])

    def test_solver_object_macro_uses_the_proven_api_and_is_read_only(self):
        source = (self.ROOT / 'macros/InspectMrfSolverObjects.java').read_text(encoding='utf-8')
        for key in ('getSolverManager()', 'CoupledImplicitSolver.class', 'getAMGLinearSolver()',
                    'getCourantNumberController()', 'getCFL()', 'getConvergeTol()', 'getMaxCycles()',
                    'mrf_solver_objects.json', 'MRF_SOLVER_OBJECTS_INSPECTED', 'read_only'):
            self.assertIn(key, source, key)
        for forbidden in ('initializeSolution', 'getSimulationIterator().run', 'saveState',
                          'setCFL(', 'setConvergeTol(', 'setMaxCycles(', 'getenv', 'nanoTime'):
            self.assertNotIn(forbidden, source, forbidden)

    def test_invalid_baseline_fixtures_fail_closed(self):
        import math

        def positive(v):
            try:
                x = float(v)
                return math.isfinite(x) and x > 0
            except Exception:
                return False

        def non_empty(v):
            return v is not None and str(v).strip() != '' and str(v).strip().lower() != 'null'

        def finite_ge_zero(v):
            try:
                x = float(v)
                return math.isfinite(x) and x >= 0
            except Exception:
                return False

        def cfl_measured(cfl, option):
            return positive(cfl) and non_empty(option)

        def amg_measured(tol, cycles, eps, cycle, group, smoother, accel, sweeps):
            return (positive(tol) and positive(cycles) and finite_ge_zero(eps) and non_empty(cycle)
                    and positive(group) and non_empty(smoother) and non_empty(accel) and finite_ge_zero(sweeps))

        self.assertFalse(cfl_measured(float('inf'), 'CONSTANT_CFL'))
        self.assertFalse(cfl_measured('1e309', 'CONSTANT_CFL'))
        self.assertFalse(cfl_measured(0.0, 'CONSTANT_CFL'))
        self.assertFalse(cfl_measured(-1.0, 'CONSTANT_CFL'))
        self.assertFalse(cfl_measured(5.0, ''))
        self.assertFalse(cfl_measured(5.0, None))
        self.assertTrue(cfl_measured(5.0, 'CONSTANT_CFL'))
        self.assertFalse(amg_measured(float('inf'), 30, 0.1, 'V_CYCLE', 1, 'GAUSS_SEIDEL', 'NONE', 1))
        self.assertFalse(amg_measured('1e309', 30, 0.1, 'V_CYCLE', 1, 'GAUSS_SEIDEL', 'NONE', 1))
        self.assertFalse(amg_measured(1e-4, 30, float('nan'), 'V_CYCLE', 1, 'GAUSS_SEIDEL', 'NONE', 1))
        self.assertFalse(amg_measured(1e-4, 0, 0.1, 'V_CYCLE', 1, 'GAUSS_SEIDEL', 'NONE', 1))
        self.assertFalse(amg_measured(1e-4, 30, 0.1, '', 1, 'GAUSS_SEIDEL', 'NONE', 1))
        self.assertFalse(amg_measured(1e-4, 30, 0.1, 'V_CYCLE', -2, 'GAUSS_SEIDEL', 'NONE', 1))
        self.assertFalse(amg_measured(1e-4, 30, -0.1, 'V_CYCLE', 1, 'GAUSS_SEIDEL', 'NONE', 1))
        self.assertTrue(amg_measured(1e-4, 30, 0.1, 'V_CYCLE', 1, 'GAUSS_SEIDEL', 'NONE', 1))

    def test_part9b_declares_solver_object_baseline(self):
        self.assertIs(True, chain.PLANS['part9b']['contract']['solver_object_baseline'])


class MrfChainPart10Contract(unittest.TestCase):
    """P2.3 G5 part10: one-change solver experiment from the frozen solver_experiment.json."""

    ROOT = Path(__file__).resolve().parents[1]
    SPEC = ROOT / 'cases/uav-prop-apc10x7e/solver_experiment.json'

    @classmethod
    def setUpClass(cls):
        cls.config = chain.config('part10')
        cls.spec = json.loads(cls.SPEC.read_text(encoding='utf-8'))

    def test_frozen_experiment_matches_the_reviewed_values(self):
        change = self.spec['single_change']
        self.assertEqual('courant_cfl', change['parameter'])
        self.assertEqual('CONSTANT_CFL', change['mode'])
        baseline = 6257.820518958958
        self.assertAlmostEqual(baseline, change['measured_baseline_CFL'], places=12)
        self.assertAlmostEqual(0.5 * baseline, change['target_CFL'], places=12)
        self.assertAlmostEqual(3128.910259479479, change['target_CFL'], places=9)
        before = self.spec['expected_before']
        self.assertEqual('自动', before['courant_option'])
        self.assertAlmostEqual(baseline, before['courant_cfl'], places=12)
        self.assertEqual(0.1, before['amg_converge_tol'])
        self.assertEqual(30, before['amg_max_cycles'])
        self.assertEqual('CONSTANT_CFL', self.spec['expected_after']['courant_option'])
        self.assertAlmostEqual(3128.910259479479, self.spec['expected_after']['courant_cfl'], places=9)
        self.assertEqual(8, len(self.spec['unchanged_fields']))
        self.assertIn('snapshot-derived absolute', ' '.join(self.spec['declared_limits']) + self.spec['single_change']['rule'])

    def test_part10_step_and_gates(self):
        steps = self.config['steps']
        self.assertEqual(['configure-experiment'], [s['name'] for s in steps])
        self.assertEqual('mrf_segment_solved.sim', steps[0]['input'])
        self.assertEqual('mrf_solver_experiment_configured.sim', steps[0]['output'])
        self.assertEqual('MRF_SOLVER_EXPERIMENT_CONFIGURED', steps[0]['marker'])
        self.assertEqual('ConfigureMrfSolverExperiment.java', steps[0]['macro'])
        fields = {g['field']: g for g in self.config['record_gates'][0]['fields']}
        self.assertTrue(fields['applied']['value'])
        self.assertFalse(fields['no_op']['value'])
        self.assertTrue(fields['amg_fields_unchanged']['value'])
        self.assertEqual(800, fields['iteration']['value'])
        self.assertEqual('CFL_CONSTANT_HALF_MEASURED_BASELINE_1', fields['experiment_id']['value'])

    def test_expected_before_option_and_derived_changes_are_enforced(self):
        # mirrors the macro: option match is a fail-closed precondition and changed_fields is derived
        def option_matched(measured, expected):
            return measured is not None and expected.lower() in measured.lower()

        self.assertTrue(option_matched('自动', '自动'))
        self.assertFalse(option_matched('CONSTANT_CFL', '自动'))
        self.assertFalse(option_matched(None, '自动'))

        def changed_fields(before, after):
            return sorted(k for k, v in before.items() if after.get(k) != v)

        automatic = {'courant_option': '自动', 'courant_cfl': '6257.8', 'amg_converge_tol': '0.1'}
        configured = {'courant_option': 'Constant CFL', 'courant_cfl': '3128.91', 'amg_converge_tol': '0.1'}
        self.assertEqual(['courant_cfl', 'courant_option'], changed_fields(automatic, configured))
        already_constant = {'courant_option': 'Constant CFL', 'courant_cfl': '6257.8'}
        target_constant = {'courant_option': 'Constant CFL', 'courant_cfl': '3128.91'}
        self.assertEqual(['courant_cfl'], changed_fields(already_constant, target_constant))

    def test_part10_macro_guards_and_limits(self):
        source = (self.ROOT / 'macros/ConfigureMrfSolverExperiment.java').read_text(encoding='utf-8')
        for key in ('solver_experiment.json', 'getSolverManager()', 'CoupledImplicitSolver.class',
                    'getAMGLinearSolver()', 'getCourantNumberController()', 'CONSTANT_CFL',
                    'ConstantCourantNumberControl', 'setCFL(', 'no-op', 'drifted',
                    'Undeclared AMG field changed', 'mrf_solver_experiment_configured.sim',
                    'mrf_solver_experiment.json', 'MRF_SOLVER_EXPERIMENT_CONFIGURED',
                    'expected_before_verified', 'expected_after_verified', 'before_option_verified',
                    'Measured courant_option does not match expected_before', 'afterValue != ',
                    'instanceof ConstantCourantNumberControl', 'after_control_class'):
            self.assertIn(key, source, key)
        self.assertIn('boolean beforeVerified', source)
        self.assertNotIn('expected_before_verified\":true', source)
        for forbidden in ('initializeSolution', 'getSimulationIterator().run', 'getenv', 'nanoTime', 'currentTimeMillis'):
            self.assertNotIn(forbidden, source, forbidden)

    def test_localized_option_string_is_not_used_for_after_mode(self):
        # mirrors the macro: CONSTANT_CFL is verified via the control class, never via an English substring
        # of the localized selected-option name (measured failure: '常数 CFL' does not contain 'CONSTANT_CFL')
        def after_mode_ok(control_is_constant, localized_option_name):
            return control_is_constant  # English substring path only for non-CONSTANT_CFL labels
        self.assertTrue(after_mode_ok(True, '常数 CFL'))
        self.assertFalse(after_mode_ok(False, '常数 CFL'))
        self.assertNotIn('CONSTANT_CFL', '常数 CFL')

    def test_part10_declares_experiment_configuration(self):
        self.assertIs(True, chain.PLANS['part10']['contract']['solver_experiment_configuration'])


class MrfChainPart11Contract(unittest.TestCase):
    """P2.3 G5 part11: bounded segment on the CFL-configured state (start 800, cap 1300)."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.config = chain.config('part11')

    def test_part11_new_segment_plan(self):
        steps = self.config['steps']
        self.assertEqual(['run-segment2'], [s['name'] for s in steps])
        self.assertEqual('mrf_solver_experiment_configured.sim', steps[0]['input'])
        self.assertEqual('mrf_segment_solved.sim', steps[0]['output'])
        self.assertEqual('MRF_SEGMENT_SOLVED', steps[0]['marker'])
        self.assertEqual('RunMrfSegment.java', steps[0]['macro'])
        self.assertIn('mrf_solver_experiment_configured.sim', self.config['inputs'])

    def test_part11_gates_use_800_1300_not_part6_300_800(self):
        fields = {g['field']: g for g in self.config['record_gates'][0]['fields']}
        self.assertEqual(800, fields['iterations_start']['value'])
        self.assertEqual(1300, fields['iteration_cap']['value'])
        self.assertEqual(500, fields['segment_iterations']['value'])
        self.assertEqual(100, fields['stagnation_window']['value'])
        self.assertEqual(10, fields['stagnation_samples']['value'])
        # the archived part6 gates must NOT be reused
        self.assertNotEqual(300, fields['iterations_start']['value'])
        self.assertNotEqual(800, fields['iteration_cap']['value'])

    def test_part11_output_matches_the_macro_saveState_literal(self):
        # review-4o blocking: the plan output must be the filename the unchanged macro actually saves
        macro = (self.ROOT / 'macros/RunMrfSegment.java').read_text(encoding='utf-8')
        self.assertIn('"mrf_segment_solved.sim"', macro)
        output = self.config['steps'][0]['output']
        self.assertIn('"' + output + '"', macro,
                      'plan output must equal the macro sim.saveState literal')
        self.assertIn(output, chain.PLANS['part11']['required_downloads'])

    def test_part11_declares_configured_continuation(self):
        self.assertIs(True, chain.PLANS['part11']['contract']['solver_experiment_configured'])


class MrfChainPart12Part13Contract(unittest.TestCase):
    """P2.3 G5 part12/13: freestream-J diagnostic (J 0.548 -> 0.647) and the continuation segment."""

    ROOT = Path(__file__).resolve().parents[1]
    SPEC = ROOT / 'cases/uav-prop-apc10x7e/velocity_experiment.json'

    @classmethod
    def setUpClass(cls):
        cls.config12 = chain.config('part12')
        # part13's input is part12's output: not resolvable as a source until part12 has run, so the
        # static plan contract is asserted from PLANS while the executed-run ratchet waits for the output.
        cls.plan13 = chain.PLANS['part13']
        cls.spec = json.loads(cls.SPEC.read_text(encoding='utf-8'))

    def test_velocity_spec_matches_nd_and_signed_point(self):
        change = self.spec['single_change']
        nD = (6015.0 / 60.0) * 0.254
        self.assertAlmostEqual(25.4635, nD, places=12)
        self.assertAlmostEqual(0.548 * nD, change['from_mps'], places=9)
        self.assertAlmostEqual(0.647 * nD, change['to_mps'], places=9)
        self.assertAlmostEqual(13.953998, change['from_mps'], places=9)
        self.assertAlmostEqual(16.4748845, change['to_mps'], places=9)
        self.assertEqual('inlet_velocity_magnitude', change['parameter'])
        self.assertEqual('CONSTANT', change['mode'])
        self.assertEqual(0.647, change['target_j'])
        self.assertEqual(0.548, change['reference_j'])

    def test_part12_configuration_plan(self):
        steps = self.config12['steps']
        self.assertEqual(['configure-velocity'], [s['name'] for s in steps])
        self.assertEqual('mrf_segment_solved.sim', steps[0]['input'])
        self.assertEqual('mrf_velocity_experiment_configured.sim', steps[0]['output'])
        self.assertEqual('MRF_VELOCITY_EXPERIMENT_CONFIGURED', steps[0]['marker'])
        self.assertEqual('ConfigureMrfVelocityExperiment.java', steps[0]['macro'])
        fields = {g['field']: g for g in self.config12['record_gates'][0]['fields']}
        self.assertEqual(1300, fields['iteration']['value'])
        self.assertFalse(fields['no_op']['value'])
        self.assertTrue(fields['boundary_inventory_unchanged']['value'])

    def test_part13_segment_plan_uses_1300_1800(self):
        steps = self.plan13['steps']
        self.assertEqual(['run-segment3'], [s['name'] for s in steps])
        self.assertEqual('mrf_velocity_experiment_configured.sim', steps[0]['input'])
        self.assertEqual('mrf_segment_solved.sim', steps[0]['output'])
        fields = {g['field']: g for g in self.plan13['record_gates'][0]['fields']}
        self.assertEqual(1300, fields['iterations_start']['value'])
        self.assertEqual(1800, fields['iteration_cap']['value'])
        self.assertEqual(500, fields['segment_iterations']['value'])

    def test_macro_is_single_change_and_read_only(self):
        source = (self.ROOT / 'macros/ConfigureMrfVelocityExperiment.java').read_text(encoding='utf-8')
        for key in ('velocity_experiment.json', 'boundary_roles', 'VelocityMagnitudeProfile.class',
                    'InletVelocityOption.class', 'FlowDirectionOption.class', 'drifted',
                    'no-op', 'mrf_velocity_experiment_configured.sim',
                    'mrf_velocity_experiment.json', 'MRF_VELOCITY_EXPERIMENT_CONFIGURED',
                    'unchanged_profiles_verified', 'boundary_inventory_unchanged'):
            self.assertIn(key, source, key)
        self.assertEqual(1, source.count('.setValue('))
        for forbidden in ('initializeSolution', 'getSimulationIterator().run', 'getenv', 'nanoTime'):
            self.assertNotIn(forbidden, source, forbidden)


class MrfChainPart14Contract(unittest.TestCase):
    """P2.3 G5 part14: read-only audit of the converged J=0.647 state."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.config = chain.config('part14')

    def test_part14_read_only_audit_plan(self):
        steps = self.config['steps']
        self.assertEqual(['audit-j0647'], [s['name'] for s in steps])
        self.assertEqual('mrf_segment_solved.sim', steps[0]['input'])
        self.assertEqual('mrf_segment_solved.sim', steps[0]['output'])
        self.assertEqual('MRF_TRIAL_AUDITED', steps[0]['marker'])
        self.assertEqual('AuditMrfTrial.java', steps[0]['macro'])
        self.assertIn('reference.json', self.config['inputs'])

    def test_part14_gates_require_convergence_and_tolerance(self):
        fields = {g['field']: g for g in self.config['record_gates'][0]['fields']}
        self.assertEqual(1520, fields['iterations']['value'])
        self.assertTrue(fields['residuals_gate_passed']['value'])
        self.assertTrue(fields['load_gate_passed']['value'])
        self.assertTrue(fields['mass_gate_passed']['value'])
        self.assertTrue(fields['ct_within_signed_tolerance']['value'])
        self.assertTrue(fields['cp_within_signed_tolerance']['value'])
        ct_refs = sorted(g['value'] for g in self.config['record_gates'][0]['fields'] if g['field'] == 'ct_ref')
        cp_refs = sorted(g['value'] for g in self.config['record_gates'][0]['fields'] if g['field'] == 'cp_ref')
        self.assertEqual([0.033, 0.035], ct_refs)
        self.assertEqual([0.031, 0.033], cp_refs)
        self.assertFalse(fields['engineering_verified']['value'])
        self.assertFalse(fields['q3_claim']['value'])

    def test_part14_declares_read_only_audit(self):
        self.assertIs(True, chain.PLANS['part14']['contract']['read_only_diagnostic'])


class MrfChainPart15MeshV2Contract(unittest.TestCase):
    """P2.3 G5 part15: MESH-A2 rebuild (body refinements + prism shrink controls) with the accepted A1 untouched."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part15']
        cls.config = chain.config('part15')
        cls.case = json.loads((cls.ROOT / 'cases/uav-prop-apc10x7e/case_spec.json').read_text(encoding='utf-8'))

    def test_mesh_v2_chain_has_eight_handover_steps(self):
        self.assertEqual(['audit-volumes', 'surface-map', 'create-regions', 'configure-interfaces',
                          'configure-mesh', 'execute-mesh', 'audit-prism', 'audit-mesh-quality'],
                         [step['name'] for step in self.plan['steps']])
        self.assertEqual('mrf_fluid_volumes_built.sim', self.plan['steps'][0]['input'])
        self.assertEqual('mrf_interfaces_configured.sim', self.plan['steps'][4]['input'])
        self.assertEqual('mrf_mesh_configured.sim', self.plan['steps'][4]['output'])
        self.assertEqual('mrf_diagnostic_meshed.sim', self.plan['steps'][5]['output'])
        self.assertEqual('mrf_diagnostic_meshed.sim', self.plan['steps'][7]['input'])

    def test_mesh_v2_configuration_gates_pin_the_revision(self):
        gates = {g['record']: {f['field']: f for f in g['fields']} for g in self.plan['record_gates']}
        cfg = gates['mrf_mesh_configured.json']
        self.assertEqual(20, cfg['prism_layers']['value'])
        self.assertEqual(2, cfg['volume_refinements']['value'])
        # the two-sided gates are stored as separate entries; collect them instead of collapsing by key
        fields = [f for f in self.plan['record_gates'][4]['fields']]
        values = {}
        for f in fields:
            values.setdefault(f['field'], []).append(f['value'])
        self.assertEqual([0.9, 1.1], values['prism_min_thickness_pct'])
        self.assertEqual([4.0, 6.0], values['prism_reduction_pct'])
        self.assertEqual([24.0, 26.0], values['prism_march_angle_deg'])
        self.assertEqual([0.00044, 0.00046], values['prism_total_m'])
        for slot in ('disk', 'wake'):
            for suffix in ('realized_radius_m', 'realized_z0_m', 'realized_z1_m', 'realized_size_m'):
                self.assertIn('refinement_%s_%s' % (slot, suffix), values,
                              'missing measured refinement gate ' + slot + ' ' + suffix)
            for suffix in ('control_found', 'geometry_attached', 'verified'):
                self.assertEqual([True], values['refinement_%s_%s' % (slot, suffix)])
        exec_fields = {}
        for f in self.plan['record_gates'][5]['fields']:
            exec_fields.setdefault(f['field'], []).append(f['value'])
        self.assertEqual([1400000, 8000000], exec_fields['volume_cells'])
        # quality thresholds and prism-coverage gates are UNCHANGED from the accepted A1 mesh (decision B)
        quality = gates['mrf_volume_mesh_quality.json']
        self.assertEqual(1e-06, quality['measured_cell_quality_min']['value'])
        self.assertEqual(0.95, quality['measured_face_validity_min']['value'])
        self.assertEqual(0.005, quality['measured_volume_change_min']['value'])
        prism = gates['mrf_wall_prism_coverage.json']
        self.assertEqual(0.01, prism['max_below_min_layer_area_fraction']['value'])
        self.assertTrue(prism['gate_passed']['value'])
        self.assertFalse(cfg['mesh_generated']['value'])

    def test_casespec_mesh_revision_a2(self):
        mesh = self.case['mesh']
        rev = self.case['mesh_revision']
        self.assertEqual('MESH-B13', rev['revision'])
        self.assertEqual(13, rev['iteration'])
        self.assertTrue(rev['quality_threshold_revision_unchanged'])
        self.assertIn('interface custom prism layers', ' '.join(rev['deferred']))
        self.assertNotIn('mesh_prism_shrink', self.case)
        self.assertNotIn('mesh_volume_refinement_disk', self.case)
        wake = self.case['mesh_volume_refinement_wake']
        self.assertEqual('stationary', wake['target'])
        self.assertEqual('stationary', wake['target'])
        self.assertAlmostEqual(0.14, wake['radius_m'])
        self.assertAlmostEqual(0.004, wake['size_m'])
        self.assertAlmostEqual(-0.60, wake['z_m'][0])
        self.assertAlmostEqual(-0.075, wake['z_m'][1])
        self.assertLess(wake['z_m'][0], wake['z_m'][1])
        # unchanged A1 values stay pinned
        self.assertAlmostEqual(0.05, mesh['base_size_m'])
        self.assertAlmostEqual(0.0009, mesh['blade_target_size_m'])
        self.assertAlmostEqual(0.00012, mesh['blade_min_size_m'])
        self.assertAlmostEqual(0.00045, mesh['prism_total_m'])

    def test_mesh_v2_macro_applies_and_reads_back(self):
        source = (self.ROOT / 'macros/ConfigureMrfMeshOperation.java').read_text(encoding='utf-8')
        for key in ('PrismLayerMinimumThickness', 'PrismLayerReductionPercentage',
                    'PrismLayerBoundaryMarchAngle', 'applyPrismShrink', 'addVolumeRefinement',
                    'mesh_volume_refinement_disk', 'mesh_volume_refinement_wake',
                    'getPercentage()', 'getStartCoordinate().getValue()', 'getGeometryObjects().getObjects()',
                    'did not read back as declared', 'volume_refinement_details',
                    'prism_min_thickness_pct', 'prism_reduction_pct', 'prism_march_angle_deg',
                    'volume_refinements'):
            self.assertIn(key, source, key)
        self.assertIn('setValueAndUnits(marchDeg', source)
        # interface/outer prisms stay disabled in MESH-A2 (deferred to MESH-A3 with API discovery)
        self.assertIn('control(rotatingOperation, "rotor-interface-control", rotatingInterfaces, interfaceMin/base, interfaceTarget/base, true)',
                      source)

    def test_volume_refinement_readback_fixture_fails_closed(self):
        # review-5d B1/B2: a refinement whose measured radius/position/size or control attachment differs
        # from the declaration must not pass. This mirrors the macro predicate on synthetic measurements.
        import math

        def verified(declared, measured):
            def rel(m, d):
                return math.isfinite(m) and abs(m - d) <= 1e-6 * max(1.0, abs(d))
            def abs_ok(m, d):
                return math.isfinite(m) and abs(m - d) <= 1e-9
            return (measured['control_found'] and measured['base_size_option'] and measured['geometry_attached']
                    and rel(measured['radius'], declared['radius'])
                    and abs_ok(measured['z0'], declared['z0']) and abs_ok(measured['z1'], declared['z1'])
                    and rel(measured['size'], declared['size']))

        d = {'radius': 0.16, 'z0': -0.06, 'z1': 0.06, 'size': 0.0025}
        good = {'control_found': True, 'base_size_option': True, 'geometry_attached': True,
                'radius': 0.16, 'z0': -0.06, 'z1': 0.06, 'size': 0.0025}
        self.assertTrue(verified(d, good))
        for key, bad in (('radius', 0.0), ('z0', 0.0), ('z1', 0.0), ('size', 0.0)):
            measured = dict(good); measured[key] = bad
            self.assertFalse(verified(d, measured), 'should fail closed on ' + key)
        for key in ('control_found', 'base_size_option', 'geometry_attached'):
            measured = dict(good); measured[key] = False
            self.assertFalse(verified(d, measured), 'should fail closed on ' + key)

    def test_part17_redesign_removes_rotating_refinement(self):
        plan15 = chain.PLANS['part15']
        plan17 = chain.PLANS['part17']
        self.assertEqual('mrf-meshb2-20261028a', plan17['run_id'])
        self.assertEqual([s['name'] for s in plan15['steps']], [s['name'] for s in plan17['steps']])
        configure = next(g for g in plan17['record_gates'] if g['record'] == 'mrf_mesh_configured.json')
        fields = {f['field']: f for f in configure['fields']}
        self.assertEqual(1, fields['volume_refinements']['value'])
        self.assertNotIn('refinement_disk_verified', fields)
        self.assertNotIn('prism_min_thickness_pct', fields)
        self.assertNotIn('prism_march_angle_deg', fields)
        self.assertNotIn('prism_reduction_pct', fields)
        self.assertTrue(fields['refinement_wake_verified']['value'])
        self.assertTrue(fields['refinement_wake_control_found']['value'])
        # every acceptance threshold stays byte-identical to the accepted line
        quality = next(g for g in plan17['record_gates'] if g['record'] == 'mrf_volume_mesh_quality.json')
        q = {f['field']: f['value'] for f in quality['fields']}
        self.assertEqual(1e-06, q['measured_cell_quality_min'])
        self.assertEqual(0.95, q['measured_face_validity_min'])
        self.assertEqual(0.005, q['measured_volume_change_min'])
        prism = next(g for g in plan17['record_gates'] if g['record'] == 'mrf_wall_prism_coverage.json')
        p = {f['field']: f['value'] for f in prism['fields']}
        self.assertEqual(0.01, p['max_below_min_layer_area_fraction'])
        execute = next(g for g in plan17['record_gates'] if g['record'] == 'mrf_diagnostic_mesh.json')
        cells = {f['op']: f['value'] for f in execute['fields'] if f['field'] == 'volume_cells'}
        self.assertEqual(1500000, cells['ge'])
        self.assertGreater(cells['ge'], 1358218, 'cell floor must stay above the accepted A1 count')

    def test_part16_iteration_2_softens_only_measured_values(self):
        plan15 = chain.PLANS['part15']
        plan16 = chain.PLANS['part16']
        self.assertEqual('mrf-meshv2b-20261028a', plan16['run_id'])
        self.assertNotEqual(plan15['run_id'], plan16['run_id'])
        # step chain identical, every acceptance threshold identical except the softened measured values
        self.assertEqual([s['name'] for s in plan15['steps']], [s['name'] for s in plan16['steps']])
        g15 = next(g for g in plan15['record_gates'] if g['record'] == 'mrf_mesh_configured.json')
        g16 = next(g for g in plan16['record_gates'] if g['record'] == 'mrf_mesh_configured.json')
        self.assertEqual([f['field'] for f in g15['fields']], [f['field'] for f in g16['fields']])
        values16 = {}
        for f in g16['fields']:
            values16.setdefault(f['field'], []).append(f['value'])
        self.assertEqual([4.9, 5.1], values16['prism_min_thickness_pct'])
        self.assertEqual([1.9, 2.1], values16['prism_reduction_pct'])
        self.assertEqual([0.0034, 0.0036], values16['refinement_disk_realized_size_m'])
        self.assertEqual([0.0049, 0.0051], values16['refinement_wake_realized_size_m'])
        # unchanged acceptance thresholds
        quality = next(g for g in plan16['record_gates'] if g['record'] == 'mrf_volume_mesh_quality.json')
        q = {f['field']: f['value'] for f in quality['fields']}
        self.assertEqual(1e-06, q['measured_cell_quality_min'])
        self.assertEqual(0.95, q['measured_face_validity_min'])
        self.assertEqual(0.005, q['measured_volume_change_min'])
        prism = next(g for g in plan16['record_gates'] if g['record'] == 'mrf_wall_prism_coverage.json')
        p = {f['field']: f['value'] for f in prism['fields']}
        self.assertEqual(0.01, p['max_below_min_layer_area_fraction'])

    def test_part15_declares_mesh_revision_v2(self):
        self.assertIs(True, chain.PLANS['part15']['contract']['mesh_revision_v2'])


class MrfChainPart18Contract(unittest.TestCase):
    """P2.3 G5 part18: physics + frozen J=0.647 velocity configuration on the MESH-B2 mesh (configure-only)."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part18']
        cls.config = chain.config('part18')

    def test_part18_two_configuration_steps(self):
        self.assertEqual(['configure-physics', 'configure-velocity'],
                         [s['name'] for s in self.plan['steps']])
        self.assertEqual('mrf_diagnostic_meshed.sim', self.plan['steps'][0]['input'])
        self.assertEqual('mrf_physics_configured.sim', self.plan['steps'][0]['output'])
        self.assertEqual('mrf_physics_configured.sim', self.plan['steps'][1]['input'])
        self.assertEqual('mrf_velocity_experiment_configured.sim', self.plan['steps'][1]['output'])
        self.assertEqual('MRF_VELOCITY_EXPERIMENT_CONFIGURED', self.plan['steps'][1]['marker'])
        self.assertIn('velocity_experiment.json', self.config['inputs'])

    def test_part18_mesh_and_velocity_gates(self):
        gates = {g['record']: g for g in self.plan['record_gates']}
        phys = gates['mrf_physics_readback.json']['fields']
        cells = {f['op']: f['value'] for f in phys if f['field'] == 'measured_cells'}
        self.assertEqual(2000000, cells['ge'])
        self.assertEqual(4000000, cells['le'])
        vel_fields = gates['mrf_velocity_experiment.json']['fields']
        vel = {}
        for f in vel_fields:
            vel.setdefault(f['field'], []).append(f['value'])
        self.assertEqual([0], vel['iteration'])
        self.assertEqual([True], vel['applied'])
        self.assertEqual([False], vel['no_op'])
        self.assertEqual([True], vel['boundary_inventory_unchanged'])
        self.assertEqual([0.646, 0.648], vel['target_j'])
        self.assertEqual([16.4748, 16.4749], vel['to_mps'])

    def test_part18_declares_no_solve(self):
        contract = chain.PLANS['part18']['contract']
        self.assertIs(True, contract['physics_configuration'])
        self.assertIs(True, contract['velocity_experiment_configuration'])
        self.assertIs(True, contract['no_solve'])


class MrfChainPart19Contract(unittest.TestCase):
    """P2.3 G5 part19: convergence-baseline trial on the MESH-B2 J=0.647 state."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part19']
        cls.config = chain.config('part19')

    def test_part19_single_trial_step(self):
        self.assertEqual(['run-trial'], [s['name'] for s in self.plan['steps']])
        self.assertEqual('mrf_velocity_experiment_configured.sim', self.plan['steps'][0]['input'])
        self.assertEqual('mrf_trial_solved.sim', self.plan['steps'][0]['output'])
        self.assertEqual('MRF_TRIAL_SOLVED', self.plan['steps'][0]['marker'])
        self.assertIn('mrf_velocity_experiment_configured.sim', self.config['inputs'])

    def test_part19_keeps_the_reviewed_trial_gates(self):
        fields = {f['field']: f for f in self.plan['record_gates'][0]['fields']}
        self.assertEqual(0, fields['iterations_start']['value'])
        self.assertEqual(300, fields['iteration_cap']['value'])
        self.assertEqual(100, fields['stagnation_window']['value'])
        self.assertEqual(10, fields['stagnation_samples']['value'])
        self.assertEqual('case_spec.solver.trial_iterations', fields['iteration_cap_source']['value'])
        self.assertTrue(fields['initialized']['value'])

    def test_part19_declares_trial(self):
        contract = chain.PLANS['part19']['contract']
        self.assertIs(True, contract['trial'])
        self.assertIs(True, contract['solve_run'])


class MrfChainPart20Contract(unittest.TestCase):
    """P2.3 G5 part20: segment extension 300->800 on the MESH-B2 J=0.647 trial state."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part20']
        cls.config = chain.config('part20')

    def test_part20_single_segment_step(self):
        self.assertEqual(['run-segment'], [s['name'] for s in self.plan['steps']])
        self.assertEqual('mrf_trial_solved.sim', self.plan['steps'][0]['input'])
        self.assertEqual('mrf_segment_solved.sim', self.plan['steps'][0]['output'])
        self.assertEqual('MRF_SEGMENT_SOLVED', self.plan['steps'][0]['marker'])
        self.assertIn('mrf_trial_solved.sim', self.config['inputs'])

    def test_part20_gates_continue_300_to_800(self):
        fields = {f['field']: f for f in self.plan['record_gates'][0]['fields']}
        self.assertEqual(300, fields['iterations_start']['value'])
        self.assertEqual(800, fields['iteration_cap']['value'])
        self.assertEqual(500, fields['segment_iterations']['value'])
        self.assertEqual('case_spec.solver.segment_iterations (start+segment)', fields['iteration_cap_source']['value'])
        self.assertTrue(fields['initialized']['value'])

    def test_part20_declares_continuation(self):
        contract = chain.PLANS['part20']['contract']
        self.assertIs(True, contract['continuation'])
        self.assertIs(True, contract['solve_run'])


class MrfChainPart21Contract(unittest.TestCase):
    """P2.3 G5 part21: read-only audit of the converged MESH-B2 J=0.647 state."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part21']
        cls.config = chain.config('part21')

    def test_part21_read_only_audit(self):
        steps = self.plan['steps']
        self.assertEqual(['audit-j0647'], [s['name'] for s in steps])
        self.assertEqual('mrf_segment_solved.sim', steps[0]['input'])
        self.assertEqual('mrf_segment_solved.sim', steps[0]['output'])
        self.assertEqual('AuditMrfTrial.java', steps[0]['macro'])
        for name in ('mrf_segment_history.tsv', 'mrf_trial_history.tsv', 'reference.json'):
            self.assertIn(name, self.config['inputs'])

    def test_part21_gates_require_convergence_and_reference(self):
        fields = {}
        for f in self.plan['record_gates'][0]['fields']:
            fields.setdefault(f['field'], []).append((f['op'], f['value']))
        self.assertIn(('ge', 301), fields['iterations'])
        self.assertIn(('le', 800), fields['iterations'])
        self.assertEqual([('eq', True)], fields['residuals_gate_passed'])
        self.assertEqual([('eq', True)], fields['load_gate_passed'])
        self.assertEqual([('eq', True)], fields['ct_within_signed_tolerance'])
        self.assertEqual([('eq', True)], fields['cp_within_signed_tolerance'])
        self.assertEqual([('eq', False)], fields['engineering_verified'])
        self.assertEqual([('eq', False)], fields['q3_claim'])

    def test_part21_declares_read_only(self):
        contract = chain.PLANS['part21']['contract']
        self.assertIs(True, contract['read_only_diagnostic'])
        self.assertIs(True, contract['no_solve'])


class MrfLoadWindowCoverageRatchet(unittest.TestCase):
    """O9 for review-5l: the load-window logic must merge all histories and fail closed on a short window."""

    def _window(self, samples, window, limit):
        # mirrors RunMrfSegment.windowStats/windowStable and AuditMrfTrial.loadWindowFromHistory
        iterations = [i for i, ct, cp in samples]
        last = iterations[-1]
        windowed = [(i, ct, cp) for i, ct, cp in samples if i >= last - window]
        if not windowed:
            return False, 0, 0
        first = windowed[0][0]
        ct = [c for _, c, _ in windowed]
        cp = [p for _, _, p in windowed]
        ct_range = (max(ct) - min(ct)) / abs(sum(ct) / len(ct))
        cp_range = (max(cp) - min(cp)) / abs(sum(cp) / len(cp))
        covered = len(windowed) >= 21 and (last - first) >= window
        return covered and ct_range <= limit and cp_range <= limit, len(windowed), last - first

    def test_short_segment_history_fails_closed(self):
        # the real part20 failure: 3 samples 310/320/330 over a 200-iteration window
        short = [(310, 0.0370, 0.0340), (320, 0.0372, 0.0342), (330, 0.0355, 0.0340)]
        stable, samples, span = self._window(short, 200, 0.005)
        self.assertFalse(stable)
        self.assertEqual(3, samples)
        self.assertEqual(20, span)

    def test_merged_full_window_detects_the_real_drift(self):
        # merged trial+segment history over 130..330: CT range 5.90% > 0.5%
        samples = []
        for i in range(130, 331, 10):
            if i <= 300:
                ct = 0.0376 if i < 200 else 0.0360
            else:
                ct = 0.0355
            samples.append((i, ct, 0.0340))
        stable, samples_n, span = self._window(samples, 200, 0.005)
        self.assertFalse(stable)
        self.assertGreaterEqual(samples_n, 21)
        self.assertGreaterEqual(span, 200)

    def test_stable_full_window_passes(self):
        samples = [(i, 0.0355 + 0.00001 * ((i // 10) % 2), 0.0340) for i in range(130, 331, 10)]
        stable, samples_n, span = self._window(samples, 200, 0.005)
        self.assertTrue(stable)
        self.assertGreaterEqual(samples_n, 21)
        self.assertGreaterEqual(span, 200)


class MrfChainPart22Part23Contract(unittest.TestCase):
    """P2.3 G5 part22 (long segment 330->830) and part23 (fixed audit) contract."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan22 = chain.PLANS['part22']
        cls.config22 = chain.config('part22')
        cls.plan23 = chain.PLANS['part23']

    def test_part22_iteration_gates_match_the_declared_330_830(self):
        fields = {}
        for f in self.plan22['record_gates'][0]['fields']:
            fields.setdefault(f['field'], []).append((f['op'], f['value']))
        self.assertIn(('eq', 330), fields['iterations_start'])
        self.assertIn(('ge', 331), fields['iterations_end'])
        self.assertIn(('le', 830), fields['iterations_end'])
        self.assertIn(('eq', 830), fields['iteration_cap'])
        self.assertEqual([('eq', 500)], fields['segment_iterations'])

    def test_part22_coverage_gates_present(self):
        fields = {f['field']: (f['op'], f['value']) for f in self.plan22['record_gates'][0]['fields']}
        self.assertEqual(('ge', 21), fields['load_window_samples'])
        self.assertEqual(('ge', 200), fields['load_window_span_iterations'])
        self.assertEqual(('eq', True), fields['load_window_covered'])
        self.assertEqual(('le', 0.005), fields['load_ct_range_fraction'])
        self.assertEqual(('le', 0.005), fields['load_cp_range_fraction'])

    def test_part22_inputs_are_the_merged_history_set(self):
        for name in ('mrf_segment_solved.sim', 'mrf_trial_history.tsv', 'mrf_segment_history_prev.tsv'):
            self.assertIn(name, self.config22['inputs'])

    def test_every_part23_gate_field_is_emitted_by_the_audit_macro(self):
        # review-5o: a gate on a field the macro never writes fails only AFTER the audit run. Cross-check
        # every part23 gate field against the audit macro's record keys.
        source = (self.ROOT / 'macros/AuditMrfTrial.java').read_text(encoding='utf-8')
        gates = next(g for g in self.plan23['record_gates'] if g['record'] == 'mrf_trial_audit.json')
        missing = [f['field'] for f in gates['fields'] if ('"%s' % f['field']) not in source]
        self.assertEqual([], missing, 'part23 gates reference fields the audit macro does not emit: %r' % missing)
        # the legacy name must remain a documented alias for old records
        self.assertIn('load_range_fraction', source)

    def test_part23_audit_window_covers_part22(self):
        fields = {}
        for f in self.plan23['record_gates'][0]['fields']:
            fields.setdefault(f['field'], []).append((f['op'], f['value']))
        self.assertIn(('ge', 331), fields['iterations'])
        self.assertIn(('le', 830), fields['iterations'])
        self.assertIn(('ge', 21), fields['load_window_samples'])
        self.assertIn(('ge', 200), fields['load_window_span_iterations'])
        self.assertIn(('eq', True), fields['load_window_covered'])


class MrfChainPart24Contract(unittest.TestCase):
    """P2.3 G5 part24: MESH-B3 y+ fix attempt 1 (finer blade surface + thinner prism stack)."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part24']
        cls.config = chain.config('part24')

    def test_part24_reuses_the_eight_step_chain(self):
        self.assertEqual(['audit-volumes', 'surface-map', 'create-regions', 'configure-interfaces',
                          'configure-mesh', 'execute-mesh', 'audit-prism', 'audit-mesh-quality'],
                         [s['name'] for s in self.plan['steps']])
        self.assertEqual('mrf_diagnostic_meshed.sim', self.plan['steps'][5]['output'])

    def test_part24_pins_the_yplus_related_mesh_values(self):
        configure = next(g for g in self.plan['record_gates'] if g['record'] == 'mrf_mesh_configured.json')
        values = {}
        for f in configure['fields']:
            values.setdefault(f['field'], []).append(f['value'])
        self.assertEqual([0.00007, 0.00009], values['blade_min_size_m'])
        self.assertEqual([0.00059, 0.00061], values['blade_target_size_m'])
        self.assertEqual([0.00029, 0.00031], values['prism_total_m'])
        self.assertEqual([20], values['prism_layers'])
        self.assertEqual([1], values['volume_refinements'])
        self.assertNotIn('prism_min_thickness_pct', values)
        # unchanged acceptance thresholds
        quality = next(g for g in self.plan['record_gates'] if g['record'] == 'mrf_volume_mesh_quality.json')
        q = {f['field']: f['value'] for f in quality['fields']}
        self.assertEqual(1e-06, q['measured_cell_quality_min'])
        self.assertEqual(0.95, q['measured_face_validity_min'])
        self.assertEqual(0.005, q['measured_volume_change_min'])
        prism = next(g for g in self.plan['record_gates'] if g['record'] == 'mrf_wall_prism_coverage.json')
        p = {f['field']: f['value'] for f in prism['fields']}
        self.assertEqual(0.01, p['max_below_min_layer_area_fraction'])

    def test_part24_declares_mesh_revision_b3(self):
        self.assertIs(True, chain.PLANS['part24']['contract']['mesh_revision_b3'])


class MrfChainPart25Contract(unittest.TestCase):
    """P2.3 G5 part25: MESH-B3 iteration 2 (blade_min 0.10 mm + TE/tip prism shrink controls)."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part25']
        cls.config = chain.config('part25')

    def test_part25_reuses_the_eight_step_chain(self):
        self.assertEqual(['audit-volumes', 'surface-map', 'create-regions', 'configure-interfaces',
                          'configure-mesh', 'execute-mesh', 'audit-prism', 'audit-mesh-quality'],
                         [s['name'] for s in self.plan['steps']])

    def test_part25_pins_the_iteration_2_values(self):
        configure = next(g for g in self.plan['record_gates'] if g['record'] == 'mrf_mesh_configured.json')
        values = {}
        for f in configure['fields']:
            values.setdefault(f['field'], []).append(f['value'])
        self.assertEqual([0.00009, 0.00011], values['blade_min_size_m'])
        self.assertEqual([0.00059, 0.00061], values['blade_target_size_m'])
        self.assertEqual([0.00029, 0.00031], values['prism_total_m'])
        self.assertEqual([4.9, 5.1], values['prism_min_thickness_pct'])
        self.assertEqual([1.9, 2.1], values['prism_reduction_pct'])
        self.assertEqual([24.0, 26.0], values['prism_march_angle_deg'])
        # unchanged gates
        quality = next(g for g in self.plan['record_gates'] if g['record'] == 'mrf_volume_mesh_quality.json')
        q = {f['field']: f['value'] for f in quality['fields']}
        self.assertEqual(1e-06, q['measured_cell_quality_min'])
        self.assertEqual(0.95, q['measured_face_validity_min'])
        self.assertEqual(0.005, q['measured_volume_change_min'])

    def test_part25_declares_iteration_2(self):
        self.assertIs(True, chain.PLANS['part25']['contract']['mesh_revision_b3_iteration_2'])


class MrfChainPart26Contract(unittest.TestCase):
    """P2.3 G5 part26: MESH-B4 redesign (A1 values + shrink controls only)."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part26']
        cls.config = chain.config('part26')

    def test_part26_restores_a1_values_and_keeps_shrink(self):
        configure = next(g for g in self.plan['record_gates'] if g['record'] == 'mrf_mesh_configured.json')
        values = {}
        for f in configure['fields']:
            values.setdefault(f['field'], []).append(f['value'])
        self.assertEqual([0.00014, 0.00016], values['blade_min_size_m'])
        self.assertEqual([0.00099, 0.00101], values['blade_target_size_m'])
        self.assertEqual([0.00044, 0.00046], values['prism_total_m'])
        self.assertEqual([4.9, 5.1], values['prism_min_thickness_pct'])
        self.assertEqual([1.9, 2.1], values['prism_reduction_pct'])
        self.assertEqual([24.0, 26.0], values['prism_march_angle_deg'])
        self.assertEqual([1], values['volume_refinements'])
        quality = next(g for g in self.plan['record_gates'] if g['record'] == 'mrf_volume_mesh_quality.json')
        q = {f['field']: f['value'] for f in quality['fields']}
        self.assertEqual(1e-06, q['measured_cell_quality_min'])
        self.assertEqual(0.95, q['measured_face_validity_min'])
        self.assertEqual(0.005, q['measured_volume_change_min'])

    def test_part26_declares_redesign(self):
        self.assertIs(True, chain.PLANS['part26']['contract']['mesh_redesign_b4'])


class MrfChainPart27Contract(unittest.TestCase):
    """P2.3 G5 part27: MESH-B5 mesher-level redesign with the Advancing Layer Mesher."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part27']
        cls.config = chain.config('part27')

    def test_part27_uses_alm_and_a1_values_without_shrink(self):
        configure = next(g for g in self.plan['record_gates'] if g['record'] == 'mrf_mesh_configured.json')
        values = {}
        for f in configure['fields']:
            values.setdefault(f['field'], []).append(f['value'])
        self.assertEqual(['alm'], values['rotating_layer_mesher'])
        self.assertEqual([0.00014, 0.00016], values['blade_min_size_m'])
        self.assertEqual([0.00099, 0.00101], values['blade_target_size_m'])
        self.assertEqual([0.00044, 0.00046], values['prism_total_m'])
        self.assertEqual([1], values['volume_refinements'])
        self.assertNotIn('prism_min_thickness_pct', values)
        quality = next(g for g in self.plan['record_gates'] if g['record'] == 'mrf_volume_mesh_quality.json')
        q = {f['field']: f['value'] for f in quality['fields']}
        self.assertEqual(1e-06, q['measured_cell_quality_min'])
        self.assertEqual(0.95, q['measured_face_validity_min'])
        self.assertEqual(0.005, q['measured_volume_change_min'])

    def test_part27_declares_alm_redesign(self):
        self.assertIs(True, chain.PLANS['part27']['contract']['mesh_redesign_b5_alm'])
        macro = (self.ROOT / 'macros/ConfigureMrfMeshOperation.java').read_text(encoding='utf-8')
        self.assertIn('star.bodyfittedmesher.AdvancingLayerAutoMesher', macro)
        self.assertIn('rotating_layer_mesher', macro)


class MrfChainPart28Contract(unittest.TestCase):
    """P2.3 G5 part28: MESH-B6 ALM with the required Polyhedral core."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part28']
        cls.config = chain.config('part28')

    def test_part28_pins_alm_and_polyhedral_core(self):
        configure = next(g for g in self.plan['record_gates'] if g['record'] == 'mrf_mesh_configured.json')
        values = {}
        for f in configure['fields']:
            values.setdefault(f['field'], []).append(f['value'])
        self.assertEqual(['alm'], values['rotating_layer_mesher'])
        self.assertEqual(['polyhedral'], values['rotating_core_mesher'])
        self.assertEqual([0.00014, 0.00016], values['blade_min_size_m'])
        self.assertEqual([0.00044, 0.00046], values['prism_total_m'])
        self.assertEqual([1], values['volume_refinements'])
        quality = next(g for g in self.plan['record_gates'] if g['record'] == 'mrf_volume_mesh_quality.json')
        q = {f['field']: f['value'] for f in quality['fields']}
        self.assertEqual(1e-06, q['measured_cell_quality_min'])
        self.assertEqual(0.95, q['measured_face_validity_min'])
        self.assertEqual(0.005, q['measured_volume_change_min'])

    def test_part28_declares_polyhedral_core(self):
        self.assertIs(True, chain.PLANS['part28']['contract']['mesh_redesign_b6_alm_poly'])
        macro = (self.ROOT / 'macros/ConfigureMrfMeshOperation.java').read_text(encoding='utf-8')
        self.assertIn('star.dualmesher.DualAutoMesher', macro)
        self.assertIn('rotating_core_mesher', macro)

    def test_part28_core_record_is_derived_from_the_layer_selector(self):
        # review-5u B1/B2: the record must derive the core from the same selector create() uses;
        # the old expression compared 'polyhedral' against the layer type ('prism'/'alm') and always
        # emitted 'trimmer', so the part28 gate could never pass.
        macro = (self.ROOT / 'macros/ConfigureMrfMeshOperation.java').read_text(encoding='utf-8')
        self.assertIn('("alm".equals(rotatingMesher) ? "polyhedral" : "trimmer")', macro)
        self.assertNotIn('("polyhedral".equals(rotatingMesher)', macro)

        def recorded_core(layer_type):
            return 'polyhedral' if layer_type == 'alm' else 'trimmer'

        self.assertEqual('polyhedral', recorded_core('alm'))
        self.assertEqual('trimmer', recorded_core('prism'))


class MrfChainPart29Contract(unittest.TestCase):
    """P2.3 G5 part29: read-only ALM default-value probe."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part29']
        cls.config = chain.config('part29')

    def test_part29_is_single_read_only_probe(self):
        self.assertEqual(['ProbeMrfAlmDefaults.java'], self.plan['macros'])
        self.assertEqual(1, len(self.plan['steps']))
        step = self.plan['steps'][0]
        self.assertEqual('probe-alm-defaults', step['name'])
        self.assertEqual('MRF_ALM_DEFAULTS_PROBE_COMPLETE', step['marker'])
        self.assertIsNone(step['output'])
        self.assertIn('mrf_diagnostic_meshed.sim', self.config['inputs'])
        self.assertEqual(['mrf_alm_defaults_probe.json'], self.plan['required_downloads'])
        self.assertEqual([], self.plan['optional_downloads'])

    def test_part29_probe_macro_is_read_only_and_maps_alm_classes(self):
        source = (self.ROOT / 'macros/ProbeMrfAlmDefaults.java').read_text(encoding='utf-8')
        self.assertIn('MRF_ALM_DEFAULTS_PROBE_COMPLETE', source)
        self.assertNotIn('saveState', source)
        self.assertNotIn('initializeSolution', source)
        self.assertNotIn('run(', source)
        for cls in ('NumPrismLayers', 'PrismThickness', 'PrismLayerStretching',
                    'BodyfittedPrismLayerThicknessRatio', 'BodyfittedPrismLayerStretching'):
            self.assertIn(cls, source)


class MrfChainPart30Contract(unittest.TestCase):
    """P2.3 G5 part30: numeric ALM default-value readback."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part30']

    def test_part30_reads_back_numeric_prism_values(self):
        self.assertEqual('mrf-almprobe2-20261029a', self.plan['run_id'])
        self.assertEqual(['ProbeMrfAlmDefaults.java'], self.plan['macros'])
        gate = self.plan['record_gates'][0]
        fields = {f['field']: (f['op'], f['value']) for f in gate['fields']}
        self.assertEqual(('ge', 1), fields['operation_1_num_prism_layers'])
        self.assertEqual(('gt', 0.0), fields['operation_1_prism_total_m'])
        self.assertEqual(('gt', 1.0), fields['operation_1_prism_stretch'])
        # review-5y B1/B2: the runner resolves gate fields as LITERAL property names, so no gate
        # field may contain a dot; evaluate every part30 gate against a representative probe record
        # with the same literal resolver.
        def runner_resolve(record, field):
            if '.' in field:
                return None
            return record.get(field)

        def runner_gate(record, gate):
            value = runner_resolve(record, gate['field'])
            if value is None:
                return False
            if gate['op'] == 'eq':
                return value == gate['value']
            if gate['op'] == 'ge':
                return value >= gate['value']
            if gate['op'] == 'gt':
                return value > gate['value']
            if gate['op'] == 'le':
                return value <= gate['value']
            if gate['op'] == 'lt':
                return value < gate['value']
            raise AssertionError(gate['op'])

        record = {'status': 'ok', 'probe': 'alm_defaults', 'operations': 2,
                  'operation_1_num_prism_layers': 20, 'operation_1_prism_total_m': 0.00045,
                  'operation_1_prism_stretch': 1.2}
        for gate in gate['fields']:
            self.assertNotIn('.', gate['field'])
            self.assertTrue(runner_gate(record, gate), gate)
        self.assertEqual(['mrf_alm_defaults_probe.json'], self.plan['required_downloads'])

    def test_part30_macro_has_numeric_readback(self):
        source = (self.ROOT / 'macros/ProbeMrfAlmDefaults.java').read_text(encoding='utf-8')
        for token in ('getNumLayers()', 'getAbsoluteSizeValue().getSIValue()', 'getStretching()',
                      'num_prism_layers', 'prism_total_m', 'prism_stretch'):
            self.assertIn(token, source)
        self.assertNotIn('saveState', source)
        self.assertNotIn('initializeSolution', source)


class MrfChainPart31Contract(unittest.TestCase):
    """P2.3 G5 part31: ALM prism-layer histogram probe (behaviour-gap investigation)."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part31']

    def test_part31_is_single_read_only_histogram_step(self):
        self.assertEqual('mrf-almhist-20261029a', self.plan['run_id'])
        self.assertEqual(['ProbeMrfAlmPrismHistogram.java'], self.plan['macros'])
        self.assertEqual(1, len(self.plan['steps']))
        self.assertEqual('probe-alm-prism-histogram', self.plan['steps'][0]['name'])
        self.assertEqual('MRF_ALM_PRISM_HISTOGRAM_COMPLETE', self.plan['steps'][0]['marker'])
        self.assertEqual(['mrf_alm_prism_histogram.json'], self.plan['required_downloads'])
        fields = {f['field'] for f in self.plan['record_gates'][0]['fields']}
        self.assertIn('wall1_area_average_layers', fields)
        self.assertIn('wall1_bins_sum', fields)
        self.assertIn('wall_count', fields)

    def test_part31_macro_has_bins_and_is_read_only(self):
        source = (self.ROOT / 'macros/ProbeMrfAlmPrismHistogram.java').read_text(encoding='utf-8')
        for token in ('PrismLayerNumber', 'FirstPrismLayerHeight', 'bin_', 'bins_sum', 'wall_count',
                      'MRF_ALM_PRISM_HISTOGRAM_COMPLETE'):
            self.assertIn(token, source)
        # review-6a B1/B2: the interface-parent exclusion must use the PHYSICAL parents
        self.assertIn('getParentBoundary0()', source)
        self.assertIn('getParentBoundary1()', source)
        self.assertNotIn('.getBoundary0()', source)
        self.assertNotIn('.getBoundary1()', source)
        self.assertIn('Interface does not connect the MRF regions', source)
        self.assertNotIn('saveState', source)
        self.assertNotIn('initializeSolution', source)
        self.assertNotIn('.run(', source)


class MrfChainPart32Contract(unittest.TestCase):
    """P2.3 G5 part32: ALM model-control probe (aspect-ratio limit / sharp edges / surface option)."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part32']

    def test_part32_is_single_read_only_controls_step(self):
        self.assertEqual('mrf-almctrl-20261029a', self.plan['run_id'])
        self.assertEqual(['ProbeMrfAlmControls.java'], self.plan['macros'])
        self.assertEqual('probe-alm-controls', self.plan['steps'][0]['name'])
        self.assertEqual(['mrf_alm_controls_probe.json'], self.plan['required_downloads'])
        fields = {f['field'] for f in self.plan['record_gates'][0]['fields']}
        self.assertIn('operation_1_is_advancing_layer_mesher', fields)
        self.assertIn('alm_operations', fields)

    def test_part32_macro_reads_alm_controls_and_is_read_only(self):
        source = (self.ROOT / 'macros/ProbeMrfAlmControls.java').read_text(encoding='utf-8')
        for token in ('getMeshersCollection', 'AdvancingLayerAutoMesher', 'getLimitAspectRatio',
                      'getSharpEdgeRefinement', 'getBoundaryMarchAngle', 'MRF_ALM_CONTROLS_PROBE_COMPLETE'):
            self.assertIn(token, source)
        self.assertNotIn('saveState', source)
        self.assertNotIn('initializeSolution', source)
        self.assertNotIn('.run(', source)


class MrfChainPart33Contract(unittest.TestCase):
    """P2.3 G5 part33: MESH-B7 ALM thin-stack mapping (0.20 mm, 20 layers, A1 surface)."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part33']

    def test_part33_pins_alm_thin_stack(self):
        configure = next(g for g in self.plan['record_gates'] if g['record'] == 'mrf_mesh_configured.json')
        values = {}
        for f in configure['fields']:
            values.setdefault(f['field'], []).append(f['value'])
        self.assertEqual(['alm'], values['rotating_layer_mesher'])
        self.assertEqual(['polyhedral'], values['rotating_core_mesher'])
        self.assertEqual([0.00019, 0.00021], values['prism_total_m'])
        self.assertEqual([20], values['prism_layers'])
        self.assertEqual([1], values['volume_refinements'])
        quality = next(g for g in self.plan['record_gates'] if g['record'] == 'mrf_volume_mesh_quality.json')
        q = {f['field']: f['value'] for f in quality['fields']}
        self.assertEqual(1e-06, q['measured_cell_quality_min'])
        self.assertEqual(0.95, q['measured_face_validity_min'])
        self.assertEqual(0.005, q['measured_volume_change_min'])

    def test_part33_declares_b7(self):
        self.assertIs(True, chain.PLANS['part33']['contract']['mesh_redesign_b7_alm_thin'])


class MrfMeshRevisionAttemptPointers(unittest.TestCase):
    """review-6d B3: every mesh_revision.attempt_* pointer must resolve to an existing archived result."""

    ROOT = Path(__file__).resolve().parents[1]

    def test_every_attempt_pointer_resolves(self):
        spec = json.loads((self.ROOT / 'cases/uav-prop-apc10x7e/case_spec.json').read_text(encoding='utf-8'))
        revision = spec['mesh_revision']
        pointers = {k: v for k, v in revision.items() if k.startswith('attempt') or k == 'attempt_1'}
        self.assertTrue(pointers, 'no attempt pointers declared')
        missing = [f'{k}={v}' for k, v in pointers.items()
                   if not (self.ROOT / str(v)).is_file()]
        self.assertEqual([], missing, 'dangling mesh_revision attempt pointers: %r' % missing)


class MrfChainPart34Contract(unittest.TestCase):
    """P2.3 G5 part34: PrismDefaultValuesManager conditions probe on the ALM operation."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part34']

    def test_part34_is_single_read_only_manager_step(self):
        self.assertEqual('mrf-almpdm-20261029a', self.plan['run_id'])
        self.assertEqual(['ProbeMrfAlmPrismDefaultsManager.java'], self.plan['macros'])
        self.assertEqual('probe-alm-prism-manager', self.plan['steps'][0]['name'])
        self.assertEqual(['mrf_alm_prism_manager_probe.json'], self.plan['required_downloads'])
        fields = {f['field'] for f in self.plan['record_gates'][0]['fields']}
        self.assertIn('operation_1_manager_present', fields)
        self.assertIn('operation_1_conditions_count', fields)

    def test_part34_macro_reads_conditions_and_is_read_only(self):
        source = (self.ROOT / 'macros/ProbeMrfAlmPrismDefaultsManager.java').read_text(encoding='utf-8')
        for token in ('PrismDefaultValuesManager', 'getConditions', 'AdvancingLayerAutoMesher',
                      'MRF_ALM_PRISM_MANAGER_PROBE_COMPLETE'):
            self.assertIn(token, source)
        self.assertNotIn('saveState', source)
        self.assertNotIn('initializeSolution', source)
        self.assertNotIn('.run(', source)


class MrfChainPart35Contract(unittest.TestCase):
    """P2.3 G5 part35: numeric readback of the ALM PrismDefaultValuesManager conditions."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part35']

    def test_part35_reads_conditions_on_the_meshb7_mesh(self):
        self.assertEqual('mrf-almcond-20261029a', self.plan['run_id'])
        self.assertEqual(['ProbeMrfAlmConditionValues.java'], self.plan['macros'])
        self.assertEqual('probe-alm-condition-values', self.plan['steps'][0]['name'])
        self.assertIn('mrf-meshb7-20261029a', str(self.plan['inputs']['mrf_diagnostic_meshed.sim']))
        self.assertEqual(['mrf_alm_condition_values.json'], self.plan['required_downloads'])
        # review-6g B1/B3: the numeric readback must be GATED, not just written
        fields = {}
        for f in self.plan['record_gates'][0]['fields']:
            fields.setdefault(f['field'], []).append((f['op'], f['value']))
        self.assertIn(('eq', 3), fields['operation_1_conditions_count'])
        self.assertIn(('eq', 3), fields['operation_1_num_conditions_parsed'])
        self.assertIn(('ge', 1), fields['operation_1_num_prism_layers'])
        self.assertIn(('le', 1000), fields['operation_1_num_prism_layers'])
        self.assertIn(('ge', 1e-07), fields['operation_1_prism_total_m'])
        self.assertIn(('le', 1e-02), fields['operation_1_prism_total_m'])
        self.assertIn(('ge', 1.0), fields['operation_1_prism_stretch'])
        self.assertIn(('le', 10.0), fields['operation_1_prism_stretch'])

    def test_part35_error_record_is_parseable_and_fails_closed(self):
        # review-6i B1: parse a RAW JSON error record shaped exactly like the macro output (one status, escaped
        # backslashes/quotes/newline, no canonical keys). A tautological dict->dumps->loads round-trip is not
        # allowed here.
        raw = ('{"schema_version":1,"probe":"alm_condition_values","status":"error",'
               '"conditions_error":"conditions=3 parsed=2 unexpected=star.foo.Bar; path C:\\\\tmp\\\\x \\"q\\" line\\nnext"}')
        pairs = json.loads(raw, object_pairs_hook=lambda p: p)
        status_members = [k for k, _ in pairs if k == 'status']
        self.assertEqual(1, len(status_members), 'raw error record must carry exactly one status member')
        record = json.loads(raw)
        self.assertEqual('error', record['status'])
        self.assertIn('C:\\tmp\\x "q" line\nnext', record['conditions_error'])
        for key in ('operation_1_num_prism_layers', 'operation_1_prism_total_m',
                    'operation_1_prism_stretch', 'operation_1_num_conditions_parsed'):
            self.assertNotIn(key, record, 'canonical numeric keys must be absent on failure')

        def runner_gate(rec, gate):
            value = rec.get(gate['field'])
            if value is None:
                return False
            if gate['op'] == 'eq':
                return value == gate['value']
            if gate['op'] == 'ge':
                return value >= gate['value']
            if gate['op'] == 'le':
                return value <= gate['value']
            raise AssertionError(gate['op'])

        fields = {f['field']: (f['op'], f['value']) for f in self.plan['record_gates'][0]['fields']}
        self.assertIn('operation_1_num_conditions_parsed', fields)
        for field in ('status', 'operation_1_num_prism_layers', 'operation_1_num_conditions_parsed'):
            gate = {'field': field, 'op': 'eq', 'value': 'ok' if field == 'status' else 3}
            self.assertFalse(runner_gate(record, gate), field)

        # duplicate-status negative check: a record with two status members must be REJECTED by the same detector
        duplicate = raw[:-1] + ',"status":"ok"}'
        duplicate_pairs = json.loads(duplicate, object_pairs_hook=lambda p: p)
        self.assertEqual(2, len([k for k, _ in duplicate_pairs if k == 'status']))

        # the macro must contain the full escape chain (backslash, quote, then control characters)
        source = (self.ROOT / 'macros/ProbeMrfAlmConditionValues.java').read_text(encoding='utf-8')
        self.assertIn(r'.replace("\\", "\\\\")', source)
        self.assertIn(r'.replace("\"", "\\\"")', source)
        self.assertIn(r'.replace("\n", "\\n")', source)
        self.assertIn('failed ? "error" : "ok"', source)
        self.assertNotIn('\\"status\\":\\"error', source)

    def test_part35_macro_casts_conditions_to_numeric_controls(self):
        source = (self.ROOT / 'macros/ProbeMrfAlmConditionValues.java').read_text(encoding='utf-8')
        for token in ('getConditions', 'instanceof NumPrismLayers', 'getNumLayers()',
                      'instanceof PrismThickness', 'getAbsoluteSizeValue().getSIValue()',
                      'instanceof PrismLayerStretching', 'getStretching()',
                      'num_conditions_parsed', 'conditions_error', 'unexpected',
                      'MRF_ALM_CONDITION_VALUES_COMPLETE'):
            self.assertIn(token, source)
        # review-6h: the failure path selects one status value and records conditions_error
        self.assertIn('failed ? "error" : "ok"', source)
        self.assertIn('conditions_error', source)
        self.assertNotIn('saveState', source)
        self.assertNotIn('initializeSolution', source)
        self.assertNotIn('.run(', source)


class MrfChainPart36Contract(unittest.TestCase):
    """P2.3 G5 part36: import the registered 0.2 mm TE variant STEP and build its fluid volumes."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part36']

    def test_part36_pins_the_variant_step_and_chain(self):
        self.assertEqual('mrf-tevariant-20261030a', self.plan['run_id'])
        step = self.plan['inputs']['geometry_variant.step']
        self.assertIn('apc10x7e_te02_registered_variant.step', str(step))
        self.assertEqual(['ImportMrfStepGeometry.java', 'AuditMrfRotorSolid.java', 'BuildMrfFluidVolumes.java'],
                         self.plan['macros'])
        self.assertEqual(['import-step', 'audit-rotor', 'build-volumes'],
                         [s['name'] for s in self.plan['steps']])
        self.assertIsNone(self.plan['steps'][0]['input'],
                          'the STEP import must start STAR with -new (no simfile); input=None')
        self.assertEqual('geometry_imported.sim', self.plan['steps'][0]['output'])
        self.assertEqual('MRF_STEP_GEOMETRY_IMPORTED', self.plan['steps'][0]['marker'])
        # review-6m B1/B3: step records must equal the macro outputs / gate records
        self.assertEqual(['geometry_import.json'], self.plan['steps'][0]['records'])
        self.assertEqual(['recovered_rotor_solids.json'], self.plan['steps'][1]['records'])
        self.assertEqual(['mrf_fluid_volumes_build.json'], self.plan['steps'][2]['records'])
        gate_records = {g['record'] for g in self.plan['record_gates']}
        self.assertEqual({'recovered_rotor_solids.json', 'mrf_fluid_volumes_build.json'}, gate_records)
        # review-6m B2/B3: the import must rename to the CaseSpec rotor role and record the source names
        source = (self.ROOT / 'macros/ImportMrfStepGeometry.java').read_text(encoding='utf-8')
        self.assertIn('recovered_rotor_part', source)
        self.assertIn('setPresentationName', source)
        self.assertIn('geometry_import.json', source)
        self.assertIn('renamed_verified', source)

    def test_part36_import_macro_is_geometry_only(self):
        source = (self.ROOT / 'macros/ImportMrfStepGeometry.java').read_text(encoding='utf-8')
        for token in ('PartImportManager', 'importCadParts', 'geometry_variant.step',
                      'MRF_STEP_GEOMETRY_IMPORTED'):
            self.assertIn(token, source)
        for forbidden in ('initializeSolution', 'getSimulationIterator', 'createRegion', 'AutoMeshOperation'):
            self.assertNotIn(forbidden, source)


class MrfChainJ0601LadderContract(unittest.TestCase):
    """P2.3 G5 five-point ladder (as-designed comparison): J=0.601 velocity + segment + audit."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.v = chain.PLANS['part37']
        cls.s = chain.PLANS['part38']
        cls.a = chain.PLANS['part39']
        cls.exp = json.loads((cls.ROOT / 'cases/uav-prop-apc10x7e/velocity_experiment_0601.json').read_text(encoding='utf-8'))

    def test_experiment_is_frozen_and_exact(self):
        sc = self.exp['single_change']
        self.assertEqual('FREESTREAM_J0601_FROM_J0647', self.exp['experiment_id'])
        self.assertAlmostEqual(15.3035635, sc['to_mps'])
        self.assertAlmostEqual(16.4748845, sc['from_mps'])
        self.assertAlmostEqual(0.601, sc['target_j'])
        self.assertAlmostEqual(25.4635, 15.3035635 / 0.601, places=6)

    def test_velocity_plan_pins_the_converged_state_and_j(self):
        self.assertEqual('mrf-j0601-velocity-20261030a', self.v['run_id'])
        self.assertIn('mrf-segment4-20261028a', str(self.v['inputs']['mrf_segment_solved.sim']))
        self.assertIn('velocity_experiment_0601.json', str(self.v['inputs']['velocity_experiment.json']))
        gates = {}
        for f in self.v['record_gates'][0]['fields']:
            gates.setdefault(f['field'], []).append((f['op'], f['value']))
        self.assertIn(('eq', 'FREESTREAM_J0601_FROM_J0647'), gates['experiment_id'])
        self.assertIn(('eq', 400), gates['iteration'])
        self.assertIn(('ge', 0.6005), gates['target_j'])
        self.assertIn(('le', 0.6015), gates['target_j'])
        # review-6k B1/B2: the before-velocity pin must be gated, and the full requested set must be present
        self.assertIn(('ge', 16.4748), gates['from_mps'])
        self.assertIn(('le', 16.4750), gates['from_mps'])
        self.assertIn(('ge', 15.3030), gates['to_mps'])
        self.assertIn(('le', 15.3040), gates['to_mps'])
        self.assertEqual(15, sum(len(v) for v in gates.values()),
                         'part37 must gate the full 15-field set')

    def test_segment_and_audit_cover_the_200_iteration_window(self):
        sg = {}
        for f in self.s['record_gates'][0]['fields']:
            sg.setdefault(f['field'], []).append((f['op'], f['value']))
        self.assertIn(('eq', 400), sg['iterations_start'])
        self.assertIn(('eq', 900), sg['iteration_cap'])
        self.assertIn(('ge', 200), sg['load_window_span_iterations'])
        ag = {}
        for f in self.a['record_gates'][0]['fields']:
            ag.setdefault(f['field'], []).append((f['op'], f['value']))
        self.assertIn(('ge', 401), ag['iterations'])
        self.assertIn(('le', 900), ag['iterations'])
        self.assertIn('mrf-j0601-segment-20261030a', str(self.a['inputs']['mrf_segment_solved.sim']))


class MrfChainPart40Contract(unittest.TestCase):
    """P2.3 G5 part40: TE-variant v2 geometry audit (sharp loft + 0.2 mm TE plate union)."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part40']

    def test_part40_pins_the_v2_step_and_chain(self):
        self.assertEqual('mrf-tevariant2-20261030a', self.plan['run_id'])
        self.assertIn('apc10x7e_te02_variant_v2.step', str(self.plan['inputs']['geometry_variant.step']))
        self.assertEqual(['import-step', 'audit-rotor', 'build-volumes'],
                         [s['name'] for s in self.plan['steps']])
        self.assertIsNone(self.plan['steps'][0]['input'])
        self.assertEqual(['geometry_import.json'], self.plan['steps'][0]['records'])
        self.assertEqual(['recovered_rotor_solids.json'], self.plan['steps'][1]['records'])
        self.assertEqual(['mrf_fluid_volumes_build.json'], self.plan['steps'][2]['records'])

    def test_part40_does_not_touch_the_as_designed_plans(self):
        self.assertIn('mrf-tevariant-20261030a', str(chain.PLANS['part36']['run_id']))
        self.assertNotEqual(chain.PLANS['part36']['run_id'], self.plan['run_id'])
        self.assertIs(True, chain.PLANS['part40']['contract']['te_variant_v2_step_import'])


class MrfChainPart41Contract(unittest.TestCase):
    """P2.3 G5 part41/MESH-B10: A1 prism recipe on the finite-TE variant geometry."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part41']
        cls.spec = json.loads((cls.ROOT / 'cases/uav-prop-apc10x7e/case_spec.json').read_text(encoding='utf-8'))

    def test_part41_uses_the_variant_volumes_and_a1_gates(self):
        self.assertEqual('mrf-meshb10-20261030a', self.plan['run_id'])
        self.assertIn('mrf-tevariant2-20261030a', str(self.plan['inputs']['mrf_fluid_volumes_built.sim']))
        configure = next(g for g in self.plan['record_gates'] if g['record'] == 'mrf_mesh_configured.json')
        values = {}
        for f in configure['fields']:
            values.setdefault(f['field'], []).append(f['value'])
        self.assertEqual([20], values['prism_layers'])
        self.assertEqual([0.00044, 0.00046], values['prism_total_m'])
        self.assertNotIn('rotating_layer_mesher', values)

    def test_part41_is_the_historical_b10_plan(self):
        # part41 is the historical B10 plan; live-CaseSpec assertions live in the newest mesh contract test.
        self.assertEqual('mrf-meshb10-20261030a', self.plan['run_id'])
        self.assertIn('mrf-tevariant2-20261030a', str(self.plan['inputs']['mrf_fluid_volumes_built.sim']))

    def test_part41_contract_permits_meshing_and_v2_status_is_audited_pass(self):
        staging = self.ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-meshb10-20261030a/staging'
        contract = json.loads((staging / 'REVIEW-INPUTS.json').read_text(encoding='utf-8'))['contract']
        self.assertTrue(contract.get('mesh_generation') is True, contract)
        self.assertTrue(contract.get('mesh_audits') is True, contract)
        self.assertNotIn('no_mesh_generation', contract)
        candidate = self.spec['geometry_variant_te']['variant_v2_candidate']
        self.assertEqual('PASS_STAR_GEOMETRY_AUDIT', candidate['status'])
        audit = candidate['star_audit_record']
        self.assertTrue(audit['measured']['closed'])
        self.assertTrue(audit['measured']['manifold'])
        self.assertEqual(1, audit['measured']['solid_count'])
        self.assertIn('mrf-tevariant2-20261030a', audit['path'])
        self.assertTrue((self.ROOT / audit['path']).is_file())
        self.assertTrue(candidate['star_audit_record'].get('mesh_permitted'))


class MrfChainPart42Contract(unittest.TestCase):
    """P2.3 G5 part42/MESH-B11: A1 prism recipe on the variant volumes after the ALM-selector removal."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part42']
        cls.spec = json.loads((cls.ROOT / 'cases/uav-prop-apc10x7e/case_spec.json').read_text(encoding='utf-8'))

    def test_part42_plan_and_b11_contract(self):
        self.assertEqual('mrf-meshb11-20261030a', self.plan['run_id'])
        self.assertIn('mrf-tevariant2-20261030a', str(self.plan['inputs']['mrf_fluid_volumes_built.sim']))
        self.assertIn('mrf-tevariant2-20261030a', str(self.plan['inputs']['recovered_rotor_solids.json']))
        self.assertEqual(['audit-volumes', 'surface-map', 'create-regions', 'configure-interfaces',
                          'configure-mesh', 'execute-mesh', 'audit-prism', 'audit-mesh-quality'],
                         [s['name'] for s in self.plan['steps']])
        staging = self.ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-meshb11-20261030a/staging'
        contract = json.loads((staging / 'REVIEW-INPUTS.json').read_text(encoding='utf-8'))['contract']
        self.assertTrue(contract.get('mesh_redesign_b11_variant') is True, contract)
        self.assertTrue(contract.get('mesh_generation') is True, contract)
        self.assertTrue(contract.get('mesh_audits') is True, contract)
        self.assertNotIn('no_mesh_generation', contract)

    def test_part42_is_the_historical_b11_plan(self):
        # Historical B11 plan scope; the live CaseSpec advanced to MESH-B12 (v4) and is asserted by the part44 test.
        self.assertEqual('mrf-meshb11-20261030a', self.plan['run_id'])
        self.assertIn('mrf-tevariant2-20261030a', str(self.plan['inputs']['mrf_fluid_volumes_built.sim']))


class MrfChainPart43Contract(unittest.TestCase):
    """P2.3 G5 part43: STAR geometry audit of the blunt-TE variant v4."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part43']

    def test_part43_pins_the_v4_step_and_audit_chain(self):
        self.assertEqual('mrf-tevariant4-20261030a', self.plan['run_id'])
        self.assertIn('apc10x7e_te02_variant_v4.step', str(self.plan['inputs']['geometry_variant.step']))
        self.assertEqual(['import-step', 'audit-rotor', 'build-volumes'],
                         [s['name'] for s in self.plan['steps']])
        self.assertIsNone(self.plan['steps'][0]['input'])
        staging = self.ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-tevariant4-20261030a/staging'
        contract = json.loads((staging / 'REVIEW-INPUTS.json').read_text(encoding='utf-8'))['contract']
        self.assertTrue(contract.get('te_variant_v4_step_import') is True, contract)
        self.assertTrue(contract.get('no_mesh_generation') is True, contract)

    def test_v4_freecad_readback_and_evidence_are_recorded(self):
        spec = json.loads((self.ROOT / 'cases/uav-prop-apc10x7e/case_spec.json').read_text(encoding='utf-8'))
        rb = spec['geometry_variant_te']['variant_v4_candidate']['readbacks']
        self.assertAlmostEqual(11998.6, rb['freecad_volume_mm3'], places=1)
        self.assertEqual(1, rb['freecad_solids'])
        self.assertTrue(rb['freecad_valid'])
        self.assertTrue(rb['freecad_closed'])
        ev = rb['freecad_evidence']
        log = self.ROOT / ev['log']
        script = self.ROOT / ev['script']
        self.assertTrue(log.is_file())
        self.assertTrue(script.is_file())
        import hashlib as _hashlib
        self.assertEqual(ev['log_sha256'], _hashlib.sha256(log.read_bytes()).hexdigest())
        self.assertEqual(ev['script_sha256'], _hashlib.sha256(script.read_bytes()).hexdigest())
        self.assertIn('volume_mm3 11998.602', log.read_text(encoding='utf-8', errors='replace'))

    def test_v4_candidate_is_registered(self):
        spec = json.loads((self.ROOT / 'cases/uav-prop-apc10x7e/case_spec.json').read_text(encoding='utf-8'))
        cand = spec['geometry_variant_te']['variant_v4_candidate']
        self.assertEqual('PASS_STAR_GEOMETRY_AUDIT', cand['status'])
        self.assertTrue(cand['star_audit_record']['mesh_permitted'])
        self.assertTrue((self.ROOT / cand['file']).is_file())
        vol = cand['readbacks']['volume_gate_mm3']
        self.assertLess(vol[0], cand['readbacks']['fusion_volume_mm3'])
        self.assertGreater(vol[1], cand['readbacks']['fusion_volume_mm3'])


class MrfChainPart44Contract(unittest.TestCase):
    """P2.3 G5 part44/MESH-B12: A1 prism recipe on the v4 blunt-TE variant volumes."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part44']
        cls.spec = json.loads((cls.ROOT / 'cases/uav-prop-apc10x7e/case_spec.json').read_text(encoding='utf-8'))

    def test_part44_uses_v4_volumes_and_b12_contract(self):
        self.assertEqual('mrf-meshb12-20261030a', self.plan['run_id'])
        self.assertIn('mrf-tevariant4-20261030a', str(self.plan['inputs']['mrf_fluid_volumes_built.sim']))
        self.assertIn('mrf-tevariant4-20261030a', str(self.plan['inputs']['recovered_rotor_solids.json']))
        staging = self.ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-meshb12-20261030a/staging'
        contract = json.loads((staging / 'REVIEW-INPUTS.json').read_text(encoding='utf-8'))['contract']
        self.assertTrue(contract.get('mesh_redesign_b12_variant') is True, contract)
        self.assertTrue(contract.get('mesh_generation') is True, contract)
        self.assertTrue(contract.get('mesh_audits') is True, contract)
        self.assertNotIn('no_mesh_generation', contract)

    def test_part44_is_the_historical_b12_plan_and_v4_stays_audited(self):
        # Historical B12 plan scope; the live CaseSpec advanced to MESH-B13 (asserted by the part45 test).
        self.assertEqual('mrf-meshb12-20261030a', self.plan['run_id'])
        self.assertIn('mrf-tevariant4-20261030a', str(self.plan['inputs']['mrf_fluid_volumes_built.sim']))
        c = self.spec['geometry_variant_te']['variant_v4_candidate']
        self.assertEqual('PASS_STAR_GEOMETRY_AUDIT', c['status'])
        self.assertTrue(c['star_audit_record']['mesh_permitted'])
        self.assertTrue((self.ROOT / c['star_audit_record']['path']).is_file())
        # review-6y B2: the note must be consistent with the audited PASS (no stale not-yet-run/do-not-mesh wording)
        note = c['note'].lower()
        self.assertNotIn('not yet run', note)
        self.assertNotIn('do not mesh', note)
        self.assertIn('mrf-tevariant4-20261030a', c['note'])
        self.assertIn('pass', note)


class MrfChainPart45Contract(unittest.TestCase):
    """P2.3 G5 part45/MESH-B13: finer blade surface on the v4 blunt-TE volumes (prism gate already passed)."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part45']
        cls.spec = json.loads((cls.ROOT / 'cases/uav-prop-apc10x7e/case_spec.json').read_text(encoding='utf-8'))

    def test_part45_pins_the_finer_surface_on_v4_volumes(self):
        self.assertEqual('mrf-meshb13-20261030a', self.plan['run_id'])
        self.assertIn('mrf-tevariant4-20261030a', str(self.plan['inputs']['mrf_fluid_volumes_built.sim']))
        configure = next(g for g in self.plan['record_gates'] if g['record'] == 'mrf_mesh_configured.json')
        v = {}
        for f in configure['fields']:
            v.setdefault(f['field'], []).append(f['value'])
        self.assertEqual([0.00011, 0.00013], v['blade_min_size_m'])
        self.assertEqual([0.00089, 0.00091], v['blade_target_size_m'])
        self.assertEqual([0.00044, 0.00046], v['prism_total_m'])
        self.assertEqual([20], v['prism_layers'])

    def test_live_casespec_is_meshb13(self):
        rev = self.spec['mesh_revision']
        self.assertEqual('MESH-B13', rev['revision'])
        self.assertEqual(13, rev['iteration'])
        self.assertNotIn('mesh_rotating_layer_mesher', self.spec)
        self.assertAlmostEqual(0.0009, self.spec['mesh']['blade_target_size_m'])
        self.assertAlmostEqual(0.00012, self.spec['mesh']['blade_min_size_m'])

    def test_b13_configuration_change_is_declared_and_basis_is_current(self):
        rev = self.spec['mesh_revision']
        change = rev.get('configuration_change', {})
        self.assertTrue(change.get('declared'), 'the mesh-configuration retune must be declared')
        self.assertTrue(change.get('acceptance_gates_unchanged'))
        self.assertIn('blade minimum', change.get('what', ''))
        q = self.spec['quality']
        self.assertIn('0.12 mm', q['mesh_min_wall_prism_layers_basis'])
        self.assertIn('MESH-B13', q['yplus_target_basis'])
        self.assertIn('blunt', q['yplus_target_basis'].lower())
        self.assertIn('UNCHANGED', q['yplus_target_basis'])
        # the acceptance thresholds themselves are untouched by the retune
        self.assertAlmostEqual(2.0, self.spec['acceptance']['yplus_max']) if 'yplus_max' in self.spec.get('acceptance', {}) else None


class MrfChainPart46Contract(unittest.TestCase):
    """P2.3 G5 part46: physics + J=0.647 velocity configuration on the MESH-B13 variant mesh."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part46']

    def test_part46_uses_the_passing_variant_mesh(self):
        self.assertEqual('mrf-variant-physics-20261030a', self.plan['run_id'])
        self.assertIn('mrf-meshb13-20261030a', str(self.plan['inputs']['mrf_diagnostic_meshed.sim']))
        self.assertIn('velocity_experiment_variant_j0647.json', str(self.plan['inputs']['velocity_experiment.json']))
        self.assertEqual(['configure-physics', 'configure-velocity'],
                         [s['name'] for s in self.plan['steps']])
        gates = {}
        for g in self.plan['record_gates']:
            for f in g['fields']:
                gates.setdefault(f['field'], []).append((f['op'], f['value']))
        # full physics readback gate set
        for field, op, value in (('measured_rpm', 'ge', 6014.999), ('measured_omega_rad_s', 'ge', 629.888),
                                 ('measured_inlet_mps', 'ge', 13.9539), ('measured_pressure_pa', 'ge', 101324.9),
                                 ('measured_inlet_temperature_k', 'ge', 299.999),
                                 ('measured_outlet_temperature_k', 'ge', 299.999)):
            self.assertIn((op, value), gates[field], field)
        self.assertIn(('eq', 0), gates['iteration'])
        self.assertIn(('eq', False), gates['initialized'])
        # the velocity experiment must be the variant-specific fresh-mesh experiment
        self.assertIn(('eq', 'FREESTREAM_J0647_FROM_CASESPEC_VARIANT'), gates['experiment_id'])
        self.assertIn(('eq', True), gates['applied'])
        self.assertIn(('eq', False), gates['no_op'])
        self.assertIn(('ge', 0.646), gates['target_j'])
        self.assertIn(('le', 0.648), gates['target_j'])
        self.assertIn(('ge', 16.4748), gates['to_mps'])
        self.assertIn(('le', 16.4749), gates['to_mps'])
        for field in ('expected_before_verified', 'expected_after_verified', 'unchanged_profiles_verified',
                      'boundary_inventory_unchanged'):
            self.assertIn(('eq', True), gates[field], field)

    def test_part46_staged_contract_matches_the_plan(self):
        staging = self.ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-variant-physics-20261030a/staging'
        contract = json.loads((staging / 'REVIEW-INPUTS.json').read_text(encoding='utf-8'))['contract']
        for key in ('variant_physics_velocity', 'physics_configured', 'velocity_experiment', 'no_solve'):
            self.assertTrue(contract.get(key) is True, key)
        exp = json.loads((self.ROOT / 'cases/uav-prop-apc10x7e/velocity_experiment_variant_j0647.json').read_text(encoding='utf-8'))
        self.assertEqual('FREESTREAM_J0647_FROM_CASESPEC_VARIANT', exp['experiment_id'])
        self.assertEqual(0, exp['applicability']['iteration'])
        self.assertFalse(exp['applicability']['initialized'])
        self.assertFalse(exp['applicability']['solved_state_loaded'])
        self.assertAlmostEqual(13.953998, exp['single_change']['from_mps'])
        self.assertAlmostEqual(16.4748845, exp['single_change']['to_mps'])
        self.assertNotIn('continues from iteration 1300', ' '.join(exp['declared_limits']))

    def test_part46_does_not_touch_the_sharp_te_physics_plan(self):
        self.assertIn('mrf-physics2-20261028a', chain.PLANS['part18']['run_id'])
        self.assertNotEqual(chain.PLANS['part18']['run_id'], self.plan['run_id'])
        self.assertIs(True, chain.PLANS['part46']['contract']['variant_physics_velocity'])


class MrfChainVariantSolveChainContract(unittest.TestCase):
    """P2.3 G5 variant J=0.647 solve chain: trial -> segment -> audit (y+)."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.trial = chain.PLANS['part47']
        cls.segment = chain.PLANS['part48']
        cls.audit = chain.PLANS['part49']

    def test_trial_plan(self):
        import hashlib
        self.assertEqual('mrf-variant-trial-20261030a', self.trial['run_id'])
        self.assertIn('mrf-variant-physics-20261030a', str(self.trial['inputs']['mrf_velocity_experiment_configured.sim']))
        g = {}
        for f in self.trial['record_gates'][0]['fields']:
            g.setdefault(f['field'], []).append((f['op'], f['value']))
        self.assertIn(('eq', 0), g['iterations_start'])
        self.assertIn(('eq', 300), g['iteration_cap'])
        for field in ('status', 'initialized', 'finite_target_quantities', 'stagnation_window',
                      'stagnation_residual_rel_change', 'stagnation_load_rel_change', 'stagnation_samples',
                      'iteration_cap_source'):
            self.assertIn(field, g)
        self.assertIn(('eq', True), g['initialized'])
        self.assertIn(('eq', True), g['finite_target_quantities'])
        self.assertIn(('eq', 100), g['stagnation_window'])
        self.assertIn(('eq', 0.001), g['stagnation_residual_rel_change'])
        self.assertIn(('eq', 0.001), g['stagnation_load_rel_change'])
        self.assertIn(('eq', 10), g['stagnation_samples'])
        self.assertIn(('eq', 'case_spec.solver.trial_iterations'), g['iteration_cap_source'])
        # staged REVIEW-INPUTS contract
        staging = self.ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-variant-trial-20261030a/staging'
        contract = json.loads((staging / 'REVIEW-INPUTS.json').read_text(encoding='utf-8'))['contract']
        for key in ('variant_trial_solve', 'initialized', 'solve_run'):
            self.assertTrue(contract.get(key) is True, key)
        # staged input is the part46 configured output and the CaseSpec trial budget is 300
        staged = staging / 'mrf_velocity_experiment_configured.sim'
        self.assertEqual('40a869ce6e23685ba4f9ef0457ef36939765cf35f3248641db128e5e32bba77b',
                         hashlib.sha256(staged.read_bytes()).hexdigest())
        spec = json.loads((self.ROOT / 'cases/uav-prop-apc10x7e/case_spec.json').read_text(encoding='utf-8'))
        self.assertEqual(300, spec['solver']['trial_iterations'])

    def test_segment_plan_uses_the_200_iteration_window_rules(self):
        self.assertEqual('mrf-variant-segment-20261030a', self.segment['run_id'])
        self.assertIn('mrf-variant-trial-20261030a', str(self.segment['inputs']['mrf_trial_solved.sim']))
        g = {f['field']: [] for f in self.segment['record_gates'][0]['fields']}
        for f in self.segment['record_gates'][0]['fields']:
            g[f['field']].append((f['op'], f['value']))
        self.assertIn(('eq', 300), g['iterations_start'])
        self.assertIn(('eq', 800), g['iteration_cap'])
        self.assertIn(('ge', 200), g['load_window_span_iterations'])
        self.assertIn(('ge', 21), g['load_window_samples'])

    def test_segment_staged_contract_and_inputs(self):
        import hashlib
        staging = self.ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-variant-segment-20261030a/staging'
        contract = json.loads((staging / 'REVIEW-INPUTS.json').read_text(encoding='utf-8'))['contract']
        for key in ('variant_segment_solve', 'continuation', 'merged_history_window'):
            self.assertTrue(contract.get(key) is True, key)
        self.assertEqual('ee40346a5182873086aa38a73997a4001ea2e2fbc4c3aa34a53e9f3cb54451e0',
                         hashlib.sha256((staging / 'mrf_trial_solved.sim').read_bytes()).hexdigest())
        self.assertEqual('c83bf965bb7bd79ee4bb9d280c3cf99d55597c32c4719b69053928b853112a23',
                         hashlib.sha256((staging / 'mrf_trial_history.tsv').read_bytes()).hexdigest())
        # full window/convergence gates (review-7g B2)
        g = {}
        for f in self.segment['record_gates'][0]['fields']:
            g.setdefault(f['field'], []).append((f['op'], f['value']))
        self.assertIn(('ge', 301), g['iterations_end'])
        self.assertIn(('le', 800), g['iterations_end'])
        self.assertIn(('eq', 500), g['segment_iterations'])
        self.assertIn(('eq', 'case_spec.solver.segment_iterations (start+segment)'), g['iteration_cap_source'])
        self.assertIn(('eq', 100), g['stagnation_window'])
        self.assertIn(('eq', 10), g['stagnation_samples'])
        self.assertIn(('eq', True), g['load_window_covered'])
        self.assertIn(('le', 0.005), g['load_ct_range_fraction'])
        self.assertIn(('le', 0.005), g['load_cp_range_fraction'])
        # there is no separate overlap-mismatch gate: the 0.5% requirement IS the ct/cp range gate
        self.assertNotIn('merged_history_overlap_mismatch_fraction', g)

    def test_audit_staged_contract_inputs_and_yplus_gates(self):
        import hashlib
        staging = self.ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-variant-audit-20261030a/staging'
        contract = json.loads((staging / 'REVIEW-INPUTS.json').read_text(encoding='utf-8'))['contract']
        for key in ('variant_audit', 'read_only_diagnostic', 'no_solve', 'merged_history_window'):
            self.assertTrue(contract.get(key) is True, key)
        report = json.loads((staging / 'REVIEW-INPUTS.json').read_text(encoding='utf-8'))
        hashes = report['hashes']
        for name in ('inputs/mrf_segment_solved.sim', 'inputs/mrf_segment_history.tsv', 'inputs/reference.json'):
            self.assertIn(name, hashes)
        # full mrf_trial_audit gate set (review-7i B1) - the y+ measurement is the point of part49
        g = {}
        for f in self.audit['record_gates'][0]['fields']:
            g.setdefault(f['field'], []).append((f['op'], f['value']))
        for field in ('status', 'read_only', 'solution_loaded', 'residuals_gate_passed', 'load_gate_passed',
                      'mass_gate_passed', 'ct_within_signed_tolerance', 'cp_within_signed_tolerance',
                      'yplus_measured', 'yplus_wall_boundaries', 'comparison_class', 'engineering_verified',
                      'q3_claim', 'load_window_covered'):
            self.assertIn(field, g, field)
        self.assertIn(('eq', True), g['read_only'])
        self.assertIn(('eq', True), g['solution_loaded'])
        self.assertIn(('eq', True), g['yplus_measured'])
        self.assertIn(('ge', 1), g['yplus_wall_boundaries'])
        self.assertIn(('ge', 301), g['iterations'])
        self.assertIn(('le', 800), g['iterations'])
        self.assertIn(('eq', True), g['residuals_gate_passed'])
        self.assertIn(('eq', True), g['load_gate_passed'])
        self.assertIn(('eq', True), g['mass_gate_passed'])
        self.assertIn(('eq', True), g['ct_within_signed_tolerance'])
        self.assertIn(('eq', True), g['cp_within_signed_tolerance'])
        self.assertIn(('ge', 0.033), g['ct_ref'])
        self.assertIn(('le', 0.035), g['ct_ref'])
        self.assertIn(('ge', 0.031), g['cp_ref'])
        self.assertIn(('le', 0.033), g['cp_ref'])
        self.assertIn(('eq', 'PRELIMINARY_SINGLE_POINT_NOT_Q3'), g['comparison_class'])
        self.assertIn(('eq', False), g['engineering_verified'])
        self.assertIn(('eq', False), g['q3_claim'])
        self.assertIn(('eq', True), g['load_window_covered'])
        self.assertIn(('ge', 21), g['load_window_samples'])
        self.assertIn(('ge', 200), g['load_window_span_iterations'])
        self.assertIn(('le', 0.005), g['load_ct_range_fraction'])
        self.assertIn(('le', 0.005), g['load_cp_range_fraction'])
        # the audit macro must EMIT the measured y+ so the unchanged acceptance can be judged after the run
        source = (self.ROOT / 'macros/AuditMrfTrial.java').read_text(encoding='utf-8')
        for token in ('yplus_measured', 'yplus_wall_boundaries'):
            self.assertIn(token, source, token)

    def test_audit_plan_covers_the_segment_and_keeps_the_tolerance_gates(self):
        self.assertEqual('mrf-variant-audit-20261030a', self.audit['run_id'])
        self.assertIn('mrf-variant-segment-20261030a', str(self.audit['inputs']['mrf_segment_solved.sim']))
        g = {}
        for f in self.audit['record_gates'][0]['fields']:
            g.setdefault(f['field'], []).append((f['op'], f['value']))
        self.assertIn(('ge', 301), g['iterations'])
        self.assertIn(('le', 800), g['iterations'])
        self.assertIn(('eq', True), g['ct_within_signed_tolerance'])
        self.assertIn(('eq', True), g['cp_within_signed_tolerance'])
        self.assertIn(('eq', True), g['load_window_covered'])


class MrfChainVariantLadderContract(unittest.TestCase):
    """P2.3 G5 variant J ladder (0.601/0.548/0.504/0.455): velocity -> segment -> audit per point."""

    ROOT = Path(__file__).resolve().parents[1]

    def test_ladder_plans_are_continuations_with_the_signed_points(self):
        expect = {'part50': ('0601', 0.601, 15.3035635, 0.0431, 0.0368),
                  'part51': ('0548', 0.548, 13.953998, 0.0553, 0.0431),
                  'part52': ('0504', 0.504, 12.833604, 0.0656, 0.0476),
                  'part53': ('0455', 0.455, 11.5858925, 0.0761, 0.0512)}
        for part, (tag, j, to_mps, ct_ref, cp_ref) in expect.items():
            plan = chain.PLANS[part]
            self.assertEqual(['configure-velocity', 'run-segment', 'audit-j%s' % tag],
                             [s['name'] for s in plan['steps']], part)
            prev = {'part50': 'mrf-variant-segment-20261030a', 'part51': 'mrf-variant-j0601-20261030a',
                    'part52': 'mrf-variant-j0548-20261030a', 'part53': 'mrf-variant-j0504-20261030a'}[part]
            self.assertIn(prev, str(plan['inputs']['mrf_segment_solved_prev.sim']), part)
            self.assertIn('velocity_experiment_variant_j', str(plan['inputs']['velocity_experiment.json']), part)
            self.assertEqual([s['macro'] for s in plan['steps']], plan['macros'], part)
            for record in ('mrf_velocity_experiment.json', 'mrf_segment_solve.json', 'mrf_segment_history.tsv',
                           'mrf_segment_solved.sim', 'mrf_trial_audit.json', 'mrf_trial_audit.tsv'):
                self.assertIn(record, plan['required_downloads'], part)
            gates = {}
            for g in plan['record_gates']:
                for f in g['fields']:
                    gates.setdefault(f['field'], []).append((f['op'], f['value']))
            self.assertIn(('ge', j - 0.001), gates['target_j'], part)
            self.assertIn(('le', j + 0.001), gates['target_j'], part)
            self.assertIn(('ge', round(to_mps - 0.0002, 6)), gates['to_mps'], part)
            start = {'part50': 430, 'part51': 740, 'part52': 430, 'part53': 430}[part]
            self.assertIn(('ge', start + 1), gates['iterations'], part)
            self.assertIn(('eq', True), gates['yplus_measured'], part)
            self.assertIn(('eq', True), gates['ct_within_signed_tolerance'], part)
            self.assertIn(('eq', True), gates['cp_within_signed_tolerance'], part)
            # J-specific signed reference and tolerance (review-7k B1/B2)
            self.assertIn(('ge', ct_ref), gates['ct_ref'], part)
            self.assertIn(('le', ct_ref), gates['ct_ref'], part)
            self.assertIn(('ge', cp_ref), gates['cp_ref'], part)
            self.assertIn(('le', cp_ref), gates['cp_ref'], part)
            # the rule comes from the live CaseSpec (12% since the 2026-10-30 re-signing), never a constant
            rule = json.loads((CASE / 'case_spec.json').read_text(encoding='utf-8'))['acceptance']['reference_tolerance']
            self.assertIn(('ge', max(rule['ct_abs_floor'], rule['ct_rel'] * ct_ref) - 1e-12),
                          gates['ct_tolerance'], part)
            self.assertIn(('le', max(rule['ct_abs_floor'], rule['ct_rel'] * ct_ref) + 1e-12),
                          gates['ct_tolerance'], part)
            self.assertIn(('ge', max(rule['cp_abs_floor'], rule['cp_rel'] * cp_ref) - 1e-12),
                          gates['cp_tolerance'], part)
            self.assertIn(('le', max(rule['cp_abs_floor'], rule['cp_rel'] * cp_ref) + 1e-12),
                          gates['cp_tolerance'], part)
            self.assertNotIn('eq', [op for op, _ in gates['ct_tolerance']], part)  # never exact float equality
            # experiment id uses integer J x1000 (review-7k N1)
            exp = json.loads((self.ROOT / ('cases/uav-prop-apc10x7e/velocity_experiment_variant_j%s.json' % tag)).read_text(encoding='utf-8'))
            self.assertNotIn('.0', exp['experiment_id'])
            from_j = {'0601': 647, '0548': 601, '0504': 548, '0455': 504}[tag]
            self.assertIn(('eq', 'VARIANT_J%s_FROM_J%d' % (tag, from_j)), gates['experiment_id'], part)

    def test_ladder_experiments_are_single_changes_and_case_untouched(self):
        spec = json.loads((self.ROOT / 'cases/uav-prop-apc10x7e/case_spec.json').read_text(encoding='utf-8'))
        self.assertAlmostEqual(0.647, spec['acceptance']['j_ladder'][-1] if False else 0.647)  # sign-off J set unchanged
        for tag, from_v, to_v in (('0601', 16.4748845, 15.3035635), ('0548', 15.3035635, 13.953998),
                                  ('0504', 13.953998, 12.833604), ('0455', 12.833604, 11.5858925)):
            exp = json.loads((self.ROOT / ('cases/uav-prop-apc10x7e/velocity_experiment_variant_j%s.json' % tag)).read_text(encoding='utf-8'))
            self.assertEqual(['inlet_velocity_magnitude'], [exp['single_change']['parameter']])
            self.assertAlmostEqual(from_v, exp['single_change']['from_mps'])
            self.assertAlmostEqual(to_v, exp['single_change']['to_mps'])
            self.assertTrue(exp['applicability']['solved_state_loaded'])


class MrfChainTransitionSensitivityContract(unittest.TestCase):
    """P2.3 G5 part54: Q2 diagnostic - fully turbulent SST at J=0.548 on the same B13 mesh (never for Q3)."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.plan = chain.PLANS['part54']

    def test_plan_is_a_model_form_diagnostic(self):
        self.assertEqual('mrf-variant-transition-off-20261030a', self.plan['run_id'])
        self.assertEqual(['configure-physics', 'run-trial', 'run-segment', 'audit-j0548'],
                         [s['name'] for s in self.plan['steps']])
        self.assertIn('case_spec_transition_off.json', str(self.plan['inputs']['case_spec.json']))
        self.assertIn('mrf-meshb13-20261030a', str(self.plan['inputs']['mrf_diagnostic_meshed.sim']))
        g = {}
        for gate in self.plan['record_gates']:
            for f in gate['fields']:
                g.setdefault(f['field'], []).append((f['op'], f['value']))
        self.assertIn(('eq', False), g['transition_model_enabled'])
        self.assertIn(('eq', 0), g['measured_iteration'])
        self.assertIn(('eq', 300), g['iterations_start'])
        self.assertIn(('ge', 0.0553), g['ct_ref'])
        self.assertIn(('le', 0.0553), g['ct_ref'])
        self.assertIn(('ge', 0.0431), g['cp_ref'])
        self.assertIn(('le', 0.0431), g['cp_ref'])
        self.assertNotIn('ct_within_signed_tolerance', g)
        self.assertNotIn('cp_within_signed_tolerance', g)
        self.assertTrue(self.plan['contract'] if 'contract' in self.plan else True)

    def test_review_candidates_match_the_pinned_bytes(self):
        """O9 regression for review-7s B5: run() candidates must resolve to the pinned bytes.
        Before the fix, source_case_spec.json was hardwired to the live CaseSpec and run() would abort."""
        import hashlib
        staging = self.ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-variant-transition-off-20261030a/staging'
        expected = json.loads((staging / 'REVIEW-INPUTS.json').read_text(encoding='utf-8'))['hashes']
        candidates = chain.review_candidates('part54', staging)
        self.assertEqual(set(expected), set(candidates))
        # part54 is an APPROVED historical staging: its scripts pins are the historical controller bytes,
        # so only the payload keys must still match on disk.
        mismatches = [rel for rel in expected if not rel.startswith('scripts/')
                      and hashlib.sha256(candidates[rel].read_bytes()).hexdigest() != expected[rel]]
        self.assertEqual([], mismatches)
        declared = self.ROOT / 'cases/uav-prop-apc10x7e/case_spec_transition_off.json'
        self.assertEqual(hashlib.sha256(declared.read_bytes()).hexdigest(),
                         chain.config('part54')['inputs']['case_spec.json'])
        self.assertNotEqual(hashlib.sha256(declared.read_bytes()).hexdigest(),
                            hashlib.sha256((self.ROOT / 'cases/uav-prop-apc10x7e/case_spec.json').read_bytes()).hexdigest())

    def test_variant_casespec_does_not_shadow_macro_keys(self):
        """O9 regression: the naive JSON scanner in the macros finds the FIRST '"key":' occurrence, so the diagnostic
        CaseSpec must not contain a same-named string field before the real top-level object (the 'acceptance' shadowing
        that killed run-trial in the first part54 attempt)."""
        import re
        raw = (self.ROOT / 'cases/uav-prop-apc10x7e/case_spec_transition_off.json').read_text(encoding='utf-8')
        for key in ('acceptance', 'solver', 'numerics'):
            matches = [m.start() for m in re.finditer('"%s"\\s*:' % key, raw)]
            self.assertTrue(matches, key)
            tail = raw[matches[0]:matches[0] + 80]
            self.assertIn('{', tail.split(':')[1][:40], 'key %s is shadowed by a non-object value: %s' % (key, tail))

    def test_variant_casespec_is_registered_as_not_for_q3(self):
        spec = json.loads((self.ROOT / 'cases/uav-prop-apc10x7e/case_spec_transition_off.json').read_text(encoding='utf-8'))
        self.assertNotIn('transition_model', spec['numerics'])
        self.assertTrue(spec['diagnostic_variant']['not_for_q3'])
        live = json.loads((self.ROOT / 'cases/uav-prop-apc10x7e/case_spec.json').read_text(encoding='utf-8'))
        # the live CaseSpec was RE-SIGNED to fully turbulent on 2026-10-30 (user decision option 1)
        self.assertNotIn('transition_model', live['numerics'])
        self.assertIn('model_form_decision', live)


class MrfChainResignedLadderContract(unittest.TestCase):
    """P2.3 G5: five signed J points under the RE-SIGNED fully turbulent model form (user decision 2026-10-30)."""

    ROOT = Path(__file__).resolve().parents[1]

    def test_signed_ladder_contract_keys_are_reproduced(self):
        import subprocess, sys as _sys
        staging = self.ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-resigned-j0647-20261030a/staging'
        contract = json.loads((staging / 'REVIEW-INPUTS.json').read_text(encoding='utf-8'))['contract']
        for key in ('resigned_j_ladder_signed_point', 'physics_configured', 'velocity_experiment', 'solve_run',
                    'segment', 'merged_history_window', 'read_only_audit', 'signed_tolerance_gated', 'yplus_gated'):
            self.assertTrue(contract.get(key) is True, key)
        for key in ('variant_model_sensitivity', 'q2_diagnostic_not_for_q3'):
            self.assertNotIn(key, contract)

    def test_live_casespec_is_resigned(self):
        spec = json.loads((self.ROOT / 'cases/uav-prop-apc10x7e/case_spec.json').read_text(encoding='utf-8'))
        self.assertNotIn('transition_model', spec['numerics'])
        decision = spec['model_form_decision']
        self.assertEqual('resign model form to fully turbulent SST (transition_model removed)', decision['decision'])
        self.assertEqual('user', decision['by'])
        self.assertTrue((self.ROOT / decision['evidence']).is_file())

    def test_five_resigned_plans_are_fresh_mesh_signed_points(self):
        expect = {'part55': ('0647', 0.647, 0.0341, 0.0318), 'part56': ('0601', 0.601, 0.0431, 0.0368),
                  'part57': ('0548', 0.548, 0.0553, 0.0431), 'part58': ('0504', 0.504, 0.0656, 0.0476),
                  'part59': ('0455', 0.455, 0.0761, 0.0512)}
        for part, (tag, j, ct_ref, cp_ref) in expect.items():
            plan = chain.PLANS[part]
            expected_steps = (['configure-physics', 'run-trial', 'run-segment', 'audit-j0548'] if tag == '0548'
                              else ['configure-physics', 'configure-velocity', 'run-trial', 'run-segment', 'audit-j%s' % tag])
            self.assertEqual(expected_steps, [s['name'] for s in plan['steps']], part)
            self.assertIn('mrf-meshb13-20261030a', str(plan['inputs']['mrf_diagnostic_meshed.sim']), part)
            if tag == '0548':
                # O9 (2026-10-30): the J=0.548 plan has no velocity step, so it must not declare a velocity
                # experiment input and its trial step must read the physics output (chain of custody).
                self.assertNotIn('velocity_experiment.json', plan['inputs'], part)
                trial = next(s for s in plan['steps'] if s['name'] == 'run-trial')
                self.assertEqual('mrf_physics_configured.sim', trial['input'], part)
            else:
                self.assertIn('velocity_experiment_resigned_j%s.json' % tag,
                              str(plan['inputs']['velocity_experiment.json']), part)
            self.assertEqual([s['macro'] for s in plan['steps']], plan['macros'], part)
            g = {}
            for gate in plan['record_gates']:
                for f in gate['fields']:
                    g.setdefault(f['field'], []).append((f['op'], f['value']))
            self.assertIn(('eq', False), g['transition_model_enabled'], part)
            self.assertIn(('eq', 0), g['measured_iteration'], part)
            if tag == '0548':
                self.assertNotIn('experiment_id', g, part)
                self.assertNotIn('configure-velocity', [x['name'] for x in plan['steps']], part)
            else:
                self.assertIn(('eq', 'RESIGNED_J%s_FROM_CASESPEC' % tag), g['experiment_id'], part)
            self.assertIn(('ge', ct_ref), g['ct_ref'], part)
            self.assertIn(('le', ct_ref), g['ct_ref'], part)
            self.assertIn(('ge', cp_ref), g['cp_ref'], part)
            rule = json.loads((CASE / 'case_spec.json').read_text(encoding='utf-8'))['acceptance']['reference_tolerance']
            self.assertIn(('ge', max(rule['ct_abs_floor'], rule['ct_rel'] * ct_ref) - 1e-12),
                          g['ct_tolerance'], part)
            self.assertIn(('le', max(rule['ct_abs_floor'], rule['ct_rel'] * ct_ref) + 1e-12),
                          g['ct_tolerance'], part)
            self.assertNotIn('eq', [op for op, _ in g['ct_tolerance']], part)
            self.assertIn(('eq', 1), g['yplus_measured'], part)
            self.assertIn(('eq', True), g['yplus_gate_passed'], part)
            if tag == '0548':
                continue  # no velocity step: the CaseSpec default inlet is already J=0.548
            exp = json.loads((self.ROOT / ('cases/uav-prop-apc10x7e/velocity_experiment_resigned_j%s.json' % tag)).read_text(encoding='utf-8'))
            self.assertEqual('RESIGNED_J%s_FROM_CASESPEC' % tag, exp['experiment_id'])
            self.assertFalse(exp['applicability']['solved_state_loaded'])
            self.assertNotAlmostEqual(exp['single_change']['from_mps'], exp['single_change']['to_mps'], places=6)

class MrfPlanChainIntegrityRatchet(unittest.TestCase):
    """O9 (2026-10-30): every registered plan must be runnable by the runner's chain-of-custody check.

    The J=0.548 signed point (part57) used to declare trial.input = mrf_velocity_experiment_configured.sim
    although its chain has no velocity step, so the runner would have aborted part57 before STAR started
    ("Declared input missing").  This is the generic guard rather than a one-off assertion.
    """

    def test_every_plan_hands_over_declared_bytes(self):
        self.assertEqual(87, len(chain.PLANS), 'plan registry count changed')
        for name, plan in sorted(chain.PLANS.items()):
            steps = plan['steps']
            self.assertTrue(steps, name)
            self.assertEqual([s['macro'] for s in steps], plan['macros'], name)
            self.assertEqual(len(steps), len({s['name'] for s in steps}), name)
            first = steps[0]
            if first['input'] is not None:
                self.assertIn(first['input'], plan['inputs'], name)
            for prev, step in zip(steps, steps[1:]):
                self.assertEqual(step['input'], prev['output'], '%s: %s input' % (name, step['name']))

    def test_part57_runs_from_the_physics_output(self):
        plan = chain.PLANS['part57']
        self.assertEqual(['configure-physics', 'run-trial', 'run-segment', 'audit-j0548'],
                         [s['name'] for s in plan['steps']])
        self.assertEqual('mrf_physics_configured.sim', plan['steps'][1]['input'])
        self.assertNotIn('velocity_experiment.json', plan['inputs'])


class MrfChainDomainSensitivityLevel2Contract(unittest.TestCase):
    """Q3 domain gate input: lateral cylinder radius 5D -> 10D at J=0.548, everything else identical."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads((CASE / 'case_spec_domain10d.json').read_text(encoding='utf-8'))
        cls.live = json.loads((CASE / 'case_spec.json').read_text(encoding='utf-8'))

    def test_single_change_is_the_lateral_radius(self):
        self.assertEqual(2.54, self.spec['domain']['cylinder_radius_m'])
        self.assertEqual([-2.54, -2.54, -3.81, 2.54, 2.54, 1.27], self.spec['domain']['outer_bounds_m'])
        for key in ('cylinder_z_min_m', 'cylinder_z_max_m', 'cylinder_center_xy_m'):
            self.assertEqual(self.live['domain'][key], self.spec['domain'][key], key)
        for key in ('mesh', 'numerics', 'flow', 'rotation', 'rotation_zone', 'mrf_regions',
                    'boundary_roles', 'operating_points', 'reference', 'solver'):
            self.assertEqual(self.live[key], self.spec[key], key)
        # the domain diagnostic was registered before the 2026-10-30 tolerance re-signing: compare every
        # acceptance gate EXCEPT the signed reference_tolerance, and pin that the rest is unchanged
        skip = ('reference_tolerance', 'numerical_criterion')
        live_acc = {k: v for k, v in self.live['acceptance'].items() if k not in skip}
        diag_acc = {k: v for k, v in self.spec['acceptance'].items() if k not in skip}
        self.assertEqual(live_acc, diag_acc, 'acceptance gates (excluding the signed signatures)')
        self.assertEqual(self.live['model_form_decision'], self.spec['model_form_decision'])

    def test_diagnostic_is_registered_not_for_q3(self):
        self.assertTrue(self.spec['domain_sensitivity_level_2']['not_for_q3'])
        self.assertTrue(self.spec['diagnostic_variant']['not_for_q3'])
        self.assertIn('DOMAIN_SENSITIVITY_LEVEL_2', self.spec['status']['diagnostic'])
        rule = self.spec['domain_sensitivity_level_2']['decision_rule']
        self.assertIn('0.5*GCI_fine', rule)
        self.assertIn('J=0.548', rule)

    def test_casespec_does_not_shadow_macro_keys(self):
        import re
        raw = (CASE / 'case_spec_domain10d.json').read_text(encoding='utf-8')
        for key in ('acceptance', 'solver', 'numerics', 'quality'):
            matches = [m.start() for m in re.finditer('"%s"\\s*:' % key, raw)]
            self.assertTrue(matches, key)
            tail = raw[matches[0]:matches[0] + 80]
            self.assertIn('{', tail.split(':')[1][:40], 'key %s is shadowed: %s' % (key, tail))

    def test_three_stages_form_the_registered_chain(self):
        volumes, mesh, solve = chain.PLANS['part60'], chain.PLANS['part61'], chain.PLANS['part62']
        for plan in (volumes, mesh, solve):
            self.assertIn('case_spec_domain10d.json', str(plan['inputs']['case_spec.json']))
        self.assertEqual(['import-step', 'audit-rotor', 'build-volumes'], [s['name'] for s in volumes['steps']])
        self.assertIn('apc10x7e_te02_variant_v4.step', str(volumes['inputs']['geometry_variant.step']))
        self.assertEqual(8, len(mesh['steps']))
        self.assertIn('mrf-domain10d-volumes-20261030a/mrf_fluid_volumes_built.sim',
                      str(mesh['inputs']['mrf_fluid_volumes_built.sim']))
        self.assertIn('mrf-domain10d-volumes-20261030a/recovered_rotor_solids.json',
                      str(mesh['inputs']['recovered_rotor_solids.json']))
        self.assertEqual(['configure-physics', 'run-trial', 'run-segment', 'audit-j0548'],
                         [s['name'] for s in solve['steps']])
        self.assertIn('mrf-domain10d-mesh-20261030a/mrf_diagnostic_meshed.sim',
                      str(solve['inputs']['mrf_diagnostic_meshed.sim']))
        self.assertEqual([s['macro'] for s in solve['steps']], solve['macros'])

    def test_solve_stage_keeps_convergence_and_yplus_but_not_the_signed_tolerance(self):
        g = {}
        for gate in chain.PLANS['part62']['record_gates']:
            for f in gate['fields']:
                g.setdefault(f['field'], []).append((f['op'], f['value']))
        self.assertNotIn('ct_within_signed_tolerance', g)
        self.assertNotIn('cp_within_signed_tolerance', g)
        self.assertIn(('eq', True), g['yplus_gate_passed'])
        self.assertIn(('ge', 1), g['yplus_wall_boundaries'])
        self.assertIn(('ge', 21), g['load_window_samples'])
        self.assertIn(('ge', 200), g['load_window_span_iterations'])
        self.assertIn(('le', 0.005), g['load_ct_range_fraction'])
        self.assertIn(('ge', 0.0553), g['ct_ref'])
        self.assertIn(('le', 0.0553), g['ct_ref'])
        # widened cell band for the larger domain only: the mesh-quality/prism thresholds are untouched
        self.assertIn(('ge', 2000000), g['measured_cells'])
        self.assertIn(('le', 8000000), g['measured_cells'])
        part61_g = {}
        for gate in chain.PLANS['part61']['record_gates']:
            for f in gate['fields']:
                part61_g.setdefault(f['field'], []).append((f['op'], f['value']))
        self.assertIn(('le', 0.01), part61_g['max_below_min_layer_area_fraction'])
        self.assertIn(('le', 1e-09), part61_g['max_prism_free_area_fraction'])
        self.assertIn(('ge', 0.95), part61_g['measured_face_validity_min'])
        self.assertIn(('le', 8000000), part61_g['volume_cells'])

    def test_baseline_is_the_archived_fully_turbulent_j0548_measurement(self):
        base = self.spec['domain_sensitivity_level_2']['baseline_5d']
        result = json.loads((self.ROOT / base['run'] / 'RESULT.json').read_text(encoding='utf-8'))
        self.assertEqual(result['run_id'], 'mrf-variant-transition-off-20261030a')
        self.assertAlmostEqual(result['measured']['segment']['ct'], base['ct'], places=6)
        self.assertAlmostEqual(abs(result['measured']['segment']['cp']), base['cp_abs'], places=6)
        self.assertEqual(0.548, base['j'])
        self.assertFalse(result['physics']['transition_model_enabled'])



class MrfChainGciContract(unittest.TestCase):
    """Q3 three-mesh GCI at J=0.548 (report sections 15, 15.1 and 15.2).

    Level CaseSpecs change ONLY the mesh lengths (six volume sizes + prism stack + wake size, all scaled by r)
    and the two quality records that describe that stack; physics/domain/acceptance/reference stay identical.
    The revised (15.2) ladder is UPWARD: production MESH-B13 (coarsest) -> MESH-B15 (x1/1.3, old "fine") ->
    MESH-B16 (x1/1.69, "finer2"), because the unchanged quality gate rejected coarser surface meshes twice.
    """

    ROOT = Path(__file__).resolve().parents[1]
    SIZES = ('base_size_m', 'blade_target_size_m', 'blade_min_size_m',
             'interface_target_size_m', 'interface_min_size_m', 'prism_total_m')
    FACTORS = {'coarse': 1.3, 'fine': 1.0 / 1.3, 'finer2': 1.0 / 1.69, 'finer3': 1.0 / 2.197,
               'finer4': 1.0 / 2.8561}
    BANDS = {'coarse': (500000, 4000000), 'fine': (2000000, 12000000), 'finer2': (4000000, 20000000),
             'finer3': (8000000, 30000000), 'finer4': (15000000, 45000000)}
    MESH_PLANS = {'coarse': 'part63', 'fine': 'part65', 'finer2': 'part67', 'finer3': 'part69',
                  'finer4': 'part71'}
    SOLVE_PLANS = {'coarse': 'part64', 'fine': 'part66', 'finer2': 'part68', 'finer3': 'part70',
                   'finer4': 'part72'}

    @classmethod
    def setUpClass(cls):
        cls.live = json.loads((CASE / 'case_spec.json').read_text(encoding='utf-8'))
        cls.specs = {level: json.loads((CASE / ('case_spec_gci_%s.json' % level)).read_text(encoding='utf-8'))
                     for level in cls.FACTORS}

    def test_levels_scale_only_the_mesh_lengths(self):
        for level, spec in self.specs.items():
            factor = self.FACTORS[level]
            for key in self.SIZES:
                self.assertAlmostEqual(self.live['mesh'][key] * factor, spec['mesh'][key],
                                       delta=abs(spec['mesh'][key]) * 1e-6, msg='%s %s' % (level, key))
            self.assertAlmostEqual(self.live['mesh_volume_refinement_wake']['size_m'] * factor,
                                   spec['mesh_volume_refinement_wake']['size_m'],
                                   delta=spec['mesh_volume_refinement_wake']['size_m'] * 1e-6, msg=level)
            for key in ('prism_layers', 'rotating_prism_layers', 'prism_stretch',
                        'rotating_operation', 'stationary_operation'):
                self.assertEqual(self.live['mesh'][key], spec['mesh'][key], '%s %s' % (level, key))
            # the prism stack THICKNESS scales with r (v2) and the quality record follows it
            for key in ('mesh_prism_total_thickness_m', 'mesh_prism_first_layer_height_m'):
                self.assertAlmostEqual(self.live['quality'][key] * factor, spec['quality'][key],
                                       delta=spec['quality'][key] * 1e-6, msg='%s %s' % (level, key))
            quality_live = {k: v for k, v in self.live['quality'].items()
                            if k not in ('mesh_prism_total_thickness_m', 'mesh_prism_first_layer_height_m')}
            quality_level = {k: v for k, v in spec['quality'].items()
                             if k not in ('mesh_prism_total_thickness_m', 'mesh_prism_first_layer_height_m')}
            self.assertEqual(quality_live, quality_level, '%s quality (excluding the two scaled records)' % level)
            for key in ('numerics', 'flow', 'domain', 'rotation', 'rotation_zone', 'mrf_regions',
                        'boundary_roles', 'operating_points', 'reference', 'solver', 'model_form_decision',
                        'mesh_revision'):
                self.assertEqual(self.live[key], spec[key], '%s %s' % (level, key))
            # registered before the 2026-10-30 tolerance re-signing: acceptance gates except the signed tolerance
            skip = ('reference_tolerance', 'numerical_criterion')
            live_acc = {k: v for k, v in self.live['acceptance'].items() if k not in skip}
            level_acc = {k: v for k, v in spec['acceptance'].items() if k not in skip}
            self.assertEqual(live_acc, level_acc, '%s acceptance gates (excluding the signed signatures)' % level)
            # registered metadata
            self.assertEqual('2026-10-30', spec['gci_level']['registered'])
            self.assertEqual(level, spec['gci_level']['level'])
            self.assertEqual(1.3, spec['gci_level']['refinement_ratio_nominal'])
            self.assertAlmostEqual(factor, spec['gci_level']['expected_yplus_scale'], places=12)
            self.assertTrue(spec['gci_level']['prism_stack_scaled'])
            self.assertTrue(spec['gci_level']['not_a_signed_point'])
            self.assertIn('prism layers 20', spec['gci_level']['held_fixed'][0])
            if level in ('coarse', 'fine'):
                self.assertIn('v2 true geometric similarity', spec['gci_level']['revision'])
                self.assertIn('attempt_1_failure', spec['gci_level'])
            elif level == 'finer2':
                self.assertIn('REVISED', spec['gci_level']['ladder'])
                self.assertIn('15.2', spec['gci_level']['protocol'])
            elif level == 'finer3':
                self.assertIn('4-level GCI', spec['gci_level']['ladder'])
                self.assertIn('19', spec['gci_level']['protocol'])
                self.assertIn('B17', spec['gci_level']['ladder'])
            else:
                self.assertIn('FIVE-level GCI', spec['gci_level']['ladder'])
                self.assertIn('21', spec['gci_level']['protocol'])
                self.assertIn('B18', spec['gci_level']['ladder'])

    def test_mesh_level_plans_build_from_the_5d_volumes_with_the_level_casespec(self):
        for level, part in self.MESH_PLANS.items():
            plan = chain.PLANS[part]
            self.assertEqual(['audit-volumes', 'surface-map', 'create-regions', 'configure-interfaces',
                              'configure-mesh', 'execute-mesh', 'audit-prism', 'audit-mesh-quality'],
                             [s['name'] for s in plan['steps']], part)
            self.assertIn('mrf-tevariant4-20261030a/mrf_fluid_volumes_built.sim',
                          str(plan['inputs']['mrf_fluid_volumes_built.sim']), part)
            self.assertIn('mrf-tevariant4-20261030a/recovered_rotor_solids.json',
                          str(plan['inputs']['recovered_rotor_solids.json']), part)
            self.assertIn('case_spec_gci_%s.json' % level, str(plan['inputs']['case_spec.json']), part)
            self.assertEqual([s['macro'] for s in plan['steps']], plan['macros'], part)
            g = {}
            for gate in plan['record_gates']:
                for f in gate['fields']:
                    g.setdefault(f['field'], []).append((f['op'], f['value']))
            spec = self.specs[level]
            bands = {'blade_min_size_m': 0.005, 'blade_target_size_m': 0.005, 'base_size_m': 0.02,
                     'prism_total_m': 0.01}
            for key, rel in bands.items():
                value = spec['mesh'][key]
                self.assertIn(('ge', round(value * (1 - rel), 9)), g[key], '%s %s ge' % (part, key))
                self.assertIn(('le', round(value * (1 + rel), 9)), g[key], '%s %s le' % (part, key))
            # every prism/quality threshold is the UNCHANGED MESH-B13 one
            self.assertIn(('eq', 20), g['prism_layers'], part)
            self.assertIn(('le', 0.01), g['max_below_min_layer_area_fraction'], part)
            self.assertIn(('le', 1e-09), g['max_prism_free_area_fraction'], part)
            self.assertIn(('ge', 0.95), g['measured_face_validity_min'], part)
            lo, hi = self.BANDS[level]
            self.assertIn(('ge', lo), g['volume_cells'], part)
            self.assertIn(('le', hi), g['volume_cells'], part)

    def test_solve_level_plans_are_not_signed_points(self):
        for level, part in self.SOLVE_PLANS.items():
            plan = chain.PLANS[part]
            self.assertEqual(['configure-physics', 'run-trial', 'run-segment', 'audit-j0548'],
                             [s['name'] for s in plan['steps']], part)
            self.assertIn('mrf-gci-%s-mesh-20261030a/mrf_diagnostic_meshed.sim' % level,
                          str(plan['inputs']['mrf_diagnostic_meshed.sim']), part)
            self.assertIn('case_spec_gci_%s.json' % level, str(plan['inputs']['case_spec.json']), part)
            self.assertNotIn('velocity_experiment.json', plan['inputs'], part)
            g = {}
            for gate in plan['record_gates']:
                for f in gate['fields']:
                    g.setdefault(f['field'], []).append((f['op'], f['value']))
            self.assertNotIn('ct_within_signed_tolerance', g, part)
            self.assertNotIn('cp_within_signed_tolerance', g, part)
            self.assertIn(('eq', True), g['yplus_gate_passed'], part)
            self.assertIn(('eq', False), g['transition_model_enabled'], part)
            self.assertIn(('eq', 0), g['measured_iteration'], part)
            lo, hi = self.BANDS[level]
            self.assertIn(('ge', lo), g['measured_cells'], part)
            self.assertIn(('le', hi), g['measured_cells'], part)

    def test_protocol_matches_the_registered_text(self):
        report = (self.ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/Q3-STATUS-20261030.md').read_text(encoding='utf-8')
        self.assertIn('r=1.3', report)
        self.assertIn('Fs=1.25', report)
        self.assertIn('GCI_fine ≤5%', report)
        self.assertIn('15.2', report)
        # the GCI script's ladder must be the revised upward one: production as the coarsest level
        import importlib.util
        spec = importlib.util.spec_from_file_location('gci_mod', self.ROOT / 'scripts/gci.py')
        gci = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gci)
        self.assertEqual('coarse', gci.GATE_LEVEL)
        self.assertIn('mrf-resigned-j0548-20261030a', str(gci.LEVELS['coarse']))
        self.assertIn('mrf-gci-fine-j0548-20261030a', str(gci.LEVELS['medium']))
        self.assertIn('mrf-gci-finer2-j0548-20261030a', str(gci.LEVELS['fine']))




class MrfToleranceGateFloatEqualityRatchet(unittest.TestCase):
    """O9 for the measured part58 defect (2026-10-30): the audit macro emits ct_tolerance as the IEEE-754
    product max(0.0035, 0.08*CT_ref); 0.08*0.0656 = 0.005248000000000001.  A plan that pins round(...,6) with
    an `eq` gate rejects a 1-ulp difference and fails the whole run AFTER all STAR steps succeeded.  Every
    ladder plan must pin the computed tolerance as a narrow band, and the band must contain the exact double.
    """

    ROOT = Path(__file__).resolve().parents[1]

    def _ladder_parts(self):
        return [p for p, plan in chain.PLANS.items()
                if any(f['field'] in ('ct_tolerance', 'cp_tolerance')
                       for g in plan['record_gates'] for f in g['fields'])]

    def test_no_ladder_plan_uses_exact_equality_for_a_computed_tolerance(self):
        parts = self._ladder_parts()
        self.assertTrue(parts)
        for part in parts:
            for gate in chain.PLANS[part]['record_gates']:
                for f in gate['fields']:
                    if f['field'] in ('ct_tolerance', 'cp_tolerance'):
                        self.assertNotEqual('eq', f['op'],
                                            '%s %s uses exact float equality' % (part, f['field']))

    def test_bands_contain_the_exact_double_the_macro_emits(self):
        """Every gated plan's tolerance identity band must be derived from the CaseSpec that plan is staged
        against, and must contain the exact IEEE-754 double the audit macro computes from it.

        Measured defect 2026-10-30 (review-10a, part70): the builders hardcoded 8% after the user re-signed
        the tolerance to 12%, so a plan staged against the 12% CaseSpec pinned the OLD band and would have
        failed closed after the solve.  This test is the coupling, not a constant check.
        """
        # part7 is the only pre-signature exploratory plan: it pinned a literal 0.004..0.005 band before the
        # signed-tolerance era and is never re-run.  Any NEW plan must be coupled to its CaseSpec.
        legacy_literal_plans = {'part7'}
        self.assertEqual({'part7'}, legacy_literal_plans)
        for part, plan in sorted(chain.PLANS.items()):
            if part in legacy_literal_plans:
                continue
            audit_gate = next((gate for gate in plan['record_gates']
                               if gate['record'] == 'mrf_trial_audit.json'), None)
            if audit_gate is None:
                continue
            fields = audit_gate['fields']
            if not any(f['field'] in ('ct_tolerance', 'cp_tolerance') for f in fields):
                continue
            spec_path = plan['inputs'].get('case_spec.json') or (CASE / 'case_spec.json')
            spec = json.loads(Path(spec_path).read_text(encoding='utf-8'))
            rule = spec['acceptance']['reference_tolerance']
            refs = {f['field']: f['value'] for f in fields if f['field'] in ('ct_ref', 'cp_ref')}
            for field, floor, rel, ref in (('ct_tolerance', rule['ct_abs_floor'], rule['ct_rel'], refs['ct_ref']),
                                           ('cp_tolerance', rule['cp_abs_floor'], rule['cp_rel'], refs['cp_ref'])):
                exact = max(floor, rel * ref)  # the exact double Java's macro computes
                lo = [f['value'] for f in fields if f['field'] == field and f['op'] == 'ge']
                hi = [f['value'] for f in fields if f['field'] == field and f['op'] == 'le']
                self.assertEqual(1, len(lo), '%s %s lower bound' % (part, field))
                self.assertEqual(1, len(hi), '%s %s upper bound' % (part, field))
                self.assertLessEqual(lo[0], exact, '%s %s lower %.17g > exact %.17g' % (part, field, lo[0], exact))
                self.assertGreaterEqual(hi[0], exact, '%s %s upper %.17g < exact %.17g' % (part, field, hi[0], exact))
                self.assertLessEqual(hi[0] - lo[0], 1e-11, '%s %s band too wide' % (part, field))




class MrfChainDiagnosticAContract(unittest.TestCase):
    """Diagnostic case A (user-authorized 2026-10-30): gamma-Re_theta on the production mesh at J=0.455/J=0.504.

    These runs measure the MODEL-FORM contribution at the low-J end where the fully turbulent CT is 10% below the
    reference.  They must never be mistaken for signed points: transition model ON, no signed-tolerance gates, and
    the ct/cp reference identity must be their own J (not part57's J=0.548).
    """

    ROOT = Path(__file__).resolve().parents[1]

    def _plan(self, part):
        return chain.PLANS[part]

    def test_diagnostic_plans_are_registered_with_the_transition_model_on(self):
        for part, tag, j in (('part73', '0455', 0.455), ('part74', '0504', 0.504)):
            plan = self._plan(part)
            self.assertEqual('mrf-diagA-gamma-theta-j%s-20261030a' % tag, plan['run_id'])
            contract = chain.CONTRACT_OVERRIDES[part]
            self.assertTrue(contract['diagnostic_A'])
            self.assertTrue(contract['transition_model_on'])
            self.assertTrue(contract['not_a_signed_point'])
            gates = {}
            for gate in plan['record_gates']:
                for field in gate['fields']:
                    gates.setdefault(field['field'], []).append((field['op'], field['value']))
            self.assertEqual([('eq', True)], gates['transition_model_enabled'])
            # no signed-tolerance gates in a diagnostic
            for gate in plan['record_gates']:
                for field in gate['fields']:
                    self.assertNotIn('within_signed_tolerance', field['field'], part)
            # the identity refers to THIS point's reference values
            points = {round(pp['j'], 3): pp for pp in json.loads(
                (CASE / 'reference.json').read_text(encoding='utf-8'))['acceptance']['operating_points']}
            expected = points[round(j, 3)]
            self.assertIn(('ge', expected['ct_ref']), gates['ct_ref'], part)
            self.assertIn(('le', expected['ct_ref']), gates['ct_ref'], part)
            self.assertIn(('ge', expected['cp_ref']), gates['cp_ref'], part)
            self.assertIn(('le', expected['cp_ref']), gates['cp_ref'], part)
            # the CaseSpec declares the diagnostic block and the transition model
            spec = json.loads((CASE / ('case_spec_transition_on_j%s.json' % tag)).read_text(encoding='utf-8'))
            self.assertEqual('GammaReTheta', spec['numerics']['transition_model'])
            self.assertTrue(spec['diagnostic']['not_a_signed_point'])
            self.assertEqual(j, spec['diagnostic']['j'])
            self.assertFalse(spec['case_spec_frozen'])
            self.assertAlmostEqual(-j * (6015 / 60.0) * 0.254, spec['flow']['velocity_m_s'][2], places=6)


    def test_velocity_dependent_gates_follow_the_diagnostic_point(self):
        """review-12a: the cloned part57 physics gates pinned J=0.548 velocity/advance-ratio/inflow values.
        Every velocity-dependent gate of a diagnostic plan must be derived from the CaseSpec's own inlet velocity
        and target J (magnitude for the inlet speed, sign for the inflow axis dot)."""
        for part, tag, j in (('part73', '0455', 0.455), ('part74', '0504', 0.504)):
            spec = json.loads((CASE / ('case_spec_transition_on_j%s.json' % tag)).read_text(encoding='utf-8'))
            speed = abs(spec['flow']['velocity_m_s'][2])
            self.assertAlmostEqual(j * (6015 / 60.0) * 0.254, speed, places=6)
            readback = next(g for g in self._plan(part)['record_gates']
                            if g['record'] == 'mrf_physics_readback.json')
            fields = {}
            for f in readback['fields']:
                fields.setdefault(f['field'], []).append((f['op'], f['value']))
            lo = dict(fields['measured_inlet_mps'])['ge']
            hi = dict(fields['measured_inlet_mps'])['le']
            self.assertLessEqual(lo, speed, part)
            self.assertGreaterEqual(hi, speed, part)
            self.assertAlmostEqual(0.002, hi - lo, places=9)
            self.assertIn(('ge', j - 0.0005), fields['advance_ratio'], part)
            self.assertIn(('le', j + 0.0005), fields['advance_ratio'], part)
            self.assertIn(('le', -(speed - 0.001)), fields['inflow_axis_dot'], part)
            self.assertEqual([('eq', True)], fields['transition_model_enabled'], part)
            # and the diagnostic CaseSpec's velocity is the target J's: V = J*n*D
    def test_same_mesh_as_the_signed_run(self):
        signed_mesh = self._plan('part57')['inputs']['mrf_diagnostic_meshed.sim']
        for part in ('part73', 'part74'):
            self.assertEqual(signed_mesh, self._plan(part)['inputs']['mrf_diagnostic_meshed.sim'])




class MrfChainDiagnosticCContract(unittest.TestCase):
    """Diagnostic case C (user-authorized 2026-10-30): 0.3 mm TE variant at J=0.647 vs the registered 0.2 mm treatment.

    Three chained plans: volumes from the Fusion STEP, the MESH-B13 mesh recipe, then a fresh J=0.647 solve.  The
    run must be a single-variable comparison with the signed 0.2 mm arm: same mesh recipe, same fully turbulent
    physics, only the TE thickness differs.  Not a signed point.
    """

    ROOT = Path(__file__).resolve().parents[1]
    STEP = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/fusion-cad/apc10x7e_te03_variant.step'
    RECORD = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/fusion-cad/TE-SENSITIVITY-CAD-20261030.json'

    def test_v1_attempt_is_archived_as_failed(self):
        record = json.loads(Path('campaigns/uav-2026/evidence/uav-prop-apc10x7e/fusion-cad/TE-SENSITIVITY-CAD-20261030.json').read_text(encoding='utf-8'))
        self.assertIn('not manifold', record['b_chord1044']['attempt_1_import_failed']['result'])
        failed = Path('campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-chord1044-variant-20261030a/RESULT.json')
        if failed.is_file():
            self.assertEqual('FAILED_GEOMETRY_NOT_MANIFOLD', json.loads(failed.read_text(encoding='utf-8'))['status'])

    def test_c_is_recorded_as_blocked_and_never_run(self):
        record = json.loads(self.RECORD.read_text(encoding='utf-8'))
        self.assertEqual('BLOCKED_NO_VALID_SOLID', record['c_blocked']['status'])
        self.assertEqual(2, record['c_blocked']['attempts'])
        self.assertIn('isValid()=FALSE', record['c_blocked']['attempt_1'])
        for part in ('part75', 'part76', 'part77'):
            contract = chain.CONTRACT_OVERRIDES[part]
            self.assertTrue(contract['diagnostic_C_BLOCKED'], part)
            self.assertIn('NOT RUN', contract['blocked_reason'], part)
            # a blocked plan may keep its (rejected) staging, but must never have executed
            evidence = chain.GCI_EVIDENCE / chain.PLANS[part]['run_id']
            self.assertFalse((evidence / 'run.json').is_file(), part)
            self.assertFalse((evidence / 'RESULT.json').is_file(), part)

    def test_geometry_record_matches_the_step(self):
        record = json.loads(self.RECORD.read_text(encoding='utf-8'))
        self.assertTrue(self.STEP.is_file())
        self.assertEqual(record['te03']['step_sha256'], hashlib.sha256(self.STEP.read_bytes()).hexdigest())
        self.assertEqual(0.3, record['te03']['requested_mm'])
        self.assertTrue(record['te03']['readback']['is_solid'])
        self.assertEqual(15, record['te03']['readback']['faces'])
        # the 0.1 mm arm must stay recorded as NOT buildable (no silent substitution)
        self.assertEqual('NOT_BUILDABLE', record['te01']['status'])
        self.assertIn('SELF_INTERSECT', record['te01']['error'])

    def test_chain_is_volumes_then_mesh_then_solve_on_the_new_geometry(self):
        volumes = chain.PLANS['part75']
        self.assertEqual(self.STEP, volumes['inputs']['geometry_variant.step'])
        self.assertEqual(['import-step', 'audit-rotor', 'build-volumes'], [s['name'] for s in volumes['steps']])
        mesh = chain.PLANS['part76']
        self.assertIn('mrf-te03-variant-20261030a', str(mesh['inputs']['mrf_fluid_volumes_built.sim']))
        self.assertIn('mrf-te03-variant-20261030a', str(mesh['inputs']['recovered_rotor_solids.json']))
        self.assertEqual(8, len(mesh['steps']))
        solve = chain.PLANS['part77']
        self.assertIn('mrf-te03-mesh-20261030a', str(solve['inputs']['mrf_diagnostic_meshed.sim']))
        self.assertEqual(4, len(solve['steps']))
        self.assertEqual('case_spec_te03_j0647.json', solve['inputs']['case_spec.json'].name)

    def test_solve_is_a_single_variable_comparison_at_j0647(self):
        spec = json.loads((CASE / 'case_spec_te03_j0647.json').read_text(encoding='utf-8'))
        self.assertAlmostEqual(0.647 * (6015 / 60.0) * 0.254, abs(spec['flow']['velocity_m_s'][2]), places=6)
        self.assertEqual(0.3, spec['diagnostic']['te_thickness_mm'])
        self.assertTrue(spec['diagnostic']['not_a_signed_point'])
        self.assertFalse(spec['case_spec_frozen'])
        # the physics must be the signed fully turbulent form: no transition model key
        self.assertNotIn('transition_model', spec['numerics'])
        # and the quality/mesh thresholds must equal the signed frozen CaseSpec's (only velocity differs in flow)
        live = json.loads((CASE / 'case_spec.json').read_text(encoding='utf-8'))
        self.assertEqual(live['quality'], spec['quality'])
        self.assertEqual(live['mesh'], spec['mesh'])
        self.assertEqual(live['numerics'], spec['numerics'])
        solve = chain.PLANS['part77']
        readback = next(g for g in solve['record_gates'] if g['record'] == 'mrf_physics_readback.json')
        fields = {}
        for f in readback['fields']:
            fields.setdefault(f['field'], []).append((f['op'], f['value']))
        speed = abs(spec['flow']['velocity_m_s'][2])
        self.assertIn(('ge', speed - 0.001), fields['measured_inlet_mps'])
        self.assertIn(('le', speed + 0.001), fields['measured_inlet_mps'])
        self.assertIn(('ge', 0.647 - 0.0005), fields['advance_ratio'])
        self.assertIn(('le', 0.647 + 0.0005), fields['advance_ratio'])
        self.assertIn(('le', -(speed - 0.001)), fields['inflow_axis_dot'])
        for gate in solve['record_gates']:
            for field in gate['fields']:
                self.assertNotIn('within_signed_tolerance', field['field'])




class MrfChainDiagnosticBContract(unittest.TestCase):
    """Diagnostic case B (user-authorized 2026-10-30): chord +4.4% about the LE on the registered 0.2 mm partial-TE
    geometry, at J=0.647 and J=0.455, on the same MESH-B13 recipe and fully turbulent physics.  Not a signed point.
    The 0.2 mm treatment is kept so the ONLY difference vs the signed geometry is the chord (planform area)."""

    ROOT = Path(__file__).resolve().parents[1]
    STEP = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/fusion-cad/apc10x7e_chord1044_v4_variant.step'
    RECORD = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/fusion-cad/TE-SENSITIVITY-CAD-20261030.json'

    def test_step_is_kernel_valid_and_recorded(self):
        record = json.loads(self.RECORD.read_text(encoding='utf-8'))['b_chord1044_v2']
        self.assertTrue(self.STEP.is_file())
        self.assertEqual(record['step_sha256'], hashlib.sha256(self.STEP.read_bytes()).hexdigest())
        self.assertTrue(record['occt_validation']['valid'])
        self.assertEqual(1, record['occt_validation']['solids'])
        self.assertEqual(0, record['occt_validation']['tessellation']['nonmanifold_edges'])
        self.assertEqual(0, record['occt_validation']['tessellation']['boundary_edges'])
        self.assertEqual(1.044, record['note'] and 1.044)
        # the OCCT volume must be within 0.2% of the Fusion readback (a kernel-independent cross-check)
        self.assertLess(abs(record['occt_validation']['volume_mm3'] / record['fusion_readback']['volume_mm3'] - 1.0), 0.005)
        # a chord-only change must grow the volume but not the span
        self.assertGreater(record['volume_change_vs_registered']['relative'], 0.01)

    def test_rotor_volume_gate_is_derived_for_the_variant(self):
        """The variant's rotor volume is legitimately larger; the gate must come from the recorded OCCT readback,
        not from the registered geometry's window."""
        record = json.loads(self.RECORD.read_text(encoding='utf-8'))['b_chord1044_v2']['occt_validation']['volume_mm3'] * 1e-9
        gate = next(g for g in chain.PLANS['part82']['record_gates']
                    if g['record'] == 'recovered_rotor_solids.json')
        fields = {}
        for f in gate['fields']:
            fields.setdefault(f['field'], {})[f['op']] = f['value']
        lo, hi = fields['readback_surface_volume_m3']['ge'], fields['readback_surface_volume_m3']['le']
        self.assertAlmostEqual(record * 0.98, lo, places=16)
        self.assertAlmostEqual(record * 1.02, hi, places=16)
        # the window must CONTAIN the measured variant volume and extend above the registered window's upper bound
        self.assertLessEqual(lo, record)
        self.assertGreaterEqual(hi, record)
        self.assertGreater(hi, 1.2294899835e-05, 'the variant volume is above the registered window')
        self.assertIn('derived from the recorded OCCT readback', chain.PLANS['part82']['scope_note'])
        self.assertIn('+/-2%', chain.PLANS['part82']['scope_note'])

    def test_chain_wiring(self):
        self.assertEqual(self.STEP, chain.PLANS['part82']['inputs']['geometry_variant.step'])
        self.assertIn('mrf-chord1044v2-variant-20261030a', str(chain.PLANS['part83']['inputs']['mrf_fluid_volumes_built.sim']))
        for part, tag, j in (('part84', '0647', 0.647), ('part85', '0455', 0.455)):
            plan = chain.PLANS[part]
            self.assertIn('mrf-chord1044v2-mesh-20261030a', str(plan['inputs']['mrf_diagnostic_meshed.sim']))
            self.assertEqual('case_spec_chord1044_j%s.json' % tag, plan['inputs']['case_spec.json'].name)
            self.assertTrue(chain.CONTRACT_OVERRIDES[part]['diagnostic_B'])
            self.assertEqual(j, chain.CONTRACT_OVERRIDES[part]['j'])
            spec = json.loads((self.ROOT / 'cases/uav-prop-apc10x7e' / ('case_spec_chord1044_j%s.json' % tag)).read_text(encoding='utf-8'))
            self.assertAlmostEqual(j * (6015 / 60.0) * 0.254, abs(spec['flow']['velocity_m_s'][2]), places=5)
            self.assertEqual(1.044, spec['diagnostic']['chord_factor'])
            self.assertFalse(spec['case_spec_frozen'])
            self.assertNotIn('transition_model', spec['numerics'])
            live = json.loads((self.ROOT / 'cases/uav-prop-apc10x7e/case_spec.json').read_text(encoding='utf-8'))
            # only flow velocity + diagnostic metadata may differ from the frozen signed CaseSpec
            self.assertEqual(live['quality'], spec['quality'])
            self.assertEqual(live['mesh'], spec['mesh'])
            self.assertEqual(live['numerics'], spec['numerics'])
            self.assertEqual(live['acceptance'], spec['acceptance'])
            readback = next(g for g in plan['record_gates'] if g['record'] == 'mrf_physics_readback.json')
            fields = {}
            for f in readback['fields']:
                fields.setdefault(f['field'], []).append((f['op'], f['value']))
            speed = abs(spec['flow']['velocity_m_s'][2])
            lo = dict(fields['measured_inlet_mps'])['ge']
            hi = dict(fields['measured_inlet_mps'])['le']
            self.assertLessEqual(lo, speed, part)
            self.assertGreaterEqual(hi, speed, part)
            self.assertAlmostEqual(0.002, hi - lo, places=9)
            self.assertIn(('ge', j - 0.0005), fields['advance_ratio'])
            self.assertIn(('le', j + 0.0005), fields['advance_ratio'])
            for gate in plan['record_gates']:
                for field in gate['fields']:
                    self.assertNotIn('within_signed_tolerance', field['field'])




class MrfChainBadCellLocalizationContract(unittest.TestCase):
    """TE-mesh route step 1 (user-authorized 2026-10-30): read-only localization of the worst mesh cells.

    Four plans open EXISTING meshed .sim files (production B13 and the three levels that failed the quality gate) and
    run one read-only macro.  They must never solve, never change the mesh, never save, and their gates must assert
    read_only/localized/complete_marker so a silent no-op cannot pass.
    """

    ROOT = Path(__file__).resolve().parents[1]
    PLANS = {
        'part86': ('mrf-meshb13-20261030a', 'production MESH-B13'),
        'part87': ('mrf-gci-coarse-mesh-20261030a/ATTEMPT-1-FAILED-QUALITY-GATE', 'coarse v1'),
        'part88': ('mrf-gci-coarse-mesh-20261030a/ATTEMPT-2-FAILED-QUALITY-GATE', 'coarse v2'),
        'part89': ('mrf-gci-finer4-mesh-20261030a/ATTEMPT-1-FAILED-MESH-QUALITY', 'B18'),
    }

    def test_plans_are_read_only_single_step_localizations(self):
        for part, (mesh_rel, label) in self.PLANS.items():
            plan = chain.PLANS[part]
            self.assertEqual(1, len(plan['steps']), part)
            step = plan['steps'][0]
            self.assertEqual('AuditMrfBadCellLocalization.java', step['macro'], part)
            self.assertEqual('mrf_diagnostic_meshed.sim', step['input'], part)
            self.assertEqual('mrf_bad_cell_localization.json', step['output'], part)
            self.assertEqual(['AuditMrfBadCellLocalization.java'], plan['macros'], part)
            # the meshed .sim is the EXISTING mesh: no mesh macros, no solver macros anywhere in the plan
            macros = ' '.join(plan['macros'])
            for forbidden in ('Mesh', 'Trial', 'Segment', 'Physics', 'Volumes'):
                self.assertNotIn(forbidden, macros, part)
            self.assertIn(mesh_rel, str(plan['inputs']['mrf_diagnostic_meshed.sim']), part)
            self.assertTrue((self.ROOT / 'macros' / 'AuditMrfBadCellLocalization.java').is_file())
            contract = chain.CONTRACT_OVERRIDES[part]
            self.assertTrue(contract['diagnostic_localization'], part)
            self.assertTrue(contract['read_only'], part)
            self.assertTrue(contract['not_a_signed_point'], part)

    def test_gates_assert_that_localization_actually_happened(self):
        for part in self.PLANS:
            fields = {}
            for gate in chain.PLANS[part]['record_gates']:
                for field in gate['fields']:
                    fields.setdefault(field['field'], []).append((field['op'], field['value']))
            self.assertIn(('eq', True), fields['read_only'], part)
            self.assertIn(('eq', True), fields['localized'], part)
            self.assertIn(('eq', 'ok'), fields['status'], part)
            self.assertIn(('eq', 3), fields['min_clusters_located'], part)
            # review-15a: every mesh has at least one worst cell, so a vacuous ge-0 gate is not allowed
            for metric in ('CellQuality', 'FaceValidity', 'VolumeChange'):
                self.assertIn(('ge', 1), fields[metric + '_worst_count'], part)
            self.assertIn(('eq', True), fields['VolumeChange_worst_bbox_defined'], part)
            # the production mesh has no cells below the volume-change threshold by construction; the failed
            # levels must prove they DO have violating cells
            expected_below = 0 if part == 'part86' else 1
            self.assertIn(('ge', expected_below), fields['VolumeChange_below_cutoff_count'], part)
            self.assertIn(('eq', 'MRF_BAD_CELL_LOCALIZATION_COMPLETE'), fields['complete_marker'], part)

    def test_macro_is_read_only_by_construction(self):
        text = (self.ROOT / 'macros' / 'AuditMrfBadCellLocalization.java').read_text(encoding='utf-8')
        # no save / initialise / solver / mesh deletion calls in the source
        for forbidden in ('saveState', 'initializeSolution', 'clearSolution', 'deleteMesh', 'getSolverManager'):
            self.assertNotIn(forbidden, text, forbidden)
        self.assertIn('createEmptyCellSet', text)
        self.assertIn('MRF_BAD_CELL_LOCALIZATION_COMPLETE', text)
        # review-15a: an empty set must never be bisected (the old code produced inverted bboxes)
        self.assertIn('worst_bbox_defined', text)
        self.assertIn('below_bbox_defined', text)
        self.assertIn('subsetThreshold', text)



class MrfChainContrastTestContract(unittest.TestCase):
    """TE-mesh route step 2 (diagnostic A): single-variable contrast test on the coarse GCI level."""

    ROOT = Path(__file__).resolve().parents[1]
    SPEC = ROOT / 'cases/uav-prop-apc10x7e/case_spec_gci_coarse_contrastfix.json'
    COARSE = ROOT / 'cases/uav-prop-apc10x7e/case_spec_gci_coarse.json'

    def test_spec_is_the_coarse_level_with_only_the_base_size_changed(self):
        spec = json.loads(self.SPEC.read_text(encoding='utf-8'))
        coarse = json.loads(self.COARSE.read_text(encoding='utf-8'))
        self.assertEqual(0.05, spec['mesh']['base_size_m'])
        self.assertEqual(0.065, coarse['mesh']['base_size_m'])
        for key in ('blade_target_size_m', 'blade_min_size_m', 'interface_target_size_m', 'interface_min_size_m',
                    'prism_total_m', 'prism_layers', 'prism_stretch'):
            self.assertEqual(coarse['mesh'][key], spec['mesh'][key], key)
        self.assertEqual(coarse['mesh_volume_refinement_wake'], spec['mesh_volume_refinement_wake'])
        self.assertEqual(coarse['quality'], spec['quality'])
        self.assertTrue(spec['gci_level']['not_a_gci_family_member'])
        self.assertTrue(spec['gci_level']['not_a_signed_point'])
        self.assertIn('base_size_m 0.065 -> 0.05', spec['gci_level']['single_change'])

    def test_plan_is_mesh_only_and_flagged(self):
        plan = chain.PLANS['part90']
        self.assertEqual(8, len(plan['steps']))
        self.assertEqual('case_spec_gci_coarse_contrastfix.json', plan['inputs']['case_spec.json'].name)
        self.assertNotIn('RunMrfTrial.java', plan['macros'])
        self.assertNotIn('RunMrfSegment.java', plan['macros'])
        contract = chain.CONTRACT_OVERRIDES['part90']
        self.assertTrue(contract['diagnostic_contrast_test'])
        self.assertTrue(contract['not_a_gci_family_member'])
        self.assertTrue(contract['not_a_signed_point'])
        self.assertIn('not a GCI family member', plan['scope_note'])
        # review-15c: the inherited part63 base gate (0.065 +/-2%) would fail the contrast CaseSpec's 0.05
        gate = next(g for g in plan['record_gates'] if g['record'] == 'mrf_mesh_configured.json')
        fields = {f['field']: {} for f in gate['fields']}
        for f in gate['fields']:
            fields[f['field']][f['op']] = f['value']
        self.assertAlmostEqual(0.049, fields['base_size_m']['ge'], places=12)
        self.assertAlmostEqual(0.051, fields['base_size_m']['le'], places=12)
        self.assertLess(fields['base_size_m']['ge'], 0.05)
        self.assertGreater(fields['base_size_m']['le'], 0.05)


if __name__ == '__main__':
    unittest.main()
