"""
CleanMesh — Final Export Script
================================

Imports a cleaned mesh and exports it with engine-specific settings.

Usage:
    blender --background --python export.py -- \\
        --input <path>              Input mesh file (.glb/.gltf/.obj/.fbx) \\
        --output <path>             Output file path \\
        --format glb|fbx            Export format \\
        [--engine unity|godot|unreal]  Target game engine

Pipeline output is printed as a single JSON line prefixed with 'RESULT:'.
"""

import bpy
import sys
import os
import json
import math
import argparse


# ---------------------------------------------------------------------------
# Argument Parsing
# ---------------------------------------------------------------------------

def parse_args():
    """Parse CLI arguments after the '--' separator."""
    try:
        separator_index = sys.argv.index("--")
        script_argv = sys.argv[separator_index + 1:]
    except ValueError:
        print("ERROR: No '--' separator found in sys.argv.")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="CleanMesh final export"
    )
    parser.add_argument("--input", required=True, help="Input mesh file")
    parser.add_argument("--output", required=True, help="Output file path")
    parser.add_argument("--format", required=True, choices=["glb", "fbx", "usd"],
                        help="Export format (glb / fbx / usd)")
    parser.add_argument("--engine",
                        choices=["unity", "godot", "unreal",
                                 "omniverse", "twinmotion", "bim"],
                        default=None,
                        help="Target platform for axis/scale conversion. "
                             "Game: unity/godot/unreal. "
                             "Digital Twin: omniverse/twinmotion/bim.")
    # Optional DT metadata that gets embedded in GLB extras / USD attributes
    parser.add_argument("--meta-category",     default=None)
    parser.add_argument("--meta-dims-mm",      default=None,
                        help="JSON list [W, D, H] in mm")
    parser.add_argument("--meta-manufacturer", default=None)
    parser.add_argument("--meta-serial",       default=None)
    parser.add_argument("--meta-source-image", default=None)

    return parser.parse_args(script_argv)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def emit_result(data: dict):
    """Print a JSON result line for the pipeline."""
    print(f"RESULT:{json.dumps(data)}")


def emit_error(message: str):
    """Print error JSON and exit."""
    emit_result({"status": "error", "message": message})
    sys.exit(1)


def get_mesh_objects():
    return [obj for obj in bpy.data.objects if obj.type == "MESH"]


def get_mesh_stats():
    """Aggregate mesh statistics."""
    total_verts = 0
    total_faces = 0
    total_tris = 0
    mat_names = []

    for obj in get_mesh_objects():
        mesh = obj.data
        mesh.calc_loop_triangles()
        total_verts += len(mesh.vertices)
        total_faces += len(mesh.polygons)
        total_tris += len(mesh.loop_triangles)
        for slot in obj.material_slots:
            if slot.material and slot.material.name not in mat_names:
                mat_names.append(slot.material.name)

    return {
        "vertices": total_verts,
        "faces": total_faces,
        "tris": total_tris,
        "materials": mat_names,
    }


# ---------------------------------------------------------------------------
# Scene Management
# ---------------------------------------------------------------------------

def clear_scene():
    """Remove all objects."""
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=True)
    for block_coll in (bpy.data.meshes, bpy.data.materials,
                       bpy.data.textures, bpy.data.images):
        for block in block_coll:
            if block.users == 0:
                block_coll.remove(block)


def import_mesh(filepath: str):
    """Import mesh, detecting format by extension."""
    ext = os.path.splitext(filepath)[1].lower()

    importers = {
        ".glb":  lambda p: bpy.ops.import_scene.gltf(filepath=p),
        ".gltf": lambda p: bpy.ops.import_scene.gltf(filepath=p),
        ".obj":  lambda p: bpy.ops.wm.obj_import(filepath=p),
        ".fbx":  lambda p: bpy.ops.import_scene.fbx(filepath=p),
        ".ply":  lambda p: bpy.ops.wm.ply_import(filepath=p),
    }

    importer = importers.get(ext)
    if importer is None:
        emit_error(f"Unsupported file extension: {ext}")

    importer(filepath)

    if not get_mesh_objects():
        emit_error("No mesh objects found after import.")


# ---------------------------------------------------------------------------
# Engine-Specific Conversions
# ---------------------------------------------------------------------------

