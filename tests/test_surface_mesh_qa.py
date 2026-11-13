import struct,tempfile,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from surface_mesh_qa import audit_binary_stl

class StlSafetyTests(unittest.TestCase):
    def audit(self,triangles):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'test.stl'
            p.write_bytes(b' '*80+struct.pack('<I',len(triangles))+b''.join(struct.pack('<12fH',0,0,0,*sum((list(x) for x in t),[]),0) for t in triangles))
            return audit_binary_stl(p,cad_volume=1/6)
    def fixture(self):
        a=(0,0,0);b=(1,0,0);c=(0,1,0);d=(0,0,1)
        return [(a,c,b),(a,b,d),(a,d,c),(b,c,d)]
    def test_closed_oriented_volume(self):
        r=self.audit(self.fixture());self.assertTrue(r['topology_and_volume_passed']);self.assertFalse(r['self_intersection_checked'])
    def test_missing_face_rejected(self):self.assertFalse(self.audit(self.fixture()[:-1])['topology_and_volume_passed'])
    def test_flipped_face_rejected(self):
        t=self.fixture();t[0]=tuple(reversed(t[0]));self.assertFalse(self.audit(t)['topology_and_volume_passed'])
    def test_duplicate_face_rejected(self):self.assertFalse(self.audit(self.fixture()+[self.fixture()[0]])['topology_and_volume_passed'])
    def test_corrupt_byte_layout_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bad.stl';p.write_bytes(b' '*80+struct.pack('<I',3))
            with self.assertRaises(ValueError):audit_binary_stl(p)
if __name__=='__main__':unittest.main()
