"""P2.3 ad-hoc geometry audit: pin the two-step record-location/acceptance contract.

Why this file exists (O9): the independent review rejected the first staging because
`ImportPropellerStep` wrote its records into the job root while the runner looked for them in a per-step
directory - a defect that would only show up AFTER a successful STAR import (wasted slot + fake
failure). It also rejected "acceptance by status string". This test pins the single directory
convention, the derived-spec rule, the field gates and the strict review binding, so those defects
cannot return without a failing test.
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_prop_geom_audit as audit  # noqa: E402

CASE = ROOT / 'cases' / 'uav-prop-apc10x7e'
RUNNER = ROOT / 'scripts/security/run_prop_geom_audit_adhoc.ps1'
CONTROLLER = ROOT / 'scripts/run_prop_geom_audit.py'
IMPORT_MACRO = ROOT / 'macros/ImportPropellerStep.java'
BODY = 'APC10X7E_ROTOR'
AUDITED_PASS = (ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/geometry-audit-20261028a'
                 / 'AUDITED-RUN-2-PASS')


class AuditContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # DETERMINISTIC-READ-ONLY (2026-10-31): this used to call audit.config() against the module STAGING
        # directory, which OVERWROTE the committed, independently reviewed prop staging on every test run
        # (measured: staging case_spec drifted from its REVIEW-INPUTS pin 033714ff... to cbc354e2...).
        # The contract is now generated into a temporary directory: same deterministic files, no mutation of
        # reviewed evidence.  Reviewing/pinning remains the job of `prepare`, not of a test.
        import tempfile
        cls._tmp = tempfile.TemporaryDirectory(dir=ROOT / 'work')  # inside the repo: prepare() computes staging.relative_to(ROOT)
        cls.staging = Path(cls._tmp.name) / 'staging'
        cls._saved_staging = audit.STAGING
        audit.STAGING = cls.staging
        try:
            import types
            audit.prepare(types.SimpleNamespace(job_dir=audit.DEFAULT_JOB_DIR))
        finally:
            audit.STAGING = cls._saved_staging
        cls.config = json.loads((cls.staging / 'config.json').read_text(encoding='utf-8'))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_job_root_convention(self):
        """PROP_JOB_DIR is always the job root; macros write records there; step 2 consumes step 1's root sim."""
        for step in self.config['steps']:
            self.assertEqual('{job_dir}', step['env']['PROP_JOB_DIR'])
        self.assertNotIn('{step_dir}', json.dumps(self.config))
        self.assertEqual('APC10X7E_ROTOR_import.sim', self.config['steps'][0]['output'])
        self.assertEqual(self.config['steps'][1]['input'], self.config['steps'][0]['output'])
        self.assertEqual(['APC10X7E_ROTOR_import.json'], self.config['steps'][0]['records'])
        self.assertEqual(['static_body_audit.json'], self.config['steps'][1]['records'])

    def test_derived_spec_is_exactly_one_insertion(self):
        source = (CASE / 'case_spec.json').read_text(encoding='utf-8')
        derived = self.staging.joinpath('case_spec.json').read_text(encoding='utf-8')
        insertion = '"body_part_name": "%s",\n  ' % BODY
        self.assertIn(insertion, derived)
        restored = derived.replace(insertion, '', 1)
        self.assertEqual(hashlib.sha256(restored.encode()).hexdigest(),
                         hashlib.sha256(source.encode()).hexdigest(),
                         'derived audit spec must be the signed CaseSpec plus exactly the one key')
        spec = json.loads(derived)
        self.assertEqual(BODY, spec['body_part_name'])
        self.assertEqual('arbitrary', spec['geometry']['body_shape'])
        self.assertEqual(1, len(spec['geometry']['body_parts']))
        self.assertIn('expected_bounds_m', spec['geometry'])
        self.assertIn('expected_volume_m3', spec['geometry'])
        self.assertEqual('m', spec['model_length_unit'])

    def test_runner_gates_the_acceptance_fields(self):
        text = RUNNER.read_text(encoding='utf-8')
        for required in ('bounds_max_abs_error_m', 'volume_relative_deviation', 'preferred_unit_readback',
                         'is_closed', 'is_manifold', 'Declared record missing in job root',
                         'chain of custody', 'import_sim_sha256', 'acceptance'):
            self.assertIn(required, text, required)
        # records are resolved in the job root, never in the per-step directory
        self.assertIn('Join-Path $job $recordName', text)
        # no wall-clock stop; only the no-log-progress watchdog
        self.assertIn('no log progress for 900 seconds', text)
        self.assertIn('wall_clock_stop_enabled=$false', text)

    def test_controller_binds_review_and_downloads_acceptance_from_root(self):
        text = CONTROLLER.read_text(encoding='utf-8')
        self.assertIn('review.hashes keys differ from REVIEW-INPUTS', text)
        self.assertIn('static_body_tessellation.stl', text)
        self.assertIn("b.scp(EVIDENCE / name, job + '\\\\' + name, download=True)", text)
        self.assertIn('ConvertTo-Json', text)
        self.assertIn('required acceptance artefacts missing from the job root', text)
        self.assertEqual(BODY, self.config['gates']['part'])
        self.assertEqual('m', self.config['gates']['model_length_unit'])
        spec = json.loads((CASE / 'case_spec.json').read_text(encoding='utf-8'))
        geom = spec['geometry']
        # intended values, not just internal equality: a regression of BOTH sides to 0.002 must fail here
        self.assertEqual(0.0002, float(geom['expected_bounds_tolerance_m']))
        self.assertEqual(0.01, float(geom['expected_volume_relative_tolerance']),
                         'volume gate is the 1% measurement-basis re-base (RUN-1: 0.526% coarse-tessellation bias)')
        basis = geom.get('expected_volume_tolerance_basis', '')
        self.assertIn('1% tolerance', basis)
        self.assertIn('coarse', basis.lower())
        self.assertTrue('REBASED' in geom.get('geometry_audit_status', '') or 'PASSED' in geom.get('geometry_audit_status', ''))
        self.assertIn('1%', geom.get('geometry_audit_todo', ''))
        self.assertEqual(float(geom['expected_bounds_tolerance_m']), self.config['gates']['bounds_tolerance_m'])
        self.assertEqual(float(geom['expected_volume_relative_tolerance']), self.config['gates']['volume_relative_tolerance'])
        self.assertEqual(0.01, self.config['gates']['volume_relative_tolerance'])
        contract = json.loads((self.staging / 'REVIEW-INPUTS.json').read_text(encoding='utf-8'))['audit_contract']
        self.assertIn('0.01', contract['step2'])
        self.assertNotIn('0.2%)', contract['step2'])

    def test_gate_fields_exist_in_the_actual_records(self):
        """O9 for the review-2h defect: a gate field that the record does not carry throws AFTER both
        STAR processes. Cross-check every gated field against the macro source and the executed E387
        audit fixture (the audit macro is byte-identical to the executed one)."""
        runner = RUNNER.read_text(encoding='utf-8')
        audit_src = (ROOT / 'macros/AuditStaticBodyGeometry.java').read_text(encoding='utf-8')
        import_src = IMPORT_MACRO.read_text(encoding='utf-8')
        fixture = json.loads((ROOT / 'campaigns/uav-2026/evidence/uav-airfoil-e387/role-readback/'
                              'e387-smooth-20261024w/audit/static_body_audit.json').read_text(encoding='utf-8'))
        audit_fields = ('usable', 'is_closed', 'is_manifold', 'audited_solid_count',
                        'model_length_unit_readback', 'bounds_max_abs_error_m',
                        'volume_relative_deviation', 'tessellation_stl_sha256',
                        'measured_bbox_si_m', 'measured_enclosed_volume_si_m3')
        for field in audit_fields:
            if field == 'usable':
                # written by writeEvidence(), not via context.put
                self.assertIn('\\"usable\\":', audit_src, 'usable is not written by the audit macro')
            else:
                self.assertIn('context.put("%s"' % field, audit_src, field + ' is not written by the audit macro')
            self.assertIn(field, runner, field + ' is not consumed by the runner')
            self.assertIn(field, fixture, field + ' is not in the executed E387 audit record')
        import_fields = ('part', 'solid_count', 'is_closed', 'is_manifold',
                         'preferred_unit_readback', 'import_sim_sha256')
        for field in import_fields:
            self.assertIn('\\"%s\\":' % field, import_src, field + ' is not written by the import macro')
            self.assertIn(field, runner, field + ' is not consumed by the runner')
        # the rejected revision gated fields the audit record does not carry
        for stale in ('declared_solids', 'resolved_solids', 'tesserellation_stl_sha256'):
            self.assertNotIn(stale, runner, stale + ' is not an audit-record field')

    def test_audit_record_location_is_deterministic(self):
        """Both getSessionDirFile() and the cwd fallback must resolve to the job root: STAR runs with
        WorkingDirectory = job root and the runner no longer copies case_spec into the step dirs."""
        runner = RUNNER.read_text(encoding='utf-8')
        self.assertIn('$info.WorkingDirectory=$job', runner)
        self.assertNotIn("Destination (Join-Path $dir 'case_spec.json')", runner)
        self.assertIn('Join-Path $job $recordName', runner)

    def test_controller_creates_upload_directories_before_scp(self):
        """Measured 2026-10-28: the first execution attempt failed because the remote sources/ directory
        did not exist when scp ran. The controller must create upload parents before any scp."""
        text = CONTROLLER.read_text(encoding='utf-8')
        sources = "job + '" + chr(92) * 2 + "sources'"  # source text carries ''\\sources''
        self.assertIn(sources, text, 'controller does not create the sources upload directory')
        create_index = text.index(sources)
        upload_index = text.index('b.scp(ROOT / RUNNER_REL, job +')
        self.assertLess(create_index, upload_index, 'uploads must start after the directory creation')

    def test_prepared_gates_equal_the_casespec_tolerances(self):
        """review-2l: the runner gate must read the SAME tolerances the audit macro uses. The first audited run
        passed the macro (1% re-based volume tolerance) and would have failed the runner gate (hard-coded
        0.2%) after both STAR processes; this ratchet derives the expectation from the CaseSpec."""
        spec = json.loads((CASE / 'case_spec.json').read_text(encoding='utf-8'))
        gates = self.config['gates']
        self.assertEqual(float(spec['geometry']['expected_bounds_tolerance_m']), gates['bounds_tolerance_m'])
        self.assertEqual(float(spec['geometry']['expected_volume_relative_tolerance']), gates['volume_relative_tolerance'])
        self.assertEqual(spec['model_length_unit'], gates['model_length_unit'])
        self.assertEqual(BODY, gates['part'])

    def test_audited_geometry_audit_passed(self):
        """O9 for the passing audited run: the record must claim ok AND the measured deviations must be inside the
        CaseSpec tolerances; a future geometry/step regression that only changes numbers fails here."""
        result = json.loads((AUDITED_PASS / 'RESULT.json').read_text(encoding='utf-8'))
        self.assertEqual('PASS', result['status'])
        import_rec = result['import']
        self.assertEqual('ok', import_rec['status'])
        self.assertTrue(import_rec['is_closed'])
        self.assertTrue(import_rec['is_manifold'])
        self.assertEqual(1, import_rec['solid_count'])
        self.assertEqual(0.01, result['audit']['volume_tolerance'])
        self.assertLessEqual(result['audit']['bounds_max_abs_error_m'], result['audit']['bounds_tolerance_m'])
        self.assertLessEqual(result['audit']['volume_relative_deviation'], result['audit']['volume_tolerance'])
        spec = json.loads((CASE / 'case_spec.json').read_text(encoding='utf-8'))
        self.assertEqual(spec['geometry']['expected_volume_relative_tolerance'], result['audit']['volume_tolerance'])
        self.assertEqual(spec['geometry']['expected_bounds_tolerance_m'], result['audit']['bounds_tolerance_m'])
        self.assertEqual(audit.sha256(CASE / 'geometry/APC_10x7E.step'), import_rec['step_sha256'])
        self.assertEqual('STAR_GEOMETRY_AUDIT_PASSED', spec['geometry']['geometry_audit_status'])

    def test_import_macro_reads_the_rename_back(self):
        text = IMPORT_MACRO.read_text(encoding='utf-8')
        self.assertIn('Rename did not stick', text)
        self.assertIn('PROP_JOB_DIR', text)
        self.assertIn('isClosed()', text)
        self.assertIn('isManifold()', text)


if __name__ == '__main__':
    unittest.main()
