# -*- coding: utf-8 -*-
"""Execute the macro's pure pre-mutation parsing against a normal JVM; no STAR."""
import json, subprocess, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class ExplicitMeshParsing(unittest.TestCase):
    def test_real_pre_mutation_block_rejects_invalid_controls(self):
        source=(ROOT/'macros/BuildExternalFlowMesh.java').read_text()
        block=source[source.index('String[] localKeys='):source.index('MeshPart part = locatePart')]
        helpers=[]
        for name in ('has','array','number'):
            start=source.index('    private static ', source.index('    private static boolean has')) if name=='has' else source.index('    private static double'+('[]' if name=='array' else '')+' '+name+'(')
            end=source.index('    private static ',start+5) if name!='number' else source.index('    private static double unitToMeters',start)
            # Each helper ends before the next method declaration.
            helpers.append(source[start:end])
        program='import java.util.regex.*; public class MeshParserTest { public static void main(String[] args) { String spec=args[0];double baseSize=.012;int prismLayers=6;'+block+'System.out.println("ACCEPT"); }'+''.join(helpers)+'}'
        valid={'outer_bounds_m':[-.6,-.5,-.5,1.2,.5,.5], 'mesh_nearfield_bounds_m':[-.06,-.06,-.08,.12,.06,.08], 'mesh_nearfield_cell_size_m':.003,'mesh_wake_bounds_m':[.06,-.09,-.08,.78,.09,.08],'mesh_wake_cell_size_m':.006,'mesh_prism_first_layer_height_m':.000192461963883,'mesh_prism_total_thickness_m':.0039996}
        bad=[]
        for key,value in [('mesh_nearfield_cell_size_m',0),('mesh_wake_cell_size_m',.013),('mesh_prism_total_thickness_m',.0001),('mesh_prism_first_layer_height_m',0),('mesh_wake_bounds_m',[.06,-.09,-.08,1.3,.09,.08]),('mesh_nearfield_bounds_m',[0,0,0,0,.06,.08]),('mesh_wake_bounds_m',[0,1])]:
            x=dict(valid);x[key]=value;bad.append(json.dumps(x))
        for key in ('mesh_wake_cell_size_m','mesh_prism_total_thickness_m'):
            x=dict(valid);del x[key];bad.append(json.dumps(x))
        bad.append(json.dumps(valid)[:-1]+',"mesh_wake_cell_size_m":0.006}')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'MeshParserTest.java').write_text(program)
            compiled=subprocess.run(['/usr/bin/javac',str(p/'MeshParserTest.java')],capture_output=True,text=True)
            self.assertEqual(0,compiled.returncode,compiled.stderr)
            def execute(spec):return subprocess.run(['/usr/bin/java','-cp',d,'MeshParserTest',spec],capture_output=True,text=True)
            for positive in (valid,{}, {k:v for k,v in valid.items() if 'prism' not in k},{k:v for k,v in valid.items() if 'bounds' not in k and 'cell_size' not in k}):
                self.assertEqual(0,execute(json.dumps(positive)).returncode)
            for negative in bad:
                self.assertNotEqual(0,execute(negative).returncode,negative)

class HistoricalMeshRevisionBinding(unittest.TestCase):
    def test_historical_native_report_reads_but_new_study_reuse_refuses_old_producer(self):
        from tests.test_sensitivity_study import driver,load,CurrentSuccessfulCheckpointDelivery
        fixture=CurrentSuccessfulCheckpointDelivery()
        metrics=driver.collect_metrics(fixture.evidence,fixture.trial_id)
        self.assertIn('record_paths',metrics['checkpoint_lineage'])
        contract=load(ROOT/'test-evidence/static-release-candidate/engineering-contract-20261007/u12-checkpoint-contract.json')
        spec=load(ROOT/'cases/static-cylinder-step/engineering-preparation-20261007/domain/d1p00/case_spec.json')
        result=driver.reused_reference(fixture.evidence,fixture.trial_id,{'reuse':{'evidence':str(fixture.evidence),'trial_id':fixture.trial_id}},spec,ROOT/contract['geometry_source_sim'])
        self.assertFalse(result['ok'])
        self.assertIn('checkpoint preprocessing producer revision differs: BuildExternalFlowMesh.java',result['differences'])
        changed=dict(spec);changed['mesh_wake_cell_size_m']=.006
        result=driver.reused_reference(fixture.evidence,fixture.trial_id,{'reuse':{'evidence':str(fixture.evidence),'trial_id':fixture.trial_id}},changed,ROOT/contract['geometry_source_sim'])
        self.assertIn('mesh_wake_cell_size_m',result['differences'])

