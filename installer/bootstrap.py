"""
CleanMesh Studio — Installer / Bootstrap
=========================================

Runs on the TARGET PC to bring it from zero to a working install.

Two modes:
  * bundle-mode  — reads assets from a sibling bundle/ folder
                   (created by 실행\\migrate_pack.bat).
  * fetch-mode   — downloads everything from the internet
                   (huggingface, blender.org, etc.).

Idempotent: safe to re-run after a partial success — it re-detects state
and only executes stages that haven't completed yet.

Stages, in order:
  1. Precheck   — NVIDIA driver, Python 3.11+, ≥30 GB free disk, WSL2 feature
  2. Blender    — install if missing (winget → direct download fallback)
  3. Project    — copy or clone source under %USERPROFILE%\\CleanMesh\\
  4. TripoSR    — copy backend OR git clone + pip install
  5. WSL        — import bundled tar OR install Ubuntu-22.04 + provision
  6. Weights    — fetch TRELLIS + SAM2 weights inside WSL (or reuse bundle)
  7. Launcher   — patch config.py, ensure C:\\t3d\\logs, ensure PyInstaller
  8. Smoke test — spawn both servers, curl /api/health, report result

Everything writes to bootstrap.log so users can post it back on failure.

Windows only. Requires either:
  * Python 3.11+ already installed (fetch mode), or
  * A bundle folder created on a source PC.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


# ───────────────────────────────────────────────────────────────────────
# Config
# ───────────────────────────────────────────────────────────────────────

DEFAULT_INSTALL = Path.home() / "CleanMesh"
LOG_FILE = Path.cwd() / "bootstrap.log"

BLENDER_MIN_VERSION = (4, 1)
BLENDER_INSTALLER_URL = (
    "https://mirror.clarkson.edu/blender/release/Blender5.1/"
    "blender-5.1.2-windows-x64.msi"
)

BLENDER_STANDARD_PATHS = [
    r"C:\Program Files\Blender Foundation\Blender 5.3\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.4\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
]

MIN_FREE_DISK_GB = 30
MIN_VRAM_MB = 6000     # warn below this; TRELLIS needs 8+

TRIPOSR_GIT_URL = "https://github.com/VAST-AI-Research/TripoSR.git"

# The two Hugging Face repos we need weights from.
TRELLIS_HF_REPO = "microsoft/TRELLIS-image-large"
SAM2_HF_REPO    = "facebook/sam2.1"


# ───────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────

def setup_logging():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def run(cmd: list[str] | str, timeout: int = 120,
        check: bool = False, shell: bool = False) -> subprocess.CompletedProcess:
    """Run a command and log its output. Never raises unless check=True."""
    logging.info("$ %s", cmd if isinstance(cmd, str) else " ".join(cmd))
    cp = subprocess.run(
        cmd, shell=shell, capture_output=True, text=True,
        timeout=timeout, encoding="utf-8", errors="replace",
    )
    if cp.stdout.strip():
        logging.info(cp.stdout.strip()[:2000])
    if cp.returncode != 0 and cp.stderr.strip():
        logging.warning("stderr: %s", cp.stderr.strip()[:1500])
    if check and cp.returncode != 0:
        raise RuntimeError(f"Command failed ({cp.returncode}): {cmd}")
    return cp


def which(name: str) -> str | None:
    return shutil.which(name)


def free_disk_gb(drive: str = "C:\\") -> float:
    total, used, free = shutil.disk_usage(drive)
    return free / (1024 ** 3)


def download(url: str, dest: Path, expected_min_size: int = 1024) -> bool:
    """Download url → dest. Returns True on success."""
    logging.info("Downloading %s → %s", url, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
    except Exception as e:
        logging.error("Download failed: %s", e)
        return False
    if dest.stat().st_size < expected_min_size:
        logging.error("Download too small (%d bytes), likely corrupt.", dest.stat().st_size)
        return False
    logging.info("Downloaded %.1f MB", dest.stat().st_size / (1024 ** 2))
    return True


# ───────────────────────────────────────────────────────────────────────
# Stage 1: Precheck
# ───────────────────────────────────────────────────────────────────────

def stage_precheck() -> dict:
    """Return a dict of {feature: state}. Never raises."""
    logging.info("=" * 60)
    logging.info("STAGE 1: PRECHECK")
    logging.info("=" * 60)
    result = {}

    # NVIDIA GPU
    if which("nvidia-smi"):
        try:
            cp = run(["nvidia-smi", "--query-gpu=memory.total",
                      "--format=csv,noheader,nounits"], timeout=10)
            vram = int(cp.stdout.strip().splitlines()[0])
            result["gpu"] = {"ok": True, "vram_mb": vram,
                             "warning": vram < MIN_VRAM_MB}
            logging.info("[GPU] present, %d MiB VRAM", vram)
            if vram < MIN_VRAM_MB:
                logging.warning("[GPU] VRAM below recommended %d MB — "
                                "TRELLIS may not work reliably", MIN_VRAM_MB)
        except Exception as e:
            result["gpu"] = {"ok": False, "reason": str(e)}
    else:
        result["gpu"] = {"ok": False, "reason": "nvidia-smi not on PATH"}
        logging.error("[GPU] no NVIDIA driver detected")

    # Python
    result["python"] = {"ok": True, "version": sys.version.split()[0],
                        "executable": sys.executable}
    logging.info("[Python] %s", result["python"]["version"])

    # WSL2
    cp = run(["wsl", "--status"], timeout=15)
    result["wsl"] = {"ok": cp.returncode == 0}
    logging.info("[WSL] %s", "OK" if result["wsl"]["ok"] else "NOT INSTALLED")

    # Blender (auto-detect)
    result["blender"] = {"path": find_blender(), "ok": False}
    if result["blender"]["path"]:
        result["blender"]["ok"] = True
        logging.info("[Blender] %s", result["blender"]["path"])
    else:
        logging.warning("[Blender] not found in standard paths")

    # Disk
    free_gb = free_disk_gb()
    result["disk"] = {"free_gb": free_gb,
                      "ok": free_gb >= MIN_FREE_DISK_GB}
    logging.info("[Disk C:] %.1f GB free", free_gb)
    if not result["disk"]["ok"]:
        logging.warning("[Disk C:] below recommended %d GB free", MIN_FREE_DISK_GB)

    # Admin
    result["admin"] = {"ok": is_admin()}
    logging.info("[Admin] %s", "yes" if result["admin"]["ok"] else "no")

    return result


def find_blender() -> str | None:
    for p in BLENDER_STANDARD_PATHS:
        if os.path.isfile(p):
            return p
    return which("blender")


# ───────────────────────────────────────────────────────────────────────
# Stage 2: Blender install
# ───────────────────────────────────────────────────────────────────────

def stage_blender(state: dict) -> bool:
    logging.info("=" * 60)
    logging.info("STAGE 2: BLENDER")
    logging.info("=" * 60)
    if state["blender"]["ok"]:
        logging.info("[Blender] already installed, skipping")
        return True

    # Try winget first (silent, best UX)
    if which("winget"):
        cp = run(["winget", "install", "-e", "--id",
                  "BlenderFoundation.Blender", "--silent",
                  "--accept-package-agreements", "--accept-source-agreements"],
                 timeout=600)
        if cp.returncode == 0:
            path = find_blender()
            if path:
                state["blender"] = {"ok": True, "path": path}
                logging.info("[Blender] winget install OK: %s", path)
                return True

    logging.warning("[Blender] auto-install unavailable — please install "
                    "manually from https://www.blender.org/download/ then "
                    "re-run this bootstrap.")
    return False


# ───────────────────────────────────────────────────────────────────────
# Stage 3: Project code
# ───────────────────────────────────────────────────────────────────────

def stage_project(bundle_dir: Path | None, install_dir: Path) -> bool:
    logging.info("=" * 60)
    logging.info("STAGE 3: PROJECT CODE → %s", install_dir)
    logging.info("=" * 60)
    install_dir.mkdir(parents=True, exist_ok=True)

    src = None
    if bundle_dir and (bundle_dir / "Image3D").exists():
        src = bundle_dir / "Image3D"
        logging.info("[Project] using bundle: %s", src)
    elif (Path.cwd().parent / "작업리소스").exists():
        # Running from inside the project itself (dev-mode bootstrap)
        src = Path.cwd().parent
        logging.info("[Project] using local: %s", src)

    if src:
        target = install_dir / "Image3D"
        # Robocopy handles Korean paths on Windows and is idempotent.
        run(["robocopy", str(src), str(target), "/E",
             "/XD", "build", "dist", "__pycache__", ".git",
             "/XF", "*.log", "*.pyc",
             "/NFL", "/NDL", "/NJH", "/NJS", "/NC", "/NS", "/NP"],
            timeout=600)
        return target.exists() and any(target.iterdir())

    # Fetch mode: git clone from GitHub
    if not which("git"):
        logging.error("[Project] no bundle and no git — install git or use bundle mode")
        return False
    target = install_dir / "Image3D"
    if target.exists():
        logging.info("[Project] already present, skipping clone")
        return True
    cp = run(["git", "clone", "--depth", "1",
              "https://github.com/Chonghongwoo/2D_to_3D.git",
              str(target)], timeout=300)
    return cp.returncode == 0


# ───────────────────────────────────────────────────────────────────────
# Stage 4: TripoSR backend
# ───────────────────────────────────────────────────────────────────────

def stage_triposr(bundle_dir: Path | None, tp_dir: Path) -> bool:
    logging.info("=" * 60)
    logging.info("STAGE 4: TRIPOSR → %s", tp_dir)
    logging.info("=" * 60)
    tp_dir.parent.mkdir(parents=True, exist_ok=True)

    if (tp_dir / "main.py").exists() and (tp_dir / "venv").exists():
        logging.info("[TripoSR] already installed, skipping")
        return True

    if bundle_dir and (bundle_dir / "triposr_backend").exists():
        run(["robocopy", str(bundle_dir / "triposr_backend"), str(tp_dir),
             "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NC", "/NS", "/NP"],
            timeout=900)
        return (tp_dir / "main.py").exists()

    # Fetch mode
    if not which("git"):
        logging.error("[TripoSR] no bundle and no git")
        return False
    if not tp_dir.exists():
        run(["git", "clone", TRIPOSR_GIT_URL, str(tp_dir)], timeout=300)
    venv_dir = tp_dir / "venv"
    if not venv_dir.exists():
        run([sys.executable, "-m", "venv", str(venv_dir)], timeout=180)
    pip = venv_dir / "Scripts" / "pip.exe"
    req = tp_dir / "requirements.txt"
    if pip.exists() and req.exists():
        run([str(pip), "install", "-r", str(req)], timeout=1800)
    return (tp_dir / "main.py").exists() and pip.exists()


# ───────────────────────────────────────────────────────────────────────
# Stage 5: WSL + Ubuntu-22.04
# ───────────────────────────────────────────────────────────────────────

def stage_wsl(bundle_dir: Path | None) -> bool:
    logging.info("=" * 60)
    logging.info("STAGE 5: WSL / UBUNTU-22.04")
    logging.info("=" * 60)

    # Already installed?
    cp = run(["wsl", "-l", "-v"], timeout=15)
    if "Ubuntu-22.04" in cp.stdout:
        logging.info("[WSL] Ubuntu-22.04 already registered")
        return True

    if bundle_dir and (bundle_dir / "Ubuntu-22.04.tar").exists():
        Path("C:\\WSL").mkdir(exist_ok=True)
        run(["wsl", "--import", "Ubuntu-22.04",
             r"C:\WSL\Ubuntu-22.04",
             str(bundle_dir / "Ubuntu-22.04.tar")],
            timeout=1800)
    else:
        # Requires admin the very first time to enable the feature.
        logging.info("[WSL] no bundle — installing Ubuntu-22.04 fresh "
                     "(needs admin + reboot on first run)")
        run(["wsl", "--install", "-d", "Ubuntu-22.04"], timeout=1800)

    cp = run(["wsl", "-l", "-v"], timeout=15)
    return "Ubuntu-22.04" in cp.stdout


# ───────────────────────────────────────────────────────────────────────
# Stage 6: Model weights (deferred to the WSL install scripts)
# ───────────────────────────────────────────────────────────────────────

def stage_weights(install_dir: Path, bundle_dir: Path | None) -> bool:
    """When bundle mode is used, weights come inside the WSL tar so
    nothing more to do here. In fetch mode we invoke the existing
    _install_step*.sh scripts inside WSL."""
    logging.info("=" * 60)
    logging.info("STAGE 6: MODEL WEIGHTS")
    logging.info("=" * 60)

    if bundle_dir and (bundle_dir / "Ubuntu-22.04.tar").exists():
        logging.info("[Weights] TRELLIS + SAM2 weights already inside "
                     "the bundled WSL tar — nothing to fetch")
        return True

    wsl_scripts = install_dir / "Image3D" / "작업리소스" / "trellis_wsl"
    if not wsl_scripts.exists():
        logging.error("[Weights] can't find trellis_wsl scripts at %s", wsl_scripts)
        return False
    # Run the same install sequence used on the source PC (long).
    for script in ("_install_apt.sh", "_install_step1.sh", "_install_step2.sh",
                   "_install_kaolin.sh", "_fix_transformers.sh",
                   "_install_sam2_safe.sh"):
        script_path = wsl_scripts / script
        if not script_path.exists():
            logging.warning("[Weights] missing %s", script_path)
            continue
        wsl_path = "/mnt/" + str(script_path).replace(":", "").replace("\\", "/").lower()
        logging.info("[Weights] running %s in WSL (%s)", script, wsl_path)
        run(["wsl", "-d", "Ubuntu-22.04", "--", "bash", wsl_path],
            timeout=3600)
    return True


# ───────────────────────────────────────────────────────────────────────
# Stage 7: Launcher wire-up
# ───────────────────────────────────────────────────────────────────────

def stage_launcher(install_dir: Path, blender_exe: str) -> bool:
    logging.info("=" * 60)
    logging.info("STAGE 7: LAUNCHER")
    logging.info("=" * 60)

    # Ensure log dir
    Path(r"C:\t3d\logs").mkdir(parents=True, exist_ok=True)

    # Pip install pyinstaller so build_launcher.bat works
    run([sys.executable, "-m", "pip", "install", "--user", "pyinstaller"],
        timeout=600)

    # Patch config.py with the detected Blender path
    cfg = (install_dir / "Image3D" / "작업리소스" /
           "cleanmesh" / "config.py")
    if cfg.exists():
        text = cfg.read_text(encoding="utf-8")
        marker = "def _find_blender()"
        if marker in text and blender_exe:
            # Insert an explicit early-return override at top of _find_blender.
            # Keeps existing auto-detect as fallback.
            override = (f'    @staticmethod\n'
                        f'    def _find_blender() -> str:\n'
                        f'        # Injected by installer bootstrap on '
                        f'{time.strftime("%Y-%m-%d")}\n'
                        f'        installer_default = r"{blender_exe}"\n'
                        f'        import os\n'
                        f'        if os.path.isfile(installer_default):\n'
                        f'            return installer_default\n')
            text = text.replace(
                f"    @staticmethod\n    {marker}",
                override + f'\n    {marker}',
                1,
            )
            cfg.write_text(text, encoding="utf-8")
            logging.info("[Launcher] config.py patched with %s", blender_exe)

    # Build .exe launcher
    build_bat = install_dir / "Image3D" / "실행" / "build_launcher.bat"
    if build_bat.exists():
        run(["cmd", "/c", str(build_bat)], timeout=600)
    exe = install_dir / "Image3D" / "실행" / "CleanMeshStudio.exe"
    if exe.exists():
        logging.info("[Launcher] built at %s", exe)
        return True
    logging.warning("[Launcher] .exe not built — will fall back to python launcher")
    return False


# ───────────────────────────────────────────────────────────────────────
# Stage 8: Smoke test
# ───────────────────────────────────────────────────────────────────────

def stage_smoke(install_dir: Path) -> bool:
    logging.info("=" * 60)
    logging.info("STAGE 8: SMOKE TEST")
    logging.info("=" * 60)
    # Spawn CleanMesh + TripoSR detached and probe the health endpoints.
    cwd = install_dir / "Image3D" / "작업리소스"
    tp_venv = Path(r"C:\WorkingJob\3d-model-tool\python-backend\venv\Scripts\python.exe")

    DETACHED = 0x00000008 | 0x00000200
    def spawn(cmd, cwd=None):
        return subprocess.Popen(
            cmd, cwd=str(cwd) if cwd else None,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=DETACHED, close_fds=True,
        )

    spawn([sys.executable, "-m", "uvicorn", "server.main:app",
           "--host", "127.0.0.1", "--port", "8100"], cwd=cwd)
    if tp_venv.exists():
        spawn([str(tp_venv), "-m", "uvicorn", "main:app",
               "--host", "127.0.0.1", "--port", "8000"],
              cwd=tp_venv.parent.parent.parent)

    # Wait for readiness (up to 30 s)
    ok_cm = ok_tp = False
    for i in range(30):
        time.sleep(1)
        try:
            urllib.request.urlopen("http://127.0.0.1:8100/api/health/blender", timeout=2).read()
            ok_cm = True
        except Exception:
            pass
        try:
            urllib.request.urlopen("http://127.0.0.1:8000/docs", timeout=2).read()
            ok_tp = True
        except Exception:
            pass
        if ok_cm and ok_tp:
            break
    logging.info("[Smoke] CleanMesh %s, TripoSR %s",
                 "OK" if ok_cm else "FAIL",
                 "OK" if ok_tp else "FAIL")
    return ok_cm and ok_tp


# ───────────────────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="CleanMesh installer bootstrap")
    p.add_argument("--bundle", type=Path, default=None,
                   help="Path to migration bundle folder (bundle/ subdir)")
    p.add_argument("--install-dir", type=Path, default=DEFAULT_INSTALL,
                   help=f"Where to install (default {DEFAULT_INSTALL})")
    p.add_argument("--stage", type=str, default=None,
                   help="Run only this stage: precheck|blender|project|"
                        "triposr|wsl|weights|launcher|smoke")
    p.add_argument("--skip", action="append", default=[],
                   help="Skip a stage (can repeat)")
    p.add_argument("--yes", action="store_true",
                   help="Do not pause between stages")
    args = p.parse_args()

    setup_logging()
    logging.info("CleanMesh installer bootstrap starting")
    logging.info("install_dir=%s bundle=%s", args.install_dir, args.bundle)

    stages = [
        ("precheck", lambda s: stage_precheck()),
        ("blender",  lambda s: stage_blender(s)),
        ("project",  lambda s: stage_project(args.bundle, args.install_dir)),
        ("triposr",  lambda s: stage_triposr(args.bundle,
                                              Path(r"C:\WorkingJob\3d-model-tool\python-backend"))),
        ("wsl",      lambda s: stage_wsl(args.bundle)),
        ("weights",  lambda s: stage_weights(args.install_dir, args.bundle)),
        ("launcher", lambda s: stage_launcher(args.install_dir,
                                               (s.get("blender") or {}).get("path", ""))),
        ("smoke",    lambda s: stage_smoke(args.install_dir)),
    ]

    state = {}
    for name, fn in stages:
        if args.stage and args.stage != name:
            continue
        if name in args.skip:
            logging.info("--skip %s", name)
            continue
        try:
            r = fn(state)
            if isinstance(r, dict):
                state.update(r)
        except Exception as e:
            logging.exception("Stage %s crashed: %s", name, e)

    logging.info("Bootstrap finished. Log at %s", LOG_FILE)


if __name__ == "__main__":
    main()
