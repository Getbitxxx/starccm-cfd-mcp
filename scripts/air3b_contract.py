"""AIR-3B CaseSpec drafts and acceptance contract; does not authorize execution."""
import copy
import hashlib
import json
import math


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def make_specs(geometry):
    if geometry.get('valid') is not True or geometry.get('solid_count')!=1:
        raise ValueError('Valid single-solid build evidence required')
    if geometry['parameters']['te_mm']!=0.8:raise ValueError('AIR-3B requires approved 0.80 mm trailing edge')
    bounds=geometry['bounds_mm']
    if len(bounds)!=6 or not all(math.isfinite(x) for x in bounds):raise ValueError('Invalid bounds')
    # STEP coordinates are mm; imported STL must explicitly use mm as well.
    D=0.410;rho=101325/(287.05*300);n=10000/60
    base=dict(schema_version=1,subplan_id='AIR-3B',case_ref=None,
              objective='AIR-3B full-circle free-air propeller acceptance',model_length_unit='mm',medium='air',
              geometry=dict(body_parts=['AIR3B_ROTOR'],rotor_parts=['AIR3B_ROTOR'],
                            step_sha256=geometry['step_sha256'],stl_sha256=geometry['stl_sha256'],
                            role_verified=False,surface_topology_volume_passed=False,diameter_m=D,te_m=0.0008),
              domain=dict(shape='cylinder',outer_bounds_m=[-2.05,-2.05,-6.15,2.05,2.05,2.05],
                          cylinder_center_xy_m=[0,0],cylinder_radius_m=2.05,cylinder_z_min_m=-6.15,cylinder_z_max_m=2.05),
              rotation_zone=dict(shape='cylinder',center_xy_m=[0,0],radius_m=0.75*D,
                                 z_min_m=bounds[2]*0.001-0.25*D,z_max_m=bounds[5]*0.001+0.25*D),
              flow=dict(velocity_m_s=[0,0,-30],temperature_k=300,pressure_pa=101325,
                        turbulence_intensity=0.05,turbulent_viscosity_ratio=10),
              rotation=dict(axis=[0,0,1],center_m=[0,0,0],rpm=10000),
              physics=dict(material='ideal_gas_air',turbulence='SST',energy=True,compressible=True),
              limits=dict(logical_cores=64,memory_gb=256,wall_hours=48),
              quality=dict(mesh_levels=3,domain_sensitivity=True,time_step_sensitivity=True,
                           force_window_rel_change=0.005,sensitivity_rel_change=0.02,
                           mesh_min_cell_quality=1e-5,mesh_min_face_validity=1.0,mesh_min_volume_change=0.01),
              acceptance=dict(residuals=dict(continuity=1e-4,momentum=2.5e-4,energy=1e-3,turbulence=1e-3),
                              steady_window_iterations=200,load_range_fraction=0.005,mass_imbalance_fraction=0.001,
                              yplus_max_for_coverage=5,yplus_required_area_fraction=0.95,zero_prism_area_fraction=0,
                              sensitivity_fraction=0.02,gci_fine_fraction=0.02,
                              relative_denominator_floor_fraction=1e-6,
                              normalization_scales=dict(force_n=rho*n*n*D**4,torque_nm=rho*n*n*D**5,efficiency=1),
                              force_positive_direction=[0,0,-1],torque_axis=[0,0,1],shaft_power_definition='-Q*Omega'),
              solver=dict(pilot_iterations=100,segment_iterations=100,max_iterations=10000,
                          sampling_interval_iterations=1,normalization='STAR residual monitor normalization; export settings and denominators'),
              status=dict(case_spec_frozen=False,production_executable=False,engineering_verified=False),
              assumptions=['0.80 mm trailing edge changes airfoil; workflow sample without experimental validation',
                           'Outer and rotating domain dimensions provisional until sensitivity validation'])
    mrf=copy.deepcopy(base);mrf['scenario']='propeller_mrf'
    transient=copy.deepcopy(base);transient['scenario']='rotating_transient'
    transient['physics']['time_model']='URANS'
    transient['transient']=dict(time_step_s=60/(10000*360),physical_duration_s=40*60/10000,
                                steps_per_revolution=360,stability_window_revolutions=5,
                                minimum_revolutions=10,maximum_revolutions=40,max_inner_iterations=50,
                                time_order=2,step_angles_deg=[2,1,0.5],target_quantities=['thrust','torque','power','KT','KQ','eta','load_rms'])
    return {'mrf':mrf,'transient':transient}


def execution_blockers(spec):
    blockers=[]
    if not spec.get('case_ref'):blockers.append('Immutable geometry .sim not registered')
    if not spec.get('geometry',{}).get('role_verified'):blockers.append('STAR geometry-role audit missing')
    if not spec.get('geometry',{}).get('surface_topology_volume_passed'):blockers.append('STEP/STL consistency gate not passed')
    if not spec.get('status',{}).get('case_spec_frozen'):blockers.append('CaseSpec not frozen against successful STAR audit')
    if not spec.get('status',{}).get('production_executable'):blockers.append('Required generic operations not released/validated')
    return blockers
