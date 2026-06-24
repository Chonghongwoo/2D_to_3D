"""
CleanMesh — WH_Drum_200L Generator
====================================
Generate a realistic 200-litre industrial drum (ISO 15750 proportions).

Usage:
    blender --background --python drum_200l.py -- --output <path> [options]

Options:
    --height    Total height in metres        (default: 0.88)
    --diameter  Outer diameter in metres       (default: 0.585)
    --has-lid   Add top lid with bung holes
    --color     Hex colour for body            (default: #2B5EA7)
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
    ap = argparse.ArgumentParser(description='Generate a 200L industrial drum.')
    ap.add_argument('--output', type=str, required=True, help='Output GLB file path')
    ap.add_argument('--height', type=float, default=0.88, help='Total height (m)')
    ap.add_argument('--diameter', type=float, default=0.585, help='Outer diameter (m)')
    ap.add_argument('--has-lid', action='store_true', help='Add lid with bung holes')
    ap.add_argument('--color', type=str, default='#2B5EA7', help='Body colour hex')
    return ap.parse_args(argv)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def hex_to_rgb(hex_str: str) -> tuple:
    """Convert '#RRGGBB' to linear (R, G, B, 1.0)."""
    h = hex_str.lstrip('#')
    srgb = tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))
    # sRGB → linear
    linear = tuple(((c / 12.92) if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4) for c in srgb)
    return (*linear, 1.0)


def clean_scene():
    """Remove all objects, meshes, materials from the scene."""
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for block in bpy.data.meshes:
        bpy.data.meshes.remove(block)
    for block in bpy.data.materials:
        bpy.data.materials.remove(block)


def set_origin_bottom_center(obj):
    """Move origin to bottom-center of the bounding box."""
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    # Apply transforms first
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    # Calculate bottom center
    local_verts = [v.co for v in obj.data.vertices]
    if not local_verts:
        return
    min_z = min(v.z for v in local_verts)
    center_x = (min(v.x for v in local_verts) + max(v.x for v in local_verts)) / 2
    center_y = (min(v.y for v in local_verts) + max(v.y for v in local_verts)) / 2
    offset = Vector((center_x, center_y, min_z))
    for v in obj.data.vertices:
        v.co -= offset
    obj.location += offset
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def smart_uv_project(obj):
    """Apply Smart UV Project to the object."""
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=66.0, island_margin=0.02)
    bpy.ops.object.mode_set(mode='OBJECT')


# ---------------------------------------------------------------------------
# Drum construction
# ---------------------------------------------------------------------------

def create_drum_body(radius, height, segments=64):
    """Create the main cylinder body with bottom taper and reinforcement ribs."""
    bm = bmesh.new()

    # We build from bottom to top using stacked rings.
    # Bottom taper: slightly smaller radius at the very bottom edge
    taper_inset = 0.004  # 4mm inward taper
    taper_height = 0.015  # 15mm taper zone

    # Rib parameters
    rib_height = 0.012  # 12mm tall
    rib_depth = 0.004   # 4mm outward protrusion
    num_ribs = 3

    # Define Z slices from bottom to top
    # (z_position, radius_at_this_ring)
    rings = []

    # Bottom taper
    rings.append((0.0, radius - taper_inset))
    rings.append((taper_height, radius))

    # Bottom rim
    rim_height = 0.012
    rim_protrusion = 0.003
    rings.append((taper_height + 0.001, radius + rim_protrusion))
    rings.append((taper_height + rim_height, radius + rim_protrusion))
    rings.append((taper_height + rim_height + 0.001, radius))

    body_start = taper_height + rim_height + 0.001
    body_end = height - rim_height - 0.001

    # Reinforcement ribs evenly spaced
    body_span = body_end - body_start
    for i in range(num_ribs):
        rib_center = body_start + body_span * (i + 1) / (num_ribs + 1)
        rib_bot = rib_center - rib_height / 2
        rib_top = rib_center + rib_height / 2
        rings.append((rib_bot - 0.001, radius))
        rings.append((rib_bot, radius + rib_depth))
        rings.append((rib_top, radius + rib_depth))
        rings.append((rib_top + 0.001, radius))

    # Top rim
    rings.append((body_end, radius))
    rings.append((body_end + 0.001, radius + rim_protrusion))
    rings.append((height - 0.001, radius + rim_protrusion))
    rings.append((height, radius))

    # Sort rings by Z
    rings.sort(key=lambda r: r[0])

    # Create vertices ring by ring
    vert_rings = []
    for z, r in rings:
        ring_verts = []
        for j in range(segments):
            angle = 2 * math.pi * j / segments
            x = r * math.cos(angle)
            y = r * math.sin(angle)
            ring_verts.append(bm.verts.new((x, y, z)))
        vert_rings.append(ring_verts)

    bm.verts.ensure_lookup_table()

    # Create faces between adjacent rings (quads)
    for i in range(len(vert_rings) - 1):
        lower = vert_rings[i]
        upper = vert_rings[i + 1]
        for j in range(segments):
            j_next = (j + 1) % segments
            bm.faces.new([lower[j], lower[j_next], upper[j_next], upper[j]])

    # Cap bottom
    bottom_verts = vert_rings[0]
    center_bot = bm.verts.new((0, 0, rings[0][0]))
    for j in range(segments):
        j_next = (j + 1) % segments
        bm.faces.new([center_bot, bottom_verts[j_next], bottom_verts[j]])

    # Cap top
    top_verts = vert_rings[-1]
    center_top = bm.verts.new((0, 0, rings[-1][0]))
    for j in range(segments):
        j_next = (j + 1) % segments
        bm.faces.new([center_top, top_verts[j], top_verts[j_next]])

    bm.faces.ensure_lookup_table()
    bm.normal_update()

    mesh = bpy.data.meshes.new('WH_Drum_200L_Mesh')
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new('WH_Drum_200L', mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def create_lid_with_bungs(radius, height, segments=64):
    """Create a top lid disc with two bung holes."""
    bm = bmesh.new()

    lid_thickness = 0.003
    lid_z_bot = height - lid_thickness
    lid_z_top = height

    # Lid disc (slightly inset from body radius)
    lid_radius = radius - 0.005

    # Create lid as a simple disc with thickness
    bot_ring = []
    top_ring = []
    for j in range(segments):
        angle = 2 * math.pi * j / segments
        x = lid_radius * math.cos(angle)
        y = lid_radius * math.sin(angle)
        bot_ring.append(bm.verts.new((x, y, lid_z_bot)))
        top_ring.append(bm.verts.new((x, y, lid_z_top)))

    # Side faces
    for j in range(segments):
        j_next = (j + 1) % segments
        bm.faces.new([bot_ring[j], bot_ring[j_next], top_ring[j_next], top_ring[j]])

    # Bottom cap
    cbot = bm.verts.new((0, 0, lid_z_bot))
    for j in range(segments):
        j_next = (j + 1) % segments
        bm.faces.new([cbot, bot_ring[j_next], bot_ring[j]])

    # Top cap
    ctop = bm.verts.new((0, 0, lid_z_top))
    for j in range(segments):
        j_next = (j + 1) % segments
        bm.faces.new([ctop, top_ring[j], top_ring[j_next]])

    bm.faces.ensure_lookup_table()
    bm.normal_update()

    mesh = bpy.data.meshes.new('WH_Drum_200L_Lid_Mesh')
    bm.to_mesh(mesh)
    bm.free()

    lid_obj = bpy.data.objects.new('WH_Drum_200L_Lid', mesh)
    bpy.context.collection.objects.link(lid_obj)

    # Bung holes — two small raised cylinders on top
    bung_positions = [(0.08, 0.0), (-0.08, 0.0)]
    bung_radius = 0.025
    bung_height = 0.012
    bung_segments = 24

    for idx, (bx, by) in enumerate(bung_positions):
        bm2 = bmesh.new()
        bot_r2 = []
        top_r2 = []
        for j in range(bung_segments):
            angle = 2 * math.pi * j / bung_segments
            x = bx + bung_radius * math.cos(angle)
            y = by + bung_radius * math.sin(angle)
            bot_r2.append(bm2.verts.new((x, y, lid_z_top)))
            top_r2.append(bm2.verts.new((x, y, lid_z_top + bung_height)))

        for j in range(bung_segments):
            j_next = (j + 1) % bung_segments
            bm2.faces.new([bot_r2[j], bot_r2[j_next], top_r2[j_next], top_r2[j]])

        cb = bm2.verts.new((bx, by, lid_z_top))
        ct = bm2.verts.new((bx, by, lid_z_top + bung_height))
        for j in range(bung_segments):
            j_next = (j + 1) % bung_segments
            bm2.faces.new([cb, bot_r2[j_next], bot_r2[j]])
            bm2.faces.new([ct, top_r2[j], top_r2[j_next]])

        bm2.faces.ensure_lookup_table()
        bm2.normal_update()

        mesh2 = bpy.data.meshes.new(f'WH_Drum_200L_Bung{idx}_Mesh')
        bm2.to_mesh(mesh2)
        bm2.free()

        bung_obj = bpy.data.objects.new(f'WH_Drum_200L_Bung{idx}', mesh2)
        bpy.context.collection.objects.link(bung_obj)

    return lid_obj


def create_drum_material(color_hex):
    """Create metallic drum material."""
    mat = bpy.data.materials.new(name='MAT_Drum_Body')
    if hasattr(mat, "use_nodes"): mat.use_nodes = True  # Blender 6.0 가드
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf:
        bsdf.inputs['Base Color'].default_value = hex_to_rgb(color_hex)
        bsdf.inputs['Metallic'].default_value = 0.8
        bsdf.inputs['Roughness'].default_value = 0.4
    return mat


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    try:
        args = parse_args()
        clean_scene()

        radius = args.diameter / 2.0
        height = args.height

        # Body
        body = create_drum_body(radius, height)

        # Material
        mat = create_drum_material(args.color)
        body.data.materials.append(mat)

        # Lid
        if args.has_lid:
            lid = create_lid_with_bungs(radius, height)
            lid.data.materials.append(mat)
            # Join bung objects to lid, then lid to body
            bpy.ops.object.select_all(action='DESELECT')
            for obj in bpy.data.objects:
                if 'Bung' in obj.name or 'Lid' in obj.name:
                    obj.select_set(True)
                    obj.data.materials.append(mat)
            body.select_set(True)
            bpy.context.view_layer.objects.active = body
            bpy.ops.object.join()
            # After join, active is the merged body
            body = bpy.context.view_layer.objects.active
        # else: body keeps the reference returned by create_drum_body()

        # Ensure body is active and selected for the next operators
        bpy.ops.object.select_all(action='DESELECT')
        body.select_set(True)
        bpy.context.view_layer.objects.active = body

        # Rename
        body.name = 'WH_Drum_200L'
        body.data.name = 'WH_Drum_200L_Mesh'

        # Apply transforms
        bpy.ops.object.select_all(action='DESELECT')
        body.select_set(True)
        bpy.context.view_layer.objects.active = body
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        # Smart UV Project
        smart_uv_project(body)

        # Origin to bottom center
        set_origin_bottom_center(body)

        # Smooth shading
        bpy.ops.object.shade_smooth()

        # Ensure output directory exists
        out_dir = os.path.dirname(os.path.abspath(args.output))
        os.makedirs(out_dir, exist_ok=True)

        # Export GLB
        bpy.ops.export_scene.gltf(
            filepath=args.output,
            export_format='GLB',
            use_selection=True,
            export_apply=True,
        )

        # Collect stats
        dims = body.dimensions
        result = {
            'status': 'success',
            'name': body.name,
            'vertices': len(body.data.vertices),
            'faces': len(body.data.polygons),
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
