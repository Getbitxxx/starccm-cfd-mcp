"""R18 independent tests execute actual nullable frame selection statements."""
import json
import hashlib
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class OriginalR18Review(unittest.TestCase):
    def test_actual_frame_selection_absent_existing_wrong_type(self):
        for filename, first, last in (
            ('ConfigureMrfInterfaces.java','Object prior = null;',': (UserRotatingReferenceFrame) prior;'),
            ('ConfigureAir3bMrfPhysics.java','Object priorFrame = null;',': (UserRotatingReferenceFrame) priorFrame;')):
            code=(ROOT/'macros'/filename).read_text();start=code.index(first);end=code.index(last,start)+len(last)
            production=code[start:end]
            self.assertNotIn('.getObject(',production)
            self.assertNotIn('catch',production)
            # Compile the exact production statements against a minimal manager surface.
            # getObject deliberately throws: optional selection must only enumerate.
            source='''import java.util.*; public class FrameProbe {
static class AbstractReferenceFrame {String name; AbstractReferenceFrame(String n){name=n;} String getPresentationName(){return name;}}
static class UserRotatingReferenceFrame extends AbstractReferenceFrame {UserRotatingReferenceFrame(String n){super(n);}}
static class ReferenceFrameManager {List<AbstractReferenceFrame> list=new ArrayList<>(); int created=0;
List<AbstractReferenceFrame> getObjects(){return list;} Object getObject(String n){throw new IllegalStateException("strict absent lookup");}
Object createReferenceFrame(Class<?> c,String n){created++; AbstractReferenceFrame f=new UserRotatingReferenceFrame(n);list.add(f);return f;}}
static class Sim {ReferenceFrameManager frames; Sim(ReferenceFrameManager f){frames=f;} ReferenceFrameManager get(Class<?> c){return frames;}}
static UserRotatingReferenceFrame select(ReferenceFrameManager frames){String frameName="role";String frameRole="role";Sim sim=new Sim(frames);
'''+production+'''
return frame;}
public static void main(String[] args){ReferenceFrameManager missing=new ReferenceFrameManager(); select(missing);if(missing.created!=1)throw new AssertionError();
ReferenceFrameManager existing=new ReferenceFrameManager();UserRotatingReferenceFrame f=new UserRotatingReferenceFrame("role");existing.list.add(f);if(select(existing)!=f||existing.created!=0)throw new AssertionError();
ReferenceFrameManager wrong=new ReferenceFrameManager();wrong.list.add(new AbstractReferenceFrame("role"));try{select(wrong);throw new AssertionError();}catch(IllegalStateException expected){}System.out.print("PASS");}}
'''
            with tempfile.TemporaryDirectory() as d:
                path=Path(d)/'FrameProbe.java';path.write_text(source)
                subprocess.run(['javac',str(path)],check=True,capture_output=True)
                result=subprocess.run(['java','-cp',d,'FrameProbe'],check=True,capture_output=True,text=True)
                self.assertEqual(result.stdout,'PASS')
    def test_current_frozen_bytes_and_budget_stop_policy(self):
        root=ROOT/'test-evidence/original-geometry/r18'
        freeze=json.loads((root/'frozen-files.json').read_text())
        self.assertEqual(len(freeze['files']),31)
        # ②R19 intentionally re-pinned the macros it corrected. The set is NOT hardcoded: it is derived
        # from the two pipelines' own pins, so a further authorized repair cannot silently make this
        # test stale (the first ②R19 review caught exactly that: the hardcoded single-macro expectation
        # went red when the second repaired macro moved). Every other frozen byte must still match, and
        # the ②R18 execution snapshot of each moved macro must still match its own freeze, so the
        # historical run stays pinned byte-for-byte.
        pipeline=json.loads((ROOT/'cases/original-geometry/r18/pipeline.json').read_text())
        later=[json.loads((ROOT/'cases/original-geometry'/r/'pipeline.json').read_text())
               for r in ('r19','r20')]
        moved={'macros/'+name for name,digest in pipeline['macro_hashes'].items()
               if any(lp['macro_hashes'].get(name,digest)!=digest for lp in later)}
        self.assertEqual(moved,{'macros/RecoverRotorSolidsFromSectorFluid.java',
                                'macros/ConfigureMrfInterfaces.java',
                                'macros/ConfigureMrfMeshOperation.java'})
        stale=[name for name,digest in freeze['files'].items()
               if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest]
        # 2026-10-15 归因：`moved` 只从 r19/r20 两条 pipeline 的 macro_hashes 派生（宏被后续轮次重钉）。
        # 2026-10-14 的时间预算撤销另外改了 6 个共享执行文件；它们不在任何 pipeline 的 macro_hashes 里，
        # 于是本断言把**已登记的合法移动**误报成漂移。改为与 r17/r19/r20 同一条规则：
        # 已登记的移动放行、未登记的漂移仍然失败（negative control 保留）。
        from tests.superseded_pins import is_recorded_change
        unrecorded=[name for name in stale
                    if not is_recorded_change(
                        name, hashlib.sha256((ROOT/name).read_bytes()).hexdigest(), ROOT/'test-evidence')]
        self.assertEqual(sorted(unrecorded),sorted(moved))
        for name in moved:
            self.assertEqual(hashlib.sha256((root/'run/execution-source'/name).read_bytes()).hexdigest(),
                             freeze['files'][name])
            self.assertNotEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),
                                freeze['files'][name])
        # The invariant to pin for the historical pipeline is the byte snapshot R18 actually executed,
        # not the mutable working copy.
        snapshot=root/'run/execution-source/macros'
        for name,digest in pipeline['macro_hashes'].items():
            self.assertEqual(hashlib.sha256((snapshot/name).read_bytes()).hexdigest(),digest,name)
        plan=json.loads((root/'execution-plan.json').read_text())
        self.assertEqual(plan['extra_batch']['geometry_existing_repairs_used'],2)
        self.assertEqual(plan['extra_batch']['additional_local_batch'],1)
        self.assertEqual(plan['extra_batch']['any_geometry_failure_after_batch'],'STOP')
        self.assertEqual(plan['other_stage_repairs_remaining']['mesh'],0)
        self.assertAlmostEqual(plan['budget_seconds'],2876.81,places=2)
        self.assertTrue(plan['old_failed_stage_checkpoint_not_input'])
        self.assertFalse(plan['independent_reproduction'])
        for name in plan['extra_batch']['batch_scoped_files']:
            compile=json.loads((root/(name+'.compile.json')).read_text())
            self.assertTrue(compile['compiled'])
            self.assertEqual(compile['compiler_exit_code'],0)
            self.assertEqual(compile['sha256'],pipeline['macro_hashes'][name])

    def test_actual_boolean_residue_failure_closes_geometry_without_citation(self):
        import sys
        sys.path.insert(0,str(ROOT/'scripts'))
        from run_record_gate import citation_status,NOT_CITABLE_FAILED_RUN
        run=ROOT/'test-evidence/original-geometry/r18/run/original-r18-20261005a'
        stage=json.loads((run/'stage-geometry.json').read_text(encoding='utf-8-sig'))
        first,recovery,fluid=stage['macros']
        self.assertEqual(stage['state'],'failed')
        self.assertEqual(first['status'],'ok')
        self.assertEqual(recovery['status'],'failed')
        self.assertTrue(recovery['ran'])
        self.assertEqual(recovery['exit_code'],1)
        self.assertFalse(fluid['ran'])
        self.assertEqual(fluid['status'],'not_run')
        error=json.loads((run/'recovered_rotor_solids.json').read_text())['error']
        self.assertIn('Boolean residue exceeds explicit artifact-volume budget',error)
        self.assertNotIn('does not exist in manager',error)
        ratio=float(error.rsplit(':',1)[1].strip())
        spec=json.loads((ROOT/'cases/original-geometry/r17/case_spec.json').read_text())
        self.assertGreater(ratio,spec['recovery_artifact_volume_ratio_max'])
        self.assertAlmostEqual(ratio,0.004698165776761509)
        certificate=run/'full_rotor_360_build.json'
        self.assertEqual(json.loads(certificate.read_text())['status'],'ok')
        self.assertEqual(citation_status(certificate)['state'],NOT_CITABLE_FAILED_RUN)
        self.assertEqual(first['output_sha256'],recovery['input_sha256'])
        seconds=sum(m['seconds'] for m in stage['macros'] if m['ran'])
        self.assertAlmostEqual(seconds,29.85,places=2)
        self.assertAlmostEqual(2876.81-seconds,2846.96,places=2)
        self.assertAlmostEqual(first['seconds'],14.72,places=2)
        self.assertAlmostEqual(recovery['seconds'],15.13,places=2)

    def test_diagnostic_delivery_and_star_ledger_are_separate_from_stage_pass(self):
        root=ROOT/'test-evidence/original-geometry/r18'
        delivery=json.loads((root/'diagnostic-delivery.json').read_text())
        self.assertEqual(delivery['new_star_seconds'],0)
        names=set()
        for artifact in delivery['artifacts']:
            data=(ROOT/artifact['local_path']).read_bytes()
            self.assertEqual(len(data),artifact['bytes'])
            self.assertEqual(hashlib.sha256(data).hexdigest(),artifact['sha256'])
            self.assertEqual(artifact['local_sha256'],artifact['sha256'])
            self.assertTrue(artifact['hash_verified'])
            names.add(artifact['name'])
        self.assertIn('recovered_rotor_raw.sim',names)
        self.assertIn('recovered_rotor_raw.stl',names)
        ledger=json.loads((root/'star-seconds-ledger.json').read_text())
        self.assertAlmostEqual(sum(e['seconds'] for e in ledger['entries']),29.85,places=2)
        self.assertAlmostEqual(ledger['charged_seconds'],29.85,places=2)
        self.assertAlmostEqual(ledger['remaining_seconds'],2846.96,places=2)
        self.assertEqual(ledger['previous_geometry_repairs_used'],2)
        self.assertTrue(ledger['extra_batch_does_not_reset_r17'])
        self.assertEqual(ledger['geometry_failure_after_batch'],'STOP')

    def test_final_delivery_partial_progress_never_certifies_failed_geometry(self):
        root=ROOT/'test-evidence/original-geometry/r18'
        result=json.loads((root/'r18-result.json').read_text())
        self.assertEqual(result['state'],'STOPPED_GEOMETRY_GATE_AFTER_EXTRA_BATCH')
        self.assertEqual(result['stages']['geometry']['status'],'PARTIAL')
        self.assertEqual(result['citable_success_certificates'],[])
        self.assertIsNone(result['final_solved_sim'])
        for field in ('actual_omega_rad_s','actual_rpm','actual_inlet_mps','actual_mrf_axis'):
            self.assertIsNone(result['parameter_readback'][field])
        self.assertFalse(result['capability_levels']['general_operation_acceptance'])
        self.assertFalse(result['capability_levels']['engineering_validation'])
        self.assertFalse(result['repair_account']['geometry_further_repair_allowed'])
        current=json.loads((ROOT/'progress.json').read_text())
        before=json.loads((root/'progress-before-r18.json').read_text())
        self.assertEqual(current['budget']['per_case']['air-three-blade'],before['budget']['per_case']['air-three-blade'])
        budget=current['budget']['per_case']['original-geometry']
        # 2026-10-15 归因：原来直接读**活的** progress.json，并要求 R18 当时的
        # last_round.advanced_stage=True / rounds_since_stage_advance=0 / star_seconds_logged=90.33 /
        # remaining=2846.96。后续已授权轮次按 §2-O1 继续更新同一份活文件，那些值必然移动
        # （现为 advanced_stage=False、rounds=2、1323.81、1613.48）。R18 自己的冻结记录是
        # r18-result.json 与 progress-before-r18.json；这里改为钉住它本来要守的不变量：
        # **R18 的修复账与专账没有被后续轮次抹掉**，且机时只增、剩余授权只减。
        self.assertEqual(budget['repair_account'],result['repair_account'])
        self.assertFalse(budget['repair_account']['geometry_further_repair_allowed'])
        self.assertEqual(budget['r18_star_seconds'],result['star_seconds'])
        self.assertEqual(budget['r18_ledger'],'test-evidence/original-geometry/r18/star-seconds-ledger.json')
        round_start=before['budget']['per_case']['original-geometry']
        self.assertGreaterEqual(budget['star_seconds_logged'],round_start['star_seconds_logged'])
        self.assertGreaterEqual(budget['star_seconds_logged'],result['star_seconds'])
        self.assertLessEqual(budget['remaining_authorized_star_seconds'],
                             round_start['remaining_authorized_star_seconds'])
        self.assertLessEqual(budget['remaining_authorized_star_seconds'],
                             result['remaining_authorized_seconds'])
        # §2-O4 的读数字段必须自洽，且**不能被清零或伪装**：独立审查 F4 指出 isinstance/int>=0/bool
        # 三条几乎不可能失败。改为按 progress_matrix 自己的推导规则复算（advanced=False 时
        # rounds = previous + 1），并明确禁止在 advanced_stage=False 时写成 0。
        last = budget['last_round']
        previous = last['previous_rounds_since_stage_advance']
        advanced = last['advanced_stage']
        self.assertIsInstance(previous, int)
        self.assertIsInstance(advanced, bool)
        self.assertEqual(0 if advanced else previous + 1, budget['rounds_since_stage_advance'])
        if not advanced:
            self.assertGreaterEqual(budget['rounds_since_stage_advance'], 1,
                                    '清零计数会让 §2-O4 永远不报警')
        # R18 之后的每一轮都继续更新了同一份活文件，因此 previous 不得小于 R18 当时的 previous
        self.assertGreaterEqual(previous, 1)
        slot=json.loads((root/'final-slot-and-source.json').read_text())
        self.assertEqual(slot['tasks'],[])
        self.assertEqual(slot['star'],[])
        self.assertEqual(slot['source_sha256'],hashlib.sha256((ROOT/'cases/original-geometry/r17/source_input.sim').read_bytes()).hexdigest())
        checks=json.loads((root/'final-byte-check.json').read_text())
        self.assertEqual(len(checks['current_run_files']),31)
        self.assertEqual(len(checks['protected19']),19)
        self.assertTrue(all(checks['current_run_files'].values()))
        self.assertTrue(all(checks['protected19'].values()))
        report=(root/'REPORT.md').read_text()
        self.assertIn('NOT_CITABLE_FAILED_RUN',report)
        self.assertIn('不重置 R17 修复 2 次账',report)
        self.assertIn('最终求解',report)

    def test_known_absence_queries_removed_required_inputs_strict(self):
        recover=(ROOT/'macros/RecoverRotorSolidsFromSectorFluid.java').read_text()
        fluid=(ROOT/'macros/BuildMrfFluidVolumes.java').read_text()
        self.assertIn('outputExists(actualPartNames,rawName)',recover)
        self.assertIn('outputExists(actualPartNames,solidName)',recover)
        self.assertIn('outputExists(actualPartNames,selected)',fluid)
        self.assertIn('parts.getPart(name)',recover)
        self.assertIn('parts.getPart(name)',fluid)
        names=['BuildFullRotor360','RecoverRotorSolidsFromSectorFluid','BuildMrfFluidVolumes','CreateMrfRegions','ConfigureMrfInterfaces','ConfigureMrfMeshOperation','ExecuteMrfDiagnosticMesh','AuditMrfSurfaceMap','ConfigureAir3bMrfPhysics','RunAir3bMrfSegment','AuditAir3bMassBalance']
        for name in names:
            code=(ROOT/'macros'/f'{name}.java').read_text()
            self.assertNotRegex(code,r'\b(?:getPart|getObject|getRegion|getBoundary)\([^;]*?[!=]=\s*null',name)
if __name__=='__main__':unittest.main()
