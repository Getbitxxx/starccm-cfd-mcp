"""F5 ratchets: the STATUS single writer and its self-consistency gate.

Why this exists. The P2 closeout shipped a STATUS document that carried ``q3_status="NOT_ACHIEVED"``
next to ``q="Q3"``/``q3_claim=true``, and a case-level ``q_state="Q0"`` after the case was raised to
Q3. No code refused it; a human found it at closeout. These tests pin:

* every contradiction the writer must refuse, by its exact stable code (not by a vague warning);
* the tamper/staleness path through the integrity hash;
* the writer refusing a contradictory document while leaving the target file BYTE-IDENTICAL;
* a valid write re-validating from the bytes on disk, with the previous content hash recorded;
* the live ``campaigns/uav-2026/STATUS.json``: it is READ-ONLY here. As of the 2026-10-30 closeout
  commit it has exactly one blocking violation (``phase`` was renamed to
  ``P2_CLOSED_Q3_SIGNED_20261030_CRITERION`` without adding that key to ``phase_states``, which also
  reddens ``tests/test_uav_campaign_status.py``). That defect is pinned and reported to the Lead --
  this task must not edit STATUS.json -- and the companion test proves the one-line repair validates.

Only temp dirs are ever written; the live STATUS.json and CURRENT.md are never touched.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import status_writer as sw  # noqa: E402

STATUS = ROOT / 'campaigns' / 'uav-2026' / 'STATUS.json'
WRITER = ROOT / 'scripts' / 'status_writer.py'

# The live blocking violations this checkout actually has (see module docstring).  A ratchet in the
# safe direction: a NEW violation reddens the test; when the Lead repairs the phase key the pin
# simply stops matching anything.
LIVE_KNOWN_VIOLATIONS = {('STATUS_PHASE_NOT_A_STATE', 'phase')}
LIVE_KNOWN_ADVISORIES = {'STATUS_STALE_Q_STATE', 'STATUS_STALE_Q3_REPORT_STATUS'}


def valid_document() -> dict:
    return {
        'schema_version': 1,
        'campaign': 'test-campaign',
        'goal': 'UAV external flow, Q3 tracked per case',
        'phase': 'P2_PROPELLER_Q3',
        'phase_states': {
            'P0_ISOLATION': 'DONE',
            'P1_AIRFOIL_Q3': 'DONE',
            'P2_PROPELLER_Q3': 'IN_PROGRESS',
            'P3_FULL_UAV_Q2': 'NOT_STARTED',
            'P4_USER_MODEL': 'NOT_STARTED',
        },
        'next_gate': 'five-point signed ladder',
        'forbidden': ['reopening old campaigns'],
        'cases': [
            {
                'case_id': 'case-airfoil', 'phase': 'P1_AIRFOIL_Q3', 'target_q': 'Q3', 'q': 'Q2',
                'source': 'public airfoil', 'reference': 'cases/airfoil/reference.json',
                'record': 'evidence/airfoil/Q2.json', 'case_spec_frozen': True,
                'tolerance_signed': True,
            },
            {
                'case_id': 'case-prop', 'phase': 'P2_PROPELLER_Q3', 'target_q': 'Q3', 'q': 'Q3',
                'source': 'manufacturer geometry', 'reference': 'cases/prop/reference.json',
                'record': 'evidence/prop/MESH-SCATTER.json', 'quality_state': 'Q3',
                'q3_claim': True, 'q3_status': 'ACHIEVED_UNDER_SIGNED_CRITERION',
                'engineering_verified': True, 'case_spec_frozen': True, 'tolerance_signed': True,
            },
        ],
        'q_counts': {'Q3': 1, 'Q2': 1, 'Q1': 0, 'Q0': 0},
        'updated': '2026-10-30',
        'last_updated': '2026-10-30',
        'last_round': 'round-test',
    }


def case(document: dict, case_id: str) -> dict:
    for entry in document['cases']:
        if entry.get('case_id') == case_id:
            return entry
    raise AssertionError('no case %r' % case_id)


def recount(document: dict) -> dict:
    counts = {q: 0 for q in sw.Q_STATES}
    for entry in document['cases']:
        if entry.get('q') in counts:
            counts[entry['q']] += 1
    document['q_counts'] = counts
    return document


def codes(document) -> set[str]:
    return {v.code for v in sw.validate_status(document)}


class LiveDocumentReadOnly(unittest.TestCase):
    """The live campaign state is inspected, never edited."""

    def setUp(self):
        self.document = sw.load_status(STATUS)

    def test_live_document_only_has_the_reported_blocking_defect(self):
        unknown = [(v.code, v.field) for v in sw.validate_status(self.document)
                   if (v.code, v.field) not in LIVE_KNOWN_VIOLATIONS]
        self.assertEqual([], unknown,
                         'STATUS.json grew a violation this task does not know about: %r'
                         % unknown)

    def test_live_phase_drift_is_reported_by_name(self):
        found = {v.code: v for v in sw.validate_status(self.document)}
        if 'STATUS_PHASE_NOT_A_STATE' in found:
            violation = found['STATUS_PHASE_NOT_A_STATE']
            self.assertEqual('phase', violation.field)
            self.assertIn(self.document['phase'], violation.message)
            self.assertIn('phase_states', violation.message)

    def test_live_document_is_one_declared_key_away_from_valid(self):
        repaired = copy.deepcopy(self.document)
        repaired.setdefault('phase_states', {})[repaired['phase']] = 'DECLARED_IN_TEST'
        # 2026-10-30 integration: the Lead wrote the real document through this writer, so the live
        # file is sealed.  The seal is bound to the content; a candidate that changes content is
        # re-sealed by the writer, while validation of a CANDIDATE is about its rules.
        repaired.pop('integrity', None)
        self.assertEqual([], sw.validate_status(repaired))

    def test_live_advisories_do_not_exceed_the_known_stale_leftovers(self):
        found = {v.code for v in sw.stale_findings(self.document)}
        self.assertTrue(found <= LIVE_KNOWN_ADVISORIES, 'unexpected advisory: %r'
                        % (found - LIVE_KNOWN_ADVISORIES))
        if not found:
            self.skipTest('the live document no longer carries stale leftovers; drop the pin')

    def test_live_document_is_unchanged_by_the_validation(self):
        before = STATUS.read_bytes()
        sw.check_document(sw.load_status(STATUS))
        self.assertEqual(before, STATUS.read_bytes())

    def test_the_writer_refuses_the_live_document_until_the_phase_key_exists(self):
        """The gate that would have caught the closeout drift, demonstrated on a byte copy."""
        if not sw.validate_status(self.document):
            self.skipTest('the live document is already valid; the refusal path is covered by '
                          'FailClosedWriter')
        with tempfile.TemporaryDirectory() as name:
            target = Path(name) / 'STATUS.json'
            target.write_bytes(STATUS.read_bytes())
            before = target.read_bytes()
            with self.assertRaises(sw.StatusWriteRefused) as caught:
                sw.write_status(target, self.document, 'tester', 'must be refused')
            self.assertEqual(before, target.read_bytes())
            self.assertIn('STATUS_PHASE_NOT_A_STATE',
                          {v.code for v in caught.exception.violations})

    def test_the_repaired_live_document_can_be_sealed_at_full_size(self):
        """Integration shape for the Lead's real write, proven on a COPY."""
        repaired = copy.deepcopy(self.document)
        repaired.setdefault('phase_states', {})[repaired['phase']] = 'DECLARED_IN_TEST'
        # the live copy is sealed; a content change is re-sealed by the writer, so drop the old seal
        repaired.pop('integrity', None)
        with tempfile.TemporaryDirectory() as name:
            target = Path(name) / 'STATUS.json'
            result = sw.write_status(target, repaired, 'tester', 'integration shape')
            self.assertGreater(result['bytes_written'], 10000)
            self.assertEqual([], sw.validate_status(sw.load_status(target)))


