"""Real approved catalog versions must retain live runtime and isolated-trial evidence."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import production_catalog as pc
import air3b_pipeline_trial as trial
from run_record_gate import assert_citable
BASE=ROOT/'test-evidence/product-acceptance/completion-20261018'
class ProductionAcceptance(unittest.TestCase):
    def entries(self):
        return [e for e in pc.load_catalog()['entries'] if e['id'] in ['static-sphere-chain-v1','air3b-mrf-chain-v1']]
    def test_approved_entries_verify_but_the_failed_MRF_version_is_mechanically_rejected(self):
        entries={e['id']:e for e in self.entries()}
        pc.verify_entry(entries['static-sphere-chain-v1'])
        current=entries['air3b-mrf-chain-v1']
        if current['approved_for_autonomous_run']:
            pc.verify_entry(current)
            self.assertEqual(current['version'],'2026-10-19b')
            self.assertEqual(current['pipeline'],'cases/air-three-blade/pipeline-production-v1-20261018c.json')
        else:
            with self.assertRaises(pc.CatalogError):pc.verify_entry(current)
        review=json.loads((BASE/'review/catalog-mrf-v1.json').read_text())
        e=dict(review['catalog_entry_identity'],approved_hashes=review['approved_hashes'],
               review_record=str((BASE/'review/catalog-mrf-v1.json').relative_to(ROOT)),
               approved_for_autonomous_run=False)
        with self.assertRaises(pc.CatalogError):pc.verify_entry(e)
        # A flag flip alone cannot turn the negative independent review into approval.
        e=dict(e,approved_for_autonomous_run=True)
        with self.assertRaises(pc.CatalogError):pc.verify_entry(e)
    def test_materialized_review_passes_unchanged_controller(self):
        for e in self.entries():
            if not e['approved_for_autonomous_run']:continue
            case='static-sphere-external-flow' if e['id'].startswith('static') else 'air-three-blade'
            source=json.loads((ROOT/'cases'/case/'e2e-inputs.json').read_text())
            inputs=[ROOT/name for name in source['inputs']]
            with tempfile.TemporaryDirectory() as folder:
                path=pc.materialize(e,inputs,Path(folder),BASE/(e['id']+'-preflight.json'))
                pipeline=json.loads((ROOT/e['pipeline']).read_text())
                trial.check_review(path.parent,pipeline['macro_hashes'],pipeline['input_hashes'],
                    trial.hash_file(ROOT/'scripts/security/trial_air3b_pipeline.ps1'),
                    trial.hash_file(ROOT/'scripts/air3b_pipeline_trial.py'),trial.hash_file(ROOT/e['pipeline']))
    def test_true_trial_results_are_required_even_when_E_is_negative(self):
        for branch,tid in [('static','prod-static-v1-20261018a'),('mrf-c','prod-mrf-v1-20261018c')]:
            p=BASE/'trials'/branch;x=json.loads((p/(tid+'-result.json')).read_text(encoding='utf-8-sig'))
            self.assertEqual('AIR3B_PIPELINE_COMPLETE',x['state']);self.assertIs(x['gate_passed'],True)
            invoice=json.loads((p/'INVOICE.json').read_text());self.assertGreater(invoice['native_sessions'],0)
            for cert in invoice['certificates']:
                self.assertEqual(cert['sha256'],pc.sha256(ROOT/cert['record']))
                assert_citable(ROOT/cert['record'])
    def test_real_MRF_physics_failure_cannot_certify_full_chain(self):
        p=BASE/'trials/mrf-v2';tid='prod-mrf-v1-20261018b'
        result=json.loads((p/(tid+'-result.json')).read_text(encoding='utf-8-sig'))
        self.assertEqual(result['state'],'GATE_FAILED')
        self.assertIs(result['gate_passed'],False)
        stage=json.loads((p/tid/'stage-physics.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(stage['state'],'failed')
        self.assertEqual(stage['macros'][0]['exit_code'],1)
        self.assertIn('Found 47 propeller wall boundaries, expected 44',(p/tid/'air3b_physics_failure.tsv').read_text())
        for name in ['stage-solve.json','stage-validation.json','mrf_physics.sim']:
            self.assertFalse((p/tid/name).exists())
        review=json.loads((BASE/'review/catalog-mrf-v1.json').read_text())
        self.assertIs(review['approved'],False)
        self.assertEqual(review['failed_stage'],'physics')
    def test_corrected_MRF_version_is_distinct_and_keeps_the_physical_wall_threshold(self):
        old=ROOT/'cases/air-three-blade/pipeline-production-v1-20261018b.json'
        new=ROOT/'cases/air-three-blade/pipeline-production-v1-20261018c.json'
        candidate=json.loads(new.read_text())
        self.assertNotEqual(pc.sha256(old),pc.sha256(new))
        self.assertIs(candidate['production_executable'],False)
        self.assertEqual(candidate['env']['AIR3B_EXPECTED_WALL_BOUNDARIES'],'44')
        self.assertIn('ConfigureAir3bMrfPhysicsV2.java',candidate['macro_hashes'])
        self.assertNotIn('ConfigureAir3bMrfPhysics.java',candidate['macro_hashes'])
    def test_new_invoices_increment_O4_once_and_preserve_prior_last_round(self):
        current=json.loads((ROOT/'progress.json').read_text())['budget']['per_case']
        prior=json.loads((BASE/'prior-state/progress.json').read_text())['budget']['per_case']
        for case in ['air-three-blade','static-sphere-external-flow']:
            now,old=current[case],prior[case]
            invoices=now['product_operation_invoices']
            formal=now.get('formal_worker_acceptance_invoices',[])
            self.assertEqual(len(invoices),len({i['invoice_id'] for i in invoices}))
            self.assertEqual(len(formal),len({i['invoice_id'] for i in formal}))
            self.assertGreaterEqual(len(invoices),1)
            self.assertEqual(now['rounds_since_stage_advance'],old['rounds_since_stage_advance']+len(invoices)+len(formal))
            self.assertEqual(now['last_round']['previous_rounds_since_stage_advance'],old['rounds_since_stage_advance']+len(invoices)+len(formal)-1)
            self.assertIs(now['last_round']['advanced_stage'],False)
            self.assertEqual(now['product_prior_last_rounds'][0]['last_round'],old['last_round'])
            self.assertAlmostEqual(now['star_hours_logged']-old['star_hours_logged'],(sum(i['native_process_seconds'] for i in invoices)+sum(i['managed_stage_seconds'] for i in formal))/3600,places=7)
    def test_formal_worker_invoice_uses_actual_stage_intervals_and_preserves_prior_round(self):
        current=json.loads((ROOT/'progress.json').read_text())['budget']['per_case']['static-sphere-external-flow']
        invoice=json.loads((ROOT/'test-evidence/product-acceptance/deployed-20261018/INVOICE.json').read_text())
        self.assertEqual(invoice['native_sessions'],4)
        self.assertEqual(len({j['job_id'] for j in invoice['jobs']}),4)
        measured=0
        for job in invoice['jobs']:
            stage=json.loads((ROOT/job['stage_record']).read_text(encoding='utf-8-sig'))
            self.assertEqual(stage['seconds'],job['managed_stage_seconds'])
            measured+=stage['seconds']
        self.assertAlmostEqual(measured,invoice['managed_stage_seconds'])
        self.assertIn('不是精确STAR PID秒',invoice['accounting_basis'])
        self.assertEqual(invoice['checkpoint_iteration_increment_verified'],1)
        self.assertEqual(invoice['cancelled_job_completed_iterations_observed'],[601])
        self.assertFalse(invoice['automatic_failed_request_retry'])
        self.assertEqual(current['formal_worker_prior_last_rounds'][0]['last_round']['invoice_id'],'prod-static-v1-20261018a')
    def test_MRF_scope_remains_sample_specific_and_beta_unapproved(self):
        entries={e['id']:e for e in pc.load_catalog()['entries']}
        for key in ['air3b-mrf-chain-v1','air3b-transient-chain-beta']:
            self.assertIs(entries[key]['sample_specific'],True)
            self.assertEqual(['air-three-blade'],entries[key]['allowed_case_ids'])
        self.assertIs(entries['air3b-transient-chain-beta']['approved_for_autonomous_run'],False)
if __name__=='__main__':unittest.main()
