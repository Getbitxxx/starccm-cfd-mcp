"""R21 registration and stale-state regressions, no STAR launches."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('registered_entry', ROOT / 'scripts/run_registered_workflow.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class RegisteredEntry(unittest.TestCase):
    def test_arbitrary_source_filename_is_copied_and_adapter_mutation_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack = root / 'registered-workflows/test-pack'
            pack.mkdir(parents=True)
            source = root / 'user-real-propeller.sim'
            source.write_bytes(b'registered geometry')
            case = root / 'UserCaseSpec.json'
            case.write_bytes(b'{}')
            present = root / 'present.py'
            present.write_text('# frozen presentation')
            adapter = root / 'adapter.py'
            adapter.write_text('# frozen adapter')
            pipeline = {'input_hashes': {'source_input.sim': entry.digest(source), 'case_spec.json': entry.digest(case)}, 'stages': [{'timeout_seconds': 60}]}
            (root / 'pipeline.json').write_text(json.dumps(pipeline))
            manifest = {'id': 'test-pack', 'mechanism': 'restricted_trial', 'pipeline': 'pipeline.json', 'star_seconds_cap': 600, 'poll_timeout_seconds': 900, 'presentation_entry': 'present.py', 'adapter_files': ['adapter.py', 'present.py']}
            manifest_path = pack / 'manifest.json'
            manifest_path.write_text(json.dumps(manifest))
            evidence = root / 'evidence'
            evidence.mkdir()
            pins = {name: entry.digest(root / name) for name in ['adapter.py', 'present.py', 'registered-workflows/test-pack/manifest.json']}
            (evidence / 'pipeline-review.json').write_text(json.dumps({'approved': True, 'independent': True, 'registration_hashes': pins}))
            with patch.object(entry, 'ROOT', root):
                _, command = entry.prepare('test-pack', source, case, evidence, 'trial-test')
                self.assertEqual((evidence / 'registered-inputs/source_input.sim').read_bytes(), source.read_bytes())
                self.assertIn(str(root / 'scripts/run_static_external_flow_e2e.py'), command)
                adapter.write_text('# changed adapter')
                with self.assertRaisesRegex(ValueError, 'adapter hash differs'):
                    entry.prepare('test-pack', source, case, evidence, 'trial-test-2')

    def test_delegates_execution_and_rejects_changed_input_before_launch(self):
        source = (ROOT / 'scripts/run_registered_workflow.py').read_text()
        self.assertIn('scripts/run_static_external_flow_e2e.py', source)
        self.assertNotIn('Backend(', source)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack = root / 'registered-workflows/test-pack'
            pack.mkdir(parents=True)
            (root / 'pipeline.json').write_text(json.dumps({'input_hashes': {'source_input.sim': '0' * 64}, 'stages': [{'timeout_seconds': 60}]}))
            (root / 'present.py').write_text('')
            (pack / 'manifest.json').write_text(json.dumps({'id': 'test-pack', 'mechanism': 'restricted_trial', 'pipeline': 'pipeline.json', 'star_seconds_cap': 600, 'presentation_entry': 'present.py', 'adapter_files': ['present.py']}))
            source_path = root / 'user.sim'
            source_path.write_bytes(b'wrong source')
            with patch.object(entry, 'ROOT', root):
                with self.assertRaisesRegex(ValueError, 'Submitted input differs'):
                    entry.prepare('test-pack', source_path, source_path, root / 'evidence', 'trial-test')
            self.assertEqual(source_path.read_bytes(), b'wrong source')

    def test_path_traversal_is_refused(self):
        for name in ['../escape', '..', '.', '/tmp/escape']:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Invalid registered workflow'):
                entry.prepare(name, ROOT / 'progress.json', ROOT / 'progress.json', ROOT, 'trial-test')

    def test_catalog_does_not_promote_registered_sample_to_generic(self):
        import sys
        sys.path.insert(0, str(ROOT / 'scripts'))
        from casespec import resolve_operations
        operation = resolve_operations(['original_geometry_mrf_chain'], case='original-geometry')['operations']['original_geometry_mrf_chain']
        self.assertEqual(operation['scope_class'], 'sample_specific')
        self.assertEqual(operation['provides'], [])
        self.assertIn('original_mrf_solved.sim', operation['outputs'])
        self.assertEqual(operation['implementation'], 'RESOLVED')

    def test_current_original_state_follows_citable_r20_not_historical_failure(self):
        import sys
        sys.path.insert(0, str(ROOT / 'scripts'))
        from run_record_gate import citation_status
        progress = json.loads((ROOT / 'progress.json').read_text())
        self.assertNotIn('BLOCKED', progress['generality']['cases']['original_geometry_8000rpm_20mps'])
        self.assertEqual(progress['generality']['matrix_status'], 'PARTIAL')
        original = progress['scenarios']['2_steady_mrf_propeller']['original_axis']
        self.assertNotEqual(original['final_solve_sim'], '未生成')
        self.assertFalse(original['engineering_validated'])
        for record in ['mrf_physics_readback.json', 'mrf_volume_mesh_quality.json', 'air3b_segment_verdict.json']:
            self.assertEqual(citation_status(ROOT / 'test-evidence/original-geometry/r20/run/original-r20-20261005b' / record)['state'], 'CITABLE')

    def test_unchanged_r20_physics_geometry_and_engineering_gates(self):
        old = json.loads((ROOT / 'cases/original-geometry/r20/pipeline.json').read_text())
        new = json.loads((ROOT / 'cases/original-geometry/r21/pipeline.json').read_text())
        for name in ['env', 'input_hashes']:
            self.assertEqual(old[name], {k: v for k, v in new[name].items() if k in old[name]})
        for before, after in zip(old['stages'][:-1], new['stages'][:-1]):
            self.assertEqual({k: v for k, v in before.items() if k != 'timeout_seconds'},
                             {k: v for k, v in after.items() if k != 'timeout_seconds'})
        before, after = old['stages'][-1], new['stages'][-1]
        self.assertEqual(before['macros'], after['macros'][:3])
        self.assertEqual(before['macro_outputs'], after['macro_outputs'][:3])
        self.assertTrue(set(before['required']).issubset(after['required']))
        self.assertTrue(set(before['require_contains']).issubset(after['require_contains']))
        self.assertEqual(before['input'], after['input'])
        self.assertEqual(before['output'], after['output'])
        self.assertLessEqual(sum(s['timeout_seconds'] for s in new['stages']), 600)


if __name__ == '__main__':
    unittest.main()
