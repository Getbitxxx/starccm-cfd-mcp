"""Counterexamples for C3, the third user-signed contract amendment of 2026-10-23.

C3 gives execution authority a second legal shape, the ONE-SHOT AUTHORIZATION
COLLECTION, for a campaign assembled from per-job one-shot admissions: no root request,
no ordered steps graph, no runtime manifest snapshot. Five properties, each checked
against a record.

The clause must fail closed, and it must fail closed in the direction that matters most:
it accepts no retroactively authored authorization file, steps graph or manifest snapshot.
Two of the tests below exist only to pin that.

The positive example is built from this campaign's real records - the members that do
carry an admission-named independent review - because the point of the clause is that it
describes evidence that already exists, not evidence written to fit.
"""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT))

import quality_contract as q  # noqa: E402

COLLECTION = ROOT / 'docs/campaign-binding-e-num-20261023/EXECUTION-AUTHORITY-COLLECTION.json'


def write_collection(directory, collection):
    """Write the binding the way the machine will read it: pinned by hash."""
    path = Path(directory) / 'COLLECTION.json'
    path.write_text(json.dumps(collection, indent=2, ensure_ascii=False) + '\n',
                    encoding='utf-8')
    return {'path': str(path),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


class C3Collection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        raw = json.loads(COLLECTION.read_text(encoding='utf-8'))
        # The real members that carry an admission-named independent review. Every other
        # field is the real one: these four are the campaign's, not a fixture's.
        keep = [m for m in raw['members'] if m['independent_review']]
        self.assertEqual(4, len(keep), 'the real collection must still carry four '
                                       'reviewed members; if it does not, this test is '
                                       'pointing at the wrong evidence')
        raw['members'] = keep
        raw['declared_jobs'] = [m['job'] for m in keep]
        self.legal = raw
        self.qualified = [m['job'] for m in keep]

    def _run(self, collection):
        ref = write_collection(self.temp.name, collection)
        return q.execution_authority_collection({'collection': ref})

    def _mutate(self, job, **fields):
        c = copy.deepcopy(self.legal)
        member = next(m for m in c['members'] if m['job'] == job)
        member.update(fields)
        return c

    def test_the_legal_collection_is_accepted(self):
        """The positive example: real records, real commits, nothing authored to fit."""
        authority, errors = self._run(self.legal)
        self.assertEqual([], errors, 'a collection of real, review-backed one-shot '
                                     'admissions must be a legal authority')
        self.assertEqual(q.COLLECTION_FORM, authority['form'])
        self.assertEqual(4, authority['collection_members'])
        self.assertEqual(q.active_runtime_pointer(), authority['runtime_digest'])
        self.assertIn('case_spec', authority)

    def test_the_clause_refuses_retrofit_in_its_own_text(self):
        """The anti-freedom words are part of the clause, not a comment about it."""
        self.assertIn('不接受事后补写', q.COLLECTION_NO_RETROFIT)
        self.assertIn('manifest 快照', q.COLLECTION_NO_RETROFIT)
        self.assertEqual(q.COLLECTION_NO_RETROFIT, self.legal['no_retrofit'],
                         'the collection must carry the clause verbatim')
        self.assertIn('优先用 pipeline 执行', q.COLLECTION_FORWARD_LOOKING,
                      'the forward-looking clause must be written down too')

    def test_an_inverted_admission_is_refused(self):
        """Admission after submission is not authority. Swap the two records."""
        job = self.qualified[0]
        member = next(m for m in self.legal['members'] if m['job'] == job)
        c = self._mutate(job, admission=member['submission_record'],
                         submission_record=member['admission'])
        _, errors = self._run(c)
        self.assertIn('execution:collection_order_inverted:' + job, errors)

    def test_a_member_without_an_independent_review_is_refused(self):
        c = self._mutate(self.qualified[0], independent_review=None)
        _, errors = self._run(c)
        self.assertIn('execution:collection_member_review_missing:' + self.qualified[0],
                      errors)

    def test_a_review_the_admission_does_not_name_is_refused(self):
        """A review of something else is not a review of this admission."""
        job = self.qualified[0]
        member = next(m for m in self.legal['members'] if m['job'] == job)
        other = next(m for m in self.legal['members'] if m['job'] != job)
        self.assertNotEqual(member['independent_review'], other['independent_review'])
        c = self._mutate(job, independent_review=other['independent_review'])
        _, errors = self._run(c)
        self.assertIn('execution:collection_review_not_named_by_admission:' + job, errors)

    def test_a_review_written_after_submission_is_refused(self):
        """The machine form of the no-retrofit clause: order, not declaration."""
        job = self.qualified[0]
        # A real review file from this campaign, committed the following day: an
        # independent review of the F4 branch decision, which has nothing to do with this
        # member and was written after this member ran. Either defect alone is enough;
        # the one this test pins is the timestamp.
        late = str(ROOT / 'test-evidence/f4-discriminate-20261023/'
                          'F4-BRANCH-INDEPENDENT-REVIEW.json')
        late = {'path': late, 'sha256': q.sha256(late)}
        c = self._mutate(job, independent_review=late)
        _, errors = self._run(c)
        self.assertIn('execution:collection_review_retrofitted:' + job, errors)

    def test_a_tampered_collection_binding_is_refused(self):
        c = copy.deepcopy(self.legal)
        ref = write_collection(self.temp.name, c)
        ref['sha256'] = '0' * 64
        _, errors = q.execution_authority_collection({'collection': ref})
        self.assertIn('execution:collection_binding_unverified', errors)

    def test_a_missing_runtime_digest_observation_is_refused(self):
        job = self.qualified[0]
        _, errors = self._run(self._mutate(job, runtime_digest=None))
        self.assertIn('execution:collection_runtime_digest_missing:' + job, errors)

    def test_a_runtime_digest_that_is_not_the_active_pointer_is_refused(self):
        job = self.qualified[0]
        _, errors = self._run(self._mutate(job, runtime_digest='1' * 64))
        self.assertIn('execution:collection_runtime_digest_differs:' + job, errors)

    def test_an_incomplete_member_list_is_refused(self):
        """Completeness: no unregistered job may be quietly left out."""
        c = copy.deepcopy(self.legal)
        c['members'].pop()
        _, errors = self._run(c)
        self.assertIn('execution:collection_members_incomplete', errors)

    def test_a_collection_that_does_not_declare_the_form_is_refused(self):
        c = copy.deepcopy(self.legal)
        c['form'] = 'SOMETHING_ELSE'
        _, errors = self._run(c)
        self.assertIn('execution:collection_form_not_declared', errors)

    def test_the_single_run_path_is_untouched_when_no_collection_is_declared(self):
        """C3 adds a path; it does not replace the old one or make it optional."""
        _, errors = q.execution_authority({})
        self.assertIn('execution:authority_missing_or_different', errors)


class C3AgainstTheRealCampaign(unittest.TestCase):
    """The collection as the campaign actually stands, with no member removed."""

    def test_the_machine_says_which_members_lack_a_review(self):
        raw = json.loads(COLLECTION.read_text(encoding='utf-8'))
        ref = write_collection(tempfile.mkdtemp(), raw)
        _, errors = q.execution_authority_collection({'collection': ref})
        missing = sorted(e.split(':')[-1] for e in errors
                         if e.startswith('execution:collection_member_review_missing:'))
        self.assertEqual(['F3/solve', 'F4/solve', 'domain-B0/mesh', 'domain-B0/solve',
                          'domain-D1/mesh', 'domain-D1/solve', 'domain-D2/mesh',
                          'domain-D2/solve'], missing,
                         'the eight jobs whose admissions name no independent review; if '
                         'this list changes the evidence changed, not the test')
        # Everything else about the collection holds, and that is worth pinning too: the
        # failure is one property on eight named jobs, not a general defect.
        self.assertFalse([e for e in errors if 'order' in e])
        self.assertFalse([e for e in errors if 'pin_' in e])
        self.assertFalse([e for e in errors if 'runtime_digest' in e])


if __name__ == '__main__':
    unittest.main()
