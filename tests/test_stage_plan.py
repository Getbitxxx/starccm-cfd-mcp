# -*- coding: utf-8 -*-
"""Tests for the per-macro execution plan and for the runners that execute it.

Round 6 measured that `starccm+ -batch A,B,C` keeps executing later macros after an earlier one
throws. The decision "one stage, several macros -> several STAR processes" is code
(scripts/stage_plan.py) so it can be tested; the two PowerShell runners are held to it by structural
assertions, because this repository has no PowerShell interpreter on the development machine. That
limitation is real and is stated here rather than papered over: the runners' refusal paths are only
exercised by a real trial, and the scripts are parse-checked on the Windows host before use.
"""
import hashlib
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from stage_plan import (StagePlanError, installed_manifest_plans, plan_manifest_stage,  # noqa: E402
                        plan_pipeline, plan_stage)

PIPELINE = ROOT / 'cases' / 'static-sphere-external-flow' / 'pipeline-region-physics-mesh.json'
SOLVE_PIPELINE = ROOT / 'cases' / 'static-sphere-external-flow' / 'pipeline-solve-validation.json'
E2E_PIPELINE = ROOT / 'cases' / 'static-sphere-external-flow' / 'pipeline-e2e-geometry-to-validation.json'
DRIVER = ROOT / 'scripts' / 'run_static_external_flow_e2e.py'
RENAME_BASE = ROOT / 'cases' / 'static-sphere-rename-variant'
RENAME_E2E = RENAME_BASE / 'positive' / 'pipeline-e2e.json'
RENAME_GEOMETRY = RENAME_BASE / 'positive' / 'pipeline-geometry.json'
RENAME_NEGATIVE = RENAME_BASE / 'negative' / 'pipeline-audit-negative.json'
TRIAL_RUNNER = ROOT / 'scripts' / 'security' / 'trial_air3b_pipeline.ps1'
WORKER_RUNNER = ROOT / 'worker' / 'run_job.ps1'
REPORT_GENERATOR = ROOT / 'scripts' / 'generate_run_report.py'

spec = importlib.util.spec_from_file_location('progress_matrix', ROOT / 'scripts' / 'progress_matrix.py')
pm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pm)


def stage(**overrides):
    base = {'name': 'a-stage', 'macros': ['A.java'], 'input': 'in.sim', 'output': 'out.sim',
            'required': ['out.sim']}
    base.update(overrides)
    return base


class PlanStage(unittest.TestCase):

    def test_a_single_macro_stage_plans_without_declaring_outputs(self):
        plan = plan_stage(stage())
        self.assertEqual([{'index': 0, 'macro': 'A.java', 'input': 'in.sim', 'output': 'out.sim'}],
                         plan['steps'])

    def test_a_multi_macro_stage_without_declared_outputs_is_refused(self):
        with self.assertRaisesRegex(StagePlanError, 'macro_outputs'):
            plan_stage(stage(macros=['A.java', 'B.java', 'C.java']))

    def test_a_multi_macro_stage_with_the_wrong_number_of_outputs_is_refused(self):
        with self.assertRaisesRegex(StagePlanError, 'one entry per macro'):
            plan_stage(stage(macros=['A.java', 'B.java'], macro_outputs=['one.sim'],
                             output='one.sim', required=['one.sim']))

    def test_the_chain_hands_each_macro_the_previous_output(self):
        plan = plan_stage(stage(macros=['A.java', 'B.java', 'C.java'],
                                macro_outputs=['a.sim', 'b.sim', 'c.sim'], output='c.sim',
                                required=['a.sim', 'b.sim', 'c.sim']))
        self.assertEqual(['in.sim', 'a.sim', 'b.sim'], [step['input'] for step in plan['steps']])
        self.assertEqual(['a.sim', 'b.sim', 'c.sim'], [step['output'] for step in plan['steps']])

    def test_a_null_output_keeps_the_previous_input(self):
        plan = plan_stage(stage(macros=['A.java', 'B.java'], macro_outputs=[None, None],
                                output=None, required=[]))
        self.assertEqual(['in.sim', 'in.sim'], [step['input'] for step in plan['steps']])

    def test_the_declared_stage_output_must_be_the_last_macro_output(self):
        with self.assertRaisesRegex(StagePlanError, 'end at'):
            plan_stage(stage(macros=['A.java', 'B.java'], macro_outputs=['a.sim', 'b.sim'],
                             output='a.sim', required=['a.sim', 'b.sim']))

    def test_a_macro_output_must_be_a_required_artefact(self):
        with self.assertRaisesRegex(StagePlanError, 'not in required'):
            plan_stage(stage(macros=['A.java', 'B.java'], macro_outputs=['a.sim', 'b.sim'],
                             output='b.sim', required=['b.sim']))

    def test_stage_without_macros_is_refused(self):
        with self.assertRaises(StagePlanError):
            plan_stage(stage(macros=[]))


class PlanPipeline(unittest.TestCase):

    def test_a_stage_input_must_be_pinned_or_produced_earlier(self):
        with self.assertRaisesRegex(StagePlanError, 'neither a reviewed input'):
            plan_pipeline({'input_hashes': {'in.sim': 'x' * 64},
                           'stages': [stage(input='elsewhere.sim')]})

    def test_the_stage_to_stage_handoff_is_accepted(self):
        plan = plan_pipeline({'input_hashes': {'in.sim': 'x' * 64},
                              'stages': [stage(),
                                         stage(name='second', input='out.sim', output=None,
                                               required=[])]})
        self.assertEqual(['in.sim', 'out.sim'], [s['input'] for s in plan['stages']])

    def test_the_round7_pipeline_plans_and_audits_the_saved_mesh(self):
        plan = plan_pipeline(json.loads(PIPELINE.read_text(encoding='utf-8')))
        self.assertEqual(['region-physics-mesh', 'mesh-quality-audit'],
                         [entry['name'] for entry in plan['stages']])
        self.assertEqual(['external_domain_built.sim', 'external_flow_region.sim',
                          'external_flow_physics.sim'],
                         [step['input'] for step in plan['stages'][0]['steps']])
        self.assertEqual(['external_flow_meshed.sim'],
                         [step['input'] for step in plan['stages'][1]['steps']])
        self.assertEqual('AuditExternalFlowMesh.java', plan['stages'][1]['steps'][0]['macro'])

    def test_every_installed_manifest_stage_plans(self):
        plans = installed_manifest_plans(ROOT / 'workflows')
        self.assertGreater(len(plans), 20)
        for plan in plans:
            self.assertEqual(len(plan['steps']), len({step['index'] for step in plan['steps']}))

    def test_a_manifest_stage_without_outputs_is_refused_like_a_pipeline_stage(self):
        with self.assertRaisesRegex(StagePlanError, 'macro_outputs'):
            plan_manifest_stage('core', 'two-macro', {'macros': ['A.java', 'B.java'],
                                                      'output_sim': None})


class TheSolvePipelineCannotBeGamedIntoGreen(unittest.TestCase):
    """Track P: NOT_CONVERGED is a pass, and a convergence claim is the one thing that must not be
    manufactured. AGENTS.md section 1 and the user's instruction for this stage both say so, so the
    rule is pinned here rather than promised in prose: the pipeline's numeric gates may only test that
    the solve RAN and produced plausible finite numbers, never that it converged.
    """

    # The numeric gates may only reference keys of these two kinds: "the solve happened / ran enough
    # iterations / produced a history" and "the measured quantities are finite and physically
    # plausible". A convergence VERDICT is recorded, never gated. The allowed set is explicit so that
    # adding a gate on a verdict key fails this test rather than passing a substring check.
    ALLOWED_KEYS = {'iterations_run', 'end_iteration', 'residual_rows', 'drag_window_size',
                    'drag_window_samples', 'measured_drag_x_n_abs', 'iteration',
                    'measured_mass_imbalance_ratio', 'outer_boundaries_measured'}

    def setUp(self):
        self.pipeline = json.loads(SOLVE_PIPELINE.read_text(encoding='utf-8'))

    def test_no_numeric_gate_references_a_verdict_key(self):
        offenders = []
        for stage in self.pipeline['stages']:
            for spec in stage.get('require_numeric') or []:
                key = spec.split('::')[-1].split('>=')[0].split('<=')[0].strip()
                if key not in self.ALLOWED_KEYS:
                    offenders.append(spec)
        self.assertEqual([], offenders,
                         'these gates reference something other than "the solve happened" or a '
                         'plausibility bound on a measured quantity: %s' % offenders)

    def test_the_records_carry_the_verdicts_even_though_they_are_not_gated(self):
        contents = ' '.join(spec for stage in self.pipeline['stages']
                            for spec in (stage.get('require_contains') or []))
        self.assertIn('convergence_basis', contents)

    def test_the_gates_only_require_that_the_solve_happened(self):
        specs = [spec for stage in self.pipeline['stages']
                 for spec in (stage.get('require_numeric') or [])]
        self.assertTrue(any('iterations_run' in spec for spec in specs), specs)
        self.assertTrue(any('residual_rows' in spec for spec in specs), specs)
        self.assertTrue(all(spec.split('::')[-1].split('>=')[0].split('<=')[0].strip()
                            for spec in specs), specs)

    def test_the_pipeline_states_the_track_p_semantics_in_itself(self):
        note = self.pipeline.get('note', '')
        self.assertIn('NOT_CONVERGED', note)
        self.assertIn('acceptable outcome', note)

    def test_the_validation_stage_records_the_reference_policy(self):
        validation = self.pipeline['stages'][1]
        contents = ' '.join(validation.get('require_contains') or [])
        self.assertIn('validation_verdict', contents)
        self.assertIn('reference_policy', contents)
        self.assertIn('read_only', contents)

    def test_the_solve_chain_hands_the_checkpoint_to_the_audit(self):
        plan = plan_pipeline(self.pipeline)
        self.assertEqual(['solve', 'validation'], [entry['name'] for entry in plan['stages']])
        self.assertEqual('external_flow_solved.sim', plan['stages'][1]['steps'][0]['input'])
        self.assertEqual('external_flow_solved.sim', plan['stages'][0]['output'])


class TheSolveMacrosRespectTheirRecordedContract(unittest.TestCase):
    """Source-contract checks for the two solve-stage macros.

    AGENTS.md section 2-O9: a review finding must become an executable check in the same round. The
    reviewer of round 8 found three defects that no test could have caught, because none of the Java
    logic is reachable from Python. These checks pin the exact contract each defect violated. They are
    text checks, not executions - the honest label for the compensating control the repo already uses
    for its PowerShell runners - and each one WOULD have failed on the revision the reviewer rejected.
    """

    SOLVE = ROOT / 'macros' / 'RunExternalFlowSolve.java'
    AUDIT = ROOT / 'macros' / 'AuditExternalFlowConvergence.java'

    def test_the_gated_residual_is_the_value_written_under_final(self):
        # Blocker 3: the caller read window[2] (the PREVIOUS window mean) as the gated value and wrote
        # it under the key `final`, so an over-gate final residual could be recorded as met: true.
        text = self.SOLVE.read_text(encoding='utf-8')
        self.assertIn('double value = window == null ? Double.NaN : window[0];', text)
        self.assertIn('double lastMean = window == null ? Double.NaN : window[1];', text)
        self.assertIn('double previousMean = window == null ? Double.NaN : window[2];', text)
        self.assertIn('boolean ok = Double.isFinite(value) && value <= gate.getValue();', text)
        self.assertIn(r'.append("\":{\"final\":").append(json(value))', text)
        self.assertIn('return worst == null ? null : new double[] {worst[0], worst[1], worst[2]};', text)
        # the audit must use the same order: final first
        audit = self.AUDIT.read_text(encoding='utf-8')
        self.assertIn('return worst == null ? null : new double[] {worst[2], worst[0], worst[1]};', audit)

    def test_a_non_finite_drag_sample_is_not_filtered_out(self):
        # Blocker 5: chunkHistoryDrag dropped non-finite samples, so the audit could certify a window
        # that stage 1 had recorded as divergent.
        audit = self.AUDIT.read_text(encoding='utf-8')
        start = audit.index('private static double[] chunkHistoryDrag(')
        end = audit.index('private static String sha256(')
        body = audit[start:end]
        self.assertIn('values.add(Double.parseDouble(parts[2]));', body)
        self.assertNotIn('if (Double.isFinite(value)) values.add', body)
        self.assertIn('if (!stage1FiniteState) loadsSteady = null;', audit)
        self.assertIn('&& stage1FiniteState', audit)

    def test_read_only_is_verified_against_the_declared_stage_output(self):
        # Blocker 4 (round 8): input_sim_sha256 was recorded but compared by nothing, while the pipeline
        # note claimed a post-run check existed.
        # ①R15: the comparison is now bound to the stage record's DECLARED OUTPUT. The first version
        # accepted ANY 64-hex value in the record (`hashes.find()` + `stageRecordSha`), so an input hash, a
        # macro digest or a runner digest that happened to equal the .sim hash would have satisfied it.
        audit = self.AUDIT.read_text(encoding='utf-8')
        self.assertIn('stage-solve.json', audit)
        self.assertIn('rootString(record, "output_sha256", null)', audit)
        self.assertIn('rootString(record, "output", null)', audit)
        self.assertIn('private static String rootString(', audit)
        self.assertIn('boolean readOnlyVerified = outputIdentityIsSim && inputSha.equals(stageOutputSha);',
                      audit)
        self.assertIn('&& readOnlyVerified', audit)
        # the old any-hex acceptance must not come back
        self.assertNotIn('stageRecordSha', audit)
        self.assertNotIn('if (hashes.group(1).equals(inputSha))', audit)
        pipeline = json.loads(SOLVE_PIPELINE.read_text(encoding='utf-8'))
        contents = ' '.join(spec for stage in pipeline['stages']
                            for spec in (stage.get('require_contains') or []))
        self.assertIn('read_only_verified', contents)
        self.assertIn('drag_sign_consistent', contents)

    def test_every_required_literal_is_written_by_the_macro_of_that_stage(self):
        # Independent review round 8, fourth pass: this is the check whose absence let the pipeline
        # require `"drag_sign_consistent":true` from stage 1 while stage 1 never wrote that key. The
        # per-stage mapping matters - an assertion that joins every stage's literals passes while one
        # stage's entry is unsatisfiable.
        pipeline = json.loads(SOLVE_PIPELINE.read_text(encoding='utf-8'))
        writers = {'solve': self.SOLVE, 'validation': self.AUDIT}
        for stage in pipeline['stages']:
            source = writers[stage['name']].read_text(encoding='utf-8')
            for literal in stage.get('require_contains') or []:
                body = literal.split('::', 1)[1]
                key = body.split('"')[1]
                # the sources are Java string literals, so the key appears escaped as \"key\"
                self.assertIn('\\"%s\\"' % key, source,
                              'stage %s requires key %r, which %s never writes'
                              % (stage['name'], key, writers[stage['name']].name))

    def test_the_solve_stage_records_the_drag_sign(self):
        # the key itself, so deleting it from the macro fails here rather than at run time
        solve = self.SOLVE.read_text(encoding='utf-8')
        self.assertIn('drag_sign_consistent', solve)
        self.assertIn('boolean dragSignConsistent = Double.isFinite(finalDrag) && finalDrag > 0;', solve)
        self.assertIn('&& dragSignConsistent;', solve)
        audit = self.AUDIT.read_text(encoding='utf-8')
        self.assertIn('&& dragSignConsistent;', audit)
        self.assertIn('boolean stage1FiniteState = false;', audit)

    def test_no_by_name_report_lookup_remains(self):
        # ROUND 8 MEASURED (trial s1-static-sphere-solve-validation-20261003c, external_flow_solve.json):
        # `ReportManager.getReport(name)` THROWS when the object does not exist, so the solve failed
        # 18.54 s in with zero iterations. The fix must be the iterate-and-match pattern that the
        # already-executed RunAir3bMrfSegment uses for monitors, and it must not be a broad catch.
        for path in (self.SOLVE, self.AUDIT):
            text = path.read_text(encoding='utf-8')
            self.assertNotIn('getReportManager().getReport(', text,
                             '%s still looks a report up by name' % path.name)
            self.assertIn('getReportManager().getObjects()', text,
                          '%s no longer iterates the report manager' % path.name)
        solve = self.SOLVE.read_text(encoding='utf-8')
        self.assertIn('private static Report findReport(Simulation sim, String name)', solve)
        self.assertIn('if (name.equals(candidate.getPresentationName())) return candidate;', solve)
        # an absent report is a NORMAL state, so the lookup must not be wrapped in a catch that would
        # also swallow real failures
        start = solve.index('private static Report findReport(')
        body = solve[start:solve.index('private static void deleteReport(')]
        self.assertNotIn('catch', body)

    def test_the_audit_creates_no_report_name_that_stage_1_saved(self):
        # Stage 1 saves EXTERNAL_FLOW_MASS_<boundary> reports into the checkpoint the audit reads, so the
        # audit must use its own names or it collides with an existing object.
        audit = self.AUDIT.read_text(encoding='utf-8')
        # Independent review R9, concern 1: the token check was evadable (keep EXTERNAL_FLOW_AUDIT_MASS_
        # in a comment, set the name from a constant). Assert the exact call site AND that the colliding
        # literal appears nowhere at all.
        self.assertIn('setPresentationName("EXTERNAL_FLOW_AUDIT_MASS_" + safe(boundary.getPresentationName()))',
                      audit)
        self.assertNotIn('"EXTERNAL_FLOW_MASS_"', audit)

    def test_the_basis_names_the_drag_direction_condition(self):
        # Round 8 review concern 2: `converged` includes dragSignConsistent but the prose did not say so.
        solve = self.SOLVE.read_text(encoding='utf-8')
        start = solve.index('convergence_basis')
        basis = solve[start:solve.index('checkpoint', start)]
        self.assertIn('drag direction', basis)
        self.assertIn('drag_sign_consistent', basis)

    def test_the_gates_measure_before_they_report(self):
        # The iteration gate must be consistent with the three-chunk window the stability test needs.
        pipeline = json.loads(SOLVE_PIPELINE.read_text(encoding='utf-8'))
        solve_keys = {spec.split('::')[-1].split('>=')[0].split('<=')[0].strip()
                      for spec in pipeline['stages'][0]['require_numeric']}
        self.assertIn('iterations_run', solve_keys)
        self.assertNotIn('converged', solve_keys)
        self.assertIn('iterations_run>=150', ' '.join(pipeline['stages'][0]['require_numeric']))


