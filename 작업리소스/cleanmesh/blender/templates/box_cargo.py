"""
CleanMesh — WH_Box_Cargo Generator
====================================
Generate a cardboard cargo box with optional tape strip.

Usage:
    blender --background --python box_cargo.py -- --output <path> [options]

Options:
    --width    Box width  in metres (X)  (default: 0.6)
    --height   Box height in metres (Z)  (default: 0.4)
    --depth    Box depth  in metres (Y)  (default: 0.4)
    --tape     Add tape strip on top
    --color    Hex colour for cardboard   (default: #B8956A)
"""

import bpy
import bmesh
import sys
import os
import json
import argparse
import math
from mathutils import Vector


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    ap = argparse.ArgumentParser(description='Generate a cargo box.')
    ap.add_argument('--output', type=str, required=True, help='Output GLB path')
    ap.add_argument('--width', type=float, default=0.6, help='Width X (m)')
    ap.add_argument('--height', type=float, default=0.4, help='Height Z (m)')
    ap.add_argument('--depth', type=float, default=0.4, help='Depth Y (m)')
    ap.add_argument('--tape', action='store_true', help='Add tape strip')
    ap.add_argument('--color', type=str, default='#B8956A', help='Cardboard hex colour')
    return ap.parse_args(argv)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def hex_to_rgb(hex_str: str) -> tuple:
    h = hex_str.lstrip('#')
    srgb = tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))
    linear = tuple(((c / 12.92) if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4) for c in srgb)
    return (*linear, 1.0)


def clean_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for block in bpy.data.meshes:
        bpy.data.meshes.remove(block)
    for block in bpy.data.materials:
        bpy.data.materials.remove(block)


def set_origin_bottom_center(obj):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    local_verts = [v.co for v in obj.data.vertices]
    if not local_verts:
        return
    min_z = min(v.z for v in local_verts)
    cx = (min(v.x for v in local_verts) + max(v.x for v in local_verts)) / 2
    cy = (min(v.y for v in local_verts) + max(v.y for v in local_verts)) / 2
    offset = Vector((cx, cy, min_z))
    for v in obj.data.vertices:
        v.co -= offset
    obj.location += offset
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def smart_uv_project(obj):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=66.0, island_margin=0.02)
    bpy.ops.object.mode_set(mode='OBJECT')


# ---------------------------------------------------------------------------
# Box construction
# ---------------------------------------------------------------------------

def create_cargo_box(width, height, depth):
    """Create a box with edge loops for flap lines and bevel modifier."""
    bm = bmesh.new()

    hw = width / 2
    hd = depth / 2

    # Main box vertices — bottom at Z=0, top at Z=height
    # Add extra edge loops near top to suggest flap fold lines
    flap_line_z = height - 0.005  # 5 mm below top edge

    # Z layers: bottom, flap_line, top
    z_levels = [0.0, flap_line_z, height]
    y_vals = [-hd, hd]
    x_vals = [-hw, hw]

    # Build structured grid: for each Z level, create a ring of 4 verts
    rings = []
    for z in z_levels:
        ring = [
            bm.verts.new((-hw, -hd, z)),
            bm.verts.new((hw, -hd, z)),
            bm.verts.new((hw, hd, z)),
            bm.verts.new((-hw, hd, z)),
        ]
        rings.append(ring)

    # Side faces between adjacent Z rings
    for i in range(len(rings) - 1):
        lower = rings[i]
        upper = rings[i + 1]
        for j in range(4):
            j_next = (j + 1) % 4
            bm.faces.new([lower[j], lower[j_next], upper[j_next], upper[j]])

    # Bottom face
    bm.faces.new([rings[0][3], rings[0][2], rings[0][1], rings[0][0]])

    # Top face
    bm.faces.new([rings[-1][0], rings[-1][1], rings[-1][2], rings[-1][3]])

    # Also add a centre top edge loop to mark the flap seam
    # (edge from midpoint of front to midpoint of back on the top face)
    # We'll use a knife-like approach: split the top face with an edge loop
    # For simplicity, add the seam line as additional geometry
    top_z = height + 0.0001  # Tiny offset to avoid z-fighting
    seam_verts = [
        bm.verts.new((0, -hd, height)),
        bm.verts.new((0, hd, height)),
    ]

    bm.normal_update()
    mesh = bpy.data.meshes.new('WH_Box_Cargo_Mesh')
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new('WH_Box_Cargo', mesh)
    bpy.context.collection.objects.link(obj)

    # Add bevel modifier for slightly rounded edges
    bevel = obj.modifiers.new(name='Bevel', type='BEVEL')
    bevel.width = 0.003
    bevel.segments = 2
    bevel.limit_method = 'ANGLE'
    bevel.angle_limit = math.radians(60)

    # Apply modifier
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.modifier_apply(modifier='Bevel')

    return obj


