# -*- coding: utf-8 -*-
"""②R17 role/mesh regression contracts.

The counter-example is the real old full-360 prism-4 mesh: it was successfully
generated but had VolumeChange 0.005696, and its geometrical roles were later
corrected. A generation certificate or an audit exit code must never certify a
fresh original-geometry quality pass. Java operation selection is checked here
as an executable lint; actual installed-API compilation and STAR readback remain
separate acceptance obligations.
"""
from __future__ import annotations
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_record_gate as gate

OWNED = ("CreateMrfRegions.java", "AuditMrfSurfaceMap.java",
         "ConfigureMrfInterfaces.java", "ConfigureMrfMeshOperation.java",
         "ExecuteMrfDiagnosticMesh.java")
LEGACY = ROOT / "test-evidence/full-360-mrf-prism4-mesh-quality.json"

def source(name):
    return (ROOT / "macros" / name).read_text(encoding="utf-8")

class RoleSelectionContract(unittest.TestCase):
    def test_no_sample_name_constant_selectors(self):
        # §4's selection lint: a renamed real user geometry cannot be silently
        # classified as a blade because the string happens to be PROPELLER.
        for name in OWNED:
            with self.subTest(macro=name):
                text = source(name)
                literals = re.findall(r'"(?:\\.|[^"\\])*"', text)
                self.assertFalse(any(re.search(r"\b(?:PROPELLER|AIR3B|AUTO_FULL_ROTOR_BLADE_)\b", item)
                                     for item in literals))
                self.assertNotIn('.startsWith(', text)
                self.assertIn('"geometry"', text)
                self.assertIn('"stationary_fluid_part"', text)
                self.assertIn('"rotating_fluid_part"', text)

    def test_regions_require_clean_entry_and_validate_real_surface_ownership(self):
        text = source("CreateMrfRegions.java")
        clean = text.index("!manager.getObjects().isEmpty()")
        create = text.index("Region stationary = create(")
        self.assertLess(clean, create)
        self.assertIn("part.getPartSurfaceManager().getObjects().contains(surface)", text)
        self.assertIn("b.getPartSurfaceGroup().getObjects().contains(selected)", text)
        self.assertNotRegex(text, r"rotorWalls\s*[!=]=\s*[1-9]")
        self.assertNotIn("AUTO_FULL360_FLOW", text)

    def test_interface_frame_certificate_is_actual_but_inlet_is_deferred(self):
        text = source("ConfigureMrfInterfaces.java")
        self.assertIn("frame.getRotationRate().getSIValue()", text)
        self.assertIn("frame.getAxisVector().getVector().toDoubleArray()", text)
        # ②R19 moved the region MotionSpecification assignment to the stage that owns the physics
        # continuum: a bare geometry region has no MotionSpecification (measured failure fixture
        # 32a89d02…). The regions macro must no longer read it, must still MEASURE the frame, and must
        # declare where the assignment happens; the physics macro must still assign it and throw when
        # the rotating region did not receive it.
        self.assertNotIn("getValues().get(MotionSpecification.class)", text)
        self.assertIn(r'\"rotation_specification_assignment_deferred_to\":\"ConfigureAir3bMrfPhysics\"', text)
        physics = source("ConfigureAir3bMrfPhysics.java")
        self.assertIn("rotating.getValues().get(MotionSpecification.class).setReferenceFrame(frame)", physics)
        self.assertIn("getValues().get(MotionSpecification.class).getReferenceFrame()", physics)
        self.assertIn("Rotating region did not receive the requested frame", physics)
        self.assertIn(r'\"boundary_values_deferred_to_physics\":true', text)
        self.assertNotIn("VelocityMagnitudeProfile", text)
        self.assertNotIn(r'\"inlet_speed_m_s\":', text)

    def test_mesh_is_two_owned_parts_and_retains_a_first_region_checkpoint(self):
        text = source("ExecuteMrfDiagnosticMesh.java")
        self.assertIn("getInputGeometryObjects().getObjects().contains(part)", text)
        self.assertIn("getInputGeometryObjects().getObjects().size() != 1", text)
        self.assertLess(text.index('rotatingOperation.execute()'),
                        text.index('"mrf_rotating_mesh_checkpoint.sim"'))
        self.assertLess(text.index('"mrf_rotating_mesh_checkpoint.sim"'),
                        text.index("stationaryOperation.execute()"))
        self.assertIn('"mrf_mesh_failure_checkpoint.sim"', text)

    def test_nonwall_interface_and_outer_surfaces_explicitly_disable_prisms(self):
        text = source("ConfigureMrfMeshOperation.java")
        for control in ("rotor-interface-control", "stator-interface-control", "outer-flow-control"):
            self.assertRegex(text, r'control\([^;]*"' + re.escape(control) + r'"[^;]*, true\)')
        self.assertIn("PartsCustomPrismsOption.Type.DISABLE", text)
        self.assertIn("setOverrideBoundaryDefault(true)", text)
        self.assertIn('"DISABLE".equals(', text)

class RealLegacyMeshCounterExample(unittest.TestCase):
    def test_real_generated_mesh_is_still_a_quality_failure(self):
        record = json.loads(LEGACY.read_text(encoding="utf-8"))
        self.assertEqual("MESH_GENERATED_QUALITY_GATE_FAILED", record["state"])
        self.assertFalse(record["mesh_quality_verified"])
        self.assertFalse(record["solver_run"])
        failures = [c for c in record["mesh_quality"]["checks"] if not c["passed"]]
        self.assertEqual([("AUTO_MRF_ROTATING", "VolumeChange")],
                         [(c["region"], c["metric"]) for c in failures])
        self.assertEqual(0.005696073114715575, failures[0]["minimum"])
        self.assertEqual(0.01, failures[0]["threshold"])
        # Same unchanged quality thresholds still apply to a newly executed mesh.
        text = source("AuditMrfVolumeMeshQuality.java")
        for field, constant in (("mesh_min_cell_quality", "MIN_CELL_QUALITY"),
                                ("mesh_min_face_validity", "MIN_FACE_VALIDITY"),
                                ("mesh_min_volume_change", "MIN_VOLUME_CHANGE")):
            actual = float(re.search(r"\b" + constant + r"\s*=\s*([-+0-9.eE]+)", text).group(1))
            self.assertEqual(record["mesh_quality"]["thresholds"][field], actual)

    def test_real_legacy_failure_cannot_be_cited_as_success(self):
        verdict = gate.citation_status(LEGACY)
        self.assertNotEqual(gate.CITABLE, verdict["state"])
        with self.assertRaises(gate.RecordNotCitable):
            gate.assert_citable(LEGACY)

if __name__ == "__main__":
    unittest.main()
