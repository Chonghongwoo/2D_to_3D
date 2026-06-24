#!/usr/bin/env python3
"""TRELLIS image-to-3D runner — invoked from WSL.

Reads N input images, produces a GLB mesh.
Prints RESULT:{json} on the last line for the host process to parse.
"""
import os
import sys
import json
import argparse
import traceback

# Use xformers attention backend (flash-attn is not installed)
os.environ.setdefault("ATTN_BACKEND", "xformers")
os.environ.setdefault("SPCONV_ALGO", "native")

# Make the TRELLIS package importable when running from anywhere
_TRELLIS_ROOT = os.path.dirname(os.path.abspath(__file__))
if _TRELLIS_ROOT not in sys.path:
    sys.path.insert(0, _TRELLIS_ROOT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="+", required=True, help="Input image paths")
    ap.add_argument("--output", required=True, help="Output GLB path")
    ap.add_argument("--model", default="microsoft/TRELLIS-image-large")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--steps-ss", type=int, default=12, help="Sparse structure sampler steps")
    ap.add_argument("--steps-slat", type=int, default=12, help="SLAT sampler steps")
    ap.add_argument("--cfg-ss", type=float, default=7.5)
    ap.add_argument("--cfg-slat", type=float, default=3.0)
    ap.add_argument(
        "--pre-masked", action="store_true",
        help="Inputs already have a clean alpha channel (e.g. from SAM2). "
             "Skip TRELLIS's built-in background removal."
    )
    args = ap.parse_args()

    try:
        # Late import so argparse errors surface clearly
        from PIL import Image
        import torch
        from trellis.pipelines import TrellisImageTo3DPipeline

        print(f"[trellis] torch={torch.__version__}  cuda={torch.cuda.is_available()}", flush=True)
        print(f"[trellis] loading pipeline: {args.model}", flush=True)
        pipeline = TrellisImageTo3DPipeline.from_pretrained(args.model)
        pipeline.cuda()
        print("[trellis] pipeline ready", flush=True)

        # Load images.
        # When --pre-masked is set, we open as RGBA so TRELLIS's preprocess
        # detects the existing alpha and skips its internal rembg call.
        # (TRELLIS pipeline.preprocess_image() short-circuits on RGBA input.)
        images = []
        for p in args.inputs:
            if not os.path.isfile(p):
                raise FileNotFoundError(f"input image not found: {p}")
            img = Image.open(p)
            if args.pre_masked:
                img = img.convert("RGBA")
            images.append(img)

        modes = sorted({im.mode for im in images})
        print(
            f"[trellis] loaded {len(images)} input image(s) modes={modes} "
            f"pre_masked={args.pre_masked}",
            flush=True,
        )
        if args.pre_masked and "RGBA" not in modes:
            print("[trellis] WARN: --pre-masked set but no RGBA inputs found", flush=True)

        # Run pipeline
        ss_params = {"steps": args.steps_ss, "cfg_strength": args.cfg_ss}
        slat_params = {"steps": args.steps_slat, "cfg_strength": args.cfg_slat}

        if len(images) == 1:
            print(f"[trellis] running single-image pipeline (seed={args.seed})", flush=True)
            outputs = pipeline.run(
                images[0],
                seed=args.seed,
                sparse_structure_sampler_params=ss_params,
                slat_sampler_params=slat_params,
            )
        else:
            print(f"[trellis] running multi-image pipeline ({len(images)} imgs, seed={args.seed})", flush=True)
            outputs = pipeline.run_multi_image(
                images,
                seed=args.seed,
                sparse_structure_sampler_params=ss_params,
                slat_sampler_params=slat_params,
            )

        mesh_obj = outputs["mesh"][0]
        # mesh_obj has .vertices and .faces tensors
        verts = mesh_obj.vertices.detach().cpu().numpy() if hasattr(mesh_obj.vertices, 'detach') else mesh_obj.vertices
        faces = mesh_obj.faces.detach().cpu().numpy() if hasattr(mesh_obj.faces, 'detach') else mesh_obj.faces
        print(f"[trellis] mesh: {len(verts)} verts, {len(faces)} faces", flush=True)

        # Try to harvest per-vertex colors from the 3D Gaussian output.
        # TRELLIS doesn't directly attach colors to the mesh — but each Gaussian
        # has rgb color, so we KNN-transfer to mesh vertices.
        vertex_colors = None
        try:
            gs = outputs.get("gaussian", [None])[0]
            if gs is not None:
                xyz = gs.get_xyz.detach().cpu().numpy()       # (N_g, 3)
                # gs.get_features returns SH coefficients; the DC term ≈ base color
                feats = gs.get_features.detach().cpu().numpy()  # (N_g, sh_coef, 3)
                # SH DC term → linear RGB (TRELLIS uses spherical harmonics)
                C0 = 0.28209479177387814  # SH zero-order coefficient
                rgb_lin = feats[:, 0, :] * C0 + 0.5
                rgb_lin = rgb_lin.clip(0.0, 1.0)
                print(f"[trellis] gaussian: {xyz.shape[0]} points with rgb", flush=True)

                # KNN: for each mesh vertex, find nearest Gaussian
                from scipy.spatial import cKDTree
                tree = cKDTree(xyz)
                _, idx = tree.query(verts, k=1)
                vertex_colors = (rgb_lin[idx] * 255).astype('uint8')
                # Add alpha
                import numpy as _np
                vertex_colors = _np.concatenate(
                    [vertex_colors, _np.full((vertex_colors.shape[0], 1), 255, dtype='uint8')],
                    axis=1,
                )
                print(f"[trellis] transferred vertex colors via KNN", flush=True)
        except Exception as ce:
            print(f"[trellis] vertex-color harvest failed: {ce}", flush=True)
            vertex_colors = None

        # Save as GLB via trimesh
        import trimesh
        os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
        if vertex_colors is not None:
            tm = trimesh.Trimesh(
                vertices=verts, faces=faces, vertex_colors=vertex_colors, process=False,
            )
        else:
            tm = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
        tm.export(args.output, file_type='glb')

        size = os.path.getsize(args.output)
        print(f"[trellis] exported: {args.output} ({size} bytes)", flush=True)

        print("RESULT:" + json.dumps({
            "status": "success",
            "method": "trellis_multi" if len(images) > 1 else "trellis_single",
            "output_path": args.output,
            "vertices": int(len(verts)),
            "faces": int(len(faces)),
            "file_size_kb": size // 1024,
            "input_count": len(images),
            "seed": args.seed,
        }))

    except Exception as exc:
        traceback.print_exc()
        print("RESULT:" + json.dumps({"status": "error", "message": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
