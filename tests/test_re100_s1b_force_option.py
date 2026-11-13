"""S1b force-option contract; real extracted Java helpers, no STAR execution."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT/'test-evidence/benchmark-re100-s1b-20261021'


class S1bForceOptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.solve = (ROOT/'macros/RunExternalFlowSolve.java').read_text()
        cls.audit = (ROOT/'macros/AuditExternalFlowConvergence.java').read_text()
        setter = cls.solve[cls.solve.index('    private static String setForceOption('):cls.solve.index('    private static String forceReportParts(')]
        check = cls.audit[cls.audit.index('    private static String requirePressureAndShear('):cls.audit.index('    /**', cls.audit.index('    private static String requirePressureAndShear('))]
        root_string = cls.audit[cls.audit.index('    private static String rootString('):cls.audit.index('    private static double number(')]
        cls.temp = tempfile.TemporaryDirectory(prefix='s1b-force-')
        source = '''public class ForceOptionProbe {
static class ForceReportForceOption {
 enum Type { PRESSURE, SHEAR, PRESSURE_AND_SHEAR }
 Type selected; boolean accepts;
 void setSelected(Type t) { if (accepts) selected = t; }
 Type getSelectedElement() { return selected; }
}
static class ForceReport {
 ForceReportForceOption option = new ForceReportForceOption();
 ForceReportForceOption getForceOption() { return option; }
}
''' + setter + check + root_string + '''
public static void main(String[] args) {
 if (args[0].equals("audit")) System.out.println(requirePressureAndShear(args[1]));
 else {
  ForceReport report = new ForceReport();
  report.option.selected = ForceReportForceOption.Type.valueOf(args[1]);
  report.option.accepts = Boolean.parseBoolean(args[2]);
  System.out.println(setForceOption(report, ForceReportForceOption.Type.PRESSURE_AND_SHEAR));
 }
}
}'''
        path = Path(cls.temp.name)/'ForceOptionProbe.java';path.write_text(source)
        subprocess.run(['/usr/bin/javac', str(path)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_probe(self, *args):
        return subprocess.run(['/usr/bin/java','-cp',self.temp.name,'ForceOptionProbe',*args], capture_output=True,text=True)

    def test_explicit_set_and_immediate_readback_before_solve(self):
        call = 'setForceOption(drag, ForceReportForceOption.Type.PRESSURE_AND_SHEAR)'
        self.assertIn(call, self.solve)
        self.assertLess(self.solve.index(call), self.solve.index('initializeSolution()'))
        helper = self.solve[self.solve.index('private static String setForceOption('):self.solve.index('private static String forceReportParts(')]
        self.assertLess(helper.index('.setSelected(requested)'), helper.index('.getSelectedElement()'))
        self.assertIn('throw new IllegalStateException',helper)
        p=self.run_probe('set','PRESSURE','true')
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(p.stdout.strip(),'PRESSURE_AND_SHEAR')
        for field in ('force_option','force_option_readback','force_report_name','force_report_parts'):
            self.assertIn('\\"'+field+'\\"',self.solve)

    def test_setter_readback_mismatch_throws(self):
        for actual in ('PRESSURE','SHEAR'):
            with self.subTest(actual=actual):
                p=self.run_probe('set',actual,'false')
                self.assertNotEqual(p.returncode,0)
                self.assertIn('Force option readback differs',p.stderr)
                self.assertEqual(p.stdout,'')

    def test_audit_accepts_matching_readback(self):
        p=self.run_probe('audit',json.dumps({'force_option':'PRESSURE_AND_SHEAR','force_option_readback':'PRESSURE_AND_SHEAR'}))
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(p.stdout.strip(),'PRESSURE_AND_SHEAR')

    def test_audit_rejects_missing_single_contribution_and_mismatch(self):
        fixtures=[{}, {'force_option':'PRESSURE_AND_SHEAR'},
                  {'force_option_readback':'PRESSURE_AND_SHEAR'},
                  {'force_option':'PRESSURE_ONLY','force_option_readback':'PRESSURE_ONLY'},
                  {'force_option':'SHEAR_ONLY','force_option_readback':'SHEAR_ONLY'},
                  {'force_option':'PRESSURE_AND_SHEAR','force_option_readback':'PRESSURE_ONLY'},
                  {'force_option':'SHEAR_ONLY','force_option_readback':'PRESSURE_AND_SHEAR'},
                  {'nested':{'force_option':'PRESSURE_AND_SHEAR','force_option_readback':'PRESSURE_AND_SHEAR'}}]
        for record in fixtures:
            with self.subTest(record=record):
                p=self.run_probe('audit',json.dumps(record))
                self.assertNotEqual(p.returncode,0)
                self.assertIn('Solve force_option/readback must both be PRESSURE_AND_SHEAR',p.stderr)
        self.assertLess(self.audit.index('String stage1ForceOption = requirePressureAndShear('),self.audit.index('String regionName ='))
        self.assertIn('force_option_check_passed',self.audit)

    def test_prior_real_s1_record_is_rejected(self):
        record=ROOT/'test-evidence/benchmark-re100-s1-20261020/pilot-b/raw/stages/run_external_flow_solve/external_flow_solve.json'
        p=self.run_probe('audit',record.read_text())
        self.assertNotEqual(p.returncode,0)

    def test_all_other_solve_bytes_are_unchanged(self):
        text=self.solve
        text=text.replace('            String forceOption = setForceOption(drag, ForceReportForceOption.Type.PRESSURE_AND_SHEAR);\n            String forceReportParts = forceReportParts(drag);\n','',1)
        text=''.join(line for line in text.splitlines(keepends=True) if not any('\\"'+key+'\\"' in line for key in ('force_option','force_option_readback','force_report_name','force_report_parts')))
        start=text.index('    // Installed 2502 API exposes selection')
        end=text.index('    /** Merge the residual exports',start)
        text=text[:start]+text[end:]
        self.assertEqual(text,(EVIDENCE/'RunExternalFlowSolve.java.before').read_text())

    def test_all_other_audit_bytes_are_unchanged(self):
        text=self.audit.replace('            String stage1ForceOption = requirePressureAndShear(new String(Files.readAllBytes(\n                    new File(dir, "external_flow_solve.json").toPath()), StandardCharsets.UTF_8));\n','',1)
        text=''.join(line for line in text.splitlines(keepends=True) if not any('\\"'+key+'\\"' in line for key in ('force_option_checked','force_option_check_passed')))
        start=text.index('    private static String requirePressureAndShear(')
        end=text.index('    /**',start)
        text=text[:start]+text[end:]
        self.assertEqual(text,(EVIDENCE/'AuditExternalFlowConvergence.java.before').read_text())


if __name__=='__main__':unittest.main(verbosity=2)
