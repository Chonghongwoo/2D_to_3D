"""
CleanMesh Blender Template Registry
====================================
Maps template names to their generator module names.
Each module is a standalone script runnable via:
    blender --background --python <module>.py -- [args]
"""

TEMPLATES = {
    'drum_200l': 'drum_200l',
    'pallet_eur': 'pallet_eur',
    'box_cargo': 'box_cargo',
    'shelf_rack': 'shelf_rack',
    'conveyor_roller': 'conveyor_roller',
}


def get_template(name: str) -> str:
    """Return the module name for a given template key."""
    if name not in TEMPLATES:
        raise KeyError(
            f"Unknown template '{name}'. "
            f"Available: {', '.join(sorted(TEMPLATES.keys()))}"
        )
    return TEMPLATES[name]


def list_templates() -> list[str]:
    """Return a sorted list of available template names."""
    return sorted(TEMPLATES.keys())
