"""
CleanMesh Pipeline — Orchestrates the full generation→cleanup→verify→export flow.

Usage:
    from cleanmesh.pipeline import CleanMeshPipeline

    pipeline = CleanMeshPipeline()
    result = pipeline.run(image_path="input.png")
    # or
    result = pipeline.run(description="200L 드럼통", quality="speed")
"""

import json
import subprocess
import logging
from pathlib import Path
from typing import Optional, List
from datetime import datetime

from .config import get_config, Config
from .router import route, GenerationMethod, RoutingDecision
from .generators import procedural as gen_procedural
from .generators import triposr as gen_triposr
from .generators import trellis as gen_trellis
from .report import generate_report

logger = logging.getLogger(__name__)


def _is_ok(stage_result: dict) -> bool:
    """True if a stage result indicates success.

    Generators return {"status": "success"} but blender scripts (cleanup/render/export)
    return {"status": "ok"}. Accept both.
    """
    return stage_result.get("status") in ("success", "ok")


class CleanMeshPipeline:
    """
    Main pipeline orchestrator for CleanMesh agent.

    Flow: Input → Route → Generate (raw) → Cleanup → Verify → Export
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or get_config()
        self.max_retries = 2

    def run(
        self,
        image_path: Optional[str] = None,
        image_paths: Optional[List[str]] = None,
        description: Optional[str] = None,
        quality: str = "speed",
        force_method: Optional[str] = None,
        object_name: Optional[str] = None,
        target_polys: Optional[int] = None,
        engine_target: Optional[str] = None,
        skip_cleanup: bool = False,
        skip_render: bool = False,
        pre_masked: bool = False,
        color_mode: str = "vertex",
        color_k: int = 4,
        # DT-friendly: light smoothing preserves surface detail
        color_smooth_iters: int = 2,
        color_label_smooth_iters: int = 1,
        color_min_region_size: int = 30,
        export_format: str = "glb",
        dt_meta: dict | None = None,
        simready: bool = False,
    ) -> dict:
        """
        Run the full CleanMesh pipeline.

        Args:
            image_path: Single input image path
            image_paths: Multiple input image paths (for multi-view)
            description: Text description of the object
            quality: "speed" or "quality"
            force_method: Override routing decision
            object_name: Custom name for the output
            target_polys: Target polygon count for decimation
            engine_target: Target platform for axis/scale conversion
                (omniverse / twinmotion / bim / unity / godot / unreal)
            skip_cleanup: Skip Blender cleanup step
            skip_render: Skip render verification step
            pre_masked: Inputs already have clean alpha (e.g. from SAM2
                click-segment). TRELLIS will skip its internal rembg.
            color_mode: "vertex" (default — keep per-vertex colors, DT-recommended
                for surface fidelity) or "region_split" (cluster colors into K
                regions, one solid material per region — for LOD / modular reuse).
            color_k: Number of color regions when color_mode='region_split'
                (clamped to 2..8).

        Returns:
            Complete pipeline result dict with all stages
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        engine = engine_target or self.config.export.engine

        # Normalize image paths
        if image_path and not image_paths:
            image_paths = [image_path]

        result = {
            "timestamp": timestamp,
            "stages": {},
            "status": "running",
        }

        # ─── Stage 1: Routing ───
        logger.info("━━━ Stage 1: 라우팅 ━━━")
        decision = route(
            description=description,
            image_paths=image_paths,
            quality=quality,
            force_method=force_method,
        )
        result["stages"]["routing"] = {
            "method": decision.method.value,
            "reason": decision.reason,
        }
        logger.info(f"📍 {decision.reason}")

        # ─── Stage 2: Generation ───
        logger.info("━━━ Stage 2: 생성 ━━━")
        gen_result = self._generate(decision, timestamp, pre_masked=pre_masked)
        result["stages"]["generation"] = gen_result

        if not _is_ok(gen_result):
            result["status"] = "error"
            result["error"] = f"생성 실패: {gen_result.get('message', 'Unknown error')}"
            return result

        raw_path = gen_result["output_path"]
        logger.info(f"✅ Raw mesh: {raw_path}")

        # Build canonical object name (used by cleanup AND color_split stages)
        prefix = decision.naming_prefix
        name = object_name or decision.template_name or f"object_{timestamp}"
        full_name = f"{prefix}_{name}".replace(" ", "_")

        # ─── Stage 3: Cleanup ───
        if not skip_cleanup:
            logger.info("━━━ Stage 3: 청소 ━━━")
            cleaned_path = str(self.config.paths.cleaned_dir / f"{full_name}.glb")
            cleanup_result = self._cleanup(
                raw_path, cleaned_path, full_name, target_polys
            )
            result["stages"]["cleanup"] = cleanup_result

            if not _is_ok(cleanup_result):
                # Retry with aggressive settings
                logger.warning("⚠️ 청소 실패, 강화 모드로 재시도...")
                cleanup_result = self._cleanup(
                    raw_path, cleaned_path, full_name, target_polys, aggressive=True
                )
                result["stages"]["cleanup_retry"] = cleanup_result

            if _is_ok(cleanup_result):
                current_path = cleaned_path
            else:
                logger.warning("⚠️ 청소 건너뜀, raw mesh 사용")
                current_path = raw_path
        else:
            current_path = raw_path

        # ─── Stage 3.5: Color post-process ───
        if color_mode == "region_split":
            logger.info("━━━ Stage 3.5: 색상 영역 분해 ━━━")
            split_path = str(self.config.paths.cleaned_dir / f"{full_name}_split.glb")
            split_result = self._color_split(
                current_path, split_path,
                k=color_k,
                smooth_iters=color_smooth_iters,
                label_smooth_iters=color_label_smooth_iters,
                min_region_size=color_min_region_size,
            )
            result["stages"]["color_split"] = split_result
            if _is_ok(split_result):
                current_path = split_path
                logger.info(
                    f"🎨 색상 분해 완료: K={split_result.get('k')} 머티리얼, "
                    f"파일 {split_result.get('file_size_kb', 0)} KB"
                )
            else:
                logger.warning(f"⚠️ 색상 분해 실패: {split_result.get('message', '?')}")
        elif color_mode == "vertex" and color_smooth_iters > 0:
            # Vertex-color noise reduction: same Laplacian smoothing, no K-means
            logger.info("━━━ Stage 3.5: 정점 색상 평탄화 ━━━")
            smooth_path = str(self.config.paths.cleaned_dir / f"{full_name}_smooth.glb")
            smooth_result = self._color_smooth(
                current_path, smooth_path,
                iters=color_smooth_iters,
            )
            result["stages"]["color_smooth"] = smooth_result
            if _is_ok(smooth_result):
                current_path = smooth_path
                logger.info(
                    f"🎨 정점 색상 평탄화 완료: {smooth_result.get('iters')} iter, "
                    f"파일 {smooth_result.get('file_size_kb', 0)} KB"
                )
            else:
                logger.warning(f"⚠️ 정점 색상 평탄화 실패: {smooth_result.get('message', '?')}")

        # ─── Stage 4: Render Verification ───
        if not skip_render:
            logger.info("━━━ Stage 4: 렌더 검증 ━━━")
            render_dir = str(self.config.paths.renders_dir / f"render_{timestamp}")
            render_result = self._render(current_path, render_dir)
            result["stages"]["render"] = render_result

            if _is_ok(render_result):
                logger.info(f"🖼️ 렌더 완료: {render_dir}")
            else:
                logger.warning("⚠️ 렌더 검증 실패 (계속 진행)")

        # ─── Stage 5: Export ───
        logger.info("━━━ Stage 5: 익스포트 ━━━")
        export_name = object_name or decision.template_name or f"export_{timestamp}"
        prefix = decision.naming_prefix
        full_export_name = f"{prefix}_{export_name}".replace(" ", "_")

        # Caller-specified format takes precedence over config default
        chosen_fmt = (export_format or self.config.export.format).lower()
        if chosen_fmt not in ("glb", "fbx", "usd"):
            chosen_fmt = "glb"
        export_path = str(
            self.config.paths.exports_dir / f"{full_export_name}.{chosen_fmt}"
        )
        export_result = self._export(current_path, export_path, engine,
                                     dt_meta=dt_meta, simready=simready)
        result["stages"]["export"] = export_result

        if _is_ok(export_result):
            result["status"] = "success"
            result["output_path"] = export_path
            logger.info(f"🎉 최종 출력: {export_path}")
        else:
            # If export failed, the cleaned/raw file is still usable
            result["status"] = "partial"
            result["output_path"] = current_path
            logger.warning(f"⚠️ 익스포트 실패, 중간 파일 사용: {current_path}")

        # ─── SimReady validate (optional, never fails the pipeline) ───
        if simready and chosen_fmt == "usd" and _is_ok(export_result):
            try:
                from .simready_validate import validate_simready
                vr = validate_simready(export_path)
                result["stages"]["simready_validate"] = vr
                if vr.get("passed"):
                    logger.info("✅ SimReady 검증 통과")
                elif vr.get("status") == "skipped":
                    logger.info(f"ℹ️ SimReady 검증 스킵: {vr.get('reason', '')}")
                else:
                    logger.warning(f"⚠️ SimReady 검증 이슈: {vr.get('issues', vr)}")
            except Exception as e:
                logger.warning(f"⚠️ SimReady 검증 오류 (무시): {e}")
                result["stages"]["simready_validate"] = {
                    "status": "failed", "error": str(e)
                }

        # ─── Report ───
        report = generate_report(result)
        result["report"] = report
        logger.info(f"\n{report}")

        return result

    def _generate(self, decision: RoutingDecision, timestamp: str, pre_masked: bool = False) -> dict:
        """Execute the generation step based on routing decision."""
        if decision.method == GenerationMethod.PROCEDURAL:
            return gen_procedural.generate(
                template_name=decision.template_name,
                params=decision.template_params,
            )
        elif decision.method == GenerationMethod.TRIPOSR:
            if not decision.image_paths:
                return {
                    "status": "error",
                    "message": "TripoSR requires an input image",
                }
            # TripoSR has its own rembg; if pre_masked, ask it to skip
            kwargs = {}
            if pre_masked:
                kwargs["remove_bg"] = False
            return gen_triposr.generate(
                image_path=decision.image_paths[0],
                **kwargs,
            )
        elif decision.method in (GenerationMethod.TRELLIS_SINGLE, GenerationMethod.TRELLIS_MULTI):
            result = gen_trellis.generate(
                image_paths=decision.image_paths or [],
                pre_masked=pre_masked,
            )

            # TripoSR fallback — if TRELLIS exhausted every recovery tier
            # (max_dim ladder → single-view → kill_hogs), drop to TripoSR
            # which peaks at ~2-3 GB and runs reliably on 8GB GPUs.
            # Quality is lower but the user gets a usable DT asset instead
            # of a failed job.
            if self._trellis_exhausted_oom(result) and decision.image_paths:
                logger.warning(
                    "⚠️ TRELLIS 모든 회복 단계 실패 — TripoSR로 자동 전환"
                )
                triposr_result = gen_triposr.generate(
                    image_path=decision.image_paths[0],
                    remove_bg=not pre_masked,
                )
                if triposr_result.get("status") == "success":
                    triposr_result["fallback_from_trellis"] = True
                    triposr_result["trellis_recovery_log"] = result.get("vram_recovery")
                    logger.info("✅ TripoSR fallback 성공 — DT 자산 생성됨")
                    return triposr_result
                # Both failed — keep original TRELLIS error (more informative)
                result["triposr_fallback_also_failed"] = True
                result["triposr_error"] = triposr_result.get("message")

            return result
        else:
            return {"status": "error", "message": f"Unknown method: {decision.method}"}

    @staticmethod
    def _trellis_exhausted_oom(result: dict) -> bool:
        """Detect 'TRELLIS ran every recovery tier and still failed with OOM'."""
        if not result or result.get("status") != "error":
            return False
        log = result.get("vram_recovery") or []
        # The last entry is fallback_single_view_failed_oom with after_kill_hogs=True
        # when all 5 tiers have been exhausted.
        for entry in reversed(log):
            if (entry.get("phase", "").startswith("fallback_single_view_failed")
                    and entry.get("after_kill_hogs") is True):
                return True
        return False

    def _cleanup(
        self,
        input_path: str,
        output_path: str,
        name: str,
        target_polys: Optional[int] = None,
        aggressive: bool = False,
    ) -> dict:
        """Run Blender cleanup script."""
        config = self.config
        script = config.blender.scripts_dir / "cleanup.py"

        cmd = [
            config.blender.executable,
            "--background",
            "--python", str(script),
            "--",
            "--input", input_path,
            "--output", output_path,
            "--name", name,
        ]

        if target_polys:
            cmd.extend(["--target-polys", str(target_polys)])
        if aggressive:
            cmd.append("--aggressive")

        return self._run_blender(cmd, "cleanup")

    def _color_smooth(self, input_path: str, output_path: str, *,
                      iters: int = 5, alpha: float = 0.7) -> dict:
        """Run Blender color_smooth script: Laplacian-smooth vertex colors in place.
        Used for the 'vertex' color mode to remove TRELLIS KNN-transfer noise
        without quantizing or splitting into regions."""
        config = self.config
        script = config.blender.scripts_dir / "color_smooth.py"
        if not script.is_file():
            return {"status": "error", "message": f"color_smooth.py not found at {script}"}

        cmd = [
            config.blender.executable,
            "--background",
            "--python", str(script),
            "--",
            "--input",  input_path,
            "--output", output_path,
            "--iters",  str(max(0, int(iters))),
            "--alpha",  str(float(alpha)),
        ]
        return self._run_blender(cmd, "color_smooth")

    def _color_split(self, input_path: str, output_path: str, *,
                     k: int = 4,
                     smooth_iters: int = 5,
                     label_smooth_iters: int = 3,
                     min_region_size: int = 100) -> dict:
        """Run Blender color_split script: cluster vertex colors into K solid regions,
        with optional denoising passes (vertex smoothing / label majority / tiny-region merge)."""
        config = self.config
        script = config.blender.scripts_dir / "color_split.py"

        if not script.is_file():
            return {"status": "error", "message": f"color_split.py not found at {script}"}

        cmd = [
            config.blender.executable,
            "--background",
            "--python", str(script),
            "--",
            "--input",  input_path,
            "--output", output_path,
            "--k",      str(max(2, min(int(k), 8))),
            "--smooth-iters",       str(max(0, int(smooth_iters))),
            "--label-smooth-iters", str(max(0, int(label_smooth_iters))),
            "--min-region-size",    str(max(1, int(min_region_size))),
        ]
        return self._run_blender(cmd, "color_split")

    def _render(self, input_path: str, output_dir: str) -> dict:
        """Run Blender render verification script."""
        config = self.config
        script = config.blender.scripts_dir / "render.py"

        cmd = [
            config.blender.executable,
            "--background",
            "--python", str(script),
            "--",
            "--input", input_path,
            "--output-dir", output_dir,
            "--resolution", str(config.render.resolution),
        ]

        return self._run_blender(cmd, "render")

    def _export(self, input_path: str, output_path: str, engine: str,
                dt_meta: dict | None = None,
                simready: bool = False) -> dict:
        """Run Blender export script. dt_meta: optional Digital Twin metadata
        (category, dimensions_mm, manufacturer, serial_number, source_image).
        Embedded into GLB extras / USD customLayerData.

        When simready=True and format=usd, the output is upgraded to a
        SimReady-compliant asset (USDPhysics + assetInfo + semantic label).
        """
        config = self.config
        script = config.blender.scripts_dir / "export.py"

        # Detect format from extension
        ext = output_path.rsplit(".", 1)[-1].lower()
        if ext in ("glb", "gltf"):  fmt = "glb"
        elif ext == "fbx":          fmt = "fbx"
        elif ext in ("usd", "usda", "usdc"):  fmt = "usd"
        else:                       fmt = "glb"

        cmd = [
            config.blender.executable,
            "--background",
            "--python", str(script),
            "--",
            "--input", input_path,
            "--output", output_path,
            "--format", fmt,
            "--engine", engine,
        ]
        # Pass DT metadata as CLI flags (each optional)
        if dt_meta:
            import json as _json
            if dt_meta.get("category"):
                cmd += ["--meta-category", str(dt_meta["category"])]
            if dt_meta.get("dimensions_mm"):
                cmd += ["--meta-dims-mm", _json.dumps(dt_meta["dimensions_mm"])]
            if dt_meta.get("manufacturer"):
                cmd += ["--meta-manufacturer", str(dt_meta["manufacturer"])]
            if dt_meta.get("serial_number"):
                cmd += ["--meta-serial", str(dt_meta["serial_number"])]
            if dt_meta.get("source_image"):
                cmd += ["--meta-source-image", str(dt_meta["source_image"])]

        if simready and fmt == "usd":
            cmd += ["--simready"]

        return self._run_blender(cmd, "export")

    def _run_blender(self, cmd: list, stage_name: str) -> dict:
        """Execute a Blender command and parse the RESULT: JSON output."""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.blender.timeout_seconds,
                encoding="utf-8",
                errors="replace",
            )

            # Parse RESULT: JSON from stdout
            for line in result.stdout.split("\n"):
                line = line.strip()
                if line.startswith("RESULT:"):
                    try:
                        return json.loads(line[7:])
                    except json.JSONDecodeError:
                        pass

            if result.returncode != 0:
                return {
                    "status": "error",
                    "message": f"Blender {stage_name} failed (exit code {result.returncode})",
                    "stderr": result.stderr[-1000:] if result.stderr else "",
                }

            return {
                "status": "warning",
                "message": f"Blender {stage_name} completed but no structured output",
            }

        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "message": f"Blender {stage_name} timed out ({self.config.blender.timeout_seconds}s)",
            }
        except FileNotFoundError:
            return {
                "status": "error",
                "message": f"Blender executable not found: {self.config.blender.executable}",
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Blender {stage_name} error: {str(e)}",
            }
