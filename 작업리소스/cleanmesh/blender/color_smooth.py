"""
color_smooth.py — Laplacian-smooth vertex colors in place.

Usage (Blender headless):
    blender --background --python color_smooth.py -- \
        --input  /path/to/mesh.glb \
        --output /path/to/smoothed.glb \
        --iters 5 [--alpha 0.7]

What it does
------------
Reads the per-vertex color attribute, averages each vertex's color with its
mesh-edge neighbors (Laplacian smoothing) `iters` times, writes the result
back to the same attribute, and re-exports the GLB. Alpha channel is
preserved untouched.

This is the counterpart of color_split.py for the **vertex-color** mode —
no K-means, no quantization, no material change. Just removes the per-vertex
KNN noise that TRELLIS Gaussian → mesh transfer introduces.

Prints `RESULT:{json}` on the last line for the host parser.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

import numpy as np

try:
    import bpy
except Exception:
    bpy = None  # type: ignore


# ---------------------------------------------------------------------------
def _clear_scene() -> None:
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for m in list(bpy.data.meshes):
        bpy.data.meshes.remove(m, do_unlink=True)
    for m in list(bpy.data.materials):
        bpy.data.materials.remove(m, do_unlink=True)


def _import_glb(path: str):
    bpy.ops.import_scene.gltf(filepath=path)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise RuntimeError(f"no mesh in {path}")
    bpy.ops.object.select_all(action="DESELECT")
    for m in meshes:
        m.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    if obj is None or obj.type != "MESH" or obj.data is None:
        obj = meshes[0]
    return obj


def _read_vertex_colors(obj):
    """Return (rgba[n,4] float32 in [0,1], attribute_handle).
    Returns (None, None) if no color attribute present.
    """
    mesh = obj.data
    if not mesh.color_attributes:
        return None, None
    attr = mesh.color_attributes[0]
    n_verts = len(mesh.vertices)
    if attr.domain == "POINT":
        arr = np.zeros(n_verts * 4, dtype=np.float32)
        attr.data.foreach_get("color", arr)
        return arr.reshape(n_verts, 4), attr
    # Corner domain → average to per-vertex
    sums = np.zeros((n_verts, 4), dtype=np.float64)
    counts = np.zeros(n_verts, dtype=np.int32)
    for loop in mesh.loops:
        v = loop.vertex_index
        c = attr.data[loop.index].color
        sums[v] += [c[0], c[1], c[2], c[3]]
        counts[v] += 1
    counts = np.maximum(counts, 1)
    return (sums / counts[:, None]).astype(np.float32), attr


def _write_vertex_colors(obj, attr, colors_rgba) -> None:
    """Write per-vertex RGBA back to the same color attribute layer."""
    mesh = obj.data
    if attr.domain == "POINT":
        attr.data.foreach_set("color", colors_rgba.ravel())
    else:
        # Corner domain: replicate per-vertex value to each loop
        for loop in mesh.loops:
            v = loop.vertex_index
            attr.data[loop.index].color = (
                float(colors_rgba[v, 0]),
                float(colors_rgba[v, 1]),
                float(colors_rgba[v, 2]),
                float(colors_rgba[v, 3]),
            )
    mesh.update()


def _build_edges(obj):
    """Return edge endpoint int32 arrays (E0, E1)."""
    mesh = obj.data
    n_edges = len(mesh.edges)
    buf = np.zeros(n_edges * 2, dtype=np.int32)
    mesh.edges.foreach_get("vertices", buf)
    return buf[0::2], buf[1::2]


def _smooth_rgb(rgb, e0, e1, iters, alpha):
    """Laplacian smoothing — vectorized via numpy.add.at."""
    if iters <= 0:
        return rgb
    n = rgb.shape[0]
    cur = rgb.astype(np.float64).copy()
    deg = np.zeros(n, dtype=np.int32)
    np.add.at(deg, e0, 1)
    np.add.at(deg, e1, 1)
    deg_safe = np.maximum(deg, 1)[:, None]
    for _ in range(iters):
        nbr_sum = np.zeros_like(cur)
        np.add.at(nbr_sum, e0, cur[e1])
        np.add.at(nbr_sum, e1, cur[e0])
        nbr_mean = nbr_sum / deg_safe
        cur = alpha * nbr_mean + (1.0 - alpha) * cur
    return np.clip(cur, 0.0, 1.0).astype(np.float32)


def _export(path: str) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for o in bpy.data.objects:
        if o.type == "MESH":
            o.select_set(True)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=path,
        export_format="GLB",
        use_selection=True,
        export_materials="EXPORT",
        export_apply=False,
    )


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",  required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--iters", type=int, default=5,
                    help="Laplacian smoothing iterations (default 5, 0 disables)")
    ap.add_argument("--alpha", type=float, default=0.7,
                    help="smoothing strength per iter; 1.0 = pure neighbor mean")
    args, _ = ap.parse_known_args(argv)

    if bpy is None:
        print("ERROR: must run inside Blender (bpy unavailable)", file=sys.stderr)
        return 2

    try:
        _clear_scene()
        obj = _import_glb(args.input)

        rgba, attr = _read_vertex_colors(obj)
        if rgba is None:
            print("RESULT:" + json.dumps({
                "status": "skipped",
                "reason": "input mesh has no vertex color layer",
                "output_path": args.input,
            }))
            return 0

        e0, e1 = _build_edges(obj)

        # Smooth only RGB; preserve alpha
        rgb_smooth = _smooth_rgb(
            rgba[:, :3], e0, e1,
            iters=max(0, int(args.iters)),
            alpha=float(args.alpha),
        )
        new_rgba = rgba.copy()
        new_rgba[:, :3] = rgb_smooth

        _write_vertex_colors(obj, attr, new_rgba)
        _export(args.output)

        size = os.path.getsize(args.output)
        print("RESULT:" + json.dumps({
            "status": "ok",
            "output_path": args.output,
            "iters": int(args.iters),
            "alpha": float(args.alpha),
            "vertex_count": int(rgba.shape[0]),
            "file_size_kb": size // 1024,
        }))
        return 0

    except Exception as exc:
        traceback.print_exc()
        print("RESULT:" + json.dumps({"status": "error", "message": str(exc)}))
        return 1


if __name__ == "__main__":
    if "--" in sys.argv:
        argv = sys.argv[sys.argv.index("--") + 1:]
    else:
        argv = sys.argv[1:]
    sys.exit(main(argv))
