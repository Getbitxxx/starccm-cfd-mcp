import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from air3b_convergence_gate import (categorise, classify_residual_scale, evaluate,
                                    read_long_table, read_segment_history, screen_residuals,
                                    screen_window, tail_mean)


def write_table(path: Path, rows: list[tuple[str, str, float, float]]) -> Path:
    lines = ["kind\tname\tfirst_number\tsecond_number"]
    lines += ["%s\t%s\t%r\t%r" % row for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class LongTableTests(unittest.TestCase):
    def test_reads_and_sorts_series(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_table(Path(tmp) / "t.tsv",
                               [("residual", "Continuity", 2.0, 1e-5),
                                ("residual", "Continuity", 1.0, 1e-4),
                                ("residual", "X-Momentum", 1.0, 2e-4)])
            table = read_long_table(path)
            self.assertEqual([x for x, _ in table["Continuity"]], [1.0, 2.0])
            self.assertEqual(len(table["X-Momentum"]), 1)

    def test_rejects_wrong_header_and_bad_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.tsv"
            bad.write_text("a\tb\tc\td\n1\t2\t3\t4\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_long_table(bad)
            bad.write_text("kind\tname\tfirst_number\tsecond_number\n"
                           "residual\tContinuity\tnope\t1\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_long_table(bad)

    def test_rejects_empty_and_non_finite(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.tsv"
            empty.write_text("kind\tname\tfirst_number\tsecond_number\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_long_table(empty)
            nan = Path(tmp) / "nan.tsv"
            nan.write_text("kind\tname\tfirst_number\tsecond_number\n"
                           "residual\tContinuity\t1\tnan\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_long_table(nan)


class CategorisationTests(unittest.TestCase):
    def test_maps_the_expected_names(self):
        self.assertEqual(categorise("Continuity"), "continuity")
        self.assertEqual(categorise("X-Momentum"), "momentum")
        self.assertEqual(categorise("Energy"), "energy")
        self.assertEqual(categorise("Tke"), "turbulence")
        self.assertEqual(categorise("Sdr"), "turbulence")
        self.assertIsNone(categorise("Something else"))

    def test_tail_mean_uses_the_tail(self):
        self.assertAlmostEqual(tail_mean([1.0, 2.0, 3.0, 100.0], 2), 51.5)
        with self.assertRaises(ValueError):
            tail_mean([1.0], 0)


class ResidualScreenTests(unittest.TestCase):
    def test_takes_the_worst_series_per_category(self):
        histories = {
            "Continuity": [(i, 1e-5) for i in range(60)],
            "X-Momentum": [(i, 1e-4) for i in range(60)],
            "Z-Momentum": [(i, 5e-4) for i in range(60)],
            "Energy": [(i, 1e-5) for i in range(60)],
            "Tke": [(i, 1e-4) for i in range(60)],
        }
        result = screen_residuals(histories, {"momentum": 2.5e-4}, window=10)
        self.assertEqual(result["categories"]["momentum"]["worst_series"], "Z-Momentum")
        self.assertFalse(result["passed"])

    def test_missing_category_does_not_pass(self):
        result = screen_residuals({"Continuity": [(i, 1e-9) for i in range(60)]},
                                  {"continuity": 1e-4, "energy": 1e-3}, window=10)
        self.assertFalse(result["categories"]["energy"]["passed"])
        self.assertFalse(result["passed"])

    def test_passing_residuals(self):
        histories = {"Continuity": [(i, 1e-6) for i in range(60)],
                     "X-Momentum": [(i, 1e-7) for i in range(60)],
                     "Energy": [(i, 1e-8) for i in range(60)],
                     "Tke": [(i, 1e-6) for i in range(60)]}
        self.assertTrue(screen_residuals(histories, None, window=10)["passed"])

    def test_ungated_series_are_reported_but_not_gated(self):
        result = screen_residuals({"Wall Y+": [(i, 5.0) for i in range(60)]},
                                  {"continuity": 1e-4}, window=10)
        self.assertIsNone(result["per_series"]["Wall Y+"]["category"])
        self.assertFalse(result["passed"])


class WindowScreenTests(unittest.TestCase):
    def test_insufficient_samples_fails_rather_than_raises(self):
        monitors = {"MON_THRUST_Z": [(i, 10.0) for i in range(50)],
                    "MON_TORQUE_Z": [(i, 1.0) for i in range(50)]}
        result = screen_window(monitors, {"thrust_z_n": "MON_THRUST_Z"}, 200, 0.005, 0.005, 1e-12)
        self.assertFalse(result["passed"])
        self.assertIn("not enough samples", result["targets"]["thrust_z_n"]["reason"])

    def test_missing_series_fails(self):
        result = screen_window({"other": [(i, 1.0) for i in range(10)]},
                               {"thrust_z_n": "MON_THRUST_Z"}, 3, 0.005, 0.005, 1e-12)
        self.assertFalse(result["passed"])
        self.assertFalse(result["targets"]["thrust_z_n"]["present"])

    def test_matching_is_exact_not_substring(self):
        # A longer name that merely contains the target must not be used.
        monitors = {"AIR3B_MON_THRUST_Z_OLD": [(i, 1.0) for i in range(100)]}
        result = screen_window(monitors, {"thrust_z_n": "MON_THRUST_Z"}, 10, 0.005, 0.005, 1e-12)
        self.assertFalse(result["passed"])
        self.assertIn("no series named", result["targets"]["thrust_z_n"]["reason"])

    def test_flat_series_passes_and_drifting_series_fails(self):
        flat = {"MON_THRUST_Z": [(i, 93.7) for i in range(500)]}
        self.assertTrue(screen_window(flat, {"thrust_z_n": "MON_THRUST_Z"}, 200, 0.005, 0.005, 1e-12)["passed"])
        drifting = {"MON_THRUST_Z": [(i, 90.0 + i * 0.01) for i in range(500)]}
        self.assertFalse(screen_window(drifting, {"thrust_z_n": "MON_THRUST_Z"}, 200, 0.005, 0.005, 1e-12)["passed"])


PROVENANCE = {"source": "AuditAir3bResidualProvenance.tsv", "monitors": {"Continuity": "NORMALIZED"}}


class MinimumSampleTests(unittest.TestCase):
    def test_short_series_is_excluded_and_fails_closed(self):
        result = screen_residuals({"Continuity": [(i, 1e-9) for i in range(5)]},
                                  {"continuity": 1e-4}, window=50)
        self.assertIsNone(result["per_series"]["Continuity"]["tail_mean"])
        self.assertEqual(result["per_series"]["Continuity"]["tail_window"], 0)
        self.assertFalse(result["per_series"]["Continuity"]["sufficient_samples"])
        self.assertFalse(result["passed"])

    def test_long_enough_series_records_the_window(self):
        result = screen_residuals({"Continuity": [(i, 1e-9) for i in range(60)]},
                                  {"continuity": 1e-4}, window=50)
        self.assertEqual(result["per_series"]["Continuity"]["tail_window"], 50)
        self.assertTrue(result["passed"])


class ProvenanceTests(unittest.TestCase):
    def test_absent_provenance_blocks_the_residual_half(self):
        residuals = {"Continuity": [(i, 1e-9) for i in range(300)],
                     "X-Momentum": [(i, 1e-9) for i in range(300)],
                     "Energy": [(i, 1e-9) for i in range(300)],
                     "Tke": [(i, 1e-9) for i in range(300)]}
        monitors = {"MON_THRUST_Z": [(i, 93.7) for i in range(500)],
                    "MON_TORQUE_Z": [(i, -5.2) for i in range(500)]}
        result = evaluate(residuals, monitors, residual_window=20, window_iterations=200)
        self.assertFalse(result["residuals_passed"])
        self.assertIn("provenance", result["residuals"]["blocked_by"])

    def test_present_provenance_allows_a_pass(self):
        residuals = {"Continuity": [(i, 1e-9) for i in range(300)],
                     "X-Momentum": [(i, 1e-9) for i in range(300)],
                     "Energy": [(i, 1e-9) for i in range(300)],
                     "Tke": [(i, 1e-9) for i in range(300)]}
        monitors = {"MON_THRUST_Z": [(i, 93.7) for i in range(500)],
                    "MON_TORQUE_Z": [(i, -5.2) for i in range(500)]}
        result = evaluate(residuals, monitors, residual_window=20,
                          normalization_provenance=PROVENANCE)
        self.assertTrue(result["passed"])


class ResidualScaleTests(unittest.TestCase):
    def test_auto_is_star_normalization_not_unrecognised(self):
        # AUTO is what the CaseSpec invokes by name; mapping it to unrecognised would block the
        # residual gate permanently, and mapping it to absolute would wrongly defer it.
        self.assertEqual(classify_residual_scale({"monitors": {"Continuity": "AUTO"}}), "normalized")
        self.assertEqual(classify_residual_scale({"monitors": {"Continuity": "DEFAULT"}}), "normalized")

    def test_classifies_normalized_and_absolute(self):
        self.assertEqual(classify_residual_scale({"monitors": {"Continuity": "ON"}}), "normalized")
        self.assertEqual(classify_residual_scale(
            {"monitors": {"Continuity": "MonitorNormalizeOption.Type.OFF"}}), "absolute")
        self.assertEqual(classify_residual_scale(None), "unknown")
        self.assertEqual(classify_residual_scale({"monitors": {}}), "unknown")

    def _run(self, monitors_option):
        residuals = {"Continuity": [(i, 0.5) for i in range(300)],
                     "X-Momentum": [(i, 0.5) for i in range(300)],
                     "Energy": [(i, 0.5) for i in range(300)],
                     "Tke": [(i, 0.5) for i in range(300)]}
        monitors = {"MON_THRUST_Z": [(i, 93.7) for i in range(500)],
                    "MON_TORQUE_Z": [(i, -5.2) for i in range(500)]}
        provenance = {"source": "AuditAir3bResidualProvenance.java",
                      "monitors": {"Continuity": monitors_option}}
        return evaluate(residuals, monitors, residual_window=20,
                        normalization_provenance=provenance)

    def test_absolute_scale_is_a_datum_and_does_not_gate(self):
        result = self._run("MonitorNormalizeOption.Type.OFF")
        self.assertFalse(result["residuals_gating"])
        self.assertFalse(result["residuals_passed"])
        self.assertIsNotNone(result["residual_gate_deferred"])
        # residuals are far above limits, but they must not decide the verdict
        self.assertTrue(result["passed"])

    def test_normalized_scale_gates(self):
        result = self._run("ON")
        self.assertTrue(result["residuals_gating"])
        self.assertFalse(result["residuals_passed"])
        self.assertFalse(result["passed"])

    def test_unrecognised_recorded_scale_fails_closed_and_flags_itself(self):
        # Real segment evidence showed that deferring here let the overall verdict read True while
        # the residuals were far above their limits, so an unrecognised scale must keep gating.
        result = self._run("SOMETHING ELSE")
        self.assertTrue(result["residuals_gating"])
        self.assertFalse(result["residuals_passed"])
        self.assertTrue(result["residuals"]["residual_scale_unrecognized"])
        self.assertEqual(result["residuals"]["raw_options"], ["SOMETHING ELSE"])
        self.assertIsNone(result["residual_gate_deferred"])
        self.assertFalse(result["passed"])

    def test_recognised_absolute_scale_still_defers(self):
        result = self._run("MonitorNormalizeOption.Type.OFF")
        self.assertFalse(result["residuals_gating"])
        self.assertTrue(result["residuals"]["datum_only"])

    def test_absent_provenance_blocks_conservatively(self):
        residuals = {"Continuity": [(i, 1e-9) for i in range(300)],
                     "X-Momentum": [(i, 1e-9) for i in range(300)],
                     "Energy": [(i, 1e-9) for i in range(300)],
                     "Tke": [(i, 1e-9) for i in range(300)]}
        monitors = {"MON_THRUST_Z": [(i, 93.7) for i in range(500)],
                    "MON_TORQUE_Z": [(i, -5.2) for i in range(500)]}
        result = evaluate(residuals, monitors, residual_window=20)
        self.assertTrue(result["residuals_gating"])
        self.assertFalse(result["passed"])


class SegmentHistoryTests(unittest.TestCase):
    def test_reads_per_chunk_history_into_series(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "h.csv"
            path.write_text("iteration,elapsed_seconds,thrust_z_n,torque_z_nm\n"
                            "110,1.0,93.1,-5.1\n120,2.0,93.2,-5.2\n", encoding="utf-8")
            series = read_segment_history(path)
            self.assertEqual(series["MON_THRUST_Z"], [(110.0, 93.1), (120.0, 93.2)])
            self.assertEqual(series["MON_TORQUE_Z"], [(110.0, -5.1), (120.0, -5.2)])

    def test_rejects_bad_header_and_non_finite(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.csv"
            bad.write_text("a,b,c,d\n1,2,3,4\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_segment_history(bad)
            nan = Path(tmp) / "nan.csv"
            nan.write_text("iteration,elapsed_seconds,thrust_z_n,torque_z_nm\n1,2,nan,4\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_segment_history(nan)

    def test_sample_interval_scales_the_window(self):
        # 200-iteration window sampled every 10 iterations needs 20 samples per window.
        monitors = {"MON_THRUST_Z": [(i * 10, 93.7) for i in range(60)],
                    "MON_TORQUE_Z": [(i * 10, -5.2) for i in range(60)]}
        residuals = {"Continuity": [(i, 1e-9) for i in range(300)],
                     "X-Momentum": [(i, 1e-9) for i in range(300)],
                     "Energy": [(i, 1e-9) for i in range(300)],
                     "Tke": [(i, 1e-9) for i in range(300)]}
        result = evaluate(residuals, monitors, residual_window=20, window_iterations=200,
                          sample_interval=10, normalization_provenance=PROVENANCE)
        self.assertEqual(result["window_samples"], 20)
        self.assertTrue(result["passed"])
        too_few = evaluate(residuals, monitors, residual_window=20, window_iterations=400,
                           sample_interval=10, normalization_provenance=PROVENANCE)
        self.assertFalse(too_few["steady_window_passed"])


class EvaluateTests(unittest.TestCase):
    def test_combined_verdict_and_disclaimer(self):
        residuals = {"Continuity": [(i, 1e-6) for i in range(300)],
                     "X-Momentum": [(i, 1e-7) for i in range(300)],
                     "Energy": [(i, 1e-8) for i in range(300)],
                     "Tke": [(i, 1e-6) for i in range(300)]}
        monitors = {"MON_THRUST_Z": [(i, 93.7265) for i in range(500)],
                    "MON_TORQUE_Z": [(i, -5.2134) for i in range(500)]}
        result = evaluate(residuals, monitors, residual_window=20,
                          normalization_provenance=PROVENANCE)
        self.assertTrue(result["passed"])
        self.assertFalse(result["physical_validation"])
        self.assertTrue(result["limitations"])

    def test_failing_window_blocks_the_combined_verdict(self):
        residuals = {"Continuity": [(i, 1e-6) for i in range(300)],
                     "X-Momentum": [(i, 1e-7) for i in range(300)],
                     "Energy": [(i, 1e-8) for i in range(300)],
                     "Tke": [(i, 1e-6) for i in range(300)]}
        monitors = {"MON_THRUST_Z": [(i, 90.0 + i * 0.05) for i in range(500)],
                    "MON_TORQUE_Z": [(i, -5.2134) for i in range(500)]}
        result = evaluate(residuals, monitors, residual_window=20,
                          normalization_provenance=PROVENANCE)
        self.assertFalse(result["passed"])
        self.assertTrue(result["residuals_passed"])
        self.assertFalse(result["steady_window_passed"])


if __name__ == "__main__":
    unittest.main()
