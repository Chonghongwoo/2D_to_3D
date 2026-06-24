"""
CleanMesh — AGV_Conveyor_Roller Generator
==========================================
Generate a gravity roller conveyor section.

Usage:
    blender --background --python conveyor_roller.py -- --output <path> [options]

Options:
    --length        Conveyor length in metres (X)   (default: 2.0)
    --width         Conveyor width in metres (Y)    (default: 0.6)
    --roller-count  Number of rollers               (default: 10)
    --height        Conveyor height in metres (Z)   (default: 0.75)
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
    ap = argparse.ArgumentParser(description='Generate a roller conveyor section.')
    ap.add_argument('--output', type=str, required=True, help='Output GLB path')
    ap.add_argument('--length', type=float, default=2.0, help='Length X (m)')
    ap.add_argument('--width', type=float, default=0.6, help='Width Y (m)')
    ap.add_argument('--roller-count', type=int, default=10, help='Number of rollers')
    ap.add_argument('--height', type=float, default=0.75, help='Height Z (m)')
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


def create_cylinder(name, radius, half_height, loc, segments=32):
    """Create a cylinder along Y axis centred at *loc*."""
    bm = bmesh.new()

    top_ring = []
    bot_ring = []
    for i in range(segments):
        angle = 2 * math.pi * i / segments
        x = loc[0] + radius * math.cos(angle)
        z = loc[2] + radius * math.sin(angle)
        bot_ring.append(bm.verts.new((x, loc[1] - half_height, z)))
        top_ring.append(bm.verts.new((x, loc[1] + half_height, z)))

    # Side quads
    for i in range(segments):
        i_next = (i + 1) % segments
        bm.faces.new([bot_ring[i], bot_ring[i_next], top_ring[i_next], top_ring[i]])

    # Caps
    cbot = bm.verts.new((loc[0], loc[1] - half_height, loc[2]))
    ctop = bm.verts.new((loc[0], loc[1] + half_height, loc[2]))
    for i in range(segments):
        i_next = (i + 1) % segments
        bm.faces.new([cbot, bot_ring[i_next], bot_ring[i]])
        bm.faces.new([ctop, top_ring[i], top_ring[i_next]])

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


def create_material(name, color_rgba, metallic=0.5, roughness=0.5):
    mat = bpy.data.materials.new(name=name)
    if hasattr(mat, "use_nodes"): mat.use_nodes = True  # Blender 6.0 가드
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf:
        bsdf.inputs['Base Color'].default_value = color_rgba
        bsdf.inputs['Metallic'].default_value = metallic
        bsdf.inputs['Roughness'].default_value = roughness
    return mat


# ---------------------------------------------------------------------------
# Conveyor construction
# ---------------------------------------------------------------------------

def build_conveyor(length, width, roller_count, height):
    """Build a gravity roller conveyor section."""

    # Dimensions
    rail_w = 0.050          # rail cross-section width
    rail_h = 0.060          # rail cross-section height
    leg_w = 0.040           # leg cross-section
    roller_radius = 0.025   # roller radius
    roller_gap = 0.005      # gap between roller end and rail inner face

    parts = []

    # Materials
    mat_roller = create_material('MAT_Roller_Silver', (0.78, 0.78, 0.78, 1.0), metallic=0.9, roughness=0.25)
    mat_frame = create_material('MAT_Frame_DarkGray', (0.25, 0.25, 0.25, 1.0), metallic=0.7, roughness=0.45)

    # Rail top Z
    rail_top_z = height
    rail_center_z = height - rail_h / 2

    # --- Two side rails ---
    # Rail at Y = 0 side
    rail_left = create_box(
        'RailLeft',
        sx=length / 2, sy=rail_w / 2, sz=rail_h / 2,
        loc=(length / 2, 0, rail_center_z),
    )
    rail_left.data.materials.append(mat_frame)
    parts.append(rail_left)

    # Rail at Y = width side
    rail_right = create_box(
        'RailRight',
        sx=length / 2, sy=rail_w / 2, sz=rail_h / 2,
        loc=(length / 2, width, rail_center_z),
    )
    rail_right.data.materials.append(mat_frame)
    parts.append(rail_right)

    # --- Rollers ---
    roller_y_center = width / 2
    roller_half_len = (width - rail_w - 2 * roller_gap) / 2
    roller_center_z = rail_center_z  # rollers sit between rails

    spacing = length / (roller_count + 1)
    for i in range(roller_count):
        rx = spacing * (i + 1)
        roller = create_cylinder(
            f'Roller_{i:02d}',
            radius=roller_radius,
            half_height=roller_half_len,
            loc=(rx, roller_y_center, roller_center_z),
            segments=24,
        )
        roller.data.materials.append(mat_roller)
        parts.append(roller)

    # --- Support legs (4 legs — one at each corner) ---
    leg_height = height - rail_h
    leg_half_h = leg_height / 2
    leg_positions = [
        (leg_w / 2 + 0.01, 0, leg_half_h),
        (length - leg_w / 2 - 0.01, 0, leg_half_h),
        (leg_w / 2 + 0.01, width, leg_half_h),
        (length - leg_w / 2 - 0.01, width, leg_half_h),
    ]
    for idx, (lx, ly, lz) in enumerate(leg_positions):
        leg = create_box(
            f'Leg_{idx}',
            sx=leg_w / 2, sy=leg_w / 2, sz=leg_half_h,
            loc=(lx, ly, lz),
        )
        leg.data.materials.append(mat_frame)
        parts.append(leg)

    # --- Cross support bars (bottom, connecting front legs to back legs) ---
    cross_z = leg_w / 2  # near the floor
    for lx in [leg_w / 2 + 0.01, length - leg_w / 2 - 0.01]:
        cross = create_box(
            'CrossBar',
            sx=leg_w / 2, sy=width / 2, sz=leg_w / 2,
            loc=(lx, width / 2, cross_z),
        )
        cross.data.materials.append(mat_frame)
        parts.append(cross)

    # --- Join everything ---
    bpy.ops.object.select_all(action='DESELECT')
    for p in parts:
        p.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()

    conveyor = bpy.context.view_layer.objects.active
    conveyor.name = 'AGV_Conveyor_Roller'
    conveyor.data.name = 'AGV_Conveyor_Roller_Mesh'

    return conveyor


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    try:
        args = parse_args()
        clean_scene()

        conveyor = build_conveyor(args.length, args.width, args.roller_count, args.height)

        # Apply transforms
        bpy.ops.object.select_all(action='DESELECT')
        conveyor.select_set(True)
        bpy.context.view_layer.objects.active = conveyor
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        # Smart UV
        smart_uv_project(conveyor)

        # Origin
        set_origin_bottom_center(conveyor)

        # Smooth shading for rollers (whole object for simplicity)
        bpy.ops.object.shade_smooth()

        # Auto smooth to keep rails flat
        # Blender 3.x: mesh.use_auto_smooth / auto_smooth_angle
        # Blender 4.0+: removed — use operator (Smooth by Angle modifier)
        if hasattr(conveyor.data, "use_auto_smooth"):
            conveyor.data.use_auto_smooth = True
            conveyor.data.auto_smooth_angle = math.radians(35)
        else:
            try:
                bpy.ops.object.shade_auto_smooth(angle=math.radians(35))
            except (AttributeError, RuntimeError):
                # Fall back silently — shade_smooth() above still gives smooth shading
                pass

        # Output
        out_dir = os.path.dirname(os.path.abspath(args.output))
        os.makedirs(out_dir, exist_ok=True)

        bpy.ops.export_scene.gltf(
            filepath=args.output,
            export_format='GLB',
            use_selection=True,
            export_apply=True,
        )

        dims = conveyor.dimensions
        result = {
            'status': 'success',
            'name': conveyor.name,
            'vertices': len(conveyor.data.vertices),
            'faces': len(conveyor.data.polygons),
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