def create_tape_strip(width, depth, height):
    """Create a thin tape strip across the top of the box."""
    tape_width = 0.048   # 48 mm packing tape
    tape_thickness = 0.0005
    hw = tape_width / 2
    hd = depth / 2 + 0.001  # slight overhang

    bm = bmesh.new()
    verts = [
        bm.verts.new((-hw, -hd, height)),
        bm.verts.new((hw, -hd, height)),
        bm.verts.new((hw, hd, height)),
        bm.verts.new((-hw, hd, height)),
        bm.verts.new((-hw, -hd, height + tape_thickness)),
        bm.verts.new((hw, -hd, height + tape_thickness)),
        bm.verts.new((hw, hd, height + tape_thickness)),
        bm.verts.new((-hw, hd, height + tape_thickness)),
    ]
    faces = [
        [verts[0], verts[1], verts[2], verts[3]],
        [verts[4], verts[7], verts[6], verts[5]],
        [verts[0], verts[4], verts[5], verts[1]],
        [verts[2], verts[6], verts[7], verts[3]],
        [verts[0], verts[3], verts[7], verts[4]],
        [verts[1], verts[5], verts[6], verts[2]],
    ]
    for f in faces:
        bm.faces.new(f)
    bm.normal_update()

    mesh = bpy.data.meshes.new('WH_Box_Tape_Mesh')
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new('WH_Box_Tape', mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def create_cardboard_material(color_hex):
    mat = bpy.data.materials.new(name='MAT_Cardboard')
    if hasattr(mat, "use_nodes"): mat.use_nodes = True  # Blender 6.0 가드
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf:
        bsdf.inputs['Base Color'].default_value = hex_to_rgb(color_hex)
        bsdf.inputs['Roughness'].default_value = 0.80
        bsdf.inputs['Metallic'].default_value = 0.0
    return mat


def create_tape_material():
    mat = bpy.data.materials.new(name='MAT_Tape')
    if hasattr(mat, "use_nodes"): mat.use_nodes = True  # Blender 6.0 가드
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf:
        # Semi-transparent brown packing tape
        bsdf.inputs['Base Color'].default_value = (0.45, 0.35, 0.15, 1.0)
        bsdf.inputs['Roughness'].default_value = 0.20
        bsdf.inputs['Metallic'].default_value = 0.0
    return mat


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    try:
        args = parse_args()
        clean_scene()

        box = create_cargo_box(args.width, args.height, args.depth)
        mat_card = create_cardboard_material(args.color)
        box.data.materials.append(mat_card)

        if args.tape:
            tape = create_tape_strip(args.width, args.depth, args.height)
            mat_tape = create_tape_material()
            tape.data.materials.append(mat_tape)

            # Join tape to box
            bpy.ops.object.select_all(action='DESELECT')
            tape.select_set(True)
            box.select_set(True)
            bpy.context.view_layer.objects.active = box
            bpy.ops.object.join()

        box = bpy.context.view_layer.objects.active
        box.name = 'WH_Box_Cargo'
        box.data.name = 'WH_Box_Cargo_Mesh'

        # Apply transforms
        bpy.ops.object.select_all(action='DESELECT')
        box.select_set(True)
        bpy.context.view_layer.objects.active = box
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        # Smart UV
        smart_uv_project(box)

        # Origin
        set_origin_bottom_center(box)

        # Output
        out_dir = os.path.dirname(os.path.abspath(args.output))
        os.makedirs(out_dir, exist_ok=True)

        bpy.ops.export_scene.gltf(
            filepath=args.output,
            export_format='GLB',
            use_selection=True,
            export_apply=True,
        )

        dims = box.dimensions
        result = {
            'status': 'success',
            'name': box.name,
            'vertices': len(box.data.vertices),
            'faces': len(box.data.polygons),
            'dimensions': {
                'x': round(dims.x, 4),
                'y': round(dims.y, 4),
                'z': round(dims.z, 4),
            },
            'output_path': os.path.abspath(args.output),
        }
        print('RESULT:' + json.dumps(result))

    except Exception as exc:
        result = {'status': 'error', 'message': str(exc)}
        print('RESULT:' + json.dumps(result))
        sys.exit(1)


if __name__ == '__main__':
    main()
