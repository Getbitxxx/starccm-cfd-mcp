# -*- coding: utf-8 -*-
"""F4 ratchet: report numbers/hashes are RENDERED from records and LINTED before commit.

AGENTS.md 2-O9 says an audit finding becomes a test in the same round or the round is not done.  The
three findings this file pins are the campaign's own recorded incidents (AGENTS.md "别重犯以下错误"):

  (a) a hand-typed short hash prefix was used as if it were the hash (``528fadab…``, ``1f6c9f``,
      ``…d7d773``);
  (b) a record belonging to a FAILED run was cited as ok / PASSED (``NOT_CITABLE_FAILED_RUN`` /
      ``NOT_CITABLE_NO_CLAIM``);
  (c) a number in a report did not match the record field it claimed to come from (e.g. the J=0.647
      CT was quoted with the J=0.548 value).

The fixtures are REAL archived records, not invented dicts: reading them proved that four Phase-2
campaign records suggested as "positive fixtures" do not assert ``status: ok`` at all, so
``run_record_gate`` returns ``NOT_CITABLE_NO_CLAIM`` for them.  That is why a manifest claim is a
"value citation" by default and only ``asserts_success: true`` claims are gated by the citation rule;
the reason is pinned by ``test_campaign_records_are_value_sources_not_success_certificates``.

Measured with ``scripts/run_record_gate.py`` on 2026-10-30 (see also the F4 task report):
  SIGNED-LADDER-20261030a.json                                -> NOT_CITABLE_NO_CLAIM
  MESH-FAMILY-GATE-20261030a.json                             -> NOT_CITABLE_NO_CLAIM
  uav-prop .../mrf-diagA-gamma-theta-j0455-20261030a/run.json -> NOT_CITABLE_NO_CLAIM
  test-evidence/.../diag-F2/field-profile-inventory.json      -> NOT_CITABLE_FAILED_RUN (run FAILED)
  test-evidence/.../air3b-mrf-sim-entry-20261004b/mrf_volume_mesh_quality.json -> CITABLE

Run: python3 -m unittest tests.test_claim_lint -v   (no pytest in this repo)
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import claim_lint as cl          # noqa: E402
import run_record_gate as gate   # noqa: E402

PY = sys.executable
FIXTURES = ROOT / 'tests' / 'fixtures'
POSITIVE_MANIFEST = FIXTURES / 'claim-positive-signed-ladder.json'
FAILED_CITATION_MANIFEST = FIXTURES / 'claim-failed-run-citation.json'
SHORT_HASH_FIXTURE = FIXTURES / 'claim-short-hash-incident.md'
FAILED_CITATION_FIXTURE = FIXTURES / 'claim-failed-run-citation.md'

CAMPAIGN = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e'
SIGNED_LADDER = CAMPAIGN / 'SIGNED-LADDER-20261030a.json'
MESH_FAMILY_GATE = CAMPAIGN / 'MESH-FAMILY-GATE-20261030a.json'
# A campaign run.json whose own status is FAILED (one of the 48 such files in this evidence tree).
FAILED_RUN_JSON = CAMPAIGN / 'mrf-diagA-gamma-theta-j0455-20261030a/run.json'
# The measured round-6 shape: the record says status ok, the run that produced it ended FAILED.
OK_IN_FAILED_RUN = ROOT / 'test-evidence/handoff-20261023/diag-F2/field-profile-inventory.json'
# A real record the gate certifies (its stage record says ok).
CITABLE_RECORD = (ROOT / 'test-evidence/air-three-blade/p-e2e/r10-sim-entry-b'
                  / 'air3b-mrf-sim-entry-20261004b/mrf_volume_mesh_quality.json')

PHASE3_DIR = ROOT / 'campaigns/uav-2026/evidence/phase3-selection-20261030'
PHASE3_CANDIDATES = PHASE3_DIR / 'CANDIDATES.json'
PHASE3_SELECTION = PHASE3_DIR / 'SELECTION.md'
PHASE3_SOURCES = PHASE3_DIR / 'SOURCES.json'
PHASE3_SUMS = PHASE3_DIR / 'SHA256SUMS.json'

_UNSET = object()


# --------------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------------

def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def codes(report: dict) -> tuple[list[str], list[str]]:
    return [item['code'] for item in report['errors']], [item['code'] for item in report['warnings']]


def make_claim(record_path: Path, field: str, *, cid: str = 'claim', text: str = 'claim',
               value: object = _UNSET, asserts_success: bool = False,
               root: Path = ROOT, record_sha256: str | None = None) -> dict:
    """Build a claim whose value and hash are measured from the real record (never hand-typed)."""
    record_path = Path(record_path)
    found, actual, _ = cl.field_value(load(record_path), field)
    if not found:
        raise AssertionError('test fixture bug: field %r not in %s' % (field, record_path))
    claim = {
        'id': cid,
        'text': text,
        'value': actual if value is _UNSET else value,
        'record': os.path.relpath(record_path, root),
        'field': field,
        'record_sha256': sha(record_path) if record_sha256 is None else record_sha256,
    }
    if asserts_success:
        claim['asserts_success'] = True
    return claim


def manifest_of(*claims: dict) -> dict:
    return {'schema_version': 1, 'claims': list(claims)}


def block_for(manifest: dict, root: Path = ROOT) -> str:
    return cl.render(manifest, root)


# --------------------------------------------------------------------------------------------------
# positive: real campaign records render and lint clean
# --------------------------------------------------------------------------------------------------

class PositiveRenderedClaims(unittest.TestCase):
    def setUp(self):
        self.manifest = cl.load_manifest(POSITIVE_MANIFEST)
        self.text = '# Signed-ladder re-signing\n\n' + block_for(self.manifest) + '\n\nProse below.\n'

    def test_positive_manifest_lints_clean_with_and_without_text(self):
        bare = cl.lint(self.manifest, ROOT)
        self.assertEqual((bare['errors'], bare['warnings']), ([], []), bare)
        report = cl.lint(self.manifest, ROOT, self.text)
        self.assertEqual((report['errors'], report['warnings']), ([], []), report)

    def test_render_is_deterministic_and_copies_values_and_hashes_from_the_records(self):
        first = cl.render(self.manifest, ROOT)
        second = cl.render(POSITIVE_MANIFEST, ROOT)          # path input must render identically
        self.assertEqual(first, second)
        for claim in self.manifest['claims']:
            found, value, _ = cl.field_value(load(ROOT / claim['record']), claim['field'])
            self.assertTrue(found)
            self.assertIn(cl.format_value(value), first)
            self.assertIn(sha(ROOT / claim['record']), first)     # measured, not the manifest's claim
            self.assertIn(claim['record'], first)
            self.assertIn(claim['field'], first)

    def test_manifest_hashes_are_the_measured_record_hashes(self):
        # If a record is re-generated, this fails loudly: re-render the block, never hand-edit hashes.
        for claim in self.manifest['claims']:
            self.assertEqual(sha(ROOT / claim['record']), claim['record_sha256'], claim['record'])

    def test_campaign_records_are_value_sources_not_success_certificates(self):
        # The reason `asserts_success` is an explicit opt-in: these real Phase-2 records have no
        # measurement-level "status: ok", so the gate refuses to certify them.  A manifest that quotes
        # their numbers is a value citation, and must not pretend otherwise.
        for record in (SIGNED_LADDER, MESH_FAMILY_GATE):
            verdict = gate.citation_status(record)
            self.assertEqual('NOT_CITABLE_NO_CLAIM', verdict['state'], record)
            self.assertFalse(gate.certifies_ok(load(record)))


# --------------------------------------------------------------------------------------------------
# negative controls: one stable code per defect shape
# --------------------------------------------------------------------------------------------------

class StableCodes(unittest.TestCase):
    def setUp(self):
        self.manifest = cl.load_manifest(POSITIVE_MANIFEST)
        self.block = block_for(self.manifest)

    def lint(self, manifest, text=None):
        return cl.lint(manifest, ROOT, text)

    def assert_codes(self, report, expected_errors, expected_warnings=()):
        self.assertEqual([item['code'] for item in report['errors']], list(expected_errors), report)
        self.assertEqual([item['code'] for item in report['warnings']], list(expected_warnings), report)

    def test_wrong_sha_is_hash_mismatch(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['claims'][0]['record_sha256'] = '0' * 64
        self.assert_codes(self.lint(manifest, self.block), ['HASH_MISMATCH'])

    def test_missing_record_is_hash_missing_record(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['claims'][0]['record'] = 'campaigns/uav-2026/evidence/does-not-exist.json'
        self.assert_codes(self.lint(manifest), ['HASH_MISSING_RECORD'])

    def test_short_manifest_hash_is_hash_missing_record(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['claims'][0]['record_sha256'] = '528fadab'      # the historical hand-typed prefix
        self.assert_codes(self.lint(manifest), ['HASH_MISSING_RECORD'])

    def test_missing_field_is_field_missing(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['claims'][0]['field'] = 'points.J0647.no_such_field'
        self.assert_codes(self.lint(manifest), ['FIELD_MISSING'])

    def test_number_from_the_wrong_field_is_number_mismatch(self):
        # Historical shape (c): quote the J=0.548 CT while claiming it is the J=0.647 value.
        manifest = copy.deepcopy(self.manifest)
        found, other_point_ct, _ = cl.field_value(load(SIGNED_LADDER), 'points.J0548.ct')
        self.assertTrue(found)
        manifest['claims'][0]['value'] = other_point_ct
        report = self.lint(manifest)
        self.assert_codes(report, ['NUMBER_MISMATCH'])
        self.assertEqual(report['errors'][0]['field'], 'points.J0647.ct')

    def test_truncated_float_is_number_mismatch(self):
        # Copying 0.03568446707542939 as 0.0356844670754294 is exactly the hand-typed-number failure.
        manifest = copy.deepcopy(self.manifest)
        manifest['claims'][0]['value'] = 0.0356844670754294
        self.assert_codes(self.lint(manifest), ['NUMBER_MISMATCH'])

    def test_unbacked_full_hash_is_unbacked_hash(self):
        unbacked = 'deadbeef' * 8
        self.assert_codes(self.lint(self.manifest, self.block + '\nsha256 ' + unbacked + '\n'),
                          ['UNBACKED_HASH'])

    def test_hash_nested_in_a_cited_record_is_backed(self):
        # The rendered reference sha (a 64-hex VALUE inside SIGNED-LADDER) must not be "unbacked".
        reference_sha = load(SIGNED_LADDER)['tolerance_source']['reference_sha256']
        self.assertIn(reference_sha, self.block)
        self.assert_codes(self.lint(self.manifest, self.block + '\nsee also %s\n' % reference_sha), [])

    def test_stale_block_is_render_stale(self):
        stale = self.block.replace('0.03568446707542939', '0.035684467075429391')
        self.assertNotEqual(stale, self.block)
        self.assert_codes(self.lint(self.manifest, stale), ['RENDER_STALE'])

    def test_missing_block_is_render_stale(self):
        self.assert_codes(self.lint(self.manifest, 'a report with no rendered block'), ['RENDER_STALE'])

    def test_bad_schema_is_schema_invalid(self):
        bad = {'schema_version': 2, 'claims': []}
        self.assert_codes(self.lint(bad), ['SCHEMA_INVALID'])
        missing_key = copy.deepcopy(self.manifest)
        del missing_key['claims'][0]['record']
        self.assert_codes(self.lint(missing_key), ['SCHEMA_INVALID'])

    def test_render_refuses_to_silently_drop_a_claim(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['claims'][0]['field'] = 'points.NOPE'
        with self.assertRaises(cl.ClaimLintError):
            cl.render(manifest, ROOT)


# --------------------------------------------------------------------------------------------------
# the historical incidents as fixtures / real-record ratchets
# --------------------------------------------------------------------------------------------------

class HistoricalIncidentRatchets(unittest.TestCase):
    def test_short_hash_prefixes_are_rejected(self):
        manifest = cl.load_manifest(POSITIVE_MANIFEST)
        text = block_for(manifest) + '\n' + SHORT_HASH_FIXTURE.read_text(encoding='utf-8')
        report = cl.lint(manifest, ROOT, text)
        self.assertEqual([item['code'] for item in report['errors']],
                         ['HASH_TRUNCATED'] * 3, report)
        self.assertEqual({item['token'] for item in report['errors']},
                         {'528fadab', '1f6c9f', 'd7d773'})

    def test_failed_run_record_cited_as_ok_is_refused(self):
        manifest = cl.load_manifest(FAILED_CITATION_MANIFEST)
        text = block_for(manifest) + '\n' + FAILED_CITATION_FIXTURE.read_text(encoding='utf-8')
        report = cl.lint(manifest, ROOT, text)
        self.assertEqual(codes(report), (['UNCITABLE_RECORD'], []), report)
        error = report['errors'][0]
        measured = gate.citation_status(OK_IN_FAILED_RUN)
        # Assert the REAL verdict run_record_gate produces, not an invented one (measured 2026-10-30).
        self.assertEqual('NOT_CITABLE_FAILED_RUN', measured['state'])
        self.assertEqual(measured['state'], error['verdict'])
        self.assertEqual('run', error['decided_by'])
        self.assertEqual(measured['reason'], error['reason'])
        self.assertEqual('FAILED', measured['run_state'])

    def test_failed_campaign_run_json_is_uncitable(self):
        claim = make_claim(FAILED_RUN_JSON, 'status', cid='diag-a-run-status',
                           text='diag-A MRF trial run status', asserts_success=True)
        self.assertEqual('FAILED', claim['value'])
        manifest = manifest_of(claim)
        report = cl.lint(manifest, ROOT, block_for(manifest))
        self.assertEqual(codes(report), (['UNCITABLE_RECORD'], []), report)
        measured = gate.citation_status(FAILED_RUN_JSON)
        self.assertEqual('NOT_CITABLE_NO_CLAIM', measured['state'])
        self.assertEqual(measured['state'], report['errors'][0]['verdict'])

    def test_failed_campaign_run_json_can_still_be_quoted_truthfully(self):
        # Quoting "this run FAILED" is legitimate; the incident is quoting it AS success.
        claim = make_claim(FAILED_RUN_JSON, 'status', cid='diag-a-run-status',
                           text='diag-A MRF trial run status', asserts_success=False)
        manifest = manifest_of(claim)
        report = cl.lint(manifest, ROOT, block_for(manifest))
        self.assertEqual(codes(report), ([], []), report)

    def test_ok_record_in_failed_run_warns_as_corroboration(self):
        # O8 R3 boundary: a record that claims ok inside a failed run may be quoted as corroboration,
        # but the lint must say so - it is not a certificate.
        claim = make_claim(OK_IN_FAILED_RUN, 'status', cid='diag-f2', text='diag-F2 status')
        manifest = manifest_of(claim)
        report = cl.lint(manifest, ROOT, block_for(manifest))
        self.assertEqual(codes(report), ([], ['UNCITABLE_RECORD']), report)
        self.assertEqual('NOT_CITABLE_FAILED_RUN', report['warnings'][0]['verdict'])

    def test_citable_record_can_be_cited_as_success(self):
        claim = make_claim(CITABLE_RECORD, 'measured_cell_quality_min', cid='mesh-quality',
                           text='measured minimum cell quality', asserts_success=True)
        manifest = manifest_of(claim)
        self.assertEqual('CITABLE', gate.citation_status(CITABLE_RECORD)['state'])
        report = cl.lint(manifest, ROOT, block_for(manifest))
        self.assertEqual(codes(report), ([], []), report)

    def test_citation_verdicts_are_delegated_to_run_record_gate(self):
        # No reimplementation: the module MUST call the gate module, and its verdict must match.
        self.assertIs(cl.run_record_gate, gate)
        cases = [
            (SIGNED_LADDER, 'points.J0647.status', 'PASS'),
            (FAILED_RUN_JSON, 'status', 'FAILED'),
            (OK_IN_FAILED_RUN, 'status', 'ok'),
            (CITABLE_RECORD, 'measured_cell_quality_min', None),
        ]
        for record, field, expected_value in cases:
            payload = load(record)
            found, value, _ = cl.field_value(payload, field)
            self.assertTrue(found, (record, field))
            if expected_value is not None:
                self.assertEqual(expected_value, value)
        for record, field, _value in cases:
            claim = make_claim(record, field, cid='delegate', asserts_success=True)
            manifest = manifest_of(claim)
            report = cl.lint(manifest, ROOT)
            measured = gate.citation_status(record)['state']
            if measured == 'CITABLE':
                self.assertFalse([e for e in report['errors'] if e['code'] == 'UNCITABLE_RECORD'],
                                 (record, report))
            else:
                refused = [e for e in report['errors'] if e['code'] == 'UNCITABLE_RECORD']
                self.assertEqual(1, len(refused), (record, report))
                self.assertEqual(measured, refused[0]['verdict'])


class PathAndBackingResolution(unittest.TestCase):
    def test_list_indices_and_nested_paths_resolve(self):
        claims = [
            make_claim(MESH_FAMILY_GATE, 'families.0.accepted', cid='family0-accepted',
                       text='first declared mesh family accepted'),
            make_claim(MESH_FAMILY_GATE, 'families.0.members.0.cells', cid='family0-production-cells',
                       text='production member cell count'),
        ]
        self.assertFalse(claims[0]['value'])
        self.assertEqual(2453722, claims[1]['value'])
        manifest = manifest_of(*claims)
        self.assertEqual(([], []), codes(cl.lint(manifest, ROOT, block_for(manifest))))

    def test_backing_record_hashes_can_be_quoted_without_a_claim(self):
        # A manifest may name records whose hashes it quotes (e.g. a SHA256SUMS file) without making a
        # per-field claim about each one.
        manifest = manifest_of(make_claim(MESH_FAMILY_GATE, 'thresholds.mesh_min_cell_quality',
                                          cid='family-gate-min-quality'))
        block = block_for(manifest)
        quoted = load(SIGNED_LADDER)['tolerance_source']['reference_sha256']
        text = block + '\nsee %s\n' % quoted
        self.assertEqual((['UNBACKED_HASH'], []), codes(cl.lint(manifest, ROOT, text)))
        manifest['backing_records'] = [os.path.relpath(SIGNED_LADDER, ROOT)]
        self.assertEqual(([], []), codes(cl.lint(manifest, ROOT, text)))

    def test_missing_backing_record_is_hash_missing_record(self):
        manifest = manifest_of(make_claim(MESH_FAMILY_GATE, 'thresholds.mesh_min_cell_quality',
                                          cid='family-gate-min-quality'))
        manifest['backing_records'] = ['campaigns/uav-2026/evidence/does-not-exist.json']
        self.assertEqual((['HASH_MISSING_RECORD'], []), codes(cl.lint(manifest, ROOT)))
        manifest['backing_records'] = 'not-a-list'
        self.assertEqual((['SCHEMA_INVALID'], []), codes(cl.lint(manifest, ROOT)))


class DeclaredReportPairing(unittest.TestCase):
    """A manifest can name its report ("report"): lint(manifest, root) then checks the text too.

    This is the PAIRING convention a manifest-discovery machine (e.g. the F6 KPI ledger) uses so the
    text-level codes become reachable without guessing report filenames.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.record = self.tmp / 'record.json'
        self.record.write_text(json.dumps({'measured': 1.5}), encoding='utf-8')
        self.manifest = manifest_of(make_claim(self.record, 'measured', cid='paired', text='paired',
                                               root=self.tmp))

    def write_report(self, extra: str = '') -> None:
        text = cl.render(self.manifest, self.tmp) + '\n' + extra
        (self.tmp / 'REPORT.md').write_text(text, encoding='utf-8')

    def test_declared_report_is_linted_without_a_text_argument(self):
        self.write_report()
        self.manifest['report'] = 'REPORT.md'
        self.assertEqual(([], []), codes(cl.lint(self.manifest, self.tmp)))

    def test_declared_report_reaches_text_codes_and_explicit_text_wins(self):
        self.write_report('hand-typed hash 1f6c9f\n')
        self.manifest['report'] = 'REPORT.md'
        report = cl.lint(self.manifest, self.tmp)
        self.assertEqual(['HASH_TRUNCATED'], [item['code'] for item in report['errors']])
        clean = cl.lint(self.manifest, self.tmp, text=cl.render(self.manifest, self.tmp))
        self.assertEqual(([], []), codes(clean))

    def test_missing_or_mistyped_declared_report(self):
        self.manifest['report'] = 'MISSING.md'
        self.assertEqual((['REPORT_UNREADABLE'], []), codes(cl.lint(self.manifest, self.tmp)))
        self.manifest['report'] = 3
        self.assertEqual((['SCHEMA_INVALID'], []), codes(cl.lint(self.manifest, self.tmp)))

    def test_cli_uses_the_declared_report(self):
        self.write_report('hash 528fadab\n')
        self.manifest['report'] = 'REPORT.md'
        manifest_path = self.tmp / 'manifest.json'
        manifest_path.write_text(json.dumps(self.manifest), encoding='utf-8')
        result = subprocess.run([PY, str(ROOT / 'scripts/claim_lint.py'), '--manifest',
                                 str(manifest_path), '--root', str(self.tmp)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn('HASH_TRUNCATED',
                      [item['code'] for item in json.loads(result.stdout)['errors']])


class RecordDriftAfterTheReportWasWritten(unittest.TestCase):
    def test_changed_record_is_hash_mismatch_and_stale_and_number_mismatch(self):
        tmp = Path(tempfile.mkdtemp())
        record = tmp / 'record.json'
        record.write_text(json.dumps({'status': 'ok', 'measured': 1.5}), encoding='utf-8')
        manifest = manifest_of(make_claim(record, 'measured', cid='drift', text='drift',
                                          root=tmp, asserts_success=False))
        text = cl.render(manifest, tmp)
        self.assertEqual([], cl.lint(manifest, tmp, text)['errors'])
        record.write_text(json.dumps({'status': 'ok', 'measured': 1.6}), encoding='utf-8')
        report = cl.lint(manifest, tmp, text)
        self.assertEqual(['HASH_MISMATCH', 'NUMBER_MISMATCH', 'RENDER_STALE'],
                         [item['code'] for item in report['errors']], report)


# --------------------------------------------------------------------------------------------------
# CLI contract: exit 1 on any error, --render prints the block, never edits the report
# --------------------------------------------------------------------------------------------------

class CommandLine(unittest.TestCase):
    def run_cli(self, *args: str):
        return subprocess.run([PY, str(ROOT / 'scripts/claim_lint.py'), *args],
                              capture_output=True, text=True, timeout=60)

    def test_cli_render_prints_the_block_and_exits_zero(self):
        result = self.run_cli('--manifest', str(POSITIVE_MANIFEST), '--render', '--root', str(ROOT))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(cl.render(POSITIVE_MANIFEST, ROOT), result.stdout)

    def test_cli_lint_exit_codes(self):
        tmp = Path(tempfile.mkdtemp())
        text = tmp / 'report.md'
        text.write_text('# report\n\n' + cl.render(POSITIVE_MANIFEST, ROOT), encoding='utf-8')
        good = self.run_cli('--manifest', str(POSITIVE_MANIFEST), '--text', str(text),
                            '--root', str(ROOT))
        self.assertEqual(0, good.returncode, good.stdout + good.stderr)
        report = json.loads(good.stdout)
        self.assertEqual(([], []), (report['errors'], report['warnings']))

        text.write_text(text.read_text(encoding='utf-8') + '\nold hash 1f6c9f\n', encoding='utf-8')
        bad = self.run_cli('--manifest', str(POSITIVE_MANIFEST), '--text', str(text),
                           '--root', str(ROOT))
        self.assertEqual(1, bad.returncode)
        self.assertIn('HASH_TRUNCATED', [item['code'] for item in json.loads(bad.stdout)['errors']])

    def test_cli_render_does_not_edit_the_report(self):
        tmp = Path(tempfile.mkdtemp())
        text = tmp / 'report.md'
        original = '# report\n\nno block here\n'
        text.write_text(original, encoding='utf-8')
        self.run_cli('--manifest', str(POSITIVE_MANIFEST), '--render', '--text', str(text),
                     '--root', str(ROOT))
        self.assertEqual(original, text.read_text(encoding='utf-8'))


# --------------------------------------------------------------------------------------------------
# Phase 3 selection evidence: must lint clean when it exists; SKIP (never fabricate) until then
# --------------------------------------------------------------------------------------------------

def _leaf_paths(value, prefix=()):
    if isinstance(value, dict):
        for key, item in value.items():
            key = str(key)
            if '.' in key:                      # the dotted field grammar cannot address it
                continue
            yield from _leaf_paths(item, prefix + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _leaf_paths(item, prefix + (str(index),))
    else:
        yield prefix, value


def phase3_manifest(max_claims: int = 250) -> dict:
    payload = load(PHASE3_CANDIDATES)
    claims = []
    for path, value in _leaf_paths(payload):
        field = '.'.join(path)
        claims.append({
            'id': 'candidates.' + field,
            'text': field,
            'value': value,
            'record': os.path.relpath(PHASE3_CANDIDATES, ROOT),
            'field': field,
            'record_sha256': sha(PHASE3_CANDIDATES),
        })
        if len(claims) >= max_claims:
            break
    if not claims:
        raise unittest.SkipTest('CANDIDATES.json has no addressable leaf fields to claim')
    backing = [os.path.relpath(path, ROOT)
               for path in (PHASE3_SOURCES, PHASE3_SUMS) if path.is_file()]
    return {'schema_version': 1, 'claims': claims, 'backing_records': backing}


class Phase3SelectionClaims(unittest.TestCase):
    """P3.1 selection evidence produced by the scout must quote numbers/hashes traceably.

    The evidence directory is produced in parallel, so this test SKIPS - with the reason - until
    CANDIDATES.json and SELECTION.md both exist.  It never fabricates the evidence and never writes
    under another teammate's evidence directory.
    """

    def setUp(self):
        if not PHASE3_CANDIDATES.is_file() or not PHASE3_SELECTION.is_file():
            self.skipTest('Phase 3 selection evidence not produced yet (need CANDIDATES.json + '
                          'SELECTION.md under %s); refusing to fabricate it' % PHASE3_DIR)

    def test_candidates_numbers_and_hashes_render_and_selection_lints_clean(self):
        manifest = phase3_manifest()
        selection = PHASE3_SELECTION.read_text(encoding='utf-8-sig')
        text = cl.render(manifest, ROOT) + '\n\n' + selection
        report = cl.lint(manifest, ROOT, text)
        self.assertEqual(report['errors'], [], report)
        self.assertEqual(report['warnings'], [], report)

    def test_selection_does_not_quote_short_or_unbacked_hashes(self):
        manifest = phase3_manifest()
        selection = PHASE3_SELECTION.read_text(encoding='utf-8-sig')
        report = cl.lint(manifest, ROOT, selection)     # no block in the prose: RENDER_STALE expected
        error_codes = {item['code'] for item in report['errors']}
        self.assertNotIn('HASH_TRUNCATED', error_codes, report)
        self.assertNotIn('UNBACKED_HASH', error_codes, report)
        self.assertEqual({'RENDER_STALE'}, error_codes, report)

    def test_hash_manifest_matches_the_evidence_bytes(self):
        if not PHASE3_SUMS.is_file():
            self.skipTest('SHA256SUMS.json not produced yet')
        sums = load(PHASE3_SUMS)
        table = sums.get('files') if isinstance(sums, dict) else None
        if not isinstance(table, dict):
            table = sums if isinstance(sums, dict) else {}
        checked = 0
        for name, entry in table.items():
            digest = entry.get('sha256') if isinstance(entry, dict) else entry
            if not isinstance(digest, str) or len(digest) != 64:
                continue
            for candidate in (ROOT / name, PHASE3_DIR / name):
                if candidate.is_file():
                    self.assertEqual(digest.lower(), sha(candidate), 'SHA256SUMS.json says %s has %s'
                                     % (candidate, digest))
                    checked += 1
                    break
        if checked == 0:
            self.skipTest('SHA256SUMS.json format not recognised or no entry resolves to a local file')


if __name__ == '__main__':
    unittest.main()
