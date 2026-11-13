"""Keep real failed readbacks and original source identity closed during diagnostic extension."""
import importlib.util, json, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('static_diagnostics',ROOT/'cases/static-cylinder-step/summarize_static_diagnostics.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
CURRENT=ROOT/'test-evidence/static-release-candidate/engineering-execution-20261007/u12-property-20261008/static-eng-u12-props-20261008'
SOURCE='2c06ba6e78906c6bca96e98cbb8e082e00c50965a352a2db8c12d2ced7589239'
SPEC='f79b7a06c2d86432b11b55c96d9a46a91b27a7bad22213a7ec8a4f668d73574b'
class NativeDiagnosticSafety(unittest.TestCase):
    def test_current_real_readback_identity(self):
        record,citation=module.load_readback(CURRENT,SOURCE,SPEC,1150)
        self.assertEqual('CITABLE',citation['state']);self.assertEqual(76930,record['cells'])
    def test_foreign_source_original_spec_and_iteration_rejected(self):
        for source,spec,iteration in [('0'*64,SPEC,1150),(SOURCE,'0'*64,1150),(SOURCE,SPEC,1000)]:
            with self.assertRaises(ValueError):module.load_readback(CURRENT,source,spec,iteration)
    def test_existing_positive_readback_cannot_certify_unexecuted_diagnostics(self):
        with self.assertRaisesRegex(ValueError,'not executed'):module.summarize(CURRENT,SOURCE,SPEC,1150)
    def test_real_r29_failed_stage_cannot_be_promoted(self):
        failed=ROOT/'test-evidence/static-cylinder-step/r29-readback/static-cylinder-r29-readback-20261006a'
        with self.assertRaises(Exception):module.load_readback(failed,SOURCE,SPEC,1150)
    def test_native_parser_rejects_ambiguous_header_and_nonfinite(self):
        fields=[{'requested_id':'Volume','function_name':'Volume','presentation_name':'Volume'}]
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'native.csv'
            for content in ['X,Y,Z,Volume,Volume\n0,0,0,1,1\n','X,Y,Z,Volume\n0,0,0,nan\n']:
                p.write_text(content)
                with self.assertRaises(ValueError):module.native_table(p,fields)
    def test_consumed_file_present_but_missing_recorded_hash_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for name in ['case_spec.json','stage-property-nearwall.json']:
                (root/name).write_bytes((CURRENT/name).read_bytes())
            record=module.read(CURRENT/'property_nearwall_readback.json')
            record['static_flow_diagnostics']={'boundaries':[{'file':'diagnostic-boundary-'+str(i)+'.csv'} for i in range(6)]}
            (root/'property_nearwall_readback.json').write_text(json.dumps(record))
            (root/'diagnostic-fixed-points.csv').write_text('X,Y,Z,Pressure\n.06,0,0,1\n')
            with self.assertRaisesRegex(ValueError,'missing from recorded hash manifest'):
                module.load_readback(root,SOURCE,SPEC,1150)
    def test_duplicate_or_missing_box_plane_rejected(self):
        good=[(k,sign) for k in range(3) for sign in [-1,1]]
        module.require_six_box_planes(good)
        for bad in [good[:-1],good[:-1]+[good[0]]]:
            with self.assertRaisesRegex(ValueError,'uniquely cover'):
                module.require_six_box_planes(bad)
    def test_optional_diagnostics_stay_inside_readonly_branch(self):
        source=(ROOT/'macros/ExportExternalFlowResults.java').read_text()
        legacy=source.split('private void executePropertyNearwall')[0]
        self.assertNotIn('diagnosticsFlag',legacy)
        readonly=source.split('private void executePropertyNearwall')[1].split('private void executeMrf')[0]
        for forbidden in ['saveState(','.initialize(','.step(','.execute()']:
            self.assertNotIn(forbidden,readonly)
        self.assertIn('start!=end||!before.equals(after)',readonly)
if __name__=='__main__':unittest.main()