def apply_engine_conversion(engine: str | None):
    """
    Apply axis and scale conversions for the target engine.

    Blender native: +Z up, +Y forward, 1 unit = 1 m.
    GLB standard:   +Y up, +Z forward (handled by glTF exporter).

    - Unity:   +Y up — GLB default, no extra conversion needed.
    - Godot:   +Y up — GLB default, no extra conversion needed.
    - Unreal:  +Z up, centimeters — scale ×100.
    """
    if engine is None:
        return

    engine = engine.lower()

    if engine in ("unity", "godot"):
        # GLB exporter handles Y-up automatically; nothing extra needed.
        pass

    elif engine in ("omniverse", "twinmotion", "bim"):
        # Digital Twin platforms use real-world meters with Z-up (USD convention)
        # USD exporter handles axis natively. No scale change needed
        # — DT requires real-world units preserved.
        pass

    elif engine == "unreal":
        # Unreal uses centimeters — scale everything by 100
        for obj in get_mesh_objects():
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj

        for obj in get_mesh_objects():
            obj.scale = (obj.scale.x * 100,
                         obj.scale.y * 100,
                         obj.scale.z * 100)

        # Apply the scale transform
        bpy.ops.object.select_all(action="DESELECT")
        for obj in get_mesh_objects():
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
        bpy.ops.object.transform_apply(location=False, rotation=False,
                                        scale=True)


def apply_all_transforms():
    """Ensure all transforms are applied on every mesh object."""
    bpy.ops.object.select_all(action="DESELECT")
    for obj in get_mesh_objects():
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

    if get_mesh_objects():
        bpy.ops.object.transform_apply(location=True, rotation=True,
                                        scale=True)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _build_dt_metadata(args) -> dict:
    """Collect Digital Twin metadata from CLI args into a dict for embedding."""
    import datetime as _dt
    meta = {
        "pipeline": "CleanMesh Studio v1.2",
        "scan_timestamp": _dt.datetime.now().isoformat(timespec="seconds"),
    }
    if args.meta_category:     meta["category"]      = args.meta_category
    if args.meta_dims_mm:
        try:
            meta["dimensions_mm"] = json.loads(args.meta_dims_mm)
        except Exception:
            meta["dimensions_mm_raw"] = args.meta_dims_mm
    if args.meta_manufacturer: meta["manufacturer"]  = args.meta_manufacturer
    if args.meta_serial:       meta["serial_number"] = args.meta_serial
    if args.meta_source_image: meta["source_image"]  = args.meta_source_image
    return meta


def export_glb(output_path: str, engine: str | None, dt_meta: dict | None = None):
    """Export scene as GLB. Embeds dt_meta into glTF asset.extras when provided."""
    kwargs = {
        "filepath": output_path,
        "export_format": "GLB",
        "use_selection": False,
        "export_apply": True,
        "export_yup": True,
    }
    bpy.ops.export_scene.gltf(**kwargs)

    # Post-process: inject extras into the GLB JSON chunk
    if dt_meta:
        try:
            _inject_glb_extras(output_path, dt_meta)
        except Exception as e:
            print(f"[export] WARN: could not inject GLB extras: {e}")


def _inject_glb_extras(glb_path: str, extras: dict) -> None:
    """Re-write a GLB file with extras merged into asset.extras."""
    import struct
    with open(glb_path, "rb") as f:
        data = f.read()
    # GLB header: magic(4) + version(4) + length(4)
    if data[:4] != b"glTF":
        return
    version, total_len = struct.unpack_from("<II", data, 4)
    # First chunk
    chunk0_len = struct.unpack_from("<I", data, 12)[0]
    chunk0_type = data[16:20]
    if chunk0_type != b"JSON":
        return
    json_bytes = data[20:20 + chunk0_len]
    # Strip trailing padding spaces (glTF chunks are 4-byte aligned)
    json_text = json_bytes.decode("utf-8").rstrip()
    gltf = json.loads(json_text)
    gltf.setdefault("asset", {}).setdefault("extras", {}).update(extras)

    new_json = json.dumps(gltf, ensure_ascii=False).encode("utf-8")
    # 4-byte align with spaces
    pad = (-len(new_json)) % 4
    new_json += b" " * pad

    body = data[20 + chunk0_len:]  # rest of the GLB (BIN chunk etc.)
    new_total = 12 + 8 + len(new_json) + len(body)
    out = bytearray()
    out += b"glTF"
    out += struct.pack("<II", version, new_total)
    out += struct.pack("<I", len(new_json))
    out += b"JSON"
    out += new_json
    out += body
    with open(glb_path, "wb") as f:
        f.write(out)


