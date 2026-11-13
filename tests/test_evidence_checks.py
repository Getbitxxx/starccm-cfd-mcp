# -*- coding: utf-8 -*-
"""Tests for the two evidence gates that stand in for prose claims.

AGENTS.md section 2 says a rule written as prose has no binding force. Two claims made in this round
were prose until these scripts existed: "the deliberate failure left its diagnosis on disk" and "the
second macro re-measured the geometry and agreed". Each test below would fail if the corresponding
claim stopped being checkable.
"""
import json
import math
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def float32_ulp(value):
    magnitude = abs(float(value))
    bits = struct.unpack('<I', struct.pack('<f', magnitude))[0]
    lower = struct.unpack('<f', struct.pack('<I', bits))[0]
    upper = struct.unpack('<f', struct.pack('<I', bits + 1))[0]
    return abs(upper - lower)


def run(script, *args):
    return subprocess.run([PY, str(ROOT / 'scripts' / script), *args],
                          capture_output=True, text=True)


def write(path, payload):
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path


class NegativeControlEvidence(unittest.TestCase):
    """The failure record must exist and be well formed, or the control has proved nothing."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.evidence = self.tmp / 'evidence'
        self.trial = self.evidence / 'trial-12345678'
        self.trial.mkdir(parents=True)
        write(self.evidence / 'trial-12345678-result.json',
              {'state': 'FAILED'})
        write(self.trial / 'case-probe.json', {'state': 'FAILED', 'error': 'STAR exit code 1'})
        self.record = self.trial / 'static_body_geometry.json'

    def invoke(self):
        return run('verify_negative_evidence.py', '--evidence', str(self.evidence),
                   '--trial-id', 'trial-12345678')

    def good_record(self, **overrides):
        payload = {'schema_version': 1, 'status': 'error', 'usable': False,
                   'working_directory': r'C:\CodexCFDRestricted\trial-12345678',
                   'error_class': 'java.lang.IllegalArgumentException',
                   'error': 'Unsupported body_shape for this release: cube',
                   'output_sim_exists': False, 'output_sim_sha256': None,
                   'complete_marker': None}
        payload.update(overrides)
        return payload

    def test_a_well_formed_diagnosis_passes(self):
        write(self.record, self.good_record())
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['checks_passed'])

    def test_a_missing_diagnosis_fails_the_control(self):
        result = self.invoke()
        self.assertEqual(1, result.returncode)
        self.assertIn('LEFT NO DIAGNOSIS ON DISK', result.stdout)

    def test_a_diagnosis_without_an_error_text_fails(self):
        write(self.record, self.good_record(error='   '))
        self.assertEqual(1, self.invoke().returncode)

    def test_a_diagnosis_that_claims_success_fails(self):
        write(self.record, self.good_record(status='ok', usable=True,
                                            complete_marker='STATIC_BODY_GEOMETRY_BUILT'))
        self.assertEqual(1, self.invoke().returncode)

    def test_an_output_sim_left_behind_fails(self):
        write(self.record, self.good_record(output_sim_exists=True))
        self.assertEqual(1, self.invoke().returncode)

    def test_a_diagnosis_written_outside_the_job_directory_fails(self):
        write(self.record, self.good_record(working_directory=r'C:\Users\chen\somewhere-else'))
        self.assertEqual(1, self.invoke().returncode)

    def test_a_run_that_did_not_fail_fails_the_control(self):
        write(self.record, self.good_record())
        write(self.evidence / 'trial-12345678-result.json', {'state': 'PIPELINE_COMPLETE'})
        self.assertEqual(1, self.invoke().returncode)


class GeometryEvidenceAgreement(unittest.TestCase):
    """The agreement claim must fail loudly when the two records disagree."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.builder = write(self.tmp / 'builder.json',
                             {'status': 'ok', 'is_closed': True, 'is_manifold': True,
                              'triangle_count': 9408.0, 'model_length_unit': 'm',
                              'measured_bbox_si_m': [-0.05, -0.049, -0.05, 0.05, 0.049, 0.05]})
        self.audit = write(self.tmp / 'audit.json', self.audit_payload())

    @staticmethod
    def audit_payload(**part_overrides):
        part = {'qualifiedName': 'BODY', 'isClosed': True, 'isManifold': True,
                'triangleCount': 9408,
                'boundingBoxRaw': [-0.05, -0.049, -0.05, 0.05, 0.049, 0.05]}
        part.update(part_overrides)
        return {'status': 'ok', 'geometryParts': [part]}

    def invoke(self):
        return run('compare_geometry_evidence.py', '--builder', str(self.builder),
                   '--audit', str(self.audit), '--part', 'BODY')

    def test_agreeing_records_pass(self):
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['agreement_established'])

    def test_a_different_triangle_count_fails(self):
        write(self.audit, self.audit_payload(triangleCount=9407))
        self.assertEqual(1, self.invoke().returncode)

    def test_a_different_bounding_box_fails(self):
        write(self.audit, self.audit_payload(
            boundingBoxRaw=[-0.0500001, -0.049, -0.05, 0.05, 0.049, 0.05]))
        self.assertEqual(1, self.invoke().returncode)

    def test_a_renamed_part_fails(self):
        write(self.audit, self.audit_payload(qualifiedName='Geometry 1/BODY', name='Geometry 1/BODY'))
        self.assertEqual(1, self.invoke().returncode)

    def test_a_missing_bounding_box_fails(self):
        write(self.audit, self.audit_payload(boundingBoxRaw=None))
        self.assertEqual(1, self.invoke().returncode)

    def test_a_failed_audit_fails(self):
        write(self.audit, {'status': 'error', 'geometryParts': []})
        self.assertEqual(1, self.invoke().returncode)

    def test_the_scope_is_stated(self):
        summary = json.loads(self.invoke().stdout)
        self.assertIn('measured_enclosed_volume_si_m3', summary['not_compared_here'])




