# -*- coding: utf-8 -*-
"""①R14: the two formal-entry defects, pinned as executable tests.

Defect 1 - the independent convergence audit crashed before it judged anything.
    Round 13's two formal runs both ended with
    `{"status":"error","usable":false,"error_class":"java.lang.NullPointerException",
      "error":"Cannot read the array length because \"<local2>\" is null"}`.
    The candidate in the handover was `report.getDirection()`. It is NOT that call: this file pins the
    real one and the shape that made it possible, using the real failing records as fixtures.

    How it was located without a stack trace: the deployed macro bytecode was compiled with the
    installed 2502 JDK and disassembled. The message names a LOCAL SLOT, so the array-length read that
    threw must be the one whose operand is slot 2. In the whole class exactly one array-length read
    loads slot 2: the compiler's array copy in `windowStats(double[])` (`aload_0; astore_2; aload_2;
    arraylength`). `dragReportVerified`'s read is at slot 4 and is guarded, so it cannot produce the
    observed message. `execute` passes `dragHistory`, which is null when neither the report-monitor
    series nor the stage-1 chunk history is present in the audit's working directory - both were absent
    in the formal per-stage layout.

Defect 2 - the report contradicted itself.
    Section 6 asserted "网格三档敏感性与计算域敏感性没有实现，本轮未做" while the same report's stage
    table listed mesh_study and domain_study as ok. The section is now built from the run's own study
    records, in the three states the review asked for: 已执行 / 未执行 / 证据不可引用.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MACRO = ROOT / 'macros' / 'AuditExternalFlowConvergence.java'
REPORTER = ROOT / 'scripts' / 'generate_run_report.py'
FIXTURE = ROOT / 'test-evidence' / 'static-external-flow' / 'round14-audit-crash'


def read(path: Path) -> str:
    return path.read_text(encoding='utf-8')


class TheAuditCannotCrashBeforeItJudges(unittest.TestCase):
    """Source-level contract for the crash: the guard must come before the dereference."""

    def setUp(self):
        self.source = read(MACRO)
        start = self.source.index('private static double[] windowStats(double[] values) {')
        self.body = self.source[start:self.source.index('\n    }', start)]

    def test_the_parameter_is_guarded_before_it_is_read(self):
        guard = 'if (values == null || values.length == 0) return null;'
        self.assertIn(guard, self.body)
        self.assertLess(self.body.index(guard), self.body.index('for (double value : values)'),
                        'the enhanced-for still dereferences the parameter before the guard')

    def test_a_series_that_is_absent_is_reported_as_unmeasured_not_as_a_verdict(self):
        # The caller already distinguishes "no series" from "series says unsteady": every window metric
        # is written as NaN/0 when the stats are null, and loadsSteady stays null.
        for field in ('drag_window_mean_n', 'drag_window_relative_deviation', 'target_quantities_steady'):
            self.assertIn(field, self.source)
        self.assertIn('dragStats == null ? Double.NaN : dragStats[0]', self.source)
        self.assertIn('dragStats == null ? Double.NaN : dragStats[3]', self.source)
        self.assertIn('dragStats != null && dragStats[4] >= 3.0', self.source)
        self.assertIn('loadsSteady == null ? "NOT_CONVERGED_UNMEASURED"', self.source)

    def test_the_hashed_sim_is_the_session_file_not_a_hard_coded_name(self):
        # The formal per-stage runner stages the declared input as input.sim; a hard-coded
        # external_flow_solved.sim threw FileNotFoundException before any criterion was judged.
        self.assertIn('sim.getSessionPath()', self.source)
        session = self.source.index('sim.getSessionPath()')
        fallback = self.source.index('openedSim == null')
        hardcoded = self.source.index('new File(dir, "external_flow_solved.sim")')
        self.assertLess(session, hardcoded, 'the session path must be tried before the legacy name')
        self.assertLess(fallback, hardcoded, 'the legacy name must only be the documented fallback')
        self.assertIn('input_sim_name', self.source)

    def test_the_verifications_this_round_was_told_not_to_bypass_are_still_there(self):
        for check in ('dragReportVerified', 'report.getParts().getObjects()', 'drag_report_parts_verified',
                      'DRAG_DIRECTION_UNIT', 'readOnlyVerified', 'stage1Agrees', 'massBalanced'):
            self.assertIn(check, self.source)
        # No catch-and-continue: the outer handler still turns a Throwable into an error record, so a
        # crash can never be silently read as a pass.
        self.assertIn('External-flow convergence audit failed', self.source)


class TheRealCrashIsKeptAsTheFixture(unittest.TestCase):
    """The counter-example is the failure that actually happened, not a synthetic one."""

    def test_the_fixture_records_the_observed_error(self):
        record = json.loads(read(FIXTURE / 'external_flow_validation.json'))
        self.assertEqual('error', record['status'])
        self.assertFalse(record['usable'])
        self.assertEqual('java.lang.NullPointerException', record['error_class'])
        self.assertIn('<local2>', record['error'])
        self.assertIsNone(record['complete_marker'])
        self.assertEqual('Cannot read the array length', record['error'].split(': ')[-1].split(' because')[0])

    def test_the_fixture_log_shows_the_deprecation_warning_that_misled_the_first_read(self):
        log = read(FIXTURE / 'star-job-0-AuditExternalFlowConvergence.log')
        self.assertIn('deprecation', log)
        self.assertIn('getVector()', log)
        # ... and that the warning is a COMPILE warning printed before the run, not the crash:
        self.assertLess(log.index('deprecation'), log.index('External-flow convergence audit failed'))


class TheReportReadsTheStudyRecordsInsteadOfAssertingThemMissing(unittest.TestCase):
    """Section 6's three states, driven through the real reporter on real-shaped evidence."""

    def build(self, tmp, study_record=True, study_detail='NOT_ESTABLISHED', not_citable=(),
              star_stage_missing=False):
        evidence = Path(tmp) / 'evidence'
        trial = 'r14-report-fixture'
        trial_dir = evidence / trial
        trial_dir.mkdir(parents=True)
        stages = [
            {'name': 'solve', 'required': ['external_flow_solve.json', 'external_flow_solved.sim']},
            {'name': 'validation', 'required': ['external_flow_validation.json']},
            {'name': 'mesh_study', 'required': ['mesh-study-reuse.json', 'mesh-study-report.json']},
        ]
        if star_stage_missing:
            stages.insert(0, {'name': 'audit-body', 'required': ['static_body_audit.json']})
        (evidence / (trial + '-config.json')).write_text(
            json.dumps({'schema_version': 1, 'trial_id': trial, 'pipeline_id': 'fixture-pipeline',
                        'case_label': 'fixture', 'stages': stages}), encoding='utf-8')
        (evidence / (trial + '-result.json')).write_text(
            json.dumps({'state': 'AIR3B_PIPELINE_COMPLETE', 'gate_passed': True}), encoding='utf-8')
        (evidence / (trial + '-checkpoints.json')).write_text(
            json.dumps({'download_succeeded': True, 'checkpoint_delivered': True, 'verified': [],
                        'failures': []}), encoding='utf-8')
        (trial_dir / 'stage-solve.json').write_text(
            json.dumps({'stage': 'solve', 'state': 'ok', 'macros': [{'macro': 'RunExternalFlowSolve.java'}]}),
            encoding='utf-8')
        (trial_dir / 'external_flow_solve.json').write_text(
            json.dumps({'status': 'ok', 'measured_drag_x_n_final': 0.4467, 'end_iteration': 600}),
            encoding='utf-8')
        (trial_dir / 'stage-validation.json').write_text(
            json.dumps({'stage': 'validation', 'state': 'ok', 'macros': [
                {'macro': 'AuditExternalFlowConvergence.java'}]}), encoding='utf-8')
        (trial_dir / 'external_flow_validation.json').write_text(
            json.dumps({'schema_version': 1, 'status': 'ok', 'usable': True, 'converged': True,
                        'validation_verdict': 'NOT_VALIDATED_NO_REFERENCE', 'read_only_verified': True,
                        'stage_record_hashes_seen': 3, 'input_sim_name': 'external_flow_solved.sim',
                        'drag_window_source': 'chunk_history', 'target_quantities_steady': True,
                        'residuals_met': True, 'mass_balanced': True}), encoding='utf-8')
        if star_stage_missing:
            (trial_dir / 'stage-audit-body.json').write_text(
                json.dumps({'stage': 'audit-body', 'state': 'ok',
                            'macros': [{'macro': 'AuditStaticBodyGeometry.java'}]}), encoding='utf-8')
            (trial_dir / 'static_body_audit.json').write_text(
                json.dumps({'status': 'ok'}), encoding='utf-8')
        if study_record:
            (trial_dir / 'stage-mesh_study.json').write_text(json.dumps({
                'stage': 'mesh_study', 'state': 'ok', 'not_a_star_stage': True, 'macros': [],
                'mode': 'REUSE_OF_EXISTING_STUDY_EVIDENCE', 'records_verified': 9, 'records_expected': 9,
                'artifacts': ['mesh-study-reuse.json', 'mesh-study-report.json']}), encoding='utf-8')
            (trial_dir / 'mesh-study-reuse.json').write_text(json.dumps({
                'schema_version': 1, 'kind': 'mesh', 'operation': 'mesh_study', 'status': 'ok',
                'sensitivity_status': study_detail, 'sensitivity_passed': study_detail == 'ESTABLISHED',
                'valid_levels': ['medium'], 'declared_level_count': 3,
                'minimum_levels_required': 3,
                'minimum_levels_source': "scripts/evaluate_sensitivity.py::MIN_LEVELS['mesh']",
                'records_cited': 9, 'records_expected': 9, 'records_not_citable': list(not_citable),
                'mesh_independent': False, 'domain_independent': False,
                'excluded_coarse': 'solve.residuals_met is not true'}), encoding='utf-8')
            (trial_dir / 'mesh-study-report.json').write_text(json.dumps({'status': 'ok'}), encoding='utf-8')
        return evidence, trial

    def report(self, evidence, trial):
        out = subprocess.run([sys.executable, str(REPORTER), '--trial-id', trial, '--evidence',
                              str(evidence)], capture_output=True, text=True)
        self.assertEqual(0, out.returncode, out.stderr[-1200:])
        return read(evidence / (trial + '-report.md'))

    def section6(self, text):
        self.assertIn('## 6.', text)
        return text.split('## 6.')[1].split('## 7.')[0]

    def test_an_executed_study_is_reported_with_its_own_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence, trial = self.build(tmp)
            section = self.section6(self.report(evidence, trial))
        self.assertIn('mesh_study', section)
        self.assertIn('已执行', section)
        self.assertIn('NOT_ESTABLISHED', section)
        self.assertIn('medium', section)
        self.assertIn('9/9', section)
        self.assertIn('关闭档位 `coarse`', section)
        self.assertNotIn('没有实现', section)
        self.assertNotIn('未做', section)

    def test_a_declared_study_with_no_record_is_reported_as_not_executed(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence, trial = self.build(tmp, study_record=False)
            section = self.section6(self.report(evidence, trial))
        self.assertIn('mesh_study', section)
        self.assertIn('未执行', section)
        self.assertNotIn('已执行', section)

    def test_a_star_stage_with_no_record_is_not_mislabelled_as_an_unexecuted_study(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence, trial = self.build(tmp, star_stage_missing=True, study_record=False)
            section = self.section6(self.report(evidence, trial))
        self.assertNotIn('`audit-body`', section)
        self.assertIn('`mesh_study`', section)

    def test_non_citable_study_evidence_is_named_and_never_turned_into_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence, trial = self.build(tmp, study_detail='ESTABLISHED',
                                         not_citable=('round13-mesh-study/coarse/x.json',))
            section = self.section6(self.report(evidence, trial))
        self.assertIn('证据不可引用', section)
        self.assertIn('round13-mesh-study/coarse/x.json', section)
        self.assertNotIn('ESTABLISHED**', section)

    def test_a_validation_record_that_produced_no_verdict_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence, trial = self.build(tmp)
            (evidence / trial / 'external_flow_validation.json').write_text(json.dumps({
                'schema_version': 1, 'status': 'error', 'usable': False,
                'error_class': 'java.lang.NullPointerException',
                'error': 'java.lang.NullPointerException: Cannot read the array length because "<local2>" is null',
                'complete_marker': None}), encoding='utf-8')
            text = self.report(evidence, trial)
        section = text.split('## 3.')[1].split('## 4.')[0]
        self.assertIn('未产生判定', section)
        self.assertIn('<local2>', section)
        self.assertIn('既不是"判据不通过"，也不是"物理不收敛"', section)

    def test_a_window_that_could_not_be_measured_is_not_reported_as_an_unsteady_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence, trial = self.build(tmp)
            (evidence / trial / 'external_flow_validation.json').write_text(json.dumps({
                'schema_version': 1, 'status': 'ok', 'usable': True, 'converged': False,
                'validation_verdict': 'NOT_CONVERGED_UNMEASURED', 'read_only_verified': False,
                'read_only': False, 'stage_record_hashes_seen': 0, 'input_sim_name': 'input.sim',
                'drag_window_source': 'none', 'drag_window_samples': 0,
                'target_quantities_steady': None, 'residuals_met': True,
                'mass_balanced': True}), encoding='utf-8')
            section = self.report(evidence, trial).split('## 3.')[1].split('## 4.')[0]
        self.assertIn('drag_window_source=none', section)
        self.assertIn('目标量稳定窗口**没有被测量**', section)
        self.assertIn('无法进行', section)


class TheReadOnlyCheckIsBoundToTheDeclaredSolveOutput(unittest.TestCase):
    """①R15: the read-only proof must name the solve stage's DECLARED OUTPUT, not any 64-hex string.

    The first version scanned the whole stage-solve.json for any 64-hex value and accepted the run when one
    of them equalled the audit's own input hash. A record that merely mentions that hash somewhere else - an
    `input_sha256`, a macro hash, a runner digest - would therefore have been accepted as proof that the
    audit read the solve stage's output. The counter-example this pins is exactly "only input_sha256
    matches, output_sha256 does not", and the real tampered run that exercises it is recorded in
    test-evidence/static-external-flow/round15-audit-binding-negative/.
    """

    def setUp(self):
        self.source = read(MACRO)
        start = self.source.index('// read_only is bound to the DECLARED SOLVE OUTPUT')
        self.block = self.source[start:self.source.index('boolean readOnlyVerified', start) + 200]

    def test_the_hash_is_read_from_the_named_output_sha256_field(self):
        # ①R15 second repair: the read must be scoped to the ROOT object. `optional()` is a first-match
        # regex, and the runner emits macros:[{... "output_sha256" ...}] BEFORE the top-level field, so a
        # first-match read bound the proof to the first PER-MACRO hash. The independent review measured the
        # consequence on the committed counter-example: readOnlyVerified came out TRUE (a false pass).
        self.assertIn('rootString(record, "output_sha256", null)', self.block)
        self.assertIn('rootString(record, "output", null)', self.block)
        self.assertNotIn('optional(record, "output_sha256"', self.block)

    def test_the_proof_requires_the_declared_output_identity_to_be_a_sim(self):
        self.assertIn('endsWith(".sim")', self.block)
        self.assertIn('outputIdentityIsSim && inputSha.equals(stageOutputSha)', self.block)

    def test_no_code_path_accepts_a_hash_matched_anywhere_else_in_the_record(self):
        # The old body assigned the proof INSIDE the "any 64-hex" scan. That assignment must be gone: the
        # scan may still count occurrences for diagnostics, but it must not decide the verdict.
        loop = self.block[self.block.index('while (hashes.find())'):self.block.index('stageOutputSha =')]
        self.assertNotIn('inputSha.equals', loop)
        self.assertNotIn('stageRecordSha', self.source)

    def test_the_binding_is_visible_in_the_record_it_writes(self):
        for field in ('stage_record_output', 'stage_record_output_sha256', 'read_only_binding'):
            self.assertIn(field, self.source)
        self.assertIn('read_only_verified', self.source)


class TheRootReaderCannotBeSatisfiedByANestedKey(unittest.TestCase):
    """The reader the macro uses, replicated in Python and run against the COMMITTED fixtures.

    Source-text assertions cannot show that a nested key is ignored. This replicates the depth-1 rule that
    `rootString` implements (and asserts the macro really calls it) and then runs both readers over the real
    records: the tampered counter-example must come out UNVERIFIED with the scoped reader and VERIFIED with
    the first-match reader, which is exactly the false pass the round-15 review found.
    """

    def root_string(self, text, key, fallback=None):
        needle = '"%s"' % key
        depth = 0
        in_string = False
        index = 0
        while index < len(text):
            char = text[index]
            if in_string:
                if char == '\\':
                    index += 2
                    continue
                if char == '"':
                    in_string = False
                index += 1
                continue
            if char == '"':
                if depth == 1 and text.startswith(needle, index):
                    cursor = index + len(needle)
                    while cursor < len(text) and text[cursor].isspace():
                        cursor += 1
                    if cursor >= len(text) or text[cursor] != ':':
                        index += 1
                        continue
                    cursor += 1
                    while cursor < len(text) and text[cursor].isspace():
                        cursor += 1
                    if cursor < len(text) and text[cursor] == '"':
                        end = text.find('"', cursor + 1)
                        if end > cursor:
                            return text[cursor + 1:end]
                    return fallback
                in_string = True
                index += 1
                continue
            if char in '{[':
                depth += 1
            elif char in '}]':
                depth -= 1
            index += 1
        return fallback

    def first_match(self, text, key, fallback=None):
        import re
        match = re.search(r'"%s"\s*:\s*"([^"]+)"' % re.escape(key), text)
        return match.group(1) if match else fallback

    def verified(self, directory, reader):
        import hashlib
        record = (directory / 'stage-solve.json').read_text(encoding='utf-8-sig')
        sim = directory / 'external_flow_solved.sim'
        sha = hashlib.sha256(sim.read_bytes()).hexdigest()
        name = reader(record, 'output')
        return bool(name and name.lower().endswith('.sim') and sha == reader(record, 'output_sha256'))

    def test_the_macro_uses_the_scoped_reader(self):
        source = read(MACRO)
        self.assertIn('private static String rootString(String text, String key, String fallback)', source)
        self.assertIn('rootString(record, "output_sha256", null)', source)
        self.assertIn('only accepts\n     * a key found at depth 1', source.replace('\r', ''))

    def test_the_tampered_record_is_refused_only_by_the_scoped_reader(self):
        tampered = FIXTURE.parent / 'round15-audit-binding-negative'
        if not tampered.is_dir():
            self.skipTest('the round-15 counter-example fixture is not present')
        self.assertTrue(self.verified(tampered, self.first_match),
                        'the fixture must be the shape the review measured (nested key matches)')
        self.assertFalse(self.verified(tampered, self.root_string),
                         'the scoped reader must refuse the tampered record')

    def test_a_genuine_record_still_verifies_with_the_scoped_reader(self):
        genuine = FIXTURE.parent / 'round14-audit-trial'
        if not genuine.is_dir():
            self.skipTest('the round-14 genuine record is not present')
        self.assertTrue(self.verified(genuine, self.root_string))
        self.assertTrue(self.verified(genuine, self.first_match))


class AnArtefactIsJudgedByItsBytesNotByItsPayload(unittest.TestCase):
    """①R16: section 2 used to print every NON-JSON artefact as `缺失`.

    `collect()` loads only `*.json` into `records`, so a `.sim`, `.stl`, `.csv` or `.tsv` always had a None
    payload and was displayed as missing - even when it was present in the trial directory and its hash had
    been verified (the same report's delivery section said VERIFIED). The three questions are now answered
    separately and `未知` is written where the evidence does not decide.
    """

    def build(self, tmp, delivery=True, recorded='match', include_stl=False):
        import hashlib
        evidence = Path(tmp) / 'evidence'
        trial = 'r16-display-fixture'
        trial_dir = evidence / trial
        trial_dir.mkdir(parents=True)
        (evidence / (trial + '-config.json')).write_text(json.dumps({
            'schema_version': 1, 'trial_id': trial, 'pipeline_id': 'fixture',
            'case_label': 'fixture',
            'stages': [{'name': 'solve', 'required': ['external_flow_solved.sim',
                                                      'static_body_tessellation.stl']}]}), encoding='utf-8')
        (evidence / (trial + '-result.json')).write_text(
            json.dumps({'state': 'AIR3B_PIPELINE_COMPLETE', 'gate_passed': True}), encoding='utf-8')
        sim = trial_dir / 'external_flow_solved.sim'
        sim.write_bytes(b'sim-bytes')
        sim_sha = hashlib.sha256(sim.read_bytes()).hexdigest()
        if include_stl:
            (trial_dir / 'static_body_tessellation.stl').write_bytes(b'stl-bytes')
        (trial_dir / 'stage-solve.json').write_text(
            json.dumps({'stage': 'solve', 'state': 'ok', 'macros': []}), encoding='utf-8')
        if delivery:
            (evidence / (trial + '-checkpoints.json')).write_text(json.dumps({
                'download_succeeded': True, 'checkpoint_delivered': True, 'failures': [],
                'final_sim': {'name': 'external_flow_solved.sim',
                              'sha256': sim_sha if recorded == 'match' else 'f' * 64,
                              'status': 'VERIFIED'}}), encoding='utf-8')
        return evidence, trial

    def section2(self, evidence, trial):
        out = subprocess.run([sys.executable, str(REPORTER), '--trial-id', trial, '--evidence',
                              str(evidence)], capture_output=True, text=True)
        self.assertEqual(0, out.returncode, out.stderr[-800:])
        text = read(evidence / (trial + '-report.md'))
        return text.split('## 2.')[1].split('## 3.')[0]

    def test_a_present_non_json_artefact_is_not_reported_as_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence, trial = self.build(tmp, delivery=True, recorded='match')
            section = self.section2(evidence, trial)
        self.assertIn('`external_flow_solved.sim`**（文件存在：是；传输登记：是；哈希核验：是',
                      section)
        self.assertNotIn('缺失', section)

    def test_an_absent_artefact_says_so_from_the_filesystem(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence, trial = self.build(tmp, delivery=True, recorded='match', include_stl=False)
            section = self.section2(evidence, trial)
        self.assertIn('`static_body_tessellation.stl`**（文件存在：否', section)

    def test_a_recorded_hash_that_does_not_match_is_reported_as_not_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence, trial = self.build(tmp, delivery=True, recorded='mismatch')
            section = self.section2(evidence, trial)
        self.assertIn('文件存在：是；传输登记：是；哈希核验：否', section)

    def test_without_a_delivery_record_the_transfer_and_hash_states_are_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence, trial = self.build(tmp, delivery=False)
            section = self.section2(evidence, trial)
        self.assertIn('文件存在：是；传输登记：未知；哈希核验：未知', section)


if __name__ == '__main__':
    unittest.main()
