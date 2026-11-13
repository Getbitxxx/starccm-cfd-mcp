"""F6 ratchets: the archived STAR ledger, the four KPI baselines, the wall-clock gap and stop-loss.

The ledger numbers are NOT invented here: they are the archived 2026-10-30 closeout figures
(17.75 h / 84 unique runs / 36 failed). The recompute rule in ``scripts/kpi_ledger.py`` must
reproduce them from the 97 ``run.json`` files on disk (13 of which are the same config recorded
again under nested audit dirs); the identity is asserted to 1e-9 so the ledger cannot drift, and
every component is re-derived from the exposed per-run rows rather than trusted.

Two metrics are honestly absent: there is no wall-clock/end timestamp in ANY archived run, so
``wall_clock_over_star`` must report NOT_MEASURABLE (never 0, never a made-up ratio), and no claims
linter/manifest exists, so ``report_number_traceability`` must report NOT_INSTRUMENTED. The
synthetic fixtures below prove the code *does* compute the wall-clock ratio when a record carries
wall seconds, so NOT_MEASURABLE is a statement about the archive, not about a missing feature.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import kpi_ledger as kpi  # noqa: E402

ARCHIVED_HOURS = 17.75197225525
ARCHIVED_FAILED_COUNT_SHARE = 0.42857142857142855
ARCHIVED_FAILED_TIME_SHARE = 0.3958788860256529
REL = 1e-9

LEDGER = None
RECORDS = None


def setUpModule():  # noqa: N802 - unittest hook
    global RECORDS, LEDGER
    RECORDS = kpi.discover_run_records()
    LEDGER = kpi.star_ledger(RECORDS)


def rel_equal(case: unittest.TestCase, actual: float, expected: float, rel: float = REL) -> None:
    case.assertLessEqual(abs(actual - expected), abs(expected) * rel,
                         'expected %r got %r (rel tol %r)' % (expected, actual, rel))


def write_run(directory: Path, *, config: str, status: str = 'EXECUTION_COMPLETED',
              star_steps=(10.0, 30.0), wall: float | None = None, name: str = 'run.json') -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        'schema_version': 1,
        'status': status,
        'config_sha256': config,
        'started_at': '2026-10-28T06:39:08.1615832Z',
        'steps': [{'step': 'step%d' % i, 'elapsed_seconds': seconds}
                  for i, seconds in enumerate(star_steps)],
    }
    if wall is not None:
        payload['wall_clock_seconds'] = wall
    path = directory / name
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path


class LedgerRecomputeIdentity(unittest.TestCase):
    """The rule reproduces the archived closeout ledger, component by component."""

    def test_file_and_unique_run_counts(self):
        self.assertEqual(97, LEDGER['record_files'])
        self.assertEqual(84, LEDGER['unique_runs'])
        self.assertEqual(13, LEDGER['duplicate_files'])
        self.assertEqual(13, len(LEDGER['duplicate_paths']))
        self.assertEqual(48, LEDGER['completed_runs'])

    def test_star_hours_reproduce_the_archived_ledger(self):
        rel_equal(self, LEDGER['star_hours_total'], ARCHIVED_HOURS)
        rel_equal(self, LEDGER['star_seconds_total'], ARCHIVED_HOURS * 3600.0)

    def test_failed_run_counts_and_shares_reproduce_the_archive(self):
        self.assertEqual(36, LEDGER['failed_runs'])
        rel_equal(self, LEDGER['failed_run_count_share'], ARCHIVED_FAILED_COUNT_SHARE)
        rel_equal(self, LEDGER['failed_run_time_share'], ARCHIVED_FAILED_TIME_SHARE)

    def test_every_component_can_be_resummed_from_the_exposed_rows(self):
        runs = LEDGER['runs']
        self.assertEqual(LEDGER['unique_runs'], len(runs))
        total = sum(row['star_seconds'] for row in runs)
        self.assertAlmostEqual(LEDGER['star_seconds_total'], total, delta=1e-6)
        failed = [row for row in runs if row['status'] != kpi.EXECUTION_COMPLETED]
        self.assertEqual(LEDGER['failed_runs'], len(failed))
        self.assertEqual(sorted(row['path'] for row in failed), LEDGER['failed_run_paths'])
        self.assertAlmostEqual(
            LEDGER['failed_star_seconds'], sum(row['star_seconds'] for row in failed), delta=1e-6)
        self.assertAlmostEqual(
            LEDGER['failed_run_time_share'],
            LEDGER['failed_star_seconds'] / LEDGER['star_seconds_total'], delta=1e-12)
        self.assertAlmostEqual(
            LEDGER['failed_run_count_share'],
            LEDGER['failed_runs'] / LEDGER['unique_runs'], delta=1e-12)

    def test_dedupe_keeps_the_first_path_in_sorted_order(self):
        groups: dict[str, list[str]] = {}
        for record in RECORDS:
            groups.setdefault(record['config_sha256'], []).append(record['path'])
        for config, paths in groups.items():
            first = min(paths)
            for record in RECORDS:
                if record['config_sha256'] != config:
                    continue
                if record['path'] == first:
                    self.assertIsNone(record['duplicate_of'], record['path'])
                else:
                    self.assertEqual(first, record['duplicate_of'], record['path'])

    def test_failed_means_status_is_not_execution_completed(self):
        statuses = {row['status'] for row in LEDGER['runs']}
        self.assertEqual({'EXECUTION_COMPLETED', 'FAILED'}, statuses)
        self.assertTrue(all(row['star_seconds'] >= 0 for row in LEDGER['runs']))
        self.assertGreater(LEDGER['star_seconds_total'], 0)

    def test_kpi_reports_both_shares_and_fails_the_target(self):
        kpi_record = kpi.kpi_failed_run_star_share(LEDGER)
        rel_equal(self, kpi_record['measured']['time_share'], ARCHIVED_FAILED_TIME_SHARE)
        rel_equal(self, kpi_record['measured']['count_share'], ARCHIVED_FAILED_COUNT_SHARE)
        self.assertEqual(0.20, kpi_record['target'])
        self.assertEqual('ABOVE_TARGET', kpi_record['verdict'])
        self.assertEqual(LEDGER['failed_star_seconds'],
                         kpi_record['components']['failed_star_seconds'])


class WallClockOverStar(unittest.TestCase):
    """NOT_MEASURABLE on the archive; computed when a record actually carries wall seconds."""

    def test_the_archive_is_not_measurable_and_does_not_invent_a_ratio(self):
        record = kpi.kpi_wall_clock_over_star(RECORDS)
        self.assertEqual('NOT_MEASURABLE', record['status'])
        self.assertEqual('NOT_MEASURABLE', record['verdict'])
        self.assertIsNone(record['measured'])
        self.assertEqual(0, record['components']['runs_with_wall_clock'])
        self.assertEqual(LEDGER['unique_runs'], record['components']['runs_considered'])
        self.assertIn('started_at', record['reason'])
        self.assertIn('not reported', record['reason'])
        self.assertEqual(['run.json', 'CONTROLLER-RUN.json (same run dir)'],
                         record['components']['field_search']['records_searched'])

    def test_ratio_is_computed_from_a_run_that_carries_wall_clock_seconds(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            write_run(root / 'run-a', config='a' * 64, star_steps=(10.0, 30.0), wall=100.0)
            records = kpi.discover_run_records(root)
            record = kpi.kpi_wall_clock_over_star(records)
            self.assertEqual('MEASURED', record['status'])
            self.assertEqual({'wall_clock_over_star': 2.5}, record['measured'])
            self.assertEqual('ABOVE_TARGET', record['verdict'])
            self.assertEqual(100.0, record['components']['wall_seconds_covered'])
            self.assertEqual(40.0, record['components']['star_seconds_covered'])
            self.assertEqual(['run.json:wall_clock_seconds'],
                             record['components']['wall_clock_sources'])

    def test_ratio_is_computed_from_a_controller_run_record(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            directory = root / 'run-b'
            write_run(directory, config='b' * 64, star_steps=(20.0, 20.0))
            (directory / 'CONTROLLER-RUN.json').write_text(json.dumps({
                'schema_version': 1, 'run_id': 'run-b', 'started_utc': '2026-10-28T06:39:08Z',
                'wall_clock_seconds': 80.0}), encoding='utf-8')
            record = kpi.kpi_wall_clock_over_star(kpi.discover_run_records(root))
            self.assertEqual('MEASURED', record['status'])
            self.assertEqual(2.0, record['measured']['wall_clock_over_star'])
            self.assertEqual('AT_OR_BELOW_TARGET', record['verdict'])
            self.assertEqual(['CONTROLLER-RUN.json:wall_clock_seconds'],
                             record['components']['wall_clock_sources'])

    def test_zero_or_non_numeric_wall_values_do_not_enter_the_ratio(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            write_run(root / 'run-c', config='c' * 64, wall=0.0)
            write_run(root / 'run-d', config='d' * 64, wall=-5.0)
            record = kpi.kpi_wall_clock_over_star(kpi.discover_run_records(root))
            self.assertEqual('NOT_MEASURABLE', record['status'])
            self.assertIsNone(record['measured'])

    def test_partial_coverage_still_reports_the_covered_ratio(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            write_run(root / 'run-e', config='e' * 64, wall=60.0)
            write_run(root / 'run-f', config='f' * 64)  # no wall seconds
            record = kpi.kpi_wall_clock_over_star(kpi.discover_run_records(root))
            self.assertEqual('MEASURED', record['status'])
            self.assertEqual(1, record['components']['runs_with_wall_clock'])
            self.assertEqual(1, record['components']['runs_without_wall_clock'])
            self.assertEqual(2, record['components']['runs_considered'])


class ReviewRoundsBaseline(unittest.TestCase):
    """The literal mechanical definition, with every component exposed and the ~1.5 mismatch recorded."""

    def setUp(self):
        self.record = kpi.kpi_review_rounds_per_new_plan()
        self.components = self.record['components']

    def test_component_counts_are_mechanically_reproducible(self):
        self.assertEqual(250, self.components['review_record_files_total'])
        self.assertEqual(79, self.components['staging_review_records'])
        self.assertEqual(171, self.components['non_staging_review_records'])
        self.assertEqual(169, self.components['numbered_review_round_files'])
        self.assertEqual(15, self.components['distinct_review_round_numbers'])
        self.assertEqual(168, self.components['re_review_files'])
        self.assertEqual(97, self.components['run_dirs'])
        self.assertEqual(250, self.components['staging_review_records']
                         + self.components['non_staging_review_records'])

    def test_ratio_equals_total_over_new_plans(self):
        new_plans = self.components['new_plans']
        self.assertGreater(new_plans, 0)
        source = self.components['plan_registry_source']
        plans_data = sorted((ROOT / 'campaigns' / 'uav-2026' / 'plans').glob('*/_campaign.json'))
        if plans_data:
            self.assertTrue(source.startswith('plan_registry'), source)
        else:
            self.assertEqual(87, new_plans)
            self.assertTrue(source.startswith('legacy_fallback'), source)
            self.assertTrue(self.components['plan_registry_note'])
        rel_equal(self, self.record['measured']['review_records_per_plan'],
                  250 / new_plans)
        rel_equal(self, self.components['review_records_per_plan'], 250 / new_plans)

    def test_baseline_fails_the_target_and_says_so(self):
        self.assertEqual(0.5, self.record['target'])
        self.assertEqual('ABOVE_TARGET', self.record['verdict'])
        self.assertGreater(self.record['measured']['review_records_per_plan'], 0.5)

    def test_the_historical_1_5_mismatch_is_recorded_not_hidden(self):
        note = self.record['note']
        self.assertIn('1.5', note)
        self.assertIn('does not reproduce', note)
        # every definition reported in the note is present as a component value
        for key in ('non_staging_review_records_per_plan', 'distinct_review_rounds_per_plan',
                    're_review_files_per_plan', 're_review_files_per_run_dir'):
            self.assertIn(key, self.components)


class ReportTraceability(unittest.TestCase):
    def test_traceability_delegates_to_claim_lint_or_says_uninstrumented(self):
        record = kpi.kpi_report_number_traceability()
        self.assertEqual(1.0, record['target'])
        self.assertTrue(record['definition'])
        self.assertEqual((ROOT / 'scripts' / 'claim_lint.py').is_file(),
                         record['components']['claim_lint_present'])
        if record['components']['claims_manifest_files'] == 0:
            self.assertEqual('NOT_INSTRUMENTED', record['status'])
            self.assertIsNone(record['measured'])
            self.assertTrue(record['reason'])
            self.assertIn('manifest', record['reason'])
        else:
            self.assertEqual('MEASURED', record['status'])
            self.assertGreaterEqual(record['measured']['traceability'], 0.0)
            self.assertLessEqual(record['measured']['traceability'], 1.0)

    def _case_root_with_manifest(self, directory: Path, mutate=None, *, report: str | None = None,
                                 name: str = 'REPORT-CLAIMS.json') -> Path:
        payload = json.loads((ROOT / 'tests' / 'fixtures' /
                              'claim-positive-signed-ladder.json').read_text(encoding='utf-8'))
        if mutate is not None:
            mutate(payload)
        if report is not None:
            payload['report'] = report
        (directory / name).write_text(json.dumps(payload, indent=1), encoding='utf-8')
        return directory

    def test_delegation_counts_every_claim_of_a_clean_manifest(self):
        with tempfile.TemporaryDirectory() as name:
            root = self._case_root_with_manifest(Path(name))
            record = kpi.kpi_report_number_traceability(root)
            self.assertEqual('MEASURED', record['status'])
            components = record['components']
            self.assertEqual(6, components['claims_checked'])
            self.assertEqual(6, components['claims_with_zero_errors'])
            self.assertEqual(0, components['claims_untraceable'])
            self.assertEqual(0, components['claim_lint_error_count'])
            self.assertEqual(1.0, record['measured']['traceability'])
            self.assertEqual('AT_TARGET', record['verdict'])

    def test_a_wrong_number_is_below_target_and_never_rounded_up(self):
        def corrupt(payload: dict) -> None:
            payload['claims'][0]['value'] = payload['claims'][0]['value'] + 1.0

        with tempfile.TemporaryDirectory() as name:
            root = self._case_root_with_manifest(Path(name), corrupt)
            record = kpi.kpi_report_number_traceability(root)
            self.assertEqual('MEASURED', record['status'])
            components = record['components']
            self.assertEqual(6, components['claims_checked'])
            self.assertEqual(5, components['claims_with_zero_errors'])
            self.assertEqual(1, components['claims_untraceable'])
            self.assertEqual(1, components['claim_lint_error_codes'].get('NUMBER_MISMATCH'))
            self.assertAlmostEqual(5 / 6, record['measured']['traceability'], delta=1e-12)
            self.assertEqual('BELOW_TARGET', record['verdict'])

    def test_a_manifest_with_no_claims_is_not_a_perfect_score(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'EMPTY-CLAIMS.json').write_text(json.dumps(
                {'schema_version': 1, 'claims': []}), encoding='utf-8')
            record = kpi.kpi_report_number_traceability(root)
            self.assertEqual('NOT_INSTRUMENTED', record['status'])
            self.assertIsNone(record['measured'])
            self.assertEqual(0, record['components']['claims_checked'])

    def test_an_unparseable_or_schema_invalid_manifest_is_flagged_not_scored(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'BROKEN-CLAIMS.json').write_text('{not json', encoding='utf-8')
            record = kpi.kpi_report_number_traceability(root)
            self.assertEqual(['BROKEN-CLAIMS.json'],
                             [Path(path).name for path in record['components']
                              ['invalid_manifest_files']])
            self.assertEqual('NOT_INSTRUMENTED', record['status'])

    def test_the_manifest_only_lint_scope_is_stated_in_the_record(self):
        with tempfile.TemporaryDirectory() as name:
            record = kpi.kpi_report_number_traceability(self._case_root_with_manifest(Path(name)))
            components = record['components']
            self.assertEqual('manifest_only', components['lint_mode'])
            self.assertFalse(components['report_text_checked'])
            self.assertEqual(['HASH_TRUNCATED', 'UNBACKED_HASH', 'RENDER_STALE'],
                             components['text_codes_out_of_scope'])
            self.assertEqual(1, components['manifests_without_report_text'])
            self.assertEqual(0, components['manifests_with_report_text'])
            self.assertEqual([], components['declared_report_paths'])

    def test_a_declared_report_turns_the_text_checks_on(self):
        """claim_lint reads the manifest's own "report" text; the KPI must stop calling it manifest_only."""
        with tempfile.TemporaryDirectory() as name:
            root = self._case_root_with_manifest(
                Path(name), report='tests/fixtures/claim-short-hash-incident.md')
            record = kpi.kpi_report_number_traceability(root)
            components = record['components']
            self.assertEqual('manifest_and_report_text', components['lint_mode'])
            self.assertTrue(components['report_text_checked'])
            self.assertEqual([], components['text_codes_out_of_scope'])
            self.assertEqual(1, components['manifests_with_report_text'])
            self.assertEqual(['tests/fixtures/claim-short-hash-incident.md'],
                             components['declared_report_paths'])
            # text-level findings carry no claim id and cannot be attributed to the (clean) claims
            self.assertGreaterEqual(components['text_level_error_count'], 1)
            self.assertEqual(0, components['claims_with_errors'])
            self.assertEqual('BELOW_TARGET', record['verdict'])

    def test_a_declared_but_missing_report_makes_the_pairing_broken_not_skipped(self):
        with tempfile.TemporaryDirectory() as name:
            root = self._case_root_with_manifest(
                Path(name), report='docs/REPORT-THAT-DOES-NOT-EXIST-20261030.md')
            record = kpi.kpi_report_number_traceability(root)
            components = record['components']
            self.assertEqual(1, components['manifests_with_unreadable_report'])
            self.assertEqual(1, components['claim_lint_error_codes'].get('REPORT_UNREADABLE'))
            self.assertEqual(components['claims_checked'], components['claims_untraceable'])
            self.assertEqual(components['claims_checked'], components['claims_with_errors'])
            self.assertEqual(0.0, record['measured']['traceability'])
            self.assertEqual('BELOW_TARGET', record['verdict'])

    def test_a_mixed_manifest_set_reports_per_manifest_report_coverage(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self._case_root_with_manifest(root, name='A-CLAIMS.json')
            self._case_root_with_manifest(
                root, report='tests/fixtures/claim-short-hash-incident.md', name='B-CLAIMS.json')
            record = kpi.kpi_report_number_traceability(root)
            components = record['components']
            self.assertEqual(2, components['claims_manifest_files'])
            self.assertEqual(1, components['manifests_with_report_text'])
            self.assertEqual(1, components['manifests_without_report_text'])
            self.assertEqual('manifest_and_report_text', components['lint_mode'])
            self.assertTrue(components['report_text_checked'])
            self.assertEqual(6 + 6, components['claims_checked'])

    def test_an_uncitable_success_claim_errors_and_cannot_score_100(self):
        """p30-claims' real failed-run fixture: an asserts_success claim over a refused record."""
        payload = json.loads((ROOT / 'tests' / 'fixtures' /
                              'claim-failed-run-citation.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'REPORT-CLAIMS.json').write_text(json.dumps(payload), encoding='utf-8')
            record = kpi.kpi_report_number_traceability(root)
            components = record['components']
            self.assertEqual(1, components['claims_checked'])
            self.assertEqual(0, components['claims_with_zero_errors'])
            self.assertEqual(1, components['claim_lint_error_codes'].get('UNCITABLE_RECORD'))
            # the value itself matches its record: the ratio is 1.0, but the error forbids AT_TARGET
            self.assertEqual(1.0, record['measured']['traceability'])
            self.assertEqual('BELOW_TARGET', record['verdict'])

    def test_a_warning_only_manifest_is_measured_but_flagged_not_clean(self):
        """A value citation of an ok-but-uncitable record is a WARNING (O8 R3), not an error."""
        class StubLinter:
            @staticmethod
            def lint(payload, root):
                return {'errors': [],
                        'warnings': [{'code': 'UNCITABLE_RECORD', 'claim': 'corroboration-only',
                                      'message': 'record claims ok but run_record_gate refuses it'}]}

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'REPORT-CLAIMS.json').write_text(json.dumps(
                {'schema_version': 1, 'claims': [{'id': 'corroboration-only'}]}), encoding='utf-8')
            with mock.patch.object(kpi, '_claim_lint_module', return_value=(StubLinter, None)):
                record = kpi.kpi_report_number_traceability(root)
            components = record['components']
            self.assertEqual('MEASURED', record['status'])
            self.assertEqual(1.0, record['measured']['traceability'])
            self.assertEqual(1, record['measured']['claim_lint_warnings'])
            self.assertTrue(record['measured']['uncitable_warning_only'])
            self.assertEqual(1, components['claims_with_warnings'])
            self.assertEqual({'UNCITABLE_RECORD': 1}, components['claim_lint_warning_codes'])
            self.assertEqual(0, components['claim_lint_error_count'])
            self.assertEqual('AT_TARGET', record['verdict'])  # numbers DO trace; the flag says why

    def test_text_level_findings_are_not_attributed_to_claims(self):
        class StubLinter:
            @staticmethod
            def lint(payload, root):
                return {'errors': [{'code': 'UNBACKED_HASH',
                                    'message': 'text quotes an unbacked 64-hex token'}],
                        'warnings': []}

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'REPORT-CLAIMS.json').write_text(json.dumps(
                {'schema_version': 1, 'claims': [{'id': 'c1'}]}), encoding='utf-8')
            with mock.patch.object(kpi, '_claim_lint_module', return_value=(StubLinter, None)):
                record = kpi.kpi_report_number_traceability(root)
            self.assertEqual(1, record['components']['text_level_error_count'])
            self.assertEqual(0, record['components']['claims_with_errors'])
            self.assertEqual('BELOW_TARGET', record['verdict'])

    def test_no_manifest_keeps_legacy_reports_uninstrumented(self):
        with tempfile.TemporaryDirectory() as name:
            record = kpi.kpi_report_number_traceability(Path(name))
            self.assertEqual('NOT_INSTRUMENTED', record['status'])
            self.assertIsNone(record['measured'])
            self.assertEqual(0, record['components']['claims_manifest_files'])
            self.assertIn('claim_lint', record['reason'])

    def test_traceability_never_reports_a_fabricated_one(self):
        record = kpi.kpi_report_number_traceability()
        if record['status'] == 'NOT_INSTRUMENTED':
            self.assertIsNone(record['measured'])
        self.assertIn(record['verdict'], {'NOT_INSTRUMENTED', 'AT_TARGET', 'BELOW_TARGET'})


class StopLoss(unittest.TestCase):
    def test_two_consecutive_fail_closed_stops_the_sample(self):
        result = kpi.consecutive_fail_closed(
            [{'plan_id': 'part40', 'fail_closed': True},
             {'plan_id': 'part41', 'fail_closed': True}],
            ['part40', 'part41', 'part42'])
        self.assertTrue(result['stop'])
        self.assertEqual('STOP_LOSS_CONSECUTIVE_FAIL_CLOSED', result['code'])
        self.assertEqual(2, result['consecutive_fail_closed'])
        self.assertEqual('part41', result['at_plan_id'])
        self.assertTrue(result['diagnosis_required'])
        self.assertEqual(2, result['examined_runs'])

    def test_an_interleaved_pass_does_not_stop(self):
        for sequence in ([True, False, True],
                         [True, False, False, True, False],
                         [True, False, True, False, True, False]):
            result = kpi.consecutive_fail_closed(
                [{'plan_id': 'part%d' % i, 'fail_closed': flag}
                 for i, flag in enumerate(sequence)],
                ['part%d' % i for i in range(len(sequence))])
            self.assertFalse(result['stop'], sequence)
            self.assertFalse(result['diagnosis_required'], sequence)
            self.assertLess(result['consecutive_fail_closed'], 2, sequence)
        interleaved = kpi.consecutive_fail_closed(
            [{'plan_id': 'a', 'fail_closed': True},
             {'plan_id': 'b', 'fail_closed': False},
             {'plan_id': 'c', 'fail_closed': True}], ['a', 'b', 'c'])
        self.assertFalse(interleaved['stop'])
        self.assertEqual(1, interleaved['consecutive_fail_closed'])

    def test_runs_outside_the_new_plan_set_are_ignored(self):
        result = kpi.consecutive_fail_closed(
            [{'plan_id': 'old1', 'fail_closed': True},
             {'plan_id': 'old2', 'fail_closed': True},
             {'plan_id': 'part50', 'fail_closed': True}],
            ['part50'])
        self.assertFalse(result['stop'])
        self.assertEqual(1, result['examined_runs'])

    def test_no_plan_log_is_reported_as_not_evaluated(self):
        status = kpi.stop_loss_status([])
        self.assertEqual('NOT_EVALUATED', status['status'])
        self.assertEqual(2, status['limit'])
        self.assertTrue(status['reason'])
        self.assertIn('consecutive_fail_closed', status['helper'])


class RecordShape(unittest.TestCase):
    def test_the_record_carries_the_four_kpis_and_no_fabricated_verdict(self):
        record = kpi.compute_kpis()
        self.assertIn('ZERO_STAR', record['compute'])
        self.assertEqual(['failed_run_star_share', 'review_rounds_per_new_plan',
                          'wall_clock_over_star', 'report_number_traceability'],
                         [item['id'] for item in record['kpis']])
        for item in record['kpis']:
            self.assertIn('definition', item)
            self.assertTrue(item['definition'])
            self.assertIn('target', item)
            self.assertTrue(item.get('verdict') or item.get('status'))
            if item.get('measured') is None:
                self.assertTrue(item.get('reason') or item.get('status') == 'NOT_MEASURABLE')
        self.assertEqual('NOT_EVALUATED', record['stop_loss']['status'])
        self.assertEqual(LEDGER['unique_runs'], record['star_ledger']['unique_runs'])

    def test_the_record_hashes_the_scripts_that_produced_it(self):
        record = kpi.compute_kpis()
        recorded = record['inputs']['scripts']['kpi_ledger.py']
        self.assertEqual(kpi.sha256_file(ROOT / 'scripts' / 'kpi_ledger.py'), recorded)
        writer = ROOT / 'scripts' / 'status_writer.py'
        if writer.is_file():
            self.assertEqual(kpi.sha256_file(writer),
                             record['inputs']['scripts']['status_writer.py'])

    def test_writing_the_record_refuses_an_existing_file_and_force_records_its_hash(self):
        with tempfile.TemporaryDirectory() as name:
            target = Path(name) / 'KPI.json'
            first = kpi.write_kpi_record({'a': 1}, target)
            self.assertTrue(target.is_file())
            self.assertTrue(target.read_bytes().endswith(b'\n'))
            self.assertEqual(first['file_sha256'], kpi.sha256_file(target))
            with self.assertRaises(FileExistsError):
                kpi.write_kpi_record({'a': 2}, target)
            second = kpi.write_kpi_record({'a': 2}, target, force=True)
            self.assertEqual(first['file_sha256'], second['previous']['file_sha256'])
            self.assertEqual({'a': 2}, json.loads(target.read_text(encoding='utf-8')))


if __name__ == '__main__':
    unittest.main()
