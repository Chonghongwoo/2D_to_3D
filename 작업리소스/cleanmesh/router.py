"""
CleanMesh Router — Analyzes input and decides which generation path to use.

Routes:
  - PROCEDURAL: Regular/mechanical objects (drums, pallets, boxes, shelves, conveyors)
  - TRIPOSR: Single image + speed priority
  - TRELLIS_SINGLE: Single image + quality priority (Phase 2)
  - TRELLIS_MULTI: Multiple images (Phase 2)
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional


class GenerationMethod(Enum):
    PROCEDURAL = "procedural"
    TRIPOSR = "triposr"
    TRELLIS_SINGLE = "trellis_single"
    TRELLIS_MULTI = "trellis_multi"


# Known procedural templates and their keyword triggers
PROCEDURAL_KEYWORDS = {
    "drum": "drum_200l",
    "드럼": "drum_200l",
    "배럴": "drum_200l",
    "barrel": "drum_200l",
    "pallet": "pallet_eur",
    "팔레트": "pallet_eur",
    "파렛트": "pallet_eur",
    "box": "box_cargo",
    "박스": "box_cargo",
    "상자": "box_cargo",
    "cargo": "box_cargo",
    "shelf": "shelf_rack",
    "선반": "shelf_rack",
    "rack": "shelf_rack",
    "랙": "shelf_rack",
    "conveyor": "conveyor_roller",
    "컨베이어": "conveyor_roller",
    "roller": "conveyor_roller",
    "롤러": "conveyor_roller",
}


@dataclass
class RoutingDecision:
    """Result of routing analysis."""
    method: GenerationMethod
    reason: str
    template_name: Optional[str] = None  # For procedural
    template_params: Optional[dict] = None  # For procedural
    image_paths: Optional[List[str]] = None  # For image-based
    naming_prefix: str = "OBJ"  # Domain prefix for naming


def route(
    description: Optional[str] = None,
    image_paths: Optional[List[str]] = None,
    quality: str = "speed",  # "speed" or "quality"
    force_method: Optional[str] = None,
) -> RoutingDecision:
    """
    Analyze input and decide generation path.

    Args:
        description: Text description of the object
        image_paths: List of input image paths
        quality: "speed" or "quality"
        force_method: Override routing (procedural/triposr/trellis)

    Returns:
        RoutingDecision with method, reason, and parameters
    """
    # Forced method override
    if force_method:
        method = GenerationMethod(force_method)
        return RoutingDecision(
            method=method,
            reason=f"사용자 지정 경로: {force_method}",
            image_paths=image_paths,
        )

    num_images = len(image_paths) if image_paths else 0

    # Check for procedural match if description is given
    if description:
        desc_lower = description.lower()
        for keyword, template in PROCEDURAL_KEYWORDS.items():
            if keyword in desc_lower:
                # Determine domain prefix
                prefix = "WH"  # Warehouse default
                if "agv" in desc_lower or "conveyor" in desc_lower:
                    prefix = "AGV"

                return RoutingDecision(
                    method=GenerationMethod.PROCEDURAL,
                    reason=f"규칙적 객체 감지 → 절차적 bpy 생성 ({template})",
                    template_name=template,
                    template_params=_extract_params(desc_lower, template),
                    naming_prefix=prefix,
                )

    # Image-based routing
    if num_images == 0 and description:
        # Text only — need to generate image first or use procedural
        return RoutingDecision(
            method=GenerationMethod.TRIPOSR,
            reason="텍스트만 입력 → TripoSR (텍스트→이미지→3D)",
            image_paths=[],
            naming_prefix="GEN",
        )

    if num_images == 1:
        if quality == "quality":
            return RoutingDecision(
                method=GenerationMethod.TRELLIS_SINGLE,
                reason="이미지 1장 + 품질 우선 → TRELLIS (단일)",
                image_paths=image_paths,
                naming_prefix="GEN",
            )
        else:
            return RoutingDecision(
                method=GenerationMethod.TRIPOSR,
                reason="이미지 1장 + 속도 우선 → TripoSR",
                image_paths=image_paths,
                naming_prefix="GEN",
            )

    if num_images >= 2:
        return RoutingDecision(
            method=GenerationMethod.TRELLIS_MULTI,
            reason=f"이미지 {num_images}장 → TRELLIS 멀티뷰",
            image_paths=image_paths,
            naming_prefix="GEN",
        )

    # Fallback
    return RoutingDecision(
        method=GenerationMethod.TRIPOSR,
        reason="기본 경로 → TripoSR",
        image_paths=image_paths or [],
        naming_prefix="GEN",
    )


def _extract_params(description: str, template: str) -> dict:
    """Extract dimension/parameter hints from description text."""
    import re

    params = {}

    # Try to extract dimensions (e.g., "600x400x400", "0.6m x 0.4m")
    dim_match = re.search(r'(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)', description)
    if dim_match:
        w, h, d = float(dim_match.group(1)), float(dim_match.group(2)), float(dim_match.group(3))
        # If values > 10, assume mm → convert to meters
        if w > 10:
            w, h, d = w / 1000, h / 1000, d / 1000
        params["width"] = w
        params["height"] = h
        params["depth"] = d

    # EUR pallet type
    if template == "pallet_eur":
        if "eur2" in description or "1200x1000" in description:
            params["type"] = "EUR2"
        elif "eur3" in description or "1000x1200" in description:
            params["type"] = "EUR3"
        else:
            params["type"] = "EUR1"

    # Drum lid (개봉/유개 표현 매핑)
    if template == "drum_200l":
        # Default to having a lid — most industrial drums are closed-top.
        # Set to False only when description explicitly indicates open-top.
        if any(kw in description for kw in ("open", "오픈", "개방", "개봉", "뚜껑없", "lidless")):
            params["has_lid"] = False
        else:
            params["has_lid"] = True

    # Shelf levels
    level_match = re.search(r'(\d+)\s*(?:단|층|level|tier)', description)
    if level_match:
        params["levels"] = int(level_match.group(1))

    # Color
    color_match = re.search(r'#([0-9a-fA-F]{6})', description)
    if color_match:
        params["color"] = f"#{color_match.group(1)}"

    return params
