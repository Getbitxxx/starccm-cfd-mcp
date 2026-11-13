"""Registration and read-only behaviour of the two CaseSpec tools.

Runs only where the MCP SDK is installed (the project venv); skipped elsewhere so the
plain `python3 -m unittest` suite still works on a bare interpreter.
"""
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

try:
    import mcp  # noqa: F401
    HAVE_MCP = True
except ImportError:  # pragma: no cover - depends on the interpreter
    HAVE_MCP = False

EVIDENCE = ROOT / 'campaigns/uav-2026/evidence/uav-trapwing-hlpw1'
TE_PRISM = EVIDENCE / 'trapwing-te-prism-20261102/staging/case_spec.json'
CORE_BL = EVIDENCE / 'trapwing-core005-bl8mm-20261102/staging/case_spec.json'
CORE_BL_CONFIG = EVIDENCE / 'trapwing-core005-bl8mm-20261102/staging/config.json'
CASESPEC = ROOT / 'cases/uav-trapwing-hlpw1/case_spec.json'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot(directory):
    return {str(item.relative_to(directory)): (item.stat().st_mtime_ns, item.stat().st_size)
            for item in sorted(Path(directory).rglob('*')) if item.is_file()}


@unittest.skipUnless(HAVE_MCP, 'mcp SDK not installed in this interpreter')
class ToolRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import asyncio
        import server_v2
        cls.server = server_v2
        cls.tools = {tool.name: tool for tool in asyncio.run(server_v2.mcp.list_tools())}

    def test_both_tools_are_advertised_once_and_read_only(self):
        self.assertEqual(len(self.tools), 33)
        self.assertEqual(len(set(self.tools)), 33)
        expectations = {'diff_case_specs': 'never judges a quality gate',
                        'check_mesh_recipe': 'not an accepted mesh'}
        for name, caveat in expectations.items():
            with self.subTest(tool=name):
                self.assertIn(name, self.tools)
                self.assertTrue(self.tools[name].annotations.readOnlyHint)
                self.assertIn('Read-only', self.tools[name].description)
                self.assertIn(caveat, self.tools[name].description)

    def test_diff_tool_does_not_touch_disk(self):
        before = {path: sha(path) for path in (TE_PRISM, CORE_BL, CORE_BL_CONFIG)}
        directory_before = snapshot(TE_PRISM.parent)
        # A tool that wrote elsewhere (cases/, work/, /tmp) would pass a local snapshot only.
        cases_before = snapshot(ROOT / 'cases')
        result = self.server.diff_case_specs(str(TE_PRISM), str(CORE_BL), str(CORE_BL_CONFIG))
        self.assertEqual(len(result['changed']), 2)
        self.assertEqual(result['undeclared'], [])
        self.assertEqual(before, {path: sha(path) for path in (TE_PRISM, CORE_BL, CORE_BL_CONFIG)})
        self.assertEqual(directory_before, snapshot(TE_PRISM.parent))
        self.assertEqual(cases_before, snapshot(ROOT / 'cases'), 'tool wrote outside its inputs')

    def test_recipe_tool_does_not_touch_disk(self):
        before = sha(CASESPEC)
        result = self.server.check_mesh_recipe(str(CASESPEC))
        self.assertTrue(result['ok'])
        self.assertAlmostEqual(result['derived']['implied_growth'], 1.2, places=6)
        self.assertEqual(before, sha(CASESPEC))

    def test_path_guard_reaches_the_tool_surface(self):
        with self.assertRaises(ValueError):        # outside the project
            self.server.check_mesh_recipe('/etc/hosts')
        with self.assertRaises(ValueError):        # inside the project but not a .json
            self.server.diff_case_specs(str(TE_PRISM), str(ROOT / 'README.md'))
        with self.assertRaises(ValueError):        # missing file
            self.server.check_mesh_recipe(str(ROOT / 'cases/nope/case_spec.json'))

    def test_module_has_no_execution_surface(self):
        # Audit the syntax tree, not the prose: the docstring legitimately contains the
        # words this test is about.
        import ast
        source = (ROOT / 'scripts/case_spec_diff.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split('.')[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split('.')[0])
        self.assertLessEqual(imported, {'hashlib', 'json', 'pathlib', '__future__'})

        banned_calls = {'open', 'write_text', 'write_bytes', 'system', 'popen',
                        'exec', 'eval', 'compile', 'run', 'check_output'}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, 'id', None) or getattr(node.func, 'attr', None)
                with self.subTest(call=name):
                    self.assertNotIn(name, banned_calls)

    def test_arguments_are_strings_only(self):
        # The gateway rejects unknown arguments; make sure the schema stays that small shape.
        for name in ('diff_case_specs', 'check_mesh_recipe'):
            schema = self.tools[name].inputSchema
            with self.subTest(tool=name):
                self.assertEqual(schema['type'], 'object')
                self.assertTrue(set(schema['properties']) <= {'baseline', 'candidate', 'declaration', 'case_spec'})
                for prop in schema['properties'].values():
                    self.assertIn('string', json.dumps(prop))


if __name__ == '__main__':
    unittest.main()
