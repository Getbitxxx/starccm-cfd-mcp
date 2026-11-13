"""Tests for the read-only CaseSpec diff and recipe checks (no STAR, no network)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from case_spec_diff import check_recipe, check_recipe_file, diff_specs, flatten, implied_growth  # noqa: E402

EVIDENCE = ROOT / 'campaigns/uav-2026/evidence/uav-trapwing-hlpw1'
TE_PRISM = EVIDENCE / 'trapwing-te-prism-20261102/staging/case_spec.json'
CORE_BL = EVIDENCE / 'trapwing-core005-bl8mm-20261102/staging/case_spec.json'
CORE_BL_CONFIG = EVIDENCE / 'trapwing-core005-bl8mm-20261102/staging/config.json'
CASESPEC = ROOT / 'cases/uav-trapwing-hlpw1/case_spec.json'


class DiffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # One directory per class: this project lives on a synced volume where removing a
        # directory costs ~1.7 s, which would dominate a pure-arithmetic suite.
        cls.work = Path(tempfile.mkdtemp(prefix='spec-diff-', dir=ROOT / 'tmp'))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def setUp(self):
        self.work = type(self).work

    def write(self, name, document):
        path = self.work / name
        path.write_text(json.dumps(document, ensure_ascii=False, indent=1), encoding='utf-8')
        return path

    def test_identical_specs_report_no_change(self):
        result = diff_specs(ROOT, TE_PRISM, TE_PRISM)
        self.assertTrue(result['identical'])
        self.assertEqual(result['changed'], [])
        self.assertFalse(result['single_variable'])

    def test_real_arms_differ_in_exactly_the_two_declared_knobs(self):
        result = diff_specs(ROOT, TE_PRISM, CORE_BL, declaration=CORE_BL_CONFIG)
        paths = [item['path'] for item in result['changed']]
        self.assertIn('mesh_nearfield_cell_size_m', paths)
        self.assertIn('quality.mesh_prism_total_thickness_m', paths)
        self.assertEqual(result['undeclared'], [], 'both knobs are declared in derivation.overrides')
        self.assertEqual(result['declared'], paths)
        self.assertFalse(result['single_variable'])
        self.assertIn('physical knobs changed', result['interpretation'])
        self.assertTrue(result['declaration']['path'].endswith('config.json'))
        self.assertEqual(len(result['declaration']['sha256']), 64)

    def test_spec_alone_declares_nothing(self):
        # Derived specs in this project keep the declaration in config.json; a spec on its
        # own must not be able to declare itself clean.
        result = diff_specs(ROOT, TE_PRISM, CORE_BL)
        self.assertEqual(len(result['undeclared']), 2)
        self.assertIsNone(result['declaration'])

    def test_undeclared_change_is_reported(self):
        document = json.loads(TE_PRISM.read_text(encoding='utf-8'))
        document['quality']['mesh_prism_layers_target'] = 20
        candidate = self.write('undeclared.json', document)
        result = diff_specs(ROOT, TE_PRISM, candidate)
        self.assertEqual(result['undeclared'], ['quality.mesh_prism_layers_target'])
        self.assertEqual(result['declared'], [])

    def test_single_variable_requires_one_change_and_no_additions(self):
        document = json.loads(TE_PRISM.read_text(encoding='utf-8'))
        document['mesh_nearfield_cell_size_m'] = 0.004
        candidate = self.write('one-knob.json', document)
        result = diff_specs(ROOT, TE_PRISM, candidate)
        self.assertTrue(result['single_variable'])
        self.assertEqual(result['changed'][0]['path'], 'mesh_nearfield_cell_size_m')

        document['mesh_wake_cell_size_m'] = 0.02
        two = self.write('two-knobs.json', document)
        self.assertFalse(diff_specs(ROOT, TE_PRISM, two)['single_variable'])

    def test_metadata_edits_are_not_counted_as_physics(self):
        document = json.loads(TE_PRISM.read_text(encoding='utf-8'))
        document.setdefault('derivation', {})['note'] = 'edited'
        candidate = self.write('metadata.json', document)
        result = diff_specs(ROOT, TE_PRISM, candidate)
        self.assertEqual(result['changed'], [])
        self.assertEqual(result['metadata_changed'], ['derivation.note'])

    def test_paths_outside_the_project_are_refused(self):
        outside = Path(tempfile.mkdtemp(prefix='outside-')) / 'spec.json'
        outside.write_text('{}', encoding='utf-8')
        try:
            with self.assertRaises(ValueError):
                diff_specs(ROOT, outside, TE_PRISM)
        finally:
            shutil.rmtree(outside.parent, ignore_errors=True)

    def test_non_json_is_refused(self):
        other = self.work / 'spec.txt'
        other.write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            diff_specs(ROOT, TE_PRISM, other)

    def test_boolean_replaced_by_a_number_is_a_change(self):
        # True == 1 in Python, so a naive `!=` would call this unchanged.
        base = self.write('bool-a.json', {'flag': 1, 'other': 0})
        cand = self.write('bool-b.json', {'flag': True, 'other': 0})
        result = diff_specs(ROOT, base, cand)
        self.assertEqual([item['path'] for item in result['changed']], ['flag'])
        self.assertFalse(result['identical'])

    def test_flatten_indexes_lists(self):
        self.assertEqual(flatten({'a': [1, {'b': 2}]}),
                         {'a.0': 1, 'a.1.b': 2})


class RecipeTests(unittest.TestCase):
    def test_casespec_recipe_is_buildable_with_known_growth(self):
        result = check_recipe_file(ROOT, CASESPEC)
        self.assertTrue(result['ok'], result['checks'])
        # CaseSpec recipe: 20 layers, first 2.8222e-5, total 5.2687e-3 -> growth exactly 1.2
        self.assertAlmostEqual(result['derived']['implied_growth'], 1.2, places=6)
        self.assertEqual(result['recipe']['layers'], 20)
        self.assertIsNone(result['recipe']['nearfield_cell_m'])
        self.assertTrue(all(item['ok'] for item in result['checks']))
        self.assertIn('not a mesh-quality gate', result['limit'])

    def test_unbuildable_stack_is_flagged(self):
        document = json.loads(CASESPEC.read_text(encoding='utf-8'))
        document['quality']['mesh_prism_total_thickness_m'] = 1.0e-4   # 12 x 2.8222e-5 = 3.39e-4
        result = check_recipe(document)
        self.assertFalse(result['ok'])
        failed = [item['check'] for item in result['checks'] if not item['ok']]
        self.assertIn('thickness_stack_buildable', failed)

    def test_prism_thicker_than_core_is_flagged(self):
        document = json.loads(CASESPEC.read_text(encoding='utf-8'))
        document['quality']['mesh_prism_total_thickness_m'] = 8.0e-3
        document['quality']['mesh_prism_layers_target'] = 12   # the spike arm's stack
        document['mesh_nearfield_cell_size_m'] = 0.005
        result = check_recipe(document)
        self.assertAlmostEqual(result['derived']['implied_growth'], 1.515893, places=5)
        self.assertAlmostEqual(result['derived']['last_layer_over_core_ratio'], 0.5482, places=3)
        self.assertTrue(result['ok'], 'a 2.7 mm last layer under a 5 mm core is still legal')

        document['mesh_nearfield_cell_size_m'] = 0.001
        stricter = check_recipe(document)
        failed = [item['check'] for item in stricter['checks'] if not item['ok']]
        self.assertIn('last_layer_not_above_core', failed)

    def test_missing_fields_are_errors_not_silent_passes(self):
        result = check_recipe({'quality': {'mesh_prism_layers_target': 12}})
        self.assertFalse(result['ok'])
        self.assertTrue(any('mesh_prism_first_layer_height_m' in error for error in result['errors']))

    def test_zero_first_layer_is_an_error_not_a_crash(self):
        result = check_recipe({'quality': {'mesh_prism_layers_target': 12,
                                          'mesh_prism_first_layer_height_m': 0.0,
                                          'mesh_prism_total_thickness_m': 1.0e-3}})
        self.assertFalse(result['ok'])
        self.assertTrue(any('first_layer' in error for error in result['errors']))
        self.assertIn('first_layer_positive', [item['check'] for item in result['checks']])

    def test_growth_solver_is_not_capped(self):
        # A capped bracket would return 4.0 here, which does not reproduce total=100.
        self.assertAlmostEqual(implied_growth(1.0, 2, 100.0), 99.0, places=6)
        self.assertIsNone(implied_growth(1.0, 2, 0.5), 'total below the layered sum is impossible')

    def test_growth_solver_matches_closed_form_when_uniform(self):
        self.assertAlmostEqual(implied_growth(1.0e-4, 10, 1.0e-3), 1.0, places=9)


if __name__ == '__main__':
    unittest.main()
