# -*- coding: utf-8 -*-
"""Tests for the round-13 mesh/domain study orchestration (scripts/run_sensitivity_study.py).

The point of these tests is that the study capability is ORCHESTRATION: the level definitions come from the
CaseSpec, the per-level pipelines copy the reviewed stages verbatim, the numerical judgement is delegated to
the already-tested evaluate_sensitivity/grid_convergence code, and a level that fails a CaseSpec gate or a
reuse check is EXCLUDED rather than quietly averaged in. No STAR host is needed: every test here is offline.
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

SPEC = ROOT / 'cases' / 'static-sphere-studies' / 'case_spec.json'
BASE_SPEC = ROOT / 'cases' / 'static-sphere-external-flow' / 'case_spec.json'
BASE_PIPELINE = ROOT / 'cases' / 'static-sphere-external-flow' / 'pipeline-e2e-geometry-to-validation.json'
GEOMETRY = (ROOT / 'test-evidence' / 'static-external-flow' / 'build-body-positive'
            / 's1-static-sphere-body-20261003a' / 'static_body_geometry.sim')
BASELINE_EVIDENCE = ROOT / 'test-evidence' / 'static-external-flow' / 'round10-e2e'
BASELINE_TRIAL = 's1-static-sphere-e2e-20261003a'
MESH_ROOT = ROOT / 'test-evidence' / 'static-external-flow' / 'round13-mesh-study'
DOMAIN_ROOT = ROOT / 'test-evidence' / 'static-external-flow' / 'round13-domain-study'

spec = importlib.util.spec_from_file_location('study_driver', ROOT / 'scripts' / 'run_sensitivity_study.py')
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)

from evaluate_sensitivity import compare_study, evaluate_file   # noqa: E402
from stage_plan import plan_pipeline                            # noqa: E402


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


class TheStudySpecDeclaresItsLevelsAndNothingElse(unittest.TestCase):
    def test_the_study_spec_is_the_baseline_case_physically_unchanged(self):
        study, base = load(SPEC), load(BASE_SPEC)
        for key in ('geometry', 'flow', 'numerics', 'domain', 'region_name', 'boundary_roles',
                    'mesh_base_size_m', 'mesh_body_target_size_m', 'mesh_body_minimum_size_m',
                    'mesh_prism_layers', 'engineering_target', 'medium', 'scenario'):
            self.assertEqual(base[key], study[key], key)
        changed = {key for key in set(study) | set(base) if study.get(key) != base.get(key)}
        self.assertEqual({'_study_id', '_study_note', '_status', '_reference_check', 'quality'}, changed)

    def test_the_level_declarations_are_complete_and_named(self):
        study = load(SPEC)
        for kind in ('mesh', 'domain'):
            levels = driver.study_levels(study, kind)
            self.assertEqual(3, len(levels), kind)
            self.assertEqual(len({level['name'] for level in levels}), len(levels))
        self.assertEqual(1.25, study['quality']['mesh_study']['refinement_ratio'])

    def test_exactly_one_level_per_study_reuses_the_citable_baseline(self):
        study = load(SPEC)
        for kind in ('mesh', 'domain'):
            reuse = [level for level in driver.study_levels(study, kind) if level.get('reuse')]
            self.assertEqual(1, len(reuse), kind)
            self.assertEqual(BASELINE_TRIAL, reuse[0]['reuse']['trial_id'])
            self.assertIn('why', reuse[0]['reuse'])


class TheGeneratedLevelsAreOneParameterStudies(unittest.TestCase):
    def test_mesh_levels_change_only_the_three_mesh_sizes(self):
        study = load(SPEC)
        for level in driver.study_levels(study, 'mesh'):
            generated = driver.level_case_spec(study, 'mesh', level)
            changed = {key for key in set(generated) | set(study) if generated.get(key) != study.get(key)}
            self.assertLessEqual(changed, {'_study_level', 'mesh_base_size_m',
                                           'mesh_body_target_size_m', 'mesh_body_minimum_size_m'},
                                 level['name'])
            self.assertNotIn('domain', changed, level['name'])

    def test_domain_levels_change_only_the_outer_bounds(self):
        study = load(SPEC)
        for level in driver.study_levels(study, 'domain'):
            generated = driver.level_case_spec(study, 'domain', level)
            changed = {key for key in set(generated) | set(study) if generated.get(key) != study.get(key)}
            self.assertLessEqual(changed, {'_study_level', 'domain'}, level['name'])
            self.assertNotIn('mesh_base_size_m', changed, level['name'])
            # and nothing inside domain except the bounds
            inner = {key for key in set(generated['domain']) | set(study['domain'])
                     if generated['domain'].get(key) != study['domain'].get(key)}
            self.assertLessEqual(inner, {'outer_bounds_m'}, level['name'])

    def test_the_mesh_sizes_scale_by_the_declared_factor(self):
        study = load(SPEC)
        base = (study['mesh_base_size_m'], study['mesh_body_target_size_m'],
                study['mesh_body_minimum_size_m'])
        for level in driver.study_levels(study, 'mesh'):
            sizes = (level['base_size_m'], level['body_target_size_m'],
                     level['body_minimum_size_m'])
            for index, factor in enumerate((level['size_factor'],) * 3):
                self.assertAlmostEqual(base[index] * factor, sizes[index], places=12, msg=level['name'])
        factors = sorted(level['size_factor'] for level in driver.study_levels(study, 'mesh'))
        self.assertAlmostEqual(1.25 / 1.0, factors[1] / factors[0], places=12)
        self.assertAlmostEqual(1.0 / 0.8, factors[2] / factors[1], places=12)

    def test_the_domain_bounds_scale_about_the_body_centre_and_keep_the_proportions(self):
        study = load(SPEC)
        base = study['domain']['outer_bounds_m']
        for level in driver.study_levels(study, 'domain'):
            bounds = level['bounds_m']
            for index, value in enumerate(bounds):
                self.assertAlmostEqual(base[index] * level['extent_factor'], value, places=12,
                                       msg=level['name'])
            # the body sits at the origin: the 1:3 upstream:downstream ratio must be preserved
            self.assertAlmostEqual(3.0, (bounds[3] - 0.0) / (0.0 - bounds[0]), places=12)


class TheGeneratedPipelinesCopyTheReviewedStages(unittest.TestCase):
    ALLOWED_DIFFERENCES = {'pipeline_id', 'case_label', 'description', 'input_hashes', 'macro_hashes',
                           'status'}

    def pipelines(self):
        for root, kind in ((MESH_ROOT, 'mesh'), (DOMAIN_ROOT, 'domain')):
            for level in ('coarse', 'fine', 'd1p25', 'd1p50'):
                path = root / level / 'pipeline.json'
                if path.is_file():
                    yield kind, level, path

    def test_the_stage_list_is_the_baseline_stage_list_verbatim(self):
        base = load(BASE_PIPELINE)
        seen = 0
        for _kind, level, path in self.pipelines():
            doc = load(path)
            self.assertEqual(base['stages'], doc['stages'], level)
            changed = {key for key in set(doc) | set(base) if doc.get(key) != base.get(key)}
            self.assertLessEqual(changed, self.ALLOWED_DIFFERENCES, level)
            seen += 1
        self.assertGreaterEqual(seen, 4)

    def test_the_macro_pins_are_the_current_macro_bytes(self):
        """A pin must equal the live bytes, unless a LATER repair is recorded as superseding it.

        Round 14 repaired macros/AuditExternalFlowConvergence.java (the formal-entry crash), which
        legitimately staled the round-13 level pipelines' pin for that one macro - exactly what
        round12-superseded-pins.json already records for the geometry macros. The recorded supersessions
        are the only allowed exceptions, so an unexplained stale pin still fails here.
        """
        from tests.superseded_pins import allowed_pins
        allowed = allowed_pins(ROOT / 'test-evidence')
        superseded = set(allowed)
        seen = set()
        for _kind, level, path in self.pipelines():
            doc = load(path)
            for name, digest in doc['macro_hashes'].items():
                live = driver.sha256(ROOT / 'macros' / name)
                if digest != live:
                    self.assertIn(name, superseded,
                                  '%s/%s pins %s at %s but it is not recorded as superseded'
                                  % (level, name, name, digest[:16]))
                    self.assertIn(digest, allowed[name],
                                  '%s/%s pins %s at %s which no record lists as superseded'
                                  % (level, name, name, digest[:16]))
                    seen.add(name)
                else:
                    self.assertEqual(live, digest)
        # every stale pin in the committed level pipelines must be a RECORDED supersession
        self.assertTrue(seen, 'no stale pin was visible at all - the check is not exercising anything')
        for name in seen:
            self.assertIn(name, allowed, '%s is pinned stale with no recorded supersession' % name)

    def test_every_level_pipeline_plans_as_one_star_process_per_macro(self):
        for _kind, level, path in self.pipelines():
            plan = plan_pipeline(load(path))
            self.assertEqual(6, len(plan['stages']), level)
            for stage in plan['stages']:
                for step in stage['steps']:
                    self.assertIsNotNone(stage['name'])

    def test_a_level_pipeline_points_at_its_own_case_spec_and_the_same_geometry(self):
        for _kind, level, path in self.pipelines():
            doc = load(path)
            self.assertEqual({driver.sha256(Path(path).parent / 'case_spec.json'),
                              driver.sha256(GEOMETRY)},
                             set(doc['input_hashes'].values()), level)


class ExcludedLevelsCannotEnterTheConclusion(unittest.TestCase):
    def valid_row(self, level, cells, drag):
        return {'level': level, 'cells': float(cells), 'domain_scale': None,
                'drag_coefficient_estimate': drag, 'drag_x_n': drag * 1.04, 'density_kg_m3': 1.1766,
                'mass_imbalance_ratio': 3.5e-09, 'valid': True, 'validity': {'valid': True, 'failures': []}}

    def quality(self):
        return load(SPEC)['quality']

    def test_a_closed_level_is_dropped_and_the_study_is_not_established(self):
        rows = [self.valid_row('coarse', 30_000, 0.4300),
                dict(self.valid_row('medium', 58_724, 0.4296), valid=False,
                     validity={'valid': False, 'failures': ['mesh.measured_face_validity_min=0.9 violates >= 1.0']}),
                self.valid_row('fine', 115_000, 0.4295)]
        record = driver.assemble_study({'quality': self.quality()}, 'mesh', rows, None)
        self.assertEqual(['coarse', 'fine'], record['valid_levels'])
        self.assertFalse(record['sensitivity_passed'])
        self.assertEqual('NOT_ESTABLISHED', record['sensitivity_status'])
        self.assertIn('required 3 levels', record['reason'])
        self.assertIn('face_validity', json.dumps(record['excluded_levels']))
        self.assertIsNone(record['gci'])
        self.assertFalse(record['mesh_independent'])

    def test_three_valid_mesh_levels_do_call_the_gci(self):
        rows = [self.valid_row('coarse', 30_000, 0.4300),
                self.valid_row('medium', 58_724, 0.4296),
                self.valid_row('fine', 115_000, 0.4295)]
        record = driver.assemble_study({'quality': self.quality()}, 'mesh', rows, None)
        self.assertIsNotNone(record['gci'])
        self.assertIn('drag_coefficient_estimate', record['gci'])
        self.assertAlmostEqual((115_000 / 30_000) ** (1 / 3), record['gci_ratio_from_cells'], places=12)
        self.assertEqual(1.25, record['gci_ratio_declared'])
        # the GCI verdict must be the function's, not this driver's
        self.assertEqual(record['gci']['drag_coefficient_estimate']['valid'],
                         record['gci']['drag_coefficient_estimate']['valid'])
        self.assertFalse(record['gci']['drag_coefficient_estimate']['engineering_verified'])

    def test_a_domain_study_never_uses_the_grid_gci(self):
        rows = [self.valid_row('d1p00', 58_724, 0.4296),
                self.valid_row('d1p25', 115_000, 0.4296),
                self.valid_row('d1p50', 198_000, 0.4296)]
        for index, row in enumerate(rows, start=1):
            row['domain_scale'] = (1.0, 1.25, 1.5)[index - 1]
        record = driver.assemble_study({'quality': self.quality()}, 'domain', rows, None)
        self.assertIsNone(record['gci'])
        self.assertIn('NOT applied to a domain study', record['gci_note'])
        self.assertIn('domain_independent', record)

    def test_the_driver_reuses_the_existing_judgement_code(self):
        source = (ROOT / 'scripts' / 'run_sensitivity_study.py').read_text(encoding='utf-8')
        self.assertIn('from evaluate_sensitivity import', source)
        self.assertIn('from grid_convergence import', source)
        self.assertNotIn('def compare_study', source)
        self.assertNotIn('def three_grid_gci', source)
        # every gate bound must be read from the CaseSpec, never from a literal in this file
        self.assertNotIn('mesh_min_face_validity": ', source)
        self.assertIn("quality.get('mesh_min_face_validity')", source)
        self.assertIn("quality.get('mass_imbalance_ratio_limit')", source)
        self.assertIn("quality.get('sensitivity_rel_change')", source)


class TheReuseOfTheCitableBaselineIsVerified(unittest.TestCase):
    def level(self, trial=BASELINE_TRIAL):
        return {'reuse': {'trial_id': trial, 'evidence': str(BASELINE_EVIDENCE)}}

    def test_the_round10_reuse_is_refused_by_exactly_the_macro_this_round_repaired(self):
        """The round-10 baseline's reuse is now refused, and the refusal names ONE macro.

        Round 14 repaired AuditExternalFlowConvergence.java, so the round-10 run's revision for it is no
        longer the live bytes. The reuse check refusing that is the intended behaviour, and the blast
        radius is pinned here: if any OTHER macro starts differing, this test fails.
        """
        study = load(SPEC)
        level = next(item for item in driver.study_levels(study, 'mesh') if item['name'] == 'medium')
        generated = driver.level_case_spec(study, 'mesh', level)
        result = driver.reused_reference(BASELINE_EVIDENCE, BASELINE_TRIAL, {'reuse': level['reuse']},
                                        generated, GEOMETRY)
        self.assertFalse(result['ok'])
        macros = [item for item in result['differences'] if 'macro revision' in item]
        # every macro that differs here must have a RECORDED supersession (①R14's repair, ①R24's two
        # geometry macros, ②R23's region/physics change); an unrecorded one still fails this test.
        from tests.superseded_pins import allowed_pins
        allowed = allowed_pins(ROOT / 'test-evidence')
        self.assertTrue(macros, result['differences'])
        for item in macros:
            self.assertTrue(any(name in item for name in allowed),
                            'a macro revision differs without a recorded supersession: %s' % item)
        self.assertTrue(any('AuditExternalFlowConvergence.java' in item for item in macros), macros)
        self.assertIn('reused_run_input_hashes', result['compared'])

    def test_a_reused_run_that_pins_the_live_macros_is_still_accepted(self):
        """The positive direction, kept: the same check accepts a run whose measured-by macros are live.

        Without this, the refusal above would also be satisfied by a check that refuses everything.
        """
        import copy
        import tempfile
        study = load(SPEC)
        level = next(item for item in driver.study_levels(study, 'mesh') if item['name'] == 'medium')
        generated = driver.level_case_spec(study, 'mesh', level)
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / 'evidence'
            trial = evidence / BASELINE_TRIAL
            trial.mkdir(parents=True)
            (trial / 'case_spec.json').write_text(
                (BASELINE_EVIDENCE / BASELINE_TRIAL / 'case_spec.json').read_text(encoding='utf-8'),
                encoding='utf-8')
            config = load(BASELINE_EVIDENCE / (BASELINE_TRIAL + '-config.json'))
            config = copy.deepcopy(config)
            for name in list(config['macro_hashes']):
                config['macro_hashes'][name] = driver.sha256(ROOT / 'macros' / name)
            (evidence / (BASELINE_TRIAL + '-config.json')).write_text(json.dumps(config), encoding='utf-8')
            # the study spec's own reuse.evidence wins over the --baseline-evidence default, so the
            # fixture must point the reference at itself (the driver documents that precedence)
            ref = dict(level['reuse'], evidence=str(evidence))
            result = driver.reused_reference(evidence, BASELINE_TRIAL, {'reuse': ref},
                                            generated, GEOMETRY)
        self.assertTrue(result['ok'], result['differences'])
        self.assertEqual([], result['differences'])

    def test_a_level_whose_parameters_differ_is_refused(self):
        study = load(SPEC)
        level = next(item for item in driver.study_levels(study, 'mesh') if item['name'] == 'medium')
        generated = driver.level_case_spec(study, 'mesh', level)
        generated['mesh_base_size_m'] = 0.021      # one value off the reused run's configuration
        result = driver.reused_reference(BASELINE_EVIDENCE, BASELINE_TRIAL, {'reuse': level['reuse']},
                                        generated, GEOMETRY)
        self.assertFalse(result['ok'])
        self.assertIn('mesh_base_size_m', result['differences'])

    def test_a_different_geometry_is_refused(self):
        study = load(SPEC)
        level = next(item for item in driver.study_levels(study, 'domain') if item['name'] == 'd1p00')
        generated = driver.level_case_spec(study, 'domain', level)
        other = ROOT / 'test-evidence' / 'static-external-flow' / 'build-body-positive' / \
            's1-static-sphere-body-20261003a' / 'static_body_geometry.json'
        result = driver.reused_reference(BASELINE_EVIDENCE, BASELINE_TRIAL, {'reuse': level['reuse']},
                                        generated, other)
        self.assertFalse(result['ok'])
        self.assertTrue(any('geometry input hash' in item for item in result['differences']))


class TheStudyOutputIsIndependentlyReEvaluable(unittest.TestCase):
    def test_the_written_csv_reproduces_the_verdict_through_the_existing_cli(self):
        study = load(SPEC)
        rows = [{'level': 'coarse', 'cells': 30_000.0, 'drag_coefficient_estimate': 0.4300,
                 'drag_x_n': 0.4472, 'density_kg_m3': 1.1766, 'mass_imbalance_ratio': 3.5e-09,
                 'valid': True, 'validity': {'valid': True, 'failures': []}},
                {'level': 'medium', 'cells': 58_724.0, 'drag_coefficient_estimate': 0.4296,
                 'drag_x_n': 0.4467, 'density_kg_m3': 1.1767, 'mass_imbalance_ratio': 3.545e-09,
                 'valid': True, 'validity': {'valid': True, 'failures': []}},
                {'level': 'fine', 'cells': 115_000.0, 'drag_coefficient_estimate': 0.4295,
                 'drag_x_n': 0.4466, 'density_kg_m3': 1.1767, 'mass_imbalance_ratio': 3.55e-09,
                 'valid': True, 'validity': {'valid': True, 'failures': []}}]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            summary = driver.write_outputs(tmp, 'mesh', study, rows, None, None)
            csv_path = Path(summary['records']['csv'])
            reread = evaluate_file(csv_path, 'mesh')
            self.assertEqual(summary['sensitivity_passed'], reread['passed'])
            self.assertTrue(reread['physical_validation'] is False)
            record = json.loads(Path(summary['records']['study']).read_text(encoding='utf-8'))
            self.assertEqual(record['sensitivity_status'], summary['sensitivity_status'])
            self.assertTrue(Path(summary['records']['report']).is_file())
            self.assertIn('不主张', Path(summary['records']['report']).read_text(encoding='utf-8'))

    def test_an_established_study_still_states_what_it_does_not_claim(self):
        study = load(SPEC)
        rows = [{'level': name, 'cells': cells, 'domain_scale': None,
                 'drag_coefficient_estimate': drag, 'drag_x_n': drag * 1.04, 'density_kg_m3': 1.1766,
                 'mass_imbalance_ratio': 3.5e-09, 'valid': True,
                 'validity': {'valid': True, 'failures': []}}
                for name, cells, drag in (('coarse', 30_000.0, 0.4300), ('medium', 58_724.0, 0.4296),
                                          ('fine', 115_000.0, 0.4295))]
        record = driver.assemble_study({'quality': load(SPEC)['quality']}, 'mesh', rows, None)
        self.assertTrue(record['sensitivity_passed'])
        self.assertEqual('ESTABLISHED', record['sensitivity_status'])
        joined = ' '.join(record['_not_claimed'])
        self.assertIn('NOT_ESTABLISHED', joined)
        self.assertIn('no engineering pass/fail', joined)


class EveryCaseSpecGateCanCloseALevel(unittest.TestCase):
    """The round-13 independent review's blocker 1: the closing logic had NO test coverage.

    It proved this by mutation - making `level_validity` always return valid killed zero tests. The gates are
    the only thing standing between "three levels ran" and "the study says something", so each gate is
    driven here one at a time with the CaseSpec's own bound tightened just past the measured value, and the
    level must be CLOSED with THAT gate named. The metrics are the real round-10 records, so the unmodified
    case is genuinely valid rather than a fixture built to pass.
    """

    def metrics(self):
        return driver.collect_metrics(BASELINE_EVIDENCE, BASELINE_TRIAL)

    def quality(self):
        return load(SPEC)['quality']

    def test_the_unmodified_round10_records_are_valid(self):
        # judged as a REUSED level: the round-10 run predates the round-12 delivery record, so its
        # delivery evidence is the shared citation state of the records it is measured from
        result = driver.level_validity(self.metrics(), self.quality(), reused=True)
        self.assertTrue(result['valid'], result['failures'])
        self.assertEqual([], result['failures'])
        self.assertTrue(result['delivery'] if 'delivery' in result else True)

    def test_each_numeric_gate_closes_the_level_and_names_itself(self):
        cases = [
            ('mesh_min_cell_quality', 'mesh.measured_cell_quality_min', 3.0),
            ('mesh_min_face_validity', 'mesh.measured_face_validity_min', 1.0000001),
            ('mesh_min_volume_change', 'mesh.measured_volume_change_min', 1.0),
            ('mesh_min_wall_prism_layers', 'mesh.measured_wall_prism_layers_min', 7),
            ('mass_imbalance_ratio_limit', 'validation.measured_mass_imbalance_ratio', 1e-12),
            ('force_window_rel_change', 'validation.drag_window_relative_deviation', 1e-9),
        ]
        for bound_name, expected_gate, tighter in cases:
            with self.subTest(bound=bound_name):
                quality = self.quality()
                quality[bound_name] = tighter
                result = driver.level_validity(self.metrics(), quality)
                self.assertFalse(result['valid'], bound_name)
                self.assertTrue(any(expected_gate in failure for failure in result['failures']),
                                (bound_name, result['failures']))

    def test_each_flag_gate_closes_the_level_and_names_itself(self):
        cases = [
            ('solve', 'finite_state', 'solve.finite_state'),
            ('solve', 'residuals_met', 'solve.residuals_met'),
            ('solve', 'target_quantities_steady', 'solve.target_quantities_steady'),
            ('validation', 'read_only_verified', 'validation.read_only_verified'),
            ('validation', 'stage1_agrees', 'validation.stage1_agrees'),
            ('validation', 'mass_balanced', 'validation.mass_balanced'),
        ]
        for record_name, flag, expected in cases:
            with self.subTest(flag=expected):
                metrics = self.metrics()
                metrics[record_name] = dict(metrics[record_name], **{flag: False})
                result = driver.level_validity(metrics, self.quality())
                self.assertFalse(result['valid'], expected)
                self.assertTrue(any(expected in failure for failure in result['failures']),
                                (expected, result['failures']))

    def test_missing_records_and_failed_delivery_close_the_level(self):
        metrics = self.metrics()
        metrics['quality'] = None
        metrics['checkpoints'] = {'download_succeeded': False, 'checkpoint_delivered': False}
        metrics['result'] = {'state': 'GATE_FAILED'}
        metrics['report'] = {'report_verdict': 'REPORT_INCOMPLETE'}
        result = driver.level_validity(metrics, self.quality())
        self.assertFalse(result['valid'])
        joined = ' '.join(result['failures'])
        for expected in ('mesh.measured_cell_quality_min missing', 'checkpoint delivery not verified',
                         'run state is not AIR3B_PIPELINE_COMPLETE', 'run report verdict is'):
            self.assertIn(expected, joined)

    def test_a_closed_level_cannot_be_reported_as_a_passing_study(self):
        # the end-to-end consequence of the gates: one closed level and the study is NOT_ESTABLISHED
        metrics = self.metrics()
        validity = driver.level_validity(metrics, {**self.quality(), 'mesh_min_wall_prism_layers': 7})
        self.assertFalse(validity['valid'])
        row = driver.level_row(metrics, 'mesh', None, self.quality())
        row.update({'level': 'medium', 'valid': False, 'validity': validity})
        rows = [row,
                {'level': 'coarse', 'cells': 30_000.0, 'drag_coefficient_estimate': 0.43,
                 'drag_x_n': 0.447, 'density_kg_m3': 1.1766, 'valid': True,
                 'validity': {'valid': True, 'failures': []}},
                {'level': 'fine', 'cells': 115_000.0, 'drag_coefficient_estimate': 0.4295,
                 'drag_x_n': 0.4466, 'density_kg_m3': 1.1767, 'valid': True,
                 'validity': {'valid': True, 'failures': []}}]
        record = driver.assemble_study({'quality': self.quality()}, 'mesh', rows, None)
        self.assertFalse(record['sensitivity_passed'])
        self.assertEqual('NOT_ESTABLISHED', record['sensitivity_status'])
        self.assertEqual(2, record['valid_level_count'])
        self.assertEqual(3, record['declared_level_count'])
        self.assertFalse(record['all_declared_levels_valid'])
        self.assertIsNone(record['gci'])
        self.assertFalse(record['mesh_independent'])
        self.assertFalse(record['domain_independent'])
        self.assertIn('wall_prism_layers', json.dumps(record['excluded_levels']))


class TheReusedLevelIsHeldToTheSameGates(unittest.TestCase):
    """The review's non-blocking finding: a reused level used to bypass level_validity entirely."""

    def test_the_reused_round10_level_is_held_to_both_checks_even_when_one_of_them_refuses(self):
        """The reuse branch runs BOTH the equality check and the CaseSpec gates, and reports each.

        Round 14's macro repair makes the equality check refuse the round-10 baseline (one named macro);
        the measurement gates still pass, because those read the records and not the macro revision. The
        two verdicts are separate fields, so a reader can see which one refused.
        """
        study = load(SPEC)
        level = next(item for item in driver.study_levels(study, 'mesh') if item['name'] == 'medium')
        equality = driver.reused_reference(BASELINE_EVIDENCE, BASELINE_TRIAL, {'reuse': level['reuse']},
                                          driver.level_case_spec(study, 'mesh', level), GEOMETRY)
        self.assertFalse(equality['ok'])
        self.assertTrue(any('AuditExternalFlowConvergence.java' in item for item in equality['differences']))
        gates = driver.level_validity(driver.collect_metrics(BASELINE_EVIDENCE, BASELINE_TRIAL),
                                     study['quality'], reused=True)
        self.assertTrue(gates['valid'], gates['failures'])

    def test_the_reuse_check_names_the_macro_revisions_whose_records_are_read(self):
        study = load(SPEC)
        level = next(item for item in driver.study_levels(study, 'mesh') if item['name'] == 'medium')
        result = driver.reused_reference(BASELINE_EVIDENCE, BASELINE_TRIAL, {'reuse': level['reuse']},
                                        driver.level_case_spec(study, 'mesh', level), GEOMETRY)
        self.assertIn('BuildExternalFlowMesh.java', result['reused_macros_measured_by_this_level'])
        # the round-10 run's AuditStaticBodyGeometry pin predates the round-12 repair; that stage produces
        # none of this level's quantities, so it must be RECORDED as differing elsewhere, not hidden
        self.assertIn('AuditStaticBodyGeometry.java', result['reused_macro_revisions_differing_elsewhere'])
        self.assertIn('reused_run_macro_hashes', result['compared'])


