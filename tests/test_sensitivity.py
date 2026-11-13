import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluate_sensitivity import compare_study, evaluate_file


class SensitivityStudyTests(unittest.TestCase):
    def test_mesh_study_requires_three_levels_and_compares_target_changes(self):
        with self.assertRaisesRegex(ValueError, "at least 3 levels"):
            compare_study("mesh", [{"cells": 100, "thrust": 1}, {"cells": 200, "thrust": 1}], ["thrust"])
        result = compare_study("mesh", [
            {"cells": 100, "thrust": 10.0},
            {"cells": 200, "thrust": 10.1},
            {"cells": 400, "thrust": 10.15},
        ], ["thrust"], 0.02)
        self.assertTrue(result["passed"])
        self.assertEqual([row["cells"] for row in result["levels"]], [100, 200, 400])

    def test_domain_study_detects_sensitive_target(self):
        result = compare_study("domain", [
            {"domain_scale": 1.0, "drag": 10.0},
            {"domain_scale": 1.5, "drag": 10.8},
            {"domain_scale": 2.0, "drag": 11.0},
        ], ["drag"], 0.02)
        self.assertFalse(result["passed"])
        self.assertFalse(result["physical_validation"])

    def test_time_step_orders_from_coarse_to_fine(self):
        result = compare_study("time_step", [
            {"time_step_s": 0.002, "torque": 20.0},
            {"time_step_s": 0.001, "torque": 20.1},
            {"time_step_s": 0.0005, "torque": 20.12},
        ], ["torque"], 0.01)
        self.assertTrue(result["passed"])
        self.assertEqual([row["time_step_s"] for row in result["levels"]], [0.002, 0.001, 0.0005])

    def test_near_zero_quantity_uses_explicit_absolute_scale_floor(self):
        rows = [
            {"cells": 100, "side_force": 1e-10},
            {"cells": 200, "side_force": 2e-10},
            {"cells": 400, "side_force": 2e-10},
        ]
        result = compare_study("mesh", rows, ["side_force"], 0.05,
                               {"side_force": 1e-8})
        self.assertTrue(result["passed"])
        self.assertEqual(result["quantity_comparisons"]["side_force"]["absolute_scale_floor"], 1e-8)

    def test_rejects_duplicate_parameters_bad_values_and_missing_target(self):
        with self.assertRaisesRegex(ValueError, "distinct"):
            compare_study("domain", [
                {"domain_scale": 1, "drag": 1}, {"domain_scale": 1, "drag": 1}], ["drag"])
        with self.assertRaisesRegex(ValueError, "finite target"):
            compare_study("domain", [
                {"domain_scale": 1, "drag": 1}, {"domain_scale": 2, "drag": float("nan")}], ["drag"])

    def test_csv_cli_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "study.csv"
            path.write_text("cells,thrust,torque\n100,10,2\n200,10.1,2.01\n400,10.12,2.02\n")
            result = evaluate_file(path, "mesh")
            self.assertEqual(set(result["quantity_comparisons"]), {"thrust", "torque"})


if __name__ == "__main__":
    unittest.main()
