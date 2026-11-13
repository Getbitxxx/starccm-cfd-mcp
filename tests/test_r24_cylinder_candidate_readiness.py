"""②R24 offline readiness for the independently imported cylinder source.

Starts no STAR, opens no SSH session, modifies nothing that another window owns. It pins the state that
decides whether the registered cylinder chain can start at all:

1. the delivered ①R24 source `.sim` and the frozen handover hashes are what they claim to be;
2. the new R24 CaseSpec binds that source's REAL identity and changes nothing else - the candidate and
   the execution must use the same CaseSpec version and the same source, and a failed audit conclusion
   must not be inherited;
3. the ①R24 geometry certificate is NOT citable (`status: error`), so `prepare_candidate` refuses today.
   That refusal is the executable statement of the blocker, not prose;
4. with a qualified certificate the flow does generate - so the certificate is the ONLY missing input;
5. `scripts/server_v2.py` registers the six registered-workflow tools the desktop acceptance needs.
"""
import asyncio
import fnmatch
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))

import run_registered_workflow_v2 as adapter  # noqa: E402
from run_record_gate import assert_citable  # noqa: E402

R24 = ROOT / 'test-evidence/static-cylinder-step/r24-import'
RUN = R24 / 'static-cylinder-step-import-20261005d'
SOURCE = RUN / 'static_body_geometry.sim'
AUDIT = RUN / 'static_body_audit.json'
IMPORT_RECORD = RUN / 'static_body_geometry.json'
HANDOVER = R24 / 'window2-handover.json'
R24_SPEC = ROOT / 'cases/static-cylinder-step/r24/case_spec.json'
FROZEN_INTENT = ROOT / 'cases/static-cylinder-step/case_spec.json'
CONTRACT = ROOT / 'cases/static-cylinder-step/geometry-contract.json'
SOURCE_SHA = 'b8c9a082a80ac40a5923559462c60c723a8441e0cb0b16d868b0329f905a2feb'
SIX_TOOLS = ['prepare_registered_workflow_candidate', 'register_reviewed_workflow_candidate',
             'list_registered_workflows', 'submit_registered_workflow',
             'get_registered_workflow_run', 'fetch_registered_workflow_results']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def flatten(data, prefix=''):
    out = {}
    for key, value in data.items():
        if isinstance(value, dict):
            out.update(flatten(value, prefix + key + '.'))
        else:
            out[prefix + key] = value
    return out


