"""②R23 offline re-review of the v2 candidate → review → registration implementation.

This module starts no STAR, opens no SSH session and does not modify any frozen artifact. It turns the
things the re-review verified BY HAND into executable checks, so the next window cannot silently lose
them (AGENTS.md §2-O9: prose has no force, compiled rules do):

1. the shared execution files and the ②R23 frozen set are still byte-frozen;
2. the candidate generator's assumptions about the template pipeline and about what the PINNED macros
   emit are true;
3. the frozen import-intent CaseSpec can never be mistaken for a runnable candidate;
4. the window-2 budget really carries the stated 480 s cap;
5. the execution approval is item-by-item: dropping or mutating any single reviewed hash is refused;
6. nothing is registered in v2 today, so nothing can be submitted today.
"""
import fnmatch
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

R23 = ROOT / 'test-evidence/static-cylinder-step/r23'
CYL = ROOT / 'cases/static-cylinder-step'
TEMPLATE = ROOT / 'cases/static-sphere-external-flow/pipeline-e2e-geometry-to-validation.json'
CANDIDATE_DEADLINES = [35, 40, 90, 35, 220, 60]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class FrozenBytesTests(unittest.TestCase):
    """'Keep the shared execution files frozen' has to be checkable, not asserted in prose."""

    def test_shared_execution_files_are_byte_unchanged(self):
        pins = json.loads((ROOT / 'test-evidence/static-cylinder-step/shared-before.json').read_text())
        recorded = dict(pins['support'])
        recorded['scripts/air3b_pipeline_trial.py'] = pins['controller']
        recorded['scripts/security/trial_air3b_pipeline.ps1'] = pins['runner']
        from tests.superseded_pins import is_recorded_change
        self.assertEqual(len(recorded), 14)
        for name, digest in recorded.items():
            live = sha(ROOT / name)
            if live != digest:
                self.assertTrue(is_recorded_change(name, live, ROOT / 'test-evidence'),
                                'shared execution file changed without a recorded supersession: ' + name)

    def test_r23_frozen_artifacts_are_frozen_per_owner(self):
        """A single round snapshot mixes both windows' artifacts, so 'no drift' is the wrong invariant.

        Window 2 (this window) must not touch its own reviewed bytes or anything shared. Window 1 may
        legitimately repair ITS OWN macros under its own authorization - observed live at 2026-10-05
        20:27:40, when it edited BuildStaticBodyGeometry.java and AuditStaticBodyGeometry.java while
        fixing the SolidModelCompositePart -> solid leaf selection. The executable invariant is
        therefore per-owner: every drifted pinned file must be window-1-owned.
        """
        frozen = json.loads((R23 / 'frozen-files.json').read_text())
        self.assertEqual(frozen['state'], 'FROZEN_PARTIAL_STOPPED_NO_EXECUTION_APPROVAL')
        self.assertIsNone(frozen['source_sim'])
        self.assertFalse(frozen['approved_execution'])
        self.assertEqual(len(frozen['files']), 39)
        ownership = json.loads((R23 / 'file-ownership.json').read_text())
        rev2 = json.loads((R23 / 'frozen-files-rev2.json').read_text())
        superseded = rev2['superseded_from_rev1']
        # A LATER authorized round may legitimately update a shared/state file (progress.json, the account
        # ledger): ①R24 did exactly that under §2-O1 while repairing the CAD solid-leaf selection. Such a drift
        # is allowed only when that round RECORDED it, exactly as this round recorded its own rev2 supersessions.
        # `documented_files` reads both record conventions (a repaired macro's `after`, and a later round's
        # `shared_state_files_updated_this_round[...].after`); an undocumented drift still fails below.
        sys.path.insert(0, str(ROOT / 'tests'))
        from superseded_pins import documented_files
        later = documented_files(ROOT)
        for name, digest in frozen['files'].items():
            if sha(ROOT / name) == digest:
                continue
            if any(fnmatch.fnmatch(name, pattern) for pattern in ownership['window1']):
                continue  # the other window's own artifact, changed under its own authorization
            if sha(ROOT / name) in later.get(name, set()):
                continue  # a later round documented this shared/state change
            # a window-2/shared pinned file may only drift if THIS window recorded the supersession
            self.assertIn(name, superseded, 'unrecorded drift of a window-2/shared pin: ' + name)
            self.assertEqual(superseded[name]['rev2'], sha(ROOT / name), name)
        for name, digest in rev2['window2_owned'].items():
            if sha(ROOT / name) == digest:
                continue
            if sha(ROOT / name) in later.get(name, set()):
                continue  # a later authorized round documented this change (see the note above)
            self.assertEqual(sha(ROOT / name), digest, 'rev2 pin drifted: ' + name)
        for name, digest in rev2['shared_execution_files'].items():
            live = sha(ROOT / name)
            if live == digest:
                continue
            # 2026-10-15（全量回归逐条归因）：这 7 个共享执行文件被**后续已授权轮次**改过
            # （2026-10-14 时间预算撤销改 stage_plan/backend/run_static_external_flow_e2e/
            # air3b_pipeline_trial/trial_air3b_pipeline/provision_restricted_worker；上一轮回归对账轮改
            # progress_matrix.py）。原断言是**硬相等、无 ledger 出口**，与同一测试上文对 window2_owned
            # 采用的规则不一致——那是适用面不一致，不是更强的判据。改为同一条规则：
            # 已登记的移动放行、未登记的平移仍然失败。
            self.assertIn(live, later.get(name, set()),
                          'shared execution file changed without a recorded supersession: ' + name)
        # the v2 implementation this review covers is unchanged by this window
        for name in ('scripts/server_v2.py', 'scripts/registered_workflow_mcp_v2.py',
                     'scripts/run_registered_workflow_v2.py', 'registered-workflows-v2/README.md',
                     'cases/static-cylinder-step/render_report.py',
                     'tests/test_registered_workflow_v2.py'):
            live = sha(ROOT / name)
            if live == frozen['files'][name]:
                continue
            # same rule as above: later authorized rounds own these bytes, and each move must be recorded
            self.assertIn(live, later.get(name, set()),
                          'v2 review bytes changed without a recorded supersession: ' + name)

    def test_legacy_freeze_check_records_no_change(self):
        check = json.loads((R23 / 'legacy-freeze-check.json').read_text())
        self.assertTrue(check['all_match'])
        self.assertEqual(check['changed_files'], [])
        self.assertEqual(check['files_checked'], 52)


