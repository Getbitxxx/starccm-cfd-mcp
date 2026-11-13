import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluate_convergence import (assess_mass_imbalance, assess_steady_history,
                                  assess_transient_history, read_history)


class ConvergenceAssessmentTests(unittest.TestCase):
    def test_steady_history_requires_two_stable_windows(self):
        samples = [(i, 10.0 + 0.001 * math.sin(i)) for i in range(100)]
        result = assess_steady_history(samples, window_samples=30)
        self.assertTrue(result["passed"])
        self.assertEqual(result["samples"], 100)

    def test_steady_history_detects_drift_and_small_mean_with_floor(self):
        drifting = [(i, 1.0 + i * 0.01) for i in range(100)]
        self.assertFalse(assess_steady_history(drifting, 20)["passed"])
        near_zero = [(i, (-1 if i % 2 else 1) * 1e-15) for i in range(60)]
        self.assertTrue(assess_steady_history(near_zero, 20, 0.01, 0.01, 1e-12)["passed"])

    def test_transient_assesses_cycle_averages_over_complete_revolutions(self):
        rpm = 600.0
        period = 60 / rpm
        samples = []
        for i in range(1201):
            t = i * period / 100
            # Periodic within-cycle load with an equal mean each revolution.
            samples.append((t, 5.0 + math.sin(2 * math.pi * t / period)))
        result = assess_transient_history(samples, rpm, 4)
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["cycle_means"]), 4)

    def test_transient_detects_cycle_to_cycle_change(self):
        rpm = 600.0
        period = 60 / rpm
        samples = []
        for i in range(1201):
            t = i * period / 100
            cycle = int(t / period)
            samples.append((t, 1.0 + cycle * 0.1))
        result = assess_transient_history(samples, rpm, 4)
        self.assertFalse(result["passed"])

    def test_transient_rejects_incomplete_window_and_bad_step_coordinates(self):
        with self.assertRaisesRegex(ValueError, "does not cover"):
            assess_transient_history([(0, 1), (0.1, 1), (0.2, 1)], 600, 3)
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            assess_steady_history([(0, 1), (1, 1), (1, 1), (2, 1)], 2)

    def test_mass_balance_uses_outward_sign_magnitude(self):
        self.assertTrue(assess_mass_imbalance(1.001, -1.0, 0.002)["passed"])
        self.assertFalse(assess_mass_imbalance(1.1, -1.0, 0.01)["passed"])

    def test_csv_import_rejects_nonfinite_and_nonmonotonic_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.csv"
            path.write_text("Iteration,Value\n0,1\n1,nan\n")
            with self.assertRaisesRegex(ValueError, "Non-finite"):
                read_history(path)
            path.write_text("Iteration,Value\n0,1\n0,2\n")
            with self.assertRaisesRegex(ValueError, "strictly increasing"):
                read_history(path)


if __name__ == "__main__":
    unittest.main()
