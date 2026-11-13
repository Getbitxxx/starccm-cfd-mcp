"""R18 fixes only absent-output probes; execute both delivered Java helpers."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts.run_record_gate import (citation_status, NOT_CITABLE_NO_CLAIM,
                                    NOT_CITABLE_FAILED_RUN)

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / 'test-evidence/original-geometry/r17'
MACROS = ['RecoverRotorSolidsFromSectorFluid.java', 'BuildMrfFluidVolumes.java']
R18 = ROOT / 'test-evidence/original-geometry/r18'
RUN = R18 / 'run/original-r18-20261005a'
FROZEN_RECOVERY = R18 / 'run/execution-source/macros/RecoverRotorSolidsFromSectorFluid.java'


def frozen_shell_analysis():
    """Run the executed Java shell/selector bodies on this run's actual raw STL.

    The selector is called once with the frozen CaseSpec cap. Additional area and
    candidate flags are diagnostic measurements, and never supply a success gate.
    """
    production = FROZEN_RECOVERY.read_text()
    methods = production[production.index('    static final class Shell {'):
                         production.index('    private static String sha(')]
    spec = json.loads((RUN / 'case_spec.json').read_text())
    bounds = spec['recovered_solid_bounds_m']
    numbers = [*bounds, spec['recovered_solid_volume_min_m3'],
               spec['recovered_solid_volume_max_m3'],
               spec['recovery_artifact_volume_ratio_max']]
    harness_source = '''import java.io.*;import java.nio.*;import java.nio.file.*;
import java.util.*;public class R18ShellHarness {
''' + methods + '''
static double area(byte[]bytes,Shell shell){
 ByteBuffer b=ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);double total=0;
 for(int i:shell.triangles){double[][]v=new double[3][3];
  for(int j=0;j<3;j++)for(int a=0;a<3;a++)v[j][a]=b.getFloat(84+50*i+12+12*j+4*a);
  double[]u={v[1][0]-v[0][0],v[1][1]-v[0][1],v[1][2]-v[0][2]};
  double[]w={v[2][0]-v[0][0],v[2][1]-v[0][1],v[2][2]-v[0][2]};
  double x=u[1]*w[2]-u[2]*w[1],y=u[2]*w[0]-u[0]*w[2],z=u[0]*w[1]-u[1]*w[0];
  total+=Math.sqrt(x*x+y*y+z*z)/2;
 }return total;
}
public static void main(String[]args)throws Exception{
 byte[]raw=Files.readAllBytes(Paths.get(args[0]));List<Shell>all=shells(raw);
 double[]bounds=new double[6];for(int a=0;a<6;a++)bounds[a]=Double.parseDouble(args[a+1]);
 double low=Double.parseDouble(args[7]),high=Double.parseDouble(args[8]),cap=Double.parseDouble(args[9]);
 System.out.print("{\\\"components\\\":[");int count=0;
 for(Shell s:all){boolean inside=true;for(int a=0;a<3;a++)if(s.bounds[a]<bounds[a]||s.bounds[a+3]>bounds[a+3])inside=false;
  boolean candidate=inside&&s.signedVolume>=low&&s.signedVolume<=high&&s.boundaryEdges==0&&s.nonmanifoldEdges==0;
  String component=s.json(false);if(count++>0)System.out.print(",");
  System.out.print(component.substring(0,component.length()-1)+",\\\"surface_area_m2\\\":"+area(raw,s)+",\\\"candidate_before_artifact_gate\\\":"+candidate+"}");
 }
 System.out.print("],\\\"selector_passed\\\":");
 try{selectShell(all,bounds,low,high,cap);System.out.print("true,\\\"selector_error\\\":null}");}
 catch(IllegalStateException e){System.out.print("false,\\\"selector_error\\\":\\\""+e.getMessage()+"\\\"}");}
}
}'''
    with tempfile.TemporaryDirectory() as temporary:
        harness = Path(temporary) / 'R18ShellHarness.java'
        harness.write_text(harness_source)
        subprocess.run(['javac', str(harness)], check=True, capture_output=True, text=True)
        output = subprocess.run(['java', '-cp', temporary, 'R18ShellHarness',
                                 str(RUN / 'recovered_rotor_raw.stl'),
                                 *(str(number) for number in numbers)],
                                check=True, capture_output=True, text=True)
    return json.loads(output.stdout)


def production_helper(source):
    start = source.index('    static boolean outputExists(')
    open_brace = source.index('{', start)
    depth = 1
    cursor = open_brace + 1
    while depth:
        depth += (source[cursor] == '{') - (source[cursor] == '}')
        cursor += 1
    return source[start:cursor]


class OriginalGeometryR18(unittest.TestCase):
    def test_real_r17_missing_output_error_remains_an_uncitable_failure(self):
        actual = OLD / 'run-c/original-r17-20261005c/recovered_rotor_solids.json'
        failed = json.loads(actual.read_text())
        self.assertEqual(failed['status'], 'error')
        self.assertIn('Named object "ORIGINAL_RECOVERY_RAW" does not exist', failed['error'])
        self.assertEqual(citation_status(actual)['state'], NOT_CITABLE_NO_CLAIM)

    def test_r17_runtime_snapshots_remain_unchanged(self):
        frozen = json.loads((OLD / 'run-c/attempt-c-freeze.json').read_text())['files']
        for macro in MACROS:
            path = OLD / 'run-c/execution-source-c/macros' / macro
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), frozen['macros/' + macro])
            self.assertNotEqual(path.read_bytes(), (ROOT / 'macros' / macro).read_bytes())

    @unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'Local Java compiler unavailable')
    def test_actual_java_helpers_allow_absence_and_detect_each_existing_output(self):
        spec = json.loads((ROOT / 'cases/original-geometry/r17/case_spec.json').read_text())
        recovery = [spec['recovery_raw_part'], spec['recovered_rotor_part']]
        geometry = spec['geometry']
        volumes = [geometry[key] for key in ('stationary_fluid_part', 'rotating_fluid_part',
                                            'outer_cylinder_part', 'rotation_envelope_part')]
        sources = [(ROOT / 'macros' / macro).read_text() for macro in MACROS]
        # Compile the exact production method bodies, not another checker implementation.
        source = ('import java.util.*; public class R18OutputHarness {'
                  'static class Recovery {' + production_helper(sources[0]) + '}'
                  'static class Volumes {' + production_helper(sources[1]) + '}'
                  'public static void main(String[]args){'
                  'List<String>actual=Arrays.asList("user sector","user fluid enclosure");'
                  'for(int i=0;i<args.length;i++){String selected=args[i];'
                  'boolean missing=i<2?Recovery.outputExists(actual,selected):Volumes.outputExists(actual,selected);'
                  'if(missing)throw new AssertionError("absent output must be allowed");'
                  'List<String>present=new ArrayList<>(actual);present.add(selected);'
                  'boolean exists=i<2?Recovery.outputExists(present,selected):Volumes.outputExists(present,selected);'
                  'if(!exists)throw new AssertionError("existing output must be detected");'
                  'if(Recovery.outputExists(present,selected+" suffix"))throw new AssertionError("match must be exact");'
                  'if(Volumes.outputExists(present,selected+" suffix"))throw new AssertionError("match must be exact");'
                  '} System.out.println("all six actual production checks passed");}}')
        with tempfile.TemporaryDirectory() as temporary:
            harness = Path(temporary) / 'R18OutputHarness.java'
            harness.write_text(source)
            subprocess.run(['javac', str(harness)], check=True, capture_output=True, text=True)
            result = subprocess.run(['java', '-cp', temporary, 'R18OutputHarness', *recovery, *volumes],
                                    check=True, capture_output=True, text=True)
        self.assertIn('all six actual production checks passed', result.stdout)

    def test_enumerated_output_checks_preserve_strict_required_input_queries(self):
        recovery, volumes = [(ROOT / 'macros' / macro).read_text() for macro in MACROS]
        self.assertNotIn('parts.getPart(rawName)', recovery)
        self.assertNotIn('parts.getPart(solidName)', recovery)
        self.assertNotIn('parts.getPart(selected)', volumes)
        for source in (recovery, volumes):
            self.assertIn('for(GeometryPart actual:parts.getObjects())', source)
            self.assertIn('actualPartNames.add(actual.getPresentationName())', source)
            self.assertIn('GeometryPart', source)
            self.assertIn('parts.getPart(name)', source)
            self.assertIn('already exists', source)
        self.assertIn('outputExists(actualPartNames,rawName) || outputExists(actualPartNames,solidName)', recovery)
        self.assertIn('if(outputExists(actualPartNames,selected))throw', volumes)

    @unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'Local Java compiler unavailable')
    def test_actual_r18_boolean_residue_fails_the_frozen_production_gate(self):
        # This is the newly executed R18 Boolean, never the historical 73-shell fixture.
        raw = RUN / 'recovered_rotor_raw.stl'
        self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(),
                         'a6e17f3f34b23ce1993953bf8a022aba259938348685178b251ecfda6fb22759')
        frozen = json.loads((R18 / 'frozen-files.json').read_text())['files']
        self.assertEqual(hashlib.sha256(FROZEN_RECOVERY.read_bytes()).hexdigest(),
                         frozen['macros/RecoverRotorSolidsFromSectorFluid.java'])
        spec = json.loads((RUN / 'case_spec.json').read_text())
        self.assertEqual(spec['recovery_artifact_volume_ratio_max'], 1e-4)
        measured = frozen_shell_analysis()
        self.assertFalse(measured['selector_passed'])
        actual_failure = json.loads((RUN / 'recovered_rotor_solids.json').read_text())
        self.assertEqual(actual_failure['status'], 'error')
        self.assertEqual(actual_failure['error'],
                         'java.lang.IllegalStateException: ' + measured['selector_error'])
        self.assertEqual(measured['selector_error'],
                         'Boolean residue exceeds explicit artifact-volume budget: 0.004698165776761509')
        components = measured['components']
        self.assertEqual(len(components), 74)
        self.assertEqual(sum(s['triangles'] for s in components), 11074)
        candidates = [s for s in components if s['candidate_before_artifact_gate']]
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate['triangles'], 8712)
        self.assertAlmostEqual(candidate['signed_volume_m3'], 0.00013665646368124016, places=18)
        self.assertEqual(candidate['boundary_edges'], 0)
        self.assertEqual(candidate['nonmanifold_edges'], 0)
        self.assertFalse(candidate['selected'])
        residue_volume = sum(abs(s['signed_volume_m3']) for s in components
                             if not s['candidate_before_artifact_gate'])
        self.assertAlmostEqual(residue_volume, 6.420347208404547e-7, places=20)
        ratio = residue_volume / candidate['signed_volume_m3']
        self.assertAlmostEqual(ratio, 0.004698165776761509, places=17)
        self.assertGreater(ratio, spec['recovery_artifact_volume_ratio_max'])
        self.assertEqual(sum(s['nonmanifold_edges'] for s in components), 4)
        self.assertFalse((RUN / 'recovered_rotor_solids.stl').exists())
        self.assertFalse((RUN / 'recovered_rotor_solids.sim').exists())

    def test_r18_actual_failed_stage_cannot_certify_its_ok_first_macro(self):
        stage = json.loads((RUN / 'stage-geometry.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(stage['state'], 'failed')
        first, recovery, volumes = stage['macros']
        self.assertTrue(first['ran'])
        self.assertEqual(first['status'], 'ok')
        self.assertEqual(first['seconds'], 14.72)
        self.assertTrue(recovery['ran'])
        self.assertEqual(recovery['status'], 'failed')
        self.assertEqual(recovery['seconds'], 15.13)
        self.assertFalse(volumes['ran'])
        self.assertEqual(volumes['status'], 'not_run')
        self.assertEqual(citation_status(RUN / 'full_rotor_360_build.json')['state'],
                         NOT_CITABLE_FAILED_RUN)
        self.assertEqual(citation_status(RUN / 'recovered_rotor_solids.json')['state'],
                         NOT_CITABLE_NO_CLAIM)
        self.assertEqual(hashlib.sha256((RUN / 'recovered_rotor_raw.sim').read_bytes()).hexdigest(),
                         '734f8db24e80271b9cffd3188b75acae33d84becf1b1287e322ae3f681b17e05')
        log = (RUN / 'star-geometry-1-RecoverRotorSolidsFromSectorFluid.log').read_text()
        self.assertIn('Executing Discrete based Boolean Subtract operation', log)
        self.assertIn('recovered_rotor_raw.sim... complete', log)


if __name__ == '__main__':
    unittest.main()
