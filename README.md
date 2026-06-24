# 2D → 3D for Digital Twin

> 현장 사진 한 장으로 디지털 트윈 플랫폼(Omniverse · Twinmotion · BIM 등)에
> 즉시 배치 가능한 정확도-우선 3D 자산을 만드는 자동화 파이프라인.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.11-blue.svg)
![Platform](https://img.shields.io/badge/Platform-Windows%2011%20%2B%20WSL2-lightgrey.svg)

## 무엇을 하는 시스템인가

AGV · 팔레트 · 드럼 · 컨베이어 · 선반 같은 현장 설비의 사진을 입력하면,
**SAM2 클릭 세그멘트** + **TRELLIS 또는 TripoSR** + **Blender 헤드리스 정리** 를
거쳐 ~60초 안에 디지털 트윈에 배치 가능한 GLB / USD 자산을 생성합니다.

- ✅ 정점 색상으로 실제 표면 외관 보존 (산화·녹·라벨·페인트 자국)
- ✅ 메타데이터 자동 임베드 (카테고리·실치수·제조사·시리얼)
- ✅ Watertight · manifold 메시 → 물리 시뮬레이션 가능
- ✅ USD 익스포트로 Omniverse / Twinmotion 즉시 import
- ✅ 동일 사진 → 동일 자산 (deterministic 모드, 16+ GB GPU)

## 빠른 시작

### 1) 사전 설치 (한 번만)

| 컴포넌트 | 버전 | 출처 |
|---|---|---|
| Windows | 10 / 11 | — |
| NVIDIA Driver + CUDA | 12.1 권장 | nvidia.com |
| Python | 3.11.x | python.org |
| WSL2 + Ubuntu | 22.04 LTS | `wsl --install Ubuntu-22.04` |
| Blender | 5.1.2 | blender.org |
| TripoSR backend | main | [VAST-AI-Research/TripoSR](https://github.com/VAST-AI-Research/TripoSR) |
| TRELLIS (WSL2) | upstream | [microsoft/TRELLIS](https://github.com/microsoft/TRELLIS) |
| SAM2 (WSL2) | 2.1 hiera_large | [facebookresearch/sam2](https://github.com/facebookresearch/sam2) |

### 2) 클론 + 설치

```powershell
git clone https://github.com/<your-username>/2D_to_3D.git D:\Image_to_3D
cd D:\Image_to_3D\작업리소스
python -m pip install -r requirements.txt

# WSL 측 (TRELLIS + SAM2 모델 weights 자동 다운로드)
wsl -d Ubuntu-22.04 -- bash /mnt/d/Image_to_3D/trellis_wsl/_install_apt.sh
wsl -d Ubuntu-22.04 -- bash /mnt/d/Image_to_3D/trellis_wsl/_install_step1.sh
wsl -d Ubuntu-22.04 -- bash /mnt/d/Image_to_3D/trellis_wsl/_install_step2.sh
wsl -d Ubuntu-22.04 -- bash /mnt/d/Image_to_3D/trellis_wsl/_install_kaolin.sh
wsl -d Ubuntu-22.04 -- bash /mnt/d/Image_to_3D/trellis_wsl/_fix_transformers.sh
wsl -d Ubuntu-22.04 -- bash /mnt/d/Image_to_3D/trellis_wsl/_install_sam2_safe.sh

# .exe 빌드 (Windows측)
cd ..\실행
.\build_launcher.bat
```

### 3) 실행

```
실행\CleanMeshStudio.exe 더블클릭
  → 브라우저 자동 오픈 (http://localhost:8100)
  → 이미지 드래그&드롭 → SAM2 클릭 → Generate
```

## 폴더 구성

```
2D_to_3D/
├── CleanMesh_기술문서.docx      ← 상세 기술 매뉴얼 (49 섹션)
├── CleanMesh_매뉴얼.pptx        ← 프로그램 소개 (20 슬라이드)
├── build_docs.py                ← 두 문서 재생성 스크립트
├── README.md                    ← 본 파일
├── LICENSE                      ← MIT
│
├── 실행/                         ← 바로 실행 가능
│   ├── CleanMeshStudio.exe          ← PyInstaller 단일 실행파일
│   ├── CleanMesh_Start.bat / Stop.bat
│   ├── build_launcher.bat
│   └── migrate_pack.bat             ← 다른 PC 이전용
│
└── 작업리소스/                    ← 소스 코드
    ├── cleanmesh_launcher.py        ← .exe 원본 소스
    ├── requirements.txt
    ├── cleanmesh/                    ← 파이프라인 패키지
    │   ├── router.py · pipeline.py
    │   ├── segment.py                ← SAM2 호출 래퍼
    │   ├── generators/               ← procedural · triposr · trellis
    │   └── blender/                  ← cleanup · render · export · color_*
    ├── server/                        ← FastAPI 웹 서버
    │   ├── main.py
    │   └── static/index.html         ← Vanilla JS SPA + SAM2 캔버스
    └── trellis_wsl/                   ← WSL2 측 스크립트
        ├── _run_trellis.py
        ├── _sam2_segment.py
        └── _install_*.sh
```

## 기능 요약

| 기능 | 설명 |
|---|---|
| **4가지 생성 경로** | Procedural / TripoSR / TRELLIS Single / TRELLIS Multi |
| **SAM2 클릭 세그멘트** | 마우스 클릭으로 배경 제거 → ghost geometry 0 |
| **색상 모드 2종** | 정점 색상 (실표면 보존) · 영역별 분해 (K-means) |
| **잡티 제거 4단계** | Laplacian smoothing 강/중/약/끄기 |
| **6 stage 파이프라인** | 라우팅 → 생성 → 청소 → 색상 처리 → 렌더 → 익스포트 |
| **익스포트 포맷** | GLB · USD · FBX 멀티 출력 |
| **DT 메타데이터** | 카테고리 · 실치수 · 제조사 · 시리얼 자동 임베드 |
| **Blender MCP 연동** | 원클릭 자동 import |
| **결정성 모드** | 같은 사진 → 같은 모델 (16GB+ GPU) |

## 처리 시간 벤치마크 (RTX 3070 8GB · 깨끗한 GPU)

| 경로 | 입력 | 시간 |
|---|---|---:|
| Procedural | 텍스트 | 1~3초 |
| TripoSR | 이미지 1장 | 3~6초 |
| **TRELLIS Single + SAM2** | **이미지 1장** | **60~70초** (권장) |
| TRELLIS Multi | 이미지 2~3장 | 1.5~2분 |

## ⚠ 8GB GPU 환경 운영 수칙

TRELLIS는 mesh extraction 단계에서 1~2 GB **연속 VRAM 블록**이 필요합니다.
8GB GPU에서 잡 시작 전에 다음 앱들을 닫아야 OOM이 안 납니다:

- Blender (잡 끝나고 켜기)
- OBS Studio · Microsoft Teams · Discord
- NVIDIA GeForce Experience / Share
- Razer Synapse
- 불필요한 Chrome 탭 (CleanMesh Studio 탭만 남김)

자세한 GPU 메모리 운영 수칙은 [기술문서 8.3절](CleanMesh_기술문서.docx) 참조.

## 디지털 트윈 표준 워크플로우

```
1. 현장 설비 사진 1장 촬영
2. 웹 UI에 드래그&드롭
3. 🎯 SAM2 클릭 → 마스크 생성
4. 색상 모드: 정점 색상 / 잡티 제거: 약
5. 메타데이터 입력 (카테고리·실치수·제조사·시리얼)
6. 익스포트 포맷: USD (Omniverse) 또는 GLB
7. ▶ Generate → 약 70초
8. Blender 열기 → 🎨 Blender로 보내기
9. Omniverse / Twinmotion / BIM 협업툴에서 import
```

## 라이센스

이 프로젝트 자체는 [MIT](LICENSE).

업스트림 컴포넌트는 각자 라이센스:
- TripoSR — MIT
- TRELLIS — MIT
- SAM 2.1 — Apache 2.0
- Blender — GPL v2/v3 (외부 도구로만 사용)
- Blender MCP addon — MIT

## 기여

이슈 / PR 환영합니다. 큰 변경 전엔 issue로 먼저 논의 부탁드립니다.

## 문서 재생성

```powershell
python build_docs.py
# → CleanMesh_기술문서.docx + CleanMesh_매뉴얼.pptx 갱신
```
