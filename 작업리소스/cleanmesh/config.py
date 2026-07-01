"""
CleanMesh Configuration — Global settings for paths, scales, and engine targets.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BlenderConfig:
    """Blender executable and script paths."""
    executable: str = ""  # Auto-detected or manually set
    scripts_dir: Path = Path(__file__).parent / "blender"
    timeout_seconds: int = 300  # 5 min max per Blender operation

    def __post_init__(self):
        if not self.executable:
            self.executable = self._find_blender()

    @staticmethod
    def _find_blender() -> str:
        """Auto-detect Blender installation.

        Order:
          1. Standard install paths for Blender 3.6 through 5.3, newest-first
             (both Program Files and Program Files (x86)).
          2. Any `blender.exe` on PATH.
          3. Glob for `C:\\Program Files\\Blender Foundation\\Blender *`
             to catch versions we didn't hard-code.
        """
        import glob
        candidates = []
        for base in (r"C:\Program Files\Blender Foundation",
                     r"C:\Program Files (x86)\Blender Foundation"):
            for ver in ("5.3", "5.2", "5.1", "5.0",
                        "4.4", "4.3", "4.2", "4.1", "4.0",
                        "3.6"):
                candidates.append(f"{base}\\Blender {ver}\\blender.exe")

        for path in candidates:
            if os.path.isfile(path):
                return path

        import shutil
        on_path = shutil.which("blender")
        if on_path:
            return on_path

        # Fallback: glob for any Blender N.M dir we didn't list
        for base in (r"C:\Program Files\Blender Foundation",
                     r"C:\Program Files (x86)\Blender Foundation"):
            for exe in glob.glob(f"{base}\\Blender *\\blender.exe"):
                if os.path.isfile(exe):
                    return exe

        raise FileNotFoundError(
            "Blender not found. Install Blender 4.x+ from blender.org, "
            "or set config.blender.executable manually."
        )


@dataclass
class TripoSRConfig:
    """TripoSR generation settings."""
    api_url: str = "http://localhost:8000"
    endpoint: str = "/generate-triposr"
    mc_resolution: int = 256
    chunk_size: int = 8192
    foreground_ratio: float = 0.85
    remove_background: bool = True
    bake_texture: bool = True
    texture_resolution: int = 1024
    timeout_seconds: int = 120
    # Path to the TripoSR backend (venv + main.py). Falls back to the
    # default install location. Override via environment variable
    # CLEANMESH_TRIPOSR_DIR for portable / non-standard installs.
    backend_dir: str = ""

    def __post_init__(self):
        if not self.backend_dir:
            self.backend_dir = os.environ.get(
                "CLEANMESH_TRIPOSR_DIR",
                r"C:\WorkingJob\3d-model-tool\python-backend",
            )


@dataclass
class TrellisConfig:
    """TRELLIS generation settings (Phase 2)."""
    enabled: bool = False
    install_path: str = ""
    fp16: bool = True  # Required for RTX 3070 8GB
    timeout_seconds: int = 600


@dataclass
class CleanupConfig:
    """Mesh cleanup parameters."""
    merge_threshold: float = 0.0001
    target_polys: Optional[int] = None  # None = no decimation
    auto_smooth: bool = True
    smart_uv_angle: float = 66.0
    smart_uv_margin: float = 0.02
    ensure_quads: bool = True


@dataclass
class ExportConfig:
    """Export settings."""
    format: str = "glb"  # glb, fbx, or usd
    engine: str = "omniverse"  # omniverse, twinmotion, bim, unity, godot, unreal


@dataclass
class RenderConfig:
    """Render verification settings."""
    resolution: int = 512
    engine: str = "BLENDER_EEVEE_NEXT"  # EEVEE for speed
    samples: int = 64
    views: list = field(default_factory=lambda: ["front", "back", "left", "right", "top"])


@dataclass
class PathConfig:
    """Directory paths."""
    output_root: Path = Path(r"C:\t3d")
    raw_dir: Path = Path(r"C:\t3d\raw")
    cleaned_dir: Path = Path(r"C:\t3d\cleaned")
    exports_dir: Path = Path(r"C:\t3d\exports")
    renders_dir: Path = Path(r"C:\t3d\renders")
    logs_dir: Path = Path(r"C:\t3d\logs")

    def ensure_dirs(self):
        """Create all directories if they don't exist."""
        for d in [self.output_root, self.raw_dir, self.cleaned_dir,
                  self.exports_dir, self.renders_dir, self.logs_dir]:
            d.mkdir(parents=True, exist_ok=True)


@dataclass
class Config:
    """Master configuration for CleanMesh pipeline."""
    blender: BlenderConfig = field(default_factory=BlenderConfig)
    triposr: TripoSRConfig = field(default_factory=TripoSRConfig)
    trellis: TrellisConfig = field(default_factory=TrellisConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    paths: PathConfig = field(default_factory=PathConfig)

    def __post_init__(self):
        self.paths.ensure_dirs()


# Global default config — lazy-loaded to avoid Blender detection at import time
_config: Optional[Config] = None


def get_config() -> Config:
    """Get or create the global config singleton."""
    global _config
    if _config is None:
        _config = Config()
    return _config


def set_config(config: Config):
    """Override the global config."""
    global _config
    _config = config
