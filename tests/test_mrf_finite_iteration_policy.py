"""Execute the MRF macro's actual stopping helpers; retain the old time-only counterexample."""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ActualMrfStopping(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'macros/RunAir3bMrfSegment.java').read_text()
        methods = []
        for signature in ('int finiteInteger', 'int nextChunk', 'boolean wallClockStop'):
            match = re.search(r'    private static ' + signature + r'\(.*?\n    }', cls.source, re.S)
            if not match:
                raise AssertionError('Missing actual helper: ' + signature)
            methods.append(match.group(0))
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        java = 'public class MrfStopProbe {\n' + '\n'.join(methods) + '''
        public static void main(String[] args) {
            if (args[0].equals("clock"))
                System.out.println(wallClockStop(Double.parseDouble(args[1]), Double.parseDouble(args[2])));
            else if (args[0].equals("integer")) {
                try { System.out.println(finiteInteger(Double.parseDouble(args[1]), 0, "cap")); }
                catch (IllegalArgumentException expected) { System.out.println("REJECTED"); }
            } else {
                int ran=0, planned=Integer.parseInt(args[1]), chunk=Integer.parseInt(args[2]);
                while (ran < planned) ran += nextChunk(chunk, planned, ran);
                System.out.println(ran);
            }
        }}'''
        path = cls.directory / 'MrfStopProbe.java'
        path.write_text(java)
        subprocess.run(['javac', str(path)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def java(self, *args):
        return subprocess.run(['java', '-cp', str(self.directory), 'MrfStopProbe', *map(str, args)],
                              check=True, capture_output=True, text=True).stdout.strip()

    def test_zero_budget_survives_elapsed_time_and_is_the_default(self):
        for elapsed in (0, 3601, 1000000000):
            self.assertEqual(self.java('clock', 0, elapsed), 'false')
        self.assertIn('env("AIR3B_SEGMENT_SECONDS", 0.0)', self.source)
        self.assertIn('if (wallClockStop(budgetSeconds, elapsed)) break;', self.source)
        self.assertNotIn('if (elapsed >= budgetSeconds) break;', self.source)

    def test_legacy_positive_budget_is_explicit_not_default(self):
        self.assertEqual(self.java('clock', 60, 59), 'false')
        self.assertEqual(self.java('clock', 60, 60), 'true')

    def test_iteration_cap_is_exact_even_for_partial_last_chunk(self):
        for planned, chunk in ((1, 10), (601, 10), (625, 100), (600, 10)):
            self.assertEqual(self.java('run', planned, chunk), str(planned))
        self.assertIn('.run(nextChunk(chunk, planned, ran))', self.source)

    def test_nonfinite_fractional_or_overflowing_controls_are_rejected(self):
        for value in ('NaN', 'Infinity', '-1', '.5', '2147483648'):
            self.assertEqual(self.java('integer', value), 'REJECTED')
        self.assertIn('if (budgetSeconds == 0 && cap == 0)', self.source)

    def test_periodic_checkpoint_and_honest_policy_are_recorded(self):
        self.assertIn('chunks % checkpointEvery == 0', self.source)
        self.assertIn('sim.saveState(periodicCheckpoint.getAbsolutePath())', self.source)
        self.assertIn('wall_clock_stop_enabled\\\":', self.source)
        self.assertIn('periodic_checkpoints_saved\\\":', self.source)


if __name__ == '__main__':
    unittest.main()
