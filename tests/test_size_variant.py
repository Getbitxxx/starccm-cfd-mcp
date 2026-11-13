# -*- coding: utf-8 -*-
"""①R15: the size axis, pinned as executable tests before any STAR time is spent.

The size variant must be a ONE-parameter variant: the size-derived quantities scale by exactly 1.2 and every
independent operating condition stays bit-identical to the round-10 baseline. These tests read the committed
variant CaseSpec against the committed baseline, so "the variant really is the size axis" is checkable
without STAR. The STAR read-back (that the built geometry really has the 1.2x radius) is separate evidence:
test-evidence/static-external-flow/round15-size-x1p2/.
"""
from __future__ import annotations

import hashlib
import json
import math
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / 'cases' / 'static-sphere-external-flow' / 'case_spec.json'
VARIANT = ROOT / 'cases' / 'static-sphere-size-x1p2' / 'case_spec.json'
NEGATIVE = ROOT / 'cases' / 'static-sphere-size-x1p2' / 'negative' / 'case_spec.json'
FACTOR = 1.2

# The only leaves allowed to differ: the size-derived values, the prose that documents them, and the
# variant's own metadata. Everything else - flow, numerics, quality gates, names, prism layers, shape,
# origin, part name - must be identical, or the variant is varying more than one thing.
METADATA = {'_status', 'case_ref', 'objective', '_size_variant', '_counter_example'}
SCALED_LEAVES = {'geometry.body_radius_m', 'geometry.characteristic_length_m', 'geometry.reference_area_m2',
                 'geometry.reference_area_basis', 'domain.outer_bounds_m', 'domain.extent_basis',
                 'mesh_base_size_m', 'mesh_body_target_size_m', 'mesh_body_minimum_size_m'}


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def leaves(node, prefix=''):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from leaves(value, (prefix + '.' + key).lstrip('.'))
    elif isinstance(node, list):
        yield prefix, json.dumps(node)
    else:
        yield prefix, node


class TheSizeVariantVariesOnlyTheSize(unittest.TestCase):

    def is_metadata(self, key):
        return any(key == name or key.startswith(name + '.') for name in METADATA)

    def test_exactly_the_size_derived_leaves_differ(self):
        base = {k: v for k, v in leaves(load(BASELINE)) if not self.is_metadata(k)}
        var = {k: v for k, v in leaves(load(VARIANT)) if not self.is_metadata(k)}
        self.assertEqual(set(base), set(var), 'the variant added or dropped a leaf')
        differing = {key for key in base if base[key] != var[key]}
        self.assertEqual(SCALED_LEAVES, differing,
                         'the variant must differ in exactly the size-derived leaves')

    def test_every_scaled_number_is_exactly_the_1_2x_value(self):
        base, var = load(BASELINE), load(VARIANT)
        self.assertAlmostEqual(base['geometry']['body_radius_m'] * FACTOR,
                               var['geometry']['body_radius_m'], places=15)
        self.assertAlmostEqual(base['geometry']['characteristic_length_m'] * FACTOR,
                               var['geometry']['characteristic_length_m'], places=15)
        self.assertAlmostEqual(math.pi * var['geometry']['characteristic_length_m'] ** 2 / 4.0,
                               var['geometry']['reference_area_m2'], places=15)
        for index, value in enumerate(base['domain']['outer_bounds_m']):
            self.assertAlmostEqual(value * FACTOR, var['domain']['outer_bounds_m'][index], places=15)
        for key in ('mesh_base_size_m', 'mesh_body_target_size_m', 'mesh_body_minimum_size_m'):
            self.assertAlmostEqual(base[key] * FACTOR, var[key], places=15)

    def test_the_dimensionless_ratios_are_scale_invariant(self):
        for spec in (load(BASELINE), load(VARIANT)):
            diameter = spec['geometry']['characteristic_length_m']
            bounds = spec['domain']['outer_bounds_m']
            self.assertAlmostEqual(5.0, abs(bounds[0]) / diameter, places=12)
            self.assertAlmostEqual(15.0, bounds[3] / diameter, places=12)
            self.assertAlmostEqual(5.0, bounds[4] / diameter, places=12)
            self.assertAlmostEqual(0.04, spec['mesh_body_target_size_m'] / diameter, places=12)

    def test_the_independent_operating_conditions_are_bit_identical(self):
        base, var = load(BASELINE), load(VARIANT)
        for key in ('flow', 'numerics', 'quality', 'model_length_unit', 'region_name', 'boundary_roles',
                    'body_wall_boundary_name', 'mesh_prism_layers'):
            self.assertEqual(json.dumps(base[key], sort_keys=True), json.dumps(var[key], sort_keys=True), key)
        for key in ('body_shape', 'body_part_name', 'body_parts', 'body_origin_m'):
            self.assertEqual(base['geometry'][key], var['geometry'][key], key)
        self.assertEqual(base['domain']['shape'], var['domain']['shape'])

    def test_the_variant_claims_no_engineering_validity(self):
        var = load(VARIANT)
        self.assertEqual('NOT_ESTABLISHED',
                         var['engineering_target']['drag_coefficient']['status'])
        self.assertFalse(var.get('case_spec_frozen', False))
        self.assertIn('NOT_ESTABLISHED', var['_status'])


