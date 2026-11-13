"""R16 independent-review regressions: declared MRF readback and genuine input identity."""
import hashlib
import json
import unittest
import sys
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from air3b_pipeline_trial import check_review, hash_file
class R16Review(unittest.TestCase):
    def test_transient_does_not_opt_into_mrf_frame_gate(self):
        transient = json.loads((ROOT/'cases/air-three-blade/pipeline-transient-revolution.json').read_text())
        self.assertIn('AIR3B_RPM', transient['env'])
        self.assertIn('AIR3B_INFLOW_MPS', transient['env'])
        self.assertNotEqual(transient['env'].get('AIR3B_VERIFY_MRF_PARAMETERS'), 'true')
        code = (ROOT/'macros/AuditAir3bMassBalance.java').read_text()
        self.assertIn('"true".equals(System.getenv("AIR3B_VERIFY_MRF_PARAMETERS"))', code)
        self.assertLess(code.index('if ("true".equals(System.getenv("AIR3B_VERIFY_MRF_PARAMETERS")))'), code.index('ReferenceFrameBase assignedFrame'))
    def test_readback_uses_physical_inlet_and_assigned_frame(self):
        code = (ROOT/'macros/AuditAir3bMassBalance.java').read_text()
        new = code[code.index('// R16:'):code.index('// Target loads:')]
        self.assertIn('boundary.getBoundaryType() instanceof InletBoundary', new)
        self.assertIn('Ambiguous inlet role', new)
        self.assertIn('get(MotionSpecification.class).getReferenceFrame()', new)
        self.assertIn('Parameter declaration mismatch', new)
        self.assertNotIn('getRegion("', new)
        self.assertNotIn('getBoundary("', new)
        self.assertIn('.append(show(measuredAxis))', code)
        self.assertEqual(json.loads('{"measured_axis":[0,0,1]}')['measured_axis'], [0,0,1])
    def test_current_gate_refuses_tampered_macro_pin(self):
        evidence = ROOT/'test-evidence/air-three-blade/r16/rpm'
        review = json.loads((evidence/'pipeline-review.json').read_text())
        pins = dict(review['macro_hashes'])
        pins['AuditAir3bMassBalance.java'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'Reviewed hash differs'):
            check_review(evidence, pins, review['input_hashes'], review['runner_sha256'],
                         review['controller_sha256'], review['pipeline_sha256'])
    def test_real_failed_attempt_is_charged_and_never_cited(self):
        # O9: real R16 failure exposes runner ran=false/seconds=null after STAR
        # starts and exits nonzero. Preserve these raw bytes; never infer zero cost.
        from run_record_gate import citation_status, CITABLE
        failed = ROOT/'test-evidence/air-three-blade/r16/negative/air3b-r16-param-neg-20261004a'
        stage = json.loads((failed/'stage-parameter-negative.json').read_text(encoding='utf-8-sig'))
        macro = stage['macros'][0]
        self.assertEqual(stage['state'], 'failed')
        self.assertEqual(macro['exit_code'], 1)
        self.assertFalse(macro['ran'])  # measured runner bookkeeping defect
        self.assertIsNone(macro['seconds'])
        log = (failed/macro['log']).read_text(errors='replace')
        self.assertIn('Parameter declaration mismatch', log)
        self.assertIn('inlet=24.0', log)
        self.assertIn('inlet=30.0', log)
        self.assertGreater(stage['seconds'], 0)
        self.assertNotEqual(citation_status(failed/'air3b_massbalance.json')['state'], CITABLE)
        # An accidental status:ok artifact in this actual FAILED stage is rejected too.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            (tmp/'stage-parameter-negative.json').write_bytes((failed/'stage-parameter-negative.json').read_bytes())
            (tmp/'air3b_massbalance.json').write_text('{"status":"ok","source":"AuditAir3bMassBalance.java"}')
            self.assertNotEqual(citation_status(tmp/'air3b_massbalance.json')['state'], CITABLE)
        positive = ROOT/'test-evidence/air-three-blade/r16/rpm/air3b-r16-rpm-20261004a'
        known = sum(m['seconds'] for p in positive.glob('stage-*.json')
                    for m in json.loads(p.read_text(encoding='utf-8-sig'))['macros'])
        # Conservative negative stage wall is substituted once, never added to a
        # fabricated process time. Positive stages are not added a second time.
        charged = known + stage['seconds']
        self.assertAlmostEqual(known, 637.36, places=2)
        self.assertAlmostEqual(charged, 662.71, places=2)
    def test_frozen_pipeline_and_original_are_distinct(self):
        pipeline = json.loads((ROOT/'cases/air-three-blade/r16/pipeline-rpm.json').read_text())
        self.assertEqual(pipeline['env']['AIR3B_VERIFY_MRF_PARAMETERS'], 'true')
        # R17 changes current macros; R16 signature/config remains historical and
        # cannot authorize current bytes. R17's own independent test pins current files.
        evidence = ROOT/'test-evidence/air-three-blade/r16/rpm'
        review = json.loads((evidence/'pipeline-review.json').read_text())
        config = json.loads((evidence/'air3b-r16-rpm-20261004a-config.json').read_text())
        self.assertEqual(review['pipeline_sha256'], hashlib.sha256((ROOT/'cases/air-three-blade/r16/pipeline-rpm.json').read_bytes()).hexdigest())
        self.assertEqual(review['macro_hashes'], pipeline['macro_hashes'])
        self.assertEqual(config['macro_hashes'], review['macro_hashes'])
        self.assertEqual(config['input_hashes'], review['input_hashes'])
        original = json.loads((ROOT/'test-evidence/air-three-blade/r16/initial-slot-and-original.json').read_text())['original'][0]
        self.assertEqual(original['sha256'].lower(), '517cfd27aa4cf69519470d301d9088d060ec1cbb85aff7e8b848207d8bd065d8')
        self.assertNotIn(original['sha256'].lower(), pipeline['input_hashes'].values())
        import_code = (ROOT/'macros/ImportAir3bBlockGeometry.java').read_text()
        self.assertIn('Expected exactly two fluid blocks', import_code)
        old = json.loads((ROOT/'workflows/external-domain-2502/manifest.json').read_text())
        self.assertNotIn('solve', old['stages'])
        self.assertNotIn('validation', old['stages'])
if __name__ == '__main__': unittest.main()
