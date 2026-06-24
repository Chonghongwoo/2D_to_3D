"""
CleanMesh TripoSR Generator — Single-image 3D reconstruction via TripoSR.

Uses the existing TripoSR FastAPI server at C:\\WorkingJob\\3d-model-tool\\python-backend\\
or falls back to direct model invocation.
"""

import json
import logging
import shutil
from pathlib import Path
from typing import Optional
from datetime import datetime

import requests

from ..config import get_config

logger = logging.getLogger(__name__)


def generate(
    image_path: str,
    output_path: Optional[str] = None,
    remove_bg: Optional[bool] = None,
    mc_resolution: Optional[int] = None,
    bake_texture: Optional[bool] = None,
) -> dict:
    """
    Generate a 3D model from a single image using TripoSR.

    Args:
        image_path: Path to input image
        output_path: Output file path. Auto-generated if None.
        remove_bg: Whether to remove background (default from config)
        mc_resolution: Marching cubes resolution (default from config)
        bake_texture: Whether to bake textures (default from config)

    Returns:
        dict with status, output_path, and generation info
    """
    config = get_config()

    # Defaults from config
    if remove_bg is None:
        remove_bg = config.triposr.remove_background
    if mc_resolution is None:
        mc_resolution = config.triposr.mc_resolution
    if bake_texture is None:
        bake_texture = config.triposr.bake_texture

    # Validate input
    image_path = Path(image_path)
    if not image_path.exists():
        return {"status": "error", "message": f"Image not found: {image_path}"}

    # Determine output path
    if not output_path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(config.paths.raw_dir / f"triposr_{timestamp}.obj")

    logger.info(f"🚀 TripoSR 생성: {image_path.name} → {output_path}")

    # Try API-based generation first
    try:
        result = _generate_via_api(
            image_path=str(image_path),
            output_path=output_path,
            config=config,
            remove_bg=remove_bg,
            mc_resolution=mc_resolution,
        )
        if result["status"] == "success":
            return result
        logger.warning(f"API 호출 실패, 직접 호출 시도: {result.get('message', '')}")
    except Exception as e:
        logger.warning(f"API 서버 연결 실패: {e}")

    # Fallback: direct invocation via subprocess
    return _generate_direct(
        image_path=str(image_path),
        output_path=output_path,
        remove_bg=remove_bg,
        mc_resolution=mc_resolution,
    )


def _generate_via_api(
    image_path: str,
    output_path: str,
    config,
    remove_bg: bool,
    mc_resolution: int,
) -> dict:
    """Call existing TripoSR FastAPI server."""
    url = f"{config.triposr.api_url}{config.triposr.endpoint}"

    with open(image_path, "rb") as f:
        files = {"file": (Path(image_path).name, f, "image/png")}
        data = {
            "remove_bg": str(remove_bg).lower(),
            "mc_resolution": str(mc_resolution),
        }

        response = requests.post(
            url,
            files=files,
            data=data,
            timeout=config.triposr.timeout_seconds,
        )

    if response.status_code != 200:
        return {
            "status": "error",
            "message": f"API returned {response.status_code}: {response.text[:500]}",
        }

    # Server may return either a binary mesh file (FileResponse) or a JSON envelope.
    content_type = response.headers.get("content-type", "").lower()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if content_type.startswith("application/json"):
        # JSON envelope with file path or URL
        result = response.json()
        if "output_path" in result:
            server_output = Path(result["output_path"])
            if server_output.exists():
                shutil.copy2(str(server_output), output_path)
        elif "file_url" in result:
            file_response = requests.get(
                f"{config.triposr.api_url}{result['file_url']}",
                timeout=60,
            )
            with open(output_path, "wb") as f:
                f.write(file_response.content)
    else:
        # Binary mesh response (FileResponse) — write straight to disk
        with open(output_path, "wb") as f:
            f.write(response.content)

    if not Path(output_path).exists():
        return {
            "status": "error",
            "message": f"API call succeeded but no output file at {output_path}",
        }

    return {
        "status": "success",
        "method": "triposr_api",
        "output_path": output_path,
        "resolution": mc_resolution,
        "file_size_kb": Path(output_path).stat().st_size // 1024,
    }


def _generate_direct(
    image_path: str,
    output_path: str,
    remove_bg: bool,
    mc_resolution: int,
) -> dict:
    """
    Direct TripoSR invocation via subprocess.
    Uses the existing venv at C:\\WorkingJob\\3d-model-tool\\python-backend\\
    """
    import subprocess

    config = get_config()
    backend_dir = Path(r"C:\WorkingJob\3d-model-tool\python-backend")
    venv_python = backend_dir / "venv" / "Scripts" / "python.exe"

    if not venv_python.exists():
        return {
            "status": "error",
            "message": f"TripoSR venv not found at {venv_python}. Start the API server instead.",
        }

    # Build a small inline script to run TripoSR directly
    script = f"""
import sys
sys.path.insert(0, r'{backend_dir}')

from PIL import Image
from tsr.system import TSR
import torch
import trimesh
import json

device = 'cuda:0' if torch.cuda.is_available() else 'cpu'

# Load model
model = TSR.from_pretrained('stabilityai/TripoSR', config_name='config.yaml', weight_name='model.ckpt')
model.renderer.set_chunk_size({config.triposr.chunk_size})
model.to(device)

# Load and preprocess image
image = Image.open(r'{image_path}')

{'from tsr.utils import remove_background; image = remove_background(image)' if remove_bg else ''}

# Inference
with torch.no_grad():
    scene_codes = model(image, device=device)

# Extract mesh
meshes = model.extract_mesh(scene_codes, has_vertex_color=True, resolution={mc_resolution})
mesh = meshes[0]

# Export
mesh.export(r'{output_path}')

import os
size = os.path.getsize(r'{output_path}')
print(f'RESULT:{{"status":"success","method":"triposr_direct","output_path":"{output_path}","file_size_kb":{"{"}size//1024{"}"} }}')
"""

    try:
        result = subprocess.run(
            [str(venv_python), "-c", script],
            capture_output=True,
            text=True,
            timeout=config.triposr.timeout_seconds,
            cwd=str(backend_dir),
            encoding="utf-8",
            errors="replace",
        )

        for line in result.stdout.split("\n"):
            line = line.strip()
            if line.startswith("RESULT:"):
                return json.loads(line[7:])

        return {
            "status": "error",
            "message": f"Direct TripoSR failed (exit code {result.returncode})",
            "stderr": result.stderr[-2000:] if result.stderr else "",
        }

    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "TripoSR timed out"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def check_server() -> bool:
    """Check if the TripoSR API server is running."""
    config = get_config()
    try:
        response = requests.get(f"{config.triposr.api_url}/docs", timeout=3)
        return response.status_code == 200
    except Exception:
        return False