class RunnersExecuteOneMacroPerProcess(unittest.TestCase):
    """Structural assertions. There is no PowerShell interpreter here, so these are text checks.

    They are deliberately about the statements that make the isolation real: the per-macro STAR
    invocation, the refusal of an undeclared chain, and the not_run marking of macros that a failure
    prevented from running.
    """

    TRIAL_BATCH = "-batch '+$macro+' \"'+$current+'\""
    WORKER_BATCH = "-batch '+$macro+' \"'+$current+'\""

    def test_the_trial_runner_launches_one_star_process_per_macro(self):
        text = TRIAL_RUNNER.read_text(encoding='utf-8')
        self.assertIn(self.TRIAL_BATCH, text)
        self.assertNotIn("$stage.macros -join ','", text)
        self.assertNotIn("$macros=$stage.macros -join ','", text)

    def test_the_trial_runner_refuses_an_undeclared_multi_macro_chain(self):
        text = TRIAL_RUNNER.read_text(encoding='utf-8')
        self.assertIn('must declare one macro_outputs entry per macro', text)
        self.assertIn('macro_outputs length mismatch', text)

    def test_the_trial_runner_writes_a_stage_record_on_both_paths(self):
        text = TRIAL_RUNNER.read_text(encoding='utf-8')
        self.assertIn("'stage-'+$stage.name+'.json'", text)
        # Written on the failure path as well as the success path: a failed stage must not be the one
        # stage that leaves no centralised record.
        self.assertGreaterEqual(text.count('Save-StageRecord $stage.name $record'), 2)
        self.assertIn("$record.state='ok'", text)
        self.assertIn("$record.state='failed'", text)

    def test_the_stage_record_exists_before_anything_that_can_throw(self):
        # Independent review round 7, concern C2: two throw sites ran before the record was created,
        # so those failure paths wrote no stage record at all. The record literal must come first.
        text = TRIAL_RUNNER.read_text(encoding='utf-8')
        record_at = text.index("$record=@{schema_version=1;stage=$stage.name")
        for later in ('macro output is not a plain file name',
                      'input missing',
                      'missing marker',
                      'missing artifact'):
            self.assertLess(record_at, text.index(later), later)

    def test_ran_means_star_was_actually_launched(self):
        # Independent review round 7, second pass: the inner catch set ran=true unconditionally, so a
        # macro that failed BEFORE launch (missing input, exhausted budget) was recorded as having run.
        text = TRIAL_RUNNER.read_text(encoding='utf-8')
        launch = text.index('$proc.Start()')
        self.assertLess(launch, text.index('$script:lastStarRan=$true'))
        self.assertIn('$lastStarRan=$false', text)
        self.assertIn('finally{$script:lastStarSeconds=', text)
        catch = text.index('# R17: R16 measured')
        self.assertIn('$macroRecord.ran=$lastStarRan', text[catch:catch + 600])
        self.assertIn('$macroRecord.seconds=$lastStarSeconds', text[catch:catch + 600])
        self.assertNotIn('$macroRecord.ran=$true', text[catch:catch + 600])
        worker = WORKER_RUNNER.read_text(encoding='utf-8')
        self.assertLess(worker.index('$stageRecord=@{schema_version=1'),
                        worker.index("throw 'Runtime digest mismatch"))

    def test_the_pipeline_records_its_residual_limitations(self):
        pipeline = json.loads(PIPELINE.read_text(encoding='utf-8'))
        note = ' '.join(pipeline.get('residual_limitations') or [])
        self.assertIn('require_numeric', note)
        self.assertIn('getRegion', note)

    def test_the_macro_that_failed_is_recorded_as_failed_not_as_not_run(self):
        # Independent review round 7, blocker 3: the failing macro was written as ran=false/not_run
        # with no exit code, although it ran, failed, and left its logs on disk.
        text = TRIAL_RUNNER.read_text(encoding='utf-8')
        self.assertIn("$macroRecord.status='failed'", text)
        self.assertIn("$macroRecord.exit_code=$lastStarExitCode", text)
        self.assertIn("status='not_run'", text)
        # the exit code must be captured before the throw, or the record can never carry it
        self.assertLess(text.index('$script:lastStarExitCode=$proc.ExitCode'),
                        text.index('throw "Owned STAR exit code'))

    def test_the_worker_runner_launches_one_star_process_per_macro(self):
        text = WORKER_RUNNER.read_text(encoding='utf-8')
        self.assertIn(self.WORKER_BATCH, text)
        self.assertNotIn("$stage.macros -join ','", text)
        self.assertIn('must declare one macro_outputs entry per macro', text)

    def test_the_worker_runner_also_writes_a_stage_record(self):
        # Independent review round 7, blocker 2: this runner wrote only state.json, so citability
        # could not be decided per stage and one failed stage marked every other stage's records in the
        # same run as not citable.
        text = WORKER_RUNNER.read_text(encoding='utf-8')
        self.assertIn("('stage-'+$request.stage+'.json')", text)
        self.assertIn('function Save-StageRecord', text)
        self.assertIn("$stageRecord.state='ok'", text)
        self.assertIn("$failureState='failed'", text)
        self.assertIn("$stageRecord.state=$failureState", text)
        self.assertIn("status='not_run'", text)
        self.assertIn("ran=[bool]$macroLaunched;status=$(if($macroLaunched){$failureState}else{'not_run'});exit_code=$lastStarExitCode", text)
        self.assertIn('$currentMacroIndex=$index', text)
        # the record must exist before the loop that can throw
        self.assertLess(text.index('$stageRecord=@{schema_version=1'),
                        text.index('for($index=0;$index -lt $macroList.Count;$index++)'))

    def test_the_worker_runner_records_every_early_exit(self):
        text = WORKER_RUNNER.read_text(encoding='utf-8')
        for state in ('cancelled', 'resource_stop'):
            self.assertIn("$failureState='%s'" % state, text)
        self.assertIn("$stageRecord.state=$failureState",text)
        self.assertIn("$stageRecord.macros=$macroRecords",text)
        self.assertIn("for($index=$macroRecords.Count;$index -lt $macroList.Count;$index++)",text)
        self.assertNotIn("$state.state='TIMED_OUT'",text)
        self.assertIn("'User requested cancellation before launch'",text)

    def test_the_worker_runner_keeps_the_marker_scan_working(self):
        # star.log is still the file the postchecks scan; it is now the concatenation of the
        # per-macro logs, and the per-macro logs are kept for attribution.
        text = WORKER_RUNNER.read_text(encoding='utf-8')
        self.assertIn("'star.log'", text)
        self.assertIn("$combinedLog+=", text)
        self.assertIn('macro_records', text)


class MeshEvidenceRequirementIsDeclared(unittest.TestCase):
    """The four measured quantities are required as *declared* record keys, not as prose.

    AGENTS.md section 8: a configured prism layer count is not evidence of a near-wall layer. The
    progress matrix must therefore name the record that carries the measured values and the keys that
    record has to contain before the mesh stage can be PASSED.
    """

    REQUIRED = ['measured_cell_quality_min', 'measured_face_validity_min',
                'measured_volume_change_min', 'measured_wall_prism_layers_min']

    def setUp(self):
        self.doc = json.loads((ROOT / 'progress.json').read_text(encoding='utf-8'))
        self.mesh = self.doc['scenarios']['1_static_external_flow']['stages']['mesh']

    def test_the_mesh_stage_declares_all_four_measured_keys(self):
        self.assertEqual(self.REQUIRED, self.mesh.get('requires_record_keys'))

    def test_the_pipeline_also_gates_on_the_four_measured_values(self):
        pipeline = json.loads(PIPELINE.read_text(encoding='utf-8'))
        specs = pipeline['stages'][1]['require_numeric']
        for key in self.REQUIRED:
            self.assertTrue(any(key in spec for spec in specs), key)

    def test_the_configured_layer_count_is_not_named_as_a_measurement(self):
        # The round-6 record said "prism_layers":6, which reads like a measurement and is not one.
        text = (ROOT / 'macros' / 'BuildExternalFlowMesh.java').read_text(encoding='utf-8')
        self.assertNotIn('\\"prism_layers\\"', text)
        self.assertIn('\\"configured_prism_layers\\"', text)


class SubmissionRefusesAnUnisolatableStage(unittest.TestCase):
    """The rule is on the submission path, not only in an offline checker.

    A stage that cannot be split into one process per macro must never be uploaded, let alone run:
    `scripts/air3b_pipeline_trial.py::start` plans it before it touches the host.
    """

    class NoHost:
        def ps(self, *args, **kwargs):
            raise AssertionError('the submission path contacted the host before planning the stage')

        scp = ps

    def test_start_refuses_a_multi_macro_stage_without_declared_outputs(self):
        import air3b_pipeline_trial as trial
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__('shutil').rmtree, tmp)
        pipeline = tmp / 'pipeline.json'
        pipeline.write_text(json.dumps({
            'pipeline_id': 'unit-test', 'input_hashes': {'case_spec.json': 'x' * 64},
            'macro_hashes': {'A.java': 'y' * 64},
            'stages': [{'name': 'stage', 'macros': ['A.java', 'B.java'], 'input': None,
                        'output': None, 'marker': 'MARKER', 'timeout_seconds': 60,
                        'required': []}]}), encoding='utf-8')
        with self.assertRaisesRegex(StagePlanError, 'macro_outputs'):
            trial.start(self.NoHost(), 'unit-test-1234', pipeline, [], tmp)


class ReviewMustPinSupportingFiles(unittest.TestCase):
    """A file that changes what a trial RUNS must be hash-bound, or its change is invisible.

    An earlier version of this gate recorded runtime_digest and never compared it. The same mistake
    would reappear here: stage_plan.py decides the per-macro chain, so a review record that does not
    pin it could be silently invalidated by editing that file.
    """

    def setUp(self):
        import air3b_pipeline_trial as trial
        import shutil
        import tempfile
        self.trial = trial
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def write_review(self, **overrides):
        payload = {'schema_version': 1, 'approved': True, 'independent': True,
                   'reviewer': 'unit test', 'reviewed_at': '2026-01-01T00:00:00Z',
                   'pipeline_sha256': 'p', 'controller_sha256': 'c', 'runner_sha256': 'r',
                   'macro_hashes': {}, 'input_hashes': {},
                   'support_hashes': self.trial.support_hashes(),
                   # 2026-10-14（C3）：试跑路径与 MCP/backend 路径一律要求钉住 resource_contract.py
                   'resource_contract_sha256': hashlib.sha256(
                       (ROOT / 'scripts' / 'resource_contract.py').read_bytes()).hexdigest()}
        payload.update(overrides)
        (self.tmp / 'pipeline-review.json').write_text(json.dumps(payload), encoding='utf-8')

    def test_a_record_without_support_hashes_is_refused(self):
        self.write_review(support_hashes=None)
        with self.assertRaisesRegex(ValueError, 'support hashes'):
            self.trial.check_review(self.tmp, {}, {}, 'r', 'c', 'p')

    def test_a_record_with_a_stale_support_hash_is_refused(self):
        self.write_review(support_hashes={'scripts/stage_plan.py': '0' * 64})
        with self.assertRaisesRegex(ValueError, 'support hashes'):
            self.trial.check_review(self.tmp, {}, {}, 'r', 'c', 'p')

    def test_a_record_with_the_current_hashes_is_accepted(self):
        self.write_review()
        self.assertIsNone(self.trial.check_review(self.tmp, {}, {}, 'r', 'c', 'p'))

    def test_an_unapproved_record_is_refused(self):
        self.write_review(approved=False)
        with self.assertRaisesRegex(ValueError, 'Independent scoped review'):
            self.trial.check_review(self.tmp, {}, {}, 'r', 'c', 'p')


class ProgressMatrixUsesTheGate(unittest.TestCase):
    """§2-O3 in the document machinery, exercised with the real failed run as the citation."""

    def doc_with_mesh_record(self, record):
        stages = {name: {'status': 'PASSED', 'evidence': []}
                  for name in ['case_spec', 'geometry', 'mesh', 'solve', 'validation']}
        stages['mesh'] = {'status': 'PASSED', 'evidence': [], 'record': record,
                          'requires_record_keys': ['measured_cell_quality_min']}
        return {
            'updated': '2026-01-01',
            'stage_names': ['case_spec', 'geometry', 'mesh', 'solve', 'validation'],
            'counters': {'pipeline_verified_P': '0/3', 'engineering_validated_E': '0/3'},
            'scenarios': {
                '1_static_external_flow': {'case': 'case-a', 'pipeline': 'PARTIAL',
                                           'engineering': 'NOT_STARTED', 'stages': stages},
                '2_steady_mrf_propeller': {'pipeline': 'PARTIAL', 'engineering': 'NOT_STARTED',
                                           'stages': stages},
                '3_transient_rotation': {'pipeline': 'PARTIAL', 'engineering': 'NOT_STARTED',
                                         'stages': stages},
            },
            'generality': {'matrix_status': 'PASSED', 'operations': []},
            'budget': {'per_case': {'case-a': {'rounds_since_stage_advance': 0, 'limit': 3,
                                               'active': True}}},
        }

    def relative(self, path):
        return str(Path(path).relative_to(ROOT))

    def test_citing_the_failed_runs_mesh_record_blocks(self):
        doc = self.doc_with_mesh_record(
            self.relative(ROUND6 := (ROOT / 'test-evidence' / 'static-external-flow'
                                     / 'batch-region-physics-mesh'
                                     / 's1-static-sphere-region-physics-mesh-20261003a'
                                     / 'external_flow_mesh.json')))
        blocking = pm.check(doc)['blocking']
        self.assertTrue(any('§2-O3' in item and 'NOT_CITABLE_FAILED_RUN' in item for item in blocking),
                        blocking)

    def test_a_measured_record_from_a_successful_run_passes_the_gate(self):
        doc = self.doc_with_mesh_record(self.relative(
            ROOT / 'test-evidence' / 'static-external-flow' / 'audit-z1p5'
            / 's1-static-sphere-audit-z1p5-20261003a' / 'static_body_audit.json'))
        # The named record is citable; the declared key is absent from it, which is what blocks.
        blocking = pm.check(doc)['blocking']
        self.assertTrue(any('measured_cell_quality_min' in item for item in blocking), blocking)
        self.assertFalse(any('NOT_CITABLE' in item for item in blocking), blocking)

    def test_an_active_case_cannot_be_passed_without_a_named_record(self):
        doc = self.doc_with_mesh_record(None)
        blocking = pm.check(doc)['blocking']
        self.assertTrue(any('没有 record' in item for item in blocking), blocking)

    def test_an_inactive_case_only_warns(self):
        doc = self.doc_with_mesh_record(None)
        doc['budget']['per_case']['case-a']['active'] = False
        report = pm.check(doc)
        self.assertFalse(any('没有 record' in item for item in report['blocking']), report['blocking'])
        self.assertTrue(any('没有 record' in item for item in report['warnings']), report['warnings'])

    def test_a_runner_state_file_cannot_be_the_certificate(self):
        # Independent review round 7, second pass, demonstrated end-to-end: PASSED + record:
        # case-probe.json + no requires_record_keys produced no blocking item at all.
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        (tmp / 'case-probe.json').write_text(json.dumps({'state': 'AIR3B_PIPELINE_COMPLETE'}),
                                             encoding='utf-8')
        doc = self.doc_with_mesh_record(str((tmp / 'case-probe.json').relative_to(ROOT))
                                        if str(tmp).startswith(str(ROOT)) else str(tmp / 'case-probe.json'))
        doc['scenarios']['1_static_external_flow']['stages']['mesh']['requires_record_keys'] = []
        blocking = pm.check(doc)['blocking']
        self.assertTrue(any('§2-O3' in item and 'NOT_CITABLE_NO_CLAIM' in item for item in blocking),
                        blocking)

    def test_a_record_attributed_by_a_failed_stage_still_warns(self):
        # Independent review round 7, second pass (regression I introduced): the R3 warning required
        # `run_state_file`, which the stage-precedence branch never sets, so once the runners wrote
        # stage records the warning silently disappeared for exactly those records.
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        round6 = (ROOT / 'test-evidence' / 'static-external-flow' / 'batch-region-physics-mesh'
                  / 's1-static-sphere-region-physics-mesh-20261003a' / 'external_flow_mesh.json')
        shutil.copy2(round6, tmp / 'external_flow_mesh.json')
        (tmp / 'case-probe.json').write_text(json.dumps({'state': 'FAILED'}), encoding='utf-8')
        (tmp / 'stage-a.json').write_text(json.dumps(
            {'stage': 'a', 'state': 'failed', 'artifacts': ['external_flow_mesh.json'],
             'macro_outputs': []}), encoding='utf-8')
        doc = self.doc_with_mesh_record(None)
        mesh_entry = doc['scenarios']['1_static_external_flow']['stages']['mesh']
        mesh_entry['status'] = 'PARTIAL'
        mesh_entry['evidence'] = [str(tmp / 'external_flow_mesh.json')]
        warnings = pm.check(doc)['warnings']
        self.assertTrue(any('NOT_CITABLE_FAILED_RUN' in item and 'external_flow_mesh.json' in item
                            for item in warnings), warnings)

    def test_a_typo_in_the_case_name_cannot_switch_the_gate_off(self):
        # Independent review round 7 concern C1: `is_active = case in active_cases` let a typo in the
        # scenario's case name turn a BLOCKING §2-O3 item into a warning.
        doc = self.doc_with_mesh_record(None)
        doc['scenarios']['1_static_external_flow']['case'] = 'static-sphere-external-flow-typo'
        report = pm.check(doc)
        self.assertTrue(any('不是 budget.per_case 里的键' in item for item in report['blocking']),
                        report['blocking'])
        self.assertTrue(any('没有 record' in item for item in report['blocking']), report['blocking'])


