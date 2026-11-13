import unittest,sys,math
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from air3b_contract import make_specs,execution_blockers
from casespec import validate_case_spec
class AirContractTests(unittest.TestCase):
 def fixture(self):return dict(valid=True,solid_count=1,parameters={'te_mm':0.8},bounds_mm=[-205,-205,-12.5,205,205,12.5],step_sha256='a'*64,stl_sha256='b'*64)
 def test_wrong_geometry_cannot_enter(self):
  q=self.fixture();q['parameters']['te_mm']=0.3
  with self.assertRaises(ValueError):make_specs(q)
 def test_unregistered_draft_never_executable(self):
  for spec in make_specs(self.fixture()).values():
   self.assertEqual(len(execution_blockers(spec)),5)
   v=validate_case_spec(spec);self.assertFalse(v.executable);self.assertIn('case_ref',v.missing)
 def test_correct_domain_direction_and_units(self):
  s=make_specs(self.fixture())['mrf'];self.assertLess(s['rotation_zone']['z_min_m'],-0.0125);self.assertGreater(s['rotation_zone']['z_max_m'],0.0125)
  self.assertEqual(s['flow']['velocity_m_s'],[0,0,-30]);self.assertEqual(s['model_length_unit'],'mm')
 def test_transient_rotation_time_consistent(self):
  s=make_specs(self.fixture())['transient'];self.assertAlmostEqual(s['transient']['time_step_s']*360*10000/60,1)
  self.assertEqual(s['transient']['minimum_revolutions'],10)
if __name__=='__main__':unittest.main()
