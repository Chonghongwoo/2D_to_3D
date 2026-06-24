"""
CleanMesh — AGV_Pallet_EUR Generator
======================================
Generate a standard EUR/EPAL pallet (EUR1, EUR2, EUR3).

Usage:
    blender --background --python pallet_eur.py -- --output <path> [options]

Options:
    --type       EUR1 | EUR2 | EUR3   (default: EUR1)
    --condition  new | used           (default: new)
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
# Pallet specifications (all in metres)
# ---------------------------------------------------------------------------

PALLET_SPECS = {
    'EUR1': {'length': 1.200, 'width': 0.800, 'height': 0.144},
    'EUR2': {'length': 1.200, 'width': 1.000, 'height': 0.144},
    'EUR3': {'length': 1.000, 'width': 1.200, 'height': 0.144},
}

# Internal construction dimensions (metres) — EPAL EUR1 standard
# Total = BOARD_THICKNESS (top) + BLOCK_HEIGHT + BOARD_THICKNESS (bottom)
#       = 0.022 + 0.100 + 0.022 = 0.144 m  ✓ matches ISO/EPAL spec
BOARD_THICKNESS = 0.022       # thickness of deck/bottom boards
BLOCK_HEIGHT = 0.100          # block height (EPAL spec)
BLOCK_SIZE_CORNER = 0.100     # corner block side length
BLOCK_SIZE_CENTER = 0.145     # centre block length
BOTTOM_BOARD_WIDTH = 0.100    # bottom runner board width
TOP_BOARD_GAP = 0.040         # gap between top deck boards


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    ap = argparse.ArgumentParser(description='Generate a EUR pallet.')
    ap.add_argument('--output', type=str, required=True, help='Output GLB path')
    ap.add_argument('--type', type=str, default='EUR1', choices=['EUR1', 'EUR2', 'EUR3'])
    ap.add_argument('--condition', type=str, default='new', choices=['new', 'used'])
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
    """Create an axis-aligned box centred at loc with half-sizes sx, sy, sz."""
    bm = bmesh.new()
    verts = [
        bm.verts.new((loc[0] - sx, loc[1] - sy, loc[2] - sz)),
        bm.verts.new((loc[0] + sx, loc[1] - sy, loc[2] - sz)),
        bm.verts.new((loc[0] + sx, loc[1] + sy, loc[2] - sz)),
        bm.verts.new((loc[0] - sx, loc[1] + sy, loc[2] - sz)),
        bm.verts.new((loc[0] - sx, loc[1] - sy, loc[2] + sz)),
        bm.verts.new((loc[0] + sx, loc[1] - sy, loc[2] + sz)),
        bm.verts.new((loc[0] + sx, loc[1] + sy, loc[2] + sz)),
        bm.verts.new((loc[0] - sx, loc[1] + sy, loc[2] + sz)),
    ]
    bm.faces.new([verts[0], verts[1], verts[2], verts[3]])  # bottom
    bm.faces.new([verts[4], verts[7], verts[6], verts[5]])  # top
    bm.faces.new([verts[0], verts[4], verts[5], verts[1]])  # front
    bm.faces.new([verts[2], verts[6], verts[7], verts[3]])  # back
    bm.faces.new([verts[0], verts[3], verts[7], verts[4]])  # left
    bm.faces.new([verts[1], verts[5], verts[6], verts[2]])  # right
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


def create_wood_material(condition='new'):
    """Principled BSDF wood material."""
    mat = bpy.data.materials.new(name='MAT_Wood_Pallet')
    if hasattr(mat, "use_nodes"): mat.use_nodes = True  # Blender 6.0 가드
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf:
        if condition == 'new':
            # Clean pine colour
            bsdf.inputs['Base Color'].default_value = (0.480, 0.380, 0.240, 1.0)
        else:
            # Weathered / used
            bsdf.inputs['Base Color'].default_value = (0.350, 0.280, 0.180, 1.0)
        bsdf.inputs['Roughness'].default_value = 0.70
        bsdf.inputs['Metallic'].default_value = 0.0
    return mat


# ---------------------------------------------------------------------------
# Pallet construction
# ---------------------------------------------------------------------------

def build_pallet(pallet_type='EUR1', condition='new'):
    """Build complete pallet geometry and return the joined object."""
    spec = PALLET_SPECS[pallet_type]
    L = spec['length']   # X axis
    W = spec['width']    # Y axis
    H = spec['height']   # Z axis (total)

    parts = []

    # --- 3 bottom boards (skids) running along X, at front/center/back in Y ---
    skid_thickness = BOARD_THICKNESS
    skid_width = BOTTOM_BOARD_WIDTH
    skid_positions_y = [skid_width / 2, W / 2, W - skid_width / 2]
    for yp in skid_positions_y:
        p = create_box(
            'Skid',
            sx=L / 2, sy=skid_width / 2, sz=skid_thickness / 2,
            loc=(L / 2, yp, skid_thickness / 2)
        )
        parts.append(p)

    # --- 9 blocks (3 rows × 3 columns) ---
    block_z_bot = skid_thickness
    block_z_center = block_z_bot + BLOCK_HEIGHT / 2
    block_x_positions = [BLOCK_SIZE_CORNER / 2, L / 2, L - BLOCK_SIZE_CORNER / 2]
    block_y_positions = skid_positions_y

    for bx in block_x_positions:
        bw_x = BLOCK_SIZE_CENTER / 2 if bx == L / 2 else BLOCK_SIZE_CORNER / 2
        for by in block_y_positions:
            bw_y = BLOCK_SIZE_CORNER / 2
            p = create_box(
                'Block',
                sx=bw_x, sy=bw_y, sz=BLOCK_HEIGHT / 2,
                loc=(bx, by, block_z_center)
            )
            parts.append(p)

    # --- 5 top deck boards ---
    deck_z_bot = skid_thickness + BLOCK_HEIGHT
    deck_z_center = deck_z_bot + BOARD_THICKNESS / 2
    # Evenly distribute 5 boards across W with gaps
    num_deck = 5
    total_gap = (num_deck - 1) * TOP_BOARD_GAP
    board_width = (W - total_gap) / num_deck

    for i in range(num_deck):
        board_y_center = board_width / 2 + i * (board_width + TOP_BOARD_GAP)
        p = create_box(
            'DeckBoard',
            sx=L / 2, sy=board_width / 2, sz=BOARD_THICKNESS / 2,
            loc=(L / 2, board_y_center, deck_z_center)
        )
        parts.append(p)

    # --- 2 bottom runner boards (perpendicular, running along Y) ---
    runner_z_center = skid_thickness / 2
    runner_x_positions = [BLOCK_SIZE_CORNER / 2 + BLOCK_SIZE_CORNER,
                          L - BLOCK_SIZE_CORNER / 2 - BLOCK_SIZE_CORNER]
    for rx in runner_x_positions:
        p = create_box(
            'Runner',
            sx=BOTTOM_BOARD_WIDTH / 2, sy=W / 2, sz=skid_thickness / 2,
            loc=(rx, W / 2, runner_z_center)
        )
        parts.append(p)

    # Join all parts
    bpy.ops.object.select_all(action='DESELECT')
    for p in parts:
        p.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()

    result_obj = bpy.context.view_layer.objects.active
    result_obj.name = f'AGV_Pallet_{pallet_type}'
    result_obj.data.name = f'AGV_Pallet_{pallet_type}_Mesh'

    # Material
    mat = create_wood_material(condition)
    result_obj.data.materials.append(mat)

    return result_obj


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    try:
        args = parse_args()
        clean_scene()

        pallet = build_pallet(args.type, args.condition)

        # Apply transforms
        bpy.ops.object.select_all(action='DESELECT')
        pallet.select_set(True)
        bpy.context.view_layer.objects.active = pallet
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        # Smart UV
        smart_uv_project(pallet)

        # Origin bottom center
        set_origin_bottom_center(pallet)

        # Ensure output dir
        out_dir = os.path.dirname(os.path.abspath(args.output))
        os.makedirs(out_dir, exist_ok=True)

        # Export GLB
        bpy.ops.export_scene.gltf(
            filepath=args.output,
            export_format='GLB',
            use_selection=True,
            export_apply=True,
        )

        dims = pallet.dimensions
        result = {
            'status': 'success',
            'name': pallet.name,
            'vertices': len(pallet.data.vertices),
            'faces': len(pallet.data.polygons),
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