class RealFailedPrismOwnerRegression(unittest.TestCase):
    def test_failed_manager_lookup_is_reproduced_and_repaired_owner_path_executes(self):
        """Model the observed manager failure; this is not a STAR runtime success certificate."""
        run=ROOT/'test-evidence/static-release-candidate/remediation-20261008/r2/medium/static-rem-r2-medium-20261008-s1'
        failure=json.loads((run/'external_flow_mesh.json').read_text())
        self.assertEqual('star.base.neo.NeoException',failure['error_class'])
        self.assertIn('PrismStretchingOption',failure['error'])
        self.assertIn('Condition not found',failure['error'])
        from tests.test_sensitivity_study import driver
        with self.assertRaises(driver.run_record_gate.RecordNotCitable):
            driver.run_record_gate.assert_citable(run/'external_flow_mesh.json')
        source=(ROOT/'macros/BuildExternalFlowMesh.java').read_text()
        helper=source[source.index('    private static PrismAutoMesher prismMesher'):source.index('    private static final String COMPLETE')]
        repaired='prismMesher(operation).getPrismStretchingOption().setSelected(PrismStretchingOption.Type.WALL_THICKNESS);'
        self.assertIn(repaired,source)
        self.assertIn('prismMesher(op).getPrismStretchingOption()', (ROOT/'macros/AuditExternalFlowMesh.java').read_text())
        program='''import java.util.*;
class MesherBase {}
class PrismStretchingOption {enum Type {WALL_THICKNESS}; Type value; void setSelected(Type x){value=x;}}
class PrismAutoMesher extends MesherBase {PrismStretchingOption option=new PrismStretchingOption(); PrismStretchingOption getPrismStretchingOption(){return option;}}
class MissingManager { <T> T get(Class<T> c){throw new IllegalStateException("Condition not found");} }
class AutoMeshOperation {List<MesherBase> meshers=new ArrayList<>(); MissingManager getDefaultValues(){return new MissingManager();} List<MesherBase> getMeshersCollection(){return meshers;} }
public class PrismOwnerRegression {'''+helper+''' public static void main(String[] args){AutoMeshOperation operation=new AutoMeshOperation();PrismAutoMesher actual=new PrismAutoMesher();operation.meshers.add(actual);
boolean rejected=false;try{operation.getDefaultValues().get(PrismStretchingOption.class);}catch(IllegalStateException e){rejected=true;}if(!rejected)throw new AssertionError("Old defect accepted");
'''+repaired+'''
if(actual.option.value!=PrismStretchingOption.Type.WALL_THICKNESS)throw new AssertionError("Owner not changed");
operation.meshers.add(new PrismAutoMesher());try{prismMesher(operation);throw new AssertionError("Duplicate accepted");}catch(IllegalStateException expected){}
operation.meshers.clear();try{prismMesher(operation);throw new AssertionError("Missing accepted");}catch(IllegalStateException expected){}
}}
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'PrismOwnerRegression.java').write_text(program)
            compile_result=subprocess.run(['/usr/bin/javac',str(p/'PrismOwnerRegression.java')],capture_output=True,text=True)
            self.assertEqual(0,compile_result.returncode,compile_result.stderr)
            result=subprocess.run(['/usr/bin/java','-cp',d,'PrismOwnerRegression'],capture_output=True,text=True)
            self.assertEqual(0,result.returncode,result.stderr)
