"""Behavioral regressions for the user-authorized 2026-10-21 entry fixes; no SSH or STAR."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_formal_run_entry as original

entry = original.entry


class ReviewFixRegressions(unittest.TestCase):
    def check_poll_deadline(self, extra, deadline):
        stub = original.StubBackend(run_state='RUNNING')
        helper = original.TheFormalEntryChainsTheFullPlan()
        # First read starts the clock; two subsequent reads observe the two nonterminal polls.
        with redirect_stdout(io.StringIO()), \
                patch.object(entry.time, 'monotonic', side_effect=[0.0, 0.0, deadline]), \
                patch.object(entry.time, 'sleep') as sleep:
            code, backend = helper.run_entry(stub, extra=extra)
        self.assertEqual(1, code)
        self.assertEqual(['submit_run', 'get_run', 'get_run'], [name for name, _ in backend.calls])
        sleep.assert_called_once()
        # The backend has no cancel operation: observation expiry leaves the worker alone.

    def test_default_observation_window_is_172800_seconds(self):
        self.check_poll_deadline([], 172800.0)

    def test_timeout_overrides_the_default_observation_window(self):
        self.check_poll_deadline(['--timeout', '42'], 42.0)

    def test_explicit_wait_seconds_overrides_timeout(self):
        self.check_poll_deadline(['--timeout', '100', '--wait-seconds', '7'], 7.0)

    def test_python_311_cli_is_refused_before_main(self):
        with patch.object(sys, 'version_info', (3, 11, 9)), \
                patch.object(sys, 'argv', ['formal_run_entry.py', '--help']):
            with self.assertRaisesRegex(SystemExit, r'require Python >= 3\.12'):
                runpy.run_path(str(Path(entry.__file__)), run_name='__main__')

    def test_python_312_cli_reaches_argument_parser(self):
        with patch.object(sys, 'version_info', (3, 12, 0)), \
                patch.object(sys, 'argv', ['formal_run_entry.py', '--help']), \
                redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                runpy.run_path(str(Path(entry.__file__)), run_name='__main__')
        self.assertEqual(0, caught.exception.code)


if __name__ == '__main__':
    unittest.main(verbosity=2)