class ThreeLevelsRunningIsNotAConclusion(unittest.TestCase):
    """A large adjacent change across three valid levels must NOT become ESTABLISHED.

    The round-13 review's mutation "force sensitivity_passed True" survived the earlier suite because every
    fixture happened to pass. This is the missing direction: the comparison reports failure and the study
    record, the independence flags and the report must all say so.
    """

    def quality(self):
        return load(SPEC)['quality']

    def rows(self, drags):
        return [{'level': name, 'cells': cells, 'domain_scale': None,
                 'drag_coefficient_estimate': drag, 'drag_x_n': drag * 1.04, 'density_kg_m3': 1.1766,
                 'valid': True, 'validity': {'valid': True, 'failures': []}}
                for name, cells, drag in zip(('coarse', 'medium', 'fine'), (30_000, 58_724, 115_000), drags)]

    def test_a_change_beyond_the_casespec_limit_is_reported_as_failed_not_established(self):
        record = driver.assemble_study({'quality': self.quality()}, 'mesh',
                                       self.rows((0.50, 0.43, 0.4295)), None)
        self.assertFalse(record['sensitivity_passed'])
        self.assertEqual('FAILED', record['sensitivity_status'])
        self.assertFalse(record['mesh_independent'])
        comparison = record['comparison']
        self.assertFalse(comparison['passed'])
        self.assertGreater(comparison['quantity_comparisons']['drag_coefficient_estimate']
                           ['maximum_relative_change'], record['relative_change_limit'])

    def test_the_report_says_the_same_thing_as_the_record(self):
        import tempfile
        record = driver.assemble_study({'quality': self.quality()}, 'mesh',
                                       self.rows((0.50, 0.43, 0.4295)), None)
        with tempfile.TemporaryDirectory() as tmp:
            text = driver.report_markdown({'quality': self.quality()}, record,
                                          self.rows((0.50, 0.43, 0.4295)), None)
        self.assertIn('FAILED', text)
        self.assertIn('mesh_independent` = **False**', text)
        self.assertNotIn('**ESTABLISHED**', text)


