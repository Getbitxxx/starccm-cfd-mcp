# -*- coding: utf-8 -*-
"""②R13: the study post steps are really invoked, re-verified, and reported as NOT_ESTABLISHED.

Window 1 delivered the mesh/domain sensitivity studies and their run evidence; R13 registers them as
operations and makes the FORMAL plan actually call them. The tests here pin the properties that make that
honest rather than decorative:

  1. every record the summary relies on is re-cited through the single shared gate (12 records across the
     four repaired levels are CITABLE today, and the two reused levels add six more);
  2. the minimum level count comes from the existing reviewed rule
     (`evaluate_sensitivity.MIN_LEVELS`): mesh 3, domain 2 - no new threshold is invented;
  3. the STUDY's own verdict (`NOT_ESTABLISHED`) and every exclusion reason are carried verbatim, and a
     NOT_ESTABLISHED study does not become a step failure or a pass;
  4. a level whose records are not citable makes the step fail closed;
  5. the shared reporter prints the conclusion, because the adapter writes a real stage record and the
     summary as a declared artifact - the reporter itself is NOT modified.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import formal_run_report as adapter                                        # noqa: E402
import formal_study_reuse as reuse                                         # noqa: E402
from casespec import load_catalog                                          # noqa: E402
from evaluate_sensitivity import MIN_LEVELS                                 # noqa: E402

BASE = ROOT / 'test-evidence' / 'static-external-flow'
STUDIES = {
    'mesh': {'manifest': BASE / 'round13-mesh-study' / 'mesh-levels.json',
             'report': BASE / 'round13-mesh-study' / 'mesh-study-report.json'},
    'domain': {'manifest': BASE / 'round13-domain-study' / 'domain-levels.json',
               'report': BASE / 'round13-domain-study' / 'domain-study-report.json'},
}
CASE_SPEC = ROOT / 'cases' / 'static-sphere-external-flow' / 'case_spec.json'


def summary(kind: str) -> dict:
    paths = STUDIES[kind]
    return reuse.summarize_study(kind, paths['manifest'], paths['report'], CASE_SPEC)


class TheRegisteredStudyEvaluatesTheRealEvidence(unittest.TestCase):
    def test_both_studies_are_registered_as_post_steps_bound_to_a_real_symbol(self):
        entries = {entry['id']: entry for entry in load_catalog()['operations']}
        for op_id, kind in (('mesh_study', 'mesh'), ('domain_study', 'domain')):
            with self.subTest(operation=op_id):
                entry = entries[op_id]
                self.assertEqual('offline_tool', entry['mechanism'])
                self.assertEqual('post', entry['plan_role'])
                binding = entry['invoked_by'][0]
                self.assertTrue((ROOT / binding['file']).is_file())
                self.assertTrue(callable(getattr(reuse, binding['symbol'], None)),
                                'the pinned symbol must exist in the pinned file')
                self.assertEqual([op_id], entry['provides'])
                # the study's own report is one of the cited pieces of evidence, with its verdict recorded
                reports = [item for item in entry['evidence'] if item['kind'] == 'report']
                self.assertEqual('NOT_ESTABLISHED', reports[0]['verdict'])

    def test_every_record_the_summary_relies_on_is_citable(self):
        for kind in STUDIES:
            with self.subTest(study=kind):
                data = summary(kind)
                self.assertEqual([], data['records_not_citable'])
                self.assertEqual(data['records_expected'], data['records_cited'])
                self.assertEqual(9, data['records_cited'], '3 levels x 3 records')
                self.assertTrue(data['operation']['evidence_re_verified'])
                for level in data['levels']:
                    for record_name, record in level['records'].items():
                        self.assertEqual('CITABLE', record['state'], (level['level'], record_name))

    def test_the_level_count_rule_is_imported_not_reinvented(self):
        mesh, domain = summary('mesh'), summary('domain')
        self.assertEqual(MIN_LEVELS['mesh'], mesh['minimum_levels_required'])
        self.assertEqual(MIN_LEVELS['domain'], domain['minimum_levels_required'])
        self.assertEqual(3, mesh['minimum_levels_required'])
        self.assertEqual(2, domain['minimum_levels_required'],
                         'the domain study legitimately needs two levels')
        for data in (mesh, domain):
            self.assertIn('evaluate_sensitivity.py::MIN_LEVELS', data['minimum_levels_source'])
            self.assertFalse(data['operation']['thresholds_added'])
            self.assertEqual(0, data['operation']['recomputed_levels'])

    def test_the_studys_own_verdict_and_every_exclusion_reason_are_carried(self):
        for kind in STUDIES:
            with self.subTest(study=kind):
                data = summary(kind)
                self.assertEqual('NOT_ESTABLISHED', data['sensitivity_status'])
                self.assertFalse(data['sensitivity_passed'])
                self.assertEqual('NOT_CLAIMED_BY_WIRING', data['conclusion']['engineering_claim'])
                self.assertIn('not a pass', data['conclusion']['reading'])
                excluded = [level for level in data['levels'] if level.get('exclusion_reasons')]
                self.assertTrue(excluded, 'the study excluded at least one level; the reasons must survive')
                for level in excluded:
                    self.assertIn('excluded_%s' % level['level'], data,
                                  'the reasons are flattened so the reporter can print them as scalars')

    def test_the_two_reused_levels_are_labelled_as_history_not_as_this_runs_evidence(self):
        for kind, reused_level in (('mesh', 'medium'), ('domain', 'd1p00')):
            with self.subTest(study=kind):
                data = summary(kind)
                self.assertEqual([], data['provenance']['measured_in_this_run'],
                                 'no level was measured in this submission: nothing may look like it was')
                names = [item['level'] for item in data['provenance']['reused_from_named_evidence']]
                self.assertEqual([reused_level], names)
                entry = [level for level in data['levels'] if level['level'] == reused_level][0]
                self.assertTrue(entry['reused'])
                self.assertFalse(entry['measured_in_this_run'])


class AStudyStepThatCannotVerifyItsEvidenceFailsClosed(unittest.TestCase):
    def test_a_level_without_citable_records_is_not_a_verified_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            report = json.loads(STUDIES['mesh']['report'].read_text(encoding='utf-8-sig'))
            # point one level at a directory whose records are absent
            manifest = json.loads(STUDIES['mesh']['manifest'].read_text(encoding='utf-8-sig'))
            manifest['levels'][0] = dict(manifest['levels'][0])
            manifest['levels'][0]['trial_id'] = 'a-trial-that-does-not-exist'
            manifest_path = tmp / 'levels.json'
            manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
            report_path = tmp / 'report.json'
            report_path.write_text(json.dumps(report), encoding='utf-8')
            data = reuse.summarize_study('mesh', manifest_path, report_path, CASE_SPEC)
        self.assertFalse(data['operation']['evidence_re_verified'])
        self.assertTrue(data['records_not_citable'] or any(
            record['state'] == 'MISSING'
            for level in data['levels'] for record in level['records'].values()))


class TheSharedReporterPrintsTheStudyConclusion(unittest.TestCase):
    """The reporter is window-1-owned and NOT modified; the conclusion reaches it as evidence."""

    def run_dir(self, root: Path) -> Path:
        run_dir = root / 'runs' / 'study-step-1'
        job = run_dir / 'stages' / 'run_external_flow_solve'
        job.mkdir(parents=True)
        import hashlib
        sim = job / 'external_flow_solved.sim'
        sim.write_bytes(b'placeholder\n')
        digest = hashlib.sha256(sim.read_bytes()).hexdigest()
        (job / 'stage-solve.json').write_text(json.dumps({
            'schema_version': 1, 'stage': 'solve', 'state': 'ok', 'seconds': 1.0,
            'artifacts': ['external_flow_solve.json', 'external_flow_solved.sim'],
            'macros': [{'index': 0, 'macro': 'RunExternalFlowSolve.java', 'ran': True, 'status': 'ok',
                        'exit_code': 0}]}), encoding='utf-8')
        (job / 'external_flow_solve.json').write_text(json.dumps({'status': 'ok'}), encoding='utf-8')
        (job / 'checkpoint.json').write_text(json.dumps({'output_path': str(sim),
                                                         'output_sha256': digest}), encoding='utf-8')
        (run_dir / 'state.json').write_text(json.dumps({
            'state': 'EXECUTION_COMPLETED', 'completed': ['run_external_flow_solve'],
            'final_sim': str(sim), 'final_sha256': digest,
            'post_operations': ['export_evidence', 'report', 'mesh_study', 'domain_study']}),
            encoding='utf-8')
        return run_dir

    def test_both_study_conclusions_appear_in_the_report_the_reporter_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = self.run_dir(root)
            outcome = adapter.report_run('study-step-1', root / 'evidence', run_dir=run_dir)
            # the adapter really invoked both studies and recorded them as plan steps
            self.assertEqual({'mesh_study', 'domain_study'},
                             {item['operation'] for item in outcome['studies']})
            for item in outcome['studies']:
                self.assertEqual('NOT_ESTABLISHED', item['sensitivity_status'])
                self.assertEqual(9, item['records_cited'])
                self.assertEqual([], item['measured_in_this_run'])
            # the reporter printed them: verdict ok (the STEP succeeded) and the study verdict beside it
            markdown = (root / 'evidence' / 'study-step-1-report.md').read_text(encoding='utf-8')
            for op_id in ('mesh_study', 'domain_study'):
                self.assertIn('stage-%s.json' % op_id, markdown)
                self.assertIn('NOT_ESTABLISHED', markdown)
            self.assertIn('excluded_coarse', markdown,
                          'the exclusion reasons are declared artifacts, so the reporter prints them')
            summary_json = json.loads((root / 'evidence' / 'study-step-1-report.json')
                                      .read_text(encoding='utf-8'))
            self.assertEqual('REPORT_OK', summary_json['report_verdict'], summary_json['report_reasons'])
            self.assertEqual({'mesh_study', 'domain_study'},
                             {item['operation'] for item in outcome['run_record']['study_steps']})
            self.assertEqual('NOT_ESTABLISHED',
                             outcome['conclusions']['engineering']['study_conclusions'][0]
                             ['sensitivity_status'])
            self.assertFalse(outcome['conclusions']['engineering']['claimed'])

    def test_a_study_step_that_could_not_verify_its_records_is_not_ok(self):
        # stage_is_ok is the adapter's own completion guard; the study record must satisfy its own
        # evidence-based condition instead of a macro list.
        good = {'state': 'ok', 'not_a_star_stage': True, 'records_verified': 9, 'records_expected': 9,
                'records_not_citable': []}
        self.assertTrue(adapter.stage_is_ok(good))
        for broken in ({**good, 'records_verified': 8},
                       {**good, 'records_not_citable': ['coarse/external_flow_solve.json (NOT_CITABLE)']},
                       {**good, 'records_expected': 0, 'records_verified': 0},
                       {**good, 'state': 'failed'}):
            with self.subTest(record=broken):
                self.assertFalse(adapter.stage_is_ok(broken))


class TheStudyStepRefusesToCertifyAnInconsistentStudy(unittest.TestCase):
    """②R13 review round 1 built these two cases and both SURVIVED, so they are pinned here.

    A step whose stated job is "re-verify the study evidence" must fail closed when the evidence it reads
    contradicts itself - otherwise it certifies a study whose inputs moved or whose verdict its own level
    list cannot support, and the report still says ok. Both checks use what already exists: the pinned
    hashes in the manifest and `evaluate_sensitivity.MIN_LEVELS`.
    """

    def _tampered(self, tmp: Path, mutate) -> dict:
        manifest = json.loads(STUDIES['mesh']['manifest'].read_text(encoding='utf-8-sig'))
        report = json.loads(STUDIES['mesh']['report'].read_text(encoding='utf-8-sig'))
        mutate(manifest, report)
        manifest_path = tmp / 'levels.json'
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        report_path = tmp / 'report.json'
        report_path.write_text(json.dumps(report), encoding='utf-8')
        return reuse.summarize_study('mesh', manifest_path, report_path, CASE_SPEC)

    def test_a_manifest_whose_pinned_input_hash_moved_is_not_certified(self):
        def mutate(manifest, report):
            manifest['spec_sha256'] = 'b' * 64
        with tempfile.TemporaryDirectory() as tmp:
            data = self._tampered(Path(tmp), mutate)
        self.assertFalse(data['consistency']['study_spec_hash_matches'])
        self.assertFalse(data['operation']['evidence_re_verified'])
        self.assertIn('input_hash_mismatch', data, 'the mismatch is a scalar so the reporter prints it')
        self.assertEqual(9, data['records_cited'], 'the records are still counted; the step is still not ok')

    def test_a_level_whose_pinned_spec_hash_moved_is_not_certified(self):
        def mutate(manifest, report):
            manifest['levels'][0]['case_spec_sha256'] = 'c' * 64
        with tempfile.TemporaryDirectory() as tmp:
            data = self._tampered(Path(tmp), mutate)
        self.assertEqual(['coarse'], data['consistency']['levels_with_mismatched_hashes'])
        self.assertFalse(data['operation']['evidence_re_verified'])
        self.assertEqual('coarse', data['level_hash_mismatch'])

    def test_an_established_claim_its_level_count_cannot_support_is_not_certified(self):
        # One level plus a report claiming ESTABLISHED: the study contradicts itself, and relaying the
        # verdict verbatim would launder it into a green step.
        def mutate(manifest, report):
            manifest['levels'] = manifest['levels'][:1]
            report['sensitivity_status'] = 'ESTABLISHED'
            report['sensitivity_passed'] = True
            report['valid_levels'] = ['coarse']
            report['excluded_levels'] = []
        with tempfile.TemporaryDirectory() as tmp:
            data = self._tampered(Path(tmp), mutate)
        self.assertEqual('ESTABLISHED', data['sensitivity_status'])
        self.assertFalse(data['consistency']['verdict_consistent_with_the_level_count'])
        self.assertFalse(data['operation']['evidence_re_verified'])
        self.assertIn('verdict_level_count_mismatch', data)
        self.assertIn('requires 3', data['verdict_level_count_mismatch'])

    def test_a_not_established_study_with_far_too_few_levels_is_still_ok(self):
        # The other direction: a study that HONESTLY reports NOT_ESTABLISHED is exactly what the existing
        # rule predicts, so the step must stay ok. Requiring sensitivity to pass would be the conflation
        # this round was told not to make.
        for kind in STUDIES:
            with self.subTest(study=kind):
                data = summary(kind)
                self.assertEqual('NOT_ESTABLISHED', data['sensitivity_status'])
                self.assertTrue(data['operation']['evidence_re_verified'])
                self.assertTrue(data['consistency']['verdict_consistent_with_the_level_count'])

    def test_measured_in_this_run_is_derived_from_the_run_directory(self):
        # Provenance must be derived, not hardwired: the same call with the CURRENT run directory set to the
        # level directory's own parent marks the measured levels, and with the formal run directory (which
        # contains none of them) marks nothing.
        level_dir = (STUDIES['mesh']['manifest'].parent / 'coarse').resolve()
        as_this_run = reuse.summarize_study('mesh', STUDIES['mesh']['manifest'], STUDIES['mesh']['report'],
                                           CASE_SPEC, current_run_dir=level_dir)
        self.assertEqual(['coarse'], as_this_run['provenance']['measured_in_this_run'])
        self.assertTrue([item for item in as_this_run['levels']
                         if item['level'] == 'coarse'][0]['measured_in_this_run'])
        # and with the run directory of the R13 formal submission (a fresh empty directory) nothing is
        with tempfile.TemporaryDirectory() as tmp:
            fresh = reuse.summarize_study('mesh', STUDIES['mesh']['manifest'], STUDIES['mesh']['report'],
                                          CASE_SPEC, current_run_dir=Path(tmp))
        self.assertEqual([], fresh['provenance']['measured_in_this_run'])
        self.assertEqual(['medium'], [item['level'] for item in fresh['provenance']['reused_from_named_evidence']])


class ARunThatDeclaresNoPostStepsSaysSo(unittest.TestCase):
    """②R13 review round 1, concern 5: an old run directory (no post_operations) reported green."""

    def test_the_adapter_records_that_there_was_nothing_to_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / 'runs' / 'old-run-1'
            job = run_dir / 'stages' / 'run_external_flow_solve'
            job.mkdir(parents=True)
            import hashlib as _h
            sim = job / 'external_flow_solved.sim'
            sim.write_bytes(b'x\n')
            (job / 'stage-solve.json').write_text(json.dumps({
                'schema_version': 1, 'stage': 'solve', 'state': 'ok', 'seconds': 0.5,
                'artifacts': ['external_flow_solve.json', 'external_flow_solved.sim'],
                'macros': [{'index': 0, 'macro': 'RunExternalFlowSolve.java', 'ran': True, 'status': 'ok',
                            'exit_code': 0}]}), encoding='utf-8')
            (job / 'external_flow_solve.json').write_text(json.dumps({'status': 'ok'}), encoding='utf-8')
            (job / 'checkpoint.json').write_text(json.dumps(
                {'output_path': str(sim), 'output_sha256': _h.sha256(sim.read_bytes()).hexdigest()}),
                encoding='utf-8')
            # no post_operations at all: the pre-R13 shape
            (run_dir / 'state.json').write_text(json.dumps(
                {'state': 'EXECUTION_COMPLETED', 'completed': ['run_external_flow_solve'],
                 'final_sim': str(sim), 'final_sha256': _h.sha256(sim.read_bytes()).hexdigest()}),
                encoding='utf-8')
            outcome = adapter.report_run('old-run-1', root / 'evidence', run_dir=run_dir)
        self.assertFalse(outcome['plan_declares_post_steps'])
        self.assertEqual([], outcome['studies'],
                         'nothing was declared, so nothing may be reported as if it had run')



class TheRoundTwoFindingsAreClosed(unittest.TestCase):
    """②R13 review round 2 (M6/M7) - both one-line holes, both pinned here."""

    def _tampered(self, tmp: Path, mutate) -> dict:
        manifest = json.loads(STUDIES['mesh']['manifest'].read_text(encoding='utf-8-sig'))
        report = json.loads(STUDIES['mesh']['report'].read_text(encoding='utf-8-sig'))
        mutate(manifest, report)
        manifest_path = tmp / 'levels.json'
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        report_path = tmp / 'report.json'
        report_path.write_text(json.dumps(report), encoding='utf-8')
        return reuse.summarize_study('mesh', manifest_path, report_path, CASE_SPEC)

    def test_m6_a_verdict_whose_two_fields_disagree_is_not_certified(self):
        for status, passed in (('NOT_ESTABLISHED', True), ('ESTABLISHED', False)):
            with self.subTest(status=status, passed=passed):
                def mutate(manifest, report, status=status, passed=passed):
                    report['sensitivity_status'] = status
                    report['sensitivity_passed'] = passed
                    report['valid_levels'] = ['coarse', 'medium', 'fine']
                with tempfile.TemporaryDirectory() as tmp:
                    data = self._tampered(Path(tmp), mutate)
                self.assertFalse(data['consistency']['verdict_status_and_passed_agree'])
                self.assertFalse(data['operation']['evidence_re_verified'])
                self.assertIn('verdict_status_passed_mismatch', data)

    def test_m7_a_pinned_hash_with_no_readable_file_is_a_mismatch(self):
        # `pipeline_sha256` pinned while the pipeline file is gone used to yield `..._matches: None` and a
        # verified step. The pinned-but-unreadable case must be flagged like any other mismatch.
        def mutate(manifest, report):
            level = manifest['levels'][0]
            level['pipeline'] = str(Path(level.get('case_spec', '')).parent / 'a-pipeline-that-does-not-exist.json')
            level['pipeline_sha256'] = 'd' * 64
        with tempfile.TemporaryDirectory() as tmp:
            data = self._tampered(Path(tmp), mutate)
        entry = [item for item in data['levels'] if item['level'] == 'coarse'][0]
        self.assertIn('pipeline', entry['pinned_but_unreadable'])
        self.assertIn('coarse', data['consistency']['levels_with_mismatched_hashes'])
        self.assertFalse(data['operation']['evidence_re_verified'])

    def test_m7_a_pinned_spec_hash_with_no_readable_spec_is_a_mismatch(self):
        def mutate(manifest, report):
            manifest['levels'][0]['case_spec'] = '/nowhere/a-case-spec-that-does-not-exist.json'
            manifest['levels'][0]['case_spec_sha256'] = 'e' * 64
        with tempfile.TemporaryDirectory() as tmp:
            data = self._tampered(Path(tmp), mutate)
        entry = [item for item in data['levels'] if item['level'] == 'coarse'][0]
        self.assertIn('case_spec', entry['pinned_but_unreadable'])
        self.assertFalse(data['operation']['evidence_re_verified'])


class TheRoundThreeFindingsAreClosed(unittest.TestCase):
    """②R13 review round 3 (N2/N3): an unknown status was certified, and the count message was mis-emitted."""

    def _tampered(self, tmp: Path, mutate) -> dict:
        manifest = json.loads(STUDIES['mesh']['manifest'].read_text(encoding='utf-8-sig'))
        report = json.loads(STUDIES['mesh']['report'].read_text(encoding='utf-8-sig'))
        mutate(manifest, report)
        manifest_path = tmp / 'levels.json'
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        report_path = tmp / 'report.json'
        report_path.write_text(json.dumps(report), encoding='utf-8')
        return reuse.summarize_study('mesh', manifest_path, report_path, CASE_SPEC)

    def test_n2_a_status_outside_the_vocabulary_is_not_certified(self):
        # `STUDY_STATUSES` existed but was never consulted, so any string with passed=false went through.
        for status in ('PASSED', 'NOT_RUN', 'ok', None, ''):
            with self.subTest(status=status):
                def mutate(manifest, report, status=status):
                    report['sensitivity_status'] = status
                    report['sensitivity_passed'] = False
                with tempfile.TemporaryDirectory() as tmp:
                    data = self._tampered(Path(tmp), mutate)
                self.assertFalse(data['consistency']['status_in_the_declared_vocabulary'])
                self.assertFalse(data['operation']['evidence_re_verified'])
                self.assertIn('verdict_status_unknown', data)

    def test_n2b_every_declared_status_is_accepted_when_it_is_consistent(self):
        for status, passed in (('NOT_ESTABLISHED', False), ('NOT_APPLICABLE', False), ('UNKNOWN', False),
                               ('ESTABLISHED', True)):
            with self.subTest(status=status):
                def mutate(manifest, report, status=status, passed=passed):
                    report['sensitivity_status'] = status
                    report['sensitivity_passed'] = passed
                    if status == 'ESTABLISHED':
                        report['valid_levels'] = ['coarse', 'medium', 'fine']
                with tempfile.TemporaryDirectory() as tmp:
                    data = self._tampered(Path(tmp), mutate)
                self.assertTrue(data['consistency']['status_in_the_declared_vocabulary'])
                self.assertTrue(data['operation']['evidence_re_verified'], data.get('verdict_level_count_mismatch'))

    def test_n3_a_status_passed_disagreement_does_not_claim_a_level_count_problem(self):
        # ESTABLISHED with enough valid levels but passed=false: the ONLY thing wrong is the disagreement.
        def mutate(manifest, report):
            report['sensitivity_status'] = 'ESTABLISHED'
            report['sensitivity_passed'] = False
            report['valid_levels'] = ['coarse', 'medium', 'fine']
        with tempfile.TemporaryDirectory() as tmp:
            data = self._tampered(Path(tmp), mutate)
        self.assertFalse(data['consistency']['verdict_status_and_passed_agree'])
        self.assertTrue(data['consistency']['level_count_supports_the_verdict'])
        self.assertNotIn('verdict_level_count_mismatch', data,
                         'the level count is fine here; only the status/passed pair disagrees')
        self.assertIn('verdict_status_passed_mismatch', data)



if __name__ == '__main__':
    unittest.main(verbosity=2)
