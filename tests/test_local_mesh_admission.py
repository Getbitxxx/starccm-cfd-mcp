import copy
import unittest
from run_registered_workflow_v2 import validate_local_mesh_controls
from run_sensitivity_study import level_case_spec

class LocalMeshAdmission(unittest.TestCase):
    def setUp(self):
        self.spec={'domain':{'outer_bounds_m':[-1,-1,-1,1,1,1]},'mesh_base_size_m':.012,'mesh_prism_layers':6,
          'mesh_nearfield_bounds_m':[-.06,-.06,-.08,.12,.06,.08],'mesh_nearfield_cell_size_m':.003,
          'mesh_wake_bounds_m':[.06,-.09,-.08,.78,.09,.08],'mesh_wake_cell_size_m':.006,
          'mesh_prism_first_layer_height_m':.000192461963883,'mesh_prism_total_thickness_m':.0039996}
    def test_required_bounds_sizes_and_prism_fail_closed(self):
        self.assertTrue(validate_local_mesh_controls(self.spec))
        mutations=[lambda s:s.pop('mesh_wake_cell_size_m'),lambda s:s.update(mesh_nearfield_cell_size_m=float('nan')),
          lambda s:s.update(mesh_base_size_m=0),lambda s:s.update(mesh_wake_cell_size_m=.02),
          lambda s:s['mesh_wake_bounds_m'].__setitem__(3,2),lambda s:s['mesh_nearfield_bounds_m'].__setitem__(0,.5),
          lambda s:s.pop('mesh_prism_first_layer_height_m'),lambda s:s.update(mesh_prism_total_thickness_m=.0001),
          lambda s:s.update(mesh_prism_layers=True)]
        for i,fn in enumerate(mutations):
            s=copy.deepcopy(self.spec);fn(s)
            with self.subTest(i=i),self.assertRaises(ValueError):validate_local_mesh_controls(s)
    def test_domain_preserves_controls_and_mesh_explicit_mapping(self):
        out=level_case_spec(self.spec,'domain',{'name':'d','bounds_m':[-2,-2,-2,2,2,2]})
        for k,v in self.spec.items():
            if k!='domain':self.assertEqual(out[k],v)
        level={'name':'fine','base_size_m':.0096,'body_target_size_m':.00192,'body_minimum_size_m':.000384,
          'nearfield_cell_size_m':.0024,'wake_cell_size_m':.0048,'prism_first_layer_height_m':.00015,'prism_total_thickness_m':.0032}
        out=level_case_spec(self.spec,'mesh',level)
        for key in ['nearfield_cell_size_m','wake_cell_size_m','prism_first_layer_height_m','prism_total_thickness_m']:
            self.assertEqual(out['mesh_'+key],level[key])
