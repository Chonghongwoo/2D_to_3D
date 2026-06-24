#!/usr/bin/env bash
# Step 2: BASIC + xformers + utils3d + spconv (TRELLIS minimum runnable set)
set -euo pipefail
cd "$HOME"
source trellis-venv/bin/activate

echo "[step2] python: $(python -V)"

echo "[step2] BASIC packages..."
pip install --quiet pillow imageio imageio-ffmpeg tqdm easydict opencv-python-headless scipy ninja rembg onnxruntime trimesh open3d xatlas pyvista pymeshfix igraph transformers 2>&1 | tail -5

echo "[step2] utils3d from git..."
pip install --quiet 'git+https://github.com/EasternJournalist/utils3d.git@9a4eb15e4021b67b12c460c7057d642626897ec8' 2>&1 | tail -3

echo "[step2] xformers 0.0.27.post2 (cu121)..."
pip install --quiet xformers==0.0.27.post2 --index-url https://download.pytorch.org/whl/cu121 2>&1 | tail -3

echo "[step2] spconv-cu120..."
pip install --quiet spconv-cu120 2>&1 | tail -3

echo "[step2] verifying imports..."
python - <<'PY'
import torch, xformers, spconv, utils3d, trimesh, rembg, transformers
print("torch", torch.__version__, "cuda", torch.cuda.is_available())
print("xformers", xformers.__version__)
print("spconv", spconv.__version__ if hasattr(spconv,'__version__') else 'ok')
print("trimesh", trimesh.__version__)
print("transformers", transformers.__version__)
PY

echo "[step2] DONE"
