"""
CleanMesh — Render Verification Script
========================================

Renders 5 orthographic views of a GLB model for visual QA, plus an optional
contact-sheet grid.

Usage:
    blender --background --python render.py -- \\
        --input <glb_path>          Input GLB file \\
        --output-dir <dir>          Directory for rendered PNGs \\
        [--resolution 512]          Render resolution (square)

Pipeline output is printed as a single JSON line prefixed with 'RESULT:'.
"""

import bpy
import sys
import os
import json
import math
import argparse
from mathutils import Vector


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
        description="CleanMesh render verification"
    )
    parser.add_argument("--input", required=True, help="Input GLB file")
    parser.add_argument("--output-dir", required=True,
                        help="Output directory for rendered images")
    parser.add_argument("--resolution", type=int, default=512,
                        help="Render resolution (square, default: 512)")

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


def get_scene_bounds():
    """Compute the combined bounding box of all mesh objects."""
    all_corners = []
    for obj in get_mesh_objects():
        for corner in obj.bound_box:
            world_co = obj.matrix_world @ Vector(corner)
            all_corners.append(world_co)

    if not all_corners:
        return Vector((0, 0, 0)), Vector((1, 1, 1)), Vector((0.5, 0.5, 0.5))

    min_co = Vector((min(c.x for c in all_corners),
                      min(c.y for c in all_corners),
                      min(c.z for c in all_corners)))
    max_co = Vector((max(c.x for c in all_corners),
                      max(c.y for c in all_corners),
                      max(c.z for c in all_corners)))
    center = (min_co + max_co) / 2.0
    return min_co, max_co, center


def get_mesh_stats():
    """Aggregate mesh statistics from all mesh objects."""
    total_verts = 0
    total_faces = 0
    total_tris = 0
    mat_names = []
    dims = [0.0, 0.0, 0.0]

    for obj in get_mesh_objects():
        mesh = obj.data
        mesh.calc_loop_triangles()
        total_verts += len(mesh.vertices)
        total_faces += len(mesh.polygons)
        total_tris += len(mesh.loop_triangles)
        for slot in obj.material_slots:
            if slot.material and slot.material.name not in mat_names:
                mat_names.append(slot.material.name)

    min_co, max_co, _ = get_scene_bounds()
    dims = [round(max_co.x - min_co.x, 4),
            round(max_co.y - min_co.y, 4),
            round(max_co.z - min_co.z, 4)]

    return {
        "vertices": total_verts,
        "faces": total_faces,
        "tris": total_tris,
        "materials": mat_names,
        "dimensions": dims,
    }


# ---------------------------------------------------------------------------
# Scene Setup
# ---------------------------------------------------------------------------

def clear_scene():
    """Remove all objects and orphan data."""
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=True)
    for block_coll in (bpy.data.meshes, bpy.data.materials,
                       bpy.data.textures, bpy.data.images,
                       bpy.data.lights, bpy.data.cameras):
        for block in block_coll:
            if block.users == 0:
                block_coll.remove(block)


