"""Regression from the real 2026-10-20 pilot: the runner writes a UTF-8 BOM, the reader ate it.

History: the deployed new macro rejected the BOM written by the unchanged Windows runner and
the pilot failed with "Invalid JSON at 0", so this suite stood at 1 PASS / 1 FAIL. The
2026-10-23 closure round (item D) fixed the reader, not the input: the historical bytes are
still verified byte-for-byte below, and no test is marked expectedFailure. The last test pins
the fix's boundary -- one leading U+FEFF is accepted, a U+FEFF anywhere else is still invalid.
No STAR is started by this suite.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/'test-evidence/e-static-sphere-urans-20261020/pilot/raw/e-sphere-urans-pilot-20261020a'


class RealPilotUtf8BomRegression(unittest.TestCase):
    def test_real_runner_input_identity_and_valid_json_payload_are_preserved(self):
        raw=(RAW/'case_spec.json').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),'f9b251bbcb7489aece07a6725dc7e39ca0e59d0c569bc8a0c883057cf7978de7')
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(json.loads(raw.decode('utf-8-sig'))['transient']['time_step_s'],.0002)

    def test_macro_reader_accepts_the_real_runner_written_utf8_bom(self):
        text=(ROOT/'macros/RunExternalFlowTransient.java').read_text()
        pure=text.split('// BEGIN PURE CONTRACT',1)[1].split('\n',1)[1].split('// END PURE CONTRACT',1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            code='import java.util.*; public class BomContract {'+pure+'''
              public static void main(String[] args) throws Exception {
                String s=new String(java.nio.file.Files.readAllBytes(java.nio.file.Paths.get(args[0])),java.nio.charset.StandardCharsets.UTF_8);
                new Config(object(new Json(s).parse()),new HashMap<String,String>());
              }}'''
            (path/'BomContract.java').write_text(code)
            subprocess.run(['javac',str(path/'BomContract.java')],check=True,capture_output=True)
            result=subprocess.run(['java','-cp',tmp,'BomContract',str(RAW/'case_spec.json')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,'the reader must accept the real runner UTF-8 BOM. '+result.stderr)

    def test_bom_is_stripped_only_at_the_head_of_the_payload(self):
        """The fix tolerates the runner's BOM; it does not excuse U+FEFF anywhere else."""
        text=(ROOT/'macros/RunExternalFlowTransient.java').read_text()
        pure=text.split('// BEGIN PURE CONTRACT',1)[1].split('\n',1)[1].split('// END PURE CONTRACT',1)[0]
        spec=json.loads((RAW/'case_spec.json').read_text(encoding='utf-8-sig'))
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            code='import java.util.*; public class BomContract {'+pure+'''
              public static void main(String[] args) throws Exception {
                String s=new String(java.nio.file.Files.readAllBytes(java.nio.file.Paths.get(args[0])),java.nio.charset.StandardCharsets.UTF_8);
                new Config(object(new Json(s).parse()),new HashMap<String,String>());
              }}'''
            (path/'BomContract.java').write_text(code)
            subprocess.run(['javac',str(path/'BomContract.java')],check=True,capture_output=True)
            # A U+FEFF smuggled in front of a member name is still malformed JSON and must fail.
            inside=json.dumps(spec).replace('"transient"','\ufeff' + '"transient"',1)
            (path/'inside.json').write_text(inside,encoding='utf-8')
            refused=subprocess.run(['java','-cp',tmp,'BomContract',str(path/'inside.json')],capture_output=True,text=True)
            self.assertNotEqual(refused.returncode,0,'U+FEFF in mid-payload must stay invalid')
            self.assertIn('Invalid JSON',refused.stderr)
            # Two leading BOMs are not the runner's output; only the first is stripped.
            (path/'double.json').write_text('\ufeff\ufeff'+json.dumps(spec),encoding='utf-8')
            doubled=subprocess.run(['java','-cp',tmp,'BomContract',str(path/'double.json')],capture_output=True,text=True)
            self.assertNotEqual(doubled.returncode,0,'a second leading U+FEFF must stay invalid')
