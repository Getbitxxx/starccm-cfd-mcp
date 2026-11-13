"""Read-only geometry role screening from STAR preflight evidence.

Names and bounding-box size never establish solid/fluid identity. A closed
surface can enclose either kind of volume. Solid roles therefore remain
explicit CaseSpec intent; boundary ownership can contradict that intent.
"""
from __future__ import annotations

import hashlib
import json
import math

UNIT_SCALE = {"m": 1.0, "mm": 0.001, "cm": 0.01, "in": 0.0254}


def classify_parts(audit: dict, geometry: dict | None = None,
                   model_length_unit: str | None = None) -> dict:
    if not isinstance(audit, dict) or audit.get("status") not in {"ok", "partial"}:
        raise ValueError("Classification requires a completed preflight audit")
    parts, regions = audit.get("geometryParts"), audit.get("regions", [])
    if (not isinstance(parts, list) or not all(isinstance(p, dict) for p in parts)
            or not isinstance(regions, list) or not all(isinstance(r, dict) for r in regions)):
        raise ValueError("Malformed geometryParts or regions")
    geometry = {} if geometry is None else geometry
    if not isinstance(geometry, dict):
        raise ValueError("geometry must be an object")
    if model_length_unit is not None and (not isinstance(model_length_unit, str)
                                          or model_length_unit not in UNIT_SCALE):
        raise ValueError("Unsupported model_length_unit")
    requested = {}
    for role in ("body_parts", "rotor_parts"):
        selectors = geometry.get(role, [])
        if not isinstance(selectors, list) or not all(isinstance(s, str) and s for s in selectors):
            raise ValueError(f"geometry.{role} must contain nonempty names")
        for name in selectors:
            requested.setdefault(name, []).append(role)
    names = [p.get("qualifiedName") or p.get("name") for p in parts]
    if not all(isinstance(n, str) and n for n in names):
        raise ValueError("Every part requires a nonempty qualifiedName or name")
    errors = [f"Ambiguous audited geometry selector: {n}" for n in sorted(set(names))
              if names.count(n) > 1]
    errors += [f"Geometry selector absent from audit: {n}" for n in sorted(set(requested) - set(names))]
    evidence = {n: [] for n in names}
    for region in regions:
        boundaries = region.get("boundaries", [])
        if not isinstance(boundaries, list) or not all(isinstance(b, dict) for b in boundaries):
            raise ValueError("Malformed region boundaries")
        physical = []
        has_interface = False
        region_type = region.get("regionTypeClass")
        flow_region = region_type is None or region_type == "star.common.FluidRegion"
        for boundary in boundaries:
            kind = boundary.get("boundaryTypeClassSimpleName") or ""
            if not isinstance(kind, str):
                raise ValueError("Malformed boundary type")
            owners = boundary.get("partSurfaceOwners", [])
            if not isinstance(owners, list) or not all(isinstance(o, str) for o in owners):
                raise ValueError("Malformed boundary partSurfaceOwners")
            has_interface |= kind == "InternalBoundary"
            if kind != "InternalBoundary":
                physical.append(set(owners))
            if "Inlet" in kind or "Outlet" in kind or kind == "PressureBoundary":
                for name in set(owners) & set(names):
                    evidence[name].append({"reason": "owns_open_flow_boundary",
                                           "region": region.get("name"),
                                           "boundary": boundary.get("name"), "type": kind})
        # A single owner of all physical boundaries of an interfaced flow
        # Region identifies the source fluid volume, including old sectors
        # whose inlet/outlet are only on a neighboring stationary Region.
        if (flow_region and has_interface and region.get("physicsContinuum") and len(physical) >= 2
                and all(len(owners) == 1 for owners in physical)
                and len(set.union(*physical)) == 1):
            name = next(iter(physical[0]))
            if name in evidence:
                evidence[name].append({"reason": "supplies_interfaced_fluid_region",
                                       "region": region.get("name")})
    classified = []
    for part, name in zip(parts, names):
        bounds = part.get("boundingBoxRaw")
        bounds_ok = (isinstance(bounds, list) and len(bounds) == 6
                     and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                             and math.isfinite(x) for x in bounds)
                     and all(bounds[i] < bounds[i + 3] for i in range(3)))
        topology_ok = part.get("isClosed") is True and part.get("isManifold") is True
        fluid = bool(evidence[name])
        intent = requested.get(name, [])
        role = "fluid_domain_candidate" if fluid else "unresolved"
        if not fluid and topology_ok and bounds_ok and model_length_unit and intent:
            role = "declared_solid_candidate"
        if fluid and intent:
            errors.append(f"Solid geometry intent conflicts with fluid boundary ownership: {name}")
        classified.append({"selector": name, "role": role, "requested_roles": intent,
                           "evidence": evidence[name], "topology_complete": topology_ok,
                           "bounds_m": [x * UNIT_SCALE[model_length_unit] for x in bounds]
                           if bounds_ok and model_length_unit else None,
                           "solid_identity_verified": False})
    return {"schema_version": 1, "parts": classified, "errors": errors,
            "intent_consistent": not errors, "engineering_verified": False,
            "audit_digest": hashlib.sha256(json.dumps(audit, sort_keys=True,
                                                       ensure_ascii=False).encode()).hexdigest(),
            "scope": "Read-only role screening; no geometry mutation, overlap/gap or solid identity verification"}
