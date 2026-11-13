import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from casespec import validate_case_spec
from mesh_quality import assess_mesh_quality, assess_near_wall_quality


class MeshQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "audit.csv"
        self.rows = [
            "region,metric,stat,value",
            "ALL,Cells,count,20",
            "stationary,ElementCount:cells,count,10",
            "rotating,ElementCount:cells,count,10",
            "stationary,CellQuality,min,0.001",
            "stationary,FaceValidity,min,1",
            "stationary,VolumeChange,min,0.02",
            "rotating,CellQuality,min,0.002",
            "rotating,FaceValidity,min,1",
            "rotating,VolumeChange,min,0.011",
            "ALL,Audit,complete,1",
        ]

    def assess(self):
        self.path.write_text("\n".join(self.rows) + "\n")
        return assess_mesh_quality(self.path, {}, ["stationary", "rotating"])

    def test_pass_and_volume_change_at_boundary_fails(self):
        self.assertTrue(self.assess()["passed"])
        self.rows[9] = "rotating,VolumeChange,min,0.01"
        result = self.assess()
        self.assertFalse(result["passed"])
        self.assertEqual([check["metric"] for check in result["checks"] if not check["passed"]],
                         ["VolumeChange"])

    def test_missing_region_or_metric_cannot_pass(self):
        self.rows.pop(8)
        with self.assertRaisesRegex(ValueError, "FaceValidity"):
            self.assess()
        self.rows.insert(8, "rotating,FaceValidity,min,1")
        self.rows[3] = "rotating,ElementCount:cells,count,9"
        with self.assertRaisesRegex(ValueError, "do not match"):
            self.assess()

    def test_invalid_quality_threshold_is_rejected_by_plan(self):
        spec = json.loads((Path(__file__).resolve().parents[1] / "cases" /
                           "free-air-propeller-360-mrf-prism4-mesh-execution.json").read_text())
        spec["quality"]["mesh_min_volume_change"] = 0
        self.assertTrue(any("mesh_min_volume_change" in error for error in
                            validate_case_spec(spec).errors))

    def test_initialized_imported_mrf_mesh_fails_real_face_validity_gate(self):
        path = Path(__file__).resolve().parents[1] / "test-evidence" / "three-blade-imported-mrf-postinit-quality.csv"
        result = assess_mesh_quality(path, {}, ["rotor_fluid_pilot_Part0", "three_blade_stator_pilot_Part0"])
        self.assertFalse(result["passed"])
        self.assertEqual(result["total_cells"], 374574)
        failed = [check for check in result["checks"] if not check["passed"]]
        self.assertEqual([(check["region"], check["metric"]) for check in failed],
                         [("rotor_fluid_pilot_Part0", "FaceValidity")])

    def test_four_region_official_tutorial_audit_keeps_strict_gate(self):
        path = Path(__file__).resolve().parents[1] / "test-evidence" / "siemens-openwater-final-quality.csv"
        regions = ["Rotating Region", "Static Region", "Downstream Extension", "Upstream Extension"]
        result = assess_mesh_quality(path, {}, regions)
        self.assertEqual(result["total_cells"], 2493321)
        self.assertFalse(result["passed"])
        failed = [(check["region"], check["metric"]) for check in result["checks"]
                  if not check["passed"]]
        self.assertEqual(failed, [("Static Region", "FaceValidity")])

    def test_cellset_localization_matches_preview_in_two_cases(self):
        root = Path(__file__).resolve().parents[1] / "test-evidence"
        for name in ("three-blade-face-validity-cellset.txt",
                     "siemens-openwater-face-validity-cellset.txt"):
            with self.subTest(name=name):
                report = (root / name).read_text()
                self.assertIn("cutoff=1.0", report)
                self.assertIn("cell_set_below_cutoff_cells=1", report)
                self.assertIn("cell_set_localization_final_count=1", report)
                self.assertIn("'minimumFaceValidity': 1", report)

    def test_near_wall_gate_requires_actual_prism_layers_and_explicit_wall_yplus(self):
        path = Path(__file__).resolve().parents[1] / "test-evidence" / "diagnose-1800-mesh_quality_summary_20260929_201357.csv"
        result = assess_near_wall_quality(
            path,
            {"three_blade_conformal_mrf_Part0/3_S_4": 3,
             "three_blade_conformal_mrf_Part0/3_S_9": 3},
            {"three_blade_conformal_mrf_Part0/3_S_4": (1, 300),
             "three_blade_conformal_mrf_Part0/3_S_9": (1, 300)})
        self.assertFalse(result["passed"])
        self.assertTrue(all(check["observed_min"] is None for check in result["checks"]
                            if check["kind"] == "prism_layers"))
        self.assertTrue(any(check["observed_max"] == 1403.3585150802585 and not check["passed"]
                            for check in result["checks"] if check["kind"] == "wall_y_plus"))

    def test_near_wall_gate_accepts_complete_explicit_evidence(self):
        self.path.write_text("\n".join([
            "section,region,metric,stat,value",
            '"prism_layer","rotor/blade","PrismLayerNumber","min",12',
            '"prism_layer","rotor/blade","PrismLayerNumber","max",12',
            '"y_plus","rotor/blade","WallYplus","min",2',
            '"y_plus","rotor/blade","WallYplus","max",120',
        ]) + "\n")
        result = assess_near_wall_quality(self.path, {"rotor/blade": 3},
                                          {"rotor/blade": (1, 150)})
        self.assertTrue(result["passed"])

    def test_near_wall_gate_rejects_wrong_input_and_targets(self):
        with self.assertRaisesRegex(ValueError, "explicit"):
            assess_near_wall_quality(self.path, {}, {"rotor/blade": (1, 100)})
        with self.assertRaisesRegex(ValueError, "selectors must match"):
            assess_near_wall_quality(self.path, {"rotor/blade": 3}, {"rotor/hub": (1, 100)})
        self.path.write_text("\n".join([
            "section,region,metric,stat,value",
            '"prism_layer","rotor/blade","PrismLayerNumber","min",0',
            '"prism_layer","rotor/blade","PrismLayerNumber","max",2',
            '"y_plus","rotor/blade","WallYplus","min",0',
            '"y_plus","rotor/blade","WallYplus","max",300',
        ]) + "\n")
        result = assess_near_wall_quality(self.path, {"rotor/blade": 3},
                                          {"rotor/blade": (1, 200)})
        self.assertFalse(result["passed"])


if __name__ == "__main__":
    unittest.main()
