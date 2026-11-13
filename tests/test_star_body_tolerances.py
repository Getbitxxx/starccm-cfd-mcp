# -*- coding: utf-8 -*-
"""Emulate the two radius-tolerance rules against float32 storage, without needing STAR.

Both rules below are the ones the building macro has actually used:

  old (round 2026-10-03a):  tolerance = 1e-6 * R, radii measured about the REQUESTED origin
  new (round 2026-10-03b):  tolerance = max(1e-6 * R, 8 * ulp(float32 max|coordinate|)),
                            radii measured about the MEASURED centroid, with a separate
                            storage-derived tolerance on the centroid offset

A binary STL stores vertices as 32-bit floats, so a coordinate of magnitude |x| carries a storage
error of about ulp(float32 |x|)/2. That error scales with the distance from the origin while the old
tolerance scaled with R, so the old rule rejected a geometrically correct sphere placed away from the
origin. This test would fail if either rule were changed, which is the point: the fix must be pinned,
not asserted in a report.
"""
import json
import math
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def float32(value: float) -> float:
    return struct.unpack('<f', struct.pack('<f', value))[0]


def tessellate(radius: float, origin, rings: int = 64, segments: int = 128):
    """A closed lat/long triangulation, rounded to float32 exactly as an STL would store it."""
    points = []
    for i in range(1, rings):
        theta = math.pi * i / rings
        for j in range(segments):
            phi = 2 * math.pi * j / segments
            points.append((origin[0] + radius * math.sin(theta) * math.cos(phi),
                           origin[1] + radius * math.sin(theta) * math.sin(phi),
                           origin[2] + radius * math.cos(theta)))
    return [(float32(p[0]), float32(p[1]), float32(p[2])) for p in points]


def measure(points, reference):
    radii = [math.dist(p, reference) for p in points]
    return min(radii), max(radii)


def centroid(points):
    n = len(points)
    return tuple(sum(p[a] for p in points) / n for a in range(3))


def old_rule(points, radius, origin):
    """Old rule: relative tolerance 1e-6 on R, radii about the requested origin. Ratio is
    error/tolerance, so the comparison is always against 1.0."""
    low, high = measure(points, origin)
    error = max(abs(low - radius), abs(high - radius))
    return (error / (1e-6 * radius)), 1.0


def new_rule(points, radius, origin):
    centre = centroid(points)
    low, high = measure(points, centre)
    error = max(abs(low - radius), abs(high - radius))
    max_abs = max(abs(c) for p in points for c in p)
    tolerance = max(1e-6 * radius, 8.0 * float32_ulp(max_abs))
    return error / tolerance, 1.0


def float32_ulp(value: float) -> float:
    """The spacing of 32-bit floats at |value|.

    math.ulp() must NOT be used directly: it returns the spacing of a 64-bit float, which is about
    1e9 times smaller. The macro is correct because Java's Math.ulp((float) x) takes a float and
    returns a float; this Python emulation has to reproduce that by stepping the bit pattern.
    """
    magnitude = abs(float(value))
    bits = struct.unpack('<I', struct.pack('<f', magnitude))[0]
    lower = struct.unpack('<f', struct.pack('<I', bits))[0]
    upper = struct.unpack('<f', struct.pack('<I', bits + 1))[0]
    return abs(upper - lower)