class GeometryEvidenceValidation(unittest.TestCase):
    """The NaN fail-open: a `> tolerance` comparison is false for NaN, so it must be caught here."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.record = self.tmp / 'static_body_geometry.json'

    def good(self, **overrides):
        volume = 0.0005227026092832345
        analytic = 4.0 / 3.0 * math.pi * 0.05 ** 3
        ulp = float32_ulp(0.05)
        radius_tolerance = max(1e-6 * 0.05, 8.0 * ulp)
        payload = {'schema_version': 1, 'status': 'ok', 'usable': True,
                   'complete_marker': 'STATIC_BODY_GEOMETRY_BUILT',
                   'model_length_unit': 'm', 'body_radius_m': 0.05,
                   'measured_vertex_radius_relative_error': 4.2e-08,
                   'measured_vertex_radius_abs_error_m': 4.2e-09,
                   'measured_vertex_radius_tolerance_m': radius_tolerance,
                   'measured_vertex_radius_error_ratio': 4.2e-09 / radius_tolerance,
                   'measured_centre_offset_m': 1e-12,
                   'measured_centre_tolerance_m': max(1e-12, 8.0 * ulp),
                   'measured_centre_offset_ratio': 1e-12 / max(1e-12, 8.0 * ulp),
                   'measured_storage_ulp_m': ulp,
                   'measured_max_abs_coordinate_m': 0.05,
                   'measured_bbox_half_extent_relative_deviation': 0.0,
                   'measured_volume_relative_deviation': abs(volume - analytic) / analytic,
                   'measured_enclosed_volume_si_m3': volume,
                   'analytic_volume_si_m3': analytic,
                   'measured_min_vertex_radius_si_m': 0.049999998,
                   'measured_max_vertex_radius_si_m': 0.050000002,
                   'measured_bbox_si_m': [-0.05, -0.05, -0.05, 0.05, 0.05, 0.05],
                   'measured_bbox_half_extent_si_m': [0.05, 0.05, 0.05],
                   'triangle_count': 9408, 'part_count': 1, 'regions': 0, 'iteration': 0}
        payload.update(overrides)
        return payload

    def write(self, payload, raw=None):
        if raw is not None:
            self.record.write_text(raw, encoding='utf-8')
        else:
            self.record.write_text(json.dumps(payload), encoding='utf-8')
        return self.record

    def invoke(self):
        return run('validate_geometry_evidence.py', '--evidence', str(self.record))

    def test_a_good_record_validates(self):
        self.write(self.good())
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_a_nan_gate_value_is_refused(self):
        # The exact defect: NaN must not pass a "<= tolerance" gate.
        self.write(self.good(measured_volume_relative_deviation=float('nan')))
        result = self.invoke()
        self.assertEqual(1, result.returncode)
        self.assertIn('not finite', result.stdout)

    def test_a_bare_nan_token_is_refused(self):
        raw = json.dumps(self.good()).replace('0.0017', 'NaN')
        self.write(None, raw=raw)
        self.assertEqual(1, self.invoke().returncode)

    def test_an_infinite_value_is_refused(self):
        self.write(self.good(measured_centre_offset_ratio=float('inf')))
        self.assertEqual(1, self.invoke().returncode)

    def test_a_nan_hidden_in_the_bounding_box_is_refused(self):
        box = [-0.05, float('nan'), -0.05, 0.05, 0.05, 0.05]
        self.write(self.good(measured_bbox_si_m=box))
        self.assertEqual(1, self.invoke().returncode)

    def test_a_record_that_claims_failure_is_refused(self):
        self.write(self.good(status='error', usable=False))
        self.assertEqual(1, self.invoke().returncode)

    def test_an_over_tolerance_gate_is_refused(self):
        self.write(self.good(measured_vertex_radius_error_ratio=2.0))
        self.assertEqual(1, self.invoke().returncode)

    def test_a_wrong_analytic_volume_is_refused(self):
        self.write(self.good(analytic_volume_si_m3=1.0))
        self.assertEqual(1, self.invoke().returncode)

    def test_an_inconsistent_half_extent_is_refused(self):
        self.write(self.good(measured_bbox_half_extent_si_m=[0.04, 0.05, 0.05]))
        self.assertEqual(1, self.invoke().returncode)


class NonFiniteEncodings(unittest.TestCase):
    """The encodings a non-finite value can actually arrive in, each of which must be refused.

    An independent review found that the first version of this validator accepted a quoted "NaN"
    because the token regex deliberately ignores quoted text and the value walk skips strings - and
    that is exactly the form the building macro's own writer produced. The removal of the recursive
    isfinite walk must also break a test; the 1e999 case below is the one that pins it, because it
    carries no NaN/Infinity token at all.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.record = self.tmp / 'static_body_geometry.json'
        self.base = GeometryEvidenceValidation.good(self)

    def raw(self, text):
        self.record.write_text(text, encoding='utf-8')
        return run('validate_geometry_evidence.py', '--evidence', str(self.record))

    def test_a_huge_literal_in_a_gate_field_is_refused(self):
        text = json.dumps(self.base).replace(
            '"measured_volume_relative_deviation": %r' % self.base['measured_volume_relative_deviation'],
            '"measured_volume_relative_deviation": 1e999')
        result = self.raw(text)
        self.assertEqual(1, result.returncode)
        self.assertIn('not finite', result.stdout)

    def test_a_huge_literal_in_a_non_gate_field_is_refused(self):
        # This is the test that pins the recursive isfinite walk: the field below is neither a gate
        # nor re-derived, and the raw literal carries no NaN/Infinity token, so if the walk is removed
        # the record is accepted and this test goes red.
        text = json.dumps(self.base).replace(
            '"measured_storage_ulp_m": %r' % self.base['measured_storage_ulp_m'],
            '"body_origin_spec_readback_m": [1e999, 0.0, 1.5]')
        result = self.raw(text)
        self.assertEqual(1, result.returncode)
        self.assertIn('not finite', result.stdout)

    def test_omitting_the_storage_fields_is_refused(self):
        payload = {key: value for key, value in self.base.items()
                   if key not in ('measured_storage_ulp_m', 'measured_max_abs_coordinate_m')}
        payload['measured_vertex_radius_tolerance_m'] = 1.0
        result = self.raw(json.dumps(payload))
        self.assertEqual(1, result.returncode)
        self.assertIn('is required and absent', result.stdout)

    def test_a_quoted_nan_is_refused(self):
        payload = dict(self.base, measured_vertex_radius_abs_error_m='NaN')
        self.record.write_text(json.dumps(payload), encoding='utf-8')
        result = run('validate_geometry_evidence.py', '--evidence', str(self.record))
        self.assertEqual(1, result.returncode)
        self.assertIn('non-finite', result.stdout)

    def test_a_quoted_infinity_in_a_non_gate_field_is_refused(self):
        payload = dict(self.base, measured_storage_ulp_m='Infinity')
        self.record.write_text(json.dumps(payload), encoding='utf-8')
        self.assertEqual(1, run('validate_geometry_evidence.py', '--evidence',
                                str(self.record)).returncode)

    def test_a_string_gate_value_is_refused(self):
        payload = dict(self.base, measured_vertex_radius_error_ratio='0.084')
        self.record.write_text(json.dumps(payload), encoding='utf-8')
        self.assertEqual(1, run('validate_geometry_evidence.py', '--evidence',
                                str(self.record)).returncode)

    def test_a_widened_tolerance_is_caught_by_re_derivation(self):
        # A record cannot make a bad radius look good by inflating its own tolerance.
        payload = dict(self.base, measured_vertex_radius_tolerance_m=1.0,
                       measured_vertex_radius_error_ratio=4.2e-09)
        self.record.write_text(json.dumps(payload), encoding='utf-8')
        result = run('validate_geometry_evidence.py', '--evidence', str(self.record))
        self.assertEqual(1, result.returncode)
        self.assertIn('measured_vertex_radius_tolerance_m', result.stdout)
        self.assertIn('implies', result.stdout)


