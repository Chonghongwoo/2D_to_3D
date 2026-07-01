# CleanMesh Installer

Auto-detect + auto-install for taking a fresh PC from zero to a working
CleanMesh Studio setup.

## Two ways to distribute

| Mode | Contents | Setup time | Requires internet on target |
|---|---|---|---|
| **Fetch mode** (~10 MB) | `CleanMesh_Installer.exe` only | 40–60 min | Yes (Blender, model weights, TripoSR git clone) |
| **Bundle mode** (~30 GB) | `CleanMesh_Installer.exe` + `bundle/` folder from `실행\migrate_pack.bat` mode B/C | 15–25 min | No (unless Blender missing) |

## Build the installer .exe

On the source (dev) PC:

```
cd D:\GoogleDrive\Image_to_3D\installer
build_installer.bat
```

Output: `CleanMesh_Installer.exe` in the same folder. Copy this file
(or bundle it with a `bundle/` sibling folder produced by
`실행\migrate_pack.bat`) and distribute.

## Run on target PC

```
# Right-click → Run as administrator (needed on first run for WSL)
CleanMesh_Installer.exe
```

Progress is printed live and also logged to `bootstrap.log` next to the
.exe. If any stage fails, the log has the reason.

### CLI options

```
CleanMesh_Installer.exe --bundle .\bundle              # bundle mode
CleanMesh_Installer.exe --install-dir D:\CleanMesh     # custom install path
CleanMesh_Installer.exe --stage precheck               # only run 1 stage
CleanMesh_Installer.exe --skip weights --yes           # skip a stage, no prompts
```

## Stage flow

```
1. precheck   →  NVIDIA / Python / Blender / WSL / disk / admin
2. blender    →  winget install BlenderFoundation.Blender  (auto)
3. project    →  copy bundle OR  git clone https://github.com/Chonghongwoo/2D_to_3D.git
4. triposr    →  copy bundle OR  git clone + venv + pip install
5. wsl        →  wsl --import Ubuntu-22.04.tar  OR  wsl --install
6. weights    →  bundle: already in tar    fetch: run trellis_wsl/_install_step*.sh
7. launcher   →  patch config.py Blender path + pip pyinstaller + build .exe
8. smoke      →  spawn both uvicorns + probe /api/health + /docs
```

## Idempotency

The installer detects the current state of each stage and only executes
what's not done yet. You can:

* Re-run after a partial success without side effects
* Run `--stage precheck` any time to diagnose current state
* Run `--stage smoke` after a manual fix to re-verify

## Target-PC minimums

| | Minimum | Recommended |
|---|---|---|
| GPU | NVIDIA 8 GB (RTX 3060) | RTX 3070+ / 12 GB+ |
| RAM | 16 GB | 32 GB |
| Disk C:\ | 30 GB free | 50 GB SSD |
| OS | Windows 10 21H2 | Windows 11 |
| Python | 3.11 | 3.11 |
| WSL2 | required | required |
| Blender | 4.1+ | 5.1 |

## When admin is needed

Only stage 5 (WSL install) requires admin — it enables the Windows
"Virtual Machine Platform" feature. On subsequent runs (or if WSL is
already installed) the installer runs fine as a normal user.

The installer detects `IsUserAnAdmin()` and logs it; if you skipped
admin and hit stage 5, re-launch as admin and re-run — earlier stages
skip on re-run.

## Troubleshooting

* **Blender install fails** → download the .msi from blender.org
  manually, install to the default path, re-run installer.
* **WSL import fails with "requires Hyper-V"** → run
  `dism.exe /online /enable-feature /featurename:Microsoft-Hyper-V-All /all /norestart`
  in admin PowerShell, reboot.
* **Weights download stalls** → the huggingface repos are large;
  restart the installer with `--stage weights` to resume.
* **Smoke test fails** → check `C:\t3d\logs\cleanmesh.err` and
  `C:\t3d\logs\triposr.err` for backend errors.
