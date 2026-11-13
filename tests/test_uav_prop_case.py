"""Phase 2 propeller case: CaseSpec/reference/compile/campaign consistency.

This test is the executable ratchet for the P2.1 round. It fails if:
  * the new probe CaseSpec stops validating against scripts/casespec.py,
  * a macro-visible CaseSpec key appears more than once (first-regex readers would silently
    pick the wrong value - the exact defect class tests/test_case_spec_macro_key_uniqueness.py
    documents for the scenario-1 study specs),
  * reference CT/CP/eta no longer trace to the saved UIUC source file, or the saved source
    bytes no longer match the recorded SHA (a silent reference edit),
  * the APC PE0 geometry bytes drift from the recorded SHA,
  * the macro source is newer than its compile record (i.e. the compiled bytes are stale),
  * the campaign state starts claiming Q3 without a signed tolerance, approved budget and a result record.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'cases' / 'uav-prop-apc10x7e'
SPEC = CASE / 'case_spec.json'
REFERENCE = CASE / 'reference.json'
STATUS = ROOT / 'campaigns/uav-2026/STATUS.json'
SELECTION = ROOT / 'campaigns/uav-2026/evidence/prop-selection-20261027'
MACRO = ROOT / 'macros' / 'ConfigureMrfPropellerPhysics.java'
COMPILE_DIR = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/compile'
UIUC_RUN = SELECTION / 'raw/uiuc-volume-1/data/apce_10x7_pg0816_6015.txt'

# Keys the Phase 2 MRF macro reads with first-match regex helpers in case_spec.json. Each must occur
# exactly once in the whole document (the macro searches the full text, not a scoped object).
MACRO_VISIBLE_KEYS = (
    'scenario', 'diameter_m', 'axis', 'center_m', 'rpm', 'velocity_m_s', 'pressure_pa',
    'temperature_k', 'turbulence_intensity', 'turbulent_viscosity_ratio', 'density_model',
    'turbulence_model', 'rotor_region', 'stator_region', 'inlet_boundary', 'outlet_boundary',
    'farfield_boundary', 'reference_frame_name', 'thrust_report_name', 'torque_report_name',
    'force_positive_direction', 'torque_axis', 'expected_rotor_wall_boundaries',
    'expected_interface_count',
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_load(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


class PropellerCaseSpec(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = json_load(SPEC)
        cls.reference = json_load(REFERENCE)

    def test_case_spec_validates_offline(self):
        import sys
        sys.path.insert(0, str(ROOT / 'scripts'))
        from casespec import validate_case_spec
        result = validate_case_spec(self.spec, installed_operations=set())
        self.assertTrue(result.valid, f'missing={result.missing} errors={result.errors}')
        self.assertEqual('propeller_mrf', self.spec['scenario'])

    def test_macro_visible_keys_are_unique(self):
        """Every CaseSpec a macro reads must keep each macro-visible key unique (first-match regex readers).

        Covers the live CaseSpec and every registered diagnostic CaseSpec that is staged for a run
        (2026-10-30: the domain-sensitivity level-2 spec is read by the same macros).
        """
        specs = [SPEC] + sorted(CASE.glob('case_spec_*.json'))
        self.assertIn(CASE / 'case_spec_domain10d.json', specs)
        for spec_path in specs:
            text = spec_path.read_text(encoding='utf-8')
            duplicates = {}
            for key in MACRO_VISIBLE_KEYS:
                count = len(re.findall('"%s"\\s*:' % re.escape(key), text))
                if count > 1:
                    duplicates[key] = count
            self.assertEqual({}, duplicates,
                             f'{spec_path.name}: duplicate macro-visible CaseSpec keys: {duplicates}')

    def test_retired_model_form_narrative_is_pinned(self):
        """O9 for the 2026-10-30 review non-blocking N5: the live CaseSpec still describes the y+ target
        basis "with gamma-Re_theta" although the signed model form has been fully turbulent SST since
        2026-10-30.  The text is narrative only (no staged macro reads `yplus_target_basis`), so it is
        pinned here as a known stale field to be fixed at the next CaseSpec re-pin; a NEW stale mention
        anywhere else fails the test.  The deliberate historical fields (transition_basis,
        transition_model_basis, rationale, model_form_decision) are the record of the change and are
        expected to mention the retired form.
        """
        # acceptance.reference_tolerance is the SIGNED tolerance record (user kept it unchanged when the
        # model form was re-signed); it is history, not a description of the current run.
        documented_history = ('numerics.transition_basis', 'numerics.transition_model_basis',
                              'numerics.rationale', 'model_form_decision',
                              'acceptance.reference_tolerance')
        pattern = re.compile(r'gamma[-_ ]?re[-_ ]?theta|gammaretheta', re.IGNORECASE)
        stale = {}

        def walk(node, path=''):
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, f'{path}.{key}' if path else key)
            elif isinstance(node, str) and pattern.search(node):
                stale[path] = node[:80]

        walk(self.spec)
        stale = {path: text for path, text in stale.items()
                 if not path.startswith(documented_history)}
        self.assertEqual({'quality.yplus_target_basis'}, set(stale),
                         'unexpected retired-model-form narrative: %r (fix at the CaseSpec re-pin)' % stale)
        self.assertIn('gamma-Re_theta', self.spec['quality']['yplus_target_basis'])

    def test_operating_points_trace_to_saved_uiuc_file(self):
        raw = {}
        for line in UIUC_RUN.read_text(encoding='utf-8').splitlines():
            parts = line.split()
            if len(parts) == 4 and parts[0] != 'J':
                raw[parts[0]] = tuple(float(x) for x in parts[1:])
        self.assertTrue(raw, 'saved UIUC run file did not parse')
        points = self.spec['operating_points']
        self.assertEqual(5, len(points))
        rpm, diameter = self.spec['rotation']['rpm'], self.spec['geometry']['diameter_m']
        for point in points:
            key = f"{point['j']:.3f}"
            self.assertIn(key, raw, f"J={key} is not a measured point in {UIUC_RUN.name}")
            ct, cp, eta = raw[key]
            self.assertAlmostEqual(ct, point['ct_ref'], places=9)
            self.assertAlmostEqual(cp, point['cp_ref'], places=9)
            self.assertAlmostEqual(eta, point['eta_ref'], places=9)
            expected_u = point['j'] * rpm / 60.0 * diameter
            self.assertAlmostEqual(expected_u, point['u_m_s'], places=5)

    def test_reference_files_match_recorded_sha(self):
        pe0 = SELECTION / 'raw/apc-pe0/10x7E-PERF-202602.PE0'
        self.assertTrue(pe0.is_file(), 'saved APC PE0 file missing')
        self.assertEqual(self.reference['geometry']['file_sha256'], sha256(pe0))
        self.assertEqual(self.spec['geometry_source']['file_sha256'], sha256(pe0))
        self.assertEqual(self.reference['data']['selected_run']['file_sha256'], sha256(UIUC_RUN))
        for name, key in (('e63.dat', 'e63_sha256'), ('naca4412.dat', 'naca4412_sha256')):
            path = SELECTION / 'raw/airfoils' / name
            self.assertTrue(path.is_file(), f'saved airfoil {name} missing')
            self.assertEqual(self.reference['geometry']['airfoil_coordinates'][key], sha256(path))

    def test_selection_record_exists(self):
        for name in ('SELECTION.md', 'IDENTITY.md', 'SOURCES.json'):
            self.assertTrue((SELECTION / name).is_file(), f'{name} missing from selection evidence')

    def test_reusable_mrf_chain_can_parse_the_case_spec(self):
        """Independent review (2026-10-27) rejected the first CaseSpec because the reusable MRF chain
        reads `geometry` with a FLAT `\\{([^{}]*)\\}` regex and then reads
        geometry.stationary_fluid_part / rotating_fluid_part. That rejection is pinned here: if the
        geometry object ever gains a nested object (or the fluid-part roles leave geometry), the chain
        dies at AuditMrfSurfaceMap before a region exists.
        """
        import re
        raw = SPEC.read_text(encoding='utf-8')

        def flat(key):
            match = re.search(r'"%s"\s*:\s*\{([^{}]*)\}' % re.escape(key), raw)
            return match.group(1) if match else None

        geometry = flat('geometry')
        self.assertIsNotNone(geometry, 'reusable chain flat parser cannot read geometry')
        for key in ('stationary_fluid_part', 'rotating_fluid_part',
                    'outer_cylinder_part', 'rotation_envelope_part', 'recovered_rotor_part'):
            self.assertIn('"%s"' % key, geometry, key)
        for key in ('domain', 'rotation_zone', 'rotation', 'mrf_regions', 'mesh'):
            self.assertIsNotNone(flat(key), 'reusable chain flat parser cannot read ' + key)
        # BuildMrfFluidVolumes requires geometry.rotor_parts == recovered_rotor_part (single solid role)
        rotor_parts = re.search(r'"rotor_parts"\s*:\s*\[([^\]]+)\]', geometry).group(1)
        names = [item.strip().strip('"') for item in rotor_parts.split(',')]
        recovered = re.search(r'"recovered_rotor_part"\s*:\s*"([^"]+)"', raw).group(1)
        self.assertEqual([recovered], names)

    def test_geometry_step_and_si_values_match_the_record(self):
        """P2.2: the CAD STEP is the source of the geometry audit values.

        Ratchets: (a) the STEP exists and its sha256 is the one the CaseSpec/reference record pins,
        (b) the SI bounds/volume in the CaseSpec are exactly the measured CAD values, not guesses.
        """
        geometry_record = json_load(CASE / 'geometry/GEOMETRY.json')
        step = CASE / 'geometry' / Path(geometry_record['files']['step']['path']).name
        self.assertTrue(step.is_file(), f'missing STEP {step}')
        self.assertEqual(geometry_record['files']['step']['sha256'], sha256(step))
        self.assertEqual(self.spec['geometry_source']['cad']['step_sha256'], sha256(step))
        measured = geometry_record['measured']
        expected_bounds = [round(v / 1000.0, 9) for v in measured['bbox_mm']]
        for got, want in zip(self.spec['geometry']['expected_bounds_m'], expected_bounds):
            self.assertAlmostEqual(got, want, places=12)
        self.assertAlmostEqual(self.spec['geometry']['expected_volume_m3'],
                               measured['volume_mm3'] * 1e-9, places=15)
        self.assertEqual('arbitrary', self.spec['geometry']['body_shape'])
        self.assertEqual(['APC10X7E_ROTOR'], self.spec['geometry']['body_parts'])

    def test_mesh_object_satisfies_the_mesh_macro_preconditions(self):
        """ConfigureMrfMeshOperation asserts these relations; a CaseSpec that violates them would
        fail the stage after an expensive geometry/region build, so the ratchet is here instead."""
        mesh = self.spec['mesh']
        base = mesh['base_size_m']
        self.assertLess(mesh['blade_min_size_m'], mesh['blade_target_size_m'])
        self.assertLess(mesh['blade_target_size_m'], base)
        self.assertLess(mesh['interface_min_size_m'], mesh['interface_target_size_m'])
        self.assertLess(mesh['interface_target_size_m'], base)
        self.assertLess(mesh['prism_total_m'], mesh['blade_target_size_m'])
        self.assertGreater(mesh['prism_stretch'], 1.0)
        self.assertLessEqual(mesh['prism_stretch'], 2.0)
        for key in ('prism_layers', 'rotating_prism_layers'):
            self.assertGreaterEqual(mesh[key], 3)
            self.assertLessEqual(mesh[key], 20, 'ConfigureMrfMeshOperation rejects >20 layers')
        self.assertEqual(20, self.spec['quality']['mesh_prism_layers_target'],
                         'attempt A3 keeps the 20-layer target that passed A1 wall-prism coverage')
        self.assertNotEqual(mesh['rotating_operation'], mesh['stationary_operation'])

    def test_geometry_variant_is_declared_and_scope_confirmed(self):
        """Review-2d blocking finding: the as-built CAD omits PE0 SWEEP/RAKE while the signed scope said
        "nominal design". The variant must be declared in BOTH the CaseSpec and the signed reference, with
        a recorded user scope confirmation (not a silent assumption)."""
        variant = self.spec['geometry']['geometry_variant']
        self.assertIn('straight_stacking_line', variant)
        self.assertIn('11.38', variant)   # max |sweep| mm, so the deviation is quantified, not vague
        addendum = self.reference['acceptance']['tolerance']['scope_addendum']
        self.assertEqual('CONFIRMED', addendum['status'])
        self.assertEqual('user', addendum['confirmed_by'])
        self.assertIn('straight-stacking-line', self.reference['acceptance']['tolerance']['signed_scope']['geometry'].lower())
        self.assertIn('straight-stacking-line', self.reference['acceptance']['q3_scope']['geometry'].lower())
        # the CaseSpec's own assumptions must agree with the variant, not call it nominal
        self.assertIn('straight-stacking-line', self.spec['assumptions'][0])
        self.assertIn('not applied', self.spec['assumptions'][0])

    def test_mesh_prism_first_layer_matches_quality_record(self):
        """Review-2d non-blocking MAJOR: 20 layers / total thickness / stretch imply a first-layer height;
        the quality record and y+ basis must be consistent with it (not a different remembered number)."""
        mesh = self.spec['mesh']
        layers = mesh['prism_layers']
        stretch = mesh['prism_stretch']
        total = mesh['prism_total_m']
        implied = total * (stretch - 1.0) / (stretch ** layers - 1.0)
        recorded = self.spec['quality']['mesh_prism_first_layer_height_m']
        self.assertAlmostEqual(implied, recorded, delta=abs(recorded) * 0.01)
        self.assertAlmostEqual(total, self.spec['quality']['mesh_prism_total_thickness_m'], places=12)
        self.assertLessEqual(self.spec['acceptance']['yplus_area_average_max'], 1.0)
        new_first = '%.6e' % float(self.spec['quality']['mesh_prism_first_layer_height_m'])
        self.assertIn(new_first, self.spec['quality']['yplus_target_basis'])

    def test_root_chord_centres_are_aligned_on_the_axis(self):
        """User-reported defect 2026-10-28: the first CAD stacked every section on its leading edge, so each
        blade root chord sat half a chord off the rotation axis. Since rev2 every section is stacked on the
        chord MIDPOINT. The adopted rev4 geometry is built in Fusion from the same PE0 stack; this ratchet pins
        the alignment rule, the corrected two-blade envelope and the registered numerical treatments."""
        record = json_load(CASE / 'geometry/GEOMETRY.json')
        self.assertEqual('rev4_fusion_watertight', record.get('revision'))
        self.assertEqual('chord midpoint on the station radius point (root chord centre on the rotation axis); straight stacking line',
                         record.get('stacking'))
        root = record['root_alignment']
        self.assertAlmostEqual(root['chord_mm'], 20.32, places=6)
        self.assertTrue(root['centre_on_axis'])
        measured = record['measured']
        self.assertEqual(1, measured['bodies'])
        self.assertEqual(1, measured['shells'])
        bbox_m = [v / 1000.0 for v in measured['bbox_mm']]
        self.assertLess(abs(bbox_m[1]), 0.015)
        self.assertLess(abs(bbox_m[4]), 0.015)
        self.assertLess(abs(bbox_m[1] + bbox_m[4]), 2e-6, 'blades must be symmetric about y=0')
        self.assertAlmostEqual(bbox_m[3] - bbox_m[0], 0.254, places=6, msg='diameter must stay 254.000 mm')
        for got, want in zip(self.spec['geometry']['expected_bounds_m'], [round(v, 9) for v in bbox_m]):
            self.assertAlmostEqual(got, want, places=12)
        self.assertTrue(record['blade_symmetry_180'])
        treatments = record['numerical_treatments']
        self.assertEqual('CONFIRMED', treatments['user_scope_confirmation']['status'])
        self.assertIn('21.2', treatments['hub_boss'])
        self.assertIn('0.05 mm', treatments['trailing_edge'])
        self.assertIn('254.000', treatments['tip'])
        self.assertEqual('CONFIRMED_BY_USER', self.reference['geometry']['numerical_treatments']['status'])

    def test_signed_tolerance_and_budget_are_recorded_without_claiming_q3(self):
        """The user signed the tolerance on 2026-10-28 and approved the 30 h budget.

        The signature is recorded in BOTH the reference and the campaign state, but Q3 still requires
        a citable result record and every numerical/reference gate: a signature alone is authority,
        not evidence.
        """
        tolerance = self.reference['acceptance']['tolerance']
        self.assertEqual('SIGNED', tolerance['status'])
        self.assertEqual('user', tolerance['signed_by'])
        self.assertTrue(tolerance['signed_at'])
        self.assertTrue(tolerance.get('signed_note'))
        status = json_load(STATUS)
        case = next(item for item in status['cases'] if item['case_id'] == 'uav-prop-apc10x7e')
        self.assertTrue(case['tolerance_signed'])
        self.assertEqual('SIGNED', case['tolerance_status'])
        self.assertEqual(tolerance['previous_signing']['signed_at'], case['tolerance_signed_at'])
        self.assertEqual(case['tolerance_resigning_20261030']['signed_at'], tolerance['signed_at'])
        self.assertTrue(case['budget_approved'])
        self.assertEqual(30, case['budget_approved_hours'])
        # Q3 is claimed ONLY under the user-signed 2026-10-30 numerical-criterion revision, and the claim must
        # carry that revision, the evidence and the superseded-requirement history.
        self.assertEqual('Q3', case['q'])
        self.assertTrue(case['q3_claim'])
        self.assertTrue(case['engineering_verified'])
        self.assertTrue(case['case_spec_frozen'])
        self.assertIsNotNone(case['record'])
        self.assertTrue((ROOT / case['record']).exists())
        revision = case['numerical_criterion_revision']
        self.assertEqual(0.05, revision['value'])
        self.assertIn('mesh-scatter', revision['user_decision'])
        self.assertEqual('three-mesh GCI p>1 & GCI_fine<=5%', revision['replaces'])
        self.assertEqual('reference.json acceptance.q3_requires_previous', revision['previous_requirement_kept_in'])
        reference = self.reference['acceptance']
        self.assertTrue(any('three-mesh GCI' in item for item in reference['q3_requires_previous']['list']))
        self.assertTrue(any('mesh-scatter numerical criterion' in item for item in reference['q3_requires']))
        self.assertEqual(0.05, reference['numerical_criterion']['value'])
        scope = case['q3_scope']
        self.assertIn('caveat', scope)
        self.assertIn('NOT met', scope['caveat'])
        self.assertEqual('campaigns/uav-2026/evidence/uav-prop-apc10x7e/MESH-SCATTER-GATE-20261030a.json',
                         scope['numerical_criterion_evidence'])

    def test_recorded_case_spec_sha_matches_the_file(self):
        status = json_load(STATUS)
        case = next(item for item in status['cases'] if item['case_id'] == 'uav-prop-apc10x7e')
        self.assertEqual(sha256(SPEC), case['case_spec_sha256'])
        self.assertEqual(sha256(REFERENCE), case['reference_sha256'])


class MrfMacroCompileGate(unittest.TestCase):
    def test_compiled_bytes_are_not_stale(self):
        self.assertTrue(MACRO.is_file())
        macro_sha = sha256(MACRO)
        records = sorted(COMPILE_DIR.glob('COMPILE-MRF-2502-*.json'))
        matching = []
        for record in records:
            data = json_load(record)
            if data.get('source_hashes', {}).get('macros/ConfigureMrfPropellerPhysics.java') == macro_sha:
                matching.append(data)
        self.assertTrue(matching, 'no 2502 compile record pins the current macro bytes; recompile first')
        data = matching[-1]
        self.assertEqual(0, data['exit_code'])
        self.assertRegex(data['javac'], r'21\.0\.4')
        self.assertEqual(471, data['jar_count'])
        self.assertEqual(0, data['star_processes'], 'compile must not run while a STAR process exists')
        self.assertFalse(data['star_started'])
        self.assertTrue(any(c['name'] == 'ConfigureMrfPropellerPhysics.class' for c in data['class_hashes']))

    def test_macro_selection_logic_has_no_sample_specific_names(self):
        """AGENTS.md section 4: the SELECTION LOGIC must not depend on sample names.

        Comments are stripped first: provenance comments that name the closed AIR-3B campaign are
        documentation, not a selection dependency, and the rule is about code that selects objects.
        """
        import re
        source = MACRO.read_text(encoding='utf-8')
        code = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
        code = re.sub(r'//[^\n]*', '', code)
        for forbidden in ('AIR3B', 'AIR-3B', 'FLUID', 'PROPELLER_', 'AUTO_FULL_ROTOR'):
            self.assertNotIn(forbidden, code, f'sample-specific token {forbidden!r} in macro code')


if __name__ == '__main__':
    unittest.main()


class PropellerRev4AirfoilRule(unittest.TestCase):
    """O9 for the user's modelling rule and the adopted Fusion build: every section profile is ONE fit
    spline through all its airfoil points (short TE line closes the loop; no polyline airfoil), every
    feature is at least 0.05 mm (below the STAR import tessellation tolerance the part imports as not
    closed), and the hub is the registered numerical clearance so the 180 deg union is watertight."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.data = json_load(cls.ROOT / 'work/p2-geometry/fusion-build/sections_fusion_tefix.json')
        cls.builder = (cls.ROOT / 'work/p2-geometry/fusion_build_prop_tefix.py').read_text(encoding='utf-8')
        cls.geometry = json_load(CASE / 'geometry/GEOMETRY.json')

    def test_26_sections_single_fit_spline_and_min_feature(self):
        self.assertEqual(26, len(self.data['sections']))
        self.assertIn('sketchFittedSplines.add', self.builder)
        self.assertIn('addByTwoPoints', self.builder)
        self.assertNotIn('sketchLines.addByTwoPoints(pts[-1], pts[-1])', self.builder)
        for section in self.data['sections']:
            pts = section['points_mm']
            self.assertGreater(len(pts), 40)
            te_edge = math.dist(pts[-1], pts[0])
            self.assertGreaterEqual(te_edge, 0.05,
                                    'S%d TE closing edge %.4f mm is below the registered 0.05 mm minimum'
                                    % (section['index'], te_edge))

    def test_hub_clearance_and_tip_chord_are_the_confirmed_treatments(self):
        self.assertEqual(21.2, self.data['hub']['diameter_mm'])
        self.assertEqual(10.4, self.data['hub']['height_mm'])
        tip = self.data['sections'][-1]
        self.assertAlmostEqual(0.414, tip['chord_mm'], places=3)
        self.assertIn('circularPatternFeatures', self.builder)
        self.assertIn('combineFeatures', self.builder)
        self.assertIn('createSTEPExportOptions', self.builder)

    def test_adopted_step_matches_the_geometry_record(self):
        step = self.ROOT / 'cases/uav-prop-apc10x7e/geometry/APC_10x7E.step'
        record = self.geometry['files']['step']
        self.assertEqual(hashlib.sha256(step.read_bytes()).hexdigest(), record['sha256'])
        self.assertEqual(step.stat().st_size, record['bytes'])
        self.assertEqual('rev4_fusion_watertight', self.geometry['revision'])
        self.assertTrue(self.geometry['validation']['star_diagnostic_import']['closed'])
        self.assertTrue(self.geometry['validation']['star_diagnostic_import']['manifold'])