class TheEndToEndPipelineRunsFromTheGeometryOnlySim(unittest.TestCase):
    """Track P: one submission from a geometry-only .sim to an independent validation record.

    The round-9 state was five stages that each ran, wired together BY HAND between two trials. The
    failure this class pins is the one that assembly invites: a pipeline that claims to start from
    geometry but actually consumes a checkpoint left behind by an earlier half-run, or a six-stage chain
    whose declared deadlines make the runner refuse the whole submission before it starts.
    """

    def setUp(self):
        self.pipeline = json.loads(E2E_PIPELINE.read_text(encoding='utf-8'))

    def test_the_chain_starts_at_geometry_and_ends_at_validation(self):
        plan = plan_pipeline(self.pipeline)
        self.assertEqual(['audit-body', 'build-domain', 'region-physics-mesh', 'mesh-quality-audit',
                          'solve', 'validation'], [entry['name'] for entry in plan['stages']])
        self.assertEqual('static_body_geometry.sim', plan['stages'][0]['steps'][0]['input'])
        self.assertEqual('static_body_geometry.sim', plan['stages'][1]['steps'][0]['input'])
        self.assertEqual('external_domain_built.sim', plan['stages'][2]['steps'][0]['input'])
        self.assertEqual('external_flow_region.sim', plan['stages'][2]['steps'][1]['input'])
        self.assertEqual('external_flow_physics.sim', plan['stages'][2]['steps'][2]['input'])
        self.assertEqual('external_flow_meshed.sim', plan['stages'][3]['steps'][0]['input'])
        self.assertEqual('external_flow_meshed.sim', plan['stages'][4]['steps'][0]['input'])
        self.assertEqual('external_flow_solved.sim', plan['stages'][5]['steps'][0]['input'])

    def test_every_stage_declares_an_input_except_one_that_creates_its_own_state(self):
        # Independent round-10 review, blocker 1: the mesh-quality-audit stage was assembled without an
        # `input`, and the blind spot was that the chain test above skipped that index. The runner turns a
        # null input into `starccm+ -new`, and AuditExternalFlowMesh.java only ever calls
        # getActiveSimulation() -- in a -new session there is no region and no volume mesh, so the stage
        # fails, case-probe.json goes FAILED, and solve/validation never run. Every macro in this pipeline
        # opens the state it is handed, so every stage must declare one.
        plan = plan_pipeline(self.pipeline)
        for entry in plan['stages']:
            self.assertIsNotNone(entry['input'],
                                 'stage %s declares no input, so the runner would launch it with -new'
                                 % entry['name'])
            for step in entry['steps']:
                self.assertIsNotNone(step['input'],
                                     'macro %s in stage %s would run against no state'
                                     % (step['macro'], entry['name']))

    def test_the_solve_deadline_sits_above_the_macros_own_budget(self):
        # Independent round-10 review, condition 1: a stage deadline BELOW the macro's own wall-clock
        # budget makes the runner's Owned STAR timeout kill the process before
        # RunExternalFlowSolve.java can write stop_reason = wall_clock_budget. The deadline must be the
        # looser of the two limits, and the env value is what the macro reads.
        solve = [stage for stage in self.pipeline['stages'] if stage['name'] == 'solve'][0]
        macro_budget = int(self.pipeline['env']['EXTERNAL_FLOW_BUDGET_SECONDS'])
        self.assertGreater(solve['timeout_seconds'], macro_budget,
                           'the solve stage deadline must exceed the macro budget')
        source = (ROOT / 'macros' / 'RunExternalFlowSolve.java').read_text(encoding='utf-8')
        self.assertIn('wall_clock_budget', source)

    def test_no_stage_starts_from_an_earlier_half_runs_checkpoint(self):
        # The two checkpoints the manual hand-off used must not appear as this pipeline's INPUT. They
        # may not appear at all: a reviewed input that the chain never reads would be dead ceremony.
        text = E2E_PIPELINE.read_text(encoding='utf-8')
        for handoff in ('external_flow_meshed.sim', 'external_domain_built.sim'):
            self.assertNotIn('"%s"' % handoff, self.pipeline['input_hashes'], handoff)
        self.assertEqual({'case_spec.json', 'static_body_geometry.sim'},
                         set(self.pipeline['input_hashes']))
        self.assertIn('geometry-only', text)

    def test_the_declared_deadlines_leave_the_controller_within_its_cap(self):
        # scripts/air3b_pipeline_trial.py sets timeout_seconds = min(1800, declared + 60) and
        # trial_air3b_pipeline.ps1 refuses the submission unless declared + 60 <= timeout_seconds. The
        # round-9 shape (900 + 300 + 900 + 300 = 2400) was refused by construction, so the sum is a
        # real gate and is asserted here rather than discovered by a rejected submission. The declared
        # total and the per-stage allocation are derived from the stages rather than retyped, so the
        # budget_basis block cannot drift away from the bytes it describes.
        declared = sum(stage['timeout_seconds'] for stage in self.pipeline['stages'])
        self.assertLessEqual(declared + 60, 1800, declared)
        self.assertEqual(self.pipeline['budget_basis']['declared_stage_budget_seconds'], declared)
        self.assertEqual(self.pipeline['budget_basis']['controller_timeout_seconds'],
                         min(1800, declared + 60))
        self.assertEqual(self.pipeline['budget_basis']['scheduled_task_limit_seconds'],
                         min(1800, declared + 60) + 60)
        self.assertEqual(self.pipeline['budget_basis']['per_stage_guardrail_seconds'],
                         {stage['name']: stage['timeout_seconds'] for stage in self.pipeline['stages']})

    def test_every_stage_deadline_is_within_the_runners_own_bounds(self):
        # trial_air3b_pipeline.ps1: a stage deadline must be 30..1500 s.
        for stage in self.pipeline['stages']:
            self.assertTrue(30 <= stage['timeout_seconds'] <= 1500, stage['name'])

    def test_the_stage_count_and_macro_count_respect_the_supported_limits(self):
        # scripts/stage_plan.py supports at most 6 stages and 4 macros per stage. The report is
        # deliberately NOT a stage: it is generator output, so the limits are not widened for it.
        self.assertLessEqual(len(self.pipeline['stages']), 6)
        for stage in self.pipeline['stages']:
            self.assertTrue(1 <= len(stage['macros']) <= 4, stage['name'])
        self.assertNotIn('generate_run_report.py', json.dumps(self.pipeline['stages']))

    def test_the_pipeline_declares_the_report_generator_as_a_reviewed_support_file(self):
        import air3b_pipeline_trial as trial
        support = trial.support_hashes()
        self.assertIn('scripts/generate_run_report.py', support)
        self.assertEqual(support['scripts/generate_run_report.py'],
                         __import__('hashlib').sha256(REPORT_GENERATOR.read_bytes()).hexdigest())

    def test_the_entry_command_is_itself_hash_bound(self):
        # Independent round-10 review, condition 3: the driver decides which pipeline and which inputs
        # are submitted. Leaving it out of SUPPORT_FILES meant an edit could change what ran while the
        # review gate stayed green - the "recorded but never compared" defect, one file over.
        import air3b_pipeline_trial as trial
        support = trial.support_hashes()
        key = 'scripts/run_static_external_flow_e2e.py'
        self.assertIn(key, support)
        self.assertEqual(support[key],
                         __import__('hashlib').sha256((ROOT / key).read_bytes()).hexdigest())

    def test_the_retired_two_stage_manifest_cannot_be_submitted_by_accident(self):
        # Round 10 retired cases/static-sphere-external-flow/pipeline-solve-validation.json: its bytes no
        # longer match the round-9 review record (the macro pin and the _budget_note both changed) and
        # the e2e pipeline replaces it. Retirement has to be visible in the file itself, because the
        # review gate would otherwise report a mismatch that looks like tampering.
        retired = json.loads(SOLVE_PIPELINE.read_text(encoding='utf-8'))
        self.assertEqual('SUPERSEDED', retired['status'])
        self.assertIn('pipeline-e2e-geometry-to-validation.json', retired['_superseded_by'])
        self.assertIn('FROZEN HISTORY, NOT A SUBMISSION', retired['_superseded_note'])
        e2e = json.loads(E2E_PIPELINE.read_text(encoding='utf-8'))
        self.assertNotEqual(e2e['pipeline_id'], retired['pipeline_id'])

    WRITERS = {
        'audit-body': ('AuditStaticBodyGeometry.java',),
        'build-domain': ('BuildExternalDomain.java',),
        'region-physics-mesh': ('BuildExternalFlowRegion.java', 'ConfigureExternalFlowPhysics.java',
                                'BuildExternalFlowMesh.java'),
        'mesh-quality-audit': ('AuditExternalFlowMesh.java',),
        'solve': ('RunExternalFlowSolve.java',),
        'validation': ('AuditExternalFlowConvergence.java',),
    }

    # A real record of each kind, where one exists in the repository. 25 of the 27 content pins are
    # GROUNDED here - the exact bytes are known to have been emitted by a successful run. The two
    # build-domain pins have no record anywhere (that file name has never been produced), which is
    # exactly why the first two round-10 revisions could pin bytes the macro cannot write.
    GROUNDED = {
        'static_body_audit.json': ('test-evidence/static-external-flow/audit-z1p5/'
                                   's1-static-sphere-audit-z1p5-20261003a/static_body_audit.json'),
        'external_flow_region_probe.json': ('test-evidence/static-external-flow/'
                                            'round7-region-physics-mesh/'
                                            's1-static-sphere-region-physics-mesh-20261003b/'
                                            'external_flow_region_probe.json'),
        'external_flow_region.json': ('test-evidence/static-external-flow/round7-region-physics-mesh/'
                                      's1-static-sphere-region-physics-mesh-20261003b/'
                                      'external_flow_region.json'),
        'external_flow_physics.json': ('test-evidence/static-external-flow/round7-region-physics-mesh/'
                                       's1-static-sphere-region-physics-mesh-20261003b/'
                                       'external_flow_physics.json'),
        'external_flow_mesh.json': ('test-evidence/static-external-flow/round7-region-physics-mesh/'
                                    's1-static-sphere-region-physics-mesh-20261003b/'
                                    'external_flow_mesh.json'),
        'external_flow_mesh_quality.json': ('test-evidence/static-external-flow/'
                                            'round7-region-physics-mesh/'
                                            's1-static-sphere-region-physics-mesh-20261003b/'
                                            'external_flow_mesh_quality.json'),
        'external_flow_solve.json': ('test-evidence/static-external-flow/round9-solve-validation/'
                                     's1-static-sphere-solve-validation-20261003d/'
                                     'external_flow_solve.json'),
        'external_flow_validation.json': ('test-evidence/static-external-flow/round9-solve-validation/'
                                          's1-static-sphere-solve-validation-20261003d/'
                                          'external_flow_validation.json'),
    }

    @staticmethod
    def emitted_stream(macro_names):
        """A sanitised stream of every Java string literal a macro can write.

        Independent round-10 review, second revision, blocker: the previous version of this test only
        checked that the KEY NAME appeared in the macro, so it passed for both
        `external_domain_build.json::"status":"ok"` and `"status": "ok"` while the runner's ordinal
        substring test accepted only the spaced form BuildExternalDomain.writeEvidence really emits. The
        stage would have failed on Windows AFTER the macro succeeded and stages 3-6 would never run.

        Reconstruction, not source grep: take every Java string literal (comments removed first, so a
        literal in a comment cannot satisfy a pin), unescape it the way the JVM would, join the pieces,
        then keep only JSON-ish characters. Concatenations with variables are handled because the literals
        on both sides survive and the variable part is simply removed from both the stream and the pinned
        fragment. This catches exactly the spacing/punctuation class of defect.
        """
        import re
        pieces = []
        for name in macro_names:
            source = (ROOT / 'macros' / name).read_text(encoding='utf-8')
            source = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
            source = re.sub(r'//[^\n]*', '', source)
            for literal in re.findall(r'"((?:[^"\\]|\\.)*)"', source):
                pieces.append(literal.replace('\\"', '"').replace('\\\\', '\\')
                              .replace('\\n', '\n').replace('\\t', '\t'))
        return TheEndToEndPipelineRunsFromTheGeometryOnlySim.sanitised('\n'.join(pieces))

    @staticmethod
    def sanitised(fragment):
        # A regex character class, not a set of literal characters: `'0-9'` inside a plain string is
        # three characters, not a range, and an earlier revision of this helper silently dropped every
        # digit and most letters while still looking like a filter. The SPACE is kept on purpose - it is
        # the difference the build-domain pin encodes.
        import re
        return re.sub(r'[^A-Za-z0-9 {}\[\]\",:.eE+\-_]', '', fragment)

    @classmethod
    def literal_pieces(cls, fragment):
        """The parts of a pin fragment that MUST be string literals in the macro.

        A pin like `"status": "ok"` is emitted as a literal `"status": "` followed by a runtime value, so
        the runtime value cannot be demanded from the source. What CAN be demanded is every quoted piece:
        those bytes come from literals, and if any of them is missing - or spelled with different
        spacing - the runner's ordinal substring test fails after the macro has already succeeded. That
        was the round-10 review's blocker, so this is the check that has to be able to see it.
        """
        pieces = [piece for piece in fragment.split('"') if piece]
        return [piece for piece in pieces if any(ch.isalnum() or ch in '_-.,:' for ch in piece)]

    def test_every_declared_literal_is_grounded_in_bytes_some_macro_can_emit(self):
        """Each content pin must be satisfied by either a REAL record of that kind, or - when no record
        exists - the text the owning macro's own string literals can produce.
        """
        grounded = recordless = 0
        for stage in self.pipeline['stages']:
            emitted = self.emitted_stream(self.WRITERS[stage['name']])
            for literal in stage.get('require_contains') or []:
                record_name, fragment = literal.split('::', 1)
                sample = self.GROUNDED.get(record_name)
                if sample is not None:
                    text = (ROOT / sample).read_text(encoding='utf-8-sig')
                    if fragment in text:
                        # The exact bytes are known to have been produced by a successful run.
                        grounded += 1
                        continue
                    # The sample predates the pin (the round-9 validation record was written before the
                    # round-10 drag-report verification existed). That is not evidence the pin is wrong,
                    # so fall through to the source check - which for THIS pin is the only evidence
                    # there is, and is labelled as such in the pipeline's residual_limitations.
                    self.assertGreater(len(text), 0, sample)
                # No record of this artefact exists anywhere in the repository, so the pin is only
                # checkable against what the macro can write - and that check is mandatory.
                pieces = self.literal_pieces(fragment)
                self.assertTrue(pieces, 'pin %r has no literal piece to check' % fragment)
                for piece in pieces:
                    self.assertIn(piece, emitted,
                                  'stage %s requires the exact bytes %r, but no real record of %s '
                                  'exists AND the owning macro cannot emit the literal %r; the stage '
                                  'would fail on Windows after the macro succeeded'
                                  % (stage['name'], fragment, record_name, piece))
                recordless += 1
        # 24 pins are grounded in the exact bytes of a real record; the other 3 (both build-domain pins
        # plus the round-10 drag_report_direction_unit, whose only sample predates it) are checked
        # against the macro's emittable literals instead. Both counts are asserted so that neither check
        # can be quietly dropped.
        self.assertGreaterEqual(grounded, 24, grounded)
        self.assertGreaterEqual(recordless, 3, recordless)

    def test_the_literal_check_would_catch_a_one_space_regression(self):
        # Mutation-checked here rather than only in prose: the two spellings differ by exactly one space,
        # and the pieces of the spaced pin are in the stream while the pieces of the unspaced pin are not.
        stream = self.emitted_stream(self.WRITERS['build-domain'])
        for piece in self.literal_pieces('"status": "ok"'):
            self.assertIn(piece, stream, piece)
        # And the provenance of the check: the macro's own literal is the SPACED spelling, so a pin
        # written without the space can never be a substring of what the macro emits.
        self.assertIn('"status": "', stream)
        self.assertNotIn('"status":"', stream)

    def test_the_build_domain_pins_carry_the_spacing_the_macro_emits(self):
        stage = [s for s in self.pipeline['stages'] if s['name'] == 'build-domain'][0]
        self.assertIn('external_domain_build.json::"status": "ok"', stage['require_contains'])
        self.assertIn('external_domain_build.json::"complete_marker": "EXTERNAL_DOMAIN_BUILD_COMPLETE"',
                      stage['require_contains'])
        emitted = self.emitted_stream(self.WRITERS['build-domain'])
        for piece in self.literal_pieces('"status": "ok"') + \
                self.literal_pieces('"complete_marker": "EXTERNAL_DOMAIN_BUILD_COMPLETE"'):
            self.assertIn(piece, emitted, piece)


    def test_every_numeric_gate_names_a_key_its_record_writer_produces(self):
        # Same defect class as above, for require_numeric: the controller reads the key out of the
        # artefact, so a key the record never contains is a gate that can never pass. Each stage's first
        # required artefact is checked against a REAL record of that kind already in the repository.
        import re as _re
        samples = {
            'static_body_audit.json': ROOT / 'test-evidence' / 'static-external-flow' / 'audit-z1p5' \
                / 's1-static-sphere-audit-z1p5-20261003a' / 'static_body_audit.json',
            'external_domain_build.json': ROOT / 'test-evidence' / 'static-external-flow' \
                / 'domain-build.json',
            'external_flow_region_probe.json': ROOT / 'test-evidence' / 'static-external-flow' \
                / 'round7-region-physics-mesh' / 's1-static-sphere-region-physics-mesh-20261003b' \
                / 'external_flow_region_probe.json',
            'external_flow_region.json': ROOT / 'test-evidence' / 'static-external-flow' \
                / 'round7-region-physics-mesh' / 's1-static-sphere-region-physics-mesh-20261003b' \
                / 'external_flow_region.json',
            'external_flow_mesh.json': ROOT / 'test-evidence' / 'static-external-flow' \
                / 'round7-region-physics-mesh' / 's1-static-sphere-region-physics-mesh-20261003b' \
                / 'external_flow_mesh.json',
            'external_flow_mesh_quality.json': ROOT / 'test-evidence' / 'static-external-flow' \
                / 'round7-region-physics-mesh' / 's1-static-sphere-region-physics-mesh-20261003b' \
                / 'external_flow_mesh_quality.json',
            'external_flow_solve.json': ROOT / 'test-evidence' / 'static-external-flow' \
                / 'round9-solve-validation' / 's1-static-sphere-solve-validation-20261003d' \
                / 'external_flow_solve.json',
            'external_flow_validation.json': ROOT / 'test-evidence' / 'static-external-flow' \
                / 'round9-solve-validation' / 's1-static-sphere-solve-validation-20261003d' \
                / 'external_flow_validation.json',
        }
        required_names = {name for stage in self.pipeline['stages'] for name in stage['required']}
        checked = 0
        skipped = []
        for stage in self.pipeline['stages']:
            for spec in stage.get('require_numeric') or []:
                match = _re.match(r'^([^:]+)::([A-Za-z0-9_.]+)(>=|<=)', spec)
                self.assertIsNotNone(match, spec)
                record_name, key = match.group(1), match.group(2)
                self.assertIn(record_name, required_names,
                              '%s is not a declared artifact of any stage' % record_name)
                if record_name not in samples:
                    # A real record of this kind has not been kept in the repository yet (the physics
                    # record is one). The gate is still pin-able, but a key check needs a real producer
                    # sample; skipping is honest, pretending is not.
                    skipped.append('%s::%s' % (record_name, key))
                    continue
                payload = json.loads(samples[record_name].read_text(encoding='utf-8-sig'))
                self.assertIn(key, payload,
                              'stage %s gates on %s::%s, which that record does not contain'
                              % (stage['name'], record_name, key))
                checked += 1
        self.assertGreaterEqual(checked, 14, checked)
        self.assertEqual([], [entry for entry in skipped if entry.startswith('static_body')], skipped)


