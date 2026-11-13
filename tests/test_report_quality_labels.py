"""E labels must never be inferred from P/PAT or failed-stage successes."""
import json
import sys
import tempfile
import subprocess
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import generate_run_report as report
class QualityLabels(unittest.TestCase):
    def label(self,payload,citable=True):
        name='external_flow_validation.json'
        return report.quality_labels({name:payload},{name:{'citation_state':report.gate.CITABLE if citable else 'NOT_CITABLE_FAILED_RUN','sha256':'a'*64}})
    def test_missing_numeric_checks_are_not_passes(self):
        labels=self.label({'status':'ok'})
        self.assertEqual('NOT_ESTABLISHED',labels['E-num']['status'])
        self.assertEqual('NOT_APPLICABLE',labels['E-phys']['status'])
    def test_reference_failure_does_not_hide_numerical_checks(self):
        labels=self.label({'residuals_met':True,'mass_balanced':True,'target_quantities_steady':True,
            'reference_status':'ESTABLISHED','reference_value':.396,'reference_policy':'AEDC source',
            'reference_acceptance_tolerance':.0224,'relative_error_against_reference':.47})
        self.assertEqual('PASS',labels['E-num']['checks']['convergence']['status'])
        self.assertEqual('NOT_ESTABLISHED',labels['E-num']['status'])
        self.assertEqual('FAIL',labels['E-phys']['status'])
    def test_failed_record_cannot_establish_labels(self):
        labels=self.label({'residuals_met':True,'mass_balanced':True,'reference_status':'ESTABLISHED',
             'reference_value':1,'reference_policy':'source','reference_acceptance_tolerance':.1,
             'relative_error_against_reference':0},False)
        self.assertEqual('NOT_ESTABLISHED',labels['E-num']['status'])
        self.assertEqual('NOT_ESTABLISHED',labels['E-phys']['status'])
    def test_nonfinite_error_or_missing_tolerance_is_never_pass(self):
        for value in [None,float('nan'),float('inf'),True,-1]:
            labels=self.label({'reference_status':'ESTABLISHED','reference_value':1,'reference_policy':'source',
                 'reference_acceptance_tolerance':.1,'relative_error_against_reference':value})
            self.assertEqual('NOT_ESTABLISHED',labels['E-phys']['status'])
    def test_unnamed_reference_source_cannot_pass(self):
        labels=self.label({'reference_status':'ESTABLISHED','reference_value':1,
          'reference_policy':'reference provided by the CaseSpec: source not named; uncertainty basis not named',
          'reference_acceptance_tolerance':.1,'relative_error_against_reference':0})
        self.assertEqual('NOT_ESTABLISHED',labels['E-phys']['status'])
    def test_json_and_markdown_keep_product_gate_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);trial='quality-test';(p/trial).mkdir()
            (p/(trial+'-result.json')).write_text(json.dumps({'state':'AIR3B_PIPELINE_COMPLETE','gate_passed':True}))
            (p/trial/'stage-audit.json').write_text(json.dumps({'stage':'audit','state':'ok','artifacts':[]}))
            r=subprocess.run([sys.executable,str(ROOT/'scripts/generate_run_report.py'),'--trial-id',trial,'--evidence',str(p)],capture_output=True,text=True)
            self.assertEqual(0,r.returncode,r.stderr)
            data=json.loads((p/(trial+'-report.json')).read_text())
            self.assertEqual('PASS',data['P']['status'])
            self.assertEqual('NOT_EVALUATED',data['PAT']['status'])
            self.assertEqual('NOT_ESTABLISHED',data['E-num']['status'])
            self.assertFalse(data['engineering_verified'])
            md=(p/(trial+'-report.md')).read_text()
            self.assertIn('E-num',md);self.assertIn('E-phys',md);self.assertIn('PAT',md)
if __name__=='__main__':unittest.main()
