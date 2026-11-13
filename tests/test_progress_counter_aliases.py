"""The observed track_P/E-only rewrite must not break the canonical matrix contract."""
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import progress_matrix


class CounterAliasTests(unittest.TestCase):
    def test_prior_track_only_shape_is_rejected_by_existing_gate(self):
        path=ROOT/'test-evidence/hypermesh-cfd-mcp-interface-20261014/prior-state-before-interface/progress.json'
        previous=json.loads(path.read_text())
        report=progress_matrix.check(previous)
        for key in ('pipeline_verified_P','engineering_validated_E'):
            self.assertTrue(any('计数器缺失: '+key in msg for msg in report['blocking']))

    def test_current_canonical_and_human_aliases_agree_with_actual_matrix(self):
        current=progress_matrix.load(ROOT/'progress.json')
        expected=progress_matrix.counters(current)
        for key,value in expected.items():self.assertEqual(current['counters'].get(key),value)
        for alias,key in [('track_P','pipeline_verified_P'),('track_E','engineering_validated_E')]:
            if alias in current['counters']:self.assertEqual(current['counters'][alias],expected[key])


if __name__=='__main__':unittest.main()
