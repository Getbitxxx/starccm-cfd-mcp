"""Phase A: executable Q0 closure, diagnosis, consistent presentation and cache guards."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import quality_contract as q
import mcp_runtime_release as release
import result_storage_policy as storage


class QualityProductization(unittest.TestCase):
    def block(self):return q.render_quality_block({'execution':{'state':'FAILED','stages':[{'state':'failed'}]}})

    def test_q0_has_computed_reasons(self):
        b=self.block();self.assertIn('STAGE_FAILED',b['Q0_REASON']);self.assertIn('NO_DELIVERY_RECORD',b['Q0_REASON'])
        self.assertTrue(q.validate_report_claims(q.render_report(b),b['quality_state'],b))

    def test_q0_without_reason_fails_closed(self):
        b=self.block();b['Q0_REASON']=[]
        with self.assertRaisesRegex(q.QualityContractError,'Q0'):q.validate_report_claims('',b['quality_state'],b)

    def test_q0_cannot_replace_computed_reason(self):
        b=self.block();b['Q0_REASON']=['UNBOUND_HISTORICAL_EVIDENCE']
        with self.assertRaisesRegex(q.QualityContractError,'Q0_REASON'):q.validate_report_claims('',b['quality_state'],b)

    def test_missing_stage_does_not_invent_failed_stage(self):
        b=q.render_quality_block({});self.assertNotIn('STAGE_FAILED',b['Q0_REASON'])

    def test_real_q0_audit_preserves_historical_bytes(self):
        for scenario in ('2_steady_mrf_propeller','3_transient_rotation'):
            audit=q.audit_historical_scenario(scenario)
            self.assertEqual(q.STATES[0],audit['quality_state']);self.assertFalse(audit['upgrade_allowed'])
            self.assertIn('UNBOUND_HISTORICAL_EVIDENCE',audit['Q0_REASON'])
            self.assertTrue(audit['records']);self.assertTrue(audit['missing_bindings'])
            for item in audit['sources']:
                self.assertEqual(item['sha256'],q.sha256(item['path']))
            for gaps in audit['missing_bindings'].values():self.assertIn('ordered_execution_graph',gaps)
            self.assertEqual('CURRENT_OBSERVER_IS_NOT_HISTORICAL_EXECUTOR',audit['binding_decision'])

    def test_legacy_delivery_and_hashes_are_not_misreported_missing(self):
        for scenario in ('2_steady_mrf_propeller','3_transient_rotation'):
            audit=q.audit_historical_scenario(scenario)
            self.assertNotIn('NO_DELIVERY_RECORD',audit['Q0_REASON'])
            self.assertNotIn('STAGE_FAILED',audit['Q0_REASON'])
            for inv in audit['legacy_inventories'].values():
                self.assertTrue(inv['delivery_record_present'])
                self.assertTrue(inv['legacy_macro_hashes']);self.assertTrue(inv['legacy_input_hashes'])
                self.assertFalse(inv['historical_runtime_manifest_bound'])

    def test_q1_cannot_claim_engineering_pass(self):
        p=q.read(ROOT/'progress.json')['quality_contract']['records'][0]['directory']
        b=q.render_quality_block(q.records_from_directory(ROOT/p));self.assertEqual(q.STATES[1],b['quality_state'])
        with self.assertRaises(q.QualityContractError):q.validate_report_claims(q.render_report(b)+'\n工程验证通过',b['quality_state'],b)

    def test_required_top_level_fields_are_enforced(self):
        b=self.block()
        for key in ('scope_statement','forbidden_uses'):
            bad=copy.deepcopy(b);bad.pop(key)
            with self.assertRaises(q.QualityContractError):q.validate_report_claims('',bad['quality_state'],bad)
        text=q.render_report(b)
        for key in ('quality_state','scope_statement','forbidden_uses','Q0_REASON'):self.assertIn(key,text.split('## 结构化')[0])

    def test_matrix_cannot_differ_from_report(self):
        rows=q.support_matrix();rows[1]['quality_state']=q.STATES[1]
        with self.assertRaisesRegex(q.QualityContractError,'matrix'):q.render_report(self.block(),matrix=rows)

    def test_readme_matrix_matches_generated_report(self):
        matrix=q.render_matrix(q.support_matrix())
        self.assertIn(matrix,(ROOT/'README.md').read_text());self.assertIn(matrix,q.render_report(self.block()))
        for row in q.support_matrix():
            if row['quality_state']==q.STATES[0]:self.assertTrue(row['Q0_REASON'])

    def test_all_error_codes_have_user_action_and_guide_matches(self):
        guide=(ROOT/'docs/mcp-user-guide.md').read_text()
        self.assertIn(release.render_error_actions(),guide)
        required={'REJECTED_BUSY','GATEWAY_RECONNECT_REQUIRED','TOOL_TIMEOUT','RELEASE_REVIEW_REQUIRED','UNPUBLISHED_CODE','TOOL_CONTRACT_CHANGED'}
        self.assertTrue(required.issubset(release.ERROR_ACTIONS))
        for code,action in release.ERROR_ACTIONS.items():
            self.assertTrue(action);d=release.error_diagnostic(code+': test')
            self.assertEqual(code,d['error_code']);self.assertEqual(action,d['user_action']);self.assertFalse(d['automatic_retry'])
            self.assertIn(action,release.diagnostic_text(code))
        self.assertEqual('DEPLOYMENT_REVIEW_MISMATCH',release.error_diagnostic('Deployment approval differs: runtime_digest')['error_code'])
        self.assertEqual('TOOL_TIMEOUT',release.error_diagnostic(TimeoutError())['error_code'])

    def test_error_code_without_action_fails_closed(self):
        bad=dict(release.ERROR_ACTIONS);bad.pop('TOOL_TIMEOUT')
        with self.assertRaisesRegex(ValueError,'action'):release.validate_error_actions(bad)
        bad=dict(release.ERROR_ACTIONS);bad['TOOL_TIMEOUT']=''
        with self.assertRaisesRegex(ValueError,'action'):release.validate_error_actions(bad)

    def test_unknown_error_is_actionable(self):
        self.assertEqual('UNCLASSIFIED_ERROR',release.error_diagnostic('new failure')['error_code'])
        self.assertTrue(release.error_diagnostic('new failure')['user_action'])

    def test_result_storage_no_deletion_and_quota(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'local_results';root.mkdir();p=root/'old.sim';p.write_bytes(b'12345');os.utime(p,(1,1))
            with patch.object(storage.shutil,'disk_usage',return_value=type('Usage',(),{'free':1000})()):
                policy=storage.inspect_results(root,quota_bytes=5,min_free_bytes=16,now=40*86400)
            self.assertEqual('REJECT_NEW_FETCH',policy['admission']);self.assertFalse(policy['automatic_delete'])
            self.assertIn(str(p.resolve()),policy['cleanup_review_candidates']);self.assertEqual(b'12345',p.read_bytes())

    def test_result_storage_disk_floor_and_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'root';root.mkdir();other=Path(temp)/'outside.sim';other.write_bytes(b'abc')
            (root/'link.sim').symlink_to(other)
            with patch.object(storage.shutil,'disk_usage',return_value=type('Usage',(),{'free':15})()):
                policy=storage.inspect_results(root,quota_bytes=100,min_free_bytes=16)
            self.assertEqual(0,policy['used_bytes']);self.assertEqual([],policy['cleanup_review_candidates']);self.assertEqual('REJECT_NEW_FETCH',policy['admission'])

    def test_snapshot_occupancy_is_included(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'downloads';root.mkdir();snap=Path(temp)/'snapshots';snap.mkdir();(snap/'large.sim').write_bytes(b'12345')
            policy=storage.inspect_results(root,quota_bytes=5,min_free_bytes=0,additional_roots=[snap])
            self.assertEqual('REJECT_NEW_FETCH',policy['admission']);self.assertEqual(5,policy['used_bytes'])
            # Deduplicate nested/identical roots.
            self.assertEqual(5,storage.inspect_results(snap,quota_bytes=6,min_free_bytes=0,additional_roots=[snap])['used_bytes'])

    def test_snapshot_readback_cannot_bypass_quota(self):
        from unittest.mock import Mock
        backend=Mock();backend.config={'local_results':'/temporary/fixture'}
        with patch.object(storage,'require_results_capacity',side_effect=ValueError('RESULTS_QUOTA_EXCEEDED')):
            with self.assertRaisesRegex(ValueError,'RESULTS_QUOTA'):q.remote_snapshot(backend,'fixture')
        backend.ps.assert_not_called()

    def test_missing_macro_does_not_become_actual_stage_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'stage-solve.json').write_text(json.dumps({'stage':'solve','state':'ok'}))
            packet=q.records_from_directory(root)
            self.assertEqual('unknown',packet['execution']['stages'][0]['state'])
            self.assertNotIn('STAGE_FAILED',q.render_quality_block(packet)['Q0_REASON'])

    def test_results_quota_rejects_before_fetch(self):
        from unittest.mock import Mock
        import server_v2 as server
        backend=Mock();backend.config={'local_results':'/temporary/fixture'}
        with patch.object(server,'Backend',return_value=backend),patch.object(storage,'require_results_capacity',side_effect=ValueError('RESULTS_QUOTA_EXCEEDED')):
            with self.assertRaisesRegex(ValueError,'RESULTS_QUOTA'):server.fetch_run_results('fixture')
            with self.assertRaisesRegex(ValueError,'RESULTS_QUOTA'):server.report_run('fixture')
        backend.fetch_run_results.assert_not_called();backend.ps.assert_not_called()

if __name__=='__main__':unittest.main()
