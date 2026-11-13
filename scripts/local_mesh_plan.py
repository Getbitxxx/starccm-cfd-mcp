"""Audit-bound, SI local-mesh configuration planning; never runs STAR."""
from __future__ import annotations
import hashlib
import json
import math

ROLES = {'blade_wall', 'hub_wall', 'stationary_wall', 'inlet', 'outlet', 'farfield', 'interface'}
WALLS = {'blade_wall', 'hub_wall', 'stationary_wall'}


def positive(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f'{label} must be finite and positive in SI')
    return value


def text(value, label):
    if not isinstance(value, str) or not value.strip() or any(c in value for c in '\r\n\x00'):
        raise ValueError(f'{label} must be a nonempty single-line selector')
    return value


def prepare_local_mesh_plan(audit: dict, controls: dict) -> dict:
    if not isinstance(audit, dict) or audit.get('status') != 'ok':
        raise ValueError('A complete preflight audit is required')
    if not isinstance(controls, dict) or controls.get('schema_version') != 1:
        raise ValueError('Expected local mesh schema_version=1')
    selected = controls.get('fluid_parts')
    if (not isinstance(selected, list) or not all(isinstance(x, str) for x in selected)
            or len(selected) != 2 or len(set(selected)) != 2):
        raise ValueError('This candidate requires two distinct explicit fluid-volume parts')
    parts = {}
    for p in audit.get('geometryParts', []):
        name = text(p.get('qualifiedName') or p.get('name'), 'part')
        if name in parts:
            raise ValueError('Ambiguous part selector: '+name)
        parts[name] = p
    available = {}
    for name in selected:
        text(name, 'fluid part')
        if name not in parts:
            raise ValueError('Part absent from audit: '+name)
        p = parts[name]
        if p.get('isClosed') is not True or p.get('isManifold') is not True:
            raise ValueError('Selected fluid part must be closed and manifold: '+name)
        surfaces = p.get('partSurfaces')
        if not isinstance(surfaces, list) or not surfaces or p.get('partSurfaceCount') != len(surfaces):
            raise ValueError('Incomplete surface inventory: '+name)
        for s in surfaces:
            q = text(s.get('qualifiedName'), 'surface')
            if q in available:
                raise ValueError('Ambiguous surface selector: '+q)
            available[q] = name
    base = positive(controls.get('base_size_m'), 'base_size_m')
    layers = controls.get('prism_layers')
    if isinstance(layers, bool) or not isinstance(layers, int) or not 3 <= layers <= 20:
        raise ValueError('prism_layers must be an integer from 3 to 20')
    thickness = positive(controls.get('prism_total_m'), 'prism_total_m')
    stretch = positive(controls.get('prism_stretching'), 'prism_stretching')
    if not 1 <= stretch <= 2:
        raise ValueError('prism_stretching must be in [1,2]')
    if 'prism_mesher' in controls:
        options = controls['prism_mesher']
        required = {'minimum_thickness_pct', 'layer_chopping_pct', 'boundary_march_angle_deg'}
        if not isinstance(options, dict) or set(options) != required:
            raise ValueError('prism_mesher requires exactly three reviewed options')
        for key, value in options.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError('prism_mesher options must be finite numbers')
        if not 0 < options['minimum_thickness_pct'] <= 100:
            raise ValueError('minimum_thickness_pct must be in (0,100]')
        if not 0 <= options['layer_chopping_pct'] < 100:
            raise ValueError('layer_chopping_pct must be in [0,100)')
        if not 0 < options['boundary_march_angle_deg'] <= 90:
            raise ValueError('boundary_march_angle_deg must be in (0,90]')
    surfaces = controls.get('surfaces')
    if not isinstance(surfaces, list) or not surfaces:
        raise ValueError('Explicit surface roles and sizes are required')
    seen, roles, pairs = set(), set(), {}
    for s in surfaces:
        if not isinstance(s, dict):
            raise ValueError('Each surface control must be an object')
        q = text(s.get('selector'), 'surface selector')
        if q not in available or q in seen:
            raise ValueError('Unknown or duplicate surface selector: '+q)
        seen.add(q)
        role = s.get('role')
        if role not in ROLES:
            raise ValueError('Unknown surface role: '+str(role))
        roles.add(role)
        minimum = positive(s.get('min_size_m'), q+'.min_size_m')
        target = positive(s.get('target_size_m'), q+'.target_size_m')
        if minimum > target:
            raise ValueError('Minimum exceeds target: '+q)
        # Farfield target can exceed base, as in the official tutorial.
        if s.get('prism_enabled') is not (role in WALLS):
            raise ValueError('Prisms must be explicitly enabled only on physical walls: '+q)
        if role == 'interface':
            pair = text(s.get('pair'), 'interface pair')
            pairs.setdefault(pair, []).append(available[q])
    if seen != set(available):
        raise ValueError('Unassigned surfaces: '+', '.join(sorted(set(available)-seen)))
    if not {'blade_wall', 'inlet', 'outlet', 'farfield', 'interface'} <= roles:
        raise ValueError('Required MRF surface roles are missing')
    if any(len(v) != 2 or len(set(v)) != 2 for v in pairs.values()):
        raise ValueError('Each interface pair must have exactly two surfaces on distinct fluid parts')
    cylinder = controls.get('near_field')
    if not isinstance(cylinder, dict):
        raise ValueError('Explicit near-field cylinder is required')
    for key in ('start_m', 'end_m'):
        v = cylinder.get(key)
        if not isinstance(v, list) or len(v) != 3 or any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in v):
            raise ValueError('Near-field endpoints must contain three finite SI coordinates')
    if cylinder['start_m'] == cylinder['end_m']:
        raise ValueError('Near-field cylinder has zero length')
    positive(cylinder.get('radius_m'), 'near_field.radius_m')
    near_size = positive(cylinder.get('target_size_m'), 'near_field.target_size_m')
    if near_size >= base:
        raise ValueError('Near-field target must refine the base size')
    curves = controls.get('edge_controls', [])
    if not isinstance(curves, list):
        raise ValueError('edge_controls must be a list')
    curve_seen = set()
    for c in curves:
        if not isinstance(c, dict):
            raise ValueError('Each edge control must be an object')
        q = text(c.get('selector'), 'curve selector')
        if q in curve_seen:
            raise ValueError('Duplicate curve selector')
        curve_seen.add(q)
        if positive(c.get('min_size_m'), 'edge min') > positive(c.get('target_size_m'), 'edge target'):
            raise ValueError('Edge minimum exceeds target')
    canonical = json.dumps(controls, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return {'schema_version': 1, 'configuration_valid': True,
            'execution_allowed': False, 'engineering_verified': False,
            'controls': json.loads(canonical),
            'controls_sha256': hashlib.sha256(canonical.encode()).hexdigest(),
            'audit_sha256': hashlib.sha256(json.dumps(audit, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            'surface_count': len(seen), 'interface_pair_count': len(pairs),
            'limitations': ['Role labels are explicit engineering intent, not inferred solid/fluid proof.',
                           'Interface labels do not prove matching area or connectivity.',
                           'Curve selectors require STAR object-tree lookup; preflight does not inventory curves.',
                           'No mesh generated; candidate macro requires review and a verified isolated worker.']}


def properties_for_plan(plan: dict) -> str:
    """UTF-8 Java Properties input; names are escaped, never interpreted as code."""
    if plan.get('configuration_valid') is not True:
        raise ValueError('Only validated configurations may be exported')
    c = plan['controls']
    values = {k: c[k] for k in ('base_size_m', 'prism_layers', 'prism_total_m', 'prism_stretching')}
    values.update(schema_version=1, part_count=len(c['fluid_parts']), surface_count=len(c['surfaces']),
                  edge_count=len(c.get('edge_controls', [])), audit_sha256=plan['audit_sha256'],
                  controls_sha256=plan['controls_sha256'])
    for key, value in c.get('prism_mesher', {}).items():
        values['prism_mesher.'+key] = value
    if 'source_sha256' in plan:
        values['source_sha256'] = plan['source_sha256']
    for i, p in enumerate(c['fluid_parts']): values[f'part.{i}'] = p
    for i, s in enumerate(c['surfaces']):
        for k in ('selector', 'role', 'min_size_m', 'target_size_m'):
            values[f'surface.{i}.{k}'] = s[k]
        if s['role'] == 'interface':
            values[f'surface.{i}.pair'] = s['pair']
    for i, e in enumerate(c.get('edge_controls', [])):
        for k in ('selector', 'min_size_m', 'target_size_m'):
            values[f'edge.{i}.{k}'] = e[k]
    n = c['near_field']
    for key in ('start_m', 'end_m'):
        for i, v in enumerate(n[key]): values[f'near.{key}.{i}'] = v
    values['near.radius_m'] = n['radius_m']; values['near.target_size_m'] = n['target_size_m']
    def escape(v):
        return str(v).replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t').replace('=', '\\=').replace(':', '\\:').replace(' ', '\\ ').replace('#', '\\#').replace('!', '\\!')
    return '\n'.join(escape(k)+'='+escape(v) for k,v in sorted(values.items()))+'\n'
