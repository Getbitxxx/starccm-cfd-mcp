import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from local_mesh_plan import prepare_local_mesh_plan, properties_for_plan
from backend import Backend

class LocalMeshPlanTests(unittest.TestCase):
    def setUp(self):
        self.audit={'status':'ok','geometryParts':[]}
        self.controls={'schema_version':1,'fluid_parts':['rotor odd name','stationary'],
                       'base_size_m':.08,'prism_layers':5,'prism_total_m':.00025,'prism_stretching':1.2,
                       'near_field':{'start_m':[0,0,-.2],'end_m':[0,0,.4],'radius_m':.3,'target_size_m':.004},'surfaces':[]}
        for part, roles in [('rotor odd name',['blade_wall','hub_wall','interface']),
                            ('stationary',['inlet','outlet','farfield','interface'])]:
            p={'qualifiedName':part,'isClosed':True,'isManifold':True,'partSurfaces':[]}
            for i,role in enumerate(roles):
                selector=part+'.arbitrary surface '+str(i)
                p['partSurfaces'].append({'qualifiedName':selector})
                self.controls['surfaces'].append({'selector':selector,'role':role,'prism_enabled':role.endswith('_wall'),
                                                 'min_size_m':.0001,'target_size_m':.16 if role=='farfield' else .0004,
                                                 **({'pair':'interface-pair'} if role=='interface' else {})})
            p['partSurfaceCount']=len(p['partSurfaces']);self.audit['geometryParts'].append(p)

    def plan(self):return prepare_local_mesh_plan(self.audit,self.controls)
    def test_arbitrary_names_and_coarse_farfield_are_supported(self):
        r=self.plan();self.assertTrue(r['configuration_valid']);self.assertFalse(r['execution_allowed']);self.assertEqual(r['surface_count'],7)
    def test_complete_coverage_is_required(self):
        self.controls['surfaces'].pop(0)
        with self.assertRaisesRegex(ValueError,'Unassigned'):self.plan()
    def test_duplicate_selection_fails(self):
        self.controls['surfaces'].append(copy.deepcopy(self.controls['surfaces'][0]))
        with self.assertRaisesRegex(ValueError,'duplicate'):self.plan()
    def test_merged_surface_cannot_masquerade_as_named_surfaces(self):
        p=self.audit['geometryParts'][0];p['partSurfaces']=[{'qualifiedName':p['qualifiedName']+'.merged'}];p['partSurfaceCount']=1
        with self.assertRaisesRegex(ValueError,'Unknown'):self.plan()
    def test_interface_pair_needs_two_distinct_parts(self):
        self.controls['surfaces'][-1]['pair']='different'
        with self.assertRaisesRegex(ValueError,'interface pair'):self.plan()
    def test_no_prisms_on_open_boundaries(self):
        self.controls['surfaces'][3]['prism_enabled']=True
        with self.assertRaisesRegex(ValueError,'Prisms'):self.plan()
    def test_wall_prism_intent_cannot_be_omitted(self):
        self.controls['surfaces'][0].pop('prism_enabled')
        with self.assertRaisesRegex(ValueError,'Prisms'):self.plan()
    def test_nan_and_boolean_sizes_fail(self):
        for val in (float('nan'),True,0):
            self.controls['base_size_m']=val
            with self.assertRaises(ValueError):self.plan()
    def test_bad_topology_and_partial_audit_fail(self):
        self.audit['geometryParts'][0]['isManifold']=False
        with self.assertRaises(ValueError):self.plan()
        self.audit['status']='partial'
        with self.assertRaises(ValueError):self.plan()
    def test_zero_length_near_field_fails(self):
        self.controls['near_field']['end_m']=self.controls['near_field']['start_m'][:]
        with self.assertRaisesRegex(ValueError,'zero length'):self.plan()
    def test_interface_prisms_require_separate_reviewed_policy(self):
        self.controls['surfaces'][-1]['prism_enabled']=True
        with self.assertRaisesRegex(ValueError,'Prisms'):self.plan()
    def test_properties_escape_names_and_record_provenance(self):
        result=properties_for_plan(self.plan());self.assertIn('rotor\\ odd\\ name',result);self.assertIn('audit_sha256=',result)
    def test_input_hash_gate_is_reused_by_backend(self):
        b=Backend.__new__(Backend);b._trusted_preflight=Mock(side_effect=ValueError('hash mismatch'))
        with self.assertRaisesRegex(ValueError,'hash mismatch'):b.prepare_local_mesh_plan('case','inspect',self.controls)
        b._trusted_preflight.assert_called_once_with('case','inspect')
    def test_control_changes_change_digest(self):
        a=self.plan()['controls_sha256'];self.controls['prism_total_m']/=2
        self.assertNotEqual(a,self.plan()['controls_sha256'])

    def test_optional_prism_parameters_round_trip_and_bind_hash(self):
        old=self.plan()['controls_sha256']
        self.controls['prism_mesher']={'minimum_thickness_pct':1.,'layer_chopping_pct':5.,'boundary_march_angle_deg':25.}
        plan=self.plan();self.assertNotEqual(old,plan['controls_sha256'])
        props=properties_for_plan(plan)
        for k,v in self.controls['prism_mesher'].items():self.assertIn('prism_mesher.'+k+'='+str(v),props)
    def test_partial_unknown_and_nonobject_prism_parameters_fail(self):
        for val in (None,{}, {'minimum_thickness_pct':1}, {'minimum_thickness_pct':1,'layer_chopping_pct':5,'boundary_march_angle_deg':25,'unknown':2}):
            self.controls['prism_mesher']=val
            with self.assertRaises(ValueError):self.plan()
    def test_nonfinite_and_boolean_prism_parameters_fail(self):
        for bad in (True,float('nan'),float('inf'),'1'):
            self.controls['prism_mesher']={'minimum_thickness_pct':bad,'layer_chopping_pct':5,'boundary_march_angle_deg':25}
            with self.assertRaises(ValueError):self.plan()
    def test_out_of_range_prism_parameters_fail(self):
        for key,bad in [('minimum_thickness_pct',0),('minimum_thickness_pct',101),('layer_chopping_pct',-1),('layer_chopping_pct',100),('boundary_march_angle_deg',0),('boundary_march_angle_deg',91)]:
            self.controls['prism_mesher']={'minimum_thickness_pct':1,'layer_chopping_pct':5,'boundary_march_angle_deg':25}
            self.controls['prism_mesher'][key]=bad
            with self.assertRaises(ValueError):self.plan()

if __name__=='__main__':unittest.main()
