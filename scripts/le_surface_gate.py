#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
le_surface_gate.py — 前缘表面网格闸门（只做 2D，不生成体网格）。

诊断依据（见 test-evidence/te-surface-mesh-gate/leading-edge-curvature-report.md）：
椭圆截面 r_LE = 0.0072c，在 Mesh.MeshSizeFromCurvature = 20 下于 r ≥ 180 mm 的
前缘逼出 0.02–0.06 mm 的三角形条带（17,096 个 < 0.06 mm），与叶身 0.35 mm 相差 17 倍，
坏四面体就长在过渡带上。

本闸门对给定 STEP 做同样的测量，判据：
    r ≥ 180 mm 的前缘细三角形 (<0.06 mm) 数量应大幅低于旧几何的 17,096

用法: python le_surface_gate.py <rotor.step> <out_dir> [--curv 8]
"""

import argparse
import json
import math
import sys
from pathlib import Path

import gmsh
import numpy as np

UNIT = 1000.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--curv", type=float, default=8.0)
    ap.add_argument("--blade", type=float, default=0.35)
    ap.add_argument("--cap", type=float, default=0.12)
    ap.add_argument("--far", type=float, default=150.0)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", a.curv)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    gmsh.option.setNumber("Mesh.MeshSizeMin", 0.002)
    gmsh.option.setNumber("Mesh.MeshSizeMax", a.far)
    gmsh.option.setNumber("Mesh.Algorithm", 6)
    gmsh.option.setNumber("Mesh.MshFileVersion", 4.1)
    gmsh.model.add("le_gate")

    blade = gmsh.model.occ.importShapes(str(a.src))
    if len(blade) != 1 or blade[0][0] != 3:
        raise ValueError("expected one closed solid, got %r" % (blade,))

    rotor_r, half_l = 0.30 * UNIT, 0.15 * UNIT
    rc = gmsh.model.occ.addCylinder(0, 0, -half_l, 0, 0, 2 * half_l, rotor_r)
    rotor, _ = gmsh.model.occ.cut([(3, rc)], blade, removeObject=True, removeTool=True)
    gmsh.model.occ.synchronize()
    if len(rotor) != 1:
        raise ValueError("rotor fluid volume not unique: %r" % (rotor,))
    rotor_tag = rotor[0][1]

    wall = {t for d, t in gmsh.model.getBoundary([(3, rotor_tag)], oriented=False) if d == 2}
    # 只保留叶片本体：圆柱壁面（r=300 或 z=±150）以外的面
    blade_faces = []
    for t in wall:
        bb = gmsh.model.getBoundingBox(2, t)
        if abs(bb[2] + half_l) < 1e-3 and abs(bb[5] - half_l) < 1e-3:
            continue
        if math.hypot(bb[0], bb[1]) > 299.0 and math.hypot(bb[3], bb[4]) > 299.0:
            continue
        blade_faces.append(t)

    areas = sorted(((t, gmsh.model.occ.getMass(2, t)) for t in blade_faces), key=lambda kv: kv[1])
    cap = [t for t, ar in areas if ar < 120.0]
    bl = sorted(set(blade_faces) - set(cap))
    print("blade faces=%d  cap(area<120mm2)=%d  BL faces=%d"
          % (len(blade_faces), len(cap), len(bl)))
    print("cap areas mm2: %s" % ["%.2f" % gmsh.model.occ.getMass(2, t) for t in cap], flush=True)

    f = gmsh.model.mesh.field
    d_cap = f.add("Distance"); f.setNumbers(d_cap, "SurfacesList", sorted(cap))
    f.setNumber(d_cap, "Sampling", 200)
    t_cap = f.add("Threshold"); f.setNumber(t_cap, "InField", d_cap)
    f.setNumber(t_cap, "SizeMin", a.cap); f.setNumber(t_cap, "SizeMax", a.blade)
    f.setNumber(t_cap, "DistMin", 0.0); f.setNumber(t_cap, "DistMax", 1.0)
    f.setNumber(t_cap, "StopAtDistMax", 1)

    d_bl = f.add("Distance"); f.setNumbers(d_bl, "SurfacesList", bl)
    f.setNumber(d_bl, "Sampling", 120)
    t_bl = f.add("Threshold"); f.setNumber(t_bl, "InField", d_bl)
    f.setNumber(t_bl, "SizeMin", a.blade); f.setNumber(t_bl, "SizeMax", a.far)
    f.setNumber(t_bl, "DistMin", 0.0); f.setNumber(t_bl, "DistMax", 800.0)
    f.setNumber(t_bl, "StopAtDistMax", 1)

    comb = f.add("Min"); f.setNumbers(comb, "FieldsList", [t_cap, t_bl])
    f.setAsBackgroundMesh(comb)

    print("generating 2-D mesh (curv=%.1f blade=%.2f cap=%.2f) ..." % (a.curv, a.blade, a.cap),
          flush=True)
    gmsh.model.mesh.generate(2)

    # 只统计叶片本体面上的三角形
    conn = []
    for t in blade_faces:
        types, etags, enodes = gmsh.model.mesh.getElements(2, t)
        for et, tg, nd in zip(types, etags, enodes):
            if gmsh.model.mesh.getElementProperties(et)[0].startswith("Triangle"):
                conn.append(np.asarray(nd, dtype=np.int64).reshape(-1, 3))
    tags, coords, _ = gmsh.model.mesh.getNodes()
    tags = np.asarray(tags, dtype=np.int64)
    coords = np.asarray(coords, dtype=np.float64).reshape(-1, 3)
    order = np.argsort(tags)
    tmap = {int(t): i for i, t in enumerate(tags)}
    c = np.vstack(conn)
    P = coords[[tmap[int(x)] for x in c.reshape(-1)]].reshape(-1, 3, 3)
    e = np.stack([np.linalg.norm(P[:, 1] - P[:, 0], axis=1),
                  np.linalg.norm(P[:, 2] - P[:, 1], axis=1),
                  np.linalg.norm(P[:, 0] - P[:, 2], axis=1)], axis=1).mean(axis=1)
    # 本脚本的模型建在毫米下（STEP 为 mm，圆柱半径传 300），不要再乘 1e3
    ctr = P.mean(axis=1)
    r = np.hypot(ctr[:, 0], ctr[:, 1])
    z = ctr[:, 2]

    bands = []
    print("\n%-14s %9s %9s %9s %9s %9s" % ("r band (mm)", "n", "min", "p10", "median", "n<0.06"))
    for lo, hi in [(0, 50), (50, 100), (100, 150), (150, 180), (180, 195),
                   (195, 200), (200, 203), (203, 205), (205, 206), (206, 300)]:
        m = (r >= lo) & (r < hi)
        if not m.any():
            continue
        n_small = int((e[m] < 0.06).sum())
        bands.append({"r_mm": [lo, hi], "n": int(m.sum()), "min": float(e[m].min()),
                      "p10": float(np.quantile(e[m], 0.1)), "median": float(np.median(e[m])),
                      "n_below_006": n_small})
        print("%3d-%-10d %9d %9.4f %9.4f %9.4f %9d"
              % (lo, hi, m.sum(), e[m].min(), np.quantile(e[m], 0.1), np.median(e[m]), n_small))

    outer = (r >= 180.0)
    n_outer_small = int((outer & (e < 0.06)).sum())
    print("\n叶片面上三角形总数 = %d" % len(e))
    print("r>=180 mm 且 <0.06 mm 的三角形 = %d   （旧椭圆几何: 17,096）" % n_outer_small)
    if n_outer_small:
        zs = np.sort(z[outer & (e < 0.06)])
        print("    其 z 分布: min %.3f  med %.3f  max %.3f mm" % (zs[0], np.median(zs), zs[-1]))

    rep = {"step": str(a.src), "curvature": a.curv, "blade_mm": a.blade, "cap_mm": a.cap,
           "n_triangles": int(len(e)), "min_edge_mm": float(e.min()),
           "outer_small_count": n_outer_small,
           "old_ellipse_outer_small_count": 17096,
           "radial_bands": bands}
    (a.out / "le_surface_gate.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False) + "\n")
    print("WROTE %s" % (a.out / "le_surface_gate.json"))
    gmsh.finalize()


if __name__ == "__main__":
    main()
