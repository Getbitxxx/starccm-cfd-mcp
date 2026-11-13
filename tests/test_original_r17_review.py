"""Independent R17 boundary and real-failure accounting regressions (AGENTS O9)."""
import hashlib
import json
import unittest
import subprocess
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
class OriginalR17Review(unittest.TestCase):
    def test_true_source_is_sector_not_synthetic_air3b(self):
        identity = json.loads((ROOT/'test-evidence/original-geometry/r17/true-source-identity.json').read_text())
        role = json.loads((ROOT/identity['role_evidence']).read_text())
        self.assertEqual(sha(ROOT/identity['local_isolated_input']), identity['source_sha256'])
        self.assertEqual(identity['source_sha256'], role['source_case_sha256'])
        self.assertEqual(role['source_part']['role'], '120-degree rotating fluid sector, not a blade solid')
        self.assertFalse(role['cad_recovery_trial']['accepted'])
        self.assertFalse(role['cad_recovery_trial']['is_manifold'])
        self.assertTrue(identity['old_solution_must_be_removed'])
    def test_window1_frozen_bytes_unchanged(self):
        """冻结字节不得漂移，**除非**某条 supersession 记录声明了这次漂移。

        2026-10-14 的时间预算撤销轮改了共享执行文件与求解宏；历史证据保持原字节，改由
        test-evidence/**/time-budget-revocation-superseded-pins.json 声明。未记录的漂移仍然失败。
        """
        from tests.superseded_pins import is_recorded_change
        pins = json.loads((ROOT/'test-evidence/original-geometry/r17/frozen-window1-files.json').read_text())
        for name, digest in pins.items():
            live = sha(ROOT/name)
            if live != digest:
                self.assertTrue(is_recorded_change(name, live, ROOT/'test-evidence'),
                                'unrecorded drift: %s' % name)
    def test_current_frozen_pipeline_pins_and_mesh_gate(self):
        freeze = json.loads((ROOT/'test-evidence/original-geometry/r17/frozen-files-repair2.json').read_text())
        snapshot = ROOT/'test-evidence/original-geometry/r17/run-c/execution-source-c'
        for name, digest in freeze['files'].items():
            preserved = snapshot/name
            self.assertEqual(sha(preserved if preserved.exists() else ROOT/name), digest, name)
        review = json.loads((ROOT/'test-evidence/original-geometry/r17/run-c/pipeline-review.json').read_text())
        pipeline = json.loads((ROOT/'cases/original-geometry/r17/pipeline-repair2.json').read_text())
        for name, digest in pipeline['macro_hashes'].items():
            self.assertEqual(sha(snapshot/'macros'/name), digest)
            self.assertEqual(review['macro_hashes'][name], digest)
            compile_name = f'{name}.repair2.compile.json' if name == 'BuildFullRotor360.java' else f'{name}.compile.json'
            compiled = json.loads((ROOT/'test-evidence/original-geometry/r17'/compile_name).read_text())
            self.assertEqual(compiled['sha256'], digest)
            self.assertTrue(compiled['compiled'])
            self.assertEqual(compiled['compiler_exit_code'], 0)
        stages = {s['name']:s for s in pipeline['stages']}
        self.assertIn('mrf_volume_mesh_quality.json::"quality_gate_passed":true', stages['mesh']['require_contains'])
        self.assertLess(list(stages).index('mesh'), list(stages).index('physics'))
        self.assertEqual(stages['solve']['input'], stages['physics']['output'])
        self.assertEqual(pipeline['env']['AIR3B_EXPECTED_LOADED_ITERATION'], '0')
        self.assertEqual(pipeline['scope_class'], 'sample_specific')
        self.assertFalse(pipeline['engineering_verified'])

    def test_actual_attempt_a_preserves_failed_launch_cost_and_blocks_citation(self):
        import sys
        sys.path.insert(0, str(ROOT/'scripts'))
        from run_record_gate import citation_status, CITABLE
        run = ROOT/'test-evidence/original-geometry/r17/run/original-r17-20261005a'
        stage = json.loads((run/'stage-geometry.json').read_text(encoding='utf-8-sig'))
        first, *remaining = stage['macros']
        self.assertEqual(stage['state'], 'failed')
        self.assertTrue(first['ran'])
        self.assertEqual(first['exit_code'], 1)
        self.assertAlmostEqual(first['seconds'], 15.11, places=2)
        self.assertGreater(first['seconds'], 0)
        for macro in remaining:
            self.assertFalse(macro['ran'])
            self.assertEqual(macro['status'], 'not_run')
            self.assertIsNone(macro['seconds'])
        failure = run/'full_rotor_360_build.json'
        payload = json.loads(failure.read_text())
        self.assertEqual(payload['status'], 'error')
        self.assertIn('Force Z', payload['error'])
        self.assertIn('ReportMonitor', payload['error'])
        self.assertNotEqual(citation_status(failure)['state'], CITABLE)
        # Preserve authentic failed-stage bytes: even an accidentally successful
        # inner artifact cannot turn this FAILED stage into a citable operation.
        with tempfile.TemporaryDirectory() as d:
            temp = Path(d)
            (temp/'stage-geometry.json').write_bytes((run/'stage-geometry.json').read_bytes())
            false_ok = temp/'full_rotor_360_build.json'
            false_ok.write_text('{"status":"ok"}')
            self.assertNotEqual(citation_status(false_ok)['state'], CITABLE)

    def test_actual_attempt_b_disconnected_failure_charged_and_uncitable(self):
        import sys
        sys.path.insert(0, str(ROOT/'scripts'))
        from run_record_gate import citation_status, CITABLE
        run = ROOT/'test-evidence/original-geometry/r17/run-b/original-r17-20261005b'
        stage = json.loads((run/'stage-geometry.json').read_text(encoding='utf-8-sig'))
        first = stage['macros'][0]
        self.assertEqual(stage['state'], 'failed')
        self.assertTrue(first['ran'])
        self.assertEqual(first['exit_code'], 1)
        self.assertAlmostEqual(first['seconds'], 15.15, places=2)
        for macro in stage['macros'][1:]:
            self.assertEqual(macro['status'], 'not_run')
            self.assertFalse(macro['ran'])
            self.assertIsNone(macro['seconds'])
        failure = run/'full_rotor_360_build.json'
        error = json.loads(failure.read_text())
        self.assertEqual(error['status'], 'error')
        self.assertIn('DisconnectedException', error['error'])
        self.assertIn('FvRepresentation_2539', error['error'])
        self.assertNotEqual(citation_status(failure)['state'], CITABLE)
        self.assertAlmostEqual(15.11+first['seconds'], 30.26, places=2)

    def test_actual_attempt_c_success_artifact_is_not_successful_stage(self):
        import sys
        sys.path.insert(0, str(ROOT/'scripts'))
        from run_record_gate import citation_status, NOT_CITABLE_FAILED_RUN
        run = ROOT/'test-evidence/original-geometry/r17/run-c/original-r17-20261005c'
        stage = json.loads((run/'stage-geometry.json').read_text(encoding='utf-8-sig'))
        built, recovered, fluid = stage['macros']
        self.assertEqual(stage['state'], 'failed')
        self.assertEqual(built['status'], 'ok')
        self.assertEqual(built['exit_code'], 0)
        self.assertAlmostEqual(built['seconds'], 15.13, places=2)
        self.assertTrue(recovered['ran'])
        self.assertEqual(recovered['exit_code'], 1)
        self.assertAlmostEqual(recovered['seconds'], 15.09, places=2)
        self.assertEqual(fluid['status'], 'not_run')
        self.assertFalse(fluid['ran'])
        certificate = run/'full_rotor_360_build.json'
        self.assertEqual(json.loads(certificate.read_text())['status'], 'ok')
        self.assertEqual(citation_status(certificate)['state'], NOT_CITABLE_FAILED_RUN)
        artifact = run/'full_rotor_360_built.sim'
        self.assertEqual(sha(artifact), built['output_sha256'])
        self.assertEqual(sha(artifact), recovered['input_sha256'])
        self.assertEqual(sha(artifact), 'be6b47ac9b488558ed9ac875de838d311705fcc916b74e7d1a01a0a3dd4150d4')
        log = (run/recovered['log']).read_text(errors='replace')
        self.assertIn('Rotor material recovery failed', log)
        error = json.loads((run/'recovered_rotor_solids.json').read_text())['error']
        self.assertIn('ORIGINAL_RECOVERY_RAW', error)
        self.assertIn('does not exist', error)
        total = 15.11+15.15+built['seconds']+recovered['seconds']
        self.assertAlmostEqual(total, 60.48, places=2)
    def test_installed_api_absent_getpart_throws_counterexample(self):
        root = ROOT/'test-evidence/original-geometry/r17'
        manager = json.loads((root/'geometry-part-manager-api.json').read_text())
        parent = json.loads((root/'geometry-part-manager-parent-api.json').read_text())
        self.assertEqual(manager['exit'], 0)
        self.assertEqual(parent['exit'], 0)
        partcode = manager['bytecode']['value']
        getpart = partcode.split(' getPart(java.lang.String);',1)[1].split(' getParts();',1)[0]
        self.assertIn('Method getObject:', getpart)
        getobject = parent['bytecode'].split(' getObject(java.lang.String);',1)[1].split(' getObjectIgnoreCase',1)[0]
        self.assertIn('Method hasObject:', getobject)
        self.assertIn('class star/base/neo/NeoException', getobject)
        self.assertIn('athrow', getobject)
        # Actual reviewed recovery bytes still contain this known fail-fast path;
        # fixing it is explicitly outside this exhausted round's execution scope.
        code = (ROOT/'test-evidence/original-geometry/r17/run-c/execution-source-c/macros/RecoverRotorSolidsFromSectorFluid.java').read_text()
        self.assertIn('parts.getPart(rawName)', code)

    def test_final_delivery_does_not_claim_solve_or_measured_parameters(self):
        root = ROOT/'test-evidence/original-geometry/r17'
        report = (root/'REPORT.md').read_text()
        review = json.loads((root/'run-c/pipeline-review.json').read_text())
        self.assertTrue(any('getPart' in issue for issue in review['known_unmeasured_limits']))
        self.assertIn('执行前', report)
        self.assertRegex(report, r'执行前[^。]*缺对象查询[^。]*风险[^。]*尚未形成阻断')
        self.assertNotIn('未检出', report)
        before = json.loads((root/'progress-before-r17.json').read_text())
        current = json.loads((ROOT/'progress.json').read_text())
        self.assertEqual(before['air3b_budget'], current['budget']['per_case']['air-three-blade'])
        self.assertEqual(current['budget']['per_case']['air-three-blade']['rounds_since_stage_advance'], 3)
        result = json.loads((root/'r17-result.json').read_text())
        self.assertEqual(result['state'], 'STOPPED_GEOMETRY_REPAIR_LIMIT_EXHAUSTED')
        self.assertIsNone(result['final_solved_sim'])
        for key in ('actual_omega_rad_s','actual_inlet_mps','actual_mrf_axis'):
            self.assertIsNone(result['parameter_readback'][key])
        for name in ('mesh','solve','validation'):
            self.assertEqual(result['stages'][name]['status'], 'NOT_STARTED')
        self.assertFalse(result['capability_levels']['general_operation_acceptance'])
        self.assertFalse(result['capability_levels']['engineering_validation'])
        ledger = json.loads((root/'star-seconds-ledger.json').read_text())
        measured = sum(entry['seconds'] for entry in ledger['entries'])
        self.assertAlmostEqual(measured, 60.48, places=2)
        self.assertAlmostEqual(ledger['charged_seconds'], measured, places=2)
        self.assertAlmostEqual(ledger['authorized_seconds']-measured, 2876.81, places=2)
        # R17 settled ledger belongs to its pre-R18 snapshot; later authorized
        # spending must not mutate its historical evidence or freeze live progress.
        progress = json.loads((ROOT/'test-evidence/original-geometry/r18/progress-before-r18.json').read_text())['budget']['per_case']['original-geometry']
        self.assertAlmostEqual(progress['star_seconds_logged'], measured, places=2)
        slot = json.loads((root/'final-slot-and-source.json').read_text())
        self.assertEqual(slot['tasks'], [])
        self.assertEqual(slot['star'], [])
        self.assertEqual(slot['original_source_sha256'], sha(ROOT/'cases/original-geometry/r17/source_input.sim'))
        checks = json.loads((root/'final-runtime-byte-check.json').read_text())
        for trial in checks['actual_execution_snapshots'].values(): self.assertTrue(all(trial.values()))
        self.assertTrue(all(checks['frozen_window1_and_shared'].values()))

    def test_mesh_error_escaper_roundtrips_all_c0_actual_java(self):
        text = (ROOT/'macros/ExecuteMrfDiagnosticMesh.java').read_text()
        start = text.index('private static String escape(String s)')
        # This helper has no trailing class methods; balanced source braces isolate
        # the actual Java implementation without translating its escaping logic.
        brace = text.index('{', start); depth = 1; end = brace+1
        while depth:
            if text[end] == '{': depth += 1
            elif text[end] == '}': depth -= 1
            end += 1
        helper = text[start:end]
        with tempfile.TemporaryDirectory() as d:
            source = Path(d)/'EscaperProbe.java'
            source.write_text('public class EscaperProbe {'+helper+
                'public static void main(String[] args){StringBuilder b=new StringBuilder();'
                'for(int i=0;i<32;i++)b.append((char)i);b.append((char)34).append((char)92);'
                'System.out.print((char)34+escape(b.toString())+(char)34);}}')
            subprocess.run(['javac', str(source)], check=True, capture_output=True)
            out = subprocess.run(['java','-cp',d,'EscaperProbe'], check=True,
                                 capture_output=True, text=True).stdout
            self.assertEqual(json.loads(out), ''.join(chr(i) for i in range(32))+'"'+chr(92))
            with self.assertRaises(json.JSONDecodeError):
                json.loads('"'+chr(12)+'"')  # actual missed formfeed shape

    def test_real_r16_nonzero_exit_needs_launch_and_time_preserved(self):
        fixture = ROOT/'test-evidence/air-three-blade/r16/negative/air3b-r16-param-neg-20261004a'
        stage = json.loads((fixture/'stage-parameter-negative.json').read_text(encoding='utf-8-sig'))
        old = stage['macros'][0]
        self.assertFalse(old['ran'])
        self.assertIsNone(old['seconds'])
        self.assertEqual(old['exit_code'], 1)
        self.assertIn('Parameter declaration mismatch', (fixture/old['log']).read_text(errors='replace'))
        runner = (ROOT/'scripts/security/trial_air3b_pipeline.ps1').read_text()
        fn = runner[runner.index('function Run-OwnedStar'):runner.index('function Save-StageRecord')]
        self.assertLess(fn.index('$proc.Start()'), fn.index('$script:lastStarRan=$true'))
        self.assertIn('finally{$script:lastStarSeconds=', fn)
        self.assertLess(fn.index('$script:lastStarExitCode=$proc.ExitCode'), fn.index('if($proc.ExitCode -ne 0){throw'))
        recovery = runner[runner.index('# R17: R16 measured'):runner.index('$macroRecords+=$macroRecord',runner.index('# R17: R16 measured'))]
        self.assertIn('$macroRecord.ran=$lastStarRan', recovery)
        self.assertIn('$macroRecord.seconds=$lastStarSeconds', recovery)
if __name__ == '__main__': unittest.main()