class EveryNewRefusalPathHasANegativeTest(unittest.TestCase):
    """The round-13 review's second pass found three refusal paths with no negative test.

    Each is driven here through the code path that would be used in a real study, not by calling a helper
    out of context: the citation-gate fallback, the macro-revision refusal, and the assembled row that has
    to mark a failed reuse invalid.
    """

    def write_reuse_fixture(self, tmp, tamper_macro=None, strip_macro_hashes=False, bad_record=None):
        """A copy of the real round-10 reuse target, optionally broken in one specific way."""
        import shutil
        evidence = Path(tmp) / 'evidence'
        trial = evidence / BASELINE_TRIAL
        trial.mkdir(parents=True)
        shutil.copy2(BASELINE_EVIDENCE / BASELINE_TRIAL / 'case_spec.json', trial / 'case_spec.json')
        config = load(BASELINE_EVIDENCE / ('%s-config.json' % BASELINE_TRIAL))
        for name in ('external_flow_mesh_quality.json', 'external_flow_solve.json',
                     'external_flow_validation.json'):
            shutil.copy2(BASELINE_EVIDENCE / BASELINE_TRIAL / name, trial / name)
        if bad_record:
            payload = load(trial / bad_record)
            payload['status'] = 'error'
            (trial / bad_record).write_text(json.dumps(payload), encoding='utf-8-sig')
        if strip_macro_hashes:
            config.pop('macro_hashes', None)
        if tamper_macro:
            config['macro_hashes'][tamper_macro] = '0' * 64
        (evidence / ('%s-config.json' % BASELINE_TRIAL)).write_text(
            json.dumps(config), encoding='utf-8')
        (evidence / ('%s-report.json' % BASELINE_TRIAL)).write_text(
            json.dumps({'report_verdict': 'REPORT_OK'}), encoding='utf-8')
        (evidence / ('%s-result.json' % BASELINE_TRIAL)).write_text(
            json.dumps({'state': 'AIR3B_PIPELINE_COMPLETE'}), encoding='utf-8')
        return evidence

    def level(self):
        study = load(SPEC)
        return next(item for item in driver.study_levels(study, 'mesh') if item['name'] == 'medium')

    def test_a_reused_record_that_does_not_claim_success_closes_the_level(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            evidence = self.write_reuse_fixture(tmp, bad_record='external_flow_solve.json')
            metrics = driver.collect_metrics(evidence, BASELINE_TRIAL)
            result = driver.level_validity(metrics, load(SPEC)['quality'], reused=True)
            self.assertFalse(result['valid'])
            self.assertTrue(any('not citable' in failure or 'citable' in failure
                                for failure in result['failures']), result['failures'])

    def test_a_computed_level_without_a_delivery_record_is_closed(self):
        # the fallback must NOT apply to a computed level: no delivery record means unverified
        metrics = driver.collect_metrics(BASELINE_EVIDENCE, BASELINE_TRIAL)
        self.assertIsNone(metrics.get('checkpoints'))
        result = driver.level_validity(metrics, load(SPEC)['quality'], reused=False)
        self.assertFalse(result['valid'])
        self.assertIn('checkpoint delivery record missing for a computed level', result['failures'])

    def test_a_reused_run_with_a_tampered_measured_macro_is_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            evidence = self.write_reuse_fixture(tmp, tamper_macro='RunExternalFlowSolve.java')
            study = load(SPEC)
            level = self.level()
            ref = dict(level['reuse'], evidence=str(evidence))
            result = driver.reused_reference(evidence, BASELINE_TRIAL, {'reuse': ref},
                                            driver.level_case_spec(study, 'mesh', level), GEOMETRY)
            self.assertFalse(result['ok'])
            self.assertTrue(any('RunExternalFlowSolve.java' in item for item in result['differences']),
                            result['differences'])

    def test_a_reused_run_without_macro_provenance_is_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            evidence = self.write_reuse_fixture(tmp, strip_macro_hashes=True)
            study = load(SPEC)
            level = self.level()
            ref = dict(level['reuse'], evidence=str(evidence))
            result = driver.reused_reference(evidence, BASELINE_TRIAL, {'reuse': ref},
                                            driver.level_case_spec(study, 'mesh', level), GEOMETRY)
            self.assertFalse(result['ok'])
            self.assertTrue(any('macro_hashes' in item for item in result['differences']),
                            result['differences'])

    def test_the_assembled_row_marks_a_failed_reuse_invalid(self):
        # the path main() uses: a reuse whose configuration does not match must produce an INVALID row
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            evidence = self.write_reuse_fixture(tmp)
            descriptor = {'kind': 'mesh', 'level': 'medium', 'dir': str(evidence / 'medium'),
                          'reuse': {'trial_id': BASELINE_TRIAL, 'evidence': str(evidence)},
                          'case_spec': str(Path(tmp) / 'medium-case_spec.json')}
            study = load(SPEC)
            level = self.level()
            generated = driver.level_case_spec(study, 'mesh', level)
            generated['mesh_base_size_m'] = 0.099        # the reuse target does not match
            Path(descriptor['case_spec']).write_text(json.dumps(generated), encoding='utf-8')
            rows = driver.assemble_rows(study, 'mesh', [descriptor], evidence, BASELINE_TRIAL, GEOMETRY,
                                        study['domain']['outer_bounds_m'])
            self.assertEqual(1, len(rows))
            self.assertFalse(rows[0]['valid'])
            self.assertFalse(rows[0]['validity']['reuse_equality_ok'])
            self.assertTrue(any('reuse verification failed' in item
                                for item in rows[0]['validity']['failures']))
            record = driver.assemble_study(study, 'mesh', rows, study['domain']['outer_bounds_m'])
            self.assertEqual('NOT_ESTABLISHED', record['sensitivity_status'])

    def test_both_independence_flags_are_present_in_every_study_shape(self):
        study = load(SPEC)
        for kind, rows in (('mesh', self.mesh_rows()), ('domain', self.domain_rows())):
            record = driver.assemble_study(study, kind, rows, study['domain']['outer_bounds_m'])
            self.assertIn('mesh_independent', record, kind)
            self.assertIn('domain_independent', record, kind)
            self.assertIsInstance(record['mesh_independent'], bool, kind)
            self.assertIsInstance(record['domain_independent'], bool, kind)

    def mesh_rows(self, drags=(0.4300, 0.4296, 0.4295)):
        return [{'level': name, 'cells': cells, 'domain_scale': None,
                 'drag_coefficient_estimate': drag, 'drag_x_n': drag * 1.04, 'density_kg_m3': 1.1766,
                 'valid': True, 'validity': {'valid': True, 'failures': []}}
                for name, cells, drag in zip(('coarse', 'medium', 'fine'),
                                             (30_000, 58_724, 115_000), drags)]

    def domain_rows(self):
        rows = self.mesh_rows()
        for index, row in enumerate(rows):
            row['domain_scale'] = (1.0, 1.25, 1.5)[index]
            row['cells'] = (58_724, 115_000, 198_000)[index]
        return rows


class NoLevelSpecKeyCanHijackAMacroRead(unittest.TestCase):
    """Round 13's real defect, turned into a test: the macros read the CaseSpec with a FIRST-MATCH regex.

    `BuildExternalFlowMesh.number(spec, "mesh_base_size_m")` is `Pattern.compile(...).find()` over the whole
    file, so the FIRST textual occurrence wins. The first version of the study spec declared the levels with
    the macro-visible names inside quality.mesh_study - and because `quality` is written BEFORE the
    top-level `mesh_base_size_m` fields, EVERY level read the coarse level's 0.025. Measured consequence:
    the fine level built the coarse mesh (45324 cells, identical drag to the coarse level), and all four
    domain levels meshed at 0.025 instead of 0.02. The invariants below are what would have caught it.
    """

    MACROS = ('AuditStaticBodyGeometry.java', 'BuildExternalDomain.java', 'BuildExternalFlowRegion.java',
              'ConfigureExternalFlowPhysics.java', 'BuildExternalFlowMesh.java',
              'AuditExternalFlowMesh.java', 'RunExternalFlowSolve.java',
              'AuditExternalFlowConvergence.java')
    # every helper the eight macros use to pull a value out of the CaseSpec text. `optional` and
    # `required` are the region/physics/solve/validation macros' helpers; the others are the geometry and
    # mesh macros'. A helper missing from this list is a blind spot in this test, so the key-set assertion
    # below fails if a known key stops being found.
    CALLS = ('stringValue', 'numberValue', 'arrayValue', 'optionalString', 'number', 'string',
             'arrayStrings', 'optional', 'required')

    def macro_parsed_keys(self):
        import re
        keys = set()
        for name in self.MACROS:
            source = (ROOT / 'macros' / name).read_text(encoding='utf-8')
            for call in self.CALLS:
                for match in re.finditer(r'\b%s\(\s*spec\s*,\s*"([A-Za-z0-9_]+)"' % call, source):
                    keys.add(match.group(1))
        return keys

    def generated_level_specs(self):
        for root in (MESH_ROOT, DOMAIN_ROOT):
            for path in sorted(root.glob('*/case_spec.json')):
                yield path

    def test_the_macro_parsed_key_set_is_what_this_test_thinks_it_is(self):
        keys = self.macro_parsed_keys()
        for expected in ('mesh_base_size_m', 'mesh_body_target_size_m', 'mesh_body_minimum_size_m',
                         'outer_bounds_m', 'body_radius_m', 'model_length_unit', 'region_name'):
            self.assertIn(expected, keys)

    def test_the_study_blocks_do_not_reuse_any_macro_parsed_key_name(self):
        spec = load(SPEC)
        keys = self.macro_parsed_keys()
        for block in ('mesh_study', 'domain_study'):
            text = json.dumps(spec['quality'][block])
            for key in sorted(keys):
                self.assertNotIn('"%s"' % key, text,
                                 'quality.%s declares the macro-visible key %r; the macros read the first '
                                 'textual occurrence, so this would hijack the level value' % (block, key))

    def test_every_macro_parsed_key_occurs_once_in_a_generated_level_spec(self):
        spec = load(SPEC)
        keys = self.macro_parsed_keys()
        for path in self.generated_level_specs():
            text = path.read_text(encoding='utf-8')
            level = json.loads(text)['_study_level']
            for key in sorted(keys):
                count = text.count('"%s"' % key)
                if count == 0:
                    continue
                self.assertEqual(1, count,
                                 '%s declares %r %d times; the macros take the FIRST occurrence, so the '
                                 'level value is not the one that is read (level %s/%s)'
                                 % (path, key, count, level['study'], level['name']))

    def test_the_first_occurrence_carries_the_levels_own_value(self):
        """Non-circular: the expectation comes from the STUDY SPEC's own declared level values.

        The round-13 review's surviving mutation injected a macro-visible key into the study spec at
        `quality` level and killed nothing, because the earlier revision derived its expectation from
        `driver.level_case_spec(...)` - the very generator under test - so both sides moved together. The
        declared values (`base_size_m` / `body_target_size_m` / `body_minimum_size_m` / `bounds_m`) are read
        straight from the spec here and are never passed through the generator.
        """
        import re
        spec = load(SPEC)
        for kind in ('mesh', 'domain'):
            for level in driver.study_levels(spec, kind):
                path = (MESH_ROOT if kind == 'mesh' else DOMAIN_ROOT) / level['name'] / 'case_spec.json'
                text = path.read_text(encoding='utf-8')
                if kind == 'mesh':
                    expected = {'mesh_base_size_m': level['base_size_m'],
                                'mesh_body_target_size_m': level['body_target_size_m'],
                                'mesh_body_minimum_size_m': level['body_minimum_size_m']}
                else:
                    expected = {'outer_bounds_m': level['bounds_m']}
                for key, value in expected.items():
                    if isinstance(value, list):
                        match = re.search(r'"%s"\s*:\s*\[([^\]]*)\]' % key, text)
                        first = [float(v) for v in match.group(1).replace('\n', '').split(',')]
                    else:
                        match = re.search(r'"%s"\s*:\s*([-+0-9.eE]+)' % key, text)
                        first = float(match.group(1))
                    self.assertEqual(value, first,
                                     '%s/%s: the first %r the macros would read is %r, not the declared %r'
                                     % (kind, level['name'], key, first, value))

    def test_the_generator_cannot_emit_a_duplicate_macro_key_anywhere_in_the_spec(self):
        """The strongest form: serialise what the driver generates and scan the WHOLE text.

        This does not depend on the committed files, so an injected macro-visible key anywhere in the study
        spec (including outside the two study blocks, where the review's mutation survived) is caught.
        """
        spec = load(SPEC)
        keys = self.macro_parsed_keys()
        for kind in ('mesh', 'domain'):
            for level in driver.study_levels(spec, kind):
                generated = driver.level_case_spec(spec, kind, level)
                text = json.dumps(generated, ensure_ascii=False, indent=2)
                for key in sorted(keys):
                    count = text.count('"%s"' % key)
                    if count == 0:
                        continue
                    self.assertEqual(1, count,
                                     'the generated %s/%s spec declares %r %d times; the macros take the '
                                     'FIRST occurrence' % (kind, level['name'], key, count))


class CurrentSourceReleaseRegressions(unittest.TestCase):
    C = ROOT / 'cases/static-reusable-entry/window1/release-candidate'
    def test_explicit_pipeline_manifest_keeps_baseline_not_last_level(self):
        study = load(self.C / 'sphere-study.json')
        pipeline = self.C / 'sphere-pipeline.json'
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            driver.prepare(self.C / 'sphere-study.json', 'mesh', GEOMETRY, out,
                           'regression', load(pipeline)['macro_hashes'], pipeline_path=pipeline,
                           source_contract=self.C / 'sphere-source.json')
            manifest = load(out / 'mesh-levels.json')
            self.assertEqual(str(pipeline), manifest['pipeline'])
            self.assertEqual(driver.sha256(pipeline), manifest['pipeline_sha256'])
            for desc in manifest['levels']:
                self.assertEqual(load(pipeline)['stages'], load(desc['pipeline'])['stages'])
            summary = driver.assemble(self.C / 'sphere-study.json', 'mesh', out)
            self.assertEqual('NOT_ESTABLISHED', summary['sensitivity_status'])
            self.assertEqual(3, len(summary['excluded_levels']))

    def test_assemble_only_cli_does_not_regenerate_pinned_levels(self):
        from unittest.mock import patch
        import contextlib, io
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);pipeline=self.C/'sphere-pipeline.json'
            driver.prepare(self.C/'sphere-study.json','mesh',GEOMETRY,out,'cli',
                           load(pipeline)['macro_hashes'],pipeline_path=pipeline,
                           source_contract=self.C/'sphere-source.json')
            before=driver.sha256(out/'mesh-levels.json')
            argv=['study','--kind','mesh','--spec',str(self.C/'sphere-study.json'),
                  '--geometry',str(GEOMETRY),'--evidence',str(out),'--assemble']
            with patch.object(sys,'argv',argv),patch.object(driver,'prepare',side_effect=AssertionError('must not prepare')):
                with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(0,driver.main())
            self.assertEqual(before,driver.sha256(out/'mesh-levels.json'))

    def test_current_source_contract_rejects_foreign_geometry_before_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                driver.prepare(self.C / 'cylinder-study.json', 'mesh', GEOMETRY, Path(tmp),
                               'foreign', {}, pipeline_path=self.C / 'cylinder-pipeline.json',
                               source_contract=self.C / 'cylinder-source.json')
            self.assertFalse((Path(tmp) / 'mesh-levels.json').exists())

    def test_real_u12_baseline_is_excluded_for_nonconvergence(self):
        root = ROOT / 'test-evidence/static-reusable-entry/desktop-positive-f1'
        metrics = driver.collect_metrics(root, 'static-reusable-u12-20261006a')
        result = driver.level_validity(metrics, load(self.C / 'cylinder-study.json')['quality'], reused=True)
        self.assertFalse(result['valid'])
        self.assertIn('solve.residuals_met is not true', result['failures'])

    def test_nan_and_missing_bound_close_level_instead_of_crashing(self):
        metrics = driver.collect_metrics(BASELINE_EVIDENCE, BASELINE_TRIAL)
        metrics['quality']['measured_cell_quality_min'] = float('nan')
        quality = load(SPEC)['quality'];quality.pop('mesh_min_face_validity')
        result = driver.level_validity(metrics, quality, reused=True)
        self.assertFalse(result['valid'])
        self.assertTrue(any('nonfinite' in x for x in result['failures']))

    def test_changed_geometry_metadata_refuses_byte_identical_source_reuse(self):
        generated = driver.level_case_spec(load(SPEC), 'mesh',
                                          next(x for x in driver.study_levels(load(SPEC),'mesh') if x['name']=='medium'))
        generated['geometry']['reference_area_m2'] *= 2
        reuse = {'evidence': str(BASELINE_EVIDENCE), 'trial_id': BASELINE_TRIAL}
        result = driver.reused_reference(BASELINE_EVIDENCE, BASELINE_TRIAL, {'reuse': reuse}, generated, GEOMETRY)
        self.assertFalse(result['ok']);self.assertIn('geometry', result['differences'])

    def test_report_excludes_foreign_study_and_property_records(self):
        E = ROOT / 'test-evidence/static-reusable-entry/window1-release-candidate'
        with tempfile.TemporaryDirectory() as tmp:
            result = driver.completion_report(self.C / 'cylinder-study.json',
                     ROOT / 'test-evidence/static-reusable-entry/desktop-positive-f1',
                     'static-reusable-u12-20261006a',
                     {'mesh': E/'sphere/mesh/mesh-study-report.json'}, Path(tmp),
                     ROOT/'test-evidence/static-cylinder-step/r30-readback/static-cylinder-r30-readback-20261006a/property_nearwall_readback.json')
            self.assertFalse(result['studies']['mesh']['applicable_to_current_case'])
            self.assertFalse(result['research_complete'])
            self.assertEqual('NOT_ESTABLISHED', result['property_nearwall']['status'])
            self.assertEqual({}, result['property_nearwall']['properties'])
            self.assertEqual('NOT_CONVERGED', result['validation_verdict'])

    def test_missing_single_measured_macro_pin_refuses_reuse(self):
        helper = EveryNewRefusalPathHasANegativeTest()
        with tempfile.TemporaryDirectory() as tmp:
            evidence = helper.write_reuse_fixture(tmp)
            path=evidence / (BASELINE_TRIAL+'-config.json');config=load(path)
            config['macro_hashes'].pop('RunExternalFlowSolve.java')
            path.write_text(json.dumps(config))
            spec=load(SPEC);level=helper.level()
            result=driver.reused_reference(evidence,BASELINE_TRIAL,
                   {'reuse': {'evidence': str(evidence), 'trial_id': BASELINE_TRIAL}},
                   driver.level_case_spec(spec,'mesh',level),GEOMETRY)
            self.assertFalse(result['ok'])
            self.assertTrue(any('RunExternalFlowSolve.java' in x for x in result['differences']))

    def test_tampered_baseline_spec_pin_refuses_reuse(self):
        helper = EveryNewRefusalPathHasANegativeTest()
        with tempfile.TemporaryDirectory() as tmp:
            evidence = helper.write_reuse_fixture(tmp)
            path=evidence/BASELINE_TRIAL/'case_spec.json';path.write_text(path.read_text()+' ')
            result=driver.reused_reference(evidence,BASELINE_TRIAL,
                   {'reuse': {'evidence': str(evidence), 'trial_id': BASELINE_TRIAL}},
                   driver.level_case_spec(load(SPEC),'mesh',helper.level()),GEOMETRY)
            self.assertFalse(result['ok'])
            self.assertTrue(any('executed input pin' in x for x in result['differences']))

    def test_missing_or_nonfinite_study_quantity_closes_in_summary(self):
        rows=EveryNewRefusalPathHasANegativeTest().mesh_rows()
        rows[0]['drag_x_n']=float('nan')
        result=driver.assemble_study(load(SPEC),'mesh',rows,None)
        self.assertEqual('NOT_ESTABLISHED',result['sensitivity_status'])
        self.assertEqual(2,result['valid_level_count'])
        self.assertIn('nonfinite',str(result['excluded_levels']))

    def test_gci_numeric_diagnostic_cannot_certify_unverified_mesh_family(self):
        rows=EveryNewRefusalPathHasANegativeTest().mesh_rows((1.003,1.001,1.0))
        for row,n in zip(rows,[100,101,102]): row['cells']=n;row['density_kg_m3']=1
        spec=load(SPEC);spec['quality']['mesh_study']['refinement_ratio']=2
        result=driver.assemble_study(spec,'mesh',rows,None)
        self.assertFalse(result['mesh_independent'])
        self.assertIn('measured mesh-family similarity',result['gci_applicability_missing'])

    def test_current_report_names_current_study_and_persistent_entry(self):
        spec=load(self.C/'cylinder-study.json')
        record=driver.assemble_study(spec,'mesh',[],None)
        text=driver.report_markdown(spec,record,[])
        self.assertIn(spec['_study_id'],text)
        self.assertNotIn('①R13',text)
        self.assertIn('assemble_registered_workflow_studies',text)

    def test_tightened_quality_cannot_reuse_old_report_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec=load(self.C/'cylinder-study.json');spec['quality']['mass_imbalance_ratio_limit']=1e-30
            path=Path(tmp)/'case_spec.json';path.write_text(json.dumps(spec))
            result=driver.completion_report(path,ROOT/'test-evidence/static-reusable-entry/desktop-positive-f1',
                                          'static-reusable-u12-20261006a',{},Path(tmp)/'report')
            self.assertFalse(result['applicable_result'])
            self.assertEqual('NOT_ESTABLISHED',result['process_status'])

    def test_foreign_result_report_cannot_supply_current_flow_or_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=driver.completion_report(self.C/'cylinder-study.json',BASELINE_EVIDENCE,
                                          BASELINE_TRIAL,{},Path(tmp))
            self.assertFalse(result['applicable_result'])
            self.assertEqual('NOT_ESTABLISHED',result['process_status'])
            self.assertEqual('NOT_ESTABLISHED',result['delivery_status'])

    def test_translated_domain_levels_scale_about_current_centre(self):
        spec = load(self.C / 'translate-study.json')
        base = spec['domain']['outer_bounds_m'];origin=spec['geometry']['body_origin_m']
        for level in driver.study_levels(spec,'domain'):
            for i,v in enumerate(level['bounds_m']):
                self.assertAlmostEqual(origin[i%3]+(base[i]-origin[i%3])*level['extent_factor'],v)


