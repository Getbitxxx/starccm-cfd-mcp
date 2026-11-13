"""Counterexamples for the deployment evidence adapters, without running CFD."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'test-evidence/product-acceptance/deployed-20261018'
sys.path.insert(0,str(ROOT/'scripts'))
import release_gate

def module(name,filename):
    spec=importlib.util.spec_from_file_location(name,BASE/filename)
    loaded=importlib.util.module_from_spec(spec);spec.loader.exec_module(loaded)
    return loaded

replay=module('deployed_history_adapter','replay_historical_pat.py')
collector=module('deployed_transfer_adapter','collect_formal_job.py')


class DesktopReadback(unittest.TestCase):
    def response(self):
        return json.loads((BASE/'history-regression/DESKTOP-OPERATIONS-static-sphere-external-flow.json').read_text())

    def test_actual_desktop_response_has_nonempty_structured_operations(self):
        self.assertGreater(len(replay.validate_desktop_resolution(self.response())['operations']),0)

    def test_empty_error_bad_json_and_empty_operation_map_are_refused(self):
        original=self.response();empty=copy.deepcopy(original)
        payload=json.loads(empty['content'][0]['text']);payload['operations']={};empty['content'][0]['text']=json.dumps(payload)
        for bad in [{},{'isError':False},{'isError':True,'content':[]},{'isError':False,'content':[{'type':'text','text':'invalid JSON'}]},{'isError':False,'content':[{'type':'text','text':'{}'}]},empty]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):replay.validate_desktop_resolution(bad)


class TransferCopy(unittest.TestCase):
    def test_valid_copy_preserves_source_and_matches_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source';destination=Path(folder)/'copy'
            source.write_bytes(b'actual downloaded evidence');expected=hashlib.sha256(source.read_bytes()).hexdigest()
            collector.copy_verified_artifact(source,destination,expected)
            self.assertEqual(source.read_bytes(),destination.read_bytes())

    def test_corrupt_source_refuses_before_evidence_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source';destination=Path(folder)/'copy';source.write_bytes(b'source')
            with self.assertRaisesRegex(ValueError,'source hash mismatch'):
                collector.copy_verified_artifact(source,destination,'0'*64)
            self.assertFalse(destination.exists())

    def test_copy_corruption_cannot_produce_verified_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source';destination=Path(folder)/'copy';source.write_bytes(b'source')
            expected=hashlib.sha256(source.read_bytes()).hexdigest()
            with patch.object(collector.shutil,'copyfile',side_effect=lambda src,dst:dst.write_bytes(b'corrupted destination')):
                with self.assertRaisesRegex(ValueError,'destination hash mismatch after copy'):
                    collector.copy_verified_artifact(source,destination,expected)
            self.assertEqual(source.read_bytes(),b'source')


class SeparatePatResult(unittest.TestCase):
    def test_explicit_deployed_result_passes_while_stale_legacy_default_still_blocks(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);pat=root/'PAT-DEPLOYED-20261018.json';result=root/'PAT-DEPLOYED-RESULT.json';legacy=root/'PAT-RESULT.json'
            pat.write_text(json.dumps({'release':'deployed','cases':[{'id':'A','release_channel':'v1','required_for_release':True}]}))
            result.write_text(json.dumps({'release':'deployed','pat_sha256':hashlib.sha256(pat.read_bytes()).hexdigest(),'cases':[{'id':'A','result':'PASS'}],'v1_required_total':1,'v1_required_passed':1}))
            legacy.write_text(json.dumps({'release':'old','pat_sha256':'0'*64,'cases':[]}));old=legacy.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(release_gate.main(['--pat',str(pat),'--result',str(result)]),0)
                self.assertEqual(release_gate.main(['--pat',str(pat)]),1)
            self.assertEqual(legacy.read_bytes(),old)


if __name__=='__main__':unittest.main()
