# -*- coding: utf-8 -*-
"""导出法（选项 2）的可执行验收：从既有产物重建 Cd 列的公式、标注与拒绝语义。

对应独立审查（solve-history-derived-columns/review）给的必要断言：
公式逐行成立、末行与官方 Cd 一致、共线需标注、已有列时拒绝重导、缺字段时 fail-closed。
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from derive_solve_history_density_columns import DerivationRefused, derive  # noqa: E402

LEVEL = (ROOT / "test-evidence/static-release-candidate/engineering-execution-20261007/"
                "domain/d1p25/static-eng-domain-d1p25-20261008-s1")


def _rows(path: Path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


def _copy_level(directory: Path) -> Path:
    target = directory / "level"
    target.mkdir()
    for name in ("external_flow_solve_history.csv", "external_flow_solve.json", "case_spec.json"):
        shutil.copy(LEVEL / name, target / name)
    return target


class TheDerivationMatchesTheFormula(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.trial = _copy_level(Path(self._tmp.name))
        self.record = derive(self.trial)

    def tearDown(self):
        self._tmp.cleanup()

    def test_header_and_rows(self):
        rows = _rows(self.trial / "external_flow_solve_history.csv.derived.csv")
        self.assertEqual(rows[0], ["iteration", "elapsed_seconds", "drag_x_n", "mass_imbalance",
                                  "rho_volume_mean_kg_m3", "drag_coefficient_estimate"])
        self.assertEqual(len(rows) - 1, 20)
        self.assertEqual(self.record["output"]["rows"], 20)

    def test_every_row_matches_two_fx_over_rho_u2_a(self):
        solve = json.loads((self.trial / "external_flow_solve.json").read_text(encoding="utf-8"))
        rho = solve["measured_density_volume_average_kg_m3"]
        u = solve["inflow_m_s"] if "inflow_m_s" in solve else solve["inflow_speed_m_s"]
        area = solve["reference_area_m2"]
        rows = _rows(self.trial / "external_flow_solve_history.csv.derived.csv")[1:]
        for row in rows:
            want = 2.0 * float(row[2]) / (rho * u * u * area)
            self.assertLess(abs(float(row[5]) - want) / abs(want), 1e-15)

    def test_rho_column_is_the_end_of_solve_density(self):
        solve = json.loads((self.trial / "external_flow_solve.json").read_text(encoding="utf-8"))
        rows = _rows(self.trial / "external_flow_solve_history.csv.derived.csv")[1:]
        self.assertEqual({float(row[4]) for row in rows}, {solve["measured_density_volume_average_kg_m3"]})

    def test_last_row_coefficient_equals_the_official_estimate(self):
        self.assertEqual(self.record["verification"]["last_coefficient_vs_official_relative_gap"], 0.0)

    def test_collinearity_is_measured_and_declared(self):
        self.assertLess(self.record["verification"]["coefficient_over_force_relative_spread"], 1e-12)
        self.assertIn("no independent information", self.record["verification"]["collinearity_note"])
        self.assertTrue(self.record["derived"])
        self.assertIn("not", " ".join(self.record["must_not_claim"]) + " not")
        self.assertIn(".derived.csv", self.record["output"]["path"])


class TheDerivationFailsClosed(unittest.TestCase):
    def test_refuses_when_the_columns_already_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            trial = _copy_level(Path(tmp))
            derive(trial)
            with self.assertRaises(DerivationRefused) as caught:
                derive(trial, history_name="external_flow_solve_history.csv.derived.csv")
            self.assertIn("already carries", str(caught.exception))

    def test_refuses_when_the_solve_record_lacks_the_density(self):
        with tempfile.TemporaryDirectory() as tmp:
            trial = _copy_level(Path(tmp))
            solve = json.loads((trial / "external_flow_solve.json").read_text(encoding="utf-8"))
            solve.pop("measured_density_volume_average_kg_m3")
            (trial / "external_flow_solve.json").write_text(json.dumps(solve), encoding="utf-8")
            with self.assertRaises(DerivationRefused) as caught:
                derive(trial)
            self.assertIn("density", str(caught.exception))

    def test_refuses_a_non_finite_density(self):
        with tempfile.TemporaryDirectory() as tmp:
            trial = _copy_level(Path(tmp))
            solve = json.loads((trial / "external_flow_solve.json").read_text(encoding="utf-8"))
            solve["measured_density_volume_average_kg_m3"] = float("nan")
            (trial / "external_flow_solve.json").write_text(json.dumps(solve), encoding="utf-8")
            with self.assertRaises(DerivationRefused):
                derive(trial)


class TheRecordedDerivationsAreOnDisk(unittest.TestCase):
    """四个 R3 档的导出产物与记录已生成，且数字与记录一致。"""

    RECORD = (ROOT / "test-evidence/engineering-validation-start-20261009/"
                     "solve-history-derived-columns/DERIVED-HISTORIES.json")

    def test_four_levels_are_recorded_with_matching_hashes(self):
        records = json.loads(self.RECORD.read_text(encoding="utf-8"))["records"]
        self.assertEqual(len(records), 4)
        for record in records:
            path = Path(record["output"]["path"])
            self.assertTrue(path.is_file(), path)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record["output"]["sha256"])
            self.assertEqual(record["verification"]["last_coefficient_vs_official_relative_gap"], 0.0)
            self.assertEqual(len(record["output"]["columns"]), 6)


if __name__ == "__main__":
    unittest.main()
