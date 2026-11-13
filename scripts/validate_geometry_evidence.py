"""Validate a static-body geometry evidence record, fail closed.

Why this exists: the building macro's gates are written as `if (error > tolerance) throw`. That
comparison is FALSE for NaN, so a non-finite quantity would pass silently - the most dangerous shape
of fail-open defect this project has hit. The macro now checks every read-back and every measured
quantity with isFinite before comparing it, and this validator is the second, independent line: it
refuses any record that contains a non-finite number or a bare JSON `NaN`/`Infinity` token (neither
is legal JSON), and it re-derives the declared gates instead of trusting the writer's own verdict.

It is also the formal counter-example harness for AGENTS.md section 4: point it at a copy of a good
record with one number replaced by NaN and it must refuse.

Usage:
    python scripts/validate_geometry_evidence.py --evidence <static_body_geometry.json> \
        [--radius-m 0.05]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import struct
from pathlib import Path


def float32_ulp(value: float) -> float:
    """Spacing of 32-bit floats at |value|, by stepping the bit pattern.

    math.ulp() must not be used: it returns the 64-bit spacing, about 1e9 times smaller, which is the
    mistake an independent review found in an early emulation. Java's Math.ulp((float) x) takes a float
    and returns a float, so the macro is correct.
    """
    magnitude = abs(float(value))
    bits = struct.unpack('<I', struct.pack('<f', magnitude))[0]
    lower = struct.unpack('<f', struct.pack('<I', bits))[0]
    upper = struct.unpack('<f', struct.pack('<I', bits + 1))[0]
    return abs(upper - lower)

# Bare NaN/Infinity tokens are not valid JSON; Python's parser accepts them, so reject them by text
# as well as by value. Inside a string they are harmless, so only match them outside quotes.
BARE_TOKEN = re.compile(r'(?<!["\w])(NaN|-?Infinity)(?!["\w])')
# A producer that degrades a non-finite number to a *quoted string* (which the macro's own writer does)
# would otherwise slip past both the token regex and the numeric walk. These names are REQUIRED and
# must be JSON numbers: an absent key is refused rather than skipped, so a record cannot dodge the
# re-derivations below by omitting their inputs.
REQUIRED_NUMERIC = (
    'body_radius_m',
    'measured_vertex_radius_relative_error',
    'measured_vertex_radius_abs_error_m',
    'measured_vertex_radius_tolerance_m',
    'measured_vertex_radius_error_ratio',
    'measured_centre_offset_m',
    'measured_centre_tolerance_m',
    'measured_centre_offset_ratio',
    'measured_storage_ulp_m',
    'measured_max_abs_coordinate_m',
    'measured_min_vertex_radius_si_m',
    'measured_max_vertex_radius_si_m',
    'measured_enclosed_volume_si_m3',
    'analytic_volume_si_m3',
    'measured_bbox_half_extent_relative_deviation',
    'measured_volume_relative_deviation',
    'triangle_count',
    'part_count',
    'regions',
    'iteration',
)
# Any string that float() reads as non-finite is refused, plus the spellings a Java writer emits that
# float() rejects. This is deliberately a value test rather than a spelling list: the whole point of a
# second-line validator is not to depend on what the writer happens to produce.
NON_FINITE_WORDS = {'nan', 'inf', 'infinity', '+nan', '-nan', '+inf', '-inf', '+infinity',
                    '-infinity', '1.#inf', '-1.#inf', '1.#qnan', 'nan(ind)', 'nan(snan)', '-nan(ind)'}


def walk(node, path='$'):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, '%s.%s' % (path, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, '%s[%d]' % (path, index))
    else:
        yield path, node


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--evidence', type=Path, required=True)
    ap.add_argument('--radius-m', type=float, default=None)
    a = ap.parse_args()

    problems: list[str] = []
    text = a.evidence.read_text(encoding='utf-8-sig')
    if BARE_TOKEN.search(text):
        problems.append('the record contains a bare NaN/Infinity token, which is not valid JSON')

    record = json.loads(text)
    if record.get('status') != 'ok':
        problems.append('status is %r, not "ok"' % record.get('status'))
    if record.get('usable') is not True:
        problems.append('usable is %r, not true' % record.get('usable'))
    if not record.get('complete_marker'):
        problems.append('the record carries no completion marker')

    # Every number, anywhere, must be finite. This is the check that a `> tolerance` comparison
    # cannot make for itself.
    checked = 0
    for path, value in walk(record):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        checked += 1
        if not math.isfinite(value):
            problems.append('%s is not finite: %r' % (path, value))

    # A quoted "NaN" is a non-finite number wearing a string, and it is exactly what a writer that
    # degrades a bad value produces. Refuse it by value, not just by token.
    for path, value in walk(record):
        if not isinstance(value, str):
            continue
        text_value = value.strip()
        if text_value.lower() in NON_FINITE_WORDS:
            problems.append('%s is a string spelling a non-finite value: %r' % (path, value))
            continue
        try:
            parsed = float(text_value)
        except ValueError:
            continue
        if not math.isfinite(parsed):
            problems.append('%s is a string that float() reads as non-finite: %r' % (path, value))
    for key in REQUIRED_NUMERIC:
        if key not in record:
            problems.append('%s is required and absent' % key)
            continue
        value = record[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            problems.append('%s must be a JSON number, got %r' % (key, value))

    radius = a.radius_m if a.radius_m is not None else record.get('body_radius_m')
    if not isinstance(radius, (int, float)) or isinstance(radius, bool) or not math.isfinite(radius) \
            or radius <= 0:
        problems.append('no usable sphere radius (argument or body_radius_m)')

    gates = [('measured_vertex_radius_error_ratio', '<=', 1.0),
             ('measured_centre_offset_ratio', '<=', 1.0),
             ('measured_bbox_half_extent_relative_deviation', '<=', 0.05),
             ('measured_volume_relative_deviation', '<=', 0.05)]
    for key, _, limit in gates:
        value = record.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            problems.append('%s is missing or not a number: %r' % (key, value))
            continue
        if not math.isfinite(value):
            problems.append('%s is not finite: %r' % (key, value))
            continue
        if value > limit:
            problems.append('%s = %r exceeds %r' % (key, value, limit))

    # Re-derive what can be re-derived instead of trusting the writer.
    if isinstance(radius, (int, float)) and not isinstance(radius, bool) and math.isfinite(radius) \
            and radius > 0:
        analytic = 4.0 / 3.0 * math.pi * radius ** 3
        claimed = record.get('analytic_volume_si_m3')
        if not isinstance(claimed, (int, float)) or isinstance(claimed, bool) \
                or not math.isfinite(claimed):
            problems.append('analytic_volume_si_m3 is missing or not finite')
        elif abs(claimed - analytic) > 1e-12 * analytic:
            problems.append('analytic_volume_si_m3 = %r does not equal 4/3*pi*R^3 = %r'
                            % (claimed, analytic))
        volume = record.get('measured_enclosed_volume_si_m3')
        if isinstance(volume, (int, float)) and not isinstance(volume, bool) \
                and math.isfinite(volume) and volume <= 0:
            problems.append('measured_enclosed_volume_si_m3 must be positive, got %r' % volume)
        # Re-derive the four gate ratios from the quantities they are computed from, so a record
        # cannot widen a tolerance and then declare a small ratio against it.
        derived = (
            ('measured_vertex_radius_error_ratio',
             lambda: abs(record['measured_vertex_radius_abs_error_m'])
             / record['measured_vertex_radius_tolerance_m']),
            ('measured_centre_offset_ratio',
             lambda: abs(record['measured_centre_offset_m']) / record['measured_centre_tolerance_m']),
            ('measured_volume_relative_deviation',
             lambda: abs(record['measured_enclosed_volume_si_m3']
                         - record['analytic_volume_si_m3']) / record['analytic_volume_si_m3']),
        )
        for key, function in derived:
            try:
                expected = function()
            except (KeyError, TypeError, ZeroDivisionError) as exc:
                problems.append('cannot re-derive %s: %s' % (key, exc))
                continue
            declared = record.get(key)
            if not isinstance(declared, (int, float)) or isinstance(declared, bool) \
                    or not math.isfinite(declared):
                problems.append('%s is not a finite number: %r' % (key, declared))
            elif abs(declared - expected) > 1e-9 * max(abs(expected), 1e-12):
                problems.append('%s = %r but its own inputs give %r' % (key, declared, expected))
        # The tolerances must be the ones the recorded storage precision implies. Without this, a
        # record could widen its own tolerance and then declare a small ratio against it.
        storage_ulp = record.get('measured_storage_ulp_m')
        max_abs = record.get('measured_max_abs_coordinate_m')
        if isinstance(max_abs, (int, float)) and not isinstance(max_abs, bool) \
                and math.isfinite(max_abs) and max_abs >= 0:
            expected_ulp = float32_ulp(max_abs)
            if not isinstance(storage_ulp, (int, float)) or isinstance(storage_ulp, bool) \
                    or not math.isfinite(storage_ulp):
                problems.append('measured_storage_ulp_m is not a finite number: %r' % storage_ulp)
            elif abs(storage_ulp - expected_ulp) > 1e-9 * max(expected_ulp, 1e-45):
                problems.append('measured_storage_ulp_m = %r but the recorded max |coordinate| %r '
                                'implies %r' % (storage_ulp, max_abs, expected_ulp))
            expected_radius_tol = max(1e-6 * radius, 8.0 * expected_ulp)
            expected_centre_tol = max(1e-12, 8.0 * expected_ulp)
            for key, expected in (('measured_vertex_radius_tolerance_m', expected_radius_tol),
                                  ('measured_centre_tolerance_m', expected_centre_tol)):
                declared = record.get(key)
                if not isinstance(declared, (int, float)) or isinstance(declared, bool) \
                        or not math.isfinite(declared):
                    problems.append('%s is not a finite number: %r' % (key, declared))
                elif abs(declared - expected) > 1e-9 * max(expected, 1e-45):
                    problems.append('%s = %r but the recorded storage precision for max |coordinate| '
                                    '%r implies %r' % (key, declared, max_abs, expected))
        box = record.get('measured_bbox_si_m')
        half = record.get('measured_bbox_half_extent_si_m')
        if isinstance(box, list) and len(box) == 6 and isinstance(half, list) and len(half) == 3:
            for index in range(3):
                expected = (box[index + 3] - box[index]) / 2.0
                if not math.isfinite(half[index]) or abs(half[index] - expected) > 1e-12 * max(
                        abs(expected), 1e-12):
                    problems.append('measured_bbox_half_extent_si_m[%d] = %r disagrees with the '
                                    'bounding box itself (%r)' % (index, half[index], expected))
        else:
            problems.append('the bounding box fields are missing or malformed')
        if isinstance(half, list) and len(half) == 3 and isinstance(radius, (int, float)) \
                and not isinstance(radius, bool) and math.isfinite(radius) and radius > 0:
            worst = max(abs(half[index] - radius) / radius for index in range(3))
            declared = record.get('measured_bbox_half_extent_relative_deviation')
            if not isinstance(declared, (int, float)) or isinstance(declared, bool) \
                    or not math.isfinite(declared):
                problems.append('measured_bbox_half_extent_relative_deviation is not a finite number: %r'
                                % declared)
            elif abs(declared - worst) > 1e-9 * max(abs(worst), 1e-12):
                problems.append('measured_bbox_half_extent_relative_deviation = %r but the recorded '
                                'half extents and radius give %r' % (declared, worst))

    summary = {
        'evidence': str(a.evidence),
        'numbers_checked_for_finiteness': checked,
        'radius_m': radius,
        'valid': not problems,
        'problems': problems,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