class DesktopJointDeliveryReview(unittest.TestCase):
    """Real desktop 2026-10-07 intake fixtures; no new candidate or STAR execution."""
    def desktop(self):
        raw=load(ROOT/'test-evidence/static-release-candidate/desktop-20261007/positive-intake.json')
        return json.loads(raw['content'][0]['text'])

    def test_four_new_level_budget_excludes_parent_base_call(self):
        candidate=self.desktop()
        calls=[v for info in candidate['studies'].values() for v in info['levels'] if not v.get('reuse')]
        self.assertEqual(4,len(calls))
        self.assertEqual(1020,sum(v['star_seconds_cap'] for v in calls))
        self.assertEqual(1275,candidate['base_call']['star_seconds_cap']+sum(v['star_seconds_cap'] for v in calls))
        self.assertNotIn(candidate['base_call']['request_id'],[v['request_id'] for v in calls])
        for call in calls:
            manifest=load(ROOT/'registered-workflows-v2'/call['workflow_id']/'manifest.json')
            self.assertNotIn('studies',manifest)
            self.assertEqual('submit_registered_workflow',call['submit_tool'])

    def test_u12_identity_reuse_and_engineering_exclusion_remain_separate(self):
        candidate=self.desktop();spec=load(candidate['case_spec_path'])
        for kind,info in candidate['studies'].items():
            level=next(v for v in info['levels'] if v.get('reuse'))
            equality=driver.reused_reference(Path('.'),'',{'reuse':level['reuse']},
                                            load(level['case_spec']),Path(candidate['source_sim']))
            # Geometry/parameter identity is retained; new solve bytes forbid unchanged-pin reuse.
            if equality['ok']:
                self.assertEqual([],equality['differences'])
            else:
                # 2026-10-14：本轮还修订了 AuditExternalFlowConvergence.java（validation 记录的 reference_policy
                # 由写死字符串改为派生、注释措辞）。该宏的 before→after 与授权依据登记在
                # test-evidence/engineering-validation-start-20261009/time-budget-revocation-superseded-pins.json
                # 的 macro_repaired 段（macros/AuditExternalFlowConvergence.java）。
                self.assertEqual(sorted('macro revision that produced a recorded quantity differs: '+name for name in ('BuildExternalFlowMesh.java','AuditExternalFlowMesh.java','RunExternalFlowSolve.java','AuditExternalFlowConvergence.java')),sorted(equality['differences']))
            metrics=driver.collect_metrics(ROOT/level['reuse']['evidence'],level['reuse']['trial_id'])
            gates=driver.level_validity(metrics,spec['quality'],True)
            self.assertFalse(gates['valid'])
            self.assertIn('solve.residuals_met is not true',gates['failures'])
            self.assertEqual('NOT_CONVERGED',metrics['validation']['validation_verdict'])

    def test_four_successful_new_levels_cannot_replace_excluded_middle_level(self):
        spec=load(self.desktop()['case_spec_path'])
        rows=EveryNewRefusalPathHasANegativeTest().mesh_rows()
        rows[1].update(valid=False,validity={'valid':False,'failures':['real U12 NOT_CONVERGED']})
        result=driver.assemble_study(spec,'mesh',rows,None)
        self.assertEqual('NOT_ESTABLISHED',result['sensitivity_status'])
        self.assertFalse(result['mesh_independent'])
        for row,factor in zip(rows,[1.,1.25,1.5]):row['domain_scale']=factor
        result=driver.assemble_study(spec,'domain',rows,None)
        # Existing domain comparison allows two effective extents; that is not
        # three valid declared levels, GCI or engineering validation.
        self.assertEqual(2,result['valid_level_count'])
        self.assertFalse(result['all_declared_levels_valid'])
        self.assertFalse(result['physical_validation'])
        self.assertIsNone(result['gci'])

    def test_mm_source_admission_is_not_SI_export_compatibility(self):
        # This is a static contract counterexample, not a claimed STAR failure trial.
        spec=load(CurrentSourceReleaseRegressions.C/'units-mm-study.json')
        source=(ROOT/'macros/ExportExternalFlowResults.java').read_text()
        self.assertEqual('mm',spec['model_length_unit'])
        self.assertIn('if(!"m".equals(text(spec,"model_length_unit")))',source)
        self.assertIn('Requires SI metre CaseSpec',source)
        pipeline=load(CurrentSourceReleaseRegressions.C/'units-mm-pipeline.json')
        self.assertIn('ExportExternalFlowResults.java',pipeline['stages'][-1]['macros'])
        self.assertNotEqual('m',spec['model_length_unit'])

    def test_recovery_contract_pins_original_checkpoint_and_excludes_research_budget(self):
        contract=load(ROOT/'test-evidence/static-release-candidate/desktop-20261007/recovery-contract.json')
        probe=contract['future_finite_probe']
        import hashlib
        for path,key in [('input_checkpoint','input_sha256'),('executed_case_spec','case_spec_sha256')]:
            self.assertEqual(probe[key],hashlib.sha256((ROOT/probe[path]).read_bytes()).hexdigest())
        self.assertEqual(150,probe['expected_start_iteration'])
        self.assertEqual(200,probe['expected_start_iteration']+probe['additional_iterations'])
        self.assertEqual(95,sum(probe['proposed_stage_caps_seconds'].values()))
        self.assertFalse(probe['included_in_1020'])
        self.assertFalse(probe['registered'])
        self.assertIsNone(probe['run_id'])
        self.assertIsNone(probe['budget_allocation'])
        self.assertEqual('CONTRACT_ONLY_NOT_EXECUTABLE_NOT_AUTHORIZED',contract['state'])

    def test_recovery_additional50_cannot_reuse_full_chain150_iteration_gate(self):
        contract=load(ROOT/'test-evidence/static-release-candidate/desktop-20261007/recovery-contract.json')
        candidate=self.desktop()
        level=next(v for v in candidate['studies']['mesh']['levels'] if not v.get('reuse'))
        pipeline=load(ROOT/'registered-workflows-v2'/level['workflow_id']/'pipeline.json')
        self.assertIn('external_flow_solve.json::iterations_run>=150',str(pipeline))
        self.assertLess(contract['future_finite_probe']['additional_iterations'],150)
        review=load(ROOT/'test-evidence/static-release-candidate/window1-desktop-review-20261007/recovery-review.json')
        self.assertEqual('CONTRACT_IDENTITY_REVIEWED_EXECUTION_MAPPING_UNESTABLISHED',review['state'])
        self.assertFalse(review['recovery_actual_trial'])
        self.assertTrue(review['download_is_not_solver_recovery'])


