"""
CleanMesh — SimReady Emit
==========================

Upgrades a vanilla USD file (just mesh + vertex colors) into a
SimReady-compliant asset that Omniverse, Twinmotion and BIM platforms can
ingest as a simulation-ready digital twin asset.

Adds the following on top of the input USD:
  - Stage metadata: defaultPrim, upAxis=Y, metersPerUnit=1.0
  - Root prim Kind = "component" + assetInfo (identifier, name, version)
  - USDPhysics: CollisionAPI (convex hull) + MeshCollisionAPI on every mesh
  - USDPhysics: MassAPI with density-based mass estimation
  - USDPhysics: Default physical material (friction / restitution)
  - Semantic label custom attribute (from dt_meta['category'])
  - Real-world scale calibration when dt_meta['dimensions_mm'] is provided

This module uses ONLY the `pxr` (USD Python) bindings — no Omniverse Kit
required. It runs inside Blender's bundled Python (Blender 5.x ships pxr)
or any environment where `usd-core` is pip-installed.

Reference: https://docs.omniverse.nvidia.com/simready/latest/
"""

from __future__ import annotations

import os
from typing import Optional


# Default physical properties — sensible fallbacks for industrial assets
_DEFAULT_DENSITY_KG_M3   = 1000.0   # water; conservative
_DEFAULT_STATIC_FRICTION  = 0.5
_DEFAULT_DYNAMIC_FRICTION = 0.5
_DEFAULT_RESTITUTION      = 0.0

# Category → density (kg/m³) lookup for better mass estimation
_DENSITY_TABLE = {
    "agv":         7800,   # steel chassis dominant
    "pallet":       700,   # wood
    "drum":        7800,   # steel barrel
    "conveyor":    7800,   # steel
    "shelf":       7800,   # steel
    "rack":        7800,
    "container":   2700,   # aluminum
    "box":          200,   # cardboard
    "machine":     7800,
    "robot":       2700,
    "default":     1000,
}


