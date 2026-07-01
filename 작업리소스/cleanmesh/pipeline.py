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
from .subproc import run as _hidden_run
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
        cancel_check=None,
        disable_triposr_fallback: bool = False,
        progress_sink=None,
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
        gen_result = self._generate_persistent(
            decision, timestamp,
            pre_masked=pre_masked,
            cancel_check=cancel_check,
            disable_triposr_fallback=disable_triposr_fallback,
            progress_sink=progress_sink,
        )
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

    # ────────────────────────────────────────────────────────────────
    # Persistent outer retry — wraps the entire (TRELLIS+TripoSR) flow
    # in an escalating loop. Each round freshens VRAM state and kills
    # an ever-wider set of Windows GPU consumers, then re-runs.
    #
    # The escalation ladder defines the FIRST N rounds. After it's
    # exhausted, every subsequent round reuses the most-aggressive
    # setting (kill_browsers + Blender) and just waits LONGER between
    # attempts — relying on external state changes (user closing apps,
    # background jobs ending) to eventually free the contiguous VRAM
    # block TRELLIS needs.
    #
    # The loop runs until success OR cancel_check() returns True.
    # ────────────────────────────────────────────────────────────────
    _ESCALATION_LADDER = [
        # (label, also_browsers, also_blender, wait_before_s)
        # NOTE: also_browsers is INTENTIONALLY False on every tier.
        # The CleanMesh UI runs in the user's Chrome/Edge/etc — auto-killing
        # browsers closes the user's own page mid-job. Browsers are
        # foreground apps; user must close them manually if they want that
        # extra VRAM headroom. We DO kill msedgewebview2 (Electron webview
        # engine used by Discord/Slack/Teams) since that's a background hog.
        ("standard",         False, False,  0),
        ("longer_wait",      False, False, 20),
        ("with_blender",     False,  True, 30),
    ]
    # Waits for round N+ (after ladder exhausted) — sec. Last value
    # repeats forever (capped). Keeps going indefinitely so external state
    # changes (user closing apps, system memory freeing) can break the
    # deadlock without killing the user's UI.
    _POST_LADDER_WAITS = [60, 90, 120]

    # Hard cap on total rounds — even in "unbounded" mode, keep going
    # forever is pointless if nothing changes. 15 rounds ≈ 15-20 min
    # of wait + attempts, enough time for a user to notice and close
    # apps externally. Beyond that we give up with a clear message.
    _MAX_PERSISTENT_ROUNDS = 15

    # "No progress" detector: if this many consecutive rounds show the
    # same OOM signature (same tier failing at the same VRAM level),
    # additional rounds cannot help without external state change.
    _NO_PROGRESS_STREAK = 5

    def _generate_persistent(
        self, decision: RoutingDecision, timestamp: str,
        pre_masked: bool = False,
        cancel_check=None,
        disable_triposr_fallback: bool = False,
        progress_sink=None,
    ) -> dict:
        """Run _generate in a persistent retry loop.

        Rounds 1..len(_ESCALATION_LADDER): walk the escalation ladder.
        Round N+: reuse most-aggressive setting; wait grows toward 120 s.
        Loops until:
          - a round succeeds, OR
          - the same OOM signature repeats _NO_PROGRESS_STREAK times
            (further retries won't help without external change), OR
          - _MAX_PERSISTENT_ROUNDS is hit, OR
          - `cancel_check` returns True.

        cancel_check: optional callable[[], bool]. Called between rounds.
            Return True to break the loop with a cancelled status.

        progress_sink: optional callable(dict) called after each round
            with {'round': N, 'label': str, 'free_mb': int, 'error_tail': str}
            so the server can expose live progress on /api/status.
        """
        import time as _time
        from .vram_guard import kill_known_gpu_hogs, wsl_shutdown, get_vram_summary

        # Procedural / TripoSR-direct don't OOM the same way — run once.
        if decision.method == GenerationMethod.PROCEDURAL:
            return self._generate(decision, timestamp, pre_masked=pre_masked)

        persistent_log = []
        last_result = None
        round_idx = 0
        # No-progress detector state: track last N (signature) tuples
        recent_signatures: list[tuple] = []

        while True:
            # Hard cap — even unbounded mode gives up eventually
            if round_idx >= self._MAX_PERSISTENT_ROUNDS:
                logger.warning(
                    f"🛑 Persistent retry: {self._MAX_PERSISTENT_ROUNDS} 라운드 상한 도달 — "
                    "자동 포기 (사용자 환경 정리 후 재시도 필요)"
                )
                if last_result is None:
                    last_result = {"status": "error",
                                   "message": "max retry rounds reached"}
                last_result["persistent_rounds_used"] = round_idx
                last_result["persistent_log"] = persistent_log
                last_result["gave_up_reason"] = "max_rounds"
                last_result["hint"] = (
                    f"자동 회복 {self._MAX_PERSISTENT_ROUNDS} 라운드 시도했으나 성공 못함. "
                    "Chrome 탭 정리 · Discord/Teams 종료 · 시스템 재부팅 후 재시도, "
                    "또는 이미지 1장으로 축소해서 재시도."
                )
                return last_result
            # Determine settings for this round
            if round_idx < len(self._ESCALATION_LADDER):
                label, browsers, blender, wait_s = self._ESCALATION_LADDER[round_idx]
            else:
                # Post-ladder rounds (4+): reuse Blender-kill setting from the
                # final ladder tier and just wait longer for external state to
                # change. Browsers are STILL never auto-killed — the CleanMesh
                # UI runs inside the user's Chrome/Edge, and closing it mid-job
                # kills the user's own control surface. If they want to donate
                # that VRAM they close the browser themselves.
                label = "post_ladder_wait"
                browsers = False        # ← never auto-kill browsers
                blender  = True         # keep the Blender kill from tier 3
                w_idx = min(round_idx - len(self._ESCALATION_LADDER),
                            len(self._POST_LADDER_WAITS) - 1)
                wait_s = self._POST_LADDER_WAITS[w_idx]

            # Cancellation check between rounds
            if cancel_check is not None and cancel_check():
                logger.warning(f"🛑 사용자 취소 요청 (라운드 {round_idx+1} 진입 전)")
                if last_result is None:
                    last_result = {"status": "error", "message": "cancelled by user"}
                last_result["cancelled"] = True
                last_result["persistent_rounds_used"] = round_idx
                last_result["persistent_log"] = persistent_log
                return last_result

            # Escalation between rounds (skip on first round — that's just _generate)
            if round_idx > 0:
                logger.warning(
                    f"🔁 Persistent retry 라운드 {round_idx+1}: escalation={label}"
                )
                if browsers or blender:
                    kr = kill_known_gpu_hogs(
                        also_browsers=browsers, also_blender=blender,
                    )
                    persistent_log.append({
                        "round": round_idx + 1, "label": label, **kr,
                    })
                    logger.info(f"   killed: {kr.get('killed', [])}")
                wsl_shutdown()
                if wait_s > 0:
                    logger.info(f"   ⏱️ {wait_s}s 대기 (드라이버 안정화 + 외부 상태 변화 대기)…")
                    # Interruptible sleep: poll cancel every second
                    for _ in range(wait_s):
                        if cancel_check is not None and cancel_check():
                            logger.warning("🛑 사용자 취소 요청 (대기 중)")
                            if last_result is None:
                                last_result = {"status": "error", "message": "cancelled by user"}
                            last_result["cancelled"] = True
                            last_result["persistent_rounds_used"] = round_idx
                            last_result["persistent_log"] = persistent_log
                            return last_result
                        _time.sleep(1)
                vr = get_vram_summary()
                logger.info(
                    f"   free VRAM: {vr.get('free_mb','?')} / {vr.get('total_mb','?')} MB"
                )

            last_result = self._generate(decision, timestamp, pre_masked=pre_masked,
                                          disable_triposr_fallback=disable_triposr_fallback)
            if _is_ok(last_result):
                if round_idx > 0:
                    last_result["persistent_round"] = round_idx + 1
                    last_result["persistent_log"] = persistent_log
                    logger.info(f"✅ Persistent retry 라운드 {round_idx+1} 성공")
                return last_result

            # Not OK — log why and update no-progress detector
            err_msg = (last_result or {}).get("message", "")[:200]
            logger.warning(f"❌ 라운드 {round_idx+1} 실패: {err_msg}")

            # Signature = (message_prefix, current free VRAM bucketed to 100 MB).
            # If same signature repeats N times, we're not making progress.
            _msg_prefix = err_msg.split(":")[0][:80] if err_msg else ""
            _vr = get_vram_summary().get("free_mb", 0) or 0
            _sig = (_msg_prefix, int(_vr) // 100)
            recent_signatures.append(_sig)
            recent_signatures = recent_signatures[-self._NO_PROGRESS_STREAK:]

            # Push progress to UI (server subscribes via progress_sink)
            if progress_sink is not None:
                try:
                    progress_sink({
                        "round": round_idx + 1,
                        "max_rounds": self._MAX_PERSISTENT_ROUNDS,
                        "free_mb": _vr,
                        "last_error_tail": err_msg[-160:],
                        "no_progress_streak": len(
                            [s for s in recent_signatures if s == _sig]),
                    })
                except Exception:
                    pass

            # No-progress detector: N identical failures = give up now
            if (len(recent_signatures) >= self._NO_PROGRESS_STREAK
                    and all(s == recent_signatures[-1] for s in recent_signatures)):
                logger.warning(
                    f"🛑 진전 없음: 최근 {self._NO_PROGRESS_STREAK} 라운드 동일 실패 "
                    f"({_sig[0]!r} @ ~{_sig[1]*100} MB) — 자동 포기"
                )
                last_result["persistent_rounds_used"] = round_idx + 1
                last_result["persistent_log"] = persistent_log
                last_result["gave_up_reason"] = "no_progress"
                last_result["hint"] = (
                    f"동일 실패 {self._NO_PROGRESS_STREAK}회 반복. 외부 상태 변화 없이 "
                    "재시도해도 결과 같음. 무거운 앱 종료 · 이미지 수/해상도 축소 · "
                    "시스템 재부팅 중 하나 후 재시도."
                )
                return last_result

            round_idx += 1

    def _generate(self, decision: RoutingDecision, timestamp: str,
                  pre_masked: bool = False,
                  disable_triposr_fallback: bool = False) -> dict:
        """Execute the generation step based on routing decision.

        ``disable_triposr_fallback``: when True, TRELLIS exhaustion returns
        the original TRELLIS error AS-IS instead of silently routing to
        TripoSR. Use this when output quality > guaranteed result.
        """
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
            #
            # Quality-critical use cases (industrial DT) can disable this
            # via the disable_triposr_fallback flag — they get a clean
            # TRELLIS error instead of a silently degraded 1MB stub.
            if disable_triposr_fallback and self._trellis_exhausted_oom(result):
                logger.warning(
                    "⚠️ TRELLIS 모든 회복 단계 실패 — TripoSR fallback 비활성화됨 → 깨끗한 실패 리턴"
                )
                result["triposr_fallback_disabled"] = True
                result["hint"] = (
                    "8GB GPU + TRELLIS-image-large 한계. "
                    "수동 조치: 시스템 재부팅 → 즉시 단일 이미지로 재시도. "
                    "또는 무거운 앱(Chrome/Discord/Teams/OBS/Blender) 종료 후 재시도. "
                    "TripoSR 자동 전환을 원하면 UI에서 '저품질 fallback 사용' 체크."
                )
                return result

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
                # Surface TripoSR's stderr so root cause is visible in UI
                tp_stderr = triposr_result.get("stderr") or ""
                if tp_stderr:
                    result["triposr_stderr_tail"] = tp_stderr[-800:]

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
            result = _hidden_run(
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