class TheEntryCommandIsCaseAgnostic(unittest.TestCase):
    """Round 11: the entry command and the report generator must not be welded to one case directory.

    The round-10 entry script hard-coded `cases/static-sphere-external-flow/...` and the report generator
    hard-coded the six stage names and the record->stage map. Both now take the case from parameters and
    from the pipeline the run actually submitted, so a second case is a new directory plus a new
    pipeline, not an edit to the tooling.
    """

    def test_the_driver_takes_the_pipeline_and_inputs_as_arguments(self):
        # Behaviour-level, not a source grep: run the real argparse setup with explicit arguments and a
        # stub backend, and assert that start() received exactly the paths that were passed. The round-11
        # review mutation-tested the previous source-text version and it SURVIVED (the docstring still
        # mentioned the flags), so the guard has to exercise the parser.
        import contextlib
        import importlib.util as _ilu
        import io
        import tempfile
        import shutil
        import sys as _sys
        spec = _ilu.spec_from_file_location('driver', DRIVER)
        driver = _ilu.module_from_spec(spec)
        spec.loader.exec_module(driver)
        self.assertEqual(driver.DEFAULT_PIPELINE, E2E_PIPELINE)
        self.assertEqual(driver.DEFAULT_INPUTS,
                         ROOT / 'cases' / 'static-sphere-external-flow' / 'e2e-inputs.json')

        _sys.path.insert(0, str(ROOT / 'scripts'))
        import air3b_pipeline_trial as trial
        calls = {}
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        pipeline = tmp / 'pipe.json'
        pipeline.write_text(json.dumps({'pipeline_id': 'unit', 'input_hashes': {}, 'macro_hashes': {},
                                        'stages': [{'name': 's', 'macros': ['A.java'], 'input': None,
                                                    'output': None, 'marker': 'M',
                                                    'timeout_seconds': 60, 'required': []}]}),
                           encoding='utf-8')
        inputs = tmp / 'inputs.json'
        inputs.write_text(json.dumps({'inputs': ['cases/static-sphere-external-flow/case_spec.json']}),
                          encoding='utf-8')
        original_start, original_backend = trial.start, _sys.modules.get('backend')
        class StubBackend:
            def __init__(self, *a, **k):
                pass
        try:
            trial.start = lambda backend, trial_id, pipeline_path, steps, evidence: (
                calls.update(pipeline=str(pipeline_path), steps=[str(s) for s in steps],
                             evidence=str(evidence)),
                {'macro_plan': [{'name': 's'}], 'job_dir': 'X'})[1]
            _sys.modules['backend'] = type('backend', (), {'Backend': StubBackend})
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                driver.main.__wrapped__ if False else None
                _argv = _sys.argv
                _sys.argv = ['driver', '--trial-id', 'unit-test-1234', '--evidence', str(tmp / 'ev'),
                             '--pipeline', str(pipeline), '--inputs', str(inputs),
                             '--report-only']
                try:
                    driver.main()
                finally:
                    _sys.argv = _argv
        finally:
            trial.start = original_start
            if original_backend is not None:
                _sys.modules['backend'] = original_backend
            else:
                _sys.modules.pop('backend', None)
        # --report-only must not call start at all; that is itself the guarantee that the report path is
        # usable without a submission. The explicit-path call is checked next.
        self.assertEqual({}, calls, '--report-only must not submit anything')

        calls.clear()
        try:
            trial.start = lambda backend, trial_id, pipeline_path, steps, evidence: (
                calls.update(pipeline=str(pipeline_path), steps=[str(s) for s in steps],
                             evidence=str(evidence)),
                {'macro_plan': [{'name': 's'}], 'job_dir': 'X'})[1]
            _sys.modules['backend'] = type('backend', (), {'Backend': StubBackend})
            polled = {}
            original_poll = trial.poll
            trial.poll = lambda backend, trial_id, evidence: (
                polled.update(trial_id=trial_id, evidence=str(evidence)),
                {'state': 'FAILED', 'engineering_verified': False})[1]
            _sys.modules['backend'] = type('backend', (), {'Backend': StubBackend})
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    _argv = _sys.argv
                    _sys.argv = ['driver', '--trial-id', 'unit-test-1234', '--evidence', str(tmp / 'ev'),
                                 '--pipeline', str(pipeline), '--inputs', str(inputs)]
                    try:
                        driver.main()
                    finally:
                        _sys.argv = _argv
            finally:
                trial.poll = original_poll
        finally:
            trial.start = original_start
            if original_backend is not None:
                _sys.modules['backend'] = original_backend
            else:
                _sys.modules.pop('backend', None)
        self.assertEqual(str(pipeline), calls.get('pipeline'))
        # The driver resolves repository-relative paths to absolute ones before uploading.
        self.assertEqual([str(ROOT / 'cases/static-sphere-external-flow/case_spec.json')],
                         calls.get('steps'))
        self.assertIn('unit-test-1234', polled.get('trial_id', ''))

    def test_the_report_generator_derives_the_stage_list_from_the_run(self):
        source = REPORT_GENERATOR.read_text(encoding='utf-8')
        # Display names may be a lookup table, but the SET of stages must come from the submitted config.
        self.assertIn('def run_view(cfg)', source)
        self.assertIn('def record_owner(required_by_stage: dict) -> dict:', source)
        self.assertIn("for stage in cfg.get('stages') or []", source)
        self.assertIn('def stage_title(name: str) -> str:', source)
        # No case name may be assumed when writing the report.
        self.assertNotIn('static-sphere-external-flow', source)
        self.assertNotIn('静止物体空气外流', source.split('def main()')[0])

    def test_a_one_stage_run_reports_without_pretending_to_be_the_full_chain(self):
        # The counter-example submission has a single stage. The report must describe it as such instead
        # of claiming five stages are missing.
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location('report_mod', REPORT_GENERATOR)
        report_mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(report_mod)
        names, required = report_mod.run_view({'stages': [{'name': 'audit-body',
                                                           'required': ['static_body_audit.json']}]})
        self.assertEqual(['audit-body'], names)
        self.assertEqual({'audit-body': ['static_body_audit.json']}, required)
        self.assertEqual({'static_body_audit.json': ['audit-body']},
                         report_mod.record_owner(required))
        self.assertEqual('几何审计（只读）', report_mod.stage_title('audit-body'))
        self.assertEqual('`a-new-stage`', report_mod.stage_title('a-new-stage'))


class TheRenameVariantIsARealRename(unittest.TestCase):
    """The variant must differ from the baseline in STAR OBJECT NAMES and nothing else.

    A file renamed on disk is not an object rename. This class pins that the variant CaseSpec changes
    body_part_name, region_name and body_wall_boundary_name (and the boundary_roles that mirror them) and
    leaves every dimension, position, unit, flow condition and mesh parameter byte-identical in value.
    """

    def setUp(self):
        self.canon = json.loads((ROOT / 'cases' / 'static-sphere-external-flow'
                                 / 'case_spec.json').read_text(encoding='utf-8'))
        self.pos = json.loads((RENAME_BASE / 'positive' / 'case_spec.json').read_text(encoding='utf-8'))
        self.neg = json.loads((RENAME_BASE / 'negative' / 'case_spec.json').read_text(encoding='utf-8'))

    def test_the_object_names_actually_change(self):
        self.assertEqual('BODY', self.canon['geometry']['body_part_name'])
        self.assertEqual('RENAMED_BODY', self.pos['geometry']['body_part_name'])
        self.assertEqual(['RENAMED_BODY'], self.pos['geometry']['body_parts'])
        self.assertEqual('AUTO_EXTERNAL_FLOW', self.canon['region_name'])
        self.assertEqual('CASE11_REGION', self.pos['region_name'])
        self.assertEqual('AUTO_BODY_WALL', self.canon['body_wall_boundary_name'])
        self.assertEqual('CASE11_WALL', self.pos['body_wall_boundary_name'])
        self.assertEqual({'region': 'CASE11_REGION', 'inlet': 'AUTO_INLET', 'outlet': 'AUTO_OUTLET',
                          'body_wall': 'CASE11_WALL'}, self.pos['boundary_roles'])

    def test_everything_that_is_not_a_name_is_unchanged(self):
        for section in ('domain', 'flow', 'numerics', 'quality', 'geometry'):
            for key, value in self.canon[section].items():
                if key in ('body_part_name', 'body_parts', 'existing_wall_roles',
                           'existing_wall_roles_basis'):
                    continue
                self.assertEqual(value, self.pos[section].get(key),
                                 '%s.%s differs from the baseline' % (section, key))
        for key in ('mesh_base_size_m', 'mesh_body_target_size_m', 'mesh_body_minimum_size_m',
                    'mesh_prism_layers', 'medium', 'model_length_unit', 'scenario'):
            self.assertEqual(self.canon[key], self.pos[key], key)
        self.assertEqual(self.canon['engineering_target'], self.pos['engineering_target'])

    def test_the_counter_example_uses_the_same_geometry_but_the_wrong_declared_name(self):
        # Same file, same flow conditions: only the declared part name is wrong, which is exactly the
        # pairing error the audit must refuse.
        self.assertEqual('BODY', self.neg['geometry']['body_part_name'])
        self.assertEqual(self.pos['flow'], self.neg['flow'])
        self.assertEqual(self.pos['domain'], self.neg['domain'])
        self.assertEqual(self.pos['quality'], self.neg['quality'])
        self.assertEqual(self.pos['mesh_prism_layers'], self.neg['mesh_prism_layers'])

    def test_the_variant_pipelines_are_stage_identical_to_the_baseline(self):
        # Same six stages, same macros, same markers and every gate -- a variant must not become a place
        # where the gate quietly changes. The stage INPUT names are the one legitimate difference: the
        # variant's geometry-only checkpoint is a different file. Round-11 review blocker 1 was this test
        # comparing the whole stage dict, which failed on that designed difference.
        baseline = json.loads(E2E_PIPELINE.read_text(encoding='utf-8'))
        variant = json.loads(RENAME_E2E.read_text(encoding='utf-8'))
        self.assertEqual(len(baseline['stages']), len(variant['stages']))
        for a, b in zip(baseline['stages'], variant['stages']):
            for key in a:
                if key in ('input', 'description'):
                    continue          # input: the variant names its own geometry; description: prose
                self.assertEqual(a[key], b[key], '%s/%s' % (a['name'], key))
            self.assertEqual(a['input'].replace('static_body_variant_', 'static_body_'),
                             b['input'].replace('static_body_variant_', 'static_body_'),
                             a['name'])
        for key in ('macro_hashes', 'env', 'logical_cores', 'stage_timeout_seconds'):
            self.assertEqual(baseline[key], variant[key], key)
        self.assertNotEqual(baseline['pipeline_id'], variant['pipeline_id'])
        self.assertEqual({'case_spec.json', 'static_body_variant_geometry.sim'},
                         set(variant['input_hashes']))

    def test_the_negative_pipeline_is_a_single_read_only_stage_on_the_same_geometry(self):
        negative = json.loads(RENAME_NEGATIVE.read_text(encoding='utf-8'))
        plan = plan_pipeline(negative)
        self.assertEqual(['audit-body'], [entry['name'] for entry in plan['stages']])
        self.assertEqual('static_body_variant_geometry.sim', plan['stages'][0]['input'])
        self.assertEqual('AuditStaticBodyGeometry.java', plan['stages'][0]['steps'][0]['macro'])
        # It must NOT declare success markers or success content: the expected outcome is a refusal.
        self.assertEqual([], negative['stages'][0].get('require_contains') or [])
        self.assertEqual([], negative['stages'][0].get('require_numeric') or [])
        self.assertIn('does NOT certify counter-examples for any downstream operation',
                      negative['description'])

    # Real records kept in the repository, for the pipelines this round adds: the build-body record is
    # the byte-level ground truth for the geometry-build pins, so those pins are checked against bytes a
    # successful run really produced rather than against the macro's source alone.
    GROUNDED_BUILD_BODY = ('test-evidence/static-external-flow/build-body-positive/'
                           's1-static-sphere-body-20261003a/static_body_geometry.json')
    GROUNDED_AUDIT = ('test-evidence/static-external-flow/audit-z1p5/'
                      's1-static-sphere-audit-z1p5-20261003a/static_body_audit.json')

    def test_every_variant_pin_is_grounded_in_real_bytes(self):
        """A pin is satisfiable if its quoted pieces come from a real record, the macro, or the input spec.

        Same defect class the round-10 review found on the baseline pipeline (a pin the owning macro
        cannot emit, which fails the stage on Windows AFTER the macro succeeded). The variant adds one new
        kind of ground truth: a value the CaseSpec supplies into the record, e.g. RENAMED_BODY. That is a
        byte the run can write, and it comes from the input this pipeline pins, so it is checked there.
        """
        cases = [
            (RENAME_GEOMETRY, self.GROUNDED_BUILD_BODY, RENAME_BASE / 'positive' / 'e2e-inputs.json'),
            (RENAME_NEGATIVE, self.GROUNDED_AUDIT, RENAME_BASE / 'negative' / 'e2e-inputs.json'),
        ]
        for path, sample, inputs_path in cases:
            doc = json.loads(path.read_text(encoding='utf-8'))
            emitted = TheEndToEndPipelineRunsFromTheGeometryOnlySim.emitted_stream(
                tuple(doc['macro_hashes']))
            sample_text = (ROOT / sample).read_text(encoding='utf-8-sig')
            spec_text = ''
            for rel in json.loads(inputs_path.read_text(encoding='utf-8'))['inputs']:
                candidate = ROOT / rel
                if candidate.suffix == '.json' and candidate.is_file():
                    spec_text += candidate.read_text(encoding='utf-8')
            grounded = 0
            for stage in doc['stages']:
                for literal in stage.get('require_contains') or []:
                    fragment = literal.split('::', 1)[1]
                    if fragment in sample_text:
                        grounded += 1
                        continue
                    pieces = [piece for piece in fragment.split('"')
                              if piece and any(ch.isalnum() or ch in '_-.,:' for ch in piece)]
                    self.assertTrue(pieces, fragment)
                    for piece in pieces:
                        where = [name for name, text in (('record', sample_text), ('macro', emitted),
                                                         ('spec', spec_text)) if piece in text]
                        self.assertTrue(where,
                                        '%s pins %r and no ground truth can produce the literal %r'
                                        % (path.name, fragment, piece))
            if isinstance(doc['stages'][0].get('require_contains'), list) and \
                    doc['stages'][0]['require_contains']:
                # A pipeline that DOES pin content must have at least one pin grounded in real bytes.
                self.assertGreaterEqual(grounded, 1, path.name)

    def test_every_declared_stage_output_is_a_name_the_pinned_macro_really_writes(self):
        """Round-11 review blocker A, pinned.

        The geometry pipeline declared `static_body_variant_geometry.sim` while the pinned macro hard-codes
        `OUTPUT_SIM = "static_body_geometry.sim"`. The runner fails that stage AFTER the macro succeeded
        ("declared output ... but produced no such file"), so the whole chain never runs. No test caught it,
        because the pin-grounding test only inspected require_contains strings. This checks the OUTPUT names
        against the .sim literals the macro can emit.
        """
        import re
        cases = [
            (RENAME_GEOMETRY, ('BuildStaticBodyGeometry.java',)),
            (RENAME_E2E, tuple(json.loads(E2E_PIPELINE.read_text(encoding='utf-8'))['macro_hashes'])),
            (RENAME_NEGATIVE, ('AuditStaticBodyGeometry.java',)),
            (E2E_PIPELINE, tuple(json.loads(E2E_PIPELINE.read_text(encoding='utf-8'))['macro_hashes'])),
        ]
        for path, macros in cases:
            doc = json.loads(path.read_text(encoding='utf-8'))
            emitted = TheEndToEndPipelineRunsFromTheGeometryOnlySim.emitted_stream(tuple(macros))
            # The emitted stream is a concatenation of literals, so a marker printed immediately before a
            # file name appears glued to it. Peel the longest trailing match instead of trying to guess a
            # boundary: every .sim literal in these macros ends with the file name and starts after a
            # literal boundary, so take the suffix that IS writable.
            writable = set(re.findall(r'[A-Za-z0-9_-]+\.sim', emitted))
            extended = set()
            for token in writable:
                for cut in range(len(token)):
                    suffix = token[cut:]
                    if suffix.endswith('.sim') and len(suffix) > 4:
                        extended.add(suffix)
            writable = extended
            for stage in doc['stages']:
                declared = [x for x in (stage.get('macro_outputs') or []) if x]
                if stage.get('output'):
                    declared.append(stage['output'])
                for name in declared:
                    if not name.endswith('.sim'):
                        continue
                    # A stage that CONSUMES a .sim may pass it straight through to the next macro, in
                    # which case the name is legitimate without a macro literal (the runner verifies it
                    # as the stage input). Only names the stage PRODUCES must be writable.
                    consumed = {step.get('input') for step in
                                [{'input': stage.get('input')}]} | {stage.get('input')}
                    if name in consumed and name not in writable:
                        continue
                    self.assertIn(name, writable,
                                  '%s/%s declares the output %r, but the pinned macro can only write %s'
                                  % (path.name, stage['name'], name, sorted(writable)))

    def test_no_new_macro_was_added_for_the_variant(self):
        # The round's rule: reuse existing operations. The variant uses BuildStaticBodyGeometry and
        # AuditStaticBodyGeometry, both of which already existed and were already compiled/reviewed.
        for path in (RENAME_GEOMETRY, RENAME_E2E, RENAME_NEGATIVE):
            doc = json.loads(path.read_text(encoding='utf-8'))
            self.assertLessEqual(set(doc['macro_hashes']),
                                 {'BuildStaticBodyGeometry.java', 'AuditStaticBodyGeometry.java'} |
                                 set(json.loads(E2E_PIPELINE.read_text(encoding='utf-8'))['macro_hashes']))