class RadiusTolerance(unittest.TestCase):
    RADIUS = 0.05

    def cases(self):
        return [('origin', [0.0, 0.0, 0.0]), ('shifted_z', [0.0, 0.0, 1.5]),
                ('shifted_xyz', [-1.234, 0.0, 2.5]), ('far_x', [5.0, 0.0, 0.0])]

    def test_the_old_rule_rejects_a_correct_sphere_away_from_the_origin(self):
        # This is the defect, demonstrated rather than described. The margin at [0,0,1.5] is thin
        # and depends on where the tessellation happens to place vertices, which is exactly why the
        # origin=[0,0,0] case that was run first could not have exposed it.
        ratios = {}
        for name, origin in self.cases():
            ratio, limit = old_rule(tessellate(self.RADIUS, origin), self.RADIUS, origin)
            ratios[name] = ratio
        self.assertLess(ratios['origin'], 1.0)
        self.assertGreater(ratios['shifted_xyz'], 1.0)
        self.assertGreater(ratios['far_x'], 1.0)
        # Pinned from both sides: the [0,0,1.5] case the user asked for used most of the old budget
        # but still passed, so it cannot itself demonstrate the defect. Bounding it above means a
        # tessellation change that made the comment false again would fail this test.
        self.assertGreater(ratios['shifted_z'], 0.5)
        self.assertLess(ratios['shifted_z'], 1.0,
                        'the old rule now fails at [0,0,1.5]: the macro comment and this test both '
                        'state that it passed there, so both must be updated together')

    def test_the_new_rule_accepts_the_same_spheres(self):
        for name, origin in self.cases():
            with self.subTest(case=name):
                ratio, limit = new_rule(tessellate(self.RADIUS, origin), self.RADIUS, origin)
                self.assertLessEqual(ratio, limit)

    def test_the_new_tolerance_still_catches_a_wrong_radius(self):
        for name, origin in self.cases():
            with self.subTest(case=name):
                ratio, limit = new_rule(tessellate(self.RADIUS * 1.01, origin), self.RADIUS, origin)
                self.assertGreater(ratio, limit)

    def test_the_new_tolerance_still_catches_a_non_sphere(self):
        # An oblate body: correct equatorial radius, wrong polar extent.
        points = [(float32(x), float32(y), float32(z * 0.9))
                  for x, y, z in tessellate(self.RADIUS, [0.0, 0.0, 1.5])]
        ratio, limit = new_rule(points, self.RADIUS, [0.0, 0.0, 1.5])
        self.assertGreater(ratio, limit)

    def test_the_recorded_tolerances_follow_the_documented_rule(self):
        """Assert against the macro's REAL output, not against a copy of its formula.

        An earlier version of this test re-implemented max(1e-12, 8*ulp) in the test body, so it
        would have stayed green if the macro changed its rule: a check that looks like a check but
        binds nothing. This version reads a recorded evidence file, so changing the macro's tolerance
        rule without changing the record makes it inconsistent and this test fails.
        """
        candidates = sorted((ROOT / 'test-evidence' / 'static-external-flow').glob(
            '*/s1-*/static_body_geometry.json'))
        # Only success records from a macro revision that wrote the storage-precision fields can be
        # checked this way; failure records and pre-field revisions are skipped explicitly rather
        # than silently.
        # Exactly one known legacy success record predates the storage-precision fields. Any OTHER
        # success record that lacks them is an error, not something to skip: silently dropping such a
        # record would let a regression that removes the field pass unnoticed.
        LEGACY_WITHOUT_FIELDS = {'build-body-positive/s1-static-sphere-body-20261003a'}
        recorded = []
        for path in candidates:
            record = json.loads(path.read_text(encoding='utf-8-sig'))
            if record.get('status') != 'ok':
                continue
            relative = '%s/%s' % (path.parent.parent.name, path.parent.name)
            if 'measured_max_abs_coordinate_m' not in record:
                self.assertIn(relative, LEGACY_WITHOUT_FIELDS,
                              'a successful record outside the known legacy set lacks the '
                              'storage-precision fields, so the tolerance rule cannot be checked '
                              'against it: %s' % path)
                continue
            recorded.append(path)
        self.assertTrue(recorded,
                        'no success record with storage-precision fields found among %d candidates'
                        % len(candidates))
        for path in recorded:
            with self.subTest(evidence=str(path.relative_to(ROOT))):
                record = json.loads(path.read_text(encoding='utf-8-sig'))
                radius = record['body_radius_m']
                ulp = float32_ulp(record['measured_max_abs_coordinate_m'])
                self.assertAlmostEqual(ulp, record['measured_storage_ulp_m'], delta=1e-9 * ulp)
                self.assertAlmostEqual(record['measured_vertex_radius_tolerance_m'],
                                       max(1e-6 * radius, 8.0 * ulp),
                                       delta=1e-9 * max(1e-6 * radius, 8.0 * ulp))
                self.assertAlmostEqual(record['measured_centre_tolerance_m'],
                                       max(1e-12, 8.0 * ulp), delta=1e-9 * max(1e-12, 8.0 * ulp))
                self.assertAlmostEqual(
                    record['measured_vertex_radius_error_ratio'],
                    record['measured_vertex_radius_abs_error_m']
                    / record['measured_vertex_radius_tolerance_m'], delta=1e-9)
                self.assertAlmostEqual(
                    record['measured_centre_offset_ratio'],
                    record['measured_centre_offset_m'] / record['measured_centre_tolerance_m'],
                    delta=1e-9)
                # The centroid the macro recorded must be the one its own bounding box implies.
                box = record['measured_bbox_si_m']
                centre = record['measured_centroid_si_m']
                for axis in range(3):
                    self.assertLessEqual(centre[axis], box[axis + 3] + 1e-12)
                    self.assertGreaterEqual(centre[axis], box[axis] - 1e-12)


if __name__ == '__main__':
    unittest.main()
