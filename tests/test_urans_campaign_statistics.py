import importlib.util
import math
from pathlib import Path
import unittest

P=Path(__file__).resolve().parents[1]/'test-evidence/e-static-sphere-urans-20261020/analyze_campaign.py'
s=importlib.util.spec_from_file_location('urans_campaign_statistics',P)
a=importlib.util.module_from_spec(s);s.loader.exec_module(a)


def history():
    return [dict(physical_time_s=i/100,time_step=i,iteration=20*i,Cd=0.4,
                 Cl_y=0.1*math.sin(2*math.pi*i/100),Cl_z=0,
                 mass_imbalance=1e-5,min_inner_decay_orders=4,eligible_statistics=True)
            for i in range(1,1501)]


def record():
    return dict(status='ok',time_step_s=.01,cell_count=58724,steps_completed=1500,physical_time_s=15)


class FrozenStatistics(unittest.TestCase):
    def test_period_is_measured_and_constant_trace_is_not_a_measurement(self):
        r=history();p=a.measured_period(r)
        self.assertEqual(p['status'],'MEASURED');self.assertAlmostEqual(p['period_s'],1)
        for x in r:x['Cl_y']=0
        self.assertEqual(a.measured_period(r)['status'],'NOT_ESTABLISHED')

    def test_consecutive_windows_pass_and_ineligible_step_never_counts(self):
        r=history();rec=record()
        self.assertEqual(a.matrix_case(r,rec,1)['status'],'PASS')
        r[600]['eligible_statistics']=False;r[600]['min_inner_decay_orders']=2.9
        v=a.matrix_case(r,rec,1)
        self.assertEqual(v['status'],'FAIL')
        self.assertEqual(v['windows'][0]['excluded_steps'],1)
        self.assertEqual(a.gci([v,v,v])['status'],'NOT_EVALUATED')

    def test_cd_and_mass_boundaries_are_strict(self):
        r=history()
        for x in r:
            if x['physical_time_s']>8:x['Cd']=.41
        self.assertFalse(a.matrix_case(r,record(),1)['checks']['mean_Cd_drift_lt_1pct'])
        for x in r:x['Cd']=.4;x['mass_imbalance']=.001
        self.assertFalse(a.matrix_case(r,record(),1)['checks']['mass_each_window_lt_1e3'])

    def test_failed_export_or_missing_step_cannot_qualify_gci(self):
        r=history();rec=record();rec['status']='error'
        v=a.matrix_case(r,rec,1)
        self.assertEqual(v['status'],'FAIL');self.assertEqual(v['first_unmet'],'execution_complete')
        self.assertEqual(a.gci([v,v,v])['status'],'NOT_EVALUATED')
        rec=record();r.pop(100)
        self.assertFalse(a.matrix_case(r,rec,1)['checks']['history_complete'])

    def test_tenth_statistical_cycle_cannot_be_silently_deleted(self):
        r=history()
        for x in r:
            if x['physical_time_s']>14:x['eligible_statistics']=False;x['min_inner_decay_orders']=2
        v=a.matrix_case(r,record(),1)
        self.assertTrue(v['checks']['eligible_three_windows'])
        self.assertFalse(v['checks']['ten_qualified_statistical_cycles'])
        self.assertEqual(v['status'],'FAIL');self.assertEqual(v['excluded_statistical_steps'],100)
        self.assertEqual(a.gci([v,v,v])['status'],'NOT_EVALUATED')

    def test_tenth_statistical_cycle_bad_mass_cannot_enter_mean_or_gci(self):
        r=history()
        for x in r:
            if x['physical_time_s']>14:x['mass_imbalance']=.001
        v=a.matrix_case(r,record(),1)
        self.assertTrue(v['checks']['mass_each_window_lt_1e3'])
        self.assertFalse(v['checks']['mass_whole_statistical_scope_lt_1e3'])
        self.assertEqual(v['status'],'FAIL');self.assertEqual(a.gci([v,v,v])['status'],'NOT_EVALUATED')

    def test_pilot_scheduling_period_is_not_own_case_period_evidence(self):
        r=history()
        for x in r:x['Cl_y']=.1*math.sin(2*math.pi*x['physical_time_s']/1.2)
        v=a.matrix_case(r,record(),1)
        self.assertAlmostEqual(v['period_measurement']['period_s'],1.2)
        self.assertFalse(v['checks']['at_least_15_measured_cycles'])
        self.assertEqual(v['status'],'FAIL')
        for x in r:x['Cl_y']=0
        self.assertFalse(a.matrix_case(r,record(),1)['checks']['own_period_identified'])

    def test_pilot_period_needs_both_crossing_directions_and_sample_resolution(self):
        r=history()[:201]
        self.assertEqual(a.measured_period(r)['status'],'MEASURED')
        self.assertEqual(a.measured_period(r[:120])['status'],'NOT_ESTABLISHED')
        self.assertEqual(a.measured_period(history()[::20])['status'],'NOT_ESTABLISHED')

    def test_gci_requires_monotonicity_and_actual_counts(self):
        c=[dict(status='PASS',cell_count=n,Cd_mean=v) for n,v in [(1000,.44),(8000,.41),(64000,.4025)]]
        v=a.gci(c);self.assertEqual(v['status'],'PASS');self.assertAlmostEqual(v['p'],2)
        self.assertAlmostEqual(v['GCI_32'],1.25*.0075/.4025/3)
        c[-1]['Cd_mean']=.42;self.assertEqual(a.gci(c)['status'],'FAIL')

    def test_unqualified_sensitivity_is_not_evaluated(self):
        self.assertEqual(a.sensitivity([dict(status='FAIL')])['status'],'NOT_EVALUATED')
        self.assertEqual(a.sensitivity([dict(status='PASS',Cd_mean=.4),dict(status='PASS',Cd_mean=.41)])['status'],'FAIL')