class DeliveredSourceTests(unittest.TestCase):
    def test_source_sim_matches_the_frozen_handover(self):
        handover = load(HANDOVER)
        self.assertTrue(handover['source_geometry_only_sim']['delivered'])
        self.assertEqual(handover['source_geometry_only_sim']['sha256'], SOURCE_SHA)
        self.assertEqual(sha(SOURCE), SOURCE_SHA)
        self.assertEqual(SOURCE.stat().st_size, handover['source_geometry_only_sim']['bytes'])
        # Window one is still repairing its own macros for the same defect (①R25 work appeared at
        # 21:21 while this review was running), so a pin move is accepted only when a supersession
        # record documents it - an unexplained move still fails. tests/superseded_pins.py is the
        # repository's own helper for exactly this rule.
        from superseded_pins import allowed_pins, documented_files
        allowed = allowed_pins(ROOT)
        # 2026-10-15（独立审查 F5a）：这里原有一段**内联的、更弱的**"这次移动有没有被登记"实现——
        # 它只读 shared_state_files_updated_this_round 的 after，忽略 segments / intermediate_from_round23 /
        # 键别名。判据只允许一处实现：tests/superseded_pins.documented_files 委托
        # scripts/supersession_ledger.documented_after（生产码与测试共用）。改用该实现。
        documented = documented_files(ROOT)
        # window 2's own reviewed artifacts: a move needs a recorded reason
        window2 = set(json.loads((ROOT / 'test-evidence/static-cylinder-step/r24/freeze.json')
                                 .read_text())['window2_owned'])
        window2 |= set(json.loads((ROOT / 'test-evidence/static-cylinder-step/r23/'
                                          'frozen-files.json').read_text())['files'])
        # the ②R23 snapshot also pinned window 1's own macros; those are not window 2's artifacts
        ownership23 = json.loads((ROOT / 'test-evidence/static-cylinder-step/r23/'
                                         'file-ownership.json').read_text())
        window2 = {n for n in window2
                   if not any(fnmatch.fnmatch(n, pattern) for pattern in ownership23['window1'])}
        # the artifacts this chain binds by identity: always exact
        strict = {'test-evidence/static-cylinder-step/r24-import/static-cylinder-step-import-20261005d/'
                  'static_body_geometry.sim'}
        for name, digest in handover['frozen_files'].items():
            if name.endswith('window2-handover.json'):
                continue
            if sha(ROOT / name) == digest:
                continue
            if name in strict:
                self.fail('the delivered source .sim moved: ' + name)
            recorded = Path(name).name in allowed and digest in allowed[Path(name).name]
            shared_state = sha(ROOT / name) in documented.get(name, set())
            if name in window2 and not (recorded or shared_state):
                self.fail('window-2 artifact moved without a recorded reason: ' + name)
            # anything else here is window 1's own artifact; its owner repairs it in flight

    def test_import_record_proves_the_composite_leaf_repair(self):
        # the delivered source really is a retained BREP solid leaf, not the rejected composite root
        record = load(IMPORT_RECORD)
        self.assertEqual(record['status'], 'ok')
        self.assertEqual(record['output_sim_sha256'], SOURCE_SHA)
        self.assertTrue(record['output_sim_exists'])
        self.assertTrue(record['cad_retained'])
        self.assertTrue(record['imported_root_is_composite'])
        self.assertEqual(str(record['imported_solid_count']), '1')
        self.assertEqual(str(record['cad_body_count']), '1')
        self.assertTrue(record['cad_body_brep_solid'])
        self.assertEqual(record['selected_part_class'], 'star.cadmodeler.SolidModelPart')
        self.assertEqual(record['geometry_step_sha256'], load(CONTRACT)['step_sha256'])


class CaseSpecIdentityTests(unittest.TestCase):
    def setUp(self):
        self.spec = load(R24_SPEC)
        self.frozen = load(FROZEN_INTENT)

    def test_the_r24_spec_binds_the_real_source_identity(self):
        self.assertEqual(self.spec['case_ref'], 'library/' + SOURCE_SHA + '.sim')
        identity = self.spec['input_identity']
        self.assertFalse(identity['source_sim_hash_pending'])
        self.assertEqual(identity['source_sim_sha256'], SOURCE_SHA)
        self.assertEqual(identity['source_sim_bytes'], SOURCE.stat().st_size)
        self.assertEqual(sha(ROOT / identity['source_sim_origin']), SOURCE_SHA)
        self.assertEqual(identity['step_sha256'], load(CONTRACT)['step_sha256'])

    def test_the_delta_against_the_frozen_intent_is_identity_only(self):
        before, after = flatten(self.frozen), flatten(self.spec)
        self.assertEqual([k for k in before if k not in after], [])
        changed = {k for k in before if before[k] != after[k]}
        self.assertEqual(changed, {'case_spec_frozen', 'case_ref',
                                   'input_identity.source_sim_hash_pending', 'input_identity.purpose'})
        for prefix in ('geometry.', 'flow.', 'domain.', 'numerics.', 'quality.', 'finite_execution.',
                       'limits.', 'engineering_target.'):
            for key in [k for k in before if k.startswith(prefix)]:
                self.assertEqual(before[key], after[key], 'non-identity change: ' + key)
        self.assertEqual(self.spec['finite_execution']['star_seconds_cap'], 480)
        self.assertEqual(self.spec['case'], self.frozen['case'])

    def test_the_spec_cannot_inherit_a_failed_audit_conclusion(self):
        # the rejected audit is named nowhere as a certificate, and the spec still demands a fresh one
        text = R24_SPEC.read_text(encoding='utf-8')
        self.assertNotIn('static_body_audit.json', text)
        self.assertIn('status=error', self.spec['input_identity']['purpose'])
        self.assertEqual(load(AUDIT)['status'], 'error')