class ActualFourStudyFailureReview(unittest.TestCase):
    C=ROOT/'test-evidence/static-release-candidate/static-rc1-desktop-20261007'

    def test_actual_domain_ok_claim_in_failed_stage_is_excluded(self):
        level=self.C/'domain/d1p25';run='static-rc1-desktop-20261007-domain-d1p25-rc1'
        metrics=driver.collect_metrics(level,run)
        self.assertEqual('ok',metrics['solve']['status'])
        path=level/run/'external_flow_solve.json'
        self.assertEqual(driver.run_record_gate.NOT_CITABLE_FAILED_RUN,
                         driver.run_record_gate.citation_status(path)['state'])
        spec=load(level/'case_spec.json')
        validity=driver.level_validity(metrics,spec['quality'])
        self.assertFalse(validity['valid'])
        self.assertIn('measurement record not citable: external_flow_solve.json',validity['failures'])
        self.assertIsNone(metrics['validation'])
        self.assertIn('measurement record not citable: external_flow_validation.json',validity['failures'])

    def test_actual_coarse_complete_flow_is_not_valid_engineering_level(self):
        level=self.C/'mesh/coarse';run='static-rc1-desktop-20261007-mesh-coarse-rc1'
        metrics=driver.collect_metrics(level,run)
        self.assertEqual('AIR3B_PIPELINE_COMPLETE',metrics['result']['state'])
        self.assertEqual('REPORT_OK',metrics['report']['report_verdict'])
        self.assertEqual('NOT_CONVERGED',metrics['validation']['validation_verdict'])
        self.assertEqual(57694,metrics['quality']['cells'])
        spec=load(level/'case_spec.json')
        self.assertFalse(driver.level_validity(metrics,spec['quality'])['valid'])

    def test_actual_partial_package_cannot_establish_research(self):
        spec=load(self.C/'case_spec.json')
        coarse=driver.collect_metrics(self.C/'mesh/coarse','static-rc1-desktop-20261007-mesh-coarse-rc1')
        failed=driver.collect_metrics(self.C/'domain/d1p25','static-rc1-desktop-20261007-domain-d1p25-rc1')
        for kind,metrics in [('mesh',coarse),('domain',failed)]:
            row=driver.level_row(metrics,kind,spec['domain']['outer_bounds_m'],spec['quality'])
            result=driver.assemble_study(spec,kind,[row],spec['domain']['outer_bounds_m'])
            self.assertEqual(0,result['valid_level_count'])
            self.assertEqual('NOT_ESTABLISHED',result['sensitivity_status'])
            self.assertFalse(result['physical_validation'])