def make_simready(
    usd_path: str,
    dt_meta: Optional[dict] = None,
    bbox_world: Optional[tuple] = None,
) -> dict:
    """Upgrade a vanilla USD file in-place into a SimReady-compliant asset.

    Args:
        usd_path:   Path to .usd / .usda / .usdc file to upgrade.
        dt_meta:    Optional digital-twin metadata dict. Recognized keys:
                      - category        (str) — used for density lookup + semantic label
                      - dimensions_mm   (list[3] of float) — real-world W,D,H in mm
                      - manufacturer    (str)
                      - serial_number   (str)
                      - source_image    (str)
        bbox_world: Optional (min_xyz, max_xyz) bounding box from Blender,
                    used as a fallback when dimensions_mm is not provided.

    Returns:
        Dict summarizing what was added (for logs / RESULT line).
    """
    try:
        from pxr import Usd, UsdGeom, UsdPhysics, UsdShade, Sdf, Gf, Kind
    except ImportError as e:
        raise RuntimeError(
            "SimReady emit needs `pxr` (USD Python). "
            "Run inside Blender 5.x or `pip install usd-core`."
        ) from e

    if not os.path.isfile(usd_path):
        raise FileNotFoundError(usd_path)

    stage = Usd.Stage.Open(usd_path)
    if stage is None:
        raise RuntimeError(f"Could not open USD stage: {usd_path}")

    dt_meta = dt_meta or {}
    report = {"applied": []}

    # 1. Stage metadata --------------------------------------------------
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    report["applied"].append("stage.upAxis=Y / metersPerUnit=1.0")

    # 2. Find or designate a root prim -----------------------------------
    root_prim = stage.GetDefaultPrim()
    if not root_prim or not root_prim.IsValid():
        # First Xform child of pseudo-root, or any top-level prim
        for p in stage.GetPseudoRoot().GetChildren():
            if p.IsValid():
                root_prim = p
                break
        if root_prim and root_prim.IsValid():
            stage.SetDefaultPrim(root_prim)
            report["applied"].append(f"defaultPrim → {root_prim.GetPath()}")

    if not root_prim or not root_prim.IsValid():
        # No prims at all — write a minimal Xform
        root_prim = UsdGeom.Xform.Define(stage, "/Asset").GetPrim()
        stage.SetDefaultPrim(root_prim)
        report["applied"].append("created /Asset root Xform")

    # 3. Kind = "component" ----------------------------------------------
    model = Usd.ModelAPI(root_prim)
    model.SetKind(Kind.Tokens.component)
    report["applied"].append("kind=component")

    # 4. assetInfo -------------------------------------------------------
    asset_name = (
        dt_meta.get("category")
        or os.path.splitext(os.path.basename(usd_path))[0]
    )
    asset_info = {
        "identifier": Sdf.AssetPath(os.path.basename(usd_path)),
        "name":       asset_name,
        "version":    "1.0",
    }
    if dt_meta.get("manufacturer"):
        asset_info["manufacturer"] = dt_meta["manufacturer"]
    if dt_meta.get("serial_number"):
        asset_info["serial_number"] = dt_meta["serial_number"]
    if dt_meta.get("source_image"):
        asset_info["source_image"] = dt_meta["source_image"]
    model.SetAssetInfo(asset_info)
    report["applied"].append(f"assetInfo (name={asset_name})")

    # 5. Real-world scale calibration -----------------------------------
    dims_mm = dt_meta.get("dimensions_mm")
    if dims_mm and len(dims_mm) == 3 and bbox_world is not None:
        try:
            bb_min, bb_max = bbox_world
            current_extent = (
                bb_max[0] - bb_min[0],
                bb_max[1] - bb_min[1],
                bb_max[2] - bb_min[2],
            )
            # average scale factor (assume uniform — TRELLIS preserves proportions)
            target_extent_m = (dims_mm[0] / 1000.0,
                               dims_mm[1] / 1000.0,
                               dims_mm[2] / 1000.0)
            scales = [
                target_extent_m[i] / current_extent[i]
                for i in range(3)
                if current_extent[i] > 1e-6
            ]
            if scales:
                # Use median to be robust against axis-misalignment from TRELLIS
                scales.sort()
                scale = scales[len(scales) // 2]
                xformable = UsdGeom.Xformable(root_prim)
                # Insert a uniform scale op above existing ops
                xformable.AddScaleOp().Set(Gf.Vec3f(scale, scale, scale))
                report["applied"].append(f"scale={scale:.4f} → {dims_mm} mm")
        except Exception as e:
            report["scale_warning"] = str(e)

    # 6. Collect mesh prims ---------------------------------------------
    mesh_prims = [
        p for p in stage.Traverse()
        if p.IsA(UsdGeom.Mesh)
    ]
    report["mesh_count"] = len(mesh_prims)

    # 7. Physics: collision API on every mesh ---------------------------
    for m in mesh_prims:
        collision = UsdPhysics.CollisionAPI.Apply(m)
        mesh_collision = UsdPhysics.MeshCollisionAPI.Apply(m)
        mesh_collision.CreateApproximationAttr().Set(
            UsdPhysics.Tokens.convexHull
        )
    if mesh_prims:
        report["applied"].append(
            f"PhysicsCollider (convexHull) × {len(mesh_prims)} mesh"
        )

    # 8. Physics: mass on root ------------------------------------------
    mass_api = UsdPhysics.MassAPI.Apply(root_prim)
    # density: lookup by category
    category = (dt_meta.get("category") or "default").lower().strip()
    density = _DENSITY_TABLE.get(category, _DEFAULT_DENSITY_KG_M3)
    mass_api.CreateDensityAttr().Set(density)
    report["applied"].append(f"MassAPI density={density} kg/m³ ({category})")

    # 9. Physics: material binding --------------------------------------
    mat_path = root_prim.GetPath().AppendChild("PhysicsMaterial")
    physics_mat = UsdShade.Material.Define(stage, mat_path)
    phys_mat_api = UsdPhysics.MaterialAPI.Apply(physics_mat.GetPrim())
    phys_mat_api.CreateStaticFrictionAttr().Set(_DEFAULT_STATIC_FRICTION)
    phys_mat_api.CreateDynamicFrictionAttr().Set(_DEFAULT_DYNAMIC_FRICTION)
    phys_mat_api.CreateRestitutionAttr().Set(_DEFAULT_RESTITUTION)
    phys_mat_api.CreateDensityAttr().Set(density)

    # Bind physics material to every mesh (purpose="physics")
    for m in mesh_prims:
        binding_api = UsdShade.MaterialBindingAPI.Apply(m)
        binding_api.Bind(
            physics_mat,
            bindingStrength=UsdShade.Tokens.weakerThanDescendants,
            materialPurpose="physics",
        )
    report["applied"].append(
        f"PhysicsMaterial μs={_DEFAULT_STATIC_FRICTION} μd={_DEFAULT_DYNAMIC_FRICTION} e={_DEFAULT_RESTITUTION}"
    )

    # 10. Semantic label -------------------------------------------------
    if dt_meta.get("category"):
        # Standard custom-data approach (SimReady semantic label feature)
        root_prim.SetCustomDataByKey(
            "semanticLabel", dt_meta["category"]
        )
        root_prim.SetCustomDataByKey(
            "semantic:class", dt_meta["category"]
        )
        report["applied"].append(f"semanticLabel={dt_meta['category']}")

    # 11. Save -----------------------------------------------------------
    stage.GetRootLayer().Save()
    return report


# ---------------------------------------------------------------------------
# CLI entry point — usable standalone (`python simready_emit.py asset.usd`)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json

    p = argparse.ArgumentParser(
        description="Upgrade a USD asset to be SimReady-compliant."
    )
    p.add_argument("usd_path", help="Path to .usd/.usda/.usdc file")
    p.add_argument("--meta", default=None,
                   help="JSON string with dt_meta (category, dimensions_mm, ...)")
    args = p.parse_args()

    meta = json.loads(args.meta) if args.meta else None
    result = make_simready(args.usd_path, dt_meta=meta)
    print(json.dumps(result, indent=2, ensure_ascii=False))