class TheSizeCounterExampleIsTheWrongPairing(unittest.TestCase):

    def test_the_negative_declares_the_baseline_radius_for_a_1_2x_body(self):
        negative, baseline, variant = load(NEGATIVE), load(BASELINE), load(VARIANT)
        self.assertAlmostEqual(baseline['geometry']['body_radius_m'],
                               negative['geometry']['body_radius_m'], places=15)
        self.assertAlmostEqual(baseline['geometry']['body_radius_m'] * FACTOR,
                               variant['geometry']['body_radius_m'], places=15)
        self.assertNotAlmostEqual(negative['geometry']['body_radius_m'],
                                  variant['geometry']['body_radius_m'], places=6)

    def test_it_is_not_a_new_rule_but_the_existing_audit_check(self):
        # the existing macro already refuses a body whose measured size is not the CaseSpec's. ②R23/①R24
        # generalised the message from the sphere-only wording to "surface/dimensions do not match the
        # CaseSpec"; the guard is that the SAME measured-size gates are still what refuse the pairing.
        source = (ROOT / 'macros' / 'AuditStaticBodyGeometry.java').read_text(encoding='utf-8')
        self.assertIn('The audited body', source)
        self.assertIn('do not match the CaseSpec', source)
        self.assertIn('measured_vertex_radius_error_ratio', source)


class TheVariantPipelinesPinLiveBytes(unittest.TestCase):

    def pipelines(self):
        for name in ('pipeline-build-body.json', 'pipeline-e2e.json'):
            yield ROOT / 'cases' / 'static-sphere-size-x1p2' / name

    def test_every_macro_pin_is_the_live_macro_or_a_recorded_supersession(self):
        """A pin must equal the live bytes, unless a later repair is RECORDED as superseding it.

        ①R24 repaired the two geometry macros for the native CAD tree, and ②R23 had already changed both plus
        the region/physics macros; those repairs legitimately stale the pins in these completed variant
        pipelines. Recorded supersessions are the only allowed exceptions (tests/superseded_pins.py), so an
        unexplained stale pin still fails here.
        """
        from tests.superseded_pins import allowed_pins
        allowed = allowed_pins(ROOT / 'test-evidence')
        for path in self.pipelines():
            doc = load(path)
            for macro, digest in doc['macro_hashes'].items():
                live = sha256(ROOT / 'macros' / macro)
                if digest != live:
                    self.assertIn(macro, allowed, '%s pins %s but no record supersedes it'
                                  % (path.name, digest[:16]))
                    self.assertIn(digest, allowed[macro],
                                  '%s pins %s which no record lists as superseded'
                                  % (path.name, digest[:16]))

    def test_the_case_spec_pin_is_the_variant_case_spec(self):
        for path in self.pipelines():
            doc = load(path)
            self.assertEqual(sha256(ROOT / 'cases' / 'static-sphere-size-x1p2' / 'case_spec.json'),
                             doc['input_hashes']['case_spec.json'], path.name)

    def test_the_e2e_stages_are_the_reviewed_baseline_stages(self):
        baseline = load(ROOT / 'cases' / 'static-sphere-external-flow' /
                        'pipeline-e2e-geometry-to-validation.json')
        variant = load(ROOT / 'cases' / 'static-sphere-size-x1p2' / 'pipeline-e2e.json')
        self.assertEqual(json.dumps(baseline['stages'], sort_keys=True),
                         json.dumps(variant['stages'], sort_keys=True),
                         'the variant must run the same six stages, not a new chain')

    def test_the_binding_negative_requires_read_only_true(self):
        doc = load(ROOT / 'test-evidence' / 'static-external-flow' / 'round15-audit-binding-negative' /
                   'pipeline.json')
        self.assertIn('external_flow_validation.json::"read_only_verified":true',
                      doc['stages'][0]['require_contains'])