class ContradictionCodes(unittest.TestCase):
    """Every rule has a stable code and names the case/field it found."""

    def fixture(self) -> dict:
        return copy.deepcopy(valid_document())

    def assertCodes(self, document: dict, expected: set[str]) -> None:
        observed = codes(document)
        self.assertTrue(expected <= observed,
                        'missing %r in %r' % (sorted(expected - observed), sorted(observed)))
        violations = sw.validate_status(document)
        self.assertTrue(violations)

    def test_q3_claim_true_while_q_is_not_q3(self):
        document = self.fixture()
        case(document, 'case-prop')['q'] = 'Q2'
        recount(document)
        self.assertCodes(document, {'STATUS_Q3_CLAIM_NOT_Q3'})

    def test_q3_claim_true_without_q3_quality_state(self):
        document = self.fixture()
        del case(document, 'case-prop')['quality_state']
        self.assertCodes(document, {'STATUS_Q3_CLAIM_QUALITY_STATE'})

    def test_q3_claim_true_without_engineering_verification(self):
        document = self.fixture()
        case(document, 'case-prop')['engineering_verified'] = False
        self.assertCodes(document, {'STATUS_Q3_CLAIM_NOT_ENGINEERING_VERIFIED'})

    def test_q3_claim_true_without_frozen_spec(self):
        document = self.fixture()
        case(document, 'case-prop')['case_spec_frozen'] = False
        self.assertCodes(document, {'STATUS_Q3_CLAIM_SPEC_NOT_FROZEN'})

    def test_q3_claim_true_without_signed_tolerance(self):
        document = self.fixture()
        case(document, 'case-prop')['tolerance_signed'] = False
        self.assertCodes(document, {'STATUS_Q3_CLAIM_TOLERANCE_NOT_SIGNED'})

    def test_the_measured_closeout_contradiction_is_two_codes(self):
        """q3_status NOT_ACHIEVED on a Q3 case that claims Q3 == the archived defect shape."""
        document = self.fixture()
        prop = case(document, 'case-prop')
        prop['q3_status'] = 'NOT_ACHIEVED'
        self.assertCodes(document, {'STATUS_Q3_STATUS_CONTRADICTS_Q',
                                    'STATUS_Q3_STATUS_CONTRADICTS_CLAIM'})

    def test_q3_status_not_achieved_with_q_not_q3_is_valid(self):
        """The honest 'not achieved' state must stay writable."""
        document = self.fixture()
        prop = case(document, 'case-prop')
        prop.update({'q': 'Q2', 'quality_state': 'Q2', 'q3_claim': False,
                     'engineering_verified': False, 'q3_status': 'NOT_ACHIEVED'})
        recount(document)
        self.assertEqual([], sw.validate_status(document))

    def test_q3_claim_is_not_a_boolean(self):
        document = self.fixture()
        case(document, 'case-prop')['q3_claim'] = 'yes'
        self.assertCodes(document, {'STATUS_Q3_CLAIM_NOT_BOOL'})

    def test_q3_case_without_q3_status(self):
        document = self.fixture()
        del case(document, 'case-prop')['q3_status']
        self.assertCodes(document, {'STATUS_Q3_STATUS_MISSING'})

    def test_q3_case_without_reference(self):
        document = self.fixture()
        del case(document, 'case-prop')['reference']
        self.assertCodes(document, {'STATUS_Q3_REFERENCE_MISSING'})

    def test_q3_case_without_record(self):
        document = self.fixture()
        del case(document, 'case-prop')['record']
        self.assertCodes(document, {'STATUS_Q3_RECORD_MISSING'})

    def test_q_and_target_q_must_be_known_states(self):
        document = self.fixture()
        case(document, 'case-airfoil')['q'] = 'Q4'
        self.assertCodes(document, {'STATUS_CASE_Q_INVALID'})
        document = self.fixture()
        case(document, 'case-prop')['target_q'] = 'Q9'
        self.assertCodes(document, {'STATUS_CASE_TARGET_Q_INVALID'})

    def test_q_may_not_rank_above_target_q(self):
        document = self.fixture()
        prop = case(document, 'case-prop')
        prop['q3_claim'] = False
        prop['target_q'] = 'Q2'
        recount(document)
        self.assertCodes(document, {'STATUS_CASE_Q_ABOVE_TARGET'})

    def test_q_counts_must_tally_the_case_list(self):
        document = self.fixture()
        document['q_counts'] = {'Q3': 0, 'Q2': 2, 'Q1': 0, 'Q0': 0}
        self.assertCodes(document, {'STATUS_Q_COUNTS_MISMATCH'})

    def test_q_counts_may_not_invent_states(self):
        document = self.fixture()
        document['q_counts']['Q7'] = 0
        self.assertCodes(document, {'STATUS_Q_COUNTS_UNKNOWN_STATE'})

    def test_phase_must_be_a_declared_state_key(self):
        document = self.fixture()
        document['phase'] = 'P2_RENAMED_AT_CLOSEOUT'
        self.assertCodes(document, {'STATUS_PHASE_NOT_A_STATE'})

    def test_phase_states_must_cover_p0_to_p4(self):
        document = self.fixture()
        del document['phase_states']['P4_USER_MODEL']
        self.assertCodes(document, {'STATUS_PHASE_STATE_KEY_MISSING'})

    def test_phase_must_not_be_empty(self):
        document = self.fixture()
        document['phase'] = '   '
        self.assertCodes(document, {'STATUS_PHASE_MISSING'})

    def test_updated_must_equal_last_updated_iso_dates(self):
        document = self.fixture()
        document['updated'] = '2026-10-29'
        self.assertCodes(document, {'STATUS_UPDATED_MISMATCH'})
        document = self.fixture()
        document['updated'] = '30/10/2026'
        document['last_updated'] = '30/10/2026'
        self.assertCodes(document, {'STATUS_UPDATED_NOT_ISO_DATE',
                                    'STATUS_LAST_UPDATED_NOT_ISO_DATE'})

    def test_last_round_must_be_named(self):
        document = self.fixture()
        document['last_round'] = ''
        self.assertCodes(document, {'STATUS_LAST_ROUND_MISSING'})

    def test_violations_name_the_case_and_the_field(self):
        document = self.fixture()
        case(document, 'case-prop')['case_spec_frozen'] = False
        violation = [v for v in sw.validate_status(document)
                     if v.code == 'STATUS_Q3_CLAIM_SPEC_NOT_FROZEN'][0]
        self.assertEqual('case-prop', violation.case_id)
        self.assertEqual('case_spec_frozen', violation.field)
        self.assertIn('case-prop', str(violation))
        self.assertEqual('violation', violation.to_dict()['severity'])


