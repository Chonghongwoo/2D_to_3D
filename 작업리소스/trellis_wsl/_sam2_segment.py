"""
SAM2 click-to-segment runner (executed inside WSL).

Reads a JSON job spec from stdin:
{
  "image_path":   "/mnt/c/.../input.png",
  "output_path":  "/mnt/c/.../mask.png",        // RGBA cutout PNG
  "mask_path":    "/mnt/c/.../mask_only.png",   // optional, binary mask
  "points":  [[x, y, label], ...],              // label: 1=foreground, 0=background
  "box":     [x1, y1, x2, y2] | null            // optional bounding box prompt
}

Prints JSON status to stdout:
{ "status": "ok", "score": 0.97, "bbox": [x1, y1, x2, y2] }

Designed to be invoked once per image. SAM2 model is loaded each call
(adds ~3s cold start; acceptable for our use case where users click a few
times per session). For higher throughput, run it as a persistent server.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
CHECKPOINT = Path(os.environ.get("SAM2_CKPT",
    f"{os.environ['HOME']}/sam2/checkpoints/sam2.1_hiera_large.pt"))
CONFIG     = os.environ.get("SAM2_CFG", "configs/sam2.1/sam2.1_hiera_l.yaml")
DEVICE     = os.environ.get("SAM2_DEVICE", "cuda")


def log(msg: str) -> None:
    print(f"[sam2] {msg}", file=sys.stderr, flush=True)


def build_predictor():
    import torch
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    log(f"loading checkpoint {CHECKPOINT}")
    if not CHECKPOINT.exists():
        raise FileNotFoundError(f"SAM2 checkpoint not found: {CHECKPOINT}")

    # Half precision for speed on consumer GPUs
    with torch.inference_mode(), torch.autocast(DEVICE, dtype=torch.bfloat16):
        model = build_sam2(CONFIG, str(CHECKPOINT), device=DEVICE)
    predictor = SAM2ImagePredictor(model)
    return predictor


def feather_alpha(alpha: np.ndarray, radius: int = 2) -> np.ndarray:
    """Soft-edge the binary mask so the cutout doesn't have stairstep edges."""
    from scipy.ndimage import distance_transform_edt
    fg = alpha > 127
    # distance from background to foreground edge
    dt_in  = distance_transform_edt(fg)
    dt_out = distance_transform_edt(~fg)
    # value 0..1 across a band of `radius` px
    sdf = dt_in - dt_out  # +inside, -outside
    soft = np.clip((sdf + radius) / (2 * radius), 0.0, 1.0)
    return (soft * 255).astype(np.uint8)


def run(spec: dict) -> dict:
    image_path  = Path(spec["image_path"])
    output_path = Path(spec["output_path"])
    mask_path   = Path(spec["mask_path"]) if spec.get("mask_path") else None
    points      = spec.get("points") or []
    box         = spec.get("box")
    feather_px  = int(spec.get("feather", 2))

    log(f"image  = {image_path}")
    log(f"output = {output_path}")
    log(f"points = {points}")
    log(f"box    = {box}")

    if not image_path.exists():
        raise FileNotFoundError(image_path)

    image = Image.open(image_path).convert("RGB")
    img_np = np.array(image)

    predictor = build_predictor()
    predictor.set_image(img_np)

    # Build prompts
    pt_coords = np.array([[p[0], p[1]] for p in points], dtype=np.float32) if points else None
    pt_labels = np.array([p[2] for p in points], dtype=np.int32) if points else None
    box_arr   = np.array(box, dtype=np.float32) if box else None

    import torch
    with torch.inference_mode(), torch.autocast(DEVICE, dtype=torch.bfloat16):
        masks, scores, _ = predictor.predict(
            point_coords=pt_coords,
            point_labels=pt_labels,
            box=box_arr,
            multimask_output=True,  # 3 candidate masks, pick best
        )

    # Pick highest-scoring mask
    best = int(np.argmax(scores))
    mask = masks[best]                # bool array [H, W]
    score = float(scores[best])
    log(f"selected mask {best+1}/{len(masks)} score={score:.3f}")

    # Build alpha + cutout
    alpha = (mask.astype(np.uint8) * 255)
    if feather_px > 0:
        try:
            alpha = feather_alpha(alpha, radius=feather_px)
        except Exception as e:
            log(f"feather skipped: {e}")

    rgba = np.dstack([img_np, alpha])
    cutout = Image.fromarray(rgba, mode="RGBA")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cutout.save(output_path, format="PNG")
    log(f"wrote cutout: {output_path}")

    if mask_path:
        mask_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(alpha, mode="L").save(mask_path, format="PNG")
        log(f"wrote mask:   {mask_path}")

    # Subject bounding box (from mask)
    ys, xs = np.where(mask)
    if len(xs) == 0:
        bbox = None
    else:
        bbox = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]

    return {
        "status": "ok",
        "score": score,
        "bbox": bbox,
        "image_size": [int(img_np.shape[1]), int(img_np.shape[0])],
        "output": str(output_path),
        "mask":   str(mask_path) if mask_path else None,
    }


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        print(json.dumps({"status": "error", "error": "empty stdin"}))
        return 2
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "error", "error": f"bad JSON: {e}"}))
        return 2

    try:
        result = run(spec)
    except Exception as e:
        log(traceback.format_exc())
        print(json.dumps({"status": "error", "error": str(e)}))
        return 1

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
