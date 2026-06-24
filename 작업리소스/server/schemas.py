from pydantic import BaseModel
from typing import Optional, List


class GenerateRequest(BaseModel):
    quality: str = "speed"
    description: Optional[str] = None
    object_name: Optional[str] = None
    target_polys: Optional[int] = None
    engine: Optional[str] = None


class ProceduralRequest(BaseModel):
    template: str
    params: Optional[dict] = None
    engine: Optional[str] = None


class JobStatus(BaseModel):
    id: str
    status: str
    created_at: str
    output_path: Optional[str] = None
    report: Optional[str] = None
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    blender_available: bool
    blender_version: str
    triposr_available: bool
    trellis_available: bool
