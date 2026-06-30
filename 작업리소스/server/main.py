"""
CleanMesh FastAPI Server — REST API for the 3D generation pipeline.

Endpoints:
    POST /api/generate           — Image upload → 3D model
    POST /api/generate-procedural — Procedural object (params)
    GET  /api/status/{job_id}    — Job status
    GET  /api/download/{job_id}  — Download result
    GET  /api/renders/{job_id}   — Render verification images
    GET  /api/templates          — List available templates
    GET  /api/health             — Health check
"""

import os
import sys
import json
import uuid
import socket
import asyncio
import logging
from pathlib import Path
from typing import Optional, List
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# Blender MCP addon socket (ahujasid/blender-mcp default port)
BLENDER_MCP_HOST = "127.0.0.1"
BLENDER_MCP_PORT = 9876

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cleanmesh.pipeline import CleanMeshPipeline
from cleanmesh.config import get_config
from cleanmesh.generators.procedural import list_templates
from cleanmesh.generators.triposr import check_server as check_triposr
from cleanmesh.segment import segment_image

from .schemas import (
    GenerateRequest,
    ProceduralRequest,
    JobStatus,
    HealthResponse,
)

# ─── App Setup ─── #
app = FastAPI(
    title="CleanMesh API",
    description="Image-to-3D pipeline with automated mesh cleanup",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Thread pool for blocking pipeline operations
executor = ThreadPoolExecutor(max_workers=2)

# In-memory job store
jobs: dict = {}

# In-memory segment session store. Each entry:
#   { id, dir: Path, images: [{idx, src, cutout, mask, score, bbox, w, h}], created_at }
segment_sessions: dict = {}


# ─── Lifecycle ─── #
@app.on_event("startup")
async def startup():
    config = get_config()
    logger.info(f"🚀 CleanMesh API starting")
    logger.info(f"   Blender: {config.blender.executable}")
    logger.info(f"   Output:  {config.paths.output_root}")


# ─── Endpoints ─── #

@app.get("/api/health")
async def health():
    """Health check with component status."""
    config = get_config()
    blender_ok = os.path.isfile(config.blender.executable)
    triposr_ok = check_triposr()

    return HealthResponse(
        status="ok" if blender_ok else "degraded",
        blender_available=blender_ok,
        blender_version=config.blender.executable,
        triposr_available=triposr_ok,
        trellis_available=config.trellis.enabled,
    )


@app.get("/api/templates")
async def templates():
    """List available procedural generation templates."""
    return list_templates()


@app.post("/api/generate")
async def generate(
    file: UploadFile = File(...),
    quality: str = Form("speed"),
    description: Optional[str] = Form(None),
    object_name: Optional[str] = Form(None),
    target_polys: Optional[int] = Form(None),
    engine: Optional[str] = Form(None),
    skip_cleanup: bool = Form(False),
    skip_render: bool = Form(False),
    color_mode: str = Form("vertex"),
    color_k: int = Form(4),
    # DT-friendly defaults: light smoothing to preserve surface detail
    color_smooth_iters: int = Form(2),
    color_label_smooth_iters: int = Form(1),
    color_min_region_size: int = Form(30),
    # Digital Twin metadata + export format (v1.2)
    export_format: str = Form("glb"),
    dt_category: Optional[str] = Form(None),
    dt_dimensions_mm: Optional[str] = Form(None),  # JSON string
    dt_manufacturer: Optional[str] = Form(None),
    dt_serial: Optional[str] = Form(None),
    simready: bool = Form(False),
):
    """
    Generate a 3D model from an uploaded image.

    Upload an image and get back a clean, digital-twin-ready 3D asset
    (GLB / USD / FBX) with optional metadata (category, dimensions, manufacturer).
    """
    job_id = str(uuid.uuid4())[:8]
    config = get_config()

    # Save uploaded file
    upload_dir = config.paths.raw_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    image_path = upload_dir / f"{job_id}_{file.filename}"

    with open(image_path, "wb") as f:
        content = await f.read()
        f.write(content)

    # Create job
    jobs[job_id] = {
        "id": job_id,
        "status": "queued",
        "created_at": datetime.now().isoformat(),
        "image_path": str(image_path),
    }

    dt_meta = _build_dt_meta(dt_category, dt_dimensions_mm, dt_manufacturer, dt_serial,
                              source_image=file.filename)
    # Run pipeline in background
    asyncio.get_event_loop().run_in_executor(
        executor,
        _run_pipeline_job,
        job_id,
        str(image_path),
        description,
        quality,
        object_name,
        target_polys,
        engine,
        skip_cleanup,
        skip_render,
        color_mode,
        color_k,
        color_smooth_iters,
        color_label_smooth_iters,
        color_min_region_size,
        export_format,
        dt_meta,
        simready,
    )

    return {"job_id": job_id, "status": "queued", "message": "파이프라인 시작됨"}


@app.post("/api/generate-procedural")
async def generate_procedural(
    template: str = Form(...),
    params: Optional[str] = Form(None),  # JSON string
    engine: Optional[str] = Form(None),
):
    """
    Generate a 3D model using procedural templates.

    Available templates: drum_200l, pallet_eur, box_cargo, shelf_rack, conveyor_roller
    """
    import json as json_lib

    job_id = str(uuid.uuid4())[:8]

    # Parse params JSON
    template_params = {}
    if params:
        try:
            template_params = json_lib.loads(params)
        except json_lib.JSONDecodeError:
            raise HTTPException(400, "Invalid params JSON")

    jobs[job_id] = {
        "id": job_id,
        "status": "queued",
        "created_at": datetime.now().isoformat(),
        "template": template,
    }

    asyncio.get_event_loop().run_in_executor(
        executor,
        _run_procedural_job,
        job_id,
        template,
        template_params,
        engine,
    )

    return {"job_id": job_id, "status": "queued", "message": f"절차적 생성 시작: {template}"}


@app.get("/api/status/{job_id}")
async def status(job_id: str):
    """Check job status."""
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    return jobs[job_id]


@app.get("/api/download/{job_id}")
async def download(job_id: str):
    """Download the generated 3D model."""
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")

    job = jobs[job_id]
    if job["status"] != "completed":
        raise HTTPException(400, f"Job not ready: {job['status']}")

    output_path = job.get("output_path")
    if not output_path or not os.path.isfile(output_path):
        raise HTTPException(404, "Output file not found")

    return FileResponse(
        output_path,
        media_type="model/gltf-binary",
        filename=os.path.basename(output_path),
    )


# ─── Blender MCP socket helpers ─── #

def _blender_socket_send(payload: dict, timeout: float = 10.0) -> dict:
    """Send one JSON command to Blender MCP addon and return its response.

    Protocol (ahujasid/blender-mcp BlenderMCPServer): raw JSON, no trailing newline,
    server reads until JSON is complete, replies with one JSON object.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((BLENDER_MCP_HOST, BLENDER_MCP_PORT))
        s.sendall(json.dumps(payload).encode("utf-8"))
        # Drain until socket closes or large chunk received
        chunks = []
        while True:
            try:
                buf = s.recv(8192)
            except socket.timeout:
                break
            if not buf:
                break
            chunks.append(buf)
            # Try to parse — if complete, stop early
            try:
                return json.loads(b"".join(chunks).decode("utf-8"))
            except json.JSONDecodeError:
                continue
        raw = b"".join(chunks).decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {"status": "error", "message": "no data"}
    finally:
        s.close()


def _blender_alive() -> bool:
    """Quick TCP probe — does anything answer on the Blender MCP port?"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect((BLENDER_MCP_HOST, BLENDER_MCP_PORT))
        s.close()
        return True
    except Exception:
        return False


@app.get("/api/health/blender")
async def health_blender():
    """Check Blender MCP addon socket and report scene info if alive."""
    alive = _blender_alive()
    if not alive:
        return {"alive": False, "message": "Blender MCP addon not reachable on port 9876"}
    try:
        resp = _blender_socket_send({"type": "get_scene_info", "params": {}}, timeout=5.0)
        return {"alive": True, "scene": resp.get("result", resp)}
    except Exception as e:
        return {"alive": True, "scene_query_failed": str(e)}


@app.post("/api/import-to-blender")
async def import_to_blender(job_id: str = Form(...)):
    """Push a completed job's GLB into the connected Blender instance."""
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    job = jobs[job_id]
    if job["status"] != "completed":
        raise HTTPException(400, f"Job not ready: {job['status']}")

    glb = job.get("output_path")
    if not glb or not os.path.isfile(glb):
        raise HTTPException(404, "Output file missing")

    if not _blender_alive():
        raise HTTPException(503, "Blender MCP addon not running (port 9876)")

    # Forward-slash path for Python literal
    glb_lit = glb.replace("\\", "/")
    code = (
        "import bpy\n"
        "bpy.ops.object.select_all(action='SELECT')\n"
        "bpy.ops.object.delete(use_global=False)\n"
        "for b in list(bpy.data.meshes): bpy.data.meshes.remove(b)\n"
        "for b in list(bpy.data.materials): bpy.data.materials.remove(b)\n"
        f"bpy.ops.import_scene.gltf(filepath=r'{glb_lit}')\n"
        "obj = next((o for o in bpy.data.objects if o.type=='MESH'), None)\n"
        "if obj:\n"
        "    bpy.context.view_layer.objects.active = obj\n"
        "    obj.select_set(True)\n"
        "    for area in bpy.context.screen.areas:\n"
        "        if area.type=='VIEW_3D':\n"
        "            for region in area.regions:\n"
        "                if region.type=='WINDOW':\n"
        "                    with bpy.context.temp_override(area=area, region=region):\n"
        "                        bpy.ops.view3d.view_selected()\n"
        "                        area.spaces.active.shading.type='MATERIAL'\n"
    )

    try:
        resp = _blender_socket_send(
            {"type": "execute_code", "params": {"code": code}}, timeout=30.0,
        )
        return {"status": "imported", "blender_response": resp}
    except Exception as e:
        raise HTTPException(502, f"Blender command failed: {e}")


@app.post("/api/generate-multi")
async def generate_multi(
    files: List[UploadFile] = File(...),
    quality: str = Form("quality"),
    description: Optional[str] = Form(None),
    object_name: Optional[str] = Form(None),
    target_polys: Optional[int] = Form(None),
    engine: Optional[str] = Form(None),
    skip_cleanup: bool = Form(False),
    skip_render: bool = Form(False),
    color_mode: str = Form("vertex"),
    color_k: int = Form(4),
    # DT-friendly defaults: light smoothing to preserve surface detail
    color_smooth_iters: int = Form(2),
    color_label_smooth_iters: int = Form(1),
    color_min_region_size: int = Form(30),
    # Digital Twin metadata + export format (v1.2)
    export_format: str = Form("glb"),
    dt_category: Optional[str] = Form(None),
    dt_dimensions_mm: Optional[str] = Form(None),  # JSON string
    dt_manufacturer: Optional[str] = Form(None),
    dt_serial: Optional[str] = Form(None),
    simready: bool = Form(False),
):
    """Multi-image generation (routes to TRELLIS_MULTI when >=2 images)."""
    if not files:
        raise HTTPException(400, "no files uploaded")

    job_id = str(uuid.uuid4())[:8]
    config = get_config()
    upload_dir = config.paths.raw_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    image_paths = []
    for i, f in enumerate(files):
        dst = upload_dir / f"{job_id}_{i:02d}_{f.filename}"
        with open(dst, "wb") as out:
            out.write(await f.read())
        image_paths.append(str(dst))

    jobs[job_id] = {
        "id": job_id,
        "status": "queued",
        "created_at": datetime.now().isoformat(),
        "image_paths": image_paths,
    }

    dt_meta = _build_dt_meta(dt_category, dt_dimensions_mm, dt_manufacturer, dt_serial,
                              source_image=files[0].filename if files else None)
    asyncio.get_event_loop().run_in_executor(
        executor,
        _run_pipeline_job_multi,
        job_id,
        image_paths,
        description,
        quality,
        object_name,
        target_polys,
        engine,
        skip_cleanup,
        skip_render,
        color_mode,
        color_k,
        color_smooth_iters,
        color_label_smooth_iters,
        color_min_region_size,
        export_format,
        dt_meta,
        simready,
    )

    return {"job_id": job_id, "status": "queued", "image_count": len(image_paths)}


@app.get("/api/renders/{job_id}")
async def renders(job_id: str):
    """Get render verification images for a job."""
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")

    job = jobs[job_id]
    render_info = job.get("result", {}).get("stages", {}).get("render", {})

    if not render_info or render_info.get("status") != "success":
        raise HTTPException(404, "Renders not available")

    return render_info


# ─── Background Workers ─── #

def _build_dt_meta(category, dimensions_mm_raw, manufacturer, serial, source_image=None):
    """Build DT metadata dict from raw form fields. dimensions_mm_raw is a JSON string."""
    meta = {}
    if category:     meta["category"]      = category
    if manufacturer: meta["manufacturer"]  = manufacturer
    if serial:       meta["serial_number"] = serial
    if source_image: meta["source_image"]  = source_image
    if dimensions_mm_raw:
        try:
            meta["dimensions_mm"] = json.loads(dimensions_mm_raw)
        except Exception:
            try:
                meta["dimensions_mm"] = [float(x) for x in dimensions_mm_raw.replace(",", " ").split()]
            except Exception:
                pass
    return meta or None


def _run_pipeline_job(
    job_id, image_path, description, quality,
    object_name, target_polys, engine,
    skip_cleanup, skip_render,
    color_mode="vertex", color_k=4,
    color_smooth_iters=2, color_label_smooth_iters=1, color_min_region_size=30,
    export_format="glb", dt_meta=None, simready=False,
):
    """Run the full pipeline in a background thread."""
    try:
        jobs[job_id]["status"] = "running"
        pipeline = CleanMeshPipeline()

        result = pipeline.run(
            image_path=image_path,
            description=description,
            quality=quality,
            object_name=object_name,
            target_polys=target_polys,
            engine_target=engine,
            skip_cleanup=skip_cleanup,
            skip_render=skip_render,
            color_mode=color_mode,
            color_k=color_k,
            color_smooth_iters=color_smooth_iters,
            color_label_smooth_iters=color_label_smooth_iters,
            color_min_region_size=color_min_region_size,
            export_format=export_format,
            dt_meta=dt_meta,
            simready=simready,
        )

        jobs[job_id]["status"] = "completed" if result["status"] == "success" else "failed"
        jobs[job_id]["result"] = result
        jobs[job_id]["output_path"] = result.get("output_path")
        jobs[job_id]["report"] = result.get("report", "")

    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        logger.exception(f"Pipeline job {job_id} failed")


def _run_pipeline_job_multi(
    job_id, image_paths, description, quality,
    object_name, target_polys, engine,
    skip_cleanup, skip_render,
    color_mode="vertex", color_k=4,
    color_smooth_iters=2, color_label_smooth_iters=1, color_min_region_size=30,
    export_format="glb", dt_meta=None, simready=False,
):
    """Multi-image variant — uses image_paths kwarg so router picks TRELLIS_MULTI."""
    try:
        jobs[job_id]["status"] = "running"
        pipeline = CleanMeshPipeline()

        result = pipeline.run(
            image_paths=image_paths,
            description=description,
            quality=quality,
            object_name=object_name,
            target_polys=target_polys,
            engine_target=engine,
            skip_cleanup=skip_cleanup,
            skip_render=skip_render,
            color_mode=color_mode,
            color_k=color_k,
            color_smooth_iters=color_smooth_iters,
            color_label_smooth_iters=color_label_smooth_iters,
            color_min_region_size=color_min_region_size,
            export_format=export_format,
            dt_meta=dt_meta,
            simready=simready,
        )

        jobs[job_id]["status"] = "completed" if result.get("status") == "success" else "failed"
        jobs[job_id]["result"] = result
        jobs[job_id]["output_path"] = result.get("output_path")
        jobs[job_id]["report"] = result.get("report", "")

    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        logger.exception(f"Pipeline multi job {job_id} failed")


def _run_procedural_job(job_id, template, params, engine):
    """Run procedural generation in a background thread."""
    try:
        jobs[job_id]["status"] = "running"
        pipeline = CleanMeshPipeline()

        result = pipeline.run(
            description=f"{template}",
            force_method="procedural",
            engine_target=engine,
        )

        jobs[job_id]["status"] = "completed" if result["status"] == "success" else "failed"
        jobs[job_id]["result"] = result
        jobs[job_id]["output_path"] = result.get("output_path")
        jobs[job_id]["report"] = result.get("report", "")

    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        logger.exception(f"Procedural job {job_id} failed")


# ─── Segment (SAM2 click-to-segment) endpoints ─── #

def _segment_session_dir(sid: str) -> Path:
    config = get_config()
    return config.paths.raw_dir / "segment_sessions" / sid


@app.post("/api/segment-upload")
async def segment_upload(files: List[UploadFile] = File(...)):
    """Upload one or more images that will later be click-segmented with SAM2.

    Returns a session_id you pass back to /api/segment.
    """
    if not files:
        raise HTTPException(400, "no files uploaded")

    sid = str(uuid.uuid4())[:8]
    sdir = _segment_session_dir(sid)
    sdir.mkdir(parents=True, exist_ok=True)

    from PIL import Image as PILImage

    images = []
    for i, f in enumerate(files):
        # Force ascii filename to avoid wsl path quoting issues
        ext = os.path.splitext(f.filename or "")[1].lower() or ".png"
        if ext not in (".png", ".jpg", ".jpeg", ".webp"):
            ext = ".png"
        dst = sdir / f"src_{i:02d}{ext}"
        data = await f.read()
        with open(dst, "wb") as out:
            out.write(data)
        # Probe dimensions
        with PILImage.open(dst) as im:
            w, h = im.size
        images.append({
            "idx": i,
            "src":    str(dst),
            "url":    f"/api/segment/{sid}/{dst.name}",
            "width":  w,
            "height": h,
            "cutout": None,
            "mask":   None,
            "score":  None,
            "bbox":   None,
        })

    segment_sessions[sid] = {
        "id": sid,
        "dir": str(sdir),
        "images": images,
        "created_at": datetime.now().isoformat(),
    }
    logger.info(f"📷 segment session {sid}: {len(images)} image(s)")
    return {"session_id": sid, "images": images}


@app.post("/api/segment")
async def segment(payload: dict):
    """Run SAM2 with click prompts.

    Body:
        {
          "session_id": "abc12345",
          "image_index": 0,
          "points": [[x, y, label], ...],   # label: 1=fg, 0=bg
          "box":    [x1, y1, x2, y2]  | null,
          "feather": 2
        }
    """
    sid = payload.get("session_id")
    idx = int(payload.get("image_index", 0))
    points = payload.get("points") or []
    box    = payload.get("box")
    feather = int(payload.get("feather", 2))

    if sid not in segment_sessions:
        raise HTTPException(404, "session not found")
    sess = segment_sessions[sid]
    if not (0 <= idx < len(sess["images"])):
        raise HTTPException(400, f"image_index {idx} out of range")
    if not points and not box:
        raise HTTPException(400, "need at least one point or a box")

    rec = sess["images"][idx]
    sdir = Path(sess["dir"])
    cutout = sdir / f"cutout_{idx:02d}.png"
    mask   = sdir / f"mask_{idx:02d}.png"

    # Run in a thread (it shells out to wsl)
    loop = asyncio.get_event_loop()
    pts_tuples = [(int(p[0]), int(p[1]), int(p[2])) for p in points]
    box_tuple = tuple(box) if box else None

    def _do():
        return segment_image(
            image_path=Path(rec["src"]),
            output_path=cutout,
            mask_path=mask,
            points=pts_tuples,
            box=box_tuple,
            feather=feather,
            timeout_sec=120.0,
        )
    result = await loop.run_in_executor(executor, _do)

    if result.status != "ok":
        raise HTTPException(500, f"SAM2 failed: {result.error}\n---\n{result.stderr[-500:]}")

    # Stash on session
    rec["cutout"] = str(cutout)
    rec["mask"]   = str(mask)
    rec["score"]  = result.score
    rec["bbox"]   = list(result.bbox) if result.bbox else None

    return {
        "status": "ok",
        "cutout_url": f"/api/segment/{sid}/{cutout.name}?t={int(datetime.now().timestamp())}",
        "mask_url":   f"/api/segment/{sid}/{mask.name}?t={int(datetime.now().timestamp())}",
        "score": result.score,
        "bbox":  rec["bbox"],
    }


@app.get("/api/segment/{sid}/{filename}")
async def segment_file(sid: str, filename: str):
    """Serve files from a segment session directory."""
    if sid not in segment_sessions:
        raise HTTPException(404, "session not found")
    # Prevent path escape
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(400, "bad filename")
    fp = Path(segment_sessions[sid]["dir"]) / filename
    if not fp.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(fp, media_type="image/png")


@app.post("/api/generate-segmented")
async def generate_segmented(payload: dict):
    """Kick off the pipeline using the SAM2-masked cutouts of a session.

    Body:
        {
          "session_id":   "abc12345",
          "image_indices": [0, 1, 2],    # optional; default = all images that have a cutout
          "quality":      "quality",
          "description":  null,
          "object_name":  null,
          "target_polys": null,
          "engine":       null,
          "skip_cleanup": false,
          "skip_render":  false
        }
    """
    sid = payload.get("session_id")
    if sid not in segment_sessions:
        raise HTTPException(404, "session not found")
    sess = segment_sessions[sid]

    indices = payload.get("image_indices")
    if not indices:
        indices = [r["idx"] for r in sess["images"] if r.get("cutout")]
    if not indices:
        raise HTTPException(400, "no segmented cutouts in this session yet")

    cutout_paths = []
    for i in indices:
        rec = sess["images"][int(i)]
        if not rec.get("cutout") or not os.path.isfile(rec["cutout"]):
            raise HTTPException(400, f"image {i} has no cutout — call /api/segment first")
        cutout_paths.append(rec["cutout"])

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "id": job_id,
        "status": "queued",
        "created_at": datetime.now().isoformat(),
        "image_paths": cutout_paths,
        "segment_session": sid,
        "pre_masked": True,
    }

    # Build DT meta from JSON payload
    dims_raw = payload.get("dt_dimensions_mm")
    if isinstance(dims_raw, list):
        dims_str = json.dumps(dims_raw)
    elif isinstance(dims_raw, str):
        dims_str = dims_raw
    else:
        dims_str = None
    dt_meta = _build_dt_meta(
        payload.get("dt_category"),
        dims_str,
        payload.get("dt_manufacturer"),
        payload.get("dt_serial"),
        source_image=None,
    )
    asyncio.get_event_loop().run_in_executor(
        executor,
        _run_pipeline_job_segmented,
        job_id,
        cutout_paths,
        payload.get("description"),
        payload.get("quality", "quality"),
        payload.get("object_name"),
        payload.get("target_polys"),
        payload.get("engine"),
        bool(payload.get("skip_cleanup", False)),
        bool(payload.get("skip_render", False)),
        str(payload.get("color_mode", "vertex")),
        int(payload.get("color_k", 4)),
        int(payload.get("color_smooth_iters", 2)),
        int(payload.get("color_label_smooth_iters", 1)),
        int(payload.get("color_min_region_size", 30)),
        str(payload.get("export_format", "glb")),
        dt_meta,
        bool(payload.get("simready", False)),
    )

    return {"job_id": job_id, "status": "queued", "image_count": len(cutout_paths), "pre_masked": True}


