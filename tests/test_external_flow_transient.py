"""Execute the new macro's actual pure contract on the JVM; no STAR and no mock API."""
import copy
import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from backend import workflow_catalog, validate_stage_parameters, stage_parameter_declarations
from stage_plan import plan_stage
from casespec import validate_case_spec

SOURCE = (ROOT/'macros/RunExternalFlowTransient.java').read_text()
CASE = ROOT/'cases/static-sphere-urans-20261020'


class ExecutedMacroContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name)
        pure = SOURCE.split('// BEGIN PURE CONTRACT', 1)[1].split('\n', 1)[1].split('// END PURE CONTRACT', 1)[0]
        code = ('import java.util.*; public class Contract {'+pure+'''
          public static void main(String[] args) throws Exception {
            String s=new String(java.nio.file.Files.readAllBytes(java.nio.file.Paths.get(args[0])),java.nio.charset.StandardCharsets.UTF_8);
            Map<String,String> env=new HashMap<String,String>();
            if(args.length>1)env.put("EXTERNAL_FLOW_DT_S",args[1]);
            new Config(object(new Json(s).parse()),env);
            if(decades(1,0.001)<3 || decades(0,0)>=3 || decades(1,0.01)>=3)
                throw new AssertionError("Decay eligibility differs");
          }}''')
        (cls.path/'Contract.java').write_text(code)
        subprocess.run(['javac', str(cls.path/'Contract.java')], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check_spec(self, spec, valid, env=None):
        p=self.path/'spec.json';p.write_text(json.dumps(spec))
        args=['java','-cp',str(self.path),'Contract',str(p)] + ([env] if env else [])
        result=subprocess.run(args,capture_output=True,text=True)
        self.assertEqual(result.returncode==0,valid,result.stderr)

    def test_both_campaign_specs_are_accepted_by_macro(self):
        for p in [CASE/'case_spec.json',CASE/'pilot-case_spec.json']:
            self.check_spec(json.loads(p.read_text()),True)

    def test_missing_dt_nonfinite_boolean_string_and_null_are_refused(self):
        for value in ['missing',float('nan'),float('inf'),0,-0.001,True,'0.0002',None]:
            s=json.loads((CASE/'case_spec.json').read_text())
            if value=='missing':s['transient'].pop('time_step_s')
            else:s['transient']['time_step_s']=value
            self.check_spec(s,False)

    def test_invalid_cycles_window_duration_and_fixed_decay_are_refused(self):
        for key,value in [('startup_cycles',0),('statistics_cycles',0),('window_cycles',11),
                          ('window_cycles',2),('steps',2499),('inner_iterations',0),
                          ('checkpoint_every_steps',0),('inner_decay_decades',2.9)]:
            s=json.loads((CASE/'case_spec.json').read_text());s['transient'][key]=value
            self.check_spec(s,False)

    def test_environment_cannot_override_spec(self):
        s=json.loads((CASE/'case_spec.json').read_text())
        self.check_spec(s,True,'0.0002');self.check_spec(s,False,'0.0001')
        self.check_spec(s,False,'NaN')


class TransientWiring(unittest.TestCase):
    def test_stage_shape_and_planner(self):
        stage=workflow_catalog()['static-external-flow-2502']['stages']['solve-transient']
        plan=plan_stage(dict(stage,name='solve-transient'))
        self.assertEqual([x['macro'] for x in plan['steps']],['RunExternalFlowTransient.java'])
        self.assertEqual(stage['macro_outputs'],['external_flow_transient.sim'])
        self.assertEqual(stage['output_sim'],'external_flow_transient.sim')
        for artifact in stage['required']:
            self.assertIn('"'+artifact+'"',SOURCE)

    def test_only_declared_parameters_are_accepted(self):
        declared=stage_parameter_declarations('static-external-flow-2502','solve-transient')
        self.assertEqual(set(declared),{'EXTERNAL_FLOW_DT_S','EXTERNAL_FLOW_STEPS',
                         'EXTERNAL_FLOW_INNER_ITER','EXTERNAL_FLOW_CHECKPOINT_EVERY'})
        validate_stage_parameters({name:'1' for name in declared},declared)
        for name in ['EXTERNAL_FLOW_MAX_ITERATIONS','PATH','UNDECLARED']:
            with self.assertRaises(ValueError):validate_stage_parameters({name:'1'},declared)

    def test_old_stages_byte_values_and_original_casespec_preserved(self):
        prior=ROOT/'test-evidence/e-static-sphere-urans-20261020/prior-state'
        old=json.loads((prior/'workflows/static-external-flow-2502/manifest.json').read_text())
        new=json.loads((ROOT/'workflows/static-external-flow-2502/manifest.json').read_text())
        del new['stages']['solve-transient'];self.assertEqual(old,new)
        original=json.loads((ROOT/'cases/static-sphere-external-flow/case_spec.json').read_text())
        s=json.loads((CASE/'case_spec.json').read_text())
        for key in ['geometry','flow','numerics','domain','boundary_roles']:
            self.assertEqual(s[key],original[key])
        for key,value in original['quality'].items():
            if key!='time_step_sensitivity':self.assertEqual(s['quality'][key],value)
        self.assertTrue(s['quality']['time_step_sensitivity'])
        self.assertEqual(s['engineering_target']['drag_coefficient']['status'],'NOT_ESTABLISHED')
        self.assertIsNone(s['engineering_target']['drag_coefficient']['value'])
        self.assertTrue(validate_case_spec(s).valid)

    def test_per_step_residuals_no_wall_clock_and_fail_record(self):
        self.assertIn('sim.getSimulationIterator().step(1)',SOURCE)
        self.assertIn('int first=counts.get(key)',SOURCE)
        self.assertIn('boolean usable=minDecay>=3.0',SOURCE)
        self.assertIn('wall_clock_stop_enabled\\\":false',SOURCE)
        self.assertNotIn('BUDGET_SECONDS',SOURCE)
        self.assertIn('quote(error.toString())',SOURCE)

    def test_actual_failure_escaper_executes_controls_and_raw_suffix_is_refused(self):
        from tests.test_run_record_gate import _verified_java_escapers, _safe_error_callsite
        verified=_verified_java_escapers(SOURCE)
        self.assertIn('quote',verified)
        matches=list(re.finditer(r'error\\":(?:\\")?',SOURCE))
        self.assertTrue(matches)
        self.assertTrue(all(_safe_error_callsite(SOURCE,m,verified) for m in matches))
        changed=SOURCE.replace('+quote(error.toString())+', '+quote(error.toString())+error.toString()+')
        self.assertFalse(all(_safe_error_callsite(changed,m,verified)
            for m in re.finditer(r'error\\":(?:\\")?',changed)))


if __name__=='__main__':unittest.main()
