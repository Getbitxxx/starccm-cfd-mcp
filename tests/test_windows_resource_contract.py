"""Run the actual Windows validation fragment without identities or STAR startup."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")

@unittest.skipUnless(PWSH, "PowerShell is required for executable Windows contract validation")
class WindowsResourceContract(unittest.TestCase):
    def validate(self, contract, **overrides):
        source = (ROOT / "scripts/security/trial_air3b_pipeline.ps1").read_text()
        fragment = source[source.index("# New contracts"):source.index("$memoryBytes=$memoryGB*1GB")]
        config = dict(resource_contract=contract, logical_cores=64, memory_gb=64, timeout_seconds=86400)
        config.update(overrides)
        code = "$c=ConvertFrom-Json @'\n" + json.dumps(config) + "\n'@\n" + fragment
        return subprocess.run([PWSH, "-NoProfile", "-Command", "$ErrorActionPreference='Stop';" + code], capture_output=True).returncode

    def contract(self):
        return dict(schema_version=1, logical_cores=64, memory_gb=64, memory_reserve_gb=16, outer_timeout_seconds=172800)

    def test_long_new_request(self):
        self.assertEqual(self.validate(self.contract()), 0)

    def test_rejects_reserve_shortfall_and_resource_drift(self):
        c = self.contract(); c["memory_reserve_gb"] = 15
        self.assertNotEqual(self.validate(c), 0)
        self.assertNotEqual(self.validate(self.contract(), logical_cores=8), 0)

    def test_rejects_timeout_outside_reviewed_outer(self):
        self.assertNotEqual(self.validate(self.contract(), timeout_seconds=172801), 0)
        c = self.contract(); c["outer_timeout_seconds"] = 2678401
        self.assertNotEqual(self.validate(c), 0)

if __name__ == "__main__":
    unittest.main()
