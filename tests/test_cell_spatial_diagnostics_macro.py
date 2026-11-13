# -*- coding: utf-8 -*-
"""Offline structural guards for macros/ExportCellSpatialDiagnostics.java (R1 preparation).

The macro cannot be compiled in this sandbox (no 2502 API jars, no javac); these tests pin the
properties the R1/R2 admission depends on, by reading the source the way
tests/test_formal_worker_deployment_guards.py does. Compile-time and behavioural verification
happen in the host round against the installed STAR 2502 API (see
docs/cell-spatial-diagnostics-20261021/REGISTRATION-DRAFT.md, reviewer checklist).
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'macros' / 'ExportCellSpatialDiagnostics.java'
V1 = ROOT / 'macros' / 'ExportMeshSpatialDiagnostics.java'


class TheV2MacroKeepsTheReadOnlyContract(unittest.TestCase):
    def setUp(self):
        self.text = SOURCE.read_text(encoding='utf-8')

    def test_braces_balance(self):
        self.assertEqual(self.text.count('{'), self.text.count('}'), 'unbalanced braces (syntax smoke)')

    def test_no_solver_mesh_or_save_actions(self):
        for forbidden in ('saveState(', '.iterate(', '.step(', '.initialize(', '.remesh('):
            self.assertNotIn(forbidden, self.text, f'forbidden action token present: {forbidden}')

    def test_all_writes_go_through_the_owned_helper(self):
        self.assertEqual(1, self.text.count('Files.writeString(f.toPath()'),
                         'the raw write helper must be defined exactly once')
        for line in self.text.splitlines():
            if 'write(' in line and 'Files.writeString' not in line and 'private static void write' not in line:
                self.assertIn('write(owned(', line, f'write outside owned(): {line.strip()[:120]}')

    def test_state_fingerprint_postcondition_and_source_repin(self):
        self.assertIn('before.equals(state(s,v))', self.text)
        self.assertGreaterEqual(self.text.count('pin(source,env("DIAG_SOURCE_SHA"))'), 2,
                                'input must be pinned before AND after the export')
        self.assertIn('case_spec.json', self.text)


class TheV2MacroClosesThePreflightGaps(unittest.TestCase):
    def setUp(self):
        self.text = SOURCE.read_text(encoding='utf-8')

    def test_no_hardcoded_worker_root_or_air3b_coupling(self):
        self.assertNotIn('CodexCFDRestricted', self.text)
        self.assertNotIn('AIR3B_JOB_DIR', self.text)
        self.assertIn('DIAG_JOB_DIR', self.text)
        self.assertIn('Session directory is not the admitted job directory', self.text)

    def test_volume_is_required_not_optional(self):
        self.assertIn('requiredNativeScalar(applicable,"Volume")', self.text)
        self.assertIn('Required native field missing: ', self.text)
        self.assertIn('Ambiguous native field: ', self.text)
        self.assertIn('volume_required', self.text)

    def test_units_are_fail_closed_and_recorded(self):
        for token in ('units_not_stated', 'Volume unit not stated', 'Volume is not length^3',
                      'Unexpected stated unit', 'coordinate_units_verified', 'volume_units_verified',
                      'export_header_raw'):
            self.assertIn(token, self.text, f'missing fail-closed units guard: {token}')

    def test_grouping_honesty_flags(self):
        self.assertIn('DIAGNOSTIC_NO_CERTIFICATE', self.text)
        # the JSON keys are escaped inside the Java string literal
        self.assertIn('\\"wall_adjacency_mapping_verified\\":false', self.text)
        self.assertIn('\\"native_cell_id_export_verified\\":false', self.text)
        self.assertIn('GEOMETRIC_DOWNSTREAM_ANALYSIS_ONLY', self.text)
        self.assertIn('wall_face_centre_tables_exported', self.text)

    def test_complete_marker_is_distinct_from_v1(self):
        self.assertIn('CELL_SPATIAL_DIAGNOSTICS_COMPLETE', self.text)
        self.assertNotIn('MESH_SPATIAL_DIAGNOSTICS_COMPLETE', self.text)
        self.assertIn('cell_spatial_failure.json', self.text)


class TheV1LineageIsUntouched(unittest.TestCase):
    def test_v1_still_carries_its_own_marker_and_coupling(self):
        text = V1.read_text(encoding='utf-8')
        self.assertIn('MESH_SPATIAL_DIAGNOSTICS_COMPLETE', text,
                      'v1 must stay byte-stable for the air3b lineage that pinned it')
        self.assertIn('CodexCFDRestricted', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
