import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from grid_convergence import three_grid_gci

class GridConvergenceTests(unittest.TestCase):
    def test_nasa_published_pressure_recovery_example(self):
        r=three_grid_gci(.96178,.96854,.97050,2)
        self.assertTrue(r['valid']);self.assertAlmostEqual(r['p'],1.78617,places=4)
        self.assertAlmostEqual(r['gci_fine_pct'],.103083,places=5)
        self.assertAlmostEqual(r['phi_extrapolated'],.9713003,places=7)
        self.assertFalse(r['engineering_verified'])
    def test_diverging_differences_cannot_become_positive_order(self):
        r=three_grid_gci(10,11,13,1.3)
        self.assertFalse(r['valid']);self.assertLess(r['p'],0)
        self.assertIsNone(r['gci_fine_pct']);self.assertFalse(r['converged'])
    def test_equal_or_zero_differences_never_certify_independence(self):
        for values in [(1,1,1),(1,1,2),(1,2,2),(1,2,3)]:
            r=three_grid_gci(*values,2);self.assertFalse(r['converged']);self.assertFalse(r['valid'])
    def test_signed_values_are_preserved_and_oscillations_rejected(self):
        pos=three_grid_gci(1.4,1.1,1.025,2)
        neg=three_grid_gci(-1.4,-1.1,-1.025,2)
        self.assertEqual(pos['gci_fine_pct'],neg['gci_fine_pct'])
        self.assertAlmostEqual(pos['phi_extrapolated'],-neg['phi_extrapolated'])
        self.assertFalse(three_grid_gci(-1,1,-1,2)['valid'])
    def test_zero_target_requires_explicit_physical_normalization(self):
        self.assertFalse(three_grid_gci(4,1,0,2)['valid'])
        r=three_grid_gci(4,1,0,2,normalization_scale=10)
        self.assertTrue(r['valid']);self.assertAlmostEqual(r['gci_fine_pct'],6.25)
    def test_invalid_inputs_rejected(self):
        for bad in (True,float('nan'),float('inf'),'1'):
            with self.assertRaises(ValueError):three_grid_gci(bad,2,3,2)
        for ratio in (0,1,-1):
            with self.assertRaises(ValueError):three_grid_gci(1,2,3,ratio)
        with self.assertRaises(ValueError):three_grid_gci(1,2,3,2,normalization_scale=0)

if __name__=='__main__':unittest.main()
