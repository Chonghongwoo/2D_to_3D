"""
CleanMesh Procedural Generator — Creates mesh objects via Blender bpy headless scripts.

Launches Blender in background mode with procedural template scripts for
regular/mechanical objects like drums, pallets, boxes, shelves, and conveyors.
"""

import json
import subprocess
from ..subproc import run as _hidden_run
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime

from ..config import get_config

logger = logging.getLogger(__name__)


# Template script filenames
TEMPLATE_SCRIPTS = {
    "drum_200l": "drum_200l.py",
    "pallet_eur": "pallet_eur.py",
    "box_cargo": "box_cargo.py",
    "shelf_rack": "shelf_rack.py",
    "conveyor_roller": "conveyor_roller.py",
}


def generate(
    template_name: str,
    params: Optional[dict] = None,
    output_path: Optional[str] = None,
) -> dict:
    """
    Generate a 3D model using a procedural Blender template.

    Args:
        template_name: Name of the template (e.g., "drum_200l")
        params: Template-specific parameters (dimensions, colors, etc.)
        output_path: Output GLB file path. Auto-generated if None.

    Returns:
        dict with status, output_path, and mesh statistics
    """
    config = get_config()
    params = params or {}

    # Validate template
    if template_name not in TEMPLATE_SCRIPTS:
        return {
            "status": "error",
            "message": f"Unknown template: {template_name}. Available: {list(TEMPLATE_SCRIPTS.keys())}"
        }

    # Determine output path
    if not output_path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(config.paths.raw_dir / f"{template_name}_{timestamp}.glb")

    # Build Blender command
    script_path = config.blender.scripts_dir / "templates" / TEMPLATE_SCRIPTS[template_name]

    if not script_path.exists():
        return {
            "status": "error",
            "message": f"Template script not found: {script_path}"
        }

    cmd = [
        config.blender.executable,
        "--background",
        "--python", str(script_path),
        "--",
        "--output", output_path,
    ]

    # Add template-specific params.
    # Boolean values map to argparse store_true flags (no value), e.g. has_lid=True → "--has-lid".
    # has_lid=False is dropped (flag absence = false). Other values are stringified.
    for key, value in params.items():
        flag = f"--{key.replace('_', '-')}"
        if isinstance(value, bool):
            if value:
                cmd.append(flag)
        elif value is not None:
            cmd.extend([flag, str(value)])

    logger.info(f"🔧 절차적 생성: {template_name} → {output_path}")
    logger.debug(f"명령어: {' '.join(cmd)}")

    try:
        result = _hidden_run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.blender.timeout_seconds,
            encoding="utf-8",
            errors="replace",
        )

        # Parse RESULT: JSON from stdout
        for line in result.stdout.split("\n"):
            line = line.strip()
            if line.startswith("RESULT:"):
                return json.loads(line[7:])

        # If no RESULT: line found, check for errors
        if result.returncode != 0:
            return {
                "status": "error",
                "message": f"Blender exited with code {result.returncode}",
                "stderr": result.stderr[-2000:] if result.stderr else "",
            }

        return {
            "status": "warning",
            "message": "Blender completed but no RESULT line found",
            "stdout": result.stdout[-2000:] if result.stdout else "",
            "output_path": output_path,
        }

    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "message": f"Blender timed out after {config.blender.timeout_seconds}s",
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to run Blender: {str(e)}",
        }


def list_templates() -> dict:
    """List all available procedural templates."""
    return {
        name: {
            "script": TEMPLATE_SCRIPTS[name],
            "exists": (get_config().blender.scripts_dir / "templates" / TEMPLATE_SCRIPTS[name]).exists(),
        }
        for name in TEMPLATE_SCRIPTS
    }
