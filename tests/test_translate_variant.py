# -*- coding: utf-8 -*-
"""①R16: the exact-translation axis, pinned as executable tests before any STAR time is spent.

The variant must move the body (and the domain with it) by exactly [0.123, -0.087, 0.041] m and change
nothing else. The two earlier position runs moved the body by +Z 1.5 m and +X 5 m; this vector is different,
so these tests also pin that it is neither of them.
"""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / 'cases' / 'static-sphere-external-flow' / 'case_spec.json'
VARIANT = ROOT / 'cases' / 'static-sphere-translate-0p123-m0p087-0p041' / 'case_spec.json'
NEGATIVE = ROOT / 'cases' / 'static-sphere-translate-0p123-m0p087-0p041' / 'negative' / 'case_spec.json'
VECTOR = [0.123, -0.087, 0.041]
METADATA = {'_status', 'case_ref', 'objective', '_translate_variant', '_counter_example'}
MOVED_LEAVES = {'geometry.body_origin_m', 'domain.outer_bounds_m', 'domain.extent_basis'}


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


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


class TheTranslationVariantVariesOnlyThePosition(unittest.TestCase):

    def is_metadata(self, key):
        return any(key == name or key.startswith(name + '.') for name in METADATA)

    def test_exactly_the_position_leaves_differ(self):
        base = {k: v for k, v in leaves(load(BASELINE)) if not self.is_metadata(k)}
        var = {k: v for k, v in leaves(load(VARIANT)) if not self.is_metadata(k)}
        self.assertEqual(set(base), set(var), 'the variant added or dropped a leaf')
        self.assertEqual(MOVED_LEAVES, {key for key in base if base[key] != var[key]})

    def test_the_origin_is_the_exact_vector_and_the_bounds_are_translated_by_it(self):
        base, var = load(BASELINE), load(VARIANT)
        self.assertEqual(VECTOR, var['geometry']['body_origin_m'])
        self.assertEqual([0.0, 0.0, 0.0], base['geometry']['body_origin_m'])
        for index, value in enumerate(base['domain']['outer_bounds_m']):
            self.assertAlmostEqual(value + VECTOR[index % 3], var['domain']['outer_bounds_m'][index],
                                   places=15)

    def test_it_is_not_either_earlier_position_run(self):
        origin = load(VARIANT)['geometry']['body_origin_m']
        self.assertNotEqual(origin, [0.0, 0.0, 1.5], 'this is the +Z 1.5 m run already measured')
        self.assertNotEqual(origin, [5.0, 0.0, 0.0], 'this is the +X 5 m run already measured')
        earlier = ROOT / 'cases' / 'static-sphere-external-flow' / 'variants'
        for path in earlier.glob('translate_*/case_spec.json'):
            other = load(path)['geometry']['body_origin_m']
            self.assertNotEqual(origin, other, '%s already measured this vector' % path.parent.name)

    def test_size_mesh_and_operating_conditions_are_bit_identical(self):
        base, var = load(BASELINE), load(VARIANT)
        for key in ('flow', 'numerics', 'quality', 'model_length_unit', 'region_name', 'boundary_roles',
                    'body_wall_boundary_name', 'mesh_prism_layers', 'mesh_base_size_m',
                    'mesh_body_target_size_m', 'mesh_body_minimum_size_m'):
            self.assertEqual(json.dumps(base[key], sort_keys=True), json.dumps(var[key], sort_keys=True), key)
        for key in ('body_radius_m', 'characteristic_length_m', 'reference_area_m2', 'body_shape',
                    'body_part_name', 'body_parts'):
            self.assertEqual(base['geometry'][key], var['geometry'][key], key)
        self.assertEqual(base['domain']['shape'], var['domain']['shape'])

    def test_the_variant_claims_no_engineering_validity(self):
        var = load(VARIANT)
        self.assertEqual('NOT_ESTABLISHED', var['engineering_target']['drag_coefficient']['status'])
        self.assertFalse(var.get('case_spec_frozen', False))
        self.assertIn('NOT_ESTABLISHED', var['_status'])


class ThePositionCounterExampleIsTheWrongPairing(unittest.TestCase):

    def test_the_negative_declares_the_baseline_origin_for_a_moved_body(self):
        negative, variant = load(NEGATIVE), load(VARIANT)
        self.assertEqual([0.0, 0.0, 0.0], negative['geometry']['body_origin_m'])
        self.assertEqual(VECTOR, variant['geometry']['body_origin_m'])
        self.assertNotEqual(negative['geometry']['body_origin_m'], variant['geometry']['body_origin_m'])

    def test_it_aims_the_existing_centre_check_not_a_new_rule(self):
        source = (ROOT / 'macros' / 'AuditStaticBodyGeometry.java').read_text(encoding='utf-8')
        self.assertIn('The audited body is not where the CaseSpec says', source)
        self.assertIn('measured_centre_offset_ratio', source)


class TheTranslatePipelinesPinLiveBytes(unittest.TestCase):

    def test_every_macro_pin_is_the_live_macro_or_a_recorded_supersession(self):
        """A pin must equal the live bytes, unless a later repair is RECORDED as superseding it.

        ①R24 repaired the two geometry macros for the native CAD tree, and ②R23 had already changed both plus
        the region/physics macros; those repairs legitimately stale the pins in these completed variant
        pipelines. Recorded supersessions are the only allowed exceptions (tests/superseded_pins.py), so an
        unexplained stale pin still fails here.
        """
        from tests.superseded_pins import allowed_pins
        allowed = allowed_pins(ROOT / 'test-evidence')
        for name in ('pipeline-build-body.json', 'pipeline-e2e.json'):
            doc = load(ROOT / 'cases' / 'static-sphere-translate-0p123-m0p087-0p041' / name)
            for macro, digest in doc['macro_hashes'].items():
                live = sha256(ROOT / 'macros' / macro)
                if digest != live:
                    self.assertIn(macro, allowed, '%s pins %s but no record supersedes it'
                                  % (name, digest[:16]))
                    self.assertIn(digest, allowed[macro],
                                  '%s pins %s which no record lists as superseded'
                                  % (name, digest[:16]))

    def test_the_case_spec_pin_is_the_variant_case_spec(self):
        for name in ('pipeline-build-body.json', 'pipeline-e2e.json'):
            doc = load(ROOT / 'cases' / 'static-sphere-translate-0p123-m0p087-0p041' / name)
            self.assertEqual(sha256(VARIANT), doc['input_hashes']['case_spec.json'], name)

    def test_the_e2e_stages_are_the_reviewed_baseline_stages(self):
        baseline = load(ROOT / 'cases' / 'static-sphere-external-flow' /
                        'pipeline-e2e-geometry-to-validation.json')
        variant = load(ROOT / 'cases' / 'static-sphere-translate-0p123-m0p087-0p041' / 'pipeline-e2e.json')
        self.assertEqual(json.dumps(baseline['stages'], sort_keys=True),
                         json.dumps(variant['stages'], sort_keys=True))


if __name__ == '__main__':
    unittest.main()