class GeneratedSphereSourceIdentity(unittest.TestCase):
    """Existing real records bind generated geometry without backfilling an old audit."""

    def setUp(self):
        self.contract = load(ROOT / 'cases/static-reusable-entry/window1/generated-sphere/source-contract.json')
        self.fixture = load(ROOT / 'test-evidence/static-reusable-entry/window1-sphere-source-contract/positive-negative-fixtures.json')

    def test_real_generation_source_and_certified_audit_handoff_agree(self):
        from scripts.run_record_gate import assert_citable
        c = self.contract
        generation = load(ROOT / c['generation_record'])
        audit_path = ROOT / c['audit_record']
        assert_citable(ROOT / c['generation_record'])
        citation = assert_citable(audit_path)
        stage = json.loads((ROOT / citation['stage_record']).read_text(encoding='utf-8-sig'))
        macros = [m for m in stage['macros'] if m['macro'] == 'AuditStaticBodyGeometry.java']
        self.assertEqual(1, len(macros))
        self.assertTrue(macros[0]['ran'])
        self.assertEqual(('ok', 0), (macros[0]['status'], macros[0]['exit_code']))
        digest = hashlib.sha256((ROOT / c['source_sim_path']).read_bytes()).hexdigest()
        self.assertEqual(c['source_sim_sha256'], digest)
        self.assertEqual(digest, generation['output_sha256'])
        self.assertEqual(digest, macros[0]['input_sha256'])
        audit = load(audit_path)
        self.assertNotIn('input_sim_sha256', audit)
        for key in ('body_shape', 'body_part_name', 'body_origin_m', 'body_radius_m',
                    'is_closed', 'is_manifold', 'regions', 'iteration'):
            self.assertEqual(generation[key], audit[key], key)
        runner = json.loads((ROOT / c['generation_runner_record']).read_text(encoding='utf-8-sig'))
        self.assertEqual(digest, runner['stages'][0]['output_sha256'])
        self.assertEqual(1, generation['part_count'])

    def test_real_failed_audit_cannot_certify_source(self):
        from scripts.run_record_gate import assert_citable, RecordNotCitable
        with self.assertRaises(RecordNotCitable):
            assert_citable(ROOT / self.fixture['real_failed_audit']['path'])

    def test_real_solved_input_cannot_inherit_geometry_source_identity(self):
        wrong = ROOT / self.fixture['wrong_input_real_solved_sim']['path']
        self.assertNotEqual(self.contract['source_sim_sha256'],
                            hashlib.sha256(wrong.read_bytes()).hexdigest())

    def test_generated_source_is_not_declared_as_step_or_cad(self):
        self.assertEqual('generated_sphere', self.contract['source_kind'])
        self.assertEqual('native', self.contract['geometry_source_format'])
        self.assertNotIn('step_path', self.contract)
        self.assertNotIn('step_sha256', self.contract)

    def test_historical_byte_identity_is_preserved(self):
        baseline = load(ROOT / 'test-evidence/static-reusable-entry/window1-sphere-source-contract/historical-byte-baseline.json')
        for name, expected in baseline['files'].items():
            with self.subTest(name=name):
                self.assertEqual(expected, hashlib.sha256((ROOT / name).read_bytes()).hexdigest())

    def test_four_real_variant_source_chains_and_wrong_declarations(self):
        from scripts.run_record_gate import assert_citable, RecordNotCitable
        index = load(ROOT / 'test-evidence/static-reusable-entry/window1-four-axis-contracts/contract-index.json')
        self.assertEqual([], index['missing'])
        self.assertEqual({'size-x1p2', 'translate', 'rename', 'units-mm'},
                         {row['axis'] for row in index['axes']})
        for row in index['axes']:
            with self.subTest(axis=row['axis']):
                c = load(ROOT / row['contract'])
                digest = hashlib.sha256((ROOT / c['source_sim_path']).read_bytes()).hexdigest()
                generation = load(ROOT / c['generation_record'])
                audit = load(ROOT / c['audit_record'])
                assert_citable(ROOT / c['generation_record'])
                citation = assert_citable(ROOT / c['audit_record'])
                stage = json.loads((ROOT / citation['stage_record']).read_text(encoding='utf-8-sig'))
                macros = [m for m in stage['macros'] if m['macro'] == 'AuditStaticBodyGeometry.java']
                self.assertEqual(1, len(macros))
                self.assertEqual((True, 'ok', 0), (macros[0]['ran'], macros[0]['status'], macros[0]['exit_code']))
                self.assertEqual(digest, generation['output_sha256'])
                self.assertEqual(digest, c['source_sim_sha256'])
                self.assertEqual(digest, macros[0]['input_sha256'])
                self.assertNotIn('input_sim_sha256', audit)
                for key in ('body_shape', 'body_part_name', 'body_origin_m', 'body_radius_m',
                            'is_closed', 'is_manifold', 'regions', 'iteration'):
                    self.assertEqual(generation[key], audit[key], key)
                with self.assertRaises(RecordNotCitable):
                    assert_citable(ROOT / row['negative']['record'])
                if row['axis'] == 'units-mm':
                    self.assertEqual('mm', generation['model_length_unit'])
                    self.assertEqual(0.001, generation['preferred_length_unit_conversion_to_m'])
                    self.assertEqual(0.05, generation['body_radius_m'])
                    self.assertEqual('mm', c['unit_evidence']['new_entry_CaseSpec_unit'])
                    self.assertEqual('m', c['unit_evidence']['contract_geometry_unit'])

    def test_four_axis_historical_records_are_not_backfilled(self):
        baseline = load(ROOT / 'test-evidence/static-reusable-entry/window1-four-axis-contracts/historical-byte-baseline.json')
        for name, expected in baseline['files'].items():
            with self.subTest(name=name):
                self.assertEqual(expected, hashlib.sha256((ROOT / name).read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