class StaleLeftovers(unittest.TestCase):
    """Advisory tier: old true-claims that were not removed when they stopped being true."""

    def test_stale_q_state_is_advisory_not_blocking(self):
        document = copy.deepcopy(valid_document())
        case(document, 'case-prop')['q_state'] = 'Q0'
        self.assertEqual([], sw.validate_status(document))
        found = {v.code for v in sw.stale_findings(document)}
        self.assertIn('STATUS_STALE_Q_STATE', found)

    def test_stale_q3_report_status_is_advisory(self):
        document = copy.deepcopy(valid_document())
        case(document, 'case-prop')['q3_report'] = {'status': 'Q3_NOT_ACHIEVED'}
        document['q3_report'] = {'status': 'Q3_NOT_ACHIEVED'}
        self.assertEqual([], sw.validate_status(document))
        found = [v for v in sw.stale_findings(document)
                 if v.code == 'STATUS_STALE_Q3_REPORT_STATUS']
        self.assertEqual(2, len(found))
        self.assertTrue(all(v.severity == 'advisory' and v.to_dict()['severity'] == 'advisory'
                            for v in found))

    def test_check_document_reports_both_tiers(self):
        document = copy.deepcopy(valid_document())
        case(document, 'case-prop')['q_state'] = 'Q0'
        case(document, 'case-prop')['case_spec_frozen'] = False
        checked = sw.check_document(document)
        self.assertEqual({'STATUS_Q3_CLAIM_SPEC_NOT_FROZEN'},
                         {v.code for v in checked['violations']})
        self.assertEqual({'STATUS_STALE_Q_STATE'}, {v.code for v in checked['advisories']})