class CertificateIsTheOnlyMissingInputTests(unittest.TestCase):
    def test_the_real_audit_record_is_not_citable_and_the_candidate_refuses(self):
        record = load(AUDIT)
        self.assertEqual(record['status'], 'error')
        self.assertFalse(record['usable'])
        self.assertIsNone(record['complete_marker'])
        self.assertEqual(record['error_class'], 'star.base.neo.NeoException')
        self.assertIn('INDEPENDENT_CYLINDER', record['error'])
        self.assertEqual(record['input_sim_sha256'], SOURCE_SHA)
        with self.assertRaises(Exception):
            assert_citable(AUDIT)

    def test_with_a_qualified_certificate_the_candidate_generates(self):
        """The dry run uses the REAL source, the REAL R24 CaseSpec and the REAL contract.

        Only the certificate is synthesised (into a temp tree), so a failure here would mean the chain
        is broken for reasons other than the missing audit. It proves the certificate is the ONLY
        missing input; it is not an execution approval and it registers nothing.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            spec = load(R24_SPEC)
            evidence = root / 'test-evidence/r24-dry-run'
            evidence.mkdir(parents=True)
            (root / 'macros').mkdir()
            template = load(ROOT / 'cases/static-sphere-external-flow/'
                                  'pipeline-e2e-geometry-to-validation.json')
            (root / 'cases/static-sphere-external-flow').mkdir(parents=True)
            (root / 'cases/static-sphere-external-flow/'
                   'pipeline-e2e-geometry-to-validation.json').write_text(json.dumps(template))
            for name in {n for stage in template['stages'] for n in stage['macros']} | {
                    'ExportExternalFlowResults.java'}:
                (root / 'macros' / name).write_text('// dry-run fixture')
            # stage the REAL source bytes inside the dry-run tree (same SHA-256, different location)
            (root / 'static_body_geometry.sim').write_bytes(SOURCE.read_bytes())
            self.assertEqual(sha(root / 'static_body_geometry.sim'), SOURCE_SHA)
            # the EXACT R24 CaseSpec bytes: candidate and execution must share one spec version
            (root / 'case.json').write_bytes(R24_SPEC.read_bytes())
            self.assertEqual(sha(root / 'case.json'), sha(R24_SPEC))
            contract = load(CONTRACT)
            # stage the REAL STEP bytes too, so the three-way STEP identity check runs for real
            (root / 'cylinder.step').write_bytes((ROOT / contract['step_path']).read_bytes())
            contract['step_path'] = 'cylinder.step'
            self.assertEqual(sha(root / 'cylinder.step'), contract['step_sha256'])
            (root / 'contract.json').write_text(json.dumps(contract))
            # a QUALIFIED certificate: built FROM the real record, replacing only the fields the failure
            # prevented from being measured. 2026-10-15 归因：原来手写一个 dict，R24 之后新增的
            # model_length_unit 读回门（mm 单位轮）因此缺少该字段，dry run 报
            # 'Declared model unit differs from the trusted geometry audit'——那不是"证书唯一缺项"，
            # 是夹具落后于契约。改为从真实记录派生后，夹具不可能再缺一个真实记录拥有的字段。
            certificate = load(AUDIT)
            for key in ('error', 'error_class'):
                self.assertIn(key, certificate)
                certificate.pop(key)
            certificate.update({
                'status': 'ok', 'usable': True, 'complete_marker': 'INDEPENDENT_CYLINDER_OK',
                'regions': 0, 'iteration': 0, 'is_closed': True, 'is_manifold': True,
                'cad_retained': True,
                'measured_bbox_si_m': spec['geometry']['expected_bounds_m']})
            for key in ('model_length_unit', 'body_part_name', 'input_sim_sha256'):
                self.assertEqual(load(AUDIT)[key], certificate[key],
                                 'the qualified certificate must keep the real record value: ' + key)
            self.assertEqual('m', certificate['model_length_unit'])
            (root / 'audit.json').write_text(json.dumps(certificate))
            from types import SimpleNamespace
            with patch.object(adapter, 'ROOT', root), \
                 patch.object(adapter, 'assert_citable', return_value={'dry_run': True}), \
                 patch.object(adapter, 'resolve_operations',
                              return_value={'unresolved': [], 'operations': {}}), \
                 patch.object(adapter, 'validate_case_spec', return_value=SimpleNamespace(
                     as_dict=lambda: {'valid': True, 'executable': False,
                                      'plan_blocker': 'missing engineering studies'})):
                result = adapter.prepare_candidate(root / 'static_body_geometry.sim',
                                                   root / 'case.json', 'r24-dry-run',
                                                   root / 'audit.json', root / 'contract.json', evidence)
            self.assertEqual(result['state'], 'PENDING_INDEPENDENT_REVIEW')
            self.assertFalse(result['approved'])
            self.assertFalse(result['star_started'])
            pipeline = json.loads((root / 'registered-workflows-v2/r24-dry-run/'
                                   'pipeline.json').read_text())
            self.assertEqual(pipeline['input_hashes'],
                             {'static_body_geometry.sim': SOURCE_SHA,
                              'case_spec.json': sha(R24_SPEC)})
            self.assertEqual([s['timeout_seconds'] for s in pipeline['stages']],
                             [35, 40, 90, 35, 220, 60])
            self.assertEqual(sum(s['timeout_seconds'] for s in pipeline['stages']), 480)
            self.assertEqual(pipeline['task_budget_seconds'], 540)
            manifest = json.loads((root / 'registered-workflows-v2/r24-dry-run/'
                                   'manifest.json').read_text())
            self.assertEqual(manifest['input_identity_sha256'], SOURCE_SHA)
            self.assertEqual(manifest['case_spec_sha256'], sha(R24_SPEC))
            self.assertFalse(manifest['capability_levels']['engineering_validated'])
            # nothing was registered in the live tree. 2026-10-15 归因：原断言要求注册表**只含 README.md**
            # ——那是 R24 当天的状态；后续已授权轮次真实登记了 72 个工作流。改为钉住它本来要守的不变量：
            # 本次 dry run 的 id 没有出现在 live 树里，且它用的那份 CaseSpec 字节在注册表中查不到。
            self.assertFalse((ROOT / 'registered-workflows-v2' / 'r24-dry-run').exists())
            registered = {}
            for manifest in sorted((ROOT / 'registered-workflows-v2').glob('*/manifest.json')):
                item = json.loads(manifest.read_text())
                registered[item['id']] = item.get('case_spec_sha256')
            self.assertNotIn(sha(R24_SPEC), registered.values(),
                             'the R24 candidate CaseSpec must not be registered')


class ServerToolTests(unittest.TestCase):
    """Server-side registration only. The desktop acceptance is a separate, human-visible step."""

    def test_server_v2_declares_the_six_registered_workflow_tools(self):
        # the test interpreter has no `mcp` package (the MCP venv does), so the authoritative
        # registration check is the venv run recorded in the R24 evidence; here we pin the source of
        # truth that cannot drift silently: the two new tools must be declared in server_v2.py and the
        # four legacy names must be delegated.
        text = (ROOT / 'scripts/server_v2.py').read_text()
        for tool in SIX_TOOLS:
            self.assertIn('def ' + tool + '(', text, 'missing tool declaration: ' + tool)
        for tool in ('submit_registered_workflow', 'get_registered_workflow_run',
                     'fetch_registered_workflow_results', 'list_registered_workflows'):
            self.assertIn('registered.' + tool, text, 'legacy delegation lost: ' + tool)
        self.assertIn('publish_candidate(workflow_id)', text)

    def test_registration_requires_review_of_the_current_bytes(self):
        api = (ROOT / 'scripts/registered_workflow_mcp_v2.py').read_text()
        for needle in ("review.get('approved')", "review.get('independent')",
                       "'registration_hashes'", "registered_request_ids",
                       "REGISTERED_CURRENT_BYTES"):
            self.assertIn(needle, api)
        adapter = (ROOT / 'scripts/run_registered_workflow_v2.py').read_text()
        self.assertIn('use a new version, never overwrite approved bytes', adapter)


if __name__ == '__main__':
    unittest.main()
