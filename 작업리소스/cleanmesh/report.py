"""
CleanMesh Report — Generates human-readable summaries of pipeline results.
"""

from typing import Optional


def generate_report(result: dict) -> str:
    """
    Generate a formatted report from pipeline result.

    Args:
        result: Complete pipeline result dict

    Returns:
        Formatted report string
    """
    lines = []
    lines.append("=" * 60)
    lines.append("  CleanMesh 파이프라인 결과 보고서")
    lines.append("=" * 60)

    # Routing
    routing = result.get("stages", {}).get("routing", {})
    if routing:
        lines.append(f"\n🔀 경로: {routing.get('method', 'N/A')}")
        lines.append(f"   사유: {routing.get('reason', 'N/A')}")

    # Generation
    gen = result.get("stages", {}).get("generation", {})
    if gen:
        lines.append(f"\n🏭 생성: {gen.get('status', 'N/A')}")
        if gen.get("method"):
            lines.append(f"   엔진: {gen['method']}")
        if gen.get("file_size_kb"):
            lines.append(f"   Raw 크기: {gen['file_size_kb']} KB")

    # Cleanup
    cleanup = result.get("stages", {}).get("cleanup", {})
    if cleanup:
        lines.append(f"\n🧹 청소: {cleanup.get('status', 'N/A')}")
        if cleanup.get("vertices"):
            lines.append(f"   정점: {cleanup['vertices']:,}")
        if cleanup.get("faces"):
            lines.append(f"   면: {cleanup['faces']:,}")
        if cleanup.get("tris"):
            lines.append(f"   삼각형: {cleanup['tris']:,}")
        if cleanup.get("has_uv") is not None:
            lines.append(f"   UV: {'✅' if cleanup['has_uv'] else '❌'}")
        if cleanup.get("materials"):
            mat_count = len(cleanup["materials"]) if isinstance(cleanup["materials"], list) else cleanup["materials"]
            lines.append(f"   머티리얼: {mat_count}")

    # Render
    render = result.get("stages", {}).get("render", {})
    if render:
        lines.append(f"\n🖼️ 렌더: {render.get('status', 'N/A')}")
        if render.get("renders"):
            lines.append(f"   뷰: {len(render['renders'])}개")

    # Export
    export = result.get("stages", {}).get("export", {})
    if export:
        lines.append(f"\n📦 익스포트: {export.get('status', 'N/A')}")
        if export.get("file_size_mb"):
            lines.append(f"   크기: {export['file_size_mb']} MB")

    # Final output
    lines.append(f"\n{'─' * 60}")
    lines.append(f"상태: {'✅ 성공' if result.get('status') == 'success' else '⚠️ ' + result.get('status', 'unknown')}")
    if result.get("output_path"):
        lines.append(f"출력: {result['output_path']}")
    if result.get("error"):
        lines.append(f"오류: {result['error']}")

    # Suggestions
    lines.append(f"\n💡 다음 단계:")
    if result.get("status") == "success":
        lines.append("   - GLB 파일을 게임 엔진에 임포트")
        lines.append("   - 렌더 이미지에서 품질 확인")
        lines.append("   - 필요시 target_polys 조정으로 LOD 생성")
    elif result.get("status") == "partial":
        lines.append("   - 중간 파일을 Blender에서 수동 검수")
        lines.append("   - 청소/익스포트 단계 재시도")
    else:
        lines.append("   - 에러 로그 확인")
        lines.append("   - 입력 이미지 품질/형식 점검")

    lines.append("=" * 60)

    return "\n".join(lines)
