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


def _sanitize_inputs(image_paths: List[str], preserve_alpha: bool = False,
                     max_dim: int = 1024) -> List[Path]:
    """Re-save each input as in_NN.png in HOST_WORKDIR/inputs with ASCII names.

    This avoids passing korean/space paths through `wsl -- bash -c` quoting,
    and normalizes color space.

    When ``preserve_alpha`` is True, inputs with an alpha channel are kept as
    RGBA (used when SAM2 has already masked the subject). Otherwise images are
    flattened to RGB.

    ``max_dim`` caps the longer side. Default 1024 fits 8GB GPUs; the OOM
    retry path drops it to 768 / 512 to claw back VRAM headroom.
    """
    from PIL import Image
    in_dir = HOST_WORKDIR / "inputs"
    in_dir.mkdir(parents=True, exist_ok=True)

    MAX_DIM = max_dim

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


# OOM detection — strings that appear in TRELLIS / PyTorch / cumm / CUDA
# error output on OOM. Matched CASE-INSENSITIVELY against stdout + stderr +
# parsed.message because OOM bubbles up from several CUDA-adjacent libs:
#   • PyTorch       → "CUDA out of memory"
#   • cumm (TensorRT helper) → "cuda failed with error 2 out of memory"
#   • cuBLAS/cuDNN  → "CUBLAS_STATUS_ALLOC_FAILED"
#   • raw CUDA      → "cudaErrorMemoryAllocation", "out of memory"
_OOM_NEEDLES = (
    "out of memory",                  # catches the cumm + most others
    "outofmemoryerror",
    "cublas_status_alloc_failed",
    "cuda error: out of memory",
    "cudaerrormemoryallocation",
    "cuda failed with error 2",       # cumm-specific error code 2
)

# Adaptive resolution ladder for OOM retries
_MAX_DIM_LADDER = [1024, 768, 512]

# Minimum free VRAM (MiB) we want before kicking off TRELLIS.
# Empirically TRELLIS-image-large needs ~5 GB peak; pre-flight at 4 GB
# gives ~1 GB headroom for fragmentation.
_MIN_FREE_VRAM_MB = 4000


def _looks_like_oom(stdout: str, stderr: str, parsed: dict | None) -> bool:
    """Detect OOM from any of: stderr, stdout, or parsed result.message.

    Case-insensitive match — OOM messages come from many CUDA-adjacent
    libs in inconsistent casing (CUDA / cuda, OutOfMemory / out of memory).
    """
    haystack = ((stderr or "") + "\n" + (stdout or "")).lower()
    if parsed and isinstance(parsed.get("message"), str):
        haystack += "\n" + parsed["message"].lower()
    return any(needle in haystack for needle in _OOM_NEEDLES)


