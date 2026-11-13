import json
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import restricted_local_mesh_trial as trial

class ScopedTrialContractTests(unittest.TestCase):
    def setUp(self):
        self.b=Mock(remote_root=r'C:\service')
        self.source=r'C:\service\library'+'\\'+('a'*64)+'.sim'
    def reject(self, **kw):
        args=dict(b=self.b,trial_id='trial-123456',mode='configure',source=self.source,
                  source_sha256='a'*64,properties=Path('missing.properties'),evidence=Path('unused'))
        args.update(kw)
        with self.assertRaises(ValueError):trial.start_trial(**args)
        self.b.ps.assert_not_called();self.b.scp.assert_not_called()
    def test_no_arbitrary_macro_mode(self):self.reject(mode='shell')
    def test_no_traversal_or_unregistered_source(self):
        for source in [r'C:\Users\chen\Desktop\x.sim',r'C:\service\library\..\x.sim',
                       r'C:\CodexCFDRestricted\trial-123456\input.sim']:
            self.reject(source=source)
    def test_no_command_syntax_in_trial_id(self):self.reject(trial_id='trial-123456;whoami')
    def test_library_hash_must_match_address(self):self.reject(source_sha256='b'*64)
    def test_changed_candidate_is_rejected_before_remote_activity(self):
        with patch.dict(trial.SOURCES, {'configure':('ConfigureAuditedLocalMesh.java','trial_local_mesh.ps1','0'*64)}):self.reject()
    def test_changed_physical_runner_is_rejected_before_remote_activity(self):
        # Macro hash is untouched; a modified runner must independently fail the scope gate.
        from hashlib import sha256
        real=sha256
        class BadRunnerHash:
            def hexdigest(self):return '0'*64
        def conditional(data):
            return BadRunnerHash() if data.startswith(b'param(') else real(data)
        with patch('restricted_local_mesh_trial.hashlib.sha256',side_effect=conditional):
            self.reject(mode='physics')

    def test_invalid_hash_cannot_reach_windows(self):self.reject(source_sha256='not-a-hash')

    def test_initialization_rejects_unassessed_source_before_remote_activity(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence=Path(directory)
            (evidence/'clearance-repair-summary.json').write_text(json.dumps({'trial':{'output_sha256':'a'*64},'preinit_quality':{'passed':True}}))
            self.reject(mode='initialize',evidence=evidence)
    def test_initialization_requires_successful_prior_quality_gate(self):
        digest='811587d6ff26936cd8aa10e55505877ea354852bf02db31d746fe82351fce803'
        with tempfile.TemporaryDirectory() as directory:
            evidence=Path(directory)
            (evidence/'clearance-repair-summary.json').write_text(json.dumps({'trial':{'output_sha256':digest},'preinit_quality':{'passed':False}}))
            self.reject(mode='initialize',evidence=evidence,source=trial.ROOT+r'\mesh-generate-clearance-20261001a\local_mesh_generated.sim',source_sha256=digest)

if __name__=='__main__':unittest.main()
