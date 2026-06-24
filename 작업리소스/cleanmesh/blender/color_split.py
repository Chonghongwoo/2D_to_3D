"""
color_split.py — split a vertex-colored mesh into K solid-color regions.

Usage (Blender headless):
    blender --background --python color_split.py -- \
        --input  /path/to/mesh.glb \
        --output /path/to/split.glb \
        --k 4 \
        [--smooth-iters 5] [--label-smooth-iters 3] [--min-region-size 100] \
        [--seed 1] [--colorspace srgb|lab]

Pipeline
--------
1. Import GLB.
2. Read per-vertex colors.
3. **Vertex-color Laplacian smoothing** (--smooth-iters) — averages each vertex
   color with its mesh neighbors. Removes the per-vertex noise that comes from
   TRELLIS Gaussian→KNN transfer, so K-means sees clean regions.
4. K-means cluster (k=2..8) in LAB space.
5. **Label majority filter** (--label-smooth-iters) — each vertex's label is
   replaced with the majority label of its 1-ring neighbors. Removes salt-and-
   pepper noise that survives clustering.
6. Face label = majority vote of its 3 vertices' labels.
7. **Small-region removal** (--min-region-size) — connected face components
   smaller than the threshold are absorbed into the touching larger region.
8. K materials created, one per cluster — Base Color = cluster center.
9. Each face's `material_index` set to its (possibly merged) label.
10. Existing vertex-color Base Color wiring is removed.
11. Export single GLB. glTF exporter splits into K primitives by material.

Prints `RESULT:{json}` on the last line for the host parser.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

import numpy as np  # ships with Blender

# Blender modules are only available when run inside Blender
try:
    import bpy
except Exception:
    bpy = None  # type: ignore


# ---------------------------------------------------------------------------
# K-means (numpy only — Blender's bundled Python has no sklearn)
# ---------------------------------------------------------------------------

def _kmeans(data: np.ndarray, k: int, *, iters: int = 40, seed: int = 0,
            tol: float = 1e-4):
    """Standard Lloyd k-means on (N, D) data. Returns (labels[N], centers[k, D])."""
    rng = np.random.RandomState(seed)
    n = data.shape[0]
    if n == 0:
        raise ValueError("no data points")
    k = max(1, min(k, n))

    # K-means++ seed
    centers = np.empty((k, data.shape[1]), dtype=np.float64)
    centers[0] = data[rng.randint(n)]
    closest = ((data - centers[0]) ** 2).sum(axis=1)
    for j in range(1, k):
        # weighted sample by squared-distance
        probs = closest / max(closest.sum(), 1e-12)
        idx = rng.choice(n, p=probs)
        centers[j] = data[idx]
        d2new = ((data - centers[j]) ** 2).sum(axis=1)
        closest = np.minimum(closest, d2new)

    labels = np.zeros(n, dtype=np.int32)
    for _ in range(iters):
        # assign
        # (n, k) distance matrix — chunk if memory tight, but n is usually < 300k
        d2 = ((data[:, None, :] - centers[None, :, :]) ** 2).sum(-1)
        new_labels = d2.argmin(axis=1).astype(np.int32)
        # update
        new_centers = centers.copy()
        for j in range(k):
            mask = new_labels == j
            if mask.any():
                new_centers[j] = data[mask].mean(axis=0)
            # else: keep old center (dead cluster — rare with k-means++)
        # converged?
        shift = np.linalg.norm(new_centers - centers, axis=1).max()
        labels = new_labels
        centers = new_centers
        if shift < tol:
            break

    return labels, centers


# ---------------------------------------------------------------------------
# sRGB ↔ Linear ↔ LAB (D65) — for perceptual color clustering
# ---------------------------------------------------------------------------

def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    a = 0.055
    return np.where(c <= 0.04045, c / 12.92, ((c + a) / (1 + a)) ** 2.4)


def _linear_to_lab(rgb_lin: np.ndarray) -> np.ndarray:
    # linear sRGB → XYZ (D65)
    M = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = rgb_lin @ M.T
    # XYZ → LAB
    ref = np.array([0.95047, 1.0, 1.08883])  # D65 white
    xyz = xyz / ref
    eps = 216 / 24389
    kap = 24389 / 27
    f = np.where(xyz > eps, np.cbrt(xyz), (kap * xyz + 16) / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


# ---------------------------------------------------------------------------
# Blender helpers
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

    # Always set active explicitly (glTF importer doesn't reliably do it)
    bpy.ops.object.select_all(action="DESELECT")
    for m in meshes:
        m.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]

    # If multiple meshes, join them into one
    if len(meshes) > 1:
        bpy.ops.object.join()

    obj = bpy.context.view_layer.objects.active
    if obj is None or obj.type != "MESH":
        obj = meshes[0]
    if obj.data is None:
        raise RuntimeError(f"imported object has no mesh data: {obj.name}")
    return obj


def _read_vertex_colors(obj) -> np.ndarray | None:
    """Return (V, 3) float in [0, 1] sRGB, or None if no color layer present."""
    mesh = obj.data
    if not mesh.color_attributes:
        return None
    # Pick first attribute (typically named 'Col' or 'Color')
    attr = mesh.color_attributes[0]
    if attr.domain != "POINT":
        # Convert corner colors → point average if needed
        # Simpler: return None and let caller use placeholder gray
        n_verts = len(mesh.vertices)
        sums = np.zeros((n_verts, 3), dtype=np.float64)
        counts = np.zeros(n_verts, dtype=np.int32)
        for loop in mesh.loops:
            v = loop.vertex_index
            c = attr.data[loop.index].color
            sums[v] += [c[0], c[1], c[2]]
            counts[v] += 1
        counts = np.maximum(counts, 1)
        return (sums / counts[:, None]).astype(np.float32)
    # POINT domain → direct read
    n_verts = len(mesh.vertices)
    arr = np.zeros((n_verts, 4), dtype=np.float32)
    attr.data.foreach_get("color", arr.ravel())
    return arr[:, :3]


def _build_edge_arrays(obj):
    """Return undirected edge endpoint arrays (E0, E1) and per-vertex neighbor degree."""
    mesh = obj.data
    n_edges = len(mesh.edges)
    e0 = np.zeros(n_edges, dtype=np.int32)
    e1 = np.zeros(n_edges, dtype=np.int32)
    buf = np.zeros(n_edges * 2, dtype=np.int32)
    mesh.edges.foreach_get("vertices", buf)
    e0 = buf[0::2]
    e1 = buf[1::2]
    return e0, e1


def _smooth_vertex_colors(rgb: np.ndarray, e0: np.ndarray, e1: np.ndarray,
                          iters: int, alpha: float = 0.7) -> np.ndarray:
    """Laplacian color smoothing along mesh edges.

    new[v] = alpha * neighbor_mean[v] + (1 - alpha) * old[v]

    Vectorized via numpy.add.at (no scipy needed).
    """
    if iters <= 0:
        return rgb
    n = rgb.shape[0]
    cur = rgb.astype(np.float64).copy()

    # Pre-compute degree once
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
    return cur.astype(np.float32)


def _smooth_labels(labels: np.ndarray, e0: np.ndarray, e1: np.ndarray,
                   k: int, iters: int) -> np.ndarray:
    """Majority-vote label smoothing along mesh edges.

    For each vertex, new_label = argmax(count of k labels among neighbors).
    Repeat `iters` times.
    """
    if iters <= 0 or k <= 1:
        return labels
    n = labels.shape[0]
    cur = labels.astype(np.int32).copy()

    for _ in range(iters):
        # one-hot counts: counts[v, c] = #(neighbors of v with label c) + 1 (self)
        counts = np.zeros((n, k), dtype=np.int32)
        counts[np.arange(n), cur] += 1  # include self vote
        np.add.at(counts, (e0, cur[e1]), 1)
        np.add.at(counts, (e1, cur[e0]), 1)
        new = counts.argmax(axis=1).astype(np.int32)
        if np.array_equal(new, cur):
            break
        cur = new
    return cur


def _face_majority_labels(obj, vertex_labels: np.ndarray, k: int) -> np.ndarray:
    mesh = obj.data
    n_polys = len(mesh.polygons)
    face_labels = np.zeros(n_polys, dtype=np.int32)
    for i, poly in enumerate(mesh.polygons):
        votes = np.bincount(vertex_labels[poly.vertices], minlength=k)
        face_labels[i] = votes.argmax()
    return face_labels


def _face_adjacency_arrays(obj):
    """Build vectorized face-adjacency: returns (a, b) int32 arrays where
    face `a[i]` and `b[i]` share an edge. Built without dict-of-lists for speed.
    """
    mesh = obj.data
    # Use mesh.edge_keys via polygon loops — but we can get the same info from
    # bmesh more efficiently. For Blender 5.1 mesh, every edge has .link_faces
    # but reading that in Python is slow. Use the polygon.edge_keys approach
    # with hash buckets done numpy-style.
    n_polys = len(mesh.polygons)
    # For each polygon, list (sorted vertex pair → polygon_index) entries
    edge_polys: dict = {}
    # Pre-allocate by iterating polygons (still O(P) but flat)
    adj_a: list[int] = []
    adj_b: list[int] = []
    for pi, poly in enumerate(mesh.polygons):
        for ek in poly.edge_keys:
            other = edge_polys.get(ek)
            if other is None:
                edge_polys[ek] = pi
            else:
                adj_a.append(other)
                adj_b.append(pi)
                # Allow third+ poly on non-manifold edge to also be neighbor
                # of all previous: but cheap path = first-pair only (typical
                # cleaned meshes are 2-manifold).
    return np.array(adj_a, dtype=np.int32), np.array(adj_b, dtype=np.int32)


def _connected_components(n: int, labels: np.ndarray,
                          adj_a: np.ndarray, adj_b: np.ndarray) -> np.ndarray:
    """Vectorized union-find: assign each face a component id such that
    components contain only faces with the same label. Returns int32 array
    of component ids (0..C-1).
    """
    # Keep only edges between same-label faces
    same = labels[adj_a] == labels[adj_b]
    ea = adj_a[same]
    eb = adj_b[same]

    parent = np.arange(n, dtype=np.int32)

    def find(x: int) -> int:
        # path compression
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    # Union loop in Python — typically n_edges is ~3×n_faces, still acceptable
    for a, b in zip(ea, eb):
        ra = find(int(a))
        rb = find(int(b))
        if ra != rb:
            parent[ra] = rb

    # Final path compression + relabeling
    roots = np.array([find(i) for i in range(n)], dtype=np.int32)
    # remap to 0..C-1
    _, comp_id = np.unique(roots, return_inverse=True)
    return comp_id.astype(np.int32)


def _remove_tiny_regions(face_labels: np.ndarray,
                         adj_a: np.ndarray, adj_b: np.ndarray,
                         min_size: int) -> tuple[np.ndarray, int]:
    """Connected-component analysis on the face graph using vectorized union-find.
    Components smaller than `min_size` are reassigned to the dominant
    cross-boundary neighbor label.
    """
    if min_size <= 1 or len(face_labels) == 0:
        return face_labels, 0

    n = face_labels.shape[0]
    labels = face_labels.copy()

    comp = _connected_components(n, labels, adj_a, adj_b)
    n_comps = int(comp.max()) + 1
    comp_size = np.bincount(comp, minlength=n_comps)

    # Components to merge
    small_mask = comp_size < min_size
    if not small_mask.any():
        return labels, 0

    # For each face whose component is small, look at its cross-label neighbors
    # and pick the most common external label. Vectorize via accumulation.
    # cross-edge votes: for each edge whose endpoints are in different labels,
    # both sides contribute a vote for the "other" label to the LOCAL component.
    diff = labels[adj_a] != labels[adj_b]
    if diff.any():
        a_diff = adj_a[diff]
        b_diff = adj_b[diff]
        # Build votes[comp, label]
        k = int(labels.max()) + 1
        votes = np.zeros((n_comps, k), dtype=np.int32)
        # Vote for label of b's side, indexed by comp of a
        np.add.at(votes, (comp[a_diff], labels[b_diff]), 1)
        np.add.at(votes, (comp[b_diff], labels[a_diff]), 1)

        merged = 0
        # For each small component, pick max-vote label (excluding own label)
        small_comp_ids = np.where(small_mask)[0]
        for cid in small_comp_ids:
            v = votes[cid].copy()
            # Find any face in this component to learn its current label
            # (use first match — fast lookup via comp == cid would be O(n);
            # instead store as we go)
            pass  # filled below

        # Faster: precompute mapping comp_id → current label (any member)
        comp_label = np.full(n_comps, -1, dtype=np.int32)
        # take first face per comp
        for i in range(n):
            c = comp[i]
            if comp_label[c] < 0:
                comp_label[c] = labels[i]

        # For each small comp, find dominant external label
        new_label_for_comp = comp_label.copy()
        for cid in small_comp_ids:
            v = votes[cid].copy()
            v[comp_label[cid]] = -1  # exclude own
            if v.max() > 0:
                new_label_for_comp[cid] = int(v.argmax())
                merged += 1

        # Apply
        labels = new_label_for_comp[comp]
        return labels, merged
    return labels, 0


def _wipe_old_materials(obj) -> None:
    obj.data.materials.clear()


def _build_materials(centers_srgb: np.ndarray) -> list:
    """Create K new materials whose Base Color = each cluster center."""
    mats = []
    for j, c in enumerate(centers_srgb):
        mat = bpy.data.materials.new(name=f"Region_{j:02d}")
        mat.use_nodes = True
        # Strip any default vertex-color → base-color wiring
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf is None:
            for n in mat.node_tree.nodes:
                if n.type == "BSDF_PRINCIPLED":
                    bsdf = n; break
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = (float(c[0]), float(c[1]),
                                                       float(c[2]), 1.0)
            # Remove any vertex-color input link to Base Color
            for link in list(mat.node_tree.links):
                if link.to_node == bsdf and link.to_socket.name == "Base Color":
                    mat.node_tree.links.remove(link)
        # Display color in solid view
        mat.diffuse_color = (float(c[0]), float(c[1]), float(c[2]), 1.0)
        mats.append(mat)
    return mats


def _strip_vertex_colors(obj) -> None:
    """Remove color attributes — solid materials supersede them."""
    mesh = obj.data
    while mesh.color_attributes:
        try:
            mesh.color_attributes.remove(mesh.color_attributes[0])
        except Exception:
            break


def _export_glb(path: str) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for o in bpy.data.objects:
        if o.type == "MESH":
            o.select_set(True)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=path,
        export_format="GLB",
        export_materials="EXPORT",
        export_apply=False,
        use_selection=True,
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",  required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--k", type=int, default=4)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--colorspace", choices=["srgb", "lab"], default="lab",
                    help="cluster space (LAB ≈ human perception, sRGB faster)")
    # ── Noise reduction passes ──
    ap.add_argument("--smooth-iters", type=int, default=5,
                    help="vertex-color Laplacian smoothing iterations (default 5; "
                         "0 disables). Removes per-vertex KNN noise before clustering.")
    ap.add_argument("--smooth-alpha", type=float, default=0.7,
                    help="smoothing strength per iter; 1.0 = pure neighbor mean")
    ap.add_argument("--label-smooth-iters", type=int, default=3,
                    help="label majority-filter iterations after K-means (default 3)")
    ap.add_argument("--min-region-size", type=int, default=100,
                    help="connected face components smaller than this are merged "
                         "into surrounding region (default 100; 0 disables)")
    args, _ = ap.parse_known_args(argv)

    if bpy is None:
        print("ERROR: must run inside Blender (bpy unavailable)", file=sys.stderr)
        return 2

    k = max(2, min(args.k, 8))

    try:
        _clear_scene()
        obj = _import_glb(args.input)

        # Read vertex colors
        rgb = _read_vertex_colors(obj)
        if rgb is None:
            # No vertex colors → can't split by color. Bail with a meaningful result.
            print("RESULT:" + json.dumps({
                "status": "skipped",
                "reason": "input mesh has no vertex color layer",
                "output_path": args.input,
            }))
            return 0

        # ─── (3) Vertex-color Laplacian smoothing (de-noise) ───
        e0, e1 = _build_edge_arrays(obj)
        rgb_smooth = _smooth_vertex_colors(
            rgb, e0, e1,
            iters=max(0, args.smooth_iters),
            alpha=float(args.smooth_alpha),
        )

        # ─── (4) Cluster in chosen color space ───
        if args.colorspace == "lab":
            data = _linear_to_lab(_srgb_to_linear(rgb_smooth))
        else:
            data = rgb_smooth.astype(np.float64)

        labels, centers_in_space = _kmeans(data, k, seed=args.seed)

        # ─── (5) Label majority filter on mesh graph ───
        labels = _smooth_labels(labels, e0, e1, k, iters=max(0, args.label_smooth_iters))

        # Convert cluster centers back to sRGB for material base color.
        # Use mean of ORIGINAL (un-smoothed) vertex colors per final cluster —
        # gives the most faithful representative color.
        centers_srgb = np.zeros((k, 3), dtype=np.float64)
        for j in range(k):
            mask = labels == j
            if mask.any():
                centers_srgb[j] = rgb[mask].mean(axis=0)
            else:
                centers_srgb[j] = (0.5, 0.5, 0.5)

        counts = np.bincount(labels, minlength=k).tolist()

        # ─── (6) Face labels by vertex majority vote ───
        face_labels = _face_majority_labels(obj, labels, k)

        # ─── (7) Remove tiny connected components ───
        merged_regions = 0
        if args.min_region_size > 1:
            adj_a, adj_b = _face_adjacency_arrays(obj)
            face_labels, merged_regions = _remove_tiny_regions(
                face_labels, adj_a, adj_b, int(args.min_region_size)
            )

        # Wipe existing materials + colors, build K fresh materials
        _wipe_old_materials(obj)
        mats = _build_materials(centers_srgb)
        for m in mats:
            obj.data.materials.append(m)
        _strip_vertex_colors(obj)

        # Assign material indices per polygon
        face_labels_buf = face_labels.astype(np.int32).copy()
        obj.data.polygons.foreach_set("material_index", face_labels_buf)
        obj.data.update()

        # Export
        _export_glb(args.output)

        size = os.path.getsize(args.output)
        # Recount per-region face counts after possible merge
        final_face_counts = np.bincount(face_labels, minlength=k).tolist()
        print("RESULT:" + json.dumps({
            "status": "ok",
            "output_path": args.output,
            "k": k,
            "regions": [
                {"index": j,
                 "rgb": [round(float(centers_srgb[j][0]), 4),
                         round(float(centers_srgb[j][1]), 4),
                         round(float(centers_srgb[j][2]), 4)],
                 "vertex_count": int(counts[j]),
                 "face_count": int(final_face_counts[j])}
                for j in range(k)
            ],
            "file_size_kb": size // 1024,
            "vertex_count": int(len(rgb)),
            "polygon_count": int(len(face_labels)),
            "denoise": {
                "smooth_iters": int(args.smooth_iters),
                "label_smooth_iters": int(args.label_smooth_iters),
                "min_region_size": int(args.min_region_size),
                "tiny_components_merged": int(merged_regions),
            },
        }))
        return 0

    except Exception as exc:
        traceback.print_exc()
        print("RESULT:" + json.dumps({"status": "error", "message": str(exc)}))
        return 1


if __name__ == "__main__":
    # When invoked as `blender ... -- --input ... --output ...`, argv after `--`
    if "--" in sys.argv:
        argv = sys.argv[sys.argv.index("--") + 1:]
    else:
        argv = sys.argv[1:]
    sys.exit(main(argv))
