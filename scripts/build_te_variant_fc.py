"""Build the registered finite-TE (0.2 mm) numerical-treatment variant of the APC 10x7E propeller.

Source: the archived Fusion section data (fusion-cad/sections_fusion_tefix.json) that produced the approved
apc10x7e_fusion_full.step. This script reproduces the same construction (26 airfoil sections, one fit-point
B-spline per section, short TE closing line, loft, hub boss 21.2x10.4 mm, 180 deg copy, fuse) with the TE
thickness changed from 0 to 0.2 mm along the section thickness normal. The variant is registered as a NUMERICAL
TREATMENT geometry, not the as-designed sharp TE.
Run: /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd scripts/build_te_variant_fc.py
"""
import json
import math
import os
import sys
import traceback

import FreeCAD
import Part
from FreeCAD import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/fusion-cad/sections_fusion_tefix.json')
OUT = os.path.join(ROOT, 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/fusion-cad/apc10x7e_te02_registered_variant.step')
TE_THICKNESS_MM = 0.2


def unit(v):
    return v / v.Length


def trim_section(points, te_mm):
    """Cut the sharp TE where the section thickness equals te_mm and close the section with a straight
    flat TE of exactly te_mm (upper/lower surfaces interpolated at the cut station)."""
    te = points[0]
    le = max(points, key=lambda q: (q - te).Length)
    chord = Vector(0, le.y - te.y, le.z - te.z)
    chord = chord / chord.Length
    normal = Vector(0, -chord.z, chord.y)
    st = [((q - te).dot(chord), (q - te).dot(normal)) for q in points]
    s_vals = [x[0] for x in st]
    k = s_vals.index(max(s_vals))  # LE index; loop = upper(0..k) then lower(k..end)
    upper = st[:k + 1]
    lower = st[k:]
    # find the station where upper-lower thickness first exceeds te_mm (searching from LE back toward TE)
    cut_s = None
    for i in range(len(upper) - 1, 0, -1):
        s_up, t_up = upper[i]
        # nearest lower point by s
        cand = min(lower, key=lambda q: abs(q[0] - s_up))
        if abs(t_up - cand[1]) >= te_mm:
            cut_s = s_up
            break
    if cut_s is None:
        cut_s = 0.0
    # interpolate upper and lower surface t at cut_s
    def interp_t(seq):
        seq = sorted(seq, key=lambda q: q[0])
        if cut_s <= seq[0][0]:
            return seq[0][1]
        for a, b in zip(seq, seq[1:]):
            if a[0] <= cut_s <= b[0]:
                if b[0] == a[0]:
                    return 0.5 * (a[1] + b[1])
                w = (cut_s - a[0]) / (b[0] - a[0])
                return a[1] + w * (b[1] - a[1])
        return seq[-1][1]
    t_up = interp_t(upper)
    t_lo = interp_t(lower)
    excess = 0.5 * (abs(t_up - t_lo) - te_mm)
    if excess > 0:
        # shrink both t values symmetrically about their mean to make the thickness exactly te_mm
        mid = 0.5 * (t_up + t_lo)
        t_up = mid + (0.5 * te_mm if t_up >= t_lo else -0.5 * te_mm)
        t_lo = mid + (-0.5 * te_mm if t_up >= t_lo else 0.5 * te_mm)
    def to_point(sv, tv):
        return te + chord * sv + normal * tv
    p_up = to_point(cut_s, t_up)
    p_lo = to_point(cut_s, t_lo)
    kept = [to_point(sv, tv) for sv, tv in upper if sv > cut_s]
    kept_lower = [to_point(sv, tv) for sv, tv in lower if sv > cut_s]
    kept = kept + list(reversed(kept_lower))
    if (p_up - p_lo).Length < 0.02:
        # thin tip sections cannot reach the nominal 0.2 mm without adding material; keep a minimal
        # 0.02 mm flat TE so the section remains a valid closed profile (registered cap behaviour)
        mid = (p_up + p_lo) * 0.5
        p_up = mid + normal * 0.01
        p_lo = mid - normal * 0.01
    def dedupe(seq):
        out = []
        for q in seq:
            if not out or (q - out[-1]).Length > 1e-4:
                out.append(q)
        return out
    interior = dedupe([q for q in kept if (q - p_up).Length > 1e-4 and (q - p_lo).Length > 1e-4])
    kept = [p_up] + interior + [p_lo]
    return kept, cut_s


def main():
    payload = json.load(open(DATA))
    wires = []
    cuts = []
    for section in payload['sections']:
        pts = [Vector(*q) for q in section['points_mm']]
        kept, cut = trim_section(pts, TE_THICKNESS_MM)
        te_len = (kept[-1] - kept[0]).Length
        cuts.append((section['index'], round(cut, 4), len(pts) - len(kept), round(te_len, 5)))
        curve = Part.BSplineCurve()
        curve.interpolate(kept)
        wire = Part.Wire([curve.toShape(), Part.makeLine(kept[-1], kept[0])])
        if not wire.isClosed():
            raise RuntimeError('section S%d wire is not closed' % section['index'])
        if not wire.isValid():
            raise RuntimeError('section S%d wire is invalid' % section['index'])
        wires.append(wire)
    te_lens = [c[3] for c in cuts]
    print('TE_VARIANT_CUTS', cuts, flush=True)
    print('TE_VARIANT_TE_THICKNESS_MM min=%.5f max=%.5f mean=%.5f' % (min(te_lens), max(te_lens), sum(te_lens)/len(te_lens)), flush=True)

    loft = Part.makeLoft(wires, solid=True, ruled=False, closed=False)
    if not loft.isValid():
        raise RuntimeError('loft invalid')
    hub = Part.makeCylinder(10.6, 10.4, Vector(0, 0, -5.2), Vector(0, 0, 1))
    blade2 = loft.rotated(Vector(0, 0, 0), Vector(0, 0, 1), 180.0)
    solid = hub.fuse(loft).fuse(blade2).removeSplitter()
    if not solid.isValid():
        raise RuntimeError('fused solid invalid')
    if len(solid.Solids) != 1:
        raise RuntimeError('expected exactly one solid, got %d' % len(solid.Solids))
    Part.export([solid], OUT)
    bb = solid.BoundBox
    print('TE_VARIANT_SOLID volume_mm3=%.3f faces=%d edges=%d bbox=%.4f,%.4f,%.4f,%.4f,%.4f,%.4f'
          % (solid.Volume, len(solid.Faces), len(solid.Edges), bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax),
          flush=True)
    print('TE_VARIANT_STEP', OUT, flush=True)


print('TE_VARIANT_SCRIPT_START', flush=True)
try:
    main()
except Exception:
    traceback.print_exc()
    print('TE_VARIANT_SCRIPT_FAILED', flush=True)
    raise
