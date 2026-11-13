"""Execute the actual Java reference parser/verdict against the false-VALIDATED fixture."""
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class ActualReferenceComparison(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'macros/AuditExternalFlowConvergence.java').read_text()
        methods = []
        for signature in ('String\\[\\] referenceBlock', 'String referenceComparisonVerdict'):
            match = re.search(r'    private static ' + signature + r'\(.*?\n    }', cls.source, re.S)
            if not match:raise AssertionError('actual Java helper missing: ' + signature)
            methods.append(match.group(0))
        # Execute the actual basename/default guard, replacing only the environment read with
        # an explicit test argument. The SHA/identity binding below it remains in the real macro.
        begin = cls.source.index('            String sourceStageRecord = System.getenv(')
        end = cls.source.index('            File stageRecord =', begin)
        guard = cls.source[begin:end].replace(
            'System.getenv("EXTERNAL_FLOW_SOURCE_SOLVE_STAGE_RECORD")', 'configured')
        methods.append('private static String configuredRecord(String configured) {\n' + guard
                       + '\n return sourceStageRecord;\n}')
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        java = 'import java.util.regex.*; public class ReferenceProbe {\n' + '\n'.join(methods) + '''
        public static void main(String[] args) {
            if (args[0].equals("record")) {
                try { System.out.println(configuredRecord(args[1])); }
                catch (IllegalArgumentException expected) { System.out.println("REJECTED"); }
            } else if (args[0].equals("parse")) {
                String[] ref = referenceBlock(args[1]);
                System.out.println(ref[0]+"|"+ref[1]+"|"+ref[2]);
            } else System.out.println(referenceComparisonVerdict(Boolean.parseBoolean(args[0]),
                Double.parseDouble(args[1]),Double.parseDouble(args[2])));
        }}'''
        path = cls.directory / 'ReferenceProbe.java';path.write_text(java)
        subprocess.run(['javac', str(path)], check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def java(self, *args):
        return subprocess.run(['java', '-cp', str(self.directory), 'ReferenceProbe', *map(str,args)],
                              check=True, capture_output=True, text=True).stdout.strip()

    def test_real_47_percent_error_is_rejected_despite_historical_VALIDATED(self):
        base = ROOT / 'test-evidence/engineering-validation-start-20261009/e-aedc-sphere-m0p204'
        actual = json.loads((base / 'solve/s1-aedc-sphere-solve-20261014b/external_flow_validation.json').read_text())
        spec = json.loads((base / 'domain-study/d1p50/case_spec.json').read_text())
        self.assertEqual(actual['validation_verdict'], 'VALIDATED')
        error = actual['relative_error_against_reference']
        tolerance = spec['engineering_target']['drag_coefficient']['acceptance_tolerance']
        self.assertGreater(error, tolerance)
        self.assertEqual(self.java('true', error, tolerance), 'NOT_WITHIN_REFERENCE_TOLERANCE')

    def test_equal_boundary_is_only_reference_agreement(self):
        self.assertEqual(self.java('true', .0224, .0224), 'REFERENCE_WITHIN_TOLERANCE')
        self.assertNotIn('else verdict = "VALIDATED";', self.source)
        self.assertIn('engineering_verified\\\":false', self.source)

    def test_missing_nonpositive_or_nan_tolerance_is_not_validated(self):
        for value in ('NaN', '0', '-1'):
            self.assertEqual(self.java('true', .001, value), 'NOT_VALIDATED_NO_TOLERANCE')

    def test_unusable_reference_or_nonfinite_error_is_refused(self):
        self.assertEqual(self.java('false', .001, .02), 'NOT_VALIDATED_NO_REFERENCE')
        self.assertEqual(self.java('true', 'NaN', .02), 'NOT_VALIDATED_UNMEASURED_REFERENCE_ERROR')

    def test_parser_uses_drag_reference_block_tolerance(self):
        spec = {'acceptance_tolerance':999,'engineering_target':{'drag_coefficient':
            {'status':'ESTABLISHED','value':.396,'acceptance_tolerance':.0224}}}
        self.assertEqual(self.java('parse', json.dumps(spec)), 'ESTABLISHED|0.396|0.0224')
        del spec['engineering_target']['drag_coefficient']['acceptance_tolerance']
        self.assertEqual(self.java('parse', json.dumps(spec)), 'ESTABLISHED|0.396|null')

    def test_source_record_defaults_and_rejects_paths(self):
        self.assertEqual(self.java('record', ''), 'stage-solve.json')
        self.assertEqual(self.java('record', 'source-solve-stage.json'), 'source-solve-stage.json')
        for invalid in ('../stage-solve.json', '/tmp/stage-solve.json', 'C:\\stage-solve.json',
                        'wrong.txt', 'x.json\n'):
            self.assertEqual(self.java('record', invalid), 'REJECTED')
        self.assertIn('inputSha.equals(stageOutputSha)', self.source)
        self.assertIn('boolean outputIdentityIsSim', self.source)

if __name__ == '__main__':unittest.main()
