실행 폴더 — 바로 실행 가능한 파일
=====================================

이 폴더의 파일들은 모두 이미 설치/빌드 완료된 상태입니다.

▶ 일상 사용
  - "CleanMesh Studio.lnk" 더블클릭 → 모든 서버 자동 기동 + 브라우저 오픈
  - "CleanMesh Stop.lnk"   더블클릭 → 서버 종료 (port 8000, 8100 kill)

▶ .lnk 가 작동하지 않으면
  - CleanMeshStudio.exe 더블클릭으로도 동일
  - 또는 CleanMesh_Start.bat 더블클릭

▶ 다른 PC로 이전
  - migrate_pack.bat 더블클릭 → 모드 A/B/C 선택 → 자동 번들 생성

▶ .exe 재빌드 (소스 수정 후)
  - build_launcher.bat 더블클릭 (PyInstaller 자동 호출)


파일별 용도
------------------------------------
CleanMeshStudio.exe       PyInstaller로 묶은 단일 런처 (~7 MB)
CleanMesh Studio.lnk      바탕화면용 단축아이콘 (시작)
CleanMesh Stop.lnk        바탕화면용 단축아이콘 (종료)
CleanMesh_Start.bat       .bat 버전 런처 (.exe 대신 사용 가능)
CleanMesh_Stop.bat        포트 점유 프로세스 강제 종료
build_launcher.bat        소스(cleanmesh_launcher.py) → .exe 재빌드
migrate_pack.bat          마이그레이션 번들 생성 (다른 PC 이전용)


로그 위치
------------------------------------
런처 자체:    CleanMeshStudio.exe 옆 CleanMeshStudio.log
API 서버:    C:\t3d\logs\cleanmesh.log
TripoSR:    C:\t3d\logs\triposr.log