def _run_trellis_once(sanitized: List[Path], raw_workdir_out: Path,
                       seed: int, steps_ss: int, steps_slat: int,
                       cfg_ss: float, cfg_slat: float, model: str,
                       pre_masked: bool, deterministic: bool,
                       timeout_s: int,
                       expandable_segments: bool = True
                       ) -> tuple[dict | None, str, str, int]:
    """One TRELLIS invocation. Returns (parsed_or_None, stdout, stderr, rc).

    ``expandable_segments``: when True (default), exports
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True into the WSL bash
    environment. This is PyTorch's official fragmentation mitigation
    and is essential on 8GB GPUs where free VRAM exists but no
    contiguous 1+ GiB block is available.
    """
    wsl_inputs = " ".join(f'"{_to_wsl_path(p)}"' for p in sanitized)
    wsl_out = _to_wsl_path(raw_workdir_out)
    pre_masked_flag = " --pre-masked" if pre_masked else ""
    deterministic_flag = " --deterministic" if deterministic else ""
    env_prefix = ""
    if expandable_segments:
        env_prefix = "export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True && "
    bash_cmd = (
        f"{env_prefix}"
        f"source {WSL_VENV_ACTIVATE} && "
        f"python {WSL_RUNNER} --inputs {wsl_inputs} --output \"{wsl_out}\" "
        f"--seed {seed} --steps-ss {steps_ss} --steps-slat {steps_slat} "
        f"--cfg-ss {cfg_ss} --cfg-slat {cfg_slat} --model {model}"
        f"{pre_masked_flag}{deterministic_flag}"
    )
    cmd = ["wsl", "-d", WSL_DISTRO, "--", "bash", "-c", bash_cmd]
    logger.debug(f"WSL cmd: {bash_cmd}")

    try:
        cp = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout_s, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return None, "", f"timeout after {timeout_s}s", -1
    except FileNotFoundError:
        return None, "", "wsl executable not found in PATH", -1

    parsed = None
    for line in cp.stdout.split("\n"):
        line = line.strip()
        if line.startswith("RESULT:"):
            try:
                parsed = json.loads(line[7:])
                break
            except json.JSONDecodeError:
                pass
    return parsed, cp.stdout, cp.stderr, cp.returncode


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
    vram_auto_recover: bool = True,
    vram_kill_hogs: bool = False,
) -> dict:
    """Generate a 3D model from 1+ images via TRELLIS (in WSL).

    Args:
        image_paths: Input images. May be RGB or RGBA.
        pre_masked: When True, treat inputs as already having a clean alpha
            channel (e.g. produced by SAM2 click-segment). Tells the WSL runner
            to bypass TRELLIS's built-in rembg preprocessing.
        deterministic: Force PyTorch/CUDA into deterministic mode. Slower
            (~30-50%) but same input + seed reproduces the same mesh.
        vram_auto_recover: When True (default), pre-flight VRAM check + on OOM
            run `wsl --shutdown`, drop image resolution one step
            (1024 → 768 → 512), and retry up to 3 attempts total.
        vram_kill_hogs: When True, also kill known GPU-hog Windows apps
            (Discord, Teams, OBS, NVIDIA Share, Razer) during recovery.
            Default OFF — opt-in only since it terminates user apps.

    Returns dict with status ('success' | 'error'), output_path, etc.,
    plus a 'vram_recovery' field summarizing any recovery actions taken.
    """
    config = get_config()
    from ..vram_guard import ensure_vram_available, wsl_shutdown, get_vram_summary

    if not image_paths:
        return {"status": "error", "message": "TRELLIS requires at least one input image"}

    # Pre-flight VRAM check
    recovery_log = []
    if vram_auto_recover:
        pre = ensure_vram_available(
            min_free_mb=_MIN_FREE_VRAM_MB,
            wait_timeout_s=15,
            allow_wsl_shutdown=True,
            allow_kill_hogs=vram_kill_hogs,
        )
        recovery_log.append({"phase": "pre_flight", **pre})
        if not pre.get("ok"):
            logger.warning(
                f"⚠️ VRAM pre-flight: 확보 못함 "
                f"(free={pre.get('free_mb')} MB, need={_MIN_FREE_VRAM_MB} MB) — 그래도 시도"
            )
        else:
            logger.info(f"✅ VRAM pre-flight OK (free={pre.get('free_mb')} MB)")

    # Resolve output paths (timestamp picked ONCE for the whole retry session)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_workdir_out = HOST_WORKDIR / "outputs" / f"trellis_{timestamp}.glb"
    raw_workdir_out.parent.mkdir(parents=True, exist_ok=True)
    if not output_path:
        output_path = str(config.paths.raw_dir / f"trellis_{timestamp}.glb")
    output_path = str(output_path)

    # Retry loop with adaptive resolution
    last_error = None
    for attempt_idx, max_dim in enumerate(_MAX_DIM_LADDER):
        try:
            sanitized = _sanitize_inputs(
                image_paths, preserve_alpha=pre_masked, max_dim=max_dim,
            )
        except Exception as e:
            return {"status": "error", "message": f"input prep failed: {e}",
                    "vram_recovery": recovery_log}

        attempt_label = (
            f"시도 {attempt_idx+1}/{len(_MAX_DIM_LADDER)}"
            f" (max_dim={max_dim})"
        )
        logger.info(f"🧠 TRELLIS {attempt_label}: {len(sanitized)}장 → {raw_workdir_out.name}")

        parsed, stdout, stderr, rc = _run_trellis_once(
            sanitized, raw_workdir_out,
            seed, steps_ss, steps_slat, cfg_ss, cfg_slat, model,
            pre_masked, deterministic,
            timeout_s=config.trellis.timeout_seconds,
        )

        # Detect outcome
        if parsed and parsed.get("status") == "success":
            # Copy out and return
            src_path = Path(raw_workdir_out)
            if not src_path.is_file():
                return {"status": "error",
                        "message": f"runner reported success but no file at {src_path}",
                        "vram_recovery": recovery_log}
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src_path), output_path)
            parsed["output_path"] = output_path
            parsed["vram_recovery"] = recovery_log
            parsed["max_dim_used"] = max_dim
            return parsed

        # Was this an OOM? Decide whether to retry.
        was_oom = _looks_like_oom(stdout, stderr, parsed)
        last_error = parsed or {
            "status": "error",
            "message": f"TRELLIS exit={rc}",
            "stdout_tail": (stdout or "")[-1500:],
            "stderr_tail": (stderr or "")[-1500:],
        }

        if not was_oom:
            # Non-OOM failure (e.g. bad input, model error) — don't retry
            logger.warning("❌ TRELLIS 실패 (OOM 아님) — 재시도 안함")
            last_error["vram_recovery"] = recovery_log
            return last_error

        # OOM detected
        is_last_attempt = (attempt_idx == len(_MAX_DIM_LADDER) - 1)
        logger.warning(
            f"⚠️ TRELLIS OOM (max_dim={max_dim}) — "
            f"{'마지막 시도였음' if is_last_attempt else '복구 후 재시도'}"
        )
        recovery_log.append({
            "phase": f"attempt_{attempt_idx+1}_oom",
            "max_dim": max_dim,
        })

        if is_last_attempt or not vram_auto_recover:
            last_error["vram_recovery"] = recovery_log
            last_error.setdefault("message", "OOM")
            last_error["message"] = f"OOM after {attempt_idx+1} attempts: " + str(
                last_error.get("message", "")
            )
            return last_error

        # Recovery: wsl shutdown (always) + optional kill hogs
        r = wsl_shutdown()
        recovery_log.append({"phase": "recover", **r})
        after = get_vram_summary()
        logger.info(
            f"🔄 WSL shutdown 완료 → free VRAM "
            f"{after.get('free_mb','?')} / {after.get('total_mb','?')} MB"
        )

    # ────────────────────────────────────────────────────────────────
    # All max_dim ladder attempts exhausted at the original image count.
    # Last-ditch fallback for 8GB GPUs: if input was multi-view, drop
    # to single-view (first image only). TRELLIS-image-large single is
    # known to fit in ~5GB peak; multi-view needs ~7-8GB contiguous.
    # ────────────────────────────────────────────────────────────────
    if vram_auto_recover and len(image_paths) > 1:
        logger.warning(
            f"⚠️ 멀티뷰 {len(image_paths)}장 모두 OOM — 첫 이미지만으로 단일뷰 fallback"
        )
        recovery_log.append({
            "phase": "fallback_single_view",
            "dropped_image_count": len(image_paths) - 1,
            "kept": image_paths[0],
        })
        wsl_shutdown()
        try:
            sanitized = _sanitize_inputs(
                [image_paths[0]], preserve_alpha=pre_masked, max_dim=1024,
            )
        except Exception as e:
            return {"status": "error", "message": f"input prep failed: {e}",
                    "vram_recovery": recovery_log}

        logger.info(f"🧠 TRELLIS 단일뷰 fallback: {Path(image_paths[0]).name}")
        parsed, stdout, stderr, rc = _run_trellis_once(
            sanitized, raw_workdir_out,
            seed, steps_ss, steps_slat, cfg_ss, cfg_slat, model,
            pre_masked, deterministic,
            timeout_s=config.trellis.timeout_seconds,
        )
        if parsed and parsed.get("status") == "success":
            src_path = Path(raw_workdir_out)
            if src_path.is_file():
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src_path), output_path)
                parsed["output_path"] = output_path
                parsed["vram_recovery"] = recovery_log
                parsed["max_dim_used"] = 1024
                parsed["fallback_to_single_view"] = True
                logger.info("✅ 단일뷰 fallback 성공")
                return parsed
        # single-view also failed
        recovery_log.append({"phase": "fallback_single_view_failed"})
        if parsed:
            last_error = parsed

    # Should not reach here, but for completeness
    if last_error is None:
        last_error = {"status": "error", "message": "unknown failure"}
    last_error["vram_recovery"] = recovery_log
    return last_error


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
