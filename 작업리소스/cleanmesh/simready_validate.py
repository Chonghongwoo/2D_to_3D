"""
CleanMesh — SimReady Validate (optional stage)
================================================

Runs NVIDIA's official SimReady validator against a USD asset and
returns a structured report. This is an OPTIONAL stage — when Omniverse
Kit is not installed, the validator is skipped gracefully without
affecting pipeline outcome.

Two validation backends are tried, in priority order:

1) ``aif-pipeline validate``  (NVIDIA-Omniverse/aif-pipeline-samples)
   — requires Omniverse Kit installed + ``aif-pipeline`` CLI on PATH.
2) Custom pxr-only sanity checks (always available; covers the basics
   that ``simready_emit.py`` was supposed to add).

The custom backend acts as a self-check: if pipeline produced an asset
where SimReady emit was requested but somehow missing, the report calls
that out.

This module is NOT invoked from inside Blender — it runs in the regular
CleanMesh Python process. ``pxr`` is reached either via Blender's
bundled python (when scripts are launched from there) or via a separate
``pip install usd-core`` install. If neither is available, only the
``aif-pipeline`` path is attempted.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import json
from typing import Optional


# ---------------------------------------------------------------------------
# Backend 1 — NVIDIA aif-pipeline (Omniverse Kit required)
# ---------------------------------------------------------------------------

def _aif_pipeline_available() -> bool:
    """Check if ``aif-pipeline`` CLI is on PATH."""
    return shutil.which("aif-pipeline") is not None


def _run_aif_validate(usd_path: str, out_dir: str) -> dict:
    """Invoke ``aif-pipeline validate``. Requires Omniverse Kit."""
    os.makedirs(out_dir, exist_ok=True)
    cmd = [
        "aif-pipeline", "validate",
        usd_path, out_dir,
        "--stage", "post",
        "--predicate", "Any",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
            encoding="utf-8", errors="replace",
        )
        return {
            "backend": "aif-pipeline",
            "returncode": proc.returncode,
            "stdout_tail": (proc.stdout or "")[-2000:],
            "stderr_tail": (proc.stderr or "")[-1000:],
            "report_dir": out_dir,
            "passed": proc.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"backend": "aif-pipeline", "passed": False,
                "error": "timeout (600s)"}
    except FileNotFoundError:
        return {"backend": "aif-pipeline", "passed": False,
                "error": "aif-pipeline not on PATH"}


# ---------------------------------------------------------------------------
# Backend 2 — pxr-only sanity check (no Omniverse Kit needed)
# ---------------------------------------------------------------------------

def _pxr_available() -> bool:
    try:
        import pxr  # noqa: F401
        return True
    except ImportError:
        return False


def _run_pxr_check(usd_path: str) -> dict:
    """Self-check that simready_emit's outputs are actually present.

    Verifies, on a best-effort basis, that the asset has:
      - default prim defined
      - upAxis = Y
      - metersPerUnit = 1.0
      - root prim kind = component
      - at least one mesh with PhysicsCollisionAPI
      - MassAPI on the root prim
      - PhysicsMaterial bound somewhere
      - semanticLabel custom data
    """
    from pxr import Usd, UsdGeom, UsdPhysics, UsdShade, Sdf, Kind

    issues = []
    passed_features = []

    stage = Usd.Stage.Open(usd_path)
    if stage is None:
        return {"backend": "pxr-check", "passed": False,
                "error": f"could not open stage: {usd_path}"}

    # 1) defaultPrim
    root = stage.GetDefaultPrim()
    if root and root.IsValid():
        passed_features.append("defaultPrim")
    else:
        issues.append("missing: defaultPrim")

    # 2) upAxis = Y
    up_axis = UsdGeom.GetStageUpAxis(stage)
    if str(up_axis) == "Y":
        passed_features.append("upAxis=Y")
    else:
        issues.append(f"upAxis={up_axis} (expected Y)")

    # 3) metersPerUnit
    mpu = UsdGeom.GetStageMetersPerUnit(stage)
    if abs(mpu - 1.0) < 1e-6:
        passed_features.append("metersPerUnit=1.0")
    else:
        issues.append(f"metersPerUnit={mpu} (expected 1.0)")

    # 4) Kind = component on root
    if root and root.IsValid():
        kind = Usd.ModelAPI(root).GetKind()
        if str(kind) == "component":
            passed_features.append("kind=component")
        else:
            issues.append(f"root kind={kind} (expected component)")

    # 5) mesh + collision
    mesh_prims = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
    if not mesh_prims:
        issues.append("no Mesh prims found")
    else:
        passed_features.append(f"{len(mesh_prims)} Mesh")
        with_collision = [
            m for m in mesh_prims
            if m.HasAPI(UsdPhysics.CollisionAPI)
        ]
        if with_collision:
            passed_features.append(
                f"PhysicsCollisionAPI on {len(with_collision)}/{len(mesh_prims)} meshes"
            )
        else:
            issues.append("no Mesh has PhysicsCollisionAPI")

    # 6) MassAPI on root
    if root and root.HasAPI(UsdPhysics.MassAPI):
        passed_features.append("MassAPI on root")
    else:
        issues.append("missing: MassAPI on root")

    # 7) Physics material binding (any mesh)
    materials_seen = False
    for m in mesh_prims:
        binding = UsdShade.MaterialBindingAPI(m)
        if binding.GetDirectBinding(materialPurpose="physics").GetMaterial():
            materials_seen = True
            break
    if materials_seen:
        passed_features.append("PhysicsMaterial bound")
    else:
        issues.append("missing: PhysicsMaterial binding")

    # 8) semanticLabel custom data
    if root and root.GetCustomDataByKey("semanticLabel"):
        passed_features.append("semanticLabel")
    else:
        issues.append("missing: semanticLabel customData")

    passed = len(issues) == 0
    return {
        "backend": "pxr-check",
        "passed": passed,
        "passed_features": passed_features,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Public entry — used by pipeline.py
# ---------------------------------------------------------------------------

def validate_simready(usd_path: str, out_dir: Optional[str] = None) -> dict:
    """Validate a USD asset as SimReady. Returns a report dict.

    Tries aif-pipeline (official, comprehensive) first, falls back to
    pxr-only sanity check. If neither backend is available, returns a
    "skipped" report — the pipeline does NOT fail.
    """
    if not os.path.isfile(usd_path):
        return {"status": "skipped",
                "reason": f"file not found: {usd_path}"}

    if not usd_path.lower().endswith((".usd", ".usda", ".usdc")):
        return {"status": "skipped",
                "reason": f"not a USD file: {usd_path}"}

    out_dir = out_dir or os.path.dirname(usd_path)

    # Try official validator first
    if _aif_pipeline_available():
        try:
            r = _run_aif_validate(usd_path, out_dir)
            return {"status": "ran", **r}
        except Exception as e:
            return {"status": "failed", "backend": "aif-pipeline",
                    "error": str(e)}

    # Fallback to pxr-only
    if _pxr_available():
        try:
            r = _run_pxr_check(usd_path)
            return {"status": "ran", **r}
        except Exception as e:
            return {"status": "failed", "backend": "pxr-check",
                    "error": str(e)}

    return {
        "status": "skipped",
        "reason": (
            "no validator available — install Omniverse Kit + aif-pipeline "
            "OR `pip install usd-core` to enable validation."
        ),
    }


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(
        description="Validate a USD asset against SimReady requirements."
    )
    p.add_argument("usd_path")
    p.add_argument("--out-dir", default=None)
    args = p.parse_args()

    result = validate_simready(args.usd_path, args.out_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False))
