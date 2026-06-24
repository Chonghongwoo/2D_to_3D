"""
CleanMesh — Mesh Cleanup Script
================================

Runs inside Blender's headless mode to clean, optimize, and export meshes.

Usage:
    blender --background --python cleanup.py -- \\
        --input <path>          Input mesh file (.glb/.gltf/.obj/.fbx/.ply) \\
        --output <path>         Output GLB file path \\
        [--target-polys N]      Decimate to approximately N polygons \\
        [--aggressive]          Enable aggressive cleanup (tighter merge, extra dissolve) \\
        [--real-size W,H,D]     Real-world size in meters (width,height,depth) \\
        [--name NAME]           Rename the final object (CleanMesh convention)

Pipeline output is printed as a single JSON line prefixed with 'RESULT:'.
"""

import bpy
import bmesh
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
    """Parse CLI arguments passed after the '--' separator."""
    try:
        separator_index = sys.argv.index("--")
        script_argv = sys.argv[separator_index + 1:]
    except ValueError:
        print("ERROR: No '--' separator found in sys.argv. "
              "Run with: blender --background --python cleanup.py -- --input ...")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="CleanMesh Blender cleanup pipeline"
    )
    parser.add_argument("--input", required=True, help="Input mesh file path")
    parser.add_argument("--output", required=True, help="Output GLB file path")
    parser.add_argument("--target-polys", type=int, default=None,
                        help="Target polygon count for decimation")
    parser.add_argument("--aggressive", action="store_true",
                        help="Enable aggressive cleanup passes")
    parser.add_argument("--real-size", type=str, default=None,
                        help="Real-world bounding size as W,H,D in meters")
    parser.add_argument("--name", type=str, default=None,
                        help="Rename the final object")
    parser.add_argument("--also-fbx", action="store_true",
                        help="In addition to GLB, also export a sibling .fbx file "
                             "(preserves quad topology that GLB strips)")

    return parser.parse_args(script_argv)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def emit_result(data: dict):
    """Print a JSON result line that the pipeline can parse."""
    print(f"RESULT:{json.dumps(data)}")


def emit_error(message: str):
    """Print an error result and exit."""
    emit_result({"status": "error", "message": message})
    sys.exit(1)


def get_mesh_objects():
    """Return a list of all mesh objects in the scene."""
    return [obj for obj in bpy.data.objects if obj.type == "MESH"]


def get_mesh_stats(obj):
    """Return vertex/face/tri counts and material info for *obj*."""
    mesh = obj.data
    mesh.calc_loop_triangles()
    return {
        "vertices": len(mesh.vertices),
        "faces": len(mesh.polygons),
        "tris": len(mesh.loop_triangles),
        "has_uv": len(mesh.uv_layers) > 0,
        "materials": [slot.material.name if slot.material else "(empty)"
                      for slot in obj.material_slots],
    }


# ---------------------------------------------------------------------------
# Pipeline Steps
# ---------------------------------------------------------------------------

def step_clear_scene():
    """1. Clear the default scene completely."""
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=True)

    # Also purge orphan data-blocks
    for block_coll in (bpy.data.meshes, bpy.data.materials,
                       bpy.data.textures, bpy.data.images):
        for block in block_coll:
            if block.users == 0:
                block_coll.remove(block)


def step_import_mesh(filepath: str):
    """2. Import a mesh file, detecting format by extension."""
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

    meshes = get_mesh_objects()
    if not meshes:
        emit_error("No mesh objects found after import.")


def step_join_meshes():
    """3. Select all mesh objects and join into one."""
    meshes = get_mesh_objects()
    if len(meshes) <= 1:
        if meshes:
            bpy.context.view_layer.objects.active = meshes[0]
            meshes[0].select_set(True)
        return

    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.join()


def step_remove_doubles(aggressive: bool = False):
    """4. Merge vertices by distance (remove doubles)."""
    obj = bpy.context.active_object
    if obj is None or obj.type != "MESH":
        return

    threshold = 0.00005 if aggressive else 0.0001

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=threshold)
    bpy.ops.object.mode_set(mode="OBJECT")


def step_dissolve_degenerate():
    """5. Dissolve degenerate faces (zero-area, collapsed)."""
    obj = bpy.context.active_object
    if obj is None or obj.type != "MESH":
        return

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.dissolve_degenerate(threshold=0.0001)
    bpy.ops.object.mode_set(mode="OBJECT")


def step_recalculate_normals():
    """6. Make normals consistent (pointing outside)."""
    obj = bpy.context.active_object
    if obj is None or obj.type != "MESH":
        return

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")


def step_tris_to_quads():
    """7. Convert triangles to quads where possible."""
    obj = bpy.context.active_object
    if obj is None or obj.type != "MESH":
        return

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.tris_convert_to_quads(
        face_threshold=math.radians(40),
        shape_threshold=math.radians(40),
    )
    bpy.ops.object.mode_set(mode="OBJECT")