class Integrity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_write_roundtrips_and_revalidates_from_disk(self):
        path = self.dir / 'STATUS.json'
        result = sw.write_status(path, valid_document(), 'tester', 'unit test write')
        self.assertTrue(path.is_file())
        on_disk = sw.load_status(path)
        self.assertEqual([], sw.validate_status(on_disk))
        self.assertEqual(sw.content_sha256(on_disk), result['content_sha256'])
        self.assertEqual(result['content_sha256'],
                         on_disk['integrity']['content_sha256'])
        self.assertEqual(result['file_sha256'], sw.file_sha256(path.read_bytes()))
        self.assertEqual(1, len(on_disk['write_log']))
        entry = on_disk['write_log'][0]
        self.assertEqual('tester', entry['actor'])
        self.assertEqual('unit test write', entry['reason'])
        self.assertIsNone(entry['previous_content_sha256'])
        self.assertTrue(entry['written_at'].endswith('Z'))
        self.assertEqual([], list(self.dir.glob('*.tmp')))

    def test_second_write_records_the_previous_content_hash(self):
        path = self.dir / 'STATUS.json'
        first = sw.write_status(path, valid_document(), 'tester', 'first')
        first_bytes = path.read_bytes()
        second_doc = valid_document()
        second_doc['last_round'] = 'round-two'
        second = sw.write_status(path, second_doc, 'tester', 'second')
        self.assertEqual(second['previous_content_sha256'], first['content_sha256'])
        self.assertEqual(second['previous_file_sha256'], first['file_sha256'])
        on_disk = sw.load_status(path)
        self.assertEqual(2, len(on_disk['write_log']))
        self.assertEqual(first['file_sha256'], on_disk['write_log'][1]['previous_file_sha256'])
        self.assertNotEqual(first_bytes, path.read_bytes())

    def test_a_candidate_write_log_cannot_rewrite_the_files_history(self):
        """A legacy (unsealed) file has no history to protect; the candidate cannot invent one."""
        path = self.dir / 'STATUS.json'
        path.write_text(json.dumps(valid_document(), indent=1), encoding='utf-8')
        forged = valid_document()
        forged['write_log'] = [{'actor': 'forger', 'reason': 'invented history',
                                'written_at': '2020-01-01T00:00:00Z'}]
        sw.write_status(path, forged, 'tester', 'the only real write')
        on_disk = sw.load_status(path)
        self.assertEqual(1, len(on_disk['write_log']))
        self.assertNotIn('forger', [entry['actor'] for entry in on_disk['write_log']])

    def test_editing_the_write_log_of_a_sealed_document_is_refused(self):
        path = self.dir / 'STATUS.json'
        sw.write_status(path, valid_document(), 'tester', 'real first write')
        forged = sw.load_status(path)
        forged['write_log'] = [{'actor': 'forger', 'reason': 'invented history',
                                'written_at': '2020-01-01T00:00:00Z'}]
        with self.assertRaises(sw.StatusWriteRefused) as caught:
            sw.write_status(path, forged, 'tester', 'second write')
        self.assertEqual({'STATUS_INTEGRITY_MISMATCH'},
                         {v.code for v in caught.exception.violations})
        self.assertEqual('real first write',
                         sw.load_status(path)['write_log'][0]['reason'])

    def test_tampering_with_content_breaks_the_integrity_hash(self):
        path = self.dir / 'STATUS.json'
        sw.write_status(path, valid_document(), 'tester', 'seal')
        tampered = sw.load_status(path)
        tampered['last_round'] = 'quietly-edited-after-sealing'
        self.assertEqual({'STATUS_INTEGRITY_MISMATCH'}, codes(tampered))

    def test_tampering_with_the_file_bytes_breaks_the_integrity_hash(self):
        path = self.dir / 'STATUS.json'
        sw.write_status(path, valid_document(), 'tester', 'seal')
        tampered = sw.load_status(path)
        tampered['last_round'] = 'file-level-edit'
        path.write_text(json.dumps(tampered, indent=1), encoding='utf-8')
        self.assertIn('STATUS_INTEGRITY_MISMATCH', codes(sw.load_status(path)))

    def test_legacy_document_without_integrity_stays_readable(self):
        path = self.dir / 'legacy.json'
        path.write_text(json.dumps(valid_document(), indent=1), encoding='utf-8')
        self.assertEqual(set(), codes(sw.load_status(path)))
        sealed = sw.seal_status(path, 'tester', 'seal legacy document')
        on_disk = sw.load_status(path)
        self.assertEqual([], sw.validate_status(on_disk))
        self.assertEqual(sealed['content_sha256'], on_disk['integrity']['content_sha256'])
        self.assertEqual(1, len(on_disk['write_log']))

    def test_integrity_block_without_hash_is_refused(self):
        document = valid_document()
        document['integrity'] = {'algorithm': 'sha256'}
        self.assertEqual({'STATUS_INTEGRITY_HASH_MISSING'}, codes(document))