class CandidateAssumptionTests(unittest.TestCase):
    """The candidate generator patches a template and gates on keys the macros must actually emit."""

    @classmethod
    def setUpClass(cls):
        cls.template = json.loads(TEMPLATE.read_text())
        cls.spec = json.loads((CYL / 'case_spec.json').read_text())

    def test_deadline_tuple_matches_the_actual_template_stage_count(self):
        # prepare_candidate zips the deadline tuple onto the template stages and then checks
        # sum(tuple) <= cap. If the template ever grew a stage, zip would silently truncate and the
        # truncated stage would keep an unbounded inherited deadline. prepare() re-sums the WRITTEN
        # deadlines, so the hazard is backstopped - this pins both halves.
        #
        # 2026-10-15 归因：源码断言原先写死 `deadlines = [35, 40, 90, 35, 220, 60]` 这一整行。2026-10-14
        # 的时间预算撤销删除了控制器侧 min(1800,·) 的夹取，deadlines 变成按 cap 分支的表达式
        # （cap<=1740 用原六元组，cap>1740 用 600/900/7200/900/144000/3600，不再夹到 1800）。
        # 这里改为钉住两条**语义**：默认分支就是原六元组；上限分支确实不再被夹到 1800。
        import re as _re
        self.assertEqual(len(self.template['stages']), len(CANDIDATE_DEADLINES))
        self.assertEqual(sum(CANDIDATE_DEADLINES), 480)
        for index in (0, 2, 4, -1):
            self.assertIn('timeout_seconds', self.template['stages'][index])
        pipeline = (ROOT / 'scripts/run_registered_workflow_v2.py').read_text()
        match = _re.search(r'deadlines\s*=\s*\(\[([0-9,\s]+)\]\s*if\s+[^)]*?else\s*\[([0-9,\s]+)\]', pipeline)
        self.assertIsNotNone(match, 'prepare_candidate 的 deadlines 分支表达式未找到')
        default = [int(value) for value in match.group(1).split(',')]
        widened = [int(value) for value in match.group(2).split(',')]
        self.assertEqual(default, CANDIDATE_DEADLINES)
        self.assertEqual(len(widened), len(CANDIDATE_DEADLINES))
        self.assertEqual(sum(default), 480)
        # 上限分支必须**不再**被夹到 1800：这正是撤销轮删除的行为
        self.assertGreater(sum(widened), 1800)
        self.assertNotIn('min(1800', pipeline)
        self.assertIn("stage['timeout_seconds']", pipeline)  # prepare() re-sums the written file

    def test_candidate_gate_keys_are_emitted_by_the_pinned_macros(self):
        gates = {
            'macros/AuditStaticBodyGeometry.java': ['cad_retained', 'input_sim_sha256', 'is_closed',
                                                    'is_manifold', 'measured_bbox_si_m'],
            'macros/BuildExternalFlowRegion.java': ['body_surface_count', 'boundary_count'],
            'macros/ConfigureExternalFlowPhysics.java': ['actual_inlet_speed_m_s'],
        }
        for name, keys in gates.items():
            text = (ROOT / name).read_text()
            for key in keys:
                self.assertIn(key, text, '%s must emit %s for the candidate gate' % (name, key))

    def test_region_macro_itself_requires_seven_boundaries(self):
        # The candidate keeps the template's exact boundary_count == 7 pins and replaces the
        # sphere-specific part_surfaces_after_split pins with body_surface_count. Both are right only
        # because the macro merges the body faces into ONE boundary and asserts the total itself.
        region = (ROOT / 'macros/BuildExternalFlowRegion.java').read_text()
        self.assertIn('if (boundaryCount != 7)', region)
        self.assertIn('getPartSurfaceGroup().setObjects(bodySurfaces)', region)
        domain = self.spec['domain']
        # six outer faces + one merged body boundary is what the macro counts
        self.assertEqual(domain['shape'], 'box')
        self.assertEqual(len(domain['outer_bounds_m']), 6)
        self.assertEqual(self.spec['geometry']['body_surface_count'], 3)
        adapter = (ROOT / 'scripts/run_registered_workflow_v2.py').read_text()
        self.assertIn('boundary_count', TEMPLATE.read_text())
        self.assertIn('body_surface_count', adapter)

    def test_frozen_import_intent_can_never_be_a_runnable_candidate(self):
        # The frozen CaseSpec is the import INTENT; the candidate requires case_ref ==
        # library/<source sha>.sim, so the intent must be refused until a new version names the real
        # saved source. A silent substitution here would be the 'inherit an old approval' defect.
        self.assertTrue(self.spec['case_ref'].startswith('pending/'))
        self.assertTrue(self.spec['input_identity']['source_sim_hash_pending'])
        adapter = (ROOT / 'scripts/run_registered_workflow_v2.py').read_text()
        self.assertIn("'library/' + digest(source) + '.sim'", adapter)
        self.assertIn('Geometry certificate source hash differs from the submitted .sim', adapter)

    def test_window2_budget_carries_the_stated_cap(self):
        # 2026-10-15 归因（"1500.0 != 480"）：原断言把**活的** progress.json 钉在 R23 当天的
        # window2_star_seconds_cap=480 / remaining=480 / used=0 上。2026-10-07 的授权包
        # static_release_four_studies_20261007（用户明确授权、+1020 s）把这个算例的窗口二上限从 480 提到 1500，
        # 并已在只读独立对账记录里逐项登记（before 480 → current_balances 1500）。历史值不改写：
        # 这里改为钉住"每次请求的 CaseSpec 上限仍是 480"这一**未变**的事实，加上活账户的
        # 算术自洽，以及那次 480→1500 变更在冻结记录里的可追溯性。
        for window in ('window1', 'window2'):
            self.assertEqual(json.loads((CYL / 'case_spec.json').read_text())['finite_execution']
                             ['star_seconds_cap'], 480)
        per_request_cap = 480
        authorization = json.loads((ROOT / 'test-evidence/static-release-candidate/'
                                          'window1-four-studies-review-20261007/'
                                          'accounting-readonly-review.json').read_text())
        package = authorization['receipt']['package']
        self.assertEqual(package['authorization_id'], 'static_release_four_studies_20261007')
        self.assertEqual(package['before']['window2_star_seconds_cap'], 480.0)
        self.assertEqual(package['added_STAR_seconds'], 1020)
        self.assertTrue(authorization['sum_matches'])
        self.assertEqual(authorization['receipt']['current_balances']['window2_star_seconds_cap'], 1500.0)
        budget = json.loads((ROOT / 'progress.json').read_text())['budget']['per_case']['static-cylinder-step-r23']
        # live account: 每次请求的上限仍装得下；剩余 = 上限 - 已用；账户不得低于授权包登记的上限
        self.assertGreaterEqual(budget['window2_star_seconds_cap'], per_request_cap)
        self.assertAlmostEqual(budget['window2_remaining_seconds'],
                               budget['window2_star_seconds_cap'] - budget['window2_used_seconds'], places=2)
        self.assertGreaterEqual(budget['window2_used_seconds'], 0)
        self.assertGreaterEqual(budget['window2_remaining_seconds'], per_request_cap)
        self.assertGreaterEqual(budget['remaining_authorized_star_seconds'], per_request_cap)
        self.assertGreaterEqual(budget['star_seconds_logged'], budget['window2_used_seconds'])
        # 2026-10-14 用户裁决 A：台账只记账、不拦提交；两个 key 仍然逐项比较，不足只写 budget_warning。
        # 2026-10-15（独立审查 F4c）：原来这里是对 registerd_workflow_mcp_v2.py 的四处**源码文本**断言，
        # 它们只覆盖提交路径的一处，studies 清单的提交门仍可把台账不足当成拒绝理由而测试看不见（F1）。
        # 改为对唯一实现 budget_ledger_warning 的**行为**断言：两个 key 各自都能单独触发 warning，
        # 两者都足够时不产生 warning；提交门本身由 registered_workflow_v2 的行为测试（排空台账后
        # submission_ready 不变）覆盖。
        from registered_workflow_mcp_v2 import budget_ledger_warning
        self.assertIn('remaining_authorized_star_seconds',
                      budget_ledger_warning({'remaining_authorized_star_seconds': 0,
                                             'window2_remaining_seconds': per_request_cap}, per_request_cap))
        self.assertIn('window2_remaining_seconds',
                      budget_ledger_warning({'remaining_authorized_star_seconds': per_request_cap,
                                             'window2_remaining_seconds': 0}, per_request_cap))
        both = budget_ledger_warning({'remaining_authorized_star_seconds': 1, 'window2_remaining_seconds': 2},
                                     per_request_cap)
        self.assertIn('remaining_authorized_star_seconds', both)
        self.assertIn('window2_remaining_seconds', both)
        self.assertIsNone(budget_ledger_warning({'remaining_authorized_star_seconds': per_request_cap,
                                                 'window2_remaining_seconds': per_request_cap}, per_request_cap))
        api_source = (ROOT / 'scripts/registered_workflow_mcp_v2.py').read_text()
        self.assertIn('budget_ledger_warning', api_source)
        self.assertNotIn("raise ValueError('Registered-case remaining budget is below the registered run cap')",
                         api_source)
        self.assertNotIn("raise ValueError('Complete selected plan lacks current reviewed registrations, "
                         "request allocation or aggregate budget')", api_source)
        self.assertEqual(sum(CANDIDATE_DEADLINES), 480)


