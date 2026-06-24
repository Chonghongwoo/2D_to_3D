"""
CleanMesh — WH_Shelf_Rack Generator
=====================================
Generate a warehouse pallet rack with uprights, beams, decks, and X-bracing.

Usage:
    blender --background --python shelf_rack.py -- --output <path> [options]

Options:
    --levels   Number of shelf levels         (default: 4)
    --width    Bay width in metres (X)        (default: 2.7)
    --depth    Frame depth in metres (Y)      (default: 1.0)
    --height   Total height in metres (Z)     (default: 4.0)
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
    ap = argparse.ArgumentParser(description='Generate a warehouse shelf rack.')
    ap.add_argument('--output', type=str, required=True, help='Output GLB path')
    ap.add_argument('--levels', type=int, default=4, help='Number of levels')
    ap.add_argument('--width', type=float, default=2.7, help='Bay width (m)')
    ap.add_argument('--depth', type=float, default=1.0, help='Frame depth (m)')
    ap.add_argument('--height', type=float, default=4.0, help='Total height (m)')
    return ap.parse_args(argv)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for block in bpy.data.meshes:
        bpy.data.meshes.remove(block)
    for block in bpy.data.materials:
        bpy.data.materials.remove(block)


def create_box(name, sx, sy, sz, loc):
    """Create box centred at *loc* with half-extents sx, sy, sz."""
    bm = bmesh.new()
    v = [
        bm.verts.new((loc[0] - sx, loc[1] - sy, loc[2] - sz)),
        bm.verts.new((loc[0] + sx, loc[1] - sy, loc[2] - sz)),
        bm.verts.new((loc[0] + sx, loc[1] + sy, loc[2] - sz)),
        bm.verts.new((loc[0] - sx, loc[1] + sy, loc[2] - sz)),
        bm.verts.new((loc[0] - sx, loc[1] - sy, loc[2] + sz)),
        bm.verts.new((loc[0] + sx, loc[1] - sy, loc[2] + sz)),
        bm.verts.new((loc[0] + sx, loc[1] + sy, loc[2] + sz)),
        bm.verts.new((loc[0] - sx, loc[1] + sy, loc[2] + sz)),
    ]
    bm.faces.new([v[0], v[1], v[2], v[3]])
    bm.faces.new([v[4], v[7], v[6], v[5]])
    bm.faces.new([v[0], v[4], v[5], v[1]])
    bm.faces.new([v[2], v[6], v[7], v[3]])
    bm.faces.new([v[0], v[3], v[7], v[4]])
    bm.faces.new([v[1], v[5], v[6], v[2]])
    bm.normal_update()
    mesh = bpy.data.meshes.new(name + '_Mesh')
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def set_origin_bottom_center(obj):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    verts = [v.co for v in obj.data.vertices]
    if not verts:
        return
    min_z = min(v.z for v in verts)
    cx = (min(v.x for v in verts) + max(v.x for v in verts)) / 2
    cy = (min(v.y for v in verts) + max(v.y for v in verts)) / 2
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


def create_material(name, color_rgba, metallic=0.6, roughness=0.45):
    mat = bpy.data.materials.new(name=name)
    if hasattr(mat, "use_nodes"): mat.use_nodes = True  # Blender 6.0 가드
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf:
        bsdf.inputs['Base Color'].default_value = color_rgba
        bsdf.inputs['Metallic'].default_value = metallic
        bsdf.inputs['Roughness'].default_value = roughness
    return mat


# ---------------------------------------------------------------------------
# Rack construction
# ---------------------------------------------------------------------------

def build_shelf_rack(levels, width, depth, height):
    """Build the full pallet rack and return the joined object."""

    # Profile dimensions (metres)
    upright_w = 0.060   # upright cross-section width
    upright_d = 0.050   # upright cross-section depth
    beam_h = 0.100      # beam height
    beam_w = 0.050      # beam width (thickness)
    brace_w = 0.025     # brace cross-section
    deck_thickness = 0.018  # shelf deck board thickness

    parts = []

    # Materials
    mat_upright = create_material('MAT_Upright_Orange', (1.0, 0.4, 0.0, 1.0), metallic=0.6, roughness=0.45)
    mat_beam = create_material('MAT_Beam_Yellow', (1.0, 0.72, 0.0, 1.0), metallic=0.6, roughness=0.45)
    mat_deck = create_material('MAT_Deck_Gray', (0.5, 0.5, 0.5, 1.0), metallic=0.3, roughness=0.6)
    mat_brace = create_material('MAT_Brace_Orange', (1.0, 0.4, 0.0, 1.0), metallic=0.6, roughness=0.45)

    # --- 4 upright posts at corners ---
    upright_positions = [
        (0, 0),
        (width, 0),
        (0, depth),
        (width, depth),
    ]
    for (ux, uy) in upright_positions:
        p = create_box(
            'Upright',
            sx=upright_w / 2, sy=upright_d / 2, sz=height / 2,
            loc=(ux, uy, height / 2),
        )
        p.data.materials.append(mat_upright)
        parts.append(p)

    # --- Horizontal beams at each level ---
    level_spacing = height / levels
    for lvl in range(levels):
        beam_z = level_spacing * (lvl + 1)
        if beam_z > height:
            beam_z = height

        # Front beam (Y=0)
        p = create_box(
            'BeamFront',
            sx=width / 2, sy=beam_w / 2, sz=beam_h / 2,
            loc=(width / 2, 0, beam_z),
        )
        p.data.materials.append(mat_beam)
        parts.append(p)

        # Back beam (Y=depth)
        p = create_box(
            'BeamBack',
            sx=width / 2, sy=beam_w / 2, sz=beam_h / 2,
            loc=(width / 2, depth, beam_z),
        )
        p.data.materials.append(mat_beam)
        parts.append(p)

        # Shelf deck — thin board spanning the bay
        deck_z = beam_z + beam_h / 2 + deck_thickness / 2
        p = create_box(
            'Deck',
            sx=width / 2, sy=depth / 2, sz=deck_thickness / 2,
            loc=(width / 2, depth / 2, deck_z),
        )
        p.data.materials.append(mat_deck)
        parts.append(p)

    # --- X-bracing on each side ---
    for side_y in [0, depth]:
        for lvl in range(levels):
            z_bot = level_spacing * lvl
            z_top = level_spacing * (lvl + 1)
            z_mid = (z_bot + z_top) / 2
            span_z = z_top - z_bot

            # Diagonal brace — represented as a thin rotated box
            # Brace from (0, side_y, z_bot) to (0, side_y, z_top) — on the side frame
            # Actually X-brace goes across the depth on each side
            # Side frame bracing: from bottom-front to top-back of the side panel

            # We'll use two diagonal braces forming an X
            # Side panel spans from Y=side_y (fixed), X from 0 to 0, Z from z_bot to z_top
            # Actually side bracing is in the Y-Z plane at X=0 and X=width

    # Simpler approach: side horizontal bracing using thin boxes at the side panels
    # Cross bracing on left side (X=0) and right side (X=width)
    for side_x in [0, width]:
        for lvl in range(levels):
            z_bot = level_spacing * lvl
            z_top = level_spacing * (lvl + 1)
            z_mid = (z_bot + z_top) / 2
            span_z = z_top - z_bot

            # Diagonal length
            diag_len = math.sqrt(depth ** 2 + span_z ** 2)
            diag_angle = math.atan2(span_z, depth)

            # Brace 1: from (side_x, 0, z_bot) to (side_x, depth, z_top)
            bm = bmesh.new()
            hw = brace_w / 2
            hl = diag_len / 2
            # Create a thin box along local X axis, then rotate
            v = [
                bm.verts.new((-hl, -hw, -hw)),
                bm.verts.new((hl, -hw, -hw)),
                bm.verts.new((hl, hw, -hw)),
                bm.verts.new((-hl, hw, -hw)),
                bm.verts.new((-hl, -hw, hw)),
                bm.verts.new((hl, -hw, hw)),
                bm.verts.new((hl, hw, hw)),
                bm.verts.new((-hl, hw, hw)),
            ]
            bm.faces.new([v[0], v[1], v[2], v[3]])
            bm.faces.new([v[4], v[7], v[6], v[5]])
            bm.faces.new([v[0], v[4], v[5], v[1]])
            bm.faces.new([v[2], v[6], v[7], v[3]])
            bm.faces.new([v[0], v[3], v[7], v[4]])
            bm.faces.new([v[1], v[5], v[6], v[2]])
            bm.normal_update()

            mesh = bpy.data.meshes.new('Brace_Mesh')
            bm.to_mesh(mesh)
            bm.free()
            brace_obj = bpy.data.objects.new('Brace', mesh)
            bpy.context.collection.objects.link(brace_obj)

            # Position and rotate
            brace_obj.location = (side_x, depth / 2, z_mid)
            brace_obj.rotation_euler = (diag_angle, 0, 0)  # rotate around X in YZ plane

            bpy.context.view_layer.objects.active = brace_obj
            brace_obj.select_set(True)
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
            brace_obj.select_set(False)

            brace_obj.data.materials.append(mat_brace)
            parts.append(brace_obj)

            # Brace 2: opposite diagonal
            bm2 = bmesh.new()
            v2 = [
                bm2.verts.new((-hl, -hw, -hw)),
                bm2.verts.new((hl, -hw, -hw)),
                bm2.verts.new((hl, hw, -hw)),
                bm2.verts.new((-hl, hw, -hw)),
                bm2.verts.new((-hl, -hw, hw)),
                bm2.verts.new((hl, -hw, hw)),
                bm2.verts.new((hl, hw, hw)),
                bm2.verts.new((-hl, hw, hw)),
            ]
            bm2.faces.new([v2[0], v2[1], v2[2], v2[3]])
            bm2.faces.new([v2[4], v2[7], v2[6], v2[5]])
            bm2.faces.new([v2[0], v2[4], v2[5], v2[1]])
            bm2.faces.new([v2[2], v2[6], v2[7], v2[3]])
            bm2.faces.new([v2[0], v2[3], v2[7], v2[4]])
            bm2.faces.new([v2[1], v2[5], v2[6], v2[2]])
            bm2.normal_update()

            mesh2 = bpy.data.meshes.new('Brace2_Mesh')
            bm2.to_mesh(mesh2)
            bm2.free()
            brace2 = bpy.data.objects.new('Brace2', mesh2)
            bpy.context.collection.objects.link(brace2)

            brace2.location = (side_x, depth / 2, z_mid)
            brace2.rotation_euler = (-diag_angle, 0, 0)

            bpy.context.view_layer.objects.active = brace2
            brace2.select_set(True)
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
            brace2.select_set(False)

            brace2.data.materials.append(mat_brace)
            parts.append(brace2)

    # --- Join everything ---
    bpy.ops.object.select_all(action='DESELECT')
    for p in parts:
        p.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()

    rack = bpy.context.view_layer.objects.active
    rack.name = 'WH_Shelf_Rack'
    rack.data.name = 'WH_Shelf_Rack_Mesh'

    return rack


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    try:
        args = parse_args()
        clean_scene()

        rack = build_shelf_rack(args.levels, args.width, args.depth, args.height)

        # Apply transforms
        bpy.ops.object.select_all(action='DESELECT')
        rack.select_set(True)
        bpy.context.view_layer.objects.active = rack
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        # Smart UV
        smart_uv_project(rack)

        # Origin
        set_origin_bottom_center(rack)

        # Output
        out_dir = os.path.dirname(os.path.abspath(args.output))
        os.makedirs(out_dir, exist_ok=True)

        bpy.ops.export_scene.gltf(
            filepath=args.output,
            export_format='GLB',
            use_selection=True,
            export_apply=True,
        )

        dims = rack.dimensions
        result = {
            'status': 'success',
            'name': rack.name,
            'vertices': len(rack.data.vertices),
            'faces': len(rack.data.polygons),
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
