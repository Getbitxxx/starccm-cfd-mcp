"""Offline synthetic positive and adversarial local-control measurements."""
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluate_hypermesh_control_effect import evaluate, EffectError

PLAN = Path(__file__).resolve().parents[1] / "test-evidence/hypermesh-cfd-mcp-interface-20261014/local-control-prospective.json"


def fixture(directory, *, scale=1, shift=(0, 0, 0), size=1, count=3):
    directory.mkdir()
    nodes, cells, boundary = [], [], []
    def add(point):
        nid = len(nodes) + 1
        nodes.append([nid] + [point[i] * scale + shift[i] for i in range(3)])
        return nid
    add((0, 0, 0))
    add((20, 10, 5))
    for index in range(count):
        x = 7 + index * 2
        conn = [add(p) for p in ((x, 3, 2), (x+size, 3, 2), (x, 3+size, 2), (x, 3, 2+size))]
        cells.append([index+1, 204, " ".join(map(str, conn))])
        conn = [add(p) for p in ((x, 3, 0), (x+size, 3, 0), (x, 3+size, 0))]
        boundary.append(["renamed_wall", "wall", 55, 100+index, 103, " ".join(map(str, conn))])
    for filename, header, rows in (("nodes.csv", ["node_id", "x", "y", "z"], nodes),
            ("elements.csv", ["element_id", "config", "nodes"], cells),
            ("boundary_faces.csv", ["label", "role", "surface_id", "element_id", "config", "nodes"], boundary)):
        with (directory / filename).open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(rows)
    (directory / "state.json").write_text(json.dumps({"status": "COMPLETED", "fixture": "SYNTHETIC_NOT_REAL_RUN"}))
    (directory / "native-readback.json").write_text(json.dumps({"status": "ok", "native_quality_passed": True, "fixture": "SYNTHETIC_NOT_REAL_RUN"}))


class ControlEffectTests(unittest.TestCase):
    def pair(self, folder, *, candidate_size=1, count=3):
        baseline, candidate = Path(folder)/"b", Path(folder)/"c"
        fixture(baseline, scale=5, shift=(125, -250, 0), size=2)
        fixture(candidate, size=candidate_size, count=count)
        return baseline, candidate

    def test_scaled_translated_positive_and_no_effect_negative(self):
        for size, passed in ((1, True), (2, False)):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as folder:
                b, c = self.pair(folder, candidate_size=size)
                result = evaluate(b, c, PLAN)
                self.assertEqual(result["local_control_effect_verified"], passed)
                self.assertAlmostEqual(result["wall_triangle_area_ratio"], (size/2)**2)
                self.assertAlmostEqual(result["box_tet_max_edge_ratio"], size/2)
                self.assertFalse(result["GCI_verified"])
                self.assertFalse(result["exact_size_enforcement_verified"])
                self.assertEqual(len(result["baseline"]["sha256"]), 5)

    def test_failed_or_uncertified_native_only_diagnostic(self):
        for filename, payload in (("state.json", {"status": "FAILED"}),
                                  ("native-readback.json", {"status": "ok", "native_quality_passed": False}),
                                  ("native-readback.json", {"status": "ok", "native_quality_passed": 1})):
            with self.subTest(filename=filename, payload=payload), tempfile.TemporaryDirectory() as folder:
                b, c = self.pair(folder)
                (c/filename).write_text(json.dumps(payload))
                result = evaluate(b, c, PLAN)
                self.assertEqual(result["status"], "EFFECT_REFUSED")
                self.assertFalse(result["local_control_effect_verified"])
                self.assertGreater(result["candidate"]["wall_triangle_area_median"], 0)

    def test_missing_small_sample_nonfinite_duplicate_and_unknown_nodes(self):
        mutations = [lambda c: (c/"state.json").unlink(),
                     lambda c: (c/"nodes.csv").write_text((c/"nodes.csv").read_text().replace("0,0,0", "nan,0,0", 1)),
                     lambda c: (c/"nodes.csv").write_text((c/"nodes.csv").read_text() + "1,0,0,0\n"),
                     lambda c: (c/"elements.csv").write_text((c/"elements.csv").read_text().replace("3 4 5 6", "3 4 5 999999")),
                     lambda c: (c/"boundary_faces.csv").write_text((c/"boundary_faces.csv").read_text().replace("7 8 9", "7 7 9"))]
        for mutate in mutations:
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as folder:
                b, c = self.pair(folder)
                mutate(c)
                with self.assertRaises((EffectError, OSError)):
                    evaluate(b, c, PLAN)
        with tempfile.TemporaryDirectory() as folder:
            b, c = self.pair(folder, count=2)
            with self.assertRaisesRegex(EffectError, "insufficient"):
                evaluate(b, c, PLAN)

    def test_box_or_bbox_mismatch_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            b, c = self.pair(folder)
            (c/"nodes.csv").write_text((c/"nodes.csv").read_text().replace("20,10,5", "21,10,5"))
            with self.assertRaisesRegex(EffectError, "bbox"):
                evaluate(b, c, PLAN)
            plan = json.loads(PLAN.read_text())
            plan["measurement"]["centroid_box_normalized_raw"] = [100, 100, 100, 101, 101, 101]
            changed = Path(folder)/"plan.json"
            changed.write_text(json.dumps(plan))
            with self.assertRaisesRegex(EffectError, "insufficient"):
                evaluate(b, c, changed)


if __name__ == "__main__":
    unittest.main()
