#!/usr/bin/env python3
"""Compare measured native local-control effects under a prospective plan.

Only reads input files. JSON on stdout; no mesh/runtime/solver operations.
"""
import argparse
import csv
import hashlib
import io
import itertools
import json
import math
from pathlib import Path
import statistics


class EffectError(ValueError):
    def __init__(self, message, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics


def require(condition, message):
    if not condition:
        raise EffectError(message)


def number(value):
    require(not isinstance(value, bool), "boolean is not a number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise EffectError("invalid numeric value")
    require(math.isfinite(result), "nonfinite numeric value")
    return result


def identifier(value):
    require(isinstance(value, str) and value.isascii() and value.isdigit() and int(value) > 0,
            "invalid native integer ID")
    return int(value)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def read_csv(path, header, hashes):
    raw = path.read_bytes()
    hashes[path.name] = hashlib.sha256(raw).hexdigest()
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
    require(reader.fieldnames == header, "unexpected CSV header: " + path.name)
    for row in reader:
        require(None not in row and all(value is not None for value in row.values()), "incomplete CSV row")
        yield row


def delta(a, b):
    return [a[i] - b[i] for i in range(3)]


def cross(a, b):
    return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]


def norm(vector):
    value = math.hypot(*vector)
    require(math.isfinite(value), "nonfinite derived length")
    return value


def measure(directory, transform, box):
    directory = Path(directory)
    hashes, nodes = {}, {}
    eligibility_errors = []
    for filename, predicate in (("state.json", lambda obj: obj.get("status") == "COMPLETED"),
                                 ("native-readback.json", lambda obj: obj.get("status") == "ok" and obj.get("native_quality_passed") is True)):
        raw = (directory / filename).read_bytes()
        hashes[filename] = hashlib.sha256(raw).hexdigest()
        obj = json.loads(raw, object_pairs_hook=unique_object)
        require(isinstance(obj, dict), "native status record must be an object")
        if not predicate(obj):
            eligibility_errors.append(filename + " does not certify completed native quality")
    shift = transform["subtract"]
    divisor = number(transform["divide"])
    require(divisor > 0 and len(shift) == 3, "invalid normalization transform")
    shift = [number(x) for x in shift]
    for row in read_csv(directory / "nodes.csv", ["node_id", "x", "y", "z"], hashes):
        nid = identifier(row["node_id"])
        require(nid not in nodes, "duplicate node ID")
        point = [(number(row[axis]) - shift[i]) / divisor for i, axis in enumerate(("x", "y", "z"))]
        require(all(math.isfinite(x) for x in point), "nonfinite normalized node")
        nodes[nid] = point
    require(nodes, "missing nodes")
    bbox = [min(p[i] for p in nodes.values()) for i in range(3)] + [max(p[i] for p in nodes.values()) for i in range(3)]
    require(all(bbox[i] < bbox[i+3] for i in range(3)), "nonpositive normalized bbox")
    def connection(row, size):
        conn = [identifier(x) for x in row["nodes"].split()]
        require(len(conn) == size and len(set(conn)) == size, "invalid native connectivity")
        require(set(conn) <= nodes.keys(), "unknown connectivity node")
        return conn
    tets, element_ids, cell_keys = [], set(), set()
    configurations = {204: 4, 205: 5, 206: 6, 208: 8}
    for row in read_csv(directory / "elements.csv", ["element_id", "config", "nodes"], hashes):
        eid, config = identifier(row["element_id"]), identifier(row["config"])
        require(eid not in element_ids and config in configurations, "duplicate element ID or unsupported config")
        element_ids.add(eid)
        conn = connection(row, configurations[config])
        key = tuple(sorted(conn))
        require(key not in cell_keys, "duplicate volume connectivity")
        cell_keys.add(key)
        if config != 204:
            continue
        points = [nodes[n] for n in conn]
        triple = sum(delta(points[1], points[0])[i] * cross(delta(points[2], points[0]), delta(points[3], points[0]))[i] for i in range(3))
        require(math.isfinite(triple) and abs(triple) > 0, "degenerate tetrahedron")
        center = [sum(p[i] for p in points) / 4 for i in range(3)]
        if all(box[i] <= center[i] <= box[i+3] for i in range(3)):
            maximum = max(norm(delta(a, b)) for a, b in itertools.combinations(points, 2))
            require(maximum > 0, "nonpositive maximum tetrahedron edge")
            tets.append(maximum)
    areas, boundary_ids, boundary_keys = [], set(), set()
    header = ["label", "role", "surface_id", "element_id", "config", "nodes"]
    for row in read_csv(directory / "boundary_faces.csv", header, hashes):
        eid, config = identifier(row["element_id"]), identifier(row["config"])
        identifier(row["surface_id"])
        require(eid not in boundary_ids and config in (103, 104), "duplicate boundary ID or unsupported config")
        require(bool(row["label"]) and row["role"] in ("wall", "inlet", "outlet", "farfield", "symmetry", "interface", "periodic"), "invalid boundary label/role")
        boundary_ids.add(eid)
        conn = connection(row, config - 100)
        key = tuple(sorted(conn))
        require(key not in boundary_keys, "duplicate boundary connectivity")
        boundary_keys.add(key)
        if row["role"] == "wall" and config == 103:
            a, b, c = [nodes[n] for n in conn]
            area = norm(cross(delta(b, a), delta(c, a))) / 2
            require(area > 0, "nonpositive wall triangle area")
            areas.append(area)
    return {"native_directory": str(directory.resolve()), "sha256": hashes, "normalized_bbox": bbox,
            "eligible_for_effect_certification": not eligibility_errors, "eligibility_errors": eligibility_errors,
            "wall_triangle_count": len(areas), "box_tet_count": len(tets),
            "wall_triangle_area_median": statistics.median(areas) if areas else None,
            "box_tet_max_edge_median": statistics.median(tets) if tets else None}


def evaluate(baseline_directory, candidate_directory, plan_path):
    raw = Path(plan_path).read_bytes()
    plan = json.loads(raw, object_pairs_hook=unique_object)
    machine = plan["measurement"]
    box = [number(x) for x in machine["centroid_box_normalized_raw"]]
    require(len(box) == 6 and all(box[i] < box[i+3] for i in range(3)), "invalid centroid box")
    minimum_wall, minimum_tets = machine["minimum_wall_triangles"], machine["minimum_box_tets"]
    require(type(minimum_wall) is int and minimum_wall >= 3 and type(minimum_tets) is int and minimum_tets >= 3, "insufficient prospective sample minimum")
    area_limit, edge_limit = number(machine["wall_area_ratio_max"]), number(machine["tet_max_edge_ratio_max"])
    require(0 < area_limit < 1 and 0 < edge_limit < 1, "invalid prospective effect thresholds")
    tolerance = number(machine["bbox_relative_tolerance"])
    require(0 < tolerance <= 1e-6, "invalid bbox tolerance")
    baseline = measure(baseline_directory, machine["baseline_transform"], box)
    candidate = measure(candidate_directory, machine["candidate_transform"], box)
    diagnostics = {"baseline": baseline, "candidate": candidate,
                   "plan_sha256": hashlib.sha256(raw).hexdigest()}
    for sample in (baseline, candidate):
        if sample["wall_triangle_count"] < minimum_wall or sample["box_tet_count"] < minimum_tets:
            raise EffectError("insufficient wall triangle or box tetrahedron samples", diagnostics)
    span = max(baseline["normalized_bbox"][i+3] - baseline["normalized_bbox"][i] for i in range(3))
    require(math.isfinite(span) and span > 0, "invalid derived bbox scale")
    if not all(abs(a-b) <= tolerance * span for a, b in zip(baseline["normalized_bbox"], candidate["normalized_bbox"])):
        raise EffectError("normalized bbox mismatch", diagnostics)
    area_ratio = candidate["wall_triangle_area_median"] / baseline["wall_triangle_area_median"]
    edge_ratio = candidate["box_tet_max_edge_median"] / baseline["box_tet_max_edge_median"]
    require(math.isfinite(area_ratio) and math.isfinite(edge_ratio) and area_ratio > 0 and edge_ratio > 0,
            "invalid derived effect ratios")
    eligible = baseline["eligible_for_effect_certification"] and candidate["eligible_for_effect_certification"]
    passed = eligible and area_ratio <= area_limit and edge_ratio <= edge_limit
    return {"status": "EFFECT_REFUSED" if not eligible else ("EFFECT_PASSED" if passed else "EFFECT_FAILED"), "local_control_effect_verified": passed,
            "scope": plan["scope"], "do_not_claim": plan["do_not_claim"],
            "plan_sha256": hashlib.sha256(raw).hexdigest(), "measurement_plan": machine,
            "baseline": baseline, "candidate": candidate,
            "wall_triangle_area_ratio": area_ratio, "box_tet_max_edge_ratio": edge_ratio,
            "exact_size_enforcement_verified": False, "GCI_verified": False,
            "STAR_quality_verified": False, "engineering_acceptance_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--plan", required=True)
    args = parser.parse_args()
    try:
        result = evaluate(args.baseline, args.candidate, args.plan)
    except (ValueError, OSError, KeyError, TypeError, csv.Error) as exc:
        result = {"status": "REFUSED", "local_control_effect_verified": False, "error": str(exc)}
        if isinstance(exc, EffectError) and exc.diagnostics is not None:
            result["diagnostics"] = exc.diagnostics
    print(json.dumps(result, allow_nan=False, indent=2))
    return 0 if result["local_control_effect_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