if __name__ == '__main__':
    unittest.main()


class NegativeControlStateNames(unittest.TestCase):
    """A declared numeric gate turns a negative into GATE_FAILED; that is still a negative."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.evidence = self.tmp / 'evidence'
        self.trial = self.evidence / 'trial-12345678'
        self.trial.mkdir(parents=True)
        write(self.trial / 'case-probe.json', {'state': 'FAILED', 'error': 'Owned STAR exit code 1'})
        write(self.trial / 'static_body_audit.json',
              {'status': 'error', 'usable': False,
               'working_directory': r'C:\CodexCFDRestricted\trial-12345678',
               'error_class': 'java.lang.IllegalStateException',
               'error': 'centre offset 1.5 m against a storage tolerance of 9.5e-07 m',
               'output_sim_exists': False, 'complete_marker': None})

    def invoke(self, state, runner_state=None):
        payload = {'state': state}
        if runner_state is not None:
            payload['runner_state'] = runner_state
        write(self.evidence / 'trial-12345678-result.json', payload)
        return run('verify_negative_evidence.py', '--evidence', str(self.evidence),
                   '--trial-id', 'trial-12345678', '--record', 'static_body_audit.json')

    def test_a_gate_failed_negative_passes(self):
        result = self.invoke('GATE_FAILED', 'FAILED')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['checks_passed'])

    def test_gate_failed_without_a_failed_runner_is_refused(self):
        self.assertEqual(1, self.invoke('GATE_FAILED', 'SUCCEEDED').returncode)
        self.assertEqual(1, self.invoke('GATE_FAILED', None).returncode)

    def test_a_successful_run_is_still_refused(self):
        self.assertEqual(1, self.invoke('AIR3B_PIPELINE_COMPLETE').returncode)

    def test_the_named_record_is_read(self):
        # Without --record the default name does not exist here, which must be a failure.
        write(self.evidence / 'trial-12345678-result.json', {'state': 'FAILED'})
        result = run('verify_negative_evidence.py', '--evidence', str(self.evidence),
                     '--trial-id', 'trial-12345678')
        self.assertEqual(1, result.returncode)
        self.assertIn('LEFT NO DIAGNOSIS ON DISK', result.stdout)


class AuditMacroFormulaPinnedToASecondImplementation(unittest.TestCase):
    """Pin the AUDIT macro's tolerance arithmetic against the Mac-side independent implementation.

    An earlier review warned that testing a macro against a copy of its own formula is a check that
    binds nothing, and that adding a third macro to audit the auditor would regress forever. The
    alternative used here is the second, already-existing implementation in
    scripts/local_stl_remeasure.py: it is a different language, a different traversal and a different
    rounding order, so the two cannot drift together by accident. This test reads the record the
    AUDIT macro actually produced and requires the independent reduction of the same STL to reproduce
    every tolerance and ratio in it.
    """

    RECORD = ROOT / 'test-evidence/static-external-flow/audit-z1p5' / \
        's1-static-sphere-audit-z1p5-20261003a' / 'static_body_audit.json'
    STL = ROOT / 'test-evidence/static-external-flow/audit-z1p5' / \
        's1-static-sphere-audit-z1p5-20261003a' / 'static_body_tessellation.stl'

    SHARED = ('measured_max_abs_coordinate_m', 'measured_storage_ulp_m',
              'measured_vertex_radius_tolerance_m', 'measured_centre_tolerance_m',
              'measured_vertex_radius_abs_error_m', 'measured_vertex_radius_error_ratio',
              'measured_centre_offset_m', 'measured_centre_offset_ratio',
              'measured_min_vertex_radius_si_m', 'measured_max_vertex_radius_si_m',
              'measured_bbox_si_m', 'measured_bbox_half_extent_relative_deviation',
              'measured_enclosed_volume_si_m3', 'measured_volume_relative_deviation',
              'analytic_volume_si_m3')

    def test_available(self):
        self.assertTrue(self.RECORD.is_file(), 'the audit macro has not produced a record yet')
        self.assertTrue(self.STL.is_file(), 'the audit STL has not been fetched yet')

    def test_the_independent_reduction_reproduces_every_recorded_gate_quantity(self):
        self.test_available()
        record = json.loads(self.RECORD.read_text(encoding='utf-8-sig'))
        result = run('local_stl_remeasure.py', str(self.STL), '--radius', str(record['body_radius_m']),
                     '--centre', *[str(v) for v in record['body_origin_m']])
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        independent = json.loads(result.stdout)
        for key in self.SHARED:
            with self.subTest(key=key):
                self.assertIn(key, independent, 'the second implementation does not derive ' + key)
                mine = record[key]
                theirs = independent[key]
                if isinstance(mine, list):
                    self.assertEqual(len(mine), len(theirs))
                    for left, right in zip(mine, theirs):
                        self.assertAlmostEqual(left, right, delta=1e-9 * max(1.0, abs(left)))
                else:
                    self.assertAlmostEqual(mine, theirs, delta=1e-9 * max(1.0, abs(mine)))

    def test_the_pin_would_fail_if_the_macro_rule_changed(self):
        # The pin is only worth having if a changed rule breaks it: recompute one tolerance under a
        # different multiplier and require disagreement with the recorded value.
        self.test_available()
        record = json.loads(self.RECORD.read_text(encoding='utf-8-sig'))
        self.assertNotAlmostEqual(record['measured_vertex_radius_tolerance_m'],
                                  4.0 * record['measured_storage_ulp_m'], delta=1e-12)
        self.assertAlmostEqual(record['measured_vertex_radius_tolerance_m'],
                               8.0 * record['measured_storage_ulp_m'], delta=1e-12)


class PreflightEmptinessIsDistinguishable(unittest.TestCase):
    """A preflight audit may only call a section empty when the audit shows it read that section.

    The user asked whether two Siemens sessions really have no geometry parts or whether the reader
    failed, and named the risk: an audit that cannot tell "none found" from "could not read" is a
    false-ok hole of the same family as the NaN fail-open. This pins the criterion.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.audit = self.tmp / 'automation_preflight.json'

    def clean(self, **overrides):
        payload = {'status': 'ok', 'warnings': [], 'warningsTruncated': False,
                   'regions': [], 'physicsContinua': [], 'geometryParts': [],
                   'derivedParts': [], 'interfaces': [], 'reports': [],
                   'counts': {'regions': 0, 'physicsContinua': 0, 'geometryParts': 0,
                              'derivedParts': 0, 'interfaces': 0, 'reports': 0, 'warnings': 0}}
        payload.update(overrides)
        return payload

    def invoke(self, payload):
        self.audit.write_text(json.dumps(payload), encoding='utf-8')
        return run('validate_preflight_audit.py', '--audit', str(self.audit))

    def test_a_clean_empty_audit_is_accepted_as_genuinely_empty(self):
        result = self.invoke(self.clean())
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual('GENUINELY_EMPTY', summary['sections']['geometryParts']['verdict'])

    def test_an_empty_section_named_by_a_warning_is_refused(self):
        payload = self.clean(status='partial', warnings=[
            {'code': 'SECTION_UNREADABLE', 'area': 'geometryParts', 'message': 'getObjects() failed'}])
        result = self.invoke(payload)
        self.assertEqual(1, result.returncode)
        self.assertIn('ambiguous', result.stdout)

    def test_an_empty_section_while_partial_is_refused(self):
        payload = self.clean(status='partial', warnings=[
            {'code': 'FIELD_UNREADABLE', 'area': 'regions[0].boundaries', 'message': 'x'}])
        result = self.invoke(payload)
        self.assertEqual(1, result.returncode)
        self.assertIn('not attributable', result.stdout)

    def test_a_null_section_is_refused(self):
        self.assertEqual(1, self.invoke(self.clean(geometryParts=None)).returncode)

    def test_a_missing_section_is_refused(self):
        payload = self.clean()
        del payload['geometryParts']
        self.assertEqual(1, self.invoke(payload).returncode)

    def test_counts_must_match_the_sections(self):
        payload = self.clean(geometryParts=[{'name': 'BODY'}])
        self.assertEqual(1, self.invoke(payload).returncode,
                         'counts says 0 parts while the section holds 1')

    def test_truncated_warnings_are_refused(self):
        self.assertEqual(1, self.invoke(self.clean(warningsTruncated=True)).returncode)

    def test_the_real_empty_and_populated_audits_validate(self):
        for name, expected in (('dualwing', 0), ('oneram6', 0), ('sphere', 2)):
            with self.subTest(case=name):
                path = ROOT / 'test-evidence' / 'static-external-flow' / ('preflight-%s-audit.json' % name)
                result = run('validate_preflight_audit.py', '--audit', str(path),
                             '--expect-parts', str(expected))
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)