def step_decimate(target_polys: int):
    """8. Decimate mesh to approximately *target_polys* polygons."""
    obj = bpy.context.active_object
    if obj is None or obj.type != "MESH":
        return

    current_polys = len(obj.data.polygons)
    if current_polys <= target_polys:
        return  # Already under target

    ratio = target_polys / current_polys
    ratio = max(ratio, 0.01)  # Never decimate below 1 %

    mod = obj.modifiers.new(name="CleanMesh_Decimate", type="DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = ratio
    bpy.ops.object.modifier_apply(modifier=mod.name)


def step_auto_smooth():
    """9. Apply smooth shading with auto-smooth normals."""
    obj = bpy.context.active_object
    if obj is None or obj.type != "MESH":
        return

    # Smooth shading on all faces
    bpy.ops.object.shade_smooth()

    # Blender 4.x: auto smooth via modifier / mesh attribute
    # Blender 3.x: mesh.use_auto_smooth
    mesh = obj.data
    if hasattr(mesh, "use_auto_smooth"):
        mesh.use_auto_smooth = True
        mesh.auto_smooth_angle = math.radians(30)
    else:
        # Blender 4.1+: use the smooth-by-angle modifier approach
        try:
            bpy.ops.object.modifier_add(type="SMOOTH_BY_ANGLE")
            # Find the modifier we just added and optionally set angle
            for mod in reversed(obj.modifiers):
                if mod.type == "NODES" and "Smooth by Angle" in mod.name:
                    break
        except Exception:
            pass  # Fallback — smooth shading already applied


def step_scale_normalization(real_size_str: str | None):
    """10. Normalize scale based on bounding box / real-world size."""
    obj = bpy.context.active_object
    if obj is None or obj.type != "MESH":
        return

    if real_size_str is None:
        return  # Keep original scale

    try:
        parts = [float(v.strip()) for v in real_size_str.split(",")]
        if len(parts) != 3:
            raise ValueError
        target_w, target_h, target_d = parts
    except (ValueError, AttributeError):
        print(f"WARNING: Could not parse --real-size '{real_size_str}', skipping.")
        return

    # Current bounding box dimensions (in world space)
    bbox_corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    min_co = Vector((min(c.x for c in bbox_corners),
                      min(c.y for c in bbox_corners),
                      min(c.z for c in bbox_corners)))
    max_co = Vector((max(c.x for c in bbox_corners),
                      max(c.y for c in bbox_corners),
                      max(c.z for c in bbox_corners)))

    cur_dims = max_co - min_co
    if cur_dims.x == 0 or cur_dims.y == 0 or cur_dims.z == 0:
        print("WARNING: Zero-dimension bounding box, skipping scale normalization.")
        return

    scale_factors = Vector((target_w / cur_dims.x,
                             target_h / cur_dims.z,   # Z is up in Blender
                             target_d / cur_dims.y))

    obj.scale = (obj.scale.x * scale_factors.x,
                 obj.scale.y * scale_factors.z,  # Blender Y = depth
                 obj.scale.z * scale_factors.y)  # Blender Z = height


def step_origin_to_bottom_center():
    """11. Set origin to geometry center, then shift so bottom sits at Z=0."""
    obj = bpy.context.active_object
    if obj is None or obj.type != "MESH":
        return

    # Apply current transforms first so bounding box is accurate
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    # Set origin to geometry center
    bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")

    # Now shift object so the bottom of the bounding box is at Z = 0
    bbox_corners = [Vector(c) for c in obj.bound_box]
    min_z = min(c.z for c in bbox_corners)

    # Move all vertices up by -min_z (in object local space)
    mesh = obj.data
    for vert in mesh.vertices:
        vert.co.z -= min_z

    # Reset object location to world origin
    obj.location = (0.0, 0.0, 0.0)


def step_apply_transforms():
    """12. Apply all transforms (location, rotation, scale)."""
    obj = bpy.context.active_object
    if obj is None or obj.type != "MESH":
        return

    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def step_smart_uv_project():
    """13. Create UVs via Smart UV Project."""
    obj = bpy.context.active_object
    if obj is None or obj.type != "MESH":
        return

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(
        angle_limit=math.radians(66),
        island_margin=0.02,
        area_weight=0.0,
        correct_aspect=True,
    )
    bpy.ops.object.mode_set(mode="OBJECT")


def step_cleanup_materials():
    """14. Ensure Principled BSDF on every material slot; rename slots."""
    obj = bpy.context.active_object
    if obj is None or obj.type != "MESH":
        return

    if len(obj.material_slots) == 0:
        # Create a default material
        mat = bpy.data.materials.new(name="CleanMesh_Default")
        if hasattr(mat, "use_nodes"): mat.use_nodes = True  # Blender 6.0 가드
        obj.data.materials.append(mat)

    for idx, slot in enumerate(obj.material_slots):
        mat = slot.material
        if mat is None:
            mat = bpy.data.materials.new(name=f"CleanMesh_Mat{idx:02d}")
            if hasattr(mat, "use_nodes"): mat.use_nodes = True  # Blender 6.0 가드
            slot.material = mat

        # Ensure node tree has a Principled BSDF
        # Read use_nodes safely — getattr defaults to True since Blender 6.0
        # treats all materials as node-based by default.
        if getattr(mat, "use_nodes", True):
            tree = mat.node_tree
            principled = None
            for node in tree.nodes:
                if node.type == "BSDF_PRINCIPLED":
                    principled = node
                    break

            if principled is None:
                # Clear existing nodes and create fresh Principled BSDF setup
                tree.nodes.clear()
                principled = tree.nodes.new(type="ShaderNodeBsdfPrincipled")
                principled.location = (0, 0)
                output_node = tree.nodes.new(type="ShaderNodeOutputMaterial")
                output_node.location = (300, 0)
                tree.links.new(principled.outputs["BSDF"],
                               output_node.inputs["Surface"])

            # If the mesh has a Color attribute (vertex colors), wire it into Base Color
            # so that the imported TRELLIS/glTF colors actually show in the viewport
            # and survive re-export. Look for any color attribute first.
            color_attr = None
            try:
                for ca in obj.data.color_attributes:
                    color_attr = ca
                    break
            except Exception:
                color_attr = None

            if color_attr is not None:
                # Connect Color Attribute node → Base Color
                ca_node = next((n for n in tree.nodes if n.type == "VERTEX_COLOR"), None)
                if ca_node is None:
                    ca_node = tree.nodes.new(type="ShaderNodeVertexColor")
                    ca_node.location = (-300, 0)
                ca_node.layer_name = color_attr.name
                # Re-link only if Base Color isn't already driven by an image texture
                base_input = principled.inputs.get("Base Color")
                if base_input is not None:
                    already_linked = any(
                        link.to_socket == base_input and link.from_node.type == "TEX_IMAGE"
                        for link in tree.links
                    )
                    if not already_linked:
                        # Remove any existing link first
                        for link in list(tree.links):
                            if link.to_socket == base_input:
                                tree.links.remove(link)
                        tree.links.new(ca_node.outputs["Color"], base_input)

        # Rename material slot using PBR convention
        base_name = obj.name if obj.name else "CleanMesh"
        if len(obj.material_slots) == 1:
            mat.name = f"{base_name}_PBR"
        else:
            mat.name = f"{base_name}_PBR_{idx:02d}"


def step_rename_object(name: str | None):
    """15. Rename the active object if --name was specified."""
    obj = bpy.context.active_object
    if obj is None or name is None:
        return

    obj.name = name
    if obj.data:
        obj.data.name = f"{name}_Mesh"


def step_export_glb(output_path: str):
    """16. Export the scene as GLB."""
    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    bpy.ops.export_scene.gltf(
        filepath=output_path,
        export_format="GLB",
        use_selection=False,
        export_apply=True,
        export_yup=True,
    )


def step_export_fbx_sibling(glb_path: str) -> str:
    """16b. Export an FBX alongside the GLB (preserves quad topology)."""
    base, _ = os.path.splitext(glb_path)
    fbx_path = base + ".fbx"
    bpy.ops.export_scene.fbx(
        filepath=fbx_path,
        use_selection=False,
        apply_unit_scale=True,
        bake_space_transform=False,
        object_types={"MESH"},
        use_mesh_modifiers=True,
        mesh_smooth_type="OFF",
        axis_forward="-Z",
        axis_up="Y",
    )
    return fbx_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    input_path = os.path.abspath(args.input)
    output_path = os.path.abspath(args.output)

    if not os.path.isfile(input_path):
        emit_error(f"Input file not found: {input_path}")

    try:
        # 1. Clear default scene
        step_clear_scene()

        # 2. Import mesh
        step_import_mesh(input_path)

        # 3. Join meshes
        step_join_meshes()

        # 4. Remove doubles
        step_remove_doubles(aggressive=args.aggressive)

        # 5. Dissolve degenerate faces
        step_dissolve_degenerate()

        # 6. Recalculate normals
        step_recalculate_normals()

        # 7. Tris to quads
        step_tris_to_quads()

        # 8. Decimate (optional)
        if args.target_polys is not None:
            step_decimate(args.target_polys)

        # 9. Auto smooth shading
        step_auto_smooth()

        # 10. Scale normalization
        step_scale_normalization(args.real_size)

        # 11. Origin to bottom center
        step_origin_to_bottom_center()

        # 12. Apply all transforms
        step_apply_transforms()

        # 13. Smart UV Project
        step_smart_uv_project()

        # 14. Cleanup materials
        step_cleanup_materials()

        # 15. Rename object
        step_rename_object(args.name)

        # 16. Export GLB
        step_export_glb(output_path)

        # 16b. Optionally also export FBX (preserves quads that GLB strips)
        fbx_path = None
        if args.also_fbx:
            try:
                fbx_path = step_export_fbx_sibling(output_path)
            except Exception as exc:
                print(f"WARN: FBX sibling export failed: {exc}")

        # 17. Collect & print result
        obj = bpy.context.active_object
        stats = get_mesh_stats(obj) if obj else {}
        stats["status"] = "ok"
        stats["output_path"] = output_path
        if fbx_path:
            stats["output_path_fbx"] = fbx_path

        emit_result(stats)

    except Exception as exc:
        import traceback
        traceback.print_exc()
        emit_error(str(exc))


if __name__ == "__main__":
    main()