class EngineeringPreparationContract(unittest.TestCase):
    def test_property_template_uses_controller_memory_contract(self):
        pipeline=load(ROOT/'cases/static-cylinder-step/engineering-preparation-20261007/property-pipeline-template.json')
        self.assertEqual(64,pipeline['memory_gb'])
        self.assertNotIn('memory_limit_gb',pipeline)
        self.assertEqual('1',pipeline['env']['PROPERTY_NEARWALL_READBACK_ONLY'])
        self.assertEqual(['ExportExternalFlowResults.java'],pipeline['stages'][0]['macros'])
        self.assertIsNone(pipeline['stages'][0]['output'])
        self.assertEqual(60,pipeline['stages'][0]['timeout_seconds'])

    def test_engineering_domain_requires_all_three_declared_valid_levels(self):
        spec=load(SPEC)
        spec['quality']['domain_study']={'require_all_declared_levels':True,
            'levels':[{'name':'d1p00','extent_factor':1},{'name':'d1p25','extent_factor':1.25},{'name':'d1p50','extent_factor':1.5}]}
        rows=EveryNewRefusalPathHasANegativeTest().mesh_rows()
        for row,name,factor in zip(rows,['d1p00','d1p25','d1p50'],[1,1.25,1.5]):
            row['level']=name;row['domain_scale']=factor;row['density_kg_m3']=1
        rows[0]['valid']=False
        result=driver.assemble_study(spec,'domain',rows,None)
        self.assertEqual(3,result['minimum_levels_required'])
        self.assertEqual('NOT_ESTABLISHED',result['sensitivity_status'])
        self.assertFalse(result['domain_independent'])

    def test_missing_or_duplicated_declared_levels_cannot_complete_engineering_plan(self):
        spec=load(SPEC);spec['quality']['domain_study']={'require_all_declared_levels':True,
            'levels':[{'name':'a','extent_factor':1},{'name':'b','extent_factor':1.25},{'name':'c','extent_factor':1.5}]}
        rows=EveryNewRefusalPathHasANegativeTest().mesh_rows()
        for row in rows:row['level']='a';row['domain_scale']=1;row['density_kg_m3']=1
        result=driver.assemble_study(spec,'domain',rows,None)
        self.assertEqual('NOT_ESTABLISHED',result['sensitivity_status'])
        self.assertFalse(result['all_declared_levels_valid'])

    def test_adjacent_measured_refinement_ratios_do_not_use_coarse_fine_span(self):
        spec=load(SPEC);rows=EveryNewRefusalPathHasANegativeTest().mesh_rows((1.003,1.001,1.0))
        for row,n in zip(rows,[100,800,6400]):row['cells']=n;row['density_kg_m3']=1
        result=driver.assemble_study(spec,'mesh',rows,None)
        self.assertEqual([2.,2.],result['gci_adjacent_ratios_from_cells'])
        self.assertAlmostEqual(4.,result['gci_ratio_from_cells'])
        self.assertFalse(result['mesh_independent'])