class AuditMacroFailurePathIsReachable(unittest.TestCase):
    """A failure before the simulation exists must still leave a diagnosis.

    There is no STAR host in this repository's test environment, so a runtime negative for "no active
    simulation" cannot be constructed; this is a structural check on the macro source and is labelled
    as one. It asserts that execute()'s try encloses getActiveSimulation() and that the failure path
    writes evidence, so the ordering cannot silently revert.
    """

    @staticmethod
    def code_only(text):
        # A comment must never satisfy a structural check: the first version of this test was thrown
        # off by the comment that explains the ordering.
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
        return re.sub(r'//[^\n]*', '', text)

    def test_the_try_encloses_get_active_simulation(self):
        raw = (ROOT / 'macros' / 'AuditStaticBodyGeometry.java').read_text(encoding='utf-8')
        execute = self.code_only(
            raw[raw.index('public void execute()'):raw.index('    private void run(')])
        self.assertIn('try {', execute, 'execute() has no try block')
        try_at = execute.index('try {')
        get_at = execute.index('getActiveSimulation()')
        self.assertLess(try_at, get_at,
                        'getActiveSimulation() is called before the try, so a missing simulation '
                        'would leave no diagnosis')
        self.assertIn('writeEvidence', execute, 'the failure path must write evidence')
        self.assertIn('No active simulation', execute)

    def test_the_evidence_path_is_resolvable_without_a_simulation(self):
        text = (ROOT / 'macros' / 'AuditStaticBodyGeometry.java').read_text(encoding='utf-8')
        execute = text[text.index('public void execute()'):]
        execute = execute[:execute.index('\n    }')]
        self.assertLess(execute.index('new File(".")'), execute.index('try {'),
                        'the fallback evidence directory must be chosen before the try')
