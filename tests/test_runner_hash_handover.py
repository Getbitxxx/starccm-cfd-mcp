# -*- coding: utf-8 -*-
"""②R12 regression: the two runner layers each verify their OWN reviewed hash.

The measured defect this pins (located in R12 while preparing the formal full-plan run):
`submit_run` pinned `worker/run_pipeline.ps1`'s sha256 into the request field `runner_sha256`;
`run_pipeline.ps1` forwarded that value verbatim into every stage request; and `run_job.ps1` compared
it with the hash of its OWN file. The two scripts have different bytes, so EVERY formal stage was
refused before STAR started - a full-plan run could not have progressed past stage 1.

The fix binds each layer to its own reviewed field (`backend.RUNNER_REVIEW_FIELDS`):
`pipeline_runner_sha256` is checked by `run_pipeline.ps1` against `$PSCommandPath`, and `runner_sha256`
is checked by `run_job.ps1` against `$PSCommandPath`. Neither comparison is removed and neither layer
is allowed to substitute a live, unreviewed digest.

Everything runs over the REAL script bytes and a REAL serialized request - no STAR, no SSH. Where a
host call would happen, `ps()` is replaced by a stub and the payload is decoded from it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import backend as backend_module                                            # noqa: E402
from backend import Backend, RUNNER_REVIEW_FIELDS, load_review_record, sha256  # noqa: E402
from tests.formal_worker_test_helpers import (current_runtime_review_pins,
                                              mock_approved_request_pins)

MANIFEST = ROOT / 'workflows' / 'static-external-flow-2502' / 'manifest.json'
STAGE = 'audit-body'
CASE_REF = 'library/' + 'a' * 64 + '.sim'
CASE_DIGEST = 'a' * 64
REVIEW_SURFACE = json.loads((ROOT / 'tests/fixtures/formal-runner-review-surface-20261019.json')
                            .read_text(encoding='utf-8'))


def read(path: Path) -> str:
    return path.read_text(encoding='utf-8', errors='replace')


def macros_of(stage: str) -> list[str]:
    return list(json.loads(MANIFEST.read_text(encoding='utf-8'))['stages'][stage]['macros'])


def review_record(tmp: Path, **overrides) -> Path:
    """A review record that matches the current bytes except for the named overrides."""
    data = {
        'schema_version': 1, 'approved': True, 'independent': True, 'reviewer': 'handover test',
        'reviewed_at': '2026-10-04',
        'pipeline_sha256': sha256(MANIFEST),
        'controller_sha256': sha256(ROOT / 'scripts' / 'backend.py'),
        'runner_sha256': sha256(ROOT / 'worker' / 'run_job.ps1'),
        'pipeline_runner_sha256': sha256(ROOT / 'worker' / 'run_pipeline.ps1'),
        'macro_hashes': {name: sha256(ROOT / 'macros' / name) for name in macros_of(STAGE)},
        'input_hashes': {'input.sim': CASE_DIGEST},
        # 2026-10-14（C3）：两条路径一律要求钉住 resource_contract.py；backend 路径一直要求，
        # 试跑路径本轮补上，因此这份夹具也要带该字段。
        'resource_contract_sha256': sha256(ROOT / 'scripts' / 'resource_contract.py'),
    }
    data.update(current_runtime_review_pins())
    data.update(overrides)
    path = tmp / 'review.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


def stub_backend() -> Backend:
    """A Backend with no connection file. `ps()` records the submission script instead of calling out."""
    backend = Backend.__new__(Backend)
    backend.remote_root = 'C:\\nowhere'
    backend.config = {'star_launcher': 'C:\\STAR\\starccm+.bat'}
    backend.calls = []
    backend.ps = lambda script, timeout=40: (backend.calls.append(script)
                                             or {'state': 'PENDING', 'run_id': 'runner-hash-test'})
    return backend


def submitted_request(backend: Backend) -> dict:
    """The exact request object submit_run serialized for the Windows pipeline."""
    script = backend.calls[-1]
    payload = re.search(r"FromBase64String\('([^']+)'\)", script).group(1)
    return json.loads(base64.b64decode(payload).decode('utf-8'))


def layers_accept(pinned_pipeline: str | None, pinned_job: str | None) -> dict:
    """Mirror of the two PowerShell comparisons, over the real files.

    `run_pipeline.ps1`:  $pipelineSelfHash -ne $req.pipeline_runner_sha256 -> 'Pipeline runner hash mismatch'
    `run_job.ps1`:       $selfHash         -ne $request.runner_sha256        -> 'Runner hash mismatch'
    """
    return {'pipeline_layer_accepts': pinned_pipeline == sha256(ROOT / 'worker' / 'run_pipeline.ps1'),
            'stage_layer_accepts': pinned_job == sha256(ROOT / 'worker' / 'run_job.ps1')}


class EachLayerHasItsOwnField(unittest.TestCase):
    def test_the_two_layers_are_different_files_with_different_hashes(self):
        pipeline = sha256(ROOT / 'worker' / 'run_pipeline.ps1')
        job = sha256(ROOT / 'worker' / 'run_job.ps1')
        self.assertNotEqual(pipeline, job,
                            'if the two runners were byte-identical the R11 defect would be invisible')
        self.assertEqual(REVIEW_SURFACE['runner_review_fields'], RUNNER_REVIEW_FIELDS)

    def test_an_unknown_runner_layer_is_refused_instead_of_silently_unchecked(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = review_record(Path(tmp))
            with self.assertRaisesRegex(ValueError, 'Unknown runner layer'):
                load_review_record(str(record), 'static-external-flow-2502', STAGE, CASE_REF,
                                   ['run_something_else.ps1'], macros_of(STAGE))

    def test_each_layer_may_only_be_pinned_by_its_own_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = review_record(Path(tmp))
            loaded = load_review_record(str(record), 'static-external-flow-2502', STAGE, CASE_REF,
                                        ['run_pipeline.ps1', 'run_job.ps1'], macros_of(STAGE))
        self.assertEqual(sha256(ROOT / 'worker' / 'run_job.ps1'), loaded['runner_sha256'])
        self.assertEqual(sha256(ROOT / 'worker' / 'run_pipeline.ps1'),
                         loaded['pipeline_runner_sha256'])


class ARecordMustPinBothLayersForSubmitRun(unittest.TestCase):
    def test_a_record_that_pins_only_the_stage_runner_cannot_authorise_the_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = review_record(Path(tmp))
            data = json.loads(record.read_text(encoding='utf-8'))
            del data['pipeline_runner_sha256']
            record.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'lacks a usable pipeline_runner_sha256'):
                load_review_record(str(record), 'static-external-flow-2502', STAGE, CASE_REF,
                                   ['run_pipeline.ps1', 'run_job.ps1'], macros_of(STAGE))
            # The deployment inventory pins both layers even for one-stage jobs.
            # A missing pipeline runner pin cannot authorize either live path.
            backend = stub_backend()
            with self.assertRaisesRegex(ValueError, 'pin current pipeline_runner_sha256'):
                backend.submit_job('audit-body', CASE_REF, 'runner-hash-job-1',
                                   workflow_id='static-external-flow-2502',
                                   review_ref=str(record))
            self.assertEqual([], backend.calls)

    def test_runtime_inventory_guard_and_both_runner_pins_are_mandatory(self):
        fields = ('runtime_digest', 'runtime_manifest_sha256', 'runtime_guard_sha256',
                  'pipeline_runner_sha256')
        for field in fields:
            for value in (None, '0' * 64):
                with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as tmp:
                    record = review_record(Path(tmp), **{field: value})
                    backend = stub_backend()
                    with self.assertRaisesRegex(ValueError, field):
                        backend.submit_job('audit-body', CASE_REF, 'runner-runtime-pin-1',
                                           workflow_id='static-external-flow-2502',
                                           review_ref=str(record))
                    self.assertEqual([], backend.calls)

    def test_valid_request_review_cannot_replace_independent_deployment_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = review_record(Path(tmp))
            backend = stub_backend()
            with patch('formal_worker.deployment_review',
                       side_effect=ValueError('No independent deployment approval')):
                with self.assertRaisesRegex(ValueError, 'independent deployment approval'):
                    backend.submit_run(CASE_REF, 'runner-deployment-gate-1',
                                       steps=[{'key': 'audit', 'workflow_id': 'static-external-flow-2502',
                                               'stage': STAGE}], review_ref=str(record))
            self.assertEqual([], backend.calls)

    def test_the_old_single_field_schema_is_refused_at_the_macos_gate(self):
        # The R11 shape: the pipeline's hash sitting in `runner_sha256`. macOS must refuse it before
        # anything is uploaded, rather than letting the stage layer refuse it on Windows.
        with tempfile.TemporaryDirectory() as tmp:
            record = review_record(Path(tmp),
                                   runner_sha256=sha256(ROOT / 'worker' / 'run_pipeline.ps1'))
            with self.assertRaisesRegex(ValueError, 'Reviewed runner_sha256 differs'):
                load_review_record(str(record), 'static-external-flow-2502', STAGE, CASE_REF,
                                   ['run_pipeline.ps1', 'run_job.ps1'], macros_of(STAGE))


class TheSerializedRequestCarriesBothReviewedHashes(unittest.TestCase):
    def submit(self, **overrides):
        with tempfile.TemporaryDirectory() as tmp:
            record = review_record(Path(tmp), **overrides)
            backend = stub_backend()
            # Installation itself is exercised by deployment-guard tests. This
            # test keeps every review hash check real and mocks only installation.
            with patch('formal_worker.request_pins', side_effect=mock_approved_request_pins):
                result = backend.submit_run(CASE_REF, 'runner-hash-run-1',
                                            steps=[{'key': 'audit_static_body',
                                                    'workflow_id': 'static-external-flow-2502',
                                                    'stage': STAGE}],
                                            review_ref=str(record))
            self.assertEqual('PENDING', result['state'])
            return submitted_request(backend)

    def test_the_request_pins_each_layer_with_its_own_hash(self):
        request = self.submit()
        self.assertEqual(sha256(ROOT / 'worker' / 'run_job.ps1'), request['runner_sha256'])
        self.assertEqual(sha256(ROOT / 'worker' / 'run_pipeline.ps1'),
                         request['pipeline_runner_sha256'])
        self.assertEqual(CASE_DIGEST, request['input_sha256'])

    def test_the_stage_layer_would_accept_the_forwarded_value(self):
        # run_pipeline.ps1 writes `runner_sha256=$req.runner_sha256` into every stage request; run_job.ps1
        # compares that value with its own file. Follow the value through the serialized request.
        request = self.submit()
        stage_request = {'runner_sha256': request['runner_sha256']}
        self.assertTrue(layers_accept(request['pipeline_runner_sha256'],
                                      stage_request['runner_sha256'])['stage_layer_accepts'])

    def test_a_wrong_layer_hash_is_refused_in_both_directions(self):
        pipeline = sha256(ROOT / 'worker' / 'run_pipeline.ps1')
        job = sha256(ROOT / 'worker' / 'run_job.ps1')
        # The correct pairing passes both layers.
        self.assertEqual({'pipeline_layer_accepts': True, 'stage_layer_accepts': True},
                         layers_accept(pipeline, job))
        # The R11 defect: the pipeline's hash handed to the stage layer.
        self.assertFalse(layers_accept(pipeline, pipeline)['stage_layer_accepts'])
        # The reverse mistake: the stage runner's hash pinned for the pipeline layer.
        self.assertFalse(layers_accept(job, job)['pipeline_layer_accepts'])
        # A stale hash on either layer.
        self.assertFalse(layers_accept('0' * 64, job)['pipeline_layer_accepts'])
        self.assertFalse(layers_accept(pipeline, '0' * 64)['stage_layer_accepts'])

    def test_the_mirror_cannot_drift_from_the_real_scripts(self):
        # The checks above mirror PowerShell in Python. These assertions keep the mirror honest: if a
        # comparison were dropped or re-pointed at the wrong field, this fails.
        pipeline = read(ROOT / 'worker' / 'run_pipeline.ps1')
        job = read(ROOT / 'worker' / 'run_job.ps1')
        # Two occurrences each: the guard computes the digest inline, and the state record computes it again.
        self.assertEqual(2, pipeline.count('Get-FileHash -LiteralPath $PSCommandPath'))
        self.assertIn('if(!$req.pipeline_runner_sha256)', pipeline)
        self.assertIn("throw 'Pipeline runner hash mismatch'", pipeline)
        self.assertIn('-ne $req.pipeline_runner_sha256', pipeline)
        # The stage layer's hash is still forwarded unchanged for run_job.ps1 to verify against ITSELF.
        self.assertIn('runner_sha256=$req.runner_sha256', pipeline)
        # Whole property name: pipeline_runner_sha256 forwarding contains the
        # old substring but cannot change the stage runner's runner_sha256 pin.
        self.assertNotRegex(pipeline, r'(?m)^\s*runner_sha256\s*=\s*\$req\.pipeline_runner_sha256\b')
        self.assertEqual(2, job.count('Get-FileHash -LiteralPath $PSCommandPath'))
        self.assertIn('if(!$request.runner_sha256)', job)
        self.assertIn("throw 'Runner hash mismatch'", job)
        self.assertIn('-ne $request.runner_sha256', job)
        self.assertNotIn('pipeline_runner_sha256', job,
                         'the stage layer must not verify a hash that belongs to the other layer')


class TheWorkerSelfChecksAreClosedAndComeFirst(unittest.TestCase):
    """The R12 review needed three rounds to close this, and each round found a real hole.

    Round 1 survivors: `throw` -> `Write-Warning`, and moving the check after the stage loop.
    Round 2 survivors: keeping the literal and the position but adding `$pipelineSelfHash=$req.…` after the
    hash, or wrapping the guard in a dead `if($false){ … }` - the comparison became vacuously true.
    Round 3 survivors: appending a dead `-and $false`, prefixing `-not $true -and`, ANDing a
    self-contradiction, and SHADOWING the cmdlet with `function Get-FileHash { … $req.… }`.

    So the assertions are now on the WHOLE STATEMENT (asssertEqual, not substring) for both the guard and
    the single state assignment, and the file may not define or alias `Get-FileHash` or assign
    `$PSCommandPath`. A semantically equivalent refactor therefore has to update this pin - which is the
    intended trade: the reviewed statement is pinned, not a family of statements that might mean something
    else. The behavioural counterpart (the real statement run on real PowerShell with a right and a wrong
    pin) is in test-evidence/operation-wiring-r12/hash-guard-behaviour-probe.json.
    """

    PIPELINE_GUARD = ("    if((Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash."
                      "ToLowerInvariant() -ne $req.pipeline_runner_sha256)"
                      "{throw 'Pipeline runner hash mismatch'}")
    PIPELINE_STATE = ("    $pipelineSelfHash=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256)."
                      "Hash.ToLowerInvariant()")
    JOB_GUARD = ("    if((Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash."
                 "ToLowerInvariant() -ne $request.runner_sha256){throw 'Runner hash mismatch'}")
    JOB_STATE = ("    $selfHash=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256)."
                 "Hash.ToLowerInvariant()")
    PIPELINE_MISSING = ("    if(!$req.pipeline_runner_sha256)"
                        "{throw 'No reviewed pipeline-runner hash pinned in this request'}")
    JOB_MISSING = ("    if(!$request.runner_sha256)"
                   "{throw 'No reviewed runner hash pinned in this request'}")

    def setUp(self):
        self.pipeline = read(ROOT / 'worker' / 'run_pipeline.ps1')
        self.job = read(ROOT / 'worker' / 'run_job.ps1')

    def lines_of(self, text: str) -> list[str]:
        return text.splitlines()

    def assert_whole_statement(self, text: str, statement: str, label: str) -> None:
        """The statement must be present EXACTLY, and no line may extend it with extra terms."""
        lines = self.lines_of(text)
        self.assertIn(statement, lines, '%s must appear as this exact line' % label)
        # Nothing may append a dead term to the guard: the exact line is the whole line.
        for line in lines:
            if 'Get-FileHash -LiteralPath $PSCommandPath' in line:
                self.assertIn(line, (self.PIPELINE_GUARD, self.PIPELINE_STATE,
                                     self.JOB_GUARD, self.JOB_STATE),
                              'unexpected Get-FileHash statement (a mutation could hide here): %r' % line)

    def test_the_pipeline_guard_is_this_exact_statement(self):
        self.assert_whole_statement(self.pipeline, self.PIPELINE_GUARD, 'the pipeline guard')
        self.assert_whole_statement(self.pipeline, self.PIPELINE_STATE, 'the pipeline state assignment')
        self.assertEqual(2, self.pipeline.count('Get-FileHash -LiteralPath $PSCommandPath'))
        self.assertIn('if(!$req.pipeline_runner_sha256)', self.pipeline)

    def test_the_stage_guard_is_this_exact_statement(self):
        self.assert_whole_statement(self.job, self.JOB_GUARD, 'the stage guard')
        self.assert_whole_statement(self.job, self.JOB_STATE, 'the stage state assignment')
        self.assertEqual(2, self.job.count('Get-FileHash -LiteralPath $PSCommandPath'))
        self.assertIn('if(!$request.runner_sha256)', self.job)

    def test_nothing_shadows_or_aliases_the_hashing_cmdlet(self):
        for label, text in (('run_pipeline.ps1', self.pipeline), ('run_job.ps1', self.job)):
            with self.subTest(file=label):
                self.assertNotIn('function Get-FileHash', text,
                                 'a local Get-FileHash would decide the comparison (V11/V12)')
                self.assertNotIn('Set-Alias', text)
                self.assertNotIn('$PSCommandPath=', text, 'the path the digest is taken from is fixed')
                self.assertNotIn('New-Alias', text)

    def test_a_second_assignment_of_the_digest_is_impossible(self):
        for label, text, variable in (('run_pipeline.ps1', self.pipeline, '$pipelineSelfHash='),
                                      ('run_job.ps1', self.job, '$selfHash=')):
            with self.subTest(file=label):
                assignments = [line for line in self.lines_of(text) if variable in line]
                self.assertEqual(1, len(assignments),
                                 '%s must assign %s exactly once' % (label, variable))
                self.assertIn('Get-FileHash -LiteralPath $PSCommandPath', assignments[0])

    def test_every_conditional_around_the_guards_is_live(self):
        # A dead or self-contradictory term appended to the guard (V8/V9/V10/V13) cannot survive the exact
        # statement pins above; this covers the same class anywhere else in the file.
        for label, text in (('run_pipeline.ps1', self.pipeline), ('run_job.ps1', self.job)):
            with self.subTest(file=label):
                for dead in ('-and $false', '-or $true', '-not $true', 'if($false', 'if($true', 'Write-Warning'):
                    self.assertNotIn(dead, text, '%s must not contain %r' % (label, dead))

    def test_nothing_ever_writes_the_pin_the_guard_reads(self):
        # Review round 4 (S1/S2/S6): inserting ONE line that re-derives the pinned field from the file it is
        # supposed to be checked against - `$req.pipeline_runner_sha256=(Get-FileHash -Path $PSCommandPath)…`
        # - makes the guard compare the file with itself. The frozen files never assign that field.
        import re as _re
        for label, text, pattern in (
                ('run_pipeline.ps1', self.pipeline, r'\$req\s*\.\s*pipeline_runner_sha256\s*='),
                ('run_job.ps1', self.job, r'\$request\s*\.\s*runner_sha256\s*=')):
            with self.subTest(file=label):
                self.assertEqual([], [line for line in self.lines_of(text) if _re.search(pattern, line)],
                                 'the pinned hash is an INPUT of this layer; nothing in the file may write it')

    def test_every_line_that_mentions_the_pinned_field_is_this_exact_list(self):
        # Review round 5 (T1-T4): a rule that matches only the literal `$req.<field>=` spelling does not
        # enforce this test's own claim - `$req.$f=`, `$req.PSObject.Properties['x'].Value=`,
        # `$req | Add-Member -Force …` and a .NET-computed digest all write the same property. So the WHOLE
        # read+write surface of the field is pinned here.
        expected_pipeline = REVIEW_SURFACE['scripts']['run_pipeline.ps1']['pinned_field_lines']
        expected_job = REVIEW_SURFACE['scripts']['run_job.ps1']['pinned_field_lines']
        for label, text, field, expected in (
                ('run_pipeline.ps1', self.pipeline, 'pipeline_runner_sha256', expected_pipeline),
                ('run_job.ps1', self.job, 'runner_sha256', expected_job)):
            with self.subTest(file=label):
                actual = [line.strip() for line in self.lines_of(text) if field in line]
                self.assertEqual([item.strip() for item in expected], actual,
                                 'the lines mentioning %s changed; any new one must be reviewed' % field)

    def test_the_workers_do_not_hash_with_dotnet_instead(self):
        # A digest computed with [Security.Cryptography.SHA256]::Create() (or by reading the bytes directly)
        # would make the Get-FileHash call-site list irrelevant (review round 5, T1).
        for label, text in (('run_pipeline.ps1', self.pipeline), ('run_job.ps1', self.job)):
            with self.subTest(file=label):
                for token in ('[Security.Cryptography', '[System.Security.Cryptography',
                              '[IO.File]::ReadAllBytes', 'SHA256]::Create', 'SHA256Managed',
                              'SHA256CryptoServiceProvider'):
                    self.assertNotIn(token, text, '%s must hash only through Get-FileHash (%s)' % (label, token))

    def test_the_complete_set_of_get_filehash_call_sites_is_pinned(self):
        # Review round 4, second fix: pin EVERY line that calls the hashing cmdlet, not only the two that
        # name $PSCommandPath. Any inserted call site (S1-S6) - a re-derivation of the pin, a shadowing
        # `function`/`filter`/`Set-Item -Path function:Get-FileHash`, or a renamed-path variant - changes this
        # list and fails here. The list is the frozen file's own; a legitimate new call site must update it,
        # which is the same deliberate-edit rule the stage allowlists use.
        expected_pipeline = REVIEW_SURFACE['scripts']['run_pipeline.ps1']['get_filehash_lines']
        expected_job = REVIEW_SURFACE['scripts']['run_job.ps1']['get_filehash_lines']
        for label, text, expected in (('run_pipeline.ps1', self.pipeline, expected_pipeline),
                                      ('run_job.ps1', self.job, expected_job)):
            with self.subTest(file=label):
                actual = [line.strip() for line in self.lines_of(text) if 'Get-FileHash' in line]
                self.assertEqual([item.strip() for item in expected], actual,
                                 'the set of hashing call sites in %s changed; a new one must be reviewed' % label)

    def test_nothing_redefines_the_hashing_cmdlet_in_any_spelling(self):
        # Review round 4 (S3/S4/S5): `function global:Get-FileHash`, `filter Get-FileHash` and
        # `Set-Item -Path function:Get-FileHash` all supply the value the guard consumes. The whole file must
        # not contain a definition/alias of that name in ANY spelling - including obfuscated ones that the
        # round-4 review noted a lexical rule cannot enumerate, which is why the host probe (evidence) is the
        # authoritative semantic gate and this is the cheap lexical net.
        import re as _re
        for label, text in (('run_pipeline.ps1', self.pipeline), ('run_job.ps1', self.job)):
            with self.subTest(file=label):
                stripped = _re.sub(r'\s+', '', text)
                for needle in ('functionglobal:Get-FileHash', 'functionGet-FileHash', 'filterGet-FileHash',
                               'Set-AliasGet-FileHash', 'New-AliasGet-FileHash',
                               'Set-Item-Pathfunction:Get-FileHash', 'Set-Itemfunction:Get-FileHash'):
                    self.assertNotIn(needle, stripped,
                                     '%s must not define or alias the hashing cmdlet (%s)' % (label, needle))

    def test_the_pipeline_self_check_runs_before_the_first_stage_and_before_any_run_job_call(self):
        guard = self.pipeline.index(self.PIPELINE_GUARD)
        loop = self.pipeline.index('foreach($item in $req.steps)')
        child = self.pipeline.index("'run_job.ps1'")
        self.assertLess(guard, loop,
                        'the pipeline must verify its own reviewed bytes before it executes a step')
        self.assertLess(guard, child,
                        'the pipeline must verify itself before it launches the stage runner')
        self.assertLess(self.pipeline.index('if(!$req.pipeline_runner_sha256)'), guard)

    def test_the_stage_self_check_runs_before_the_first_macro(self):
        guard = self.job.index(self.JOB_GUARD)
        # The anchor is the STAR LAUNCH, not the first `foreach($macro …)` (the first one copies the macro
        # files into the job directory, which is harmless and happens before the checks on purpose).
        self.assertLess(guard, self.job.index('$proc=Start-Process'),
                        'the stage runner must verify its own reviewed bytes before it launches STAR')
        self.assertLess(self.job.index('if(!$request.runner_sha256)'), guard)

    def test_a_record_without_any_pipeline_pin_is_refused(self):
        # The review's P1: deleting the scalar branch of the pipeline check left every targeted test
        # green. A record may pin the pipeline either way, but it MUST pin it.
        with tempfile.TemporaryDirectory() as tmp:
            record = review_record(Path(tmp))
            data = json.loads(record.read_text(encoding='utf-8'))
            del data['pipeline_sha256']
            record.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'lacks a usable pipeline_sha256'):
                load_review_record(str(record), 'static-external-flow-2502', STAGE, CASE_REF,
                                   ['run_pipeline.ps1', 'run_job.ps1'], macros_of(STAGE))
        with tempfile.TemporaryDirectory() as tmp:
            record = review_record(Path(tmp))
            data = json.loads(record.read_text(encoding='utf-8'))
            del data['pipeline_sha256']
            data['pipeline_hashes'] = None
            record.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'lacks a usable pipeline_sha256'):
                load_review_record(str(record), 'static-external-flow-2502', STAGE, CASE_REF,
                                   ['run_pipeline.ps1', 'run_job.ps1'], macros_of(STAGE))
        with tempfile.TemporaryDirectory() as tmp:
            record = review_record(Path(tmp))
            data = json.loads(record.read_text(encoding='utf-8'))
            del data['pipeline_sha256']
            data['pipeline_hashes'] = []
            record.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'unusable pipeline_hashes'):
                load_review_record(str(record), 'static-external-flow-2502', STAGE, CASE_REF,
                                   ['run_pipeline.ps1', 'run_job.ps1'], macros_of(STAGE))


class APLanSpanningTwoWorkflowsCanBeAuthorised(unittest.TestCase):
    """R12 measured this with an otherwise perfect record: one scalar `pipeline_sha256` cannot equal two
    different workflow manifests, so a plan that spans two workflows (the scenario-1 chain needs its domain
    stage from external-domain-2502) could never be authorised at all."""

    def two_workflow_record(self, tmp: Path, **overrides) -> Path:
        first = json.loads(MANIFEST.read_text(encoding='utf-8'))
        second_manifest = ROOT / 'workflows' / 'external-domain-2502' / 'manifest.json'
        second = json.loads(second_manifest.read_text(encoding='utf-8'))
        macros = set(first['stages'][STAGE]['macros']) | set(second['stages']['build']['macros'])
        data = {'schema_version': 1, 'approved': True, 'independent': True, 'reviewer': 'multi-workflow',
                'reviewed_at': '2026-10-04',
                'pipeline_hashes': {'static-external-flow-2502': sha256(MANIFEST),
                                    'external-domain-2502': sha256(second_manifest)},
                'controller_sha256': sha256(ROOT / 'scripts' / 'backend.py'),
                'runner_sha256': sha256(ROOT / 'worker' / 'run_job.ps1'),
                'pipeline_runner_sha256': sha256(ROOT / 'worker' / 'run_pipeline.ps1'),
                'macro_hashes': {name: sha256(ROOT / 'macros' / name) for name in sorted(macros)},
                'input_hashes': {'input.sim': CASE_DIGEST}}
        data.update(current_runtime_review_pins())
        data.update(overrides)
        path = tmp / 'multi-review.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def test_each_workflow_of_the_plan_is_checked_against_its_own_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = self.two_workflow_record(Path(tmp))
            for workflow_id, stage in (('static-external-flow-2502', STAGE),
                                       ('external-domain-2502', 'build')):
                with self.subTest(workflow=workflow_id):
                    macros = list(json.loads((ROOT / 'workflows' / workflow_id /
                                              'manifest.json').read_text(encoding='utf-8')
                                             )['stages'][stage]['macros'])
                    load_review_record(str(record), workflow_id, stage, CASE_REF,
                                       ['run_pipeline.ps1', 'run_job.ps1'], macros)

    def test_a_workflow_the_record_does_not_pin_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = self.two_workflow_record(
                Path(tmp), pipeline_hashes={'static-external-flow-2502': sha256(MANIFEST)})
            with self.assertRaisesRegex(ValueError, r'does not pin pipeline_hashes\[external-domain-2502\]'):
                load_review_record(str(record), 'external-domain-2502', 'build', CASE_REF,
                                   ['run_pipeline.ps1', 'run_job.ps1'],
                                   list(json.loads((ROOT / 'workflows' / 'external-domain-2502' /
                                                    'manifest.json').read_text(encoding='utf-8')
                                                   )['stages']['build']['macros']))

    def test_a_stale_entry_for_one_workflow_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = self.two_workflow_record(
                Path(tmp), pipeline_hashes={'static-external-flow-2502': sha256(MANIFEST),
                                            'external-domain-2502': '0' * 64})
            with self.assertRaisesRegex(ValueError,
                                        r'Reviewed pipeline_hashes\[external-domain-2502\] differs'):
                load_review_record(str(record), 'external-domain-2502', 'build', CASE_REF,
                                   ['run_pipeline.ps1', 'run_job.ps1'],
                                   list(json.loads((ROOT / 'workflows' / 'external-domain-2502' /
                                                    'manifest.json').read_text(encoding='utf-8')
                                                   )['stages']['build']['macros']))

    def test_the_single_workflow_scalar_field_still_works(self):
        # Nothing already signed breaks: with no pipeline_hashes key the scalar check runs as before.
        with tempfile.TemporaryDirectory() as tmp:
            record = review_record(Path(tmp))
            loaded = load_review_record(str(record), 'static-external-flow-2502', STAGE, CASE_REF,
                                        ['run_pipeline.ps1', 'run_job.ps1'], macros_of(STAGE))
            self.assertEqual(CASE_DIGEST, loaded['input_sha256'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