class CheckpointStudyLineage(unittest.TestCase):
    def fixture(self, directory):
        root=Path(directory); run=root/'segment';run.mkdir()
        contract=ROOT/'test-evidence/static-release-candidate/engineering-contract-20261007/u12-checkpoint-contract.json'
        value=load(contract);parent=load(ROOT/value['executed_case_spec'])
        (run/'case_spec.json').write_text(json.dumps(parent))
        record=driver.read_json(ROOT/value['checkpoint_record']);stage=driver.read_json((ROOT/value['checkpoint_record']).parent/'stage-solve.json')
        (run/'external_flow_solve.json').write_text(json.dumps({'start_iteration':record['end_iteration']}))
        quality=load((ROOT/value['checkpoint_record']).parent/'external_flow_mesh_quality.json')
        (run/'external_flow_validation.json').write_text(json.dumps({'cells':quality['cells']}))
        (root/'segment-config.json').write_text(json.dumps({'input_hashes':{
            'case_spec.json':driver.sha256(run/'case_spec.json'),'solve_input_checkpoint.sim':stage['output_sha256']}}))
        manifest=root/'manifest.json';manifest.write_text(json.dumps({'checkpoint_contract':str(contract),
            'adapter_files':[str(contract.relative_to(ROOT))]}))
        parent_record=ROOT/value['checkpoint_record']
        pinned=[contract,parent_record,parent_record.parent/'stage-solve.json',ROOT/value['executed_case_spec'],
                ROOT/value['executed_config'],parent_record.parent.parent/'pipeline-review.json']
        for name in ('static_body_audit.json','external_flow_mesh.json','external_flow_mesh_quality.json',
                     'external_domain_build.json','external_flow_physics.json'):
            path=parent_record.parent/name;pinned.append(path)
            pinned.append(Path(driver.run_record_gate.citation_status(path)['stage_record']))
        (root/'pipeline-review.json').write_text(json.dumps({'approved':True,'independent':True,
            'input_hashes':load(root/'segment-config.json')['input_hashes'],
            'registration_hashes':{str(path.relative_to(ROOT)):driver.sha256(path) for path in pinned+[manifest]}}))
        (root/'candidate.json').write_text(json.dumps({'source_kind':'successful_solve_checkpoint','manifest':str(manifest)}))
        review=load(root/'pipeline-review.json');review['registration_hashes'][str((root/'candidate.json').relative_to(ROOT))]=driver.sha256(root/'candidate.json')
        (root/'pipeline-review.json').write_text(json.dumps(review))
        return root,run,value

    def test_real_u12_preprocessing_is_read_by_original_certificate_path(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'test-evidence') as tmp:
            root,run,value=self.fixture(tmp)
            result=driver.checkpoint_preprocessing(root,'segment')
            self.assertEqual(str((ROOT/value['checkpoint_record']).parent/'external_flow_mesh_quality.json'),
                             result['record_paths']['external_flow_mesh_quality.json'])
            self.assertFalse(result['engineering_conclusion_reused'])
            self.assertFalse((run/'external_flow_mesh_quality.json').exists())

    def test_changed_mesh_and_start_are_refused(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'test-evidence') as tmp:
            root,run,_=self.fixture(tmp);spec=load(run/'case_spec.json');spec['mesh_base_size_m']*=2
            (run/'case_spec.json').write_text(json.dumps(spec));config=load(root/'segment-config.json')
            config['input_hashes']['case_spec.json']=driver.sha256(run/'case_spec.json')
            (root/'segment-config.json').write_text(json.dumps(config))
            review=load(root/'pipeline-review.json');review['input_hashes']=config['input_hashes']
            (root/'pipeline-review.json').write_text(json.dumps(review))
            with self.assertRaisesRegex(ValueError,'physical or mesh'):driver.checkpoint_preprocessing(root,'segment')
            metrics=driver.collect_metrics(root,'segment')
            self.assertIn('checkpoint_lineage_error',metrics)
            self.assertFalse(driver.level_validity(metrics,load(SPEC)['quality'])['valid'])

    def test_terminal_download_cannot_replace_checkpoint_stage_hash(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'test-evidence') as tmp:
            root,run,_=self.fixture(tmp);config=load(root/'segment-config.json')
            config['input_hashes']['solve_input_checkpoint.sim']='download-only'
            (root/'segment-config.json').write_text(json.dumps(config))
            review=load(root/'pipeline-review.json');review['input_hashes']=config['input_hashes']
            (root/'pipeline-review.json').write_text(json.dumps(review))
            with self.assertRaisesRegex(ValueError,'successful stage output'):driver.checkpoint_preprocessing(root,'segment')


    def test_unapproved_or_changed_contract_is_refused(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'test-evidence') as tmp:
            root,_,_=self.fixture(tmp);review=load(root/'pipeline-review.json');review['registration_hashes']={}
            (root/'pipeline-review.json').write_text(json.dumps(review))
            with self.assertRaisesRegex(ValueError,'review hash differs|candidate content binding'):driver.checkpoint_preprocessing(root,'segment')

    def test_real_failed_domain_record_remains_visible_in_summary(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'test-evidence') as tmp:
            root,_,value=self.fixture(tmp)
            failed=list((ROOT/'test-evidence/static-release-candidate').rglob('cyl-rc1-domain-d1p25-20261007a/external_flow_solve.json'))
            if not failed:
                failed=[p for p in (ROOT/'test-evidence/static-release-candidate').rglob('external_flow_solve.json')
                        if 'domain-d1p25' in str(p) and driver.run_record_gate.citation_status(p)['state']!='CITABLE']
            self.assertTrue(failed)
            value['checkpoint_record']=str(failed[0]);contract=root/'failed-contract.json';contract.write_text(json.dumps(value))
            manifest=root/'manifest.json';v=load(manifest);v['checkpoint_contract']=str(contract);v['adapter_files']=[str(contract.relative_to(ROOT))]
            manifest.write_text(json.dumps(v))
            (root/'pipeline-review.json').write_text(json.dumps({'approved':True,'independent':True,
                'registration_hashes':{str(manifest.relative_to(ROOT)):driver.sha256(manifest),str(contract.relative_to(ROOT)):driver.sha256(contract)}}))
            metrics=driver.collect_metrics(root,'segment')
            self.assertIn('checkpoint_lineage_error',metrics)
            self.assertFalse(driver.level_validity(metrics,load(SPEC)['quality'])['valid'])


class CheckpointCertificateContentBinding(unittest.TestCase):
    def fixture(self, directory):
        import shutil
        root, run, value = CheckpointStudyLineage().fixture(directory)
        original_parent = (ROOT / value['checkpoint_record']).parent
        evidence = root / 'parent'; parent = evidence / original_parent.name; parent.mkdir(parents=True)
        for path in original_parent.glob('*.json'):
            shutil.copyfile(path, parent / path.name)
        config = evidence / (original_parent.name + '-config.json')
        shutil.copyfile(ROOT / value['executed_config'], config)
        shutil.copyfile(original_parent.parent / 'pipeline-review.json', evidence / 'pipeline-review.json')
        spec = evidence / 'executed_case_spec.json'; shutil.copyfile(ROOT / value['executed_case_spec'], spec)
        value.update(checkpoint_record=str(parent / 'external_flow_solve.json'), executed_config=str(config), executed_case_spec=str(spec))
        contract = root / 'contract.json'; contract.write_text(json.dumps(value))
        pins = list(parent.glob('*.json')) + [config, spec, evidence / 'pipeline-review.json', contract]
        manifest = root / 'manifest.json'; manifest.write_text(json.dumps({'checkpoint_contract': str(contract), 'adapter_files': [str(p.relative_to(ROOT)) for p in pins]}))
        review = load(root / 'pipeline-review.json')
        review['registration_hashes'] = {str(p.relative_to(ROOT)): driver.sha256(p) for p in pins + [manifest]}
        review['registration_hashes'][str((root/'candidate.json').relative_to(ROOT))]=driver.sha256(root/'candidate.json')
        (root / 'pipeline-review.json').write_text(json.dumps(review))
        driver.checkpoint_preprocessing(root, 'segment')
        return root, parent

    def test_changed_candidate_source_kind_cannot_skip_the_existing_approval_pin(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'test-evidence') as tmp:
            root,_,_=CheckpointStudyLineage().fixture(tmp)
            path=root/'candidate.json';candidate=load(path);candidate['source_kind']='step_cad';path.write_text(json.dumps(candidate))
            with self.assertRaisesRegex(ValueError,'before source-kind selection'):
                driver.checkpoint_preprocessing(root,'segment')

    def test_true_parent_solve_and_physics_content_mutations_are_refused(self):
        for name, key, value in [('external_flow_solve.json', 'measured_drag_x_n_final', 3.9),
                                 ('external_flow_physics.json', 'actual_inlet_speed_m_s', 15)]:
            with tempfile.TemporaryDirectory(dir=ROOT / 'test-evidence') as tmp:
                root, parent = self.fixture(tmp); path = parent / name; record = load(path); record[key] = value
                path.write_text(json.dumps(record))
                with self.assertRaisesRegex(ValueError, 'certificate content binding'):
                    driver.checkpoint_preprocessing(root, 'segment')

    def test_missing_inherited_certificate_pin_is_refused(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'test-evidence') as tmp:
            root, parent = self.fixture(tmp); review = load(root / 'pipeline-review.json')
            review['registration_hashes'].pop(str((parent / 'external_flow_physics.json').relative_to(ROOT)))
            (root / 'pipeline-review.json').write_text(json.dumps(review))
            with self.assertRaisesRegex(ValueError, 'certificate content binding'):
                driver.checkpoint_preprocessing(root, 'segment')

    def test_read_only_content_review_cannot_apply_to_another_execution_review(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'test-evidence') as tmp:
            root, _ = self.fixture(tmp)
            (root / 'checkpoint-evidence-review.json').write_text(json.dumps({'approved': True, 'independent': True,
                'scope': 'INHERITED_CERTIFICATE_CONTENT_ONLY', 'manifest_sha256': driver.sha256(root / 'manifest.json'),
                'pipeline_review_sha256': 'different-review', 'registration_hashes': {}}))
            with self.assertRaisesRegex(ValueError, 'content review identity'):
                driver.checkpoint_preprocessing(root, 'segment')


