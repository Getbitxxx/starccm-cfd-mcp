import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from compare_gmsh_interfaces import compare
from map_face_validity_to_gmsh import map_source_cell


class InterfaceMatchTests(unittest.TestCase):
    def test_real_rotor_stator_interface_mismatch(self):
        rotor = ROOT / "cad/checks/gmsh_rotor_tip_transition_a/rotor_fluid_pilot.msh"
        stator = ROOT / "cad/checks/gmsh_stator_fluid_pilot/stator_fluid_pilot.msh"
        result = compare(rotor, stator, 0.3, 0.15)
        self.assertTrue(result["interfaces"]["top"]["conformal_at_tolerance"])
        self.assertTrue(result["interfaces"]["bottom"]["conformal_at_tolerance"])
        self.assertFalse(result["interfaces"]["side"]["conformal_at_tolerance"])
        self.assertEqual(result["interfaces"]["side"]["geometrically_matching_faces"], 1207)

    def test_localized_bad_cell_maps_to_unmatched_side_face(self):
        rotor = ROOT / "cad/checks/gmsh_rotor_tip_transition_a/rotor_fluid_pilot.msh"
        stator = ROOT / "cad/checks/gmsh_stator_fluid_pilot/stator_fluid_pilot.msh"
        result = map_source_cell(rotor, stator,
                                 (0.04999597049641049, 0.2886048211684318,
                                  -0.10430386460494995), 0.3)
        self.assertEqual(result["source_tetra_candidate_tag"], 172745)
        self.assertLess(result["centroid_distance_m"], 0.001)
        self.assertEqual(len(result["candidate_boundary_side_faces"]), 1)
        self.assertFalse(result["candidate_boundary_side_faces"][0]
                         ["matches_stator_triangle"])

    def test_smooth_rotor_interface_uses_metric_tolerance(self):
        rotor = ROOT / "cad/checks/gmsh_rotor_smooth_model_b/rotor_fluid_pilot.msh"
        stator = ROOT / "cad/checks/gmsh_stator_iface_refine_a/stator_fluid_pilot.msh"
        result = compare(rotor, stator, 0.3, 0.15, tolerance_m=1e-6)
        self.assertEqual(result["interfaces"]["side"]["geometrically_matching_faces"], 1958)
        self.assertFalse(result["interfaces"]["side"]["conformal_at_tolerance"])


if __name__ == "__main__":
    unittest.main()
