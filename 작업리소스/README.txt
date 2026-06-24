작업리소스 폴더 — 소스 코드 (개발/수정용)
============================================

이 폴더의 파일들은 코드 수정·확장·디버깅 용도입니다.
실제 실행은 ../실행/CleanMeshStudio.exe 가 담당합니다.


폴더 구조
------------------------------------
cleanmesh/                메인 Python 패키지
  router.py               입력에 따라 어느 생성기 쓸지 결정
  pipeline.py             5단계 흐름 (생성→정리→렌더→내보내기)
  segment.py              SAM2 호출 래퍼 (WSL subprocess)
  config.py               경로/설정 dataclass
  report.py               결과 보고서 포맷터
  generators/             3종 생성기
    procedural.py             Blender CLI로 절차적 모델 생성
    triposr.py                TripoSR HTTP 클라이언트
    trellis.py                TRELLIS WSL subprocess
  blender/                Blender headless 스크립트
    cleanup.py                bmesh 메시 정리
    render.py                 6방향 EEVEE 렌더
    export.py                 엔진별 GLB/FBX 변환
    templates/                5종 절차 템플릿
      drum_200l.py            200L 드럼통
      pallet_eur.py           EUR1 표준 팔레트
      box_cargo.py            화물 박스
      shelf_rack.py           산업 선반
      conveyor_roller.py      롤러 컨베이어

server/                   FastAPI 웹 서버
  main.py                 모든 REST 엔드포인트 + 잡 큐
  schemas.py              Pydantic 요청/응답 모델
  static/index.html       SPA: 드래그&드롭 + SAM2 캔버스 + 결과 패널

trellis_wsl/              WSL2 Ubuntu 안에서 실행되는 스크립트
  _run_trellis.py             TRELLIS 추론 러너 (Windows에서 subprocess 호출)
  _sam2_segment.py            SAM2 클릭→마스크 PNG
  _install_apt.sh             apt 의존성
  _install_step1.sh           torch 2.4 + cu121 + venv
  _install_step2.sh           xformers, spconv 등
  _install_kaolin.sh          NVIDIA Kaolin
  _install_sam2_safe.sh       SAM2 안전 설치 (반드시 이것 사용)
  _install_sam2.sh            ⚠️ 구버전 — 실행 시 즉시 차단됨
  _fix_transformers.sh        transformers 4.46.3 다운그레이드
  _sanity.sh                  import 검증

cleanmesh_launcher.py     .exe 원본 소스 (PyInstaller 입력)
requirements.txt          Windows Python 패키지 의존성


설치 (새 PC에서)
------------------------------------
1. ../CleanMesh_기술문서.docx > "4. 설치 가이드" 섹션 참조

2. 요약:
   Windows측:
     pip install -r requirements.txt
   WSL2 측 (순서대로):
     bash /mnt/d/trellis/_install_apt.sh
     bash /mnt/d/trellis/_install_step1.sh
     bash /mnt/d/trellis/_install_step2.sh
     bash /mnt/d/trellis/_install_kaolin.sh
     bash /mnt/d/trellis/_fix_transformers.sh
     bash /mnt/d/trellis/_sanity.sh
     bash /mnt/d/trellis/_install_sam2_safe.sh

   ⚠️  주의: _install_sam2.sh (구버전) 는 torch 2.4 → 2.12 강제 업그레이드로
       TRELLIS 의존성을 모두 깨뜨립니다. 반드시 _install_sam2_safe.sh 사용.


소스 수정 후 반영
------------------------------------
1. cleanmesh_launcher.py 수정 → ../실행/build_launcher.bat 더블클릭
2. 다른 .py 파일은 서버 재시작만 하면 됨
   (../실행/CleanMesh_Stop.bat → CleanMeshStudio.exe 재실행)
