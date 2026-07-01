"""
CleanMesh — Subprocess helpers with hidden console windows.

Every subprocess.run() / Popen() call on Windows creates a visible cmd
window by default, which makes the UI flash console pop-ups constantly
(nvidia-smi polling, WSL calls, taskkill, Blender headless, etc.).

Use these helpers everywhere instead of subprocess.* directly to get:
  - Hidden console on Windows (CREATE_NO_WINDOW)
  - No behaviour change on Linux/macOS
  - Same signature as subprocess.run / Popen so refactoring is trivial

Usage:
    from cleanmesh.subproc import run, popen

    cp = run(['nvidia-smi', '...'], capture_output=True, text=True, timeout=5)
    proc = popen(cmd, stdout=..., creationflags=DETACHED_PROCESS)
        # (extra creationflags are OR'd with CREATE_NO_WINDOW)
"""

from __future__ import annotations

import os
import subprocess

# On Windows, prevent visible cmd window from popping up per subprocess.
# 0x08000000 = CREATE_NO_WINDOW. No effect on other platforms.
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _merge_flags(kwargs: dict) -> dict:
    """Force CREATE_NO_WINDOW into creationflags without clobbering
    existing flags the caller might have set (e.g. DETACHED_PROCESS)."""
    existing = kwargs.get("creationflags", 0)
    kwargs["creationflags"] = existing | _NO_WINDOW
    return kwargs


def run(cmd, **kwargs) -> subprocess.CompletedProcess:
    """Drop-in for subprocess.run with a hidden console on Windows."""
    return subprocess.run(cmd, **_merge_flags(kwargs))


def popen(cmd, **kwargs) -> subprocess.Popen:
    """Drop-in for subprocess.Popen with a hidden console on Windows."""
    return subprocess.Popen(cmd, **_merge_flags(kwargs))