def import_glb(filepath: str):
    """Import a GLB/GLTF file."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=filepath)
    else:
        emit_error(f"render.py expects GLB/GLTF input, got: {ext}")


def setup_renderer(resolution: int):
    """Configure EEVEE renderer for fast verification renders."""
    scene = bpy.context.scene

    # Use EEVEE (Blender 4.x: BLENDER_EEVEE_NEXT, 3.x: BLENDER_EEVEE)
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except Exception:
        scene.render.engine = "BLENDER_EEVEE"

    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.compression = 15

    # Samples — keep low for speed
    if hasattr(scene.eevee, "taa_render_samples"):
        scene.eevee.taa_render_samples = 32
    elif hasattr(scene.eevee, "samples"):
        scene.eevee.samples = 32


def setup_world():
    """Set up a neutral gray world/background."""
    world = bpy.data.worlds.get("World")
    if world is None:
        world = bpy.data.worlds.new("World")
    bpy.context.scene.world = world

    if hasattr(world, "use_nodes"): world.use_nodes = True  # Blender 6.0 가드
    tree = world.node_tree
    tree.nodes.clear()

    bg_node = tree.nodes.new(type="ShaderNodeBackground")
    bg_node.inputs["Color"].default_value = (0.35, 0.35, 0.35, 1.0)
    bg_node.inputs["Strength"].default_value = 1.0

    output_node = tree.nodes.new(type="ShaderNodeOutputWorld")
    tree.links.new(bg_node.outputs["Background"],
                   output_node.inputs["Surface"])


def setup_three_point_lighting(center: Vector, radius: float):
    """Add key, fill, and rim lights around the scene center."""
    light_configs = [
        # (name, type, energy, offset from center)
        ("Key",  "AREA", 150, Vector((radius * 1.5, -radius * 1.2, radius * 1.8))),
        ("Fill", "AREA", 60,  Vector((-radius * 1.5, -radius * 0.5, radius * 1.0))),
        ("Rim",  "AREA", 100, Vector((0.0, radius * 2.0, radius * 1.0))),
    ]

    for name, light_type, energy, offset in light_configs:
        light_data = bpy.data.lights.new(name=f"CleanMesh_{name}", type=light_type)
        light_data.energy = energy
        if hasattr(light_data, "size"):
            light_data.size = radius * 0.8

        light_obj = bpy.data.objects.new(name=f"CleanMesh_{name}", object_data=light_data)
        bpy.context.collection.objects.link(light_obj)
        light_obj.location = center + offset

        # Point at center
        direction = center - light_obj.location
        rot_quat = direction.to_track_quat("-Z", "Y")
        light_obj.rotation_euler = rot_quat.to_euler()


def create_camera():
    """Create and return a camera object."""
    cam_data = bpy.data.cameras.new(name="CleanMesh_Camera")
    cam_obj = bpy.data.objects.new(name="CleanMesh_Camera", object_data=cam_data)
    bpy.context.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj
    return cam_obj


# ---------------------------------------------------------------------------
# Camera Positions for 5 Views
# ---------------------------------------------------------------------------

VIEW_DEFINITIONS = {
    # name: (angle_h_deg, angle_v_deg)  — spherical angles around center
    # angle_h = horizontal rotation (0=front/-Y), angle_v = elevation
    "front": (0, 20),
    "back":  (180, 20),
    "left":  (90, 20),
    "right": (-90, 20),
    "top":   (0, 85),
}


def position_camera(cam_obj, center: Vector, radius: float,
                     h_deg: float, v_deg: float):
    """Place *cam_obj* on a sphere around *center* at the given angles."""
    h_rad = math.radians(h_deg)
    v_rad = math.radians(v_deg)

    distance = radius * 3.0  # Pull back far enough

    x = center.x + distance * math.sin(h_rad) * math.cos(v_rad)
    y = center.y - distance * math.cos(h_rad) * math.cos(v_rad)
    z = center.z + distance * math.sin(v_rad)

    cam_obj.location = Vector((x, y, z))

    # Point at center
    direction = center - cam_obj.location
    rot_quat = direction.to_track_quat("-Z", "Y")
    cam_obj.rotation_euler = rot_quat.to_euler()

    # Adjust lens so object fills the frame
    cam_obj.data.type = "PERSP"
    cam_obj.data.lens = 50


# ---------------------------------------------------------------------------
# Contact Sheet (using Blender's compositor)
# ---------------------------------------------------------------------------

def create_contact_sheet(image_paths: list, output_path: str, resolution: int):
    """
    Create a 2×3 contact sheet from rendered images.
    Falls back gracefully if dependencies are missing.
    """
    try:
        # Try using Pillow if available in Blender's Python
        from PIL import Image as PILImage

        cols, rows = 3, 2
        sheet_w = cols * resolution
        sheet_h = rows * resolution
        sheet = PILImage.new("RGBA", (sheet_w, sheet_h), (60, 60, 60, 255))

        for idx, path in enumerate(image_paths):
            if not os.path.isfile(path):
                continue
            img = PILImage.open(path)
            img = img.resize((resolution, resolution), PILImage.LANCZOS)
            col = idx % cols
            row = idx // cols
            sheet.paste(img, (col * resolution, row * resolution))

        sheet.save(output_path)
        return True

    except ImportError:
        pass

    # Fallback: use Blender's built-in image API
    try:
        cols, rows = 3, 2
        sheet_w = cols * resolution
        sheet_h = rows * resolution

        sheet_img = bpy.data.images.new(
            "ContactSheet", width=sheet_w, height=sheet_h, alpha=True
        )
        # Fill with gray
        pixels = [0.235, 0.235, 0.235, 1.0] * (sheet_w * sheet_h)
        sheet_img.pixels[:] = pixels

        for idx, path in enumerate(image_paths):
            if not os.path.isfile(path):
                continue

            src = bpy.data.images.load(path)
            src_w, src_h = src.size

            col = idx % cols
            row = idx // cols

            # Copy pixels row by row (Blender images are bottom-up)
            src_pixels = list(src.pixels[:])
            sheet_pixels = list(sheet_img.pixels[:])

            paste_x = col * resolution
            # Blender images are bottom-up, so invert row
            paste_y = sheet_h - (row + 1) * resolution

            for py in range(min(src_h, resolution)):
                for px in range(min(src_w, resolution)):
                    src_idx = (py * src_w + px) * 4
                    dst_x = paste_x + px
                    dst_y = paste_y + py
                    dst_idx = (dst_y * sheet_w + dst_x) * 4
                    for c in range(4):
                        sheet_pixels[dst_idx + c] = src_pixels[src_idx + c]

            sheet_img.pixels[:] = sheet_pixels
            bpy.data.images.remove(src)

        sheet_img.filepath_raw = output_path
        sheet_img.file_format = "PNG"
        sheet_img.save_render(output_path)
        bpy.data.images.remove(sheet_img)
        return True

    except Exception as exc:
        print(f"WARNING: Could not create contact sheet: {exc}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    input_path = os.path.abspath(args.input)
    output_dir = os.path.abspath(args.output_dir)
    resolution = args.resolution

    if not os.path.isfile(input_path):
        emit_error(f"Input file not found: {input_path}")

    os.makedirs(output_dir, exist_ok=True)

    try:
        # 1. Clear scene
        clear_scene()

        # 2. Import GLB
        import_glb(input_path)

        # 3. Set up renderer
        setup_renderer(resolution)

        # 4. Compute scene bounds
        min_co, max_co, center = get_scene_bounds()
        extent = max_co - min_co
        radius = extent.length / 2.0
        if radius < 0.001:
            radius = 1.0

        # 5. Three-point lighting
        setup_three_point_lighting(center, radius)

        # 6. World background
        setup_world()

        # 7. Create camera
        cam_obj = create_camera()

        # 8. Render 5 views
        render_paths = []
        view_names = ["front", "back", "left", "right", "top"]

        for view_name in view_names:
            h_deg, v_deg = VIEW_DEFINITIONS[view_name]
            position_camera(cam_obj, center, radius, h_deg, v_deg)

            out_path = os.path.join(output_dir, f"{view_name}.png")
            bpy.context.scene.render.filepath = out_path
            bpy.ops.render.render(write_still=True)
            render_paths.append(out_path)

        # 9. Create contact sheet
        contact_sheet_path = os.path.join(output_dir, "contact_sheet.png")
        sheet_ok = create_contact_sheet(render_paths, contact_sheet_path,
                                        resolution)
        if sheet_ok:
            render_paths.append(contact_sheet_path)

        # 10. Collect stats and emit result
        stats = get_mesh_stats()

        emit_result({
            "status": "ok",
            "renders": render_paths,
            "resolution": resolution,
            "object_info": stats,
        })

    except Exception as exc:
        import traceback
        traceback.print_exc()
        emit_error(str(exc))


if __name__ == "__main__":
    main()