class TheR9CarryOversAreFixedAndPinned(unittest.TestCase):
    """Round 9's independent review left two concerns for this round; each one becomes a test (O9).

    (a) the validation macro reused stage 1's saved drag report BY NAME without re-verifying its parts
        or its direction; (b) the solve pipeline's _budget_note contradicted the numbers it cited.
    """

    def test_the_budget_note_and_the_retired_manifest_agree(self):
        retired = json.loads(SOLVE_PIPELINE.read_text(encoding='utf-8'))
        note = retired['_budget_note']
        self.assertIn('first limit reached is therefore the macro budget', note)
        self.assertEqual(retired['declared_stage_budget_seconds'] + 60,
                         retired['scheduled_task_limit_seconds'] - 60)

    def test_the_audit_adopts_a_report_only_after_verifying_parts_and_direction(self):
        audit = (ROOT / 'macros' / 'AuditExternalFlowConvergence.java').read_text(encoding='utf-8')
        self.assertIn('private static boolean dragReportVerified(ForceReport report, Boundary wall)',
                      audit)
        self.assertIn('report.getParts().getObjects()', audit)
        self.assertIn('report.getDirection().getVector().toDoubleArray()', audit)
        self.assertIn('&& dragReportVerified((ForceReport) candidate, wall)', audit)
        # The verification must be part of the ADOPTION decision, not merely printed afterwards.
        adoption = audit.index('&& dragReportVerified((ForceReport) candidate, wall)')
        self.assertLess(adoption, audit.index('double force = drag.getReportMonitorValue();'))
        # A report that fails verification must be replaced, not trusted; and the record must say which
        # one was used.
        self.assertIn('boolean dragPartsVerified = false;', audit)
        self.assertIn('drag_report_parts_verified', audit)
        self.assertIn('+ dragPartsVerified', audit)
        self.assertIn('drag_report_direction_unit', audit)

    def test_the_pipeline_pins_the_drag_direction_it_expects(self):
        pipeline = json.loads(E2E_PIPELINE.read_text(encoding='utf-8'))
        contents = ' '.join(spec for stage in pipeline['stages']
                            for spec in (stage.get('require_contains') or []))
        self.assertIn('drag_report_direction_unit', contents)
        self.assertIn('1,0,0', contents)



