"""Independent review counterexamples; no STAR or production mutation."""
import importlib.util
from pathlib import Path
import re
import json
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
sp = importlib.util.spec_from_file_location('independent_resource', ROOT / 'scripts/resource_contract.py')
r = importlib.util.module_from_spec(sp)
sp.loader.exec_module(r)

class IndependentContractReview(unittest.TestCase):
    def test_exact_registration_manifest_missing_pin_refuses(self):
        from unittest.mock import patch
        import sys
        sys.path.insert(0, str(ROOT/'scripts'))
        import registered_workflow_mcp_v2 as v2
        from registered_workflow_mcp import checked_registration as legacy_check
        wid = 'resource64-fine-readback-20261009a'
        manifest_path = ROOT/'registered-workflows-v2'/wid/'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        evidence = ROOT/'test-evidence/resource-contract-20261009/fine-readback'
        review = json.loads((evidence/'pipeline-review.json').read_text())
        self.assertIn(str(manifest_path.relative_to(ROOT)), review['registration_hashes'])
        bad = dict(review, registration_hashes=dict(review['registration_hashes']))
        bad['registration_hashes'].pop(str(manifest_path.relative_to(ROOT)))
        # Exercise the actual production check without changing disk or approved bytes.
        import registered_workflow_mcp as legacy
        with patch.object(legacy, 'registration', return_value=(manifest,evidence,manifest_path)), patch.object(legacy, 'load', return_value=bad):
            with self.assertRaisesRegex(ValueError, 'Reviewed registration bytes changed'):
                legacy_check(wid)
        v2.checked_registration(wid)

    def test_build_step_wrong_basename_rejected_before_upload(self):
        import sys
        sys.path.insert(0, str(ROOT/'scripts'))
        from air3b_pipeline_trial import start
        d = ROOT/'test-evidence/resource-contract-20261009/generalization-plan'
        class NoNetwork:
            def __getattr__(self, name):
                raise AssertionError('No network or STAR allowed in counterexample')
        with self.assertRaisesRegex(ValueError, 'Input differs from the pipeline pin: case_spec-build.json'):
            start(NoNetwork(), 'resource64-combined-origin-20261009a', d/'pipeline-build-source.json', [d/'case_spec-build.json'], d/'build')

    def test_log_format_only_restores_original_approved_bytes(self):
        """日志格式改动可逆；只有**未被后续批准轮次改过行为的**文件才应还原到历史基线。

        2026-10-14 归因（审查者点名 20b0e4d2 vs 8615d030）：本测试原先把两个文件一视同仁，要求
        "只回退日志格式即可还原历史批准字节"。时间预算撤销轮改了**试跑路径** runner 的行为
        （删除到点杀进程与四处按时间拒绝），于是：
          * `worker/run_job.ps1` 在2026-10-18正式部署修复后也不再能仅回退日志格式还原历史字节；
          * `scripts/security/trial_air3b_pipeline.ps1`（该轮改过行为）**不可能**再还原到 8615d030…。
        两者都仍必须满足"日志格式改动可精确往返"。历史值与归因见
        REGRESSION-ROUND-RULING-COMPLIANCE-20261014.json。
        """
        import hashlib
        suffix = b' | ForEach-Object {[string]$_}'
        # (文件, 历史基线, 该基线是否仍可达)
        pairs = [('scripts/security/trial_air3b_pipeline.ps1',
                  '8615d0302f838d3aca58e194b288b06f5344900ae8bac0438c8a8f9f5fcc32b3', False),
                 ('worker/run_job.ps1',
                  '3f26172c5fd4c877091c0a1ca7540a85ca8078dfe1c151368188546bcc285bba', False)]
        for name, oldhash, reachable in pairs:
            content = (ROOT/name).read_bytes()
            evidence_lines = [line for line in content.splitlines() if b'lines=@($lines | Where-Object' in line]
            self.assertEqual(len(evidence_lines), 2, name)
            self.assertTrue(all(suffix in line for line in evidence_lines), name)
            restored = content.replace(b'}'+suffix+b')', b'})')
            # 可逆性：逐行比较——改动只应落在那两条证据行上，且每行都能精确还原
            original_lines, restored_lines = content.splitlines(), restored.splitlines()
            self.assertEqual(len(original_lines), len(restored_lines), name)
            differing = [i for i, (a, b) in enumerate(zip(original_lines, restored_lines)) if a != b]
            self.assertEqual(2, len(differing), '日志格式改动只应影响两条证据行：' + name)
            for i in differing:
                self.assertEqual(original_lines[i], restored_lines[i].replace(b'})', b'}'+suffix+b')'),
                                 '该行的日志格式改动必须可精确还原：' + name)
            actual = hashlib.sha256(restored).hexdigest()
            if reachable:
                self.assertEqual(actual, oldhash, name)
            else:
                self.assertNotEqual(actual, oldhash,
                                    '若这里又相等，说明撤销轮的行为改动被回退了；需要重新裁定：' + name)

    def test_formal_worker_changes_have_a_current_independent_deployment_review(self):
        from backend import Backend,sha256
        from formal_worker import deployment_review
        approval=deployment_review(Backend())
        self.assertIs(approval['approved_for_deployment'],True)
        self.assertIs(approval['independent'],True)
        self.assertEqual(approval['runner_sha256'],sha256(ROOT/'worker/run_job.ps1'))

    def test_real_tessellation_collision_audit_refused_build_citable(self):
        import sys
        sys.path.insert(0, str(ROOT/'scripts'))
        from run_record_gate import assert_citable, RecordNotCitable
        d = ROOT/'test-evidence/resource-contract-20261009/generalization-plan/origin-run/resource64-combined-origin-20261009a'
        error = json.loads((d/'static_body_audit.json').read_text())
        self.assertIn('Tessellation export target already exists', error['error'])
        with self.assertRaises(RecordNotCitable):
            assert_citable(d/'static_body_audit.json')
        assert_citable(d/'static_body_geometry.json')

    def test_collision_repair_is_only_audit_without_stl_input(self):
        import sys
        sys.path.insert(0, str(ROOT/'scripts'))
        from stage_plan import plan_pipeline
        from resource_contract import validate_pipeline_resources
        d = ROOT/'test-evidence/resource-contract-20261009/generalization-plan'
        p = json.loads((d/'pipeline-audit-fix1.json').read_text())
        self.assertEqual(set(p['input_hashes']), {'case_spec.json','static_body_geometry.sim'})
        self.assertEqual(list(p['macro_hashes']), ['AuditStaticBodyGeometry.java'])
        self.assertEqual(len(p['stages']), 1)
        self.assertIsNone(p['stages'][0]['output'])
        self.assertEqual(p['stages'][0]['macro_outputs'], [None])
        self.assertEqual(p['stages'][0]['input'], 'static_body_geometry.sim')
        plan_pipeline(p)
        validate_pipeline_resources(p, 900)

    def test_new_default_does_not_inherit_old_spec_8(self):
        self.assertEqual(r.make_contract({'logical_cores': 8})['logical_cores'], 64)

    def test_pipeline_bool_not_equal_numeric_resource(self):
        contract = r.make_contract({}, {'logical_cores': 1, 'memory_gb': 1, 'outer_timeout_seconds': 600})
        for field in ('logical_cores', 'memory_gb'):
            pipeline = dict(resource_contract=contract, logical_cores=1, memory_gb=1,
                            task_budget_seconds=120, stages=[{'timeout_seconds': 60}])
            pipeline[field] = True
            with self.subTest(field=field), self.assertRaises(ValueError):
                r.validate_pipeline_resources(pipeline, 60)

    def test_python_strict_types_and_nonfinite(self):
        base = r.make_contract({})
        for key in base:
            bad = dict(base, **{key: True})
            with self.subTest(key=key), self.assertRaises(ValueError):
                r.validate_contract(bad)
        for key in ('memory_gb', 'memory_reserve_gb'):
            for value in (float('nan'), float('inf'), '64'):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    r.validate_contract(dict(base, **{key: value}))

    def test_powershell_contract_checks_type_before_conversion(self):
        # Real JSON bool/string coercion is independently guarded in Windows acceptance.
        for name in ('worker/run_job.ps1', 'scripts/security/trial_air3b_pipeline.ps1'):
            src = (ROOT/name).read_text()
            prefix = src[src.index('$fields='):src.index('$cores=[double]')]
            self.assertRegex(prefix, r'(?i)(-is\s+\[bool\]|-isnot\s+\[.*(?:int|double)|Assert.*(?:Numeric|Number|Resource))', name)

    def test_failed_checkout_not_a_success_certificate(self):
        for name in ('worker/run_job.ps1', 'scripts/security/trial_air3b_pipeline.ps1'):
            src = (ROOT/name).read_text()
            line = next(x for x in src.splitlines() if 'license_checkout_lines=@($lines' in x)
            regex = re.search(r"-match '([^']+)'", line).group(1)
            fake = 'Failed to checkout license ccmpsuite'
            matches = bool(re.search(regex, fake))
            # Explicit exclusions or a positive successful-checkout matcher must protect certificate.
            excludes = bool(re.search(r'(?i)-notmatch.*(?:fail|denied|reject|unable)', line))
            self.assertTrue(not matches or excludes, name + ': ' + fake)