def _run_pipeline_job_segmented(
    job_id, image_paths, description, quality,
    object_name, target_polys, engine,
    skip_cleanup, skip_render,
    color_mode="vertex", color_k=4,
    color_smooth_iters=2, color_label_smooth_iters=1, color_min_region_size=30,
    export_format="glb", dt_meta=None, simready=False,
):
    """Pipeline runner that flags inputs as pre-masked (SAM2 cutouts)."""
    try:
        jobs[job_id]["status"] = "running"
        pipeline = CleanMeshPipeline()
        result = pipeline.run(
            image_paths=image_paths,
            description=description,
            quality=quality,
            object_name=object_name,
            target_polys=target_polys,
            engine_target=engine,
            skip_cleanup=skip_cleanup,
            skip_render=skip_render,
            pre_masked=True,
            color_mode=color_mode,
            color_k=color_k,
            color_smooth_iters=color_smooth_iters,
            color_label_smooth_iters=color_label_smooth_iters,
            color_min_region_size=color_min_region_size,
            export_format=export_format,
            dt_meta=dt_meta,
            simready=simready,
        )
        jobs[job_id]["status"] = "completed" if result.get("status") == "success" else "failed"
        jobs[job_id]["result"] = result
        jobs[job_id]["output_path"] = result.get("output_path")
        jobs[job_id]["report"] = result.get("report", "")
    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        logger.exception(f"Segmented job {job_id} failed")


# ─── Static / images endpoints ─── #

@app.get("/api/contact-sheet/{job_id}")
async def contact_sheet(job_id: str):
    """Stream the contact_sheet.png for a finished job."""
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    job = jobs[job_id]
    renders = job.get("result", {}).get("stages", {}).get("render", {}).get("renders", [])
    cs = next((r for r in renders if r.endswith("contact_sheet.png")), None)
    if not cs or not os.path.isfile(cs):
        raise HTTPException(404, "Contact sheet not ready")
    return FileResponse(cs, media_type="image/png")


# Mount static frontend (must be last so it doesn't shadow /api/*)
_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")


# ─── Main ─── #
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)