class FailClosedWriter(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def contradiction(self) -> dict:
        document = copy.deepcopy(valid_document())
        prop = case(document, 'case-prop')
        prop.update({'q': 'Q2', 'q3_claim': True, 'q3_status': 'NOT_ACHIEVED'})
        recount(document)
        return document

    def test_refusal_leaves_the_existing_file_byte_identical(self):
        path = self.dir / 'STATUS.json'
        sw.write_status(path, valid_document(), 'tester', 'baseline')
        before = path.read_bytes()
        with self.assertRaises(sw.StatusWriteRefused) as caught:
            sw.write_status(path, self.contradiction(), 'tester', 'must be refused')
        self.assertEqual(before, path.read_bytes())
        refused_codes = {v.code for v in caught.exception.violations}
        self.assertIn('STATUS_Q3_CLAIM_NOT_Q3', refused_codes)
        self.assertIn('STATUS_Q3_STATUS_CONTRADICTS_CLAIM', refused_codes)
        self.assertEqual([], list(self.dir.glob('*.tmp')),
                         'a refused write must not leave a temp file behind')

    def test_refusal_does_not_create_a_new_file(self):
        path = self.dir / 'missing' / 'STATUS.json'
        with self.assertRaises(sw.StatusWriteRefused):
            sw.write_status(path, self.contradiction(), 'tester', 'refused')
        self.assertFalse(path.exists())
        self.assertFalse(path.parent.exists())

    def test_unparsable_existing_file_is_refused_not_overwritten(self):
        path = self.dir / 'STATUS.json'
        path.write_text('{"phase": ', encoding='utf-8')
        before = path.read_bytes()
        with self.assertRaises(sw.StatusWriteRefused) as caught:
            sw.write_status(path, valid_document(), 'tester', 'refused')
        self.assertEqual({'STATUS_EXISTING_UNREADABLE'},
                         {v.code for v in caught.exception.violations})
        self.assertEqual(before, path.read_bytes())

    def test_actor_and_reason_are_required(self):
        path = self.dir / 'STATUS.json'
        with self.assertRaises(ValueError):
            sw.write_status(path, valid_document(), '', 'reason')
        with self.assertRaises(ValueError):
            sw.write_status(path, valid_document(), 'actor', '  ')
        self.assertFalse(path.exists())

    def test_write_log_that_is_not_a_list_is_refused(self):
        document = valid_document()
        document['write_log'] = {'oops': True}
        path = self.dir / 'STATUS.json'
        with self.assertRaises(sw.StatusWriteRefused) as caught:
            sw.write_status(path, document, 'tester', 'refused')
        self.assertEqual({'STATUS_WRITE_LOG_NOT_LIST'},
                         {v.code for v in caught.exception.violations})


class CommandLine(unittest.TestCase):
    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(WRITER)] + list(arguments),
                              capture_output=True, text=True, cwd=str(ROOT), timeout=60)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.valid = self.dir / 'valid.json'
        self.valid.write_text(json.dumps(valid_document(), indent=1), encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_validate_exits_zero_on_a_valid_document(self):
        completed = self.run_cli('--validate', str(self.valid))
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_validate_exits_one_and_names_the_code(self):
        broken = self.dir / 'broken.json'
        document = copy.deepcopy(valid_document())
        case(document, 'case-prop')['q3_status'] = 'NOT_ACHIEVED'
        broken.write_text(json.dumps(document, indent=1), encoding='utf-8')
        completed = self.run_cli('--validate', str(broken))
        self.assertEqual(1, completed.returncode)
        self.assertIn('STATUS_Q3_STATUS_CONTRADICTS_Q', completed.stderr)

    def test_check_consistency_exits_two_for_advisories_only(self):
        stale = self.dir / 'stale.json'
        document = copy.deepcopy(valid_document())
        case(document, 'case-prop')['q_state'] = 'Q0'
        stale.write_text(json.dumps(document, indent=1), encoding='utf-8')
        completed = self.run_cli('--check-consistency', str(stale))
        self.assertEqual(2, completed.returncode)
        self.assertIn('STATUS_STALE_Q_STATE', completed.stdout)
        self.assertEqual(0, self.run_cli('--validate', str(stale)).returncode)

    def test_write_mode_needs_actor_and_reason(self):
        completed = self.run_cli('--write', str(self.dir / 'out.json'),
                                 '--document', str(self.valid))
        self.assertEqual(2, completed.returncode)  # argparse usage error
        self.assertFalse((self.dir / 'out.json').exists())

    def test_write_mode_writes_a_valid_sealed_document(self):
        target = self.dir / 'out.json'
        completed = self.run_cli('--write', str(target), '--document', str(self.valid),
                                 '--actor', 'cli-test', '--reason', 'writer cli')
        self.assertEqual(0, completed.returncode, completed.stderr)
        sealed = sw.load_status(target)
        self.assertEqual([], sw.validate_status(sealed))
        self.assertEqual('cli-test', sealed['write_log'][0]['actor'])


if __name__ == '__main__':
    unittest.main()
