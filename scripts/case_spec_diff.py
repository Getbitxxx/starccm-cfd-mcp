"""Read-only CaseSpec comparison and mesh-recipe plausibility checks.

Why these exist. Two recurring failures in this project were never machine-checked:

* a run was reported as a single-variable experiment while the derived spec had changed
  more than one knob (a spike contract said `mesh_prism_first_layer_height_m = 2.8222e-5`
  while the spec actually executed `6.0e-5`);
* a recipe was proposed that no mesher can build sensibly (a 15.5 mm prism stack, which is
  44x the 0.355 mm trailing edge, or a total thickness equal to `layers * first_layer`).

Both are pure arithmetic on JSON. Nothing here starts STAR, opens a socket or writes a
file, and nothing here judges a quality gate: a plausible recipe is not an accepted mesh.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024
METADATA_PREFIX = 'derivation'


def _resolve(root, path):
    """Accept a project-relative or absolute path, but never read outside the project."""
    root = Path(root).resolve()
    candidate = Path(path).expanduser()
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError('CaseSpec path must stay inside the project: ' + str(path))
    if resolved.suffix != '.json':
        raise ValueError('Expected a .json CaseSpec: ' + str(path))
    if not resolved.is_file():
        raise ValueError('CaseSpec not found: ' + str(path))
    if resolved.stat().st_size > MAX_BYTES:
        raise ValueError('CaseSpec is larger than %d bytes: %s' % (MAX_BYTES, path))
    return resolved


def _load(root, path):
    resolved = _resolve(root, path)
    text = resolved.read_text(encoding='utf-8')
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError('CaseSpec is not valid JSON: %s (%s)' % (path, exc)) from exc
    if not isinstance(document, dict):
        raise ValueError('CaseSpec must be a JSON object: ' + str(path))
    return resolved, document, hashlib.sha256(text.encode()).hexdigest()


def flatten(value, prefix=''):
    """Dotted paths for every leaf; list items are indexed (`a.0.b`)."""
    leaves = {}
    if isinstance(value, dict) and value:
        for key, child in value.items():
            leaves.update(flatten(child, '%s.%s' % (prefix, key) if prefix else str(key)))
    elif isinstance(value, list) and value:
        for index, child in enumerate(value):
            leaves.update(flatten(child, '%s.%d' % (prefix, index) if prefix else str(index)))
    else:
        leaves[prefix] = value
    return leaves


def declared_overrides(document):
    """Dotted keys a document declares it changed, from derivation.overrides.

    Derived CaseSpecs in this project keep their declaration in the run `config.json`
    (`config['derivation']['overrides']`), not inside the spec itself, so this accepts
    either a spec or a config document. A spec with no declaration returns an empty list:
    that is the honest answer and is why callers should pass the config when they have it.
    """
    derivation = document.get(METADATA_PREFIX) if isinstance(document, dict) else None
    if not isinstance(derivation, dict):
        return []
    overrides = derivation.get('overrides')
    if isinstance(overrides, dict):
        keys = list(overrides)
    elif isinstance(overrides, list):
        keys = [item for item in overrides if isinstance(item, str)]
    else:
        keys = []
    return sorted({str(key) for key in keys})


def _covers(declared, path):
    return path == declared or path.startswith(declared + '.')


def diff_specs(root, baseline, candidate, declaration=None):
    """Compare two CaseSpecs and split the changes into declared and undeclared ones.

    `declaration` is an optional path to the run config that carries
    `derivation.overrides`; without it only the candidate spec's own derivation block is
    consulted, so a declared-everything claim cannot be invented by the caller.
    """
    base_path, base_doc, base_sha = _load(root, baseline)
    cand_path, cand_doc, cand_sha = _load(root, candidate)
    if declaration is None:
        declared = declared_overrides(cand_doc)
        declaration_source = None
    else:
        decl_path, decl_doc, decl_sha = _load(root, declaration)
        declared = declared_overrides(decl_doc)
        declaration_source = {'path': str(decl_path), 'sha256': decl_sha}
    base_flat, cand_flat = flatten(base_doc), flatten(cand_doc)

    changed, added, removed = [], [], []
    for path in sorted(set(base_flat) | set(cand_flat)):
        if path not in cand_flat:
            removed.append({'path': path, 'baseline': base_flat[path]})
        elif path not in base_flat:
            added.append({'path': path, 'candidate': cand_flat[path]})
        elif (base_flat[path] != cand_flat[path]
              or isinstance(base_flat[path], bool) != isinstance(cand_flat[path], bool)):
            # True == 1 in Python: a boolean replaced by 0/1 would otherwise look unchanged.
            changed.append({'path': path, 'baseline': base_flat[path], 'candidate': cand_flat[path]})

    metadata = [item['path'] for item in changed if _covers(METADATA_PREFIX, item['path'])]
    metadata += [item['path'] for item in added if _covers(METADATA_PREFIX, item['path'])]
    metadata += [item['path'] for item in removed if _covers(METADATA_PREFIX, item['path'])]
    physical = [item for item in changed if item['path'] not in metadata]
    declared_hits = [item['path'] for item in physical
                     if any(_covers(key, item['path']) for key in declared)]
    undeclared = [item['path'] for item in physical if item['path'] not in declared_hits]

    return {
        'baseline': {'path': str(base_path), 'sha256': base_sha},
        'candidate': {'path': str(cand_path), 'sha256': cand_sha},
        'changed': physical,
        'added': added,
        'removed': removed,
        'declaration': declaration_source,
        'declared_keys': declared,
        'declared': declared_hits,
        'undeclared': undeclared,
        'metadata_changed': metadata,
        'single_variable': len(physical) == 1 and not added and not removed,
        'identical': not (changed or added or removed),
        'interpretation': ('exactly one physical knob changed' if len(physical) == 1 and not added and not removed
                           else '%d physical knobs changed, %d keys added, %d keys removed'
                                % (len(physical), len(added), len(removed))),
    }


def _optional_number(source, key, errors):
    value = source.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append('non-numeric field: ' + key)
        return None
    return float(value)


def _number(source, key, errors):
    value = source.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append('missing numeric field: ' + key)
        return None
    return float(value)


def implied_growth(first_layer, layers, total):
    """Solve first*(g^n - 1)/(g - 1) = total for g > 1 (returns None when impossible)."""
    if first_layer <= 0 or layers <= 0:
        return None
    if total < layers * first_layer - 1e-15:
        return None
    if abs(total - layers * first_layer) <= 1e-15:
        return 1.0
    low, high = 1.0, 2.0
    # Expand the bracket instead of capping it: a capped bracket would silently return a
    # growth ratio that does not reproduce `total`, and this project has a history of
    # quoting exactly such "plausible looking" numbers.
    while True:
        try:
            stacked = first_layer * (high ** layers - 1) / (high - 1)
        except OverflowError:
            return None
        if stacked >= total:
            break
        high *= 2.0
        if high > 1e6:
            return None
    for _ in range(200):
        middle = (low + high) / 2
        stacked = first_layer * (middle ** layers - 1) / (middle - 1)
        if stacked < total:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def check_recipe(spec):
    """Arithmetic plausibility of one mesh recipe. Never a gate verdict."""
    quality = spec.get('quality') if isinstance(spec.get('quality'), dict) else {}
    errors = []
    layers = quality.get('mesh_prism_layers_target', spec.get('mesh_prism_layers'))
    if isinstance(layers, bool) or not isinstance(layers, int):
        errors.append('missing integer field: quality.mesh_prism_layers_target')
        layers = None
    first_layer = _number(quality, 'mesh_prism_first_layer_height_m', errors)
    total = _number(quality, 'mesh_prism_total_thickness_m', errors)
    # Local-refinement sizes are spike-only keys and may legitimately be absent.
    core = _optional_number(spec, 'mesh_nearfield_cell_size_m', errors)
    target = spec.get('mesh_body_target_size_m')
    minimum = spec.get('mesh_body_minimum_size_m')
    if isinstance(target, bool) or not isinstance(target, (int, float)):
        target = None
    if isinstance(minimum, bool) or not isinstance(minimum, (int, float)):
        minimum = None

    derived = {'implied_growth': None, 'last_layer_m': None,
               'last_layer_over_core_ratio': None, 'stack_over_layers_times_first': None}
    checks = []

    if first_layer is not None and first_layer <= 0:
        errors.append('non-positive field: quality.mesh_prism_first_layer_height_m')
        checks.append({'check': 'first_layer_positive', 'ok': False,
                       'detail': 'first layer %.6e must be positive' % first_layer})

    if layers and first_layer is not None and total is not None and first_layer > 0:
        growth = implied_growth(first_layer, layers, total)
        derived['implied_growth'] = growth
        derived['stack_over_layers_times_first'] = total / (layers * first_layer)
        if growth is None:
            checks.append({'check': 'thickness_stack_buildable', 'ok': False,
                           'detail': 'total %.6e is below layers*first_layer %.6e'
                                     % (total, layers * first_layer)})
        else:
            last = first_layer * growth ** (layers - 1)
            derived['last_layer_m'] = last
            checks.append({'check': 'thickness_stack_buildable', 'ok': True,
                           'detail': 'growth %.6f, last layer %.6e' % (growth, last)})
            checks.append({'check': 'growth_within_1_2', 'ok': 1.0 < growth <= 2.0,
                           'detail': 'growth %.6f' % growth})
            if core:
                derived['last_layer_over_core_ratio'] = last / core
                checks.append({'check': 'last_layer_not_above_core', 'ok': last <= core,
                               'detail': 'last layer %.6e vs core %.6e (ratio %.3f)'
                                         % (last, core, last / core)})
    if core and target:
        checks.append({'check': 'local_refinement_not_coarser_than_target', 'ok': core <= target,
                       'detail': 'nearfield %.6e vs body target %.6e' % (core, target)})
    if core and minimum:
        checks.append({'check': 'local_refinement_not_below_surface_minimum', 'ok': core >= minimum,
                       'detail': 'nearfield %.6e vs body minimum %.6e' % (core, minimum)})

    return {
        'recipe': {'layers': layers, 'first_layer_m': first_layer, 'total_thickness_m': total,
                   'nearfield_cell_m': core, 'body_target_m': target, 'body_minimum_m': minimum},
        'derived': derived,
        'checks': checks,
        'ok': bool(checks) and not errors and all(item['ok'] for item in checks),
        'errors': errors,
        'limit': 'arithmetic only: this is not a mesh-quality gate and never accepts a mesh',
    }


def check_recipe_file(root, path):
    _, document, sha = _load(root, path)
    return dict(check_recipe(document), source={'path': str(_resolve(root, path)), 'sha256': sha})
