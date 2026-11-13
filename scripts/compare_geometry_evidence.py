"""Make the "a second macro re-measured the geometry and agreed" claim executable.

An independent review of the preflight audit (2026-10-03) recorded a blocker: approving the audit
record could not support a claim of agreement, because nothing compared the audit's numbers with the
building macro's own record. A claim of agreement that only exists in a report is exactly the
prose-instead-of-code pattern this project forbids, so the comparison lives here.

It is deliberately conservative about what it checks:
  * the audit's part topology flags and bounding box ARE compared, because the audit re-opens the .sim
    in a fresh session and re-measures them;
  * the audit re-uses the same MeshPart API for the flags and the same export-plus-reduce algorithm
    for the box, so this is a second *observation*, not a second *method* - the output says so;
  * per-vertex radius and enclosed volume are NOT compared here, because the audit does not measure
    them. Those were re-measured on the Mac by scripts/local_stl_remeasure.py.

Usage:
    python scripts/compare_geometry_evidence.py --builder <static_body_geometry.json> \
        --audit <automation_preflight.json> --part BODY
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def load(path: Path) -> dict:
    # PowerShell writes a UTF-8 BOM with Set-Content -Encoding UTF8.
    return json.loads(path.read_text(encoding='utf-8-sig'))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--builder', type=Path, required=True)
    ap.add_argument('--audit', type=Path, required=True)
    ap.add_argument('--part', default='BODY')
    ap.add_argument('--bbox-rtol', type=float, default=1e-9)
    a = ap.parse_args()

    builder = load(a.builder)
    audit = load(a.audit)
    problems: list[str] = []
    compared: list[dict] = []

    if builder.get('status') != 'ok':
        problems.append('builder record is not ok: %r' % builder.get('status'))
    parts = [p for p in (audit.get('geometryParts') or [])
             if isinstance(p, dict) and (p.get('qualifiedName') or p.get('name')) == a.part]
    if len(parts) != 1:
        problems.append('the audit must report exactly one part named %r, found %d'
                        % (a.part, len(parts)))
        part: dict = {}
    else:
        part = parts[0]

    if part:
        for key, claimed, actual in (
                ('is_closed', builder.get('is_closed'), part.get('isClosed')),
                ('is_manifold', builder.get('is_manifold'), part.get('isManifold')),
                ('triangle_count', builder.get('triangle_count'), part.get('triangleCount'))):
            ok = claimed == actual
            compared.append({'quantity': key, 'builder': claimed, 'audit': actual, 'agree': ok})
            if not ok:
                problems.append('%s disagrees: builder %r vs audit %r' % (key, claimed, actual))
        claimed_box = builder.get('measured_bbox_si_m') or []
        actual_box = part.get('boundingBoxRaw')
        if not isinstance(actual_box, list) or len(actual_box) != 6:
            problems.append('the audit reports no usable bounding box for %r' % a.part)
        elif len(claimed_box) != 6:
            problems.append('the builder record has no usable measured bounding box')
        else:
            # boundingBoxRaw is in model units and model_length_unit is metres for this case.
            unit = builder.get('model_length_unit')
            factor = {'m': 1.0, 'mm': 0.001, 'cm': 0.01, 'in': 0.0254}.get(unit)
            if factor is None:
                problems.append('unknown model_length_unit %r' % unit)
            else:
                worst = 0.0
                for index in range(6):
                    builder_value = float(claimed_box[index])
                    audit_value = float(actual_box[index]) * factor
                    scale = max(abs(builder_value), abs(audit_value), 1e-12)
                    worst = max(worst, abs(builder_value - audit_value) / scale)
                ok = worst <= a.bbox_rtol
                compared.append({'quantity': 'measured_bbox_si_m', 'builder': claimed_box,
                                 'audit': actual_box, 'unit': unit,
                                 'max_relative_difference': worst, 'agree': ok})
                if not ok:
                    problems.append('bounding boxes disagree by %.3e relative, above %.1e'
                                    % (worst, a.bbox_rtol))
        if audit.get('status') not in {'ok', 'partial'}:
            problems.append('audit status is %r' % audit.get('status'))

    summary = {
        'part': a.part,
        'scope': ('the audit re-opens the .sim in a fresh session and re-reads the same MeshPart '
                  'topology flags and a fresh bounding box; it is a second observation, and its '
                  'bounding box shares the export-plus-reduce algorithm with the builder'),
        'not_compared_here': ['measured_min_vertex_radius_si_m', 'measured_max_vertex_radius_si_m',
                              'measured_enclosed_volume_si_m3'],
        'compared': compared,
        'agreement_established': not problems,
        'problems': problems,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