class ApprovalIsItemByItemTests(unittest.TestCase):
    """Offline tests passing is not execution approval: every reviewed hash is compared one by one."""

    def setUp(self):
        from air3b_pipeline_trial import support_hashes
        self.temp = tempfile.TemporaryDirectory()
        self.evidence = Path(self.temp.name)
        self.macros = {'AuditStaticBodyGeometry.java': sha(ROOT / 'macros/AuditStaticBodyGeometry.java')}
        self.inputs = {'static_body_geometry.sim': 'a' * 64, 'case_spec.json': 'b' * 64}
        self.runner = sha(ROOT / 'scripts/security/trial_air3b_pipeline.ps1')
        self.controller = sha(ROOT / 'scripts/air3b_pipeline_trial.py')
        self.pipeline = sha(TEMPLATE)
        # 2026-10-15 归因：C3 裁定（FULL-REGRESSION-TRIAGE-RULING-20261014）要求
        # resource_contract_sha256 = sha256(scripts/resource_contract.py) 在**两条路径**（MCP/backend 与
        # 隔离试跑）上都是必填；air3b_pipeline_trial.check_review 因此新增第六条哈希校验。
        # 这条夹具是合成的 review，不是历史证据，所以按 C3 规则本身补字段——
        # 判据没有被绕过：缺字段或被改值都在下面的变异清单里逐个拒绝。
        self.resource_contract = sha(ROOT / 'scripts/resource_contract.py')
        self.review = {'approved': True, 'independent': True, 'macro_hashes': dict(self.macros),
                       'input_hashes': dict(self.inputs), 'runner_sha256': self.runner,
                       'controller_sha256': self.controller, 'pipeline_sha256': self.pipeline,
                       'resource_contract_sha256': self.resource_contract,
                       'support_hashes': support_hashes()}
        self.write(self.review)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, review):
        (self.evidence / 'pipeline-review.json').write_text(json.dumps(review))

    def check(self):
        from air3b_pipeline_trial import check_review
        check_review(self.evidence, self.macros, self.inputs, self.runner, self.controller, self.pipeline)

    def test_a_complete_item_by_item_review_is_accepted(self):
        self.check()

    def test_every_single_missing_or_changed_hash_is_refused(self):
        mutations = {
            'approved': False, 'independent': False,
            'macro_hashes': {'AuditStaticBodyGeometry.java': 'c' * 64},
            'input_hashes': {'static_body_geometry.sim': 'c' * 64, 'case_spec.json': 'b' * 64},
            'runner_sha256': 'c' * 64, 'controller_sha256': 'c' * 64, 'pipeline_sha256': 'c' * 64,
            # C3：第六条哈希（resource_contract.py）同样逐项校验，改值或缺失都必须拒绝
            'resource_contract_sha256': 'c' * 64,
            'support_hashes': {'scripts/stage_plan.py': 'c' * 64},
        }
        for key, value in mutations.items():
            with self.subTest(field=key):
                review = dict(self.review); review[key] = value
                self.write(review)
                with self.assertRaises(ValueError):
                    self.check()
        for key in mutations:
            with self.subTest(missing=key):
                review = dict(self.review); review.pop(key, None)
                self.write(review)
                with self.assertRaises(ValueError):
                    self.check()

    def test_nothing_is_registered_in_v2_so_nothing_can_be_submitted(self):
        # 2026-10-15 归因：原断言要求 v2 注册表**只含 README.md**——那是 R23 当天的状态。R23 之后，
        # 后续已授权轮次真实登记了 72 个工作流（球的 desktop/offline 双源、四轴变体、圆柱 R25/R26 读回等）；
        # 把状态快照钉死等于禁止后续轮次做它们被授权做的事。按 ①R24 在本文件里已采用的写法，
        # 改为钉住它本来要守的**不变量**：本算例的 R24 候选 CaseSpec 与冻结的导入意图 CaseSpec
        # 都没有被登记，因此今天仍然无法提交它们。
        r24_spec = CYL / 'r24/case_spec.json'
        self.assertTrue(r24_spec.is_file())
        frozen_spec_hashes = {sha(CYL / 'case_spec.json'), sha(r24_spec)}
        self.assertEqual(2, len(frozen_spec_hashes))
        registered = {}
        for manifest in sorted((ROOT / 'registered-workflows-v2').glob('*/manifest.json')):
            item = json.loads(manifest.read_text())
            registered[item['id']] = item.get('case_spec_sha256')
        self.assertFalse([name for name in registered if name.startswith('static-cylinder-step-r23')],
                         'the R23 candidate must not be registered')
        self.assertFalse([name for name, digest in registered.items() if digest in frozen_spec_hashes],
                         'neither the R24 candidate CaseSpec nor the frozen intent may be registered')
        # Round 23 could also assert that no geometry-only source existed anywhere. ①R24 produced one
        # (r24-import/static-cylinder-step-import-20261005d/static_body_geometry.sim, b8c9a082...), so that
        # state assertion is replaced by the invariant it was standing in for: the source exists, but it is
        # NOT registered and no candidate was generated from it, so submission is still impossible.
        sources = sorted((ROOT / 'test-evidence/static-cylinder-step').rglob('*.sim'))
        self.assertTrue(sources, 'the round-24 geometry-only source should exist by now')
        self.assertEqual(sorted((ROOT / 'cases/static-cylinder-step').rglob('*.sim')), [],
                         'no .sim may be registered inside the case directory')
        candidates = ROOT / 'test-evidence' / 'static-cylinder-step' / 'r24-import' / 'candidate'
        self.assertFalse(candidates.exists(), 'no candidate may be generated before approval')


if __name__ == '__main__':
    unittest.main()
