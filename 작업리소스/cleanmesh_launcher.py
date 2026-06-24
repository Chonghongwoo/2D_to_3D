"""
CleanMesh Studio Launcher
=========================
- Starts TripoSR FastAPI server (port 8000) if not running
- Starts CleanMesh FastAPI server (port 8100) if not running
- Opens the default browser to http://localhost:8100/
- Writes detailed logs to CleanMeshStudio.log right next to the executable
  for easy debugging.

When packaged with PyInstaller (--onefile), the log file lives in the same
folder as CleanMeshStudio.exe.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration (edit these constants if your install paths change)
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(r"D:\GoogleDrive\Image_to_3D\작업리소스")
TRIPOSR_DIR = Path(r"C:\WorkingJob\3d-model-tool\python-backend")
TRIPOSR_PY  = TRIPOSR_DIR / "venv" / "Scripts" / "python.exe"
TRIPOSR_MAIN = TRIPOSR_DIR / "main.py"
SERVER_LOG_DIR = Path(r"C:\t3d\logs")

CLEANMESH_PORT = 8100
TRIPOSR_PORT   = 8000
CLEANMESH_URL  = f"http://localhost:{CLEANMESH_PORT}/"

WARMUP_TIMEOUT_SEC = 20.0    # how long we wait for CleanMesh to start listening
WARMUP_POLL_SEC    = 0.5

# ---------------------------------------------------------------------------
# Resolve "next to the exe" path (works in both frozen and dev mode)
# ---------------------------------------------------------------------------
def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR  = app_dir()
LOG_FILE = APP_DIR / "CleanMeshStudio.log"

# ---------------------------------------------------------------------------
# Logging setup — both file and console
# ---------------------------------------------------------------------------
def setup_logging() -> logging.Logger:
    logger = logging.getLogger("cleanmesh")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler (UTF-8, appended)
    try:
        fh = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
        fh.setFormatter(fmt)
        fh.setLevel(logging.DEBUG)
        logger.addHandler(fh)
    except Exception as e:
        # If file logging fails, at least keep console logging alive
        print(f"[launcher] WARNING: could not open log file {LOG_FILE}: {e}")

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    ch.setLevel(logging.INFO)
    logger.addHandler(ch)

    return logger


log = setup_logging()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def is_port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            return s.connect_ex((host, port)) == 0
        except Exception as exc:
            log.debug(f"port check {host}:{port} failed: {exc}")
            return False


def spawn_detached(cmd: list[str], cwd: Path, log_path: Path, label: str) -> int | None:
    """Launch a background process whose stdout/stderr go to `log_path`.

    Returns the PID, or None on failure.
    """
    log.info(f"[{label}] spawning: {' '.join(cmd)}")
    log.info(f"[{label}] cwd:      {cwd}")
    log.info(f"[{label}] log:      {log_path}")
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        out_f = open(log_path, "ab")
        # DETACHED_PROCESS = 0x00000008
        # CREATE_NEW_PROCESS_GROUP = 0x00000200
        # CREATE_NO_WINDOW = 0x08000000
        creationflags = 0x00000008 | 0x00000200 | 0x08000000
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=out_f,
            stderr=out_f,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
        log.info(f"[{label}] launched PID={proc.pid}")
        return proc.pid
    except Exception as exc:
        log.error(f"[{label}] spawn FAILED: {exc}")
        log.debug(traceback.format_exc())
        return None


def wait_for_port(port: int, label: str, timeout: float, poll: float) -> bool:
    deadline = time.monotonic() + timeout
    waited = 0.0
    while time.monotonic() < deadline:
        if is_port_listening(port):
            log.info(f"[{label}] ready on port {port} after {waited:.1f}s")
            return True
        time.sleep(poll)
        waited += poll
    log.error(f"[{label}] did NOT come up on port {port} within {timeout}s")
    return False


def pause_for_user(prompt: str) -> None:
    try:
        input(prompt)
    except EOFError:
        # Can happen when the exe is launched in a context without a TTY
        time.sleep(2.0)


# ---------------------------------------------------------------------------
# Main launcher logic
# ---------------------------------------------------------------------------
def main() -> int:
    log.info("=" * 70)
    log.info("CleanMesh Studio Launcher")
    log.info(f"Started at:  {datetime.now().isoformat(timespec='seconds')}")
    log.info(f"Frozen exe:  {getattr(sys, 'frozen', False)}")
    log.info(f"App dir:     {APP_DIR}")
    log.info(f"Log file:    {LOG_FILE}")
    log.info(f"Project dir: {PROJECT_DIR}")
    log.info(f"TripoSR dir: {TRIPOSR_DIR}")
    log.info("=" * 70)

    SERVER_LOG_DIR.mkdir(parents=True, exist_ok=True)

    # --- 1. TripoSR (port 8000) -----------------------------------------
    log.info("[1/3] Checking TripoSR server (port 8000)...")
    if is_port_listening(TRIPOSR_PORT):
        log.info("[TripoSR] already running OK")
    else:
        log.info("[TripoSR] not running — attempting to start")
        if TRIPOSR_PY.exists() and TRIPOSR_MAIN.exists():
            spawn_detached(
                cmd=[str(TRIPOSR_PY), str(TRIPOSR_MAIN)],
                cwd=TRIPOSR_DIR,
                log_path=SERVER_LOG_DIR / "triposr.log",
                label="TripoSR",
            )
        else:
            log.warning(f"[TripoSR] SKIPPED — files not found:")
            log.warning(f"           python:  {TRIPOSR_PY}  exists={TRIPOSR_PY.exists()}")
            log.warning(f"           main.py: {TRIPOSR_MAIN}  exists={TRIPOSR_MAIN.exists()}")

    # --- 2. CleanMesh (port 8100) ---------------------------------------
    log.info("[2/3] Checking CleanMesh server (port 8100)...")
    if is_port_listening(CLEANMESH_PORT):
        log.info("[CleanMesh] already running OK")
    else:
        log.info("[CleanMesh] not running — starting uvicorn")
        if not PROJECT_DIR.exists():
            log.error(f"[CleanMesh] PROJECT_DIR missing: {PROJECT_DIR}")
            return 2
        spawn_detached(
            cmd=[
                sys.executable if not getattr(sys, "frozen", False) else "python",
                "-m", "uvicorn", "server.main:app",
                "--host", "0.0.0.0",
                "--port", str(CLEANMESH_PORT),
            ],
            cwd=PROJECT_DIR,
            log_path=SERVER_LOG_DIR / "cleanmesh.log",
            label="CleanMesh",
        )
        wait_for_port(
            CLEANMESH_PORT,
            label="CleanMesh",
            timeout=WARMUP_TIMEOUT_SEC,
            poll=WARMUP_POLL_SEC,
        )

    # --- 3. Browser ------------------------------------------------------
    log.info(f"[3/3] Opening browser → {CLEANMESH_URL}")
    try:
        webbrowser.open(CLEANMESH_URL)
    except Exception as exc:
        log.error(f"webbrowser.open failed: {exc}")
        log.info(f"  → please open this URL manually: {CLEANMESH_URL}")

    log.info("-" * 70)
    log.info("Launcher finished. Servers keep running in the background.")
    log.info(f"  TripoSR  logs: {SERVER_LOG_DIR / 'triposr.log'}")
    log.info(f"  CleanMesh logs: {SERVER_LOG_DIR / 'cleanmesh.log'}")
    log.info(f"  Launcher log:   {LOG_FILE}")
    log.info("To stop the servers, run CleanMesh_Stop.bat (or kill ports 8000/8100).")
    log.info("-" * 70)

    pause_for_user("\nPress Enter to close this window (servers keep running)...")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.warning("Interrupted by user (Ctrl+C)")
        sys.exit(130)
    except Exception as exc:
        log.critical(f"Launcher CRASHED: {exc}")
        log.critical(traceback.format_exc())
        pause_for_user("\nLauncher crashed. See log above. Press Enter to exit...")
        sys.exit(1)
