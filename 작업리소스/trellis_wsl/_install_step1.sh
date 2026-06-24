#!/usr/bin/env bash
# Step 1: venv + PyTorch 2.4.0 + cu121
set -euo pipefail
cd "$HOME"

if [ ! -f trellis-venv/bin/activate ]; then
    echo "[step1] (re)creating venv at ~/trellis-venv"
    rm -rf trellis-venv
    python3 -m venv trellis-venv
fi

source trellis-venv/bin/activate
echo "[step1] python: $(python -V)"
echo "[step1] which: $(which python)"

pip install -U pip wheel setuptools 2>&1 | tail -3
echo "[step1] installing PyTorch 2.4.0 + cu121 (~2GB download)..."
pip install torch==2.4.0 torchvision==0.19.0 --index-url https://download.pytorch.org/whl/cu121 2>&1 | tail -10

echo "[step1] verifying torch + CUDA..."
python - <<'PY'
import torch
print(f"torch: {torch.__version__}")
print(f"CUDA available: {torch.cuda.is_available()}")
print(f"CUDA version: {torch.version.cuda}")
if torch.cuda.is_available():
    print(f"device: {torch.cuda.get_device_name(0)}")
PY
echo "[step1] DONE"
