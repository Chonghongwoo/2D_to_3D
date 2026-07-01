"""
CleanMesh — VRAM Guard
=======================

Pre-flight and recovery utilities for OOM-prone GPU workloads
(TRELLIS in particular) on 8 GB consumer GPUs.

Strategy stack (low to high impact):

  1. **Measure**      — query `nvidia-smi` for free VRAM.
  2. **Wait**         — short backoff polling, in case a transient
                          allocation (Discord/Teams burst) just freed.
  3. **WSL shutdown** — `wsl --shutdown` nukes the entire WSL2 VM and
                          forces it to release ALL allocated VRAM
                          (including PyTorch-held fragments). Heaviest
                          single hammer; takes ~3 s.
  4. **Kill hogs**    — Stop known Windows-side GPU consumers
                          (Discord, Teams, Chrome, OBS, NVIDIA Share,
                          Razer). OFF by default; user must opt in via
                          config because it kills running apps.

All routines fail soft — they NEVER raise; they return a result dict so
that the caller's retry loop can log and decide.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from .subproc import run as _hidden_run
import time

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1) Measure
# ---------------------------------------------------------------------------

def get_free_vram_mb(timeout: float = 5.0) -> int | None:
    """Return free VRAM on the first NVIDIA GPU, in MiB.

    Returns None when nvidia-smi is unavailable or fails — callers
    treat that as "unknown, proceed without guard".
    """
    nvsmi = shutil.which("nvidia-smi")
    if nvsmi is None:
        return None
    try:
        cp = _hidden_run(
            [nvsmi,
             "--query-gpu=memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        if cp.returncode != 0:
            return None
        first_line = cp.stdout.strip().splitlines()[0].strip()
        return int(first_line)
    except (subprocess.TimeoutExpired, ValueError, IndexError):
        return None


def get_vram_summary() -> dict:
    """Return {total_mb, used_mb, free_mb} or empty dict on failure."""
    nvsmi = shutil.which("nvidia-smi")
    if nvsmi is None:
        return {}
    try:
        cp = _hidden_run(
            [nvsmi,
             "--query-gpu=memory.total,memory.used,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            encoding="utf-8", errors="replace",
        )
        if cp.returncode != 0:
            return {}
        total, used, free = [
            int(s.strip()) for s in cp.stdout.strip().splitlines()[0].split(",")
        ]
        return {"total_mb": total, "used_mb": used, "free_mb": free}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 2) Wait
# ---------------------------------------------------------------------------

def wait_for_vram(min_free_mb: int,
                  timeout_s: int = 30,
                  poll_interval: float = 1.5) -> bool:
    """Block until free VRAM ≥ threshold, or timeout. Returns True on success."""
    if min_free_mb <= 0:
        return True
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        free = get_free_vram_mb()
        if free is None:                  # unknown — skip guard
            return True
        if free >= min_free_mb:
            return True
        time.sleep(poll_interval)
    return False


# ---------------------------------------------------------------------------
# 3) WSL shutdown — hardest VRAM hammer
# ---------------------------------------------------------------------------

TRELLIS_DISTRO = "Ubuntu-22.04"


def wsl_shutdown(timeout: float = 15.0,
                 distro: str = TRELLIS_DISTRO) -> dict:
    """Release the TRELLIS distro's VRAM.

    Strategy — **targeted, not systemwide**:
      1) `wsl -t <distro>` terminates ONLY the TRELLIS distro (Ubuntu-22.04).
         Other WSL sessions (Docker Desktop, dev environments) survive.
      2) If VRAM didn't drop enough, fall back to `wsl --shutdown` which
         nukes every WSL2 VM (heaviest hammer). This still preserves
         non-WSL work but does end Docker containers etc.

    Return dict records which path was used so the caller can log it.
    """
    wsl = shutil.which("wsl") or "wsl"
    before = get_vram_summary().get("free_mb")

    # 1) Try targeted terminate first
    try:
        cp = _hidden_run(
            [wsl, "-t", distro],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        targeted_ok = cp.returncode == 0
    except subprocess.TimeoutExpired:
        targeted_ok = False
    except FileNotFoundError:
        return {"action": "wsl_shutdown", "ok": False,
                "reason": "wsl not on PATH"}

    time.sleep(2.5)
    after = get_vram_summary().get("free_mb")
    freed = (after - before) if (before is not None and after is not None) else None

    # 2) Escalate to full shutdown ONLY if targeted terminate freed < 500 MB
    #    (this means WSL2's utility VM still holds the memory across distros).
    escalated = False
    if freed is not None and freed < 500:
        try:
            _hidden_run(
                [wsl, "--shutdown"],
                capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
            escalated = True
            time.sleep(2.5)
            after = get_vram_summary().get("free_mb")
            freed = (after - before) if (before is not None and after is not None) else None
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    return {
        "action": "wsl_shutdown",
        "ok": targeted_ok or escalated,
        "distro_terminated": distro,
        "escalated_to_full_shutdown": escalated,
        "free_mb_before": before,
        "free_mb_after":  after,
        "freed_mb":       freed,
    }


# ---------------------------------------------------------------------------
# 4) Kill known GPU hogs (opt-in only — kills user's running apps)
# ---------------------------------------------------------------------------

# Process basenames (no extension) that hold VRAM in the BACKGROUND
# without the user actively working with them right now. Killing these
# is intended to be low-surprise:
#   - NVIDIA Share:      silent recording overlay; not the user's foreground app
#   - NVIDIA Overlay:    performance overlay; same rationale
#   - msedgewebview2:    embedded webview engine for Electron apps
#                        (Discord/Slack/Teams). Killing it just triggers
#                        their embedded views to reload — no user data loss.
#
# Apps that USERS ACTIVELY USE (Discord/Teams chats, OBS recording, Razer
# peripheral controllers) are DELIBERATELY EXCLUDED from the default tier
# because killing them without asking loses in-progress conversations,
# recordings, or peripheral state (RGB lighting, macro keys).
#
# Those apps can still be killed manually via _AGGRESSIVE_HOGS (opt-in via
# a config flag not enabled by any current code path — reserved for future
# opt-in UI toggle).
_KNOWN_HOGS = [
    "NVIDIA Share",
    "NVIDIA Overlay",
    "msedgewebview2",
]

# Reserved for future opt-in — currently NOT invoked by any caller.
# Killing these disrupts real user work:
#   - Discord/Teams: active chats and calls
#   - OBS: mid-recording livestreams
#   - Razer: peripheral RGB and macros
_AGGRESSIVE_HOGS = [
    "Discord",
    "Teams",
    "ms-teams",
    "obs64",
    "obs32",
    "RazerCortex",
    "RazerAppEngine",
]


def kill_known_gpu_hogs(also_browsers: bool = False,
                        also_blender: bool = False) -> dict:
    """Stop processes known to hold VRAM unnecessarily.

    Escalation tiers:
      - tier 0 (default):  Discord, Teams, OBS, NVIDIA Share, Razer, msedgewebview2
      - + also_browsers:   chrome, msedge, firefox, brave
      - + also_blender:    blender (user must not have unsaved work)

    Returns {killed: [...], errors: [...]}.
    """
    targets = list(_KNOWN_HOGS)
    if also_browsers:
        targets += ["chrome", "msedge", "firefox", "brave"]
    if also_blender:
        targets += ["blender", "blender-app"]

    killed, errors = [], []
    for name in targets:
        try:
            # Use taskkill — present on every Windows install, no PowerShell quirks
            cp = _hidden_run(
                ["taskkill", "/F", "/IM", f"{name}.exe", "/T"],
                capture_output=True, text=True, timeout=8,
                encoding="utf-8", errors="replace",
            )
            if cp.returncode == 0:
                killed.append(name)
        except Exception as e:
            errors.append({"name": name, "error": str(e)})

    return {"action": "kill_hogs",
            "killed": killed,
            "errors": errors,
            "also_browsers": also_browsers,
            "also_blender":  also_blender}


# ---------------------------------------------------------------------------
# Composite: pre-flight check
# ---------------------------------------------------------------------------

def ensure_vram_available(min_free_mb: int,
                          wait_timeout_s: int = 30,
                          allow_wsl_shutdown: bool = True,
                          allow_kill_hogs: bool = False) -> dict:
    """Try to make sure ≥ min_free_mb VRAM is free before a job.

    Escalation:
      1) Quick measure — if already enough free, return immediately.
      2) Wait & poll up to wait_timeout_s.
      3) If still short and allow_wsl_shutdown: wsl --shutdown.
      4) If still short and allow_kill_hogs: kill known GPU hogs.
      5) Measure once more, return final state.

    Returns {ok: bool, free_mb: int|None, steps: [...]}.
    """
    steps = []

    free = get_free_vram_mb()
    steps.append({"step": "initial_measure", "free_mb": free})
    if free is None:
        return {"ok": True, "free_mb": None, "steps": steps,
                "note": "nvidia-smi unavailable, skipping guard"}
    if free >= min_free_mb:
        return {"ok": True, "free_mb": free, "steps": steps}

    # 2) wait & poll
    if wait_for_vram(min_free_mb, timeout_s=wait_timeout_s):
        free = get_free_vram_mb()
        steps.append({"step": "wait_succeeded", "free_mb": free})
        return {"ok": True, "free_mb": free, "steps": steps}
    steps.append({"step": "wait_timeout", "free_mb": get_free_vram_mb()})

    # 3) WSL shutdown
    if allow_wsl_shutdown:
        r = wsl_shutdown()
        steps.append(r)
        free = get_free_vram_mb()
        if free is not None and free >= min_free_mb:
            return {"ok": True, "free_mb": free, "steps": steps}

    # 4) Kill known hogs (opt-in)
    if allow_kill_hogs:
        r = kill_known_gpu_hogs(also_browsers=False)
        steps.append(r)
        time.sleep(2.0)
        free = get_free_vram_mb()
        if free is not None and free >= min_free_mb:
            return {"ok": True, "free_mb": free, "steps": steps}

    # Final measurement
    final = get_free_vram_mb()
    steps.append({"step": "final_measure", "free_mb": final})
    return {
        "ok": (final is not None and final >= min_free_mb),
        "free_mb": final,
        "steps": steps,
        "min_required_mb": min_free_mb,
    }
