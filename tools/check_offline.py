"""Portable regression entry point; never connects to a worker."""
from pathlib import Path
import sys
import os
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'scripts'))
MODULES = (
    'test_stage_plan.PlanStage',
    'test_stage_plan.InheritedStageInputs',
    'test_mesh_quality.MeshQualityTests.test_pass_and_volume_change_at_boundary_fails',
    'test_mesh_quality.MeshQualityTests.test_missing_region_or_metric_cannot_pass',
    'test_mesh_quality.MeshQualityTests.test_invalid_quality_threshold_is_rejected_by_plan',
    'test_mesh_quality.MeshQualityTests.test_near_wall_gate_accepts_complete_explicit_evidence',
    'test_mesh_quality.MeshQualityTests.test_near_wall_gate_rejects_wrong_input_and_targets',
    'test_resource_contract',
    'test_runtime_manifest', 'test_gci.GciMathRatchet',
    'test_gci.GciCliWiringRatchet',
)

if __name__ == '__main__':
    # A placeholder config supports constructor-only tests; no real SSH target is used.
    with tempfile.TemporaryDirectory() as folder:
        config = Path(folder) / 'connection.json'
        config.write_text((ROOT / 'connection.example.json').read_text())
        os.environ['STARCCM_CONFIG'] = str(config)
        suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(not result.wasSuccessful())
