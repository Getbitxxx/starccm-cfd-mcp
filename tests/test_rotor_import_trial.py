import sys,unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from restricted_local_mesh_trial import start_geometry_import_trial,IMPORT_MACRO_SHA,IMPORT_RUNNER_SHA
class ImportSafetyTests(unittest.TestCase):
 def reject(self,review):
  b=Mock()
  with self.assertRaises(ValueError):start_geometry_import_trial(b,'air3b-import-20261002a',Path('unused.stl'),Path('unused'),review)
  b.ps.assert_not_called();b.scp.assert_not_called()
 def test_compilation_is_not_approval(self):self.reject({'approved':True,'independent':False})
 def test_no_review_is_rejected(self):self.reject({})
 def test_changed_review_hash_is_rejected(self):self.reject(dict(approved=True,independent=True,macro_sha256='0'*64,runner_sha256=IMPORT_RUNNER_SHA))
if __name__=='__main__':unittest.main()
