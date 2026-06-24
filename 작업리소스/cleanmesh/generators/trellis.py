"""
CleanMesh TRELLIS Generator — High-quality multi-view 3D reconstruction.

Calls TRELLIS-image-large installed in WSL2 Ubuntu under ~/trellis-venv/.
The host runner script lives at /mnt/d/trellis/_run_trellis.py.
"""

import os
import json
import shutil
import logging
import subprocess
from pathlib import Path
from typing import Optional, List
from datetime import datetime

from ..config import get_config

logger = logging.getLogger(__name__)

WSL_DISTRO = "Ubuntu-22.04"
WSL_VENV_ACTIVATE = "$HOME/trellis-venv/bin/activate"
WSL_RUNNER = "/mnt/d/trellis/_run_trellis.py"
HOST_WORKDIR = Path(r"D:\trellis\_workdir")


def _to_wsl_path(p) -> str:
    """Convert a Windows path to its /mnt/<drive>/... WSL equivalent."""
    p = str(p).replace("\\", "/")
    if len(p) >= 3 and p[1] == ":":
        return f"/mnt/{p[0].lower()}{p[2:]}"
    return p


def _sanitize_inputs(image_paths: List[str], preserve_alpha: bool = False) -> List[Path]:
    """Re-save each input as in_NN.png in HOST_WORKDIR/inputs with ASCII names.

    This avoids passing korean/space paths through `wsl -- bash -c` quoting,
    and normalizes color space.

    When ``preserve_alpha`` is True, inputs with an alpha channel are kept as
    RGBA (used when SAM2 has already masked the subject). Otherwise images are
    flattened to RGB.
    """
    from PIL import Image
    in_dir = HOST_WORKDIR / "inputs"
    in_dir.mkdir(parents=True, exist_ok=True)

    # Hard cap on input dimensions — TRELLIS internally rescales to 518x518,
    # but unscaled 1500+px images blow up peak VRAM during preprocessing.
    # Cap the LONGER side at 1024 to keep memory headroom on 8GB GPUs.
    MAX_DIM = 1024

    sanitized = []
    for i, raw in enumerate(image_paths):
        src = Path(raw)
        if not src.is_file():
            raise FileNotFoundError(f"image not found: {raw}")
        dst = in_dir / f"in_{i:02d}.png"
        img = Image.open(src)

        # Downscale oversized inputs (LANCZOS preserves edge sharpness)
        w, h = img.size
        if max(w, h) > MAX_DIM:
            scale = MAX_DIM / max(w, h)
            new_size = (int(w * scale), int(h * scale))
            img = img.resize(new_size, Image.LANCZOS)
            logger.info(f"  downsampled {src.name}: {w}x{h} → {new_size[0]}x{new_size[1]}")

        if preserve_alpha and img.mode in ("RGBA", "LA") or (
            preserve_alpha and "transparency" in img.info
        ):
            img.convert("RGBA").save(dst)
        else:
            img.convert("RGB").save(dst)
        sanitized.append(dst)
    return sanitized


def generate(
    image_paths: List[str],
    output_path: Optional[str] = None,
    seed: int = 1,
    steps_ss: int = 12,
    steps_slat: int = 12,
    cfg_ss: float = 7.5,
    cfg_slat: float = 3.0,
    model: str = "microsoft/TRELLIS-image-large",
    pre_masked: bool = False,
    deterministic: bool = False,  # OFF by default — RTX 3070 8GB hits OOM
) -> dict:
    """Generate a 3D model from 1+ images via TRELLIS (in WSL).

    Args:
        image_paths: Input images. May be RGB or RGBA.
        pre_masked: When True, treat inputs as already having a clean alpha
            channel (e.g. produced by SAM2 click-segment). Tells the WSL runner
            to bypass TRELLIS's built-in rembg preprocessing.
        deterministic: Force PyTorch/CUDA into deterministic mode. Slower
            (~30-50%) but same input + seed reproduces the same mesh.

    Returns dict with status ('success' | 'error'), output_path, etc.
    """
    config = get_config()

    if not image_paths:
        return {"status": "error", "message": "TRELLIS requires at least one input image"}

    # Sanitize → ASCII-named copies under D:\trellis\_workdir\inputs\
    try:
        sanitized = _sanitize_inputs(image_paths, preserve_alpha=pre_masked)
    except Exception as e:
        return {"status": "error", "message": f"input prep failed: {e}"}

    # Resolve output paths
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_workdir_out = HOST_WORKDIR / "outputs" / f"trellis_{timestamp}.glb"
    raw_workdir_out.parent.mkdir(parents=True, exist_ok=True)

    if not output_path:
        output_path = str(config.paths.raw_dir / f"trellis_{timestamp}.glb")
    output_path = str(output_path)

    # Build the WSL command. Quote each input individually.
    wsl_inputs = " ".join(f'"{_to_wsl_path(p)}"' for p in sanitized)
    wsl_out = _to_wsl_path(raw_workdir_out)
    pre_masked_flag = " --pre-masked" if pre_masked else ""
    deterministic_flag = " --deterministic" if deterministic else ""
    bash_cmd = (
        f"source {WSL_VENV_ACTIVATE} && "
        f"python {WSL_RUNNER} --inputs {wsl_inputs} --output \"{wsl_out}\" "
        f"--seed {seed} --steps-ss {steps_ss} --steps-slat {steps_slat} "
        f"--cfg-ss {cfg_ss} --cfg-slat {cfg_slat} --model {model}"
        f"{pre_masked_flag}{deterministic_flag}"
    )
    cmd = ["wsl", "-d", WSL_DISTRO, "--", "bash", "-c", bash_cmd]

    logger.info(f"🧠 TRELLIS 생성: {len(sanitized)}장 → {raw_workdir_out.name}")
    logger.debug(f"WSL cmd: {bash_cmd}")

    try:
        cp = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.trellis.timeout_seconds,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": f"TRELLIS timed out after {config.trellis.timeout_seconds}s"}
    except FileNotFoundError:
        return {"status": "error", "message": "wsl executable not found in PATH"}

    # Parse RESULT: line from runner
    parsed = None
    for line in cp.stdout.split("\n"):
        line = line.strip()
        if line.startswith("RESULT:"):
            try:
                parsed = json.loads(line[7:])
                break
            except json.JSONDecodeError:
                pass

    if parsed is None:
        return {
            "status": "error",
            "message": f"TRELLIS produced no RESULT (exit {cp.returncode})",
            "stdout_tail": cp.stdout[-2000:],
            "stderr_tail": cp.stderr[-2000:],
        }

    if parsed.get("status") != "success":
        return parsed

    # Copy raw_workdir_out → output_path (different drives may need full copy)
    src_path = Path(raw_workdir_out)
    if not src_path.is_file():
        return {"status": "error", "message": f"runner reported success but no file at {src_path}"}

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src_path), output_path)

    parsed["output_path"] = output_path
    return parsed


def check_available() -> bool:
    """Quick check: WSL distro reachable + venv exists."""
    try:
        cp = subprocess.run(
            ["wsl", "-d", WSL_DISTRO, "--", "test", "-f", "/root/trellis-venv/bin/python"],
            capture_output=True, text=True, timeout=10,
        )
        return cp.returncode == 0
    except Exception:
        return False