@unittest.skipUnless(shutil.which('pwsh'), 'PowerShell executable required; run on Windows before approval')
class IndependentWindowsExecution(unittest.TestCase):
    def execute_fragment(self, contract):
        src = (ROOT/'scripts/security/trial_air3b_pipeline.ps1').read_text()
        fragment = src[src.index('# New contracts'):src.index('$memoryBytes=$memoryGB*1GB')]
        c = dict(resource_contract=contract, logical_cores=64, memory_gb=64, timeout_seconds=120)
        code = "$ErrorActionPreference='Stop';$c=ConvertFrom-Json @'\n"+json.dumps(c)+"\n'@\n"+fragment
        return subprocess.run([shutil.which('pwsh'), '-NoProfile', '-Command', code], capture_output=True).returncode

    def test_rejects_bool_string_float_integer_fields(self):
        base = r.make_contract({})
        for key in base:
            for value in (True, '64'):
                with self.subTest(key=key, value=value):
                    self.assertNotEqual(self.execute_fragment(dict(base, **{key: value})), 0)
        for key in ('schema_version', 'logical_cores', 'outer_timeout_seconds'):
            with self.subTest(key=key):
                self.assertNotEqual(self.execute_fragment(dict(base, **{key: float(base[key])})), 0)

if __name__ == '__main__':
    unittest.main()
