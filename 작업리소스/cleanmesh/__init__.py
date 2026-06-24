"""
CleanMesh — Image-to-3D Pipeline for Digital Twin Asset Creation

Generates clean, digital-twin-ready 3D assets (GLB / USD / FBX) from
images or text descriptions, with optional metadata embedding
(category, dimensions, manufacturer, serial).

Supports procedural generation (bpy), TripoSR (fast), and TRELLIS (quality).
All meshes are cleaned and validated via Blender headless pipeline.
"""

__version__ = "1.2.0"