class TheReportGeneratorPublishesHashesAndAFailureReport(unittest.TestCase):
    """Round 10: the Chinese report is generated from evidence, not written by hand.

    The round-9 report was typed after the run, so nothing could check that it matched the records. This
    class pins the two properties that make the generated one different: it hashes itself, and it still
    produces a report when the run failed.
    """

    def run_generator(self, evidence, trial_id, extra=()):
        import subprocess
        return subprocess.run([sys.executable, str(REPORT_GENERATOR), '--trial-id', trial_id,
                               '--evidence', str(evidence), *extra],
                              capture_output=True, text=True)

    def setUp(self):
        import shutil
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.trial_id = 'unit-test-report-1234'

    def write_failed_run(self):
        (self.tmp / (self.trial_id + '-result.json')).write_text(json.dumps(
            {'state': 'FAILED', 'error': 'Owned STAR exit code 1', 'engineering_verified': False}),
            encoding='utf-8')
        trial = self.tmp / self.trial_id
        trial.mkdir()
        (trial / 'stage-solve.json').write_text(json.dumps(
            {'stage': 'solve', 'state': 'failed', 'error': 'boom', 'macros': [], 'artifacts': []}),
            encoding='utf-8-sig')

    def test_a_failed_run_still_gets_a_report_that_says_so(self):
        self.write_failed_run()
        result = self.run_generator(self.tmp, self.trial_id)
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual('REPORT_RUN_FAILED', payload['verdict'])
        report = Path(payload['report'])
        self.assertTrue(report.is_file())
        text = report.read_text(encoding='utf-8')
        self.assertIn('REPORT_RUN_FAILED', text)
        self.assertIn('Owned STAR exit code 1', text)

    def test_the_report_writes_its_own_hash_and_an_evidence_index(self):
        self.write_failed_run()
        payload = json.loads(self.run_generator(self.tmp, self.trial_id).stdout)
        sums = Path(payload['hashes']).read_text(encoding='utf-8').strip().splitlines()
        self.assertEqual(3, len(sums))
        import hashlib
        for line in sums:
            digest, name = line.split('  ', 1)
            self.assertEqual(digest, hashlib.sha256((self.tmp / name).read_bytes()).hexdigest(), name)
        index = json.loads(Path(payload['evidence_index']).read_text(encoding='utf-8'))
        # Keys are relative to the trial directory the runner wrote (flat layout), which is where the
        # downloaded artefacts live.
        entry = index['stage-solve.json']
        self.assertEqual(64, len(entry['sha256']))
        self.assertIn('citation_state', entry)

    def test_strict_mode_exits_nonzero_so_a_caller_can_chain_on_it(self):
        self.write_failed_run()
        self.assertEqual(2, self.run_generator(self.tmp, self.trial_id, ('--strict',)).returncode)

    STAGE_REQUIRED = {
        'audit-body': ('static_body_audit.json', 'static_body_tessellation.stl'),
        'build-domain': ('external_domain_build.json', 'external_domain_built.sim'),
        'region-physics-mesh': ('external_flow_region.json', 'external_flow_region_probe.json',
                                'external_flow_physics.json', 'external_flow_mesh.json',
                                'external_flow_region.sim', 'external_flow_physics.sim',
                                'external_flow_meshed.sim'),
        'mesh-quality-audit': ('external_flow_mesh_quality.json',),
        'solve': ('external_flow_solve.json', 'external_flow_residuals.tsv',
                  'external_flow_solved.sim'),
        'validation': ('external_flow_validation.json',
                       'external_flow_validation_residuals.tsv'),
    }

    def write_config(self, stage_names=None, required_by_stage=None):
        """The trial config, exactly as the controller writes it.

        Round-11 review blocker 2 was that every generator test omitted this file, so the config-driven
        path was never exercised and the generator still judged against six hard-coded stage names.
        """
        by_stage = required_by_stage or {name: list(self.STAGE_REQUIRED[name])
                                         for name in (stage_names or self.STAGE_REQUIRED)}
        doc = {'pipeline_id': 'unit-test-pipeline', 'case_label': '单元测试案例',
               'stages': [{'name': name, 'required': list(by_stage[name])} for name in by_stage]}
        (self.tmp / (self.trial_id + '-config.json')).write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
        return doc

    def write_delivery(self, **overrides):
        """The checkpoint delivery record the entry command writes after a transfer.

        A real round-12 run ALWAYS leaves one of these, because `run_static_external_flow_e2e.py` writes it
        before it calls the report generator. The happy-path fixtures therefore write one too; the
        round-12 tests below delete it or falsify it on purpose, which is the counter-example direction.
        """
        cfg = json.loads((self.tmp / (self.trial_id + '-config.json')).read_text(encoding='utf-8'))
        sims = sorted({name for stage in cfg.get('stages') or []
                       for name in (stage.get('required') or [])
                       if str(name).lower().endswith('.sim')})
        artifacts = [{'name': name, 'recorded_sha256': 'a' * 64, 'local_sha256': 'a' * 64,
                      'bytes': 1024, 'status': 'verified', 'declared_by': ['stage-record.json']}
                     for name in sims]
        final = None
        if artifacts:
            final = {'name': artifacts[-1]['name'], 'sha256': artifacts[-1]['local_sha256'],
                     'path': str(self.tmp / self.trial_id / artifacts[-1]['name']),
                     'status': 'verified'}
        doc = {'schema_version': 1, 'trial_id': self.trial_id,
               'solve_state': 'AIR3B_PIPELINE_COMPLETE', 'solve_completed': True,
               'numeric_gate_passed': True, 'runner_records_present': True,
               'download_succeeded': True,
               'checkpoint_delivered': bool(artifacts), 'final_sim': final,
               'artifacts': artifacts, 'failures': []}
        doc.update(overrides)
        (self.tmp / (self.trial_id + '-checkpoints.json')).write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
        return doc

    def write_six_stage_run(self, records=None):
        """A complete six-stage trial directory, so the generator's happy path is exercised."""
        trial = self.tmp / self.trial_id
        trial.mkdir()
        (self.tmp / (self.trial_id + '-result.json')).write_text(json.dumps(
            {'state': 'AIR3B_PIPELINE_COMPLETE', 'gate_passed': True}), encoding='utf-8')
        self.write_config()
        self.write_delivery()
        stages = self.STAGE_REQUIRED
        for name, artifacts in stages.items():
            (trial / ('stage-%s.json' % name)).write_text(json.dumps(
                {'stage': name, 'state': 'ok', 'artifacts': list(artifacts), 'macro_outputs': [],
                 'seconds': 1.0, 'macros': []}), encoding='utf-8-sig')
        # Every measurement record the generator knows about is written by default, each claiming ok and
        # each named by its own stage record -- that is what an honest complete run looks like. Tests
        # override entries to remove the claim or the stage attribution.
        default = {name: {'schema_version': 1, 'status': 'ok',
                          'measured_cell_quality_min': 0.3, 'cells': 100}
                   for name in ('static_body_audit.json', 'external_domain_build.json',
                                'external_flow_region.json', 'external_flow_region_probe.json',
                                'external_flow_physics.json', 'external_flow_mesh.json',
                                'external_flow_mesh_quality.json', 'external_flow_solve.json',
                                'external_flow_validation.json')}
        default.update(records or {})
        for record_name, payload in default.items():
            (trial / record_name).write_text(json.dumps(payload), encoding='utf-8-sig')
        return trial

    def generate(self):
        import subprocess
        result = subprocess.run([sys.executable, str(REPORT_GENERATOR), '--trial-id', self.trial_id,
                                 '--evidence', str(self.tmp)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        summary = json.loads(Path(payload['report_json']).read_text(encoding='utf-8'))
        return payload, summary

    def test_a_complete_run_reports_ok_and_hashes_itself(self):
        import hashlib
        self.write_six_stage_run()  # writes the config too
        payload, summary = self.generate()
        self.assertEqual([], summary['report_reasons'])
        self.assertEqual('REPORT_OK', payload['verdict'], summary['report_reasons'])
        # Only the records that claim success are required to be citable; the probe and the two .tsv
        # files do not claim anything and must not block.
        self.assertNotIn('external_flow_region_probe.json', ' '.join(summary['report_reasons']))
        for line in Path(payload['hashes']).read_text(encoding='utf-8').strip().splitlines():
            digest, name = line.split('  ', 1)
            self.assertEqual(digest, hashlib.sha256((self.tmp / name).read_bytes()).hexdigest(), name)

    def test_a_record_that_does_not_claim_success_is_not_a_defect(self):
        # Round 10 measured this on the region stage: external_flow_region_probe.json is written before
        # the region macro's later gates run and carries no `status`, while external_flow_region.json is
        # the record that claims ok. A completeness rule that required every declared artefact to be
        # citable would mark every honest six-stage run REPORT_INCOMPLETE and train readers to ignore the
        # verdict. The rule: only a record whose OWN bytes claim success must be citable.
        self.write_six_stage_run({'external_flow_region.json': {'schema_version': 1, 'status': 'ok'},
                                  'external_flow_region_probe.json': {'schema_version': 1,
                                                                       'part_surfaces_after_split': 7}})
        payload, summary = self.generate()
        self.assertEqual([], summary['report_reasons'], summary['report_reasons'])
        self.assertEqual('REPORT_OK', payload['verdict'])
        self.assertTrue(any('external_flow_region_probe.json' in note
                            for note in summary['records_without_a_success_claim']),
                        summary['records_without_a_success_claim'])
        entry = summary['evidence_index']['external_flow_region_probe.json']
        # No status field -> the gate says "no measurement-level status", not "unsuccessful", and the
        # report must treat it as corroboration rather than as a failed record.
        self.assertEqual('NOT_CITABLE_NO_CLAIM', entry['citation_state'])
        self.assertFalse(entry['record_claims_ok'])

    def test_a_record_that_claims_success_but_cannot_be_cited_blocks(self):
        # The mirror image, so the rule above cannot be satisfied by ignoring uncitable records entirely.
        # A record whose own bytes say status ok is citable either because a stage record names it (the
        # normal path) or because the RUN ended successfully (the fallback). To make it genuinely
        # uncitable, BOTH are removed: the config does not list it as a required artefact of the solve
        # stage, so no stage record names it, and the run state is a failure.
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location('report_mod', REPORT_GENERATOR)
        report_mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(report_mod)
        trial = self.write_six_stage_run({'external_flow_mesh_quality.json': {'schema_version': 1,
                                                                             'status': 'ok'}})
        # The mesh-quality record claims success but NO stage declares it here (that is the point of this
        # test), and its own stage record is removed as well: the gate attributes an artefact by the stage
        # record that names it, so removing only the config entry would leave stage-mesh-quality-audit.json
        # still naming it and the record would be citable by the normal path.
        (trial / 'stage-mesh-quality-audit.json').unlink()
        self.write_config(required_by_stage={
            'audit-body': ['static_body_audit.json'],
            'build-domain': ['external_domain_build.json'],
            'solve': ['external_flow_solve.json'],
            'validation': ['external_flow_validation.json'],
        })
        # The Windows runner also writes the run-state file the gate falls back to. This fixture is the
        # FAILED run the reviewer measured for the numeric_gate condition, so the fallback must say so.
        (trial / 'case-probe.json').write_text(json.dumps(
            {'state': 'FAILED', 'error': 'boom', 'engineering_verified': False}), encoding='utf-8')
        (self.tmp / (self.trial_id + '-result.json')).write_text(json.dumps(
            {'state': 'FAILED', 'error': 'boom'}), encoding='utf-8')
        payload, summary = self.generate()
        # The run itself is the outermost signal for the report file; the record-level rule lives in
        # verdict_of(), which is driven here directly with the config-derived stage sets.
        self.assertEqual('REPORT_RUN_FAILED', payload['verdict'])
        stage_names, required_by_stage = report_mod.run_view(
            json.loads((self.tmp / (self.trial_id + '-config.json')).read_text(encoding='utf-8')))
        citable = report_mod.citable_index(trial)
        stages = {name: {'state': 'ok'} for name in stage_names}
        verdict, reasons, no_claim = report_mod.verdict_of(
            {'state': 'AIR3B_PIPELINE_COMPLETE'}, stages, citable, stage_names,
            {'solve': ['external_flow_solve.json', 'external_flow_mesh_quality.json']})
        self.assertEqual('REPORT_INCOMPLETE', verdict, reasons)
        self.assertTrue(any('自称成功但不可引用' in reason for reason in reasons), reasons)
        self.assertNotIn('external_flow_mesh_quality.json', ' '.join(no_claim))
        # And the index still shows WHY it cannot be cited.
        self.assertFalse(citable['external_flow_mesh_quality.json']['record_claims_ok'] is False)

    def test_stage_names_this_repository_has_never_used_still_report_ok(self):
        """The round-11 core generality claim, executed end to end rather than unit-tested.

        The round-11 review measured that the generator still judged against the six baseline stage names
        in its verdict path, so a trial whose stages are called `alpha`/`beta` came back REPORT_INCOMPLETE
        naming six stages that were never supposed to run. This runs the real generator over such a trial.
        """
        trial = self.tmp / self.trial_id
        trial.mkdir()
        (self.tmp / (self.trial_id + '-config.json')).write_text(json.dumps(
            {'pipeline_id': 'never-seen-pipeline', 'case_label': '任意案例',
             'stages': [{'name': 'alpha', 'required': ['alpha_record.json']},
                        {'name': 'beta', 'required': ['beta_record.json']}]},
            ensure_ascii=False), encoding='utf-8')
        (self.tmp / (self.trial_id + '-result.json')).write_text(
            json.dumps({'state': 'AIR3B_PIPELINE_COMPLETE', 'gate_passed': True}), encoding='utf-8')
        for name in ('alpha', 'beta'):
            (trial / ('stage-%s.json' % name)).write_text(json.dumps(
                {'stage': name, 'state': 'ok', 'seconds': 2.5, 'artifacts': ['%s_record.json' % name],
                 'macro_outputs': [], 'macros': [{'macro': 'Whatever.java', 'ran': True, 'status': 'ok',
                                                  'exit_code': 0}]}), encoding='utf-8-sig')
            (trial / ('%s_record.json' % name)).write_text(json.dumps(
                {'schema_version': 1, 'status': 'ok', 'value': 1}), encoding='utf-8-sig')
        payload, summary = self.generate()
        self.assertEqual([], summary['report_reasons'], summary['report_reasons'])
        self.assertEqual('REPORT_OK', payload['verdict'])
        self.assertEqual(['alpha', 'beta'], summary['stage_names'])
        text = Path(payload['report']).read_text(encoding='utf-8')
        for baseline_stage in ('audit-body', 'build-domain', 'region-physics-mesh',
                               'mesh-quality-audit', 'solve', 'validation'):
            self.assertNotIn(baseline_stage, text,
                             'the report named a baseline stage that this run never had')
        self.assertIn('alpha', text)
        self.assertIn('beta', text)

    def test_the_generator_does_not_invent_missing_stages_when_the_config_is_absent(self):
        # A missing config is not evidence about which stages should have run: the expected set is then
        # whatever records exist, so a single-stage submission is not reported as five stages missing.
        trial = self.tmp / self.trial_id
        trial.mkdir()
        (self.tmp / (self.trial_id + '-result.json')).write_text(
            json.dumps({'state': 'AIR3B_PIPELINE_COMPLETE', 'gate_passed': True}), encoding='utf-8')
        (trial / 'stage-build-body.json').write_text(json.dumps(
            {'stage': 'build-body', 'state': 'ok', 'seconds': 4.0, 'artifacts': [],
             'macro_outputs': [], 'macros': []}), encoding='utf-8-sig')
        payload, summary = self.generate()
        self.assertEqual([], summary['report_reasons'], summary['report_reasons'])
        self.assertEqual('REPORT_OK', payload['verdict'])
        self.assertEqual(['build-body'], summary['stage_names'])

    def test_a_single_stage_run_says_so_instead_of_claiming_missing_stages(self):
        # The round's own positive geometry build is a ONE-stage submission. Its report must describe
        # that honestly. Round-11 review blocker 2 was that it claimed all six baseline stages missing.
        trial = self.tmp / self.trial_id
        trial.mkdir()
        (self.tmp / (self.trial_id + '-result.json')).write_text(
            json.dumps({'state': 'AIR3B_PIPELINE_COMPLETE', 'gate_passed': True}), encoding='utf-8')
        (self.tmp / (self.trial_id + '-config.json')).write_text(json.dumps(
            {'pipeline_id': 'unit-single', 'case_label': '单阶段',
             'stages': [{'name': 'build-body', 'required': ['static_body_geometry.json']}]},
            ensure_ascii=False), encoding='utf-8')
        (trial / 'stage-build-body.json').write_text(json.dumps(
            {'stage': 'build-body', 'state': 'ok', 'seconds': 4.0,
             'artifacts': ['static_body_geometry.json'], 'macro_outputs': [],
             'macros': [{'macro': 'BuildStaticBodyGeometry.java', 'ran': True, 'status': 'ok',
                         'exit_code': 0}]}), encoding='utf-8-sig')
        (trial / 'static_body_geometry.json').write_text(json.dumps(
            {'schema_version': 1, 'status': 'ok', 'body_part_name': 'RENAMED_BODY',
             'triangle_count': 9408}), encoding='utf-8-sig')
        payload, summary = self.generate()
        self.assertEqual([], summary['report_reasons'], summary['report_reasons'])
        self.assertEqual('REPORT_OK', payload['verdict'])
        text = Path(payload['report']).read_text(encoding='utf-8')
        self.assertIn('只声明了 1 个阶段', text)
        for baseline_stage in ('audit-body', 'build-domain', 'region-physics-mesh',
                               'mesh-quality-audit', 'solve', 'validation'):
            self.assertNotIn(baseline_stage, text, baseline_stage)

    def test_the_generator_reuses_the_shared_record_gate_instead_of_reimplementing_it(self):
        source = REPORT_GENERATOR.read_text(encoding='utf-8')
        self.assertIn('run_record_gate.py', source)
        self.assertIn('gate.citation_status', source)
        # Exactly one definition of "may this record be cited" in the repository. The generator may
        # NAME the gate's states (it prints them verbatim) but must not define one: no citation table,
        # no membership test of its own, no second copy of the state vocabulary.
        self.assertNotIn('def citation_status', source)
        self.assertNotIn('def scan_run', source)
        for state in ('CITABLE', 'NOT_CITABLE_FAILED_RUN', 'NOT_CITABLE_NO_CLAIM', 'UNKNOWN_RUN_STATE',
                      'UNPARSEABLE'):
            self.assertNotIn('%s = ' % state, source, 'the generator defines its own %s' % state)
        # The only citation-state decision in the file is the imported gate's. Every comparison must
        # name `gate.<STATE>` or the entry the gate produced, never a literal.
        self.assertIn('gate.CITABLE', source)
        self.assertNotIn('citation_state\') == \'CITABLE', source)


class TheReportNeverClaimsAnUndeliveredCheckpoint(unittest.TestCase):
    """Round 12: a finished solve is not a delivered .sim.

    Round 11 ended with `poll()` transferring only .json/.log/.tsv/.csv, so every `.sim` checkpoint was
    copied by hand and no record said whether the copy arrived. The entry command now writes a delivery
    record and the report reads it. These tests pin BOTH directions: a real failed transfer must be an
    explicit failure, and an absent delivery record must not be silently upgraded into success.
    """

    def setUp(self):
        import shutil
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.trial_id = 'unit-test-delivery-1234'

    def write_run(self, required_by_stage=None):
        trial = self.tmp / self.trial_id
        trial.mkdir()
        required = required_by_stage or {'solve': ['external_flow_solve.json', 'external_flow_solved.sim'],
                                         'validation': ['external_flow_validation.json']}
        (self.tmp / (self.trial_id + '-config.json')).write_text(json.dumps(
            {'pipeline_id': 'unit-delivery', 'case_label': '单元',
             'stages': [{'name': name, 'required': list(files)} for name, files in required.items()]}),
            encoding='utf-8')
        (self.tmp / (self.trial_id + '-result.json')).write_text(json.dumps(
            {'state': 'AIR3B_PIPELINE_COMPLETE', 'gate_passed': True}), encoding='utf-8')
        for name, files in required.items():
            (trial / ('stage-%s.json' % name)).write_text(json.dumps(
                {'stage': name, 'state': 'ok', 'seconds': 1.0, 'artifacts': list(files),
                 'macro_outputs': [], 'macros': []}), encoding='utf-8-sig')
        for record in ('external_flow_solve.json', 'external_flow_validation.json'):
            (trial / record).write_text(json.dumps({'schema_version': 1, 'status': 'ok'}),
                                        encoding='utf-8-sig')
        return trial

    def write_delivery(self, **overrides):
        doc = {'schema_version': 1, 'trial_id': self.trial_id,
               'solve_state': 'AIR3B_PIPELINE_COMPLETE', 'solve_completed': True,
               'numeric_gate_passed': True, 'runner_records_present': True,
               'download_succeeded': True,
               'checkpoint_delivered': True,
               'final_sim': {'name': 'external_flow_solved.sim', 'sha256': 'b' * 64,
                             'path': '/tmp/external_flow_solved.sim', 'status': 'verified'},
               'artifacts': [{'name': 'external_flow_solved.sim', 'recorded_sha256': 'b' * 64,
                              'local_sha256': 'b' * 64, 'bytes': 10, 'status': 'verified',
                              'declared_by': ['stage-solve.json']}],
               'failures': []}
        doc.update(overrides)
        (self.tmp / (self.trial_id + '-checkpoints.json')).write_text(
            json.dumps(doc, ensure_ascii=False), encoding='utf-8')
        return doc

    def generate(self):
        import subprocess
        result = subprocess.run([sys.executable, str(REPORT_GENERATOR), '--trial-id', self.trial_id,
                                 '--evidence', str(self.tmp)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        return payload, json.loads(Path(payload['report_json']).read_text(encoding='utf-8'))

    def test_absent_delivery_record_is_not_treated_as_delivered(self):
        # The counter-example that matters: the six stages all say ok and the run says COMPLETE, but no
        # delivery record exists. The report must NOT return REPORT_OK, because nothing verified that the
        # declared .sim arrived. Before round 12 this same fixture was the happy path.
        self.write_run()
        payload, summary = self.generate()
        self.assertEqual('REPORT_ARTIFACT_NOT_DELIVERED', payload['verdict'], summary['report_reasons'])
        self.assertTrue(any('未找到 checkpoint 下载记录' in reason
                            for reason in summary['report_reasons']), summary['report_reasons'])
        self.assertIsNone(summary['delivery'])
        text = Path(payload['report']).read_text(encoding='utf-8')
        self.assertIn('不能主张这些 .sim 已被取回', text)

    def test_a_failed_transfer_is_an_explicit_failure_and_not_a_pass(self):
        self.write_run()
        record = {'name': 'external_flow_solved.sim', 'recorded_sha256': 'b' * 64,
                  'local_sha256': None, 'bytes': None, 'status': 'hash_mismatch',
                  'declared_by': ['stage-solve.json'],
                  'error': 'sha256 ccc does not match the runner record bbb'}
        self.write_delivery(download_succeeded=False, checkpoint_delivered=False, final_sim=None,
                            failures=[record], artifacts=[record])
        payload, summary = self.generate()
        self.assertEqual('REPORT_ARTIFACT_NOT_DELIVERED', payload['verdict'], summary['report_reasons'])
        self.assertTrue(any('external_flow_solved.sim' in reason for reason in summary['report_reasons']),
                        summary['report_reasons'])
        self.assertEqual(False, summary['delivery']['download_succeeded'])
        self.assertEqual(True, summary['delivery']['solve_completed'])
        text = Path(payload['report']).read_text(encoding='utf-8')
        self.assertIn('送达失败项', text)
        self.assertIn('求解完成不等于产物已交付', text)

    def test_a_completed_solve_without_the_final_checkpoint_blocks(self):
        # download_succeeded true and failures empty, but the final .sim was never verified: that is an
        # internally inconsistent record and must not be read as delivery.
        self.write_run()
        self.write_delivery(checkpoint_delivered=False)
        payload, summary = self.generate()
        self.assertEqual('REPORT_ARTIFACT_NOT_DELIVERED', payload['verdict'], summary['report_reasons'])
        self.assertTrue(any('checkpoint_delivered=false' in reason
                            for reason in summary['report_reasons']), summary['report_reasons'])

    def test_a_delivery_failure_does_not_hide_a_failed_run(self):
        # A run that failed in the solve cannot produce a final checkpoint; the report still leads with the
        # RUN failure, and the delivery state is listed underneath rather than replacing it.
        self.write_run()
        (self.tmp / (self.trial_id + '-result.json')).write_text(json.dumps(
            {'state': 'FAILED', 'error': 'Owned STAR exit code 1'}), encoding='utf-8')
        self.write_delivery(solve_completed=False, download_succeeded=False,
                            checkpoint_delivered=False, final_sim=None,
                            failures=[{'name': 'external_flow_solved.sim', 'status': 'download_failed',
                                       'declared_by': ['stage-solve.json'], 'error': 'scp timeout'}])
        payload, summary = self.generate()
        self.assertEqual('REPORT_RUN_FAILED', payload['verdict'], summary['report_reasons'])
        self.assertTrue(any('external_flow_solved.sim' in reason for reason in summary['report_reasons']))

    def test_a_delivery_record_that_lost_its_runner_records_blocks(self):
        # A delivery record that says the runner records were absent means the transfer step could not
        # even identify a product. It must not read as success. The mutation test proved this reason was
        # not asserted by any test: disabling just this condition survived the whole class.
        self.write_run()
        self.write_delivery(runner_records_present=False, download_succeeded=False,
                            checkpoint_delivered=False, final_sim=None, failures=[])
        payload, summary = self.generate()
        self.assertEqual('REPORT_ARTIFACT_NOT_DELIVERED', payload['verdict'], summary['report_reasons'])
        self.assertTrue(any('runner 记录' in reason for reason in summary['report_reasons']),
                        summary['report_reasons'])

    def test_a_verified_delivery_record_keeps_the_report_ok(self):
        # The positive direction: with a delivery record whose .sim was verified, the same fixture is
        # REPORT_OK again, so the new check cannot be satisfied by failing everything.
        self.write_run()
        self.write_delivery()
        payload, summary = self.generate()
        self.assertEqual([], summary['report_reasons'], summary['report_reasons'])
        self.assertEqual('REPORT_OK', payload['verdict'])
        self.assertEqual('external_flow_solved.sim', summary['delivery']['final_sim']['name'])


class PropertyReadbackUsesExistingExportTransfer(unittest.TestCase):
    def test_property_artifacts_use_recorded_hashes_and_keep_failures_visible(self):
        import tempfile
        import hashlib
        spec = importlib.util.spec_from_file_location('property_delivery_driver', DRIVER)
        driver = importlib.util.module_from_spec(spec); spec.loader.exec_module(driver)
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp); trial = 'property-offline-1234'; run = evidence / trial; run.mkdir()
            record = run / 'property_nearwall_readback.json'
            body = b'area,yplus\n1,2\n'
            digest = hashlib.sha256(body).hexdigest()
            record.write_text(json.dumps({'status': 'ok', 'artifacts': [{
                'name': 'nearwall-face-values.csv', 'sha256': digest, 'bytes': len(body)}]}))
            stage = run / 'stage-property-nearwall.json'
            stage.write_text(json.dumps({'stage': 'property-nearwall', 'state': 'ok',
                'artifacts': [record.name], 'macros': [{'ran': True, 'status': 'ok', 'exit_code': 0}]}))
            (evidence / (trial + '-config.json')).write_text(json.dumps({'job_dir': 'owned-property-job'}))
            class Backend:
                payload = body
                def scp(self, local, remote, download=False): Path(local).write_bytes(self.payload)
            backend = Backend()
            success = driver.retrieve_result_exports(backend, trial, evidence)
            self.assertTrue(success['download_succeeded'])
            self.assertEqual(success['artifacts'][0]['local_sha256'], digest)
            (run / 'nearwall-face-values.csv').unlink(); backend.payload = b'wrong downloaded bytes'
            failure = driver.retrieve_result_exports(backend, trial, evidence)
            self.assertFalse(failure['download_succeeded'])
            self.assertEqual(failure['failures'][0]['status'], 'hash_mismatch')
            state = json.loads(stage.read_text()); state['state'] = 'failed'; stage.write_text(json.dumps(state))
            refused = driver.retrieve_result_exports(backend, trial, evidence)
            self.assertFalse(refused['download_succeeded'])
            self.assertEqual(refused['citation']['state'], 'NOT_CITABLE_FAILED_RUN')


class CheckpointRetrievalIsDataDrivenAndResumable(unittest.TestCase):
    """Round 12: the .sim to fetch is discovered from the runner's records, never assumed by name.

    The user's requirement was explicit: do not assume the output is called external_flow_solved.sim, and
    a one-stage build-body run must retrieve its own output. These tests exercise the entry script's
    functions directly with a stub transport, so they run offline; the real remote retrieval is exercised
    separately against the already-existing Windows trial directories.
    """

    def setUp(self):
        import shutil
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        spec = importlib.util.spec_from_file_location('delivery_driver', DRIVER)
        self.driver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.driver)
        self.trial_id = 'unit-test-retrieve-1234'
        self.evidence = self.tmp / 'ev'
        self.trial_dir = self.evidence / self.trial_id
        self.trial_dir.mkdir(parents=True)

    def write_stage(self, name, state, output, digest, macros=None):
        (self.trial_dir / ('stage-%s.json' % name)).write_text(json.dumps(
            {'stage': name, 'state': state, 'seconds': 1.0, 'output': output, 'output_sha256': digest,
             'macro_outputs': [output], 'artifacts': [], 'macros': macros or []}),
            encoding='utf-8-sig')

    def write_config(self, stages):
        (self.evidence / (self.trial_id + '-config.json')).write_text(json.dumps(
            {'pipeline_id': 'unit', 'job_dir': r'C:\CodexCFDRestricted\\' + self.trial_id,
             'stages': stages}), encoding='utf-8')

    def run_retrieve(self, result=None, payloads=None):
        payloads = payloads or {}
        calls = []

        class StubBackend:
            def scp(self, local, remote, download=False):
                calls.append(remote)
                name = Path(str(local)).name
                if name in payloads:
                    Path(local).parent.mkdir(parents=True, exist_ok=True)
                    Path(local).write_bytes(payloads[name])
                else:
                    raise RuntimeError('no such remote file: ' + str(remote))

        class StubTrial:
            ROOT = r'C:\CodexCFDRestricted'

        summary = self.driver.retrieve_checkpoints(
            StubBackend(), StubTrial, self.trial_id, self.evidence,
            result if result is not None else {'state': 'AIR3B_PIPELINE_COMPLETE'})
        return summary, calls

    def test_the_output_name_comes_from_the_record_not_from_a_constant(self):
        # No file in this fixture is called external_flow_solved.sim, and nothing may assume that name.
        body = b'unusual checkpoint'
        digest = __import__('hashlib').sha256(body).hexdigest()
        self.write_config([{'name': 'solve', 'required': ['my_solve_output.sim']}])
        self.write_stage('solve', 'ok', 'my_solve_output.sim', digest)
        summary, calls = self.run_retrieve(payloads={'my_solve_output.sim': body})
        self.assertTrue(summary['download_succeeded'])
        self.assertTrue(summary['checkpoint_delivered'])
        self.assertEqual('my_solve_output.sim', summary['final_sim']['name'])
        self.assertEqual(digest, summary['final_sim']['sha256'])
        self.assertEqual(1, len(calls))

    def test_a_single_stage_build_body_run_retrieves_its_own_output(self):
        body = b'geometry only'
        digest = __import__('hashlib').sha256(body).hexdigest()
        self.write_config([{'name': 'build-body', 'required': ['static_body_geometry.sim']}])
        self.write_stage('build-body', 'ok', 'static_body_geometry.sim', digest,
                         macros=[{'macro': 'BuildStaticBodyGeometry.java', 'ran': True, 'status': 'ok',
                                  'exit_code': 0, 'output': 'static_body_geometry.sim',
                                  'output_sha256': digest}])
        summary, _calls = self.run_retrieve(payloads={'static_body_geometry.sim': body})
        self.assertEqual('static_body_geometry.sim', summary['final_sim']['name'])
        self.assertTrue(summary['checkpoint_delivered'])

    def test_a_verified_local_copy_is_not_transferred_again(self):
        # This is the resume guarantee: a second --retrieve-only run after a partial failure must not
        # re-fetch what already matches, because re-fetching is the cheap half of "do not re-run STAR".
        body = b'already here'
        digest = __import__('hashlib').sha256(body).hexdigest()
        self.write_config([{'name': 'solve', 'required': ['a.sim']}])
        self.write_stage('solve', 'ok', 'a.sim', digest)
        (self.trial_dir / 'a.sim').write_bytes(body)
        summary, calls = self.run_retrieve()
        self.assertEqual([], calls)
        self.assertEqual('already_present', summary['artifacts'][0]['status'])
        self.assertTrue(summary['artifacts'][0]['resumed_from_local_copy'])
        self.assertTrue(summary['checkpoint_delivered'])

    def test_only_the_missing_or_wrong_files_are_retried(self):
        good, bad = b'good', b'bad-local'
        good_digest = __import__('hashlib').sha256(good).hexdigest()
        bad_digest = __import__('hashlib').sha256(b'bad-remote').hexdigest()
        self.write_config([{'name': 'solve', 'required': ['a.sim', 'b.sim']}])
        self.write_stage('solve', 'ok', 'a.sim', good_digest)
        # b.sim is declared by a macro of the same stage, with its own hash.
        (self.trial_dir / 'stage-solve.json').write_text(json.dumps(
            {'stage': 'solve', 'state': 'ok', 'output': 'a.sim', 'output_sha256': good_digest,
             'macro_outputs': ['a.sim', 'b.sim'], 'artifacts': [],
             'macros': [{'macro': 'M.java', 'ran': True, 'status': 'ok', 'exit_code': 0,
                         'output': 'b.sim', 'output_sha256': bad_digest}]}), encoding='utf-8-sig')
        (self.trial_dir / 'a.sim').write_bytes(good)
        (self.trial_dir / 'b.sim').write_bytes(bad)          # present but WRONG -> must be refetched
        summary, calls = self.run_retrieve(payloads={'b.sim': b'bad-remote'})
        self.assertEqual(1, len(calls))
        self.assertIn('b.sim', calls[0])
        statuses = {record['name']: record['status'] for record in summary['artifacts']}
        self.assertEqual('already_present', statuses['a.sim'])
        self.assertEqual('verified', statuses['b.sim'])

    def test_a_missing_expected_checkpoint_fails_the_delivery(self):
        digest = __import__('hashlib').sha256(b'never arrived').hexdigest()
        self.write_config([{'name': 'solve', 'required': ['gone.sim']}])
        self.write_stage('solve', 'ok', 'gone.sim', digest)
        summary, _calls = self.run_retrieve()
        self.assertFalse(summary['download_succeeded'])
        self.assertFalse(summary['checkpoint_delivered'])
        self.assertEqual('download_failed', summary['failures'][0]['status'])

    def test_downloaded_bytes_that_do_not_match_the_record_fail_verification(self):
        # The transfer SUCCEEDS and the file exists, but its bytes are not the bytes the runner hashed.
        # This is the case a transport-only check cannot see, and the mutation test proved it was not
        # covered: replacing the post-download comparison with `if True` survived the whole class.
        digest = __import__('hashlib').sha256(b'the bytes the runner hashed').hexdigest()
        self.write_config([{'name': 'solve', 'required': ['a.sim']}])
        self.write_stage('solve', 'ok', 'a.sim', digest)
        summary, calls = self.run_retrieve(payloads={'a.sim': b'DIFFERENT bytes entirely'})
        self.assertEqual(1, len(calls))
        self.assertEqual('hash_mismatch', summary['failures'][0]['status'])
        self.assertIn('does not match the runner record', summary['failures'][0]['error'])
        self.assertFalse(summary['download_succeeded'])
        self.assertFalse(summary['checkpoint_delivered'])
        # `final_sim.sha256` is the hash of the bytes actually on disk, not a claim of verification; the
        # status is what says whether it matched, and the artifact entry carries the recorded hash too.
        self.assertEqual(__import__('hashlib').sha256(b'DIFFERENT bytes entirely').hexdigest(),
                         summary['final_sim']['sha256'])
        self.assertNotEqual(summary['final_sim']['sha256'], digest)
        self.assertEqual(digest, summary['artifacts'][0]['recorded_sha256'])

    def test_conflicting_runner_hashes_are_a_delivery_failure(self):
        body = b'x'
        digest = __import__('hashlib').sha256(body).hexdigest()
        self.write_config([{'name': 'solve', 'required': ['a.sim']}])
        self.write_stage('solve', 'ok', 'a.sim', digest)
        (self.trial_dir / 'case-probe.json').write_text(json.dumps(
            {'state': 'AIR3B_PIPELINE_COMPLETE',
             'stages': [{'name': 'solve', 'status': 'OK', 'output': 'a.sim',
                         'output_sha256': 'f' * 64}]}), encoding='utf-8-sig')
        summary, calls = self.run_retrieve(payloads={'a.sim': body})
        self.assertEqual([], calls, 'a hash conflict must not be resolved by downloading one of them')
        self.assertEqual('recording_conflict', summary['failures'][0]['status'])
        self.assertFalse(summary['download_succeeded'])

    def test_an_unhashed_declared_checkpoint_cannot_be_claimed_as_delivered(self):
        # The runner recorded no sha256, so no transfer can be verified. The file may exist, but the
        # delivery claim is unsupported and must be reported as such rather than assumed.
        self.write_config([{'name': 'solve', 'required': ['a.sim']}])
        self.write_stage('solve', 'ok', 'a.sim', None)
        summary, calls = self.run_retrieve()
        self.assertEqual('unverifiable', summary['failures'][0]['status'])
        self.assertFalse(summary['download_succeeded'])
        self.assertEqual([], calls)

    def test_a_failed_run_with_no_expected_checkpoint_is_not_a_download_failure(self):
        # A stage that failed before its macro produced a file declares no output at all. That is an
        # absent deliverable, not a transfer failure: the two must stay distinguishable.
        self.write_config([{'name': 'solve', 'required': []}])
        self.write_stage('solve', 'failed', None, None,
                         macros=[{'macro': 'RunExternalFlowSolve.java', 'ran': True, 'status': 'failed',
                                  'exit_code': 1, 'output': None, 'output_sha256': None}])
        summary, calls = self.run_retrieve(result={'state': 'FAILED', 'error': 'exit 1'})
        self.assertEqual([], calls)
        self.assertTrue(summary['download_succeeded'])
        self.assertFalse(summary['checkpoint_delivered'])
        self.assertFalse(summary['solve_completed'])
        self.assertEqual([], summary['failures'])

    def test_solve_completion_and_download_success_are_separate_fields(self):
        # A finished solve whose transfer failed: solve_completed must stay true while download_succeeded
        # says false. Collapsing them would make one of the two facts unreadable.
        digest = __import__('hashlib').sha256(b'held remotely').hexdigest()
        self.write_config([{'name': 'solve', 'required': ['a.sim']}])
        self.write_stage('solve', 'ok', 'a.sim', digest)
        summary, _calls = self.run_retrieve(result={'state': 'AIR3B_PIPELINE_COMPLETE'})
        self.assertTrue(summary['solve_completed'])
        self.assertFalse(summary['download_succeeded'])
        self.assertFalse(summary['checkpoint_delivered'])

    def test_retrieving_nothing_because_there_are_no_runner_records_is_a_failure(self):
        # The "retrieved nothing, claimed everything" shape: an evidence directory with no config, no
        # case-probe and no stage records cannot identify a product, so download_succeeded must be false
        # rather than vacuously true (an empty failure list would otherwise read as success).
        summary, calls = self.run_retrieve()
        self.assertEqual([], calls)
        self.assertIs(False, summary['runner_records_present'])
        self.assertFalse(summary['download_succeeded'])
        self.assertFalse(summary['checkpoint_delivered'])
        self.assertEqual([], summary['failures'])


def raw_stl_geometry(path) -> dict:
    """Vertex geometry of a BINARY STL in whatever units the file actually stores, with NO scale applied.

    This is the round-12 witness for the unit acceptance. The Windows macros export the tessellation in
    MODEL units and then multiply by the unit scale (`unitToMeters`) themselves, so every number they
    report is conditional on their own unit assumption. Reading the file here without that multiplication
    is the one measurement that does not share it: the same physical sphere must show a half-extent of
    about 0.05 in a metre-unit .sim and about 50 in a millimetre-unit .sim.
    """
    import struct
    raw = Path(path).read_bytes()
    triangles = struct.unpack_from('<I', raw, 80)[0]
    if len(raw) != 84 + triangles * 50:
        raise AssertionError('not a binary STL: %d bytes for %d triangles' % (len(raw), triangles))
    lo = [float('inf')] * 3
    hi = [float('-inf')] * 3
    for index in range(triangles):
        offset = 84 + index * 50 + 12
        for vertex in range(3):
            point = struct.unpack_from('<fff', raw, offset + vertex * 12)
            for axis in range(3):
                lo[axis] = min(lo[axis], point[axis])
                hi[axis] = max(hi[axis], point[axis])
    return {'triangles': triangles, 'bbox': lo + hi,
            'half_extent': [(hi[axis] - lo[axis]) / 2.0 for axis in range(3)],
            'max_abs_coordinate': max(abs(value) for value in lo + hi)}


def strip_java_comments(source: str) -> str:
    """Remove // and /* */ comments so a source-level pin cannot be satisfied by prose.

    The round-12 independent review found that reverting the repair-2 constant at its call sites
    SURVIVED the entire suite, so this is the only instrument available offline (there is no STAR host in
    the test environment). Stripping comments first means a pin cannot be 'satisfied' by the explanatory
    text that describes the fix.
    """
    without_block = __import__('re').sub(r'/\*.*?\*/', '', source, flags=__import__('re').S)
    return '\n'.join(line.split('//')[0] for line in without_block.splitlines())


class TheStlExportUnitIsPinned(unittest.TestCase):
    """Round 12: STAR's STL export is in the INTERNAL SI unit, and that fact is now executable.

    The independent review mutated the two macros - reverting `measure(stl, STL_EXPORT_UNIT_TO_M)` to
    `measure(stl, scale)` and the audit's `raw * STL_EXPORT_UNIT_TO_M` to `raw * scale` - and BOTH
    mutations survived the full suite. Since the observed 1000x defect was exactly this, a silent revert
    would pass every test and fail only at STAR, where the repair allowance is already spent. These pins
    are source-level because the behaviour can only be observed on a STAR host; the file is read and its
    comments stripped, so the constant and both call sites must be real code.
    """

    def stripped(self, name):
        return strip_java_comments((ROOT / 'macros' / name).read_text(encoding='utf-8'))

    def test_both_macros_define_the_internal_si_stl_constant_as_one(self):
        for name in ('BuildStaticBodyGeometry.java', 'AuditStaticBodyGeometry.java'):
            source = self.stripped(name)
            self.assertIn('double STL_EXPORT_UNIT_TO_M = 1.0', source, name)

    def test_the_build_macro_measures_the_stl_with_the_internal_si_constant(self):
        source = self.stripped('BuildStaticBodyGeometry.java')
        self.assertIn('measure(stl, STL_EXPORT_UNIT_TO_M)', source)
        # The exact 1000x defect that attempt A and B measured: the CaseSpec scale applied to an
        # SI-exported STL. It must not come back.
        self.assertNotIn('measure(stl, scale)', source)

    def test_the_audit_macro_parses_the_stl_with_the_internal_si_constant(self):
        source = self.stripped('AuditStaticBodyGeometry.java')
        self.assertIn('raw * STL_EXPORT_UNIT_TO_M', source)
        self.assertNotIn('raw * scale', source)

    def test_the_audit_macro_reads_back_the_saved_model_unit(self):
        # The saved .sim's model unit must be re-read in a fresh session, not echoed from the CaseSpec.
        # The full statements are pinned, not just the key names: a mutation that renames the key (e.g.
        # model_length_unit_readback -> model_length_unit_readback_disabled) still CONTAINS the key name
        # and survived a substring-only assertion in the round-12 mutation check.
        source = self.stripped('AuditStaticBodyGeometry.java')
        self.assertIn('getPreferredUnits', source)
        self.assertIn('context.put("model_length_unit_readback", preferredLength.getSuffix());', source)
        self.assertIn('context.put("preferred_length_unit_conversion_to_m", '
                      'Double.toString(preferredConversion));', source)
        self.assertIn('Math.abs(preferredConversion - scale) > 1e-12', source)


class TheMmUnitVariantIsNotAStringChange(unittest.TestCase):
    """Round 12, the units axis of the generalisation matrix (AGENTS.md section 4).

    The claim under test is NOT "a CaseSpec string says mm". It is: STAR built the geometry-only .sim with
    mm as the simulation's MODEL unit, the full chain ran from that file, and the physical (SI) size,
    domain, boundaries, cell count and solution are the same as the metre-unit baseline. The witnesses are
    deliberately separated, because the first design for this test was WRONG and the STAR runs corrected it:

      * the MODEL-unit claim is carried by the preferred-length-unit READ-BACK recorded by the macros
        (`preferred_length_unit_conversion_to_m` = 0.001, i.e. mm) - in the build record for the file that
        was written, and in the AUDIT record for the file that was re-opened, which is the stage that does
        not trust the builder;
      * the PHYSICAL-size claim is carried by the exported STL. Round 12 measured that
        `MeshPart.exportStlPart` writes the simulation's INTERNAL SI unit, not the model unit: the mm .sim's
        STL is byte-identical to the metre baseline's. So the STL witnesses "same SI object", and the
        read-back witnesses "different model unit" - a 1000x error would break the first (its raw extent
        would be 50 or 5e-5, not 0.05);
      * the run records (audit, domain, mesh quality, solve) must agree with the baseline's SI numbers.

    If the mm evidence is not present yet these tests SKIP rather than pass; a skipped test is not a claim.
    """

    BASELINE_TRIAL = (ROOT / 'test-evidence' / 'static-external-flow' / 'round10-e2e'
                      / 's1-static-sphere-e2e-20261003a')
    BASELINE_STL = (ROOT / 'test-evidence' / 'static-external-flow' / 'build-body-positive'
                    / 's1-static-sphere-body-20261003a' / 'static_body_tessellation.stl')
    MM_DIR = ROOT / 'test-evidence' / 'static-external-flow' / 'round12-mm'
    MM_STL = MM_DIR / 'mm_static_body_tessellation.stl'
    MM_RECORD = MM_DIR / 'unit-verification.json'
    MM_GEOM_TRIAL = (ROOT / 'test-evidence' / 'static-external-flow' / 'round12-mm-geometry-c'
                     / 's1-static-sphere-mm-body-20261003c')
    MM_TRIAL = (ROOT / 'test-evidence' / 'static-external-flow' / 'round12-mm-e2e'
                / 's1-static-sphere-mm-e2e-20261003a')
    STL_EXPORT_TO_M = 1.0     # measured twice in round 12; see macros/BuildStaticBodyGeometry.java

    def record(self):
        if not self.MM_RECORD.is_file():
            self.skipTest('mm unit verification record not produced yet: %s' % self.MM_RECORD)
        return json.loads(self.MM_RECORD.read_text(encoding='utf-8'))

    def test_the_baseline_stl_is_in_metres(self):
        # Always runs: the baseline STL is committed evidence, and it pins the instrument itself.
        geometry = raw_stl_geometry(self.BASELINE_STL)
        self.assertEqual(9408, geometry['triangles'])
        self.assertAlmostEqual(0.05, max(geometry['half_extent']), delta=0.001)

    def test_the_build_record_proves_the_model_unit_is_mm_and_the_si_size_is_baseline(self):
        if not (self.MM_GEOM_TRIAL / 'static_body_geometry.json').is_file():
            self.skipTest('mm geometry record not present yet')
        record = json.loads((self.MM_GEOM_TRIAL / 'static_body_geometry.json').read_text(encoding='utf-8-sig'))
        self.assertEqual('ok', record.get('status'))
        self.assertEqual('mm', record.get('model_length_unit'))
        self.assertAlmostEqual(0.001, record.get('preferred_length_unit_conversion_to_m'), delta=1e-12)
        self.assertIn('mm', record.get('model_length_unit_readback'),
                      'the read-back must name the mm unit, got %r' % record.get('model_length_unit_readback'))
        # SI size, unchanged from the metre baseline.
        self.assertAlmostEqual(0.05, record.get('measured_max_vertex_radius_si_m'), delta=0.0001)
        self.assertAlmostEqual(0.05, record.get('measured_min_vertex_radius_si_m'), delta=0.0001)
        self.assertAlmostEqual(0.00052359877559829, record.get('measured_enclosed_volume_si_m3'),
                               delta=0.000001)
        self.assertEqual(9408.0, record.get('triangle_count'))

    def test_the_mm_stl_is_the_same_physical_object_as_the_baseline(self):
        if not self.MM_STL.is_file():
            self.skipTest('mm tessellation not retrieved yet: %s' % self.MM_STL)
        baseline = raw_stl_geometry(self.BASELINE_STL)
        variant = raw_stl_geometry(self.MM_STL)
        self.assertEqual(baseline['triangles'], variant['triangles'])
        # Both exports are in the internal SI unit, so the same physical sphere must give the same extent.
        self.assertAlmostEqual(max(baseline['half_extent']), max(variant['half_extent']), delta=1e-6)
        # And the strongest statement available: the two exported files are byte-identical.
        self.assertEqual(hashlib.sha256(self.BASELINE_STL.read_bytes()).hexdigest(),
                         hashlib.sha256(self.MM_STL.read_bytes()).hexdigest())

    def test_the_recorded_checks_agree_with_this_recomputation(self):
        record = self.record()
        checks = record['checks']
        self.assertTrue(all(check.get('passed') for check in checks.values()),
                        [name for name, check in checks.items() if not check.get('passed')])
        if self.MM_STL.is_file():
            variant = raw_stl_geometry(self.MM_STL)
            self.assertAlmostEqual(max(variant['half_extent']),
                                   checks['stl_si_size']['variant_raw_half_extent'], delta=0.01)
        self.assertAlmostEqual(0.001, checks['model_unit_conversion']['variant'], delta=1e-12)

    def test_the_mm_run_records_the_baseline_si_geometry_domain_and_cells(self):
        if not self.MM_TRIAL.is_dir():
            self.skipTest('mm e2e evidence not present yet: %s' % self.MM_TRIAL)
        baseline = self.BASELINE_TRIAL
        pairs = [('static_body_audit.json', ('measured_max_abs_coordinate_m',)),
                 ('external_domain_build.json', ('outer_bounds_m',)),
                 ('external_flow_mesh_quality.json', ('cells', 'measured_cell_quality_min',
                                                      'measured_face_validity_min',
                                                      'measured_volume_change_min',
                                                      'measured_wall_prism_layers_min')),
                 ('external_flow_solve.json', ('measured_drag_x_n_final', 'measured_mass_imbalance_final',
                                               'measured_density_volume_average_kg_m3'))]
        for name, keys in pairs:
            left = json.loads((baseline / name).read_text(encoding='utf-8-sig'))
            right = json.loads((self.MM_TRIAL / name).read_text(encoding='utf-8-sig'))
            self.assertEqual('ok', right.get('status'), '%s status' % name)
            for key in keys:
                self.assertIn(key, right, '%s lacks %s' % (name, key))
                if isinstance(left.get(key), list):
                    self.assertEqual(len(left[key]), len(right[key]), key)
                    for a, b in zip(left[key], right[key]):
                        self.assertAlmostEqual(a, b, delta=max(1e-6, abs(a) * 1e-3), msg=key)
                else:
                    self.assertAlmostEqual(left[key], right[key], delta=max(1e-9, abs(left[key]) * 1e-3),
                                           msg='%s::%s' % (name, key))
        # The SAVED .sim's model unit, re-read by the audit stage in a fresh session - this is the witness
        # that does not trust the builder.
        audit = json.loads((self.MM_TRIAL / 'static_body_audit.json').read_text(encoding='utf-8-sig'))
        domain = json.loads((self.MM_TRIAL / 'external_domain_build.json').read_text(encoding='utf-8-sig'))
        self.assertEqual('mm', audit.get('model_length_unit'))
        self.assertAlmostEqual(0.001, audit.get('preferred_length_unit_conversion_to_m'), delta=1e-12)
        self.assertIn('mm', audit.get('model_length_unit_readback'))
        self.assertEqual('mm', domain.get('model_length_unit'))
        # Boundaries: same boundary set as the baseline.
        mesh = json.loads((self.MM_TRIAL / 'external_flow_mesh_quality.json').read_text(encoding='utf-8-sig'))
        base_mesh = json.loads((baseline / 'external_flow_mesh_quality.json').read_text(encoding='utf-8-sig'))
        self.assertEqual([b['boundary'] for b in base_mesh['boundaries']],
                         [b['boundary'] for b in mesh['boundaries']])


class InheritedStageInputs(unittest.TestCase):
    """②R14: a stage may declare that it consumes records produced by an EARLIER stage of the same run.

    The measured blocker (round14-formal-layout-finding.json): the validation stage's independent audit could
    not judge because the runner handed it only the staged .sim, so it reported NOT_CONVERGED_UNMEASURED. The
    declaration is validated HERE, before submission; the two runner layers enforce it at run time.
    """

    SOLVE = {'name': 'solve', 'macros': ['RunExternalFlowSolve.java'],
             'required': ['external_flow_solve.json', 'external_flow_solve_history.csv',
                          'external_flow_solved.sim'],
             'output_sim': 'external_flow_solved.sim'}

    def pipeline(self, *stages):
        return {'input_hashes': {'reviewed.sim': 'a' * 64}, 'stages': list(stages)}

    def validation(self, inherited):
        return {'name': 'validation', 'macros': ['AuditExternalFlowConvergence.java'],
                'required': ['external_flow_validation.json'], 'output_sim': None,
                'inherited_inputs': inherited}

    def test_the_measured_declaration_is_accepted(self):
        plan = plan_pipeline(self.pipeline(self.SOLVE, self.validation(
            {'stage': 'solve', 'files': ['external_flow_solve.json', 'external_flow_solve_history.csv',
                                         'stage-solve.json']})))
        inherited = plan['stages'][1]['inherited_inputs']
        self.assertEqual('solve', inherited['stage'])
        self.assertEqual(['external_flow_solve.json', 'external_flow_solve_history.csv', 'stage-solve.json'],
                         inherited['files'])

    def test_inheriting_from_a_stage_that_is_not_earlier_is_refused(self):
        later = {'name': 'later', 'macros': ['X.java'], 'required': ['x.json'], 'output_sim': 'x.sim'}
        with self.assertRaisesRegex(StagePlanError, 'not an earlier stage'):
            plan_pipeline(self.pipeline(self.validation({'stage': 'later', 'files': ['x.json']}), later))

    def test_inheriting_a_file_the_source_does_not_produce_is_refused(self):
        with self.assertRaisesRegex(StagePlanError, 'does not declare'):
            plan_pipeline(self.pipeline(self.SOLVE, self.validation(
                {'stage': 'solve', 'files': ['external_flow_validation.json']})))

    def test_a_required_artifact_is_inheritable_because_the_runner_verifies_it_exists(self):
        # Review round 1 of this round called inheriting a `required`-but-not-macro-produced file a gap. It is
        # not: worker/run_job.ps1 verifies EVERY `required` artifact exists before a stage may report ok, so a
        # `required` file is a stronger guarantee than the macro_outputs chain (a macro may write artefacts the
        # chain never names, e.g. the solve stage's measurement record). A file that no macro really writes
        # fails the source stage at run time, and the inheriting stage is then refused before it starts.
        self.assertIn('external_flow_solve.json', self.SOLVE['required'])
        plan_pipeline(self.pipeline(self.SOLVE, self.validation(
            {'stage': 'solve', 'files': ['external_flow_solve.json']})))

    def test_the_runner_written_stage_record_counts_as_produced(self):
        # stage-<name>.json is written by the RUNNER (AGENTS section 6), so it need not be in `required`.
        plan_pipeline(self.pipeline(self.SOLVE, self.validation(
            {'stage': 'solve', 'files': ['stage-solve.json']})))

    def test_malformed_declarations_are_refused(self):
        for inherited, pattern in (({'stage': 'solve', 'files': []}, 'nonempty'),
                                   ({'stage': 'SOLVE!', 'files': ['a.json']}, 'invalid'),
                                   ({'stage': 'solve', 'files': ['a.json', 'a.json']}, 'repeats'),
                                   ({'stage': 'validation', 'files': ['a.json']}, 'itself'),
                                   ('solve', 'must be an object')):
            with self.subTest(inherited=inherited):
                with self.assertRaisesRegex(StagePlanError, pattern):
                    plan_pipeline(self.pipeline(self.SOLVE, self.validation(inherited)))

    def test_the_installed_validation_stage_declares_exactly_the_measured_three_files(self):
        import json
        manifest = json.loads((ROOT / 'workflows' / 'static-external-flow-2502' /
                               'manifest.json').read_text(encoding='utf-8'))
        declared = manifest['stages']['validation']['inherited_inputs']
        self.assertEqual('solve', declared['stage'])
        self.assertEqual(['external_flow_solve.json', 'external_flow_solve_history.csv', 'stage-solve.json'],
                         declared['files'])
        # and the solve stage really declares producing two of them (the third is the runner's own record)
        solve_required = set(manifest['stages']['solve']['required'])
        self.assertIn('external_flow_solve.json', solve_required)
        self.assertIn('external_flow_solve_history.csv', solve_required)



if __name__ == '__main__':
    unittest.main()



class EngineeringSolveMacroContract(unittest.TestCase):
    """Source-level counterexamples; compilation is separate, no STAR execution claimed."""
    def test_checkpoint_identity_is_checked_before_saved_solution_can_be_initialized(self):
        text=(ROOT/'macros/RunExternalFlowSolve.java').read_text()
        self.assertLess(text.index('start != expectedStart'),text.index('initializeSolution()'))
        self.assertIn('EXTERNAL_FLOW_EXPECTED_START_ITERATION',text)
        self.assertIn('!Double.isFinite(expectedStart)',text)

    def test_final_nonmultiple_chunk_is_bounded_and_checkpoint_is_not_ok_evidence(self):
        text=(ROOT/'macros/RunExternalFlowSolve.java').read_text()
        self.assertIn('run(Math.min(chunk, maxIterations - ran))',text)
        self.assertIn('EXTERNAL_FLOW_CHECKPOINT_EVERY_CHUNKS',text)
        self.assertIn('stage_success_certified',text)
        self.assertIn('checkpointInterval != Math.rint(checkpointInterval)',text)

    def test_solver_soft_clock_excludes_save_and_exit_but_legacy_basis_remains(self):
        text=(ROOT/'macros/RunExternalFlowSolve.java').read_text()
        self.assertIn('budgetBasis.equals("iterator") ? iteratorSeconds : elapsed',text)
        self.assertIn('budgetBasis = "macro_elapsed"',text)
        for field in ['setup_seconds','iterator_seconds','monitor_export_seconds','checkpoint_save_seconds','macro_elapsed_before_record_seconds']:
            self.assertIn(field,text)

class TheResourceContractPinIsRequiredOnBothPaths(unittest.TestCase):
    """C3（独立审查裁定）：MCP/backend 一直要求 review 钉住 resource_contract.py，
    而试跑路径此前不要求，导致同一份记录在两条路径上结论相反。现在两条路径一律要求。"""

    def test_omitting_the_resource_contract_pin_is_refused(self):
        import inspect
        import air3b_pipeline_trial as trial_module
        source = inspect.getsource(trial_module.check_review)
        self.assertIn('resource_contract_sha256', source,
                      'check_review 必须校验 resource_contract_sha256（C3 裁定：两条路径一律要求）')
        self.assertIn('resource_contract.py', source)