def export_usd(output_path: str, engine: str | None, dt_meta: dict | None = None):
    """Export scene as USD (Omniverse / Twinmotion / pixar-compat).
    Embeds dt_meta as customLayerData when provided."""
    kwargs = {
        "filepath": output_path,
        "selected_objects_only": False,
        "export_materials": True,
        "export_uvmaps": True,
        "export_normals": True,
        "export_meshes": True,
        "use_instancing": False,
    }
    # Some Blender versions name options slightly differently — fall back gracefully
    try:
        bpy.ops.wm.usd_export(**kwargs)
    except TypeError:
        bpy.ops.wm.usd_export(filepath=output_path)

    if dt_meta:
        try:
            _inject_usd_metadata(output_path, dt_meta)
        except Exception as e:
            print(f"[export] WARN: could not inject USD metadata: {e}")


def _inject_usd_metadata(usd_path: str, meta: dict) -> None:
    """Append customLayerData to the .usd / .usda file. Works for .usda text;
    for binary .usdc this is a no-op (would need Pixar USD bindings)."""
    if not usd_path.lower().endswith((".usda", ".usd")):
        return  # binary usdc skipped
    try:
        with open(usd_path, "r", encoding="utf-8") as f:
            head = f.read(200)
    except UnicodeDecodeError:
        return  # binary file
    # text USD starts with "#usda 1.0"
    if not head.startswith("#usda"):
        return
    with open(usd_path, "r", encoding="utf-8") as f:
        content = f.read()
    # Insert customLayerData under the root layer metadata
    meta_lines = "\n".join(
        f'    string "{k}" = "{v}"' for k, v in meta.items()
        if isinstance(v, (str, int, float))
    )
    if "customLayerData = {" not in content:
        # naive insertion after #usda line
        lines = content.split("\n", 1)
        injection = f'(\n    customLayerData = {{\n{meta_lines}\n    }}\n)\n'
        content = lines[0] + "\n" + injection + (lines[1] if len(lines) > 1 else "")
        with open(usd_path, "w", encoding="utf-8") as f:
            f.write(content)


def export_fbx(output_path: str, engine: str | None):
    """Export scene as FBX with engine-specific settings."""
    kwargs = {
        "filepath": output_path,
        "use_selection": False,
        "apply_scale_options": "FBX_SCALE_ALL",
        "bake_space_transform": True,
        "mesh_smooth_type": "FACE",
        "add_leaf_bones": False,
    }

    engine = (engine or "").lower()

    if engine == "unity":
        # Unity expects Y-up, which FBX exporter does by default
        kwargs["axis_forward"] = "-Z"
        kwargs["axis_up"] = "Y"
        kwargs["apply_unit_scale"] = True

    elif engine == "unreal":
        # Unreal: X-forward, Z-up
        kwargs["axis_forward"] = "X"
        kwargs["axis_up"] = "Z"
        kwargs["apply_unit_scale"] = True

    elif engine == "godot":
        # Godot with FBX: Y-up (same as default)
        kwargs["axis_forward"] = "-Z"
        kwargs["axis_up"] = "Y"
        kwargs["apply_unit_scale"] = True

    bpy.ops.export_scene.fbx(**kwargs)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    input_path = os.path.abspath(args.input)
    output_path = os.path.abspath(args.output)
    export_format = args.format.lower()
    engine = args.engine

    if not os.path.isfile(input_path):
        emit_error(f"Input file not found: {input_path}")

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    try:
        # 1. Clear & import
        clear_scene()
        import_mesh(input_path)

        # 2. Engine-specific axis/scale conversion
        apply_engine_conversion(engine)

        # 3. Apply transforms
        apply_all_transforms()

        # 4. Export
        dt_meta = _build_dt_metadata(args)

        if export_format == "glb":
            export_glb(output_path, engine, dt_meta=dt_meta)
        elif export_format == "fbx":
            export_fbx(output_path, engine)
        elif export_format == "usd":
            export_usd(output_path, engine, dt_meta=dt_meta)
        else:
            emit_error(f"Unknown format: {export_format}")

        # 5. Result
        file_size = os.path.getsize(output_path) if os.path.isfile(output_path) else 0
        stats = get_mesh_stats()

        emit_result({
            "status": "ok",
            "output_path": output_path,
            "format": export_format,
            "engine": engine,
            "file_size_bytes": file_size,
            "file_size_mb": round(file_size / (1024 * 1024), 2),
            **stats,
        })

    except Exception as exc:
        import traceback
        traceback.print_exc()
        emit_error(str(exc))


if __name__ == "__main__":
    main()