class CurrentSuccessfulCheckpointDelivery(unittest.TestCase):
    evidence = ROOT / 'test-evidence/static-release-candidate/engineering-contract-20261007/u12-continuation'
    trial_id = 'static-eng-u12-segment1-20261007'

    def test_dependency_inventory_contains_exact_child_and_inherited_review_inputs(self):
        lineage=driver.checkpoint_preprocessing(self.evidence,self.trial_id)
        paths=set(lineage['verified_dependency_paths'])
        candidate=load(self.evidence/'candidate.json');manifest_path=Path(candidate['manifest']);manifest=load(manifest_path)
        contract_path=ROOT/manifest['checkpoint_contract'];contract=load(contract_path)
        expected={str(self.evidence/'candidate.json'),str(manifest_path),str(contract_path),
                  str(self.evidence/'pipeline-review.json'),str(self.evidence/'checkpoint-evidence-review.json'),
                  str(self.evidence/(self.trial_id+'-config.json')),str(self.evidence/self.trial_id/'case_spec.json')}
        expected.update(str(ROOT/contract[key]) for key in ('checkpoint_record','executed_config','executed_case_spec'))
        for value in lineage['record_paths'].values():
            expected.add(value);expected.add(driver.run_record_gate.citation_status(Path(value))['stage_record'])
        self.assertTrue(expected<=paths,expected-paths)

    def test_study_summary_retains_declared_level_gate_for_completion_report(self):
        spec = load(SPEC)
        rows = EveryNewRefusalPathHasANegativeTest().mesh_rows()
        with tempfile.TemporaryDirectory() as tmp:
            for excluded in (False, True):
                rows[0]['valid'] = not excluded
                summary = driver.write_outputs(Path(tmp), 'mesh', spec, rows, None, None)
                stored = load(Path(tmp) / 'mesh-study-report.json')
                self.assertEqual(2 if excluded else 3, stored['valid_level_count'])
                self.assertEqual(not excluded, stored['all_declared_levels_valid'])
                self.assertEqual(stored['all_declared_levels_valid'], summary['all_declared_levels_valid'])

    def test_write_read_completion_preserves_full_level_gate_without_inventing_gci(self):
        spec_path = ROOT / 'cases/static-cylinder-step/engineering-preparation-20261007/case_spec.json'
        spec = load(spec_path); study_paths = {}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for kind in ('mesh', 'domain'):
                rows = EveryNewRefusalPathHasANegativeTest().mesh_rows()
                for i, (row, level) in enumerate(zip(rows, driver.study_levels(spec, kind))):
                    row['level'] = level['name']; row['domain_scale'] = (1, 1.25, 1.5)[i]
                target = root / kind
                driver.write_outputs(target, kind, spec, rows, spec['domain']['outer_bounds_m'], None)
                study_paths[kind] = target / (kind + '-study-report.json')
            complete = driver.completion_report(spec_path, self.evidence, self.trial_id, study_paths, root / 'completion')
            self.assertTrue(all(value['all_declared_levels_valid'] for value in complete['studies'].values()))
            self.assertFalse(complete['research_complete'])
            self.assertFalse(complete['studies']['mesh']['independent'])

    def test_local_foreign_physics_cannot_replace_true_continuation_parent_certificate(self):
        import shutil
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=ROOT / 'test-evidence') as tmp:
            evidence = Path(tmp); run = evidence / self.trial_id; run.mkdir()
            for path in (self.evidence / self.trial_id).iterdir():
                if path.is_file() and path.suffix != '.sim': shutil.copyfile(path, run / path.name)
            for name in ['candidate.json', 'pipeline-review.json', 'checkpoint-evidence-review.json', self.trial_id + '-config.json',
                         self.trial_id + '-exports.json', self.trial_id + '-report.json', self.trial_id + '-report.md']:
                shutil.copyfile(self.evidence / name, evidence / name)
            review=load(evidence/'pipeline-review.json');review['registration_hashes'][str((evidence/'candidate.json').relative_to(ROOT))]=driver.sha256(evidence/'candidate.json')
            (evidence/'pipeline-review.json').write_text(json.dumps(review))
            content=load(evidence/'checkpoint-evidence-review.json');content['pipeline_review_sha256']=driver.sha256(evidence/'pipeline-review.json')
            (evidence/'checkpoint-evidence-review.json').write_text(json.dumps(content))
            parent = Path(driver.checkpoint_preprocessing(self.evidence, self.trial_id)['record_paths']['external_flow_physics.json'])
            foreign = load(parent); foreign['actual_inlet_speed_m_s'] = 15
            (run / 'external_flow_physics.json').write_text(json.dumps(foreign))
            module_spec = importlib.util.spec_from_file_location('foreign_physics_renderer', ROOT / 'cases/static-cylinder-step/render_report.py')
            renderer = importlib.util.module_from_spec(module_spec); module_spec.loader.exec_module(renderer)
            with patch.object(sys, 'argv', ['renderer', '--run-dir', str(run)]), patch('builtins.print'): renderer.main()
            self.assertEqual(str(parent), load(run / 'presentation.json')['physics_record'])
            self.assertIn('入口速度：12.0', (run / 'report.html').read_text())

    def fixture(self, directory):
        root, run, _ = CheckpointStudyLineage().fixture(directory)
        config = load(root / 'segment-config.json')
        config['macro_hashes'] = load(self.evidence / 'pipeline-review.json')['macro_hashes']
        (root / 'segment-config.json').write_text(json.dumps(config))
        review = load(root / 'pipeline-review.json')
        review.update(input_hashes=config['input_hashes'], macro_hashes=config['macro_hashes'])
        (root / 'pipeline-review.json').write_text(json.dumps(review))
        contract = load(ROOT / 'test-evidence/static-release-candidate/engineering-contract-20261007/u12-checkpoint-contract.json')
        return root, run, ROOT / contract['geometry_source_sim']

    def test_real_successful_u12_is_medium_and_original_domain_common_baseline(self):
        contract = load(ROOT / 'test-evidence/static-release-candidate/engineering-contract-20261007/u12-checkpoint-contract.json')
        geometry = ROOT / contract['geometry_source_sim']
        for kind, level in [('mesh', 'medium'), ('domain', 'd1p00')]:
            level_spec = load(ROOT / 'cases/static-cylinder-step/engineering-preparation-20261007' / kind / level / 'case_spec.json')
            equality = driver.reused_reference(self.evidence, self.trial_id,
                {'reuse': {'evidence': str(self.evidence), 'trial_id': self.trial_id}}, level_spec, geometry)
            # Historical metrics remain readable; starting a new study cannot reuse superseded producers.
            self.assertFalse(equality['ok'])
            # 2026-10-14：除预处理两个宏外，本轮还修订了 RunExternalFlowSolve.java（时间预算撤销）
            # 与 AuditExternalFlowConvergence.java（reference_policy 派生）；两者都属于"产出了被记录量的宏"，
            # 因此差异清单里以另一条前缀出现。依据：time-budget-revocation-superseded-pins.json 的 macro_repaired。
            expected_differences = sorted(
                ['checkpoint preprocessing producer revision differs: ' + name
                 for name in ('BuildExternalFlowMesh.java', 'AuditExternalFlowMesh.java')]
                + ['macro revision that produced a recorded quantity differs: ' + name
                   for name in ('RunExternalFlowSolve.java', 'AuditExternalFlowConvergence.java')])
            self.assertEqual(expected_differences, sorted(equality['differences']))
            self.assertFalse(equality['checkpoint_lineage']['engineering_conclusion_reused'])
        metrics = driver.collect_metrics(self.evidence, self.trial_id)
        spec = load(self.evidence / self.trial_id / 'case_spec.json')
        self.assertTrue(driver.level_validity(metrics, spec['quality'], reused=True)['valid'])
        self.assertEqual('NOT_VALIDATED_NO_REFERENCE', metrics['validation']['validation_verdict'])

    def test_current_producer_or_review_mismatch_cannot_reuse_parent_preprocessing(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'test-evidence') as tmp:
            root, run, geometry = self.fixture(tmp)
            config = load(root / 'segment-config.json')
            config['macro_hashes']['RunExternalFlowSolve.java'] = 'changed-current-producer'
            (root / 'segment-config.json').write_text(json.dumps(config))
            result = driver.reused_reference(root, 'segment', {'reuse': {'evidence': str(root), 'trial_id': 'segment'}}, load(run / 'case_spec.json'), geometry)
            self.assertFalse(result['ok'])
            self.assertTrue(any('checkpoint current execution differs from independent review' in value for value in result['differences']))
            self.assertTrue(any('RunExternalFlowSolve.java' in v for v in result['differences']))

    def test_changed_parent_pin_cannot_supply_current_geometry(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'test-evidence') as tmp:
            root, run, geometry = self.fixture(tmp)
            review = load(root / 'pipeline-review.json'); review['registration_hashes'] = {}
            (root / 'pipeline-review.json').write_text(json.dumps(review))
            result = driver.reused_reference(root, 'segment', {'reuse': {'evidence': str(root), 'trial_id': 'segment'}}, load(run / 'case_spec.json'), geometry)
            self.assertFalse(result['ok'])
            self.assertTrue(any('checkpoint lineage' in v for v in result['differences']))

    def property_fixture(self, directory):
        root = Path(directory); executed = self.evidence / self.trial_id / 'case_spec.json'
        spec = load(executed); spec['_study_id'] = 'different-study-metadata'
        study_path = root / 'study.json'; study_path.write_text(json.dumps(spec))
        stage = driver.read_json(self.evidence / self.trial_id / 'stage-solve.json')
        end = load(self.evidence / self.trial_id / 'external_flow_solve.json')['end_iteration']
        record = {'status': 'ok', 'case_spec_sha256': driver.sha256(executed),
            'input_sim_sha256': stage['output_sha256'], 'input_sim_sha256_after': stage['output_sha256'],
            'start_iteration': end, 'end_iteration': end, 'properties': {'fixture': 'identity only, not a STAR trial'}}
        property_path = root / 'property_nearwall_readback.json'; property_path.write_text(json.dumps(record))
        (root / 'stage-property-nearwall.json').write_text(json.dumps({'stage': 'property-nearwall', 'state': 'ok', 'artifacts': [property_path.name]}))
        return root, study_path, property_path, record

    def test_property_identity_uses_executed_spec_not_study_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, study_path, property_path, _ = self.property_fixture(tmp)
            result = driver.completion_report(study_path, self.evidence, self.trial_id, {}, root / 'report', property_path)
            self.assertTrue(result['applicable_result'])
            self.assertEqual('READBACK_AVAILABLE', result['property_nearwall']['status'])
            self.assertEqual('NOT_VALIDATED_NO_REFERENCE', result['validation_verdict'])
            self.assertFalse(result['research_complete'])

    def test_property_wrong_saved_hash_or_iteration_cannot_match_current_solution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, study_path, property_path, record = self.property_fixture(tmp)
            for key, value in [('input_sim_sha256', 'foreign'), ('input_sim_sha256_after', 'changed'),
                               ('start_iteration', 150), ('end_iteration', 150), ('case_spec_sha256', driver.sha256(study_path))]:
                changed = dict(record); changed[key] = value; property_path.write_text(json.dumps(changed))
                result = driver.completion_report(study_path, self.evidence, self.trial_id, {}, root / 'report', property_path)
                self.assertEqual('NOT_ESTABLISHED', result['property_nearwall']['status'], key)

    def test_real_continuation_renderer_retains_original_certified_physics_path(self):
        from unittest.mock import patch
        module_spec = importlib.util.spec_from_file_location('checkpoint_renderer_test', ROOT / 'cases/static-cylinder-step/render_report.py')
        renderer = importlib.util.module_from_spec(module_spec); module_spec.loader.exec_module(renderer)
        run = self.evidence / self.trial_id
        self.assertFalse((run / 'external_flow_physics.json').exists())
        with patch.object(sys, 'argv', ['renderer', '--run-dir', str(run)]), patch('builtins.print'):
            renderer.main()
        presentation = load(run / 'presentation.json')
        inherited = driver.checkpoint_preprocessing(self.evidence, self.trial_id)['record_paths']['external_flow_physics.json']
        self.assertEqual(inherited, presentation['physics_record'])
        self.assertEqual('CITABLE', presentation['physics_citation']['state'])
        self.assertTrue(presentation['inherited_preprocessing'])
        self.assertFalse(presentation['engineering_verified'])
        self.assertFalse((run / 'external_flow_physics.json').exists())
        with patch.object(sys, 'argv', ['renderer', '--run-dir', str(run)]), patch.object(renderer, 'checkpoint_preprocessing', side_effect=ValueError('changed parent approval pin')):
            with self.assertRaisesRegex(ValueError, 'changed parent approval pin'):
                renderer.main()


if __name__ == '__main__':
    unittest.main()
