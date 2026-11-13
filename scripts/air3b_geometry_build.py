"""Rebuild the approved AIR-3B solid with the historical parametrized generator.
Runs in the existing Windows CAD Python environment; no STAR invocation.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--generator',type=Path,required=True)
    ap.add_argument('--generator-sha256',required=True)
    ap.add_argument('--output-dir',type=Path,required=True)
    a=ap.parse_args()
    if sha(a.generator)!=a.generator_sha256:raise ValueError('Generator hash mismatch')
    if a.output_dir.exists():raise ValueError('Output directory already exists; preserve prior evidence')
    a.output_dir.mkdir(parents=True)
    step=a.output_dir/'air3b_rotor_te080.step'
    sys.argv=[str(a.generator),str(step),'--alpha','6.0','--induction','0.16','--te','0.80']
    # Historical generator main exports STEP and exits; import instead to retain control.
    spec=importlib.util.spec_from_file_location('air3b_generator',a.generator)
    g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
    g.main()
    import build123d as bd
    rotor=bd.import_step(str(step));bounds=rotor.bounding_box()
    if len(rotor.solids())!=1 or not rotor.is_valid or rotor.volume<=0:raise ValueError('Invalid imported STEP')
    rotor.label='AIR3B_ROTOR'
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    adaptive=GProp_GProps();error=BRepGProp.VolumeProperties_s(rotor.wrapped,adaptive,1e-7)
    independent=GProp_GProps();gk_error=BRepGProp.VolumePropertiesGK_s(rotor.wrapped,independent,1e-7)
    volume=adaptive.Mass()
    if error<0 or gk_error<0 or volume<=0 or abs(volume-independent.Mass())/volume>1e-6:
        raise ValueError('Adaptive CAD volume consistency failed')
    stl=a.output_dir/'air3b_rotor_te080_mm.stl'
    if not bd.export_stl(rotor,str(stl),tolerance=0.0002,angular_tolerance=0.03):raise ValueError('STL export failed')
    sections=[g.section_wire(st,6.0,0.16,0.80)[1] for st in g.STATIONS]
    qa=dict(subplan_id='AIR-3B',length_unit='mm',geometry_role='rotor_solid',
            source_generator_sha256=a.generator_sha256,wrapper_sha256=sha(__file__),
            parameters=dict(te_mm=0.8,alpha_deg=6.0,induction=0.16,diameter_design_mm=410),
            build123d_version=bd.__version__,solid_count=len(rotor.solids()),valid=rotor.is_valid,
            volume_mm3=volume,default_volume_mm3=rotor.volume,volume_method="adaptive_Gauss_eps1e-7",
            volume_estimated_error=error,gk_volume_mm3=independent.Mass(),gk_estimated_error=gk_error,area_mm2=rotor.area,
            bounds_mm=[bounds.min.X,bounds.min.Y,bounds.min.Z,bounds.max.X,bounds.max.Y,bounds.max.Z],
            sections=sections,step_sha256=sha(step),stl_sha256=sha(stl),stl_tolerance_mm=0.0002,
            geometry_verified_in_star=False,engineering_verified=False,
            warning='Thick truncated trailing edge changes section; analytical angle is not CFD stall proof.')
    (a.output_dir/'geometry-build.json').write_text(json.dumps(qa,indent=2),encoding='utf-8')
    print('AIR3B_GEOMETRY_BUILD_COMPLETE')

if __name__=='__main__':main()
