"""
Click-to-segment helper (calls SAM2 inside WSL2).

Usage:
    from cleanmesh.segment import segment_image

    result = segment_image(
        image_path=Path("C:/.../input.png"),
        output_path=Path("C:/.../masked.png"),
        points=[(512, 384, 1), (10, 10, 0)],   # (x, y, 1=fg, 0=bg)
    )

SAM2 lives inside the trellis-venv in WSL2 (shares CUDA 12.1 / torch 2.4).
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
WSL_DISTRO     = "Ubuntu-22.04"
WSL_VENV       = "$HOME/trellis-venv/bin/activate"
WSL_RUNNER     = "/mnt/d/trellis/_sam2_segment.py"
WSL_SAM2_DIR   = "$HOME/sam2"  # needed as cwd so hydra finds configs
# ---------------------------------------------------------------------------

Point = Tuple[int, int, int]  # (x, y, label)  label: 1=fg, 0=bg


@dataclass
class SegmentResult:
    status: str
    output_path: Optional[Path] = None
    mask_path:   Optional[Path] = None
    score:       float = 0.0
    bbox:        Optional[Tuple[int, int, int, int]] = None
    image_size:  Optional[Tuple[int, int]] = None
    error:       Optional[str] = None
    stderr:      str = ""


def _win_to_wsl(p: Path) -> str:
    """C:\\foo\\bar  ->  /mnt/c/foo/bar"""
    s = str(p).replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        return f"/mnt/{s[0].lower()}{s[2:]}"
    return s


def segment_image(
    image_path: Path,
    output_path: Path,
    points: Sequence[Point] = (),
    box: Optional[Tuple[int, int, int, int]] = None,
    mask_path: Optional[Path] = None,
    feather: int = 2,
    timeout_sec: float = 60.0,
) -> SegmentResult:
    """Run SAM2 on `image_path` with the given click prompts, write RGBA cutout
    to `output_path`.

    `points` is a list of (x, y, label) tuples where label=1 means foreground
    (subject) and 0 means background. You can mix both.

    `box` is an optional (x1, y1, x2, y2) bounding-box prompt — often more
    reliable than a single click for tightly-packed scenes.

    Returns a SegmentResult with the score and computed subject bbox.
    """
    if not image_path.exists():
        return SegmentResult(status="error", error=f"input image not found: {image_path}")
    if not points and not box:
        return SegmentResult(status="error",
                             error="need at least one click point or a box")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if mask_path:
        mask_path.parent.mkdir(parents=True, exist_ok=True)

    spec = {
        "image_path":  _win_to_wsl(image_path),
        "output_path": _win_to_wsl(output_path),
        "mask_path":   _win_to_wsl(mask_path) if mask_path else None,
        "points":      [[int(x), int(y), int(l)] for (x, y, l) in points],
        "box":         list(box) if box else None,
        "feather":     int(feather),
    }
    spec_json = json.dumps(spec, ensure_ascii=True)

    # Invoke WSL runner. We chdir into the sam2 repo because hydra looks for
    # config files relative to cwd.
    cmd = [
        "wsl", "-d", WSL_DISTRO, "--", "bash", "-lc",
        f"source {WSL_VENV} && cd {WSL_SAM2_DIR} && "
        f"python {WSL_RUNNER}",
    ]
    log.info("SAM2 segment: %s -> %s (points=%d, box=%s)",
             image_path.name, output_path.name, len(points), bool(box))

    try:
        proc = subprocess.run(
            cmd,
            input=spec_json,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return SegmentResult(status="error", error=f"SAM2 timed out after {timeout_sec}s")
    except Exception as exc:
        return SegmentResult(status="error", error=f"WSL invocation failed: {exc}")

    stderr = (proc.stderr or "").strip()
    stdout = (proc.stdout or "").strip()

    if proc.returncode != 0 and not stdout:
        return SegmentResult(status="error",
                             error=f"runner exited {proc.returncode}",
                             stderr=stderr)

    # Parse JSON status line (last non-empty stdout line)
    last = next((ln for ln in reversed(stdout.splitlines()) if ln.strip()), "")
    try:
        payload = json.loads(last)
    except json.JSONDecodeError:
        return SegmentResult(status="error",
                             error=f"could not parse runner output: {last[:200]}",
                             stderr=stderr)

    if payload.get("status") != "ok":
        return SegmentResult(
            status="error",
            error=payload.get("error", "unknown SAM2 error"),
            stderr=stderr,
        )

    bbox = payload.get("bbox")
    return SegmentResult(
        status="ok",
        output_path=Path(output_path),
        mask_path=Path(mask_path) if mask_path else None,
        score=float(payload.get("score", 0.0)),
        bbox=tuple(bbox) if bbox else None,
        image_size=tuple(payload.get("image_size") or ()),
        stderr=stderr,
    )


def segment_batch(
    images: Sequence[Path],
    output_dir: Path,
    points_per_image: Sequence[Sequence[Point]],
    feather: int = 2,
) -> List[SegmentResult]:
    """Segment a batch of images (multi-view). `points_per_image[i]` lists the
    click prompts for `images[i]`. If a per-image list is empty, that image is
    skipped (returns status='skipped')."""
    if len(images) != len(points_per_image):
        raise ValueError("images and points_per_image must be the same length")

    output_dir.mkdir(parents=True, exist_ok=True)
    results: List[SegmentResult] = []
    for img, pts in zip(images, points_per_image):
        if not pts:
            results.append(SegmentResult(status="skipped"))
            continue
        out = output_dir / f"{img.stem}_masked.png"
        msk = output_dir / f"{img.stem}_mask.png"
        results.append(segment_image(
            image_path=img,
            output_path=out,
            mask_path=msk,
            points=pts,
            feather=feather,
        ))
    return results
