#!/usr/bin/env bash
# Safe SAM2 install: uses --no-deps to avoid replacing our pinned torch 2.4.
# Only adds the pure-Python deps SAM2 actually needs.
#
# Run as: bash /mnt/d/trellis/_install_sam2_safe.sh

set -e

echo "=== Activating trellis-venv ==="
source $HOME/trellis-venv/bin/activate
python -c "import torch; print(f'torch={torch.__version__} cuda={torch.cuda.is_available()}')"

# Clean up wasted torch 2.12 download from previous attempt
echo
echo "=== Purging pip cache of torch 2.12 / cu13 wheels (saves ~2 GB) ==="
pip cache remove 'torch-2.12*' 2>/dev/null || true
pip cache remove 'nvidia_*cu13*' 2>/dev/null || true
pip cache remove 'triton-3.7*' 2>/dev/null || true
pip cache remove 'torchvision-0.27*' 2>/dev/null || true
pip cache remove 'cuda_*' 2>/dev/null || true

echo
echo "=== Cloning SAM2 (if needed) ==="
cd $HOME
if [ ! -d "sam2" ]; then
    git clone https://github.com/facebookresearch/sam2.git
fi
cd sam2

echo
echo "=== SAM2 install with --no-deps (keeps torch 2.4 intact) ==="
pip install -e . --no-deps --no-build-isolation

echo
echo "=== Installing only the pure-Python deps SAM2 actually needs ==="
# hydra-core: config loading
# iopath: facebook path util
# Pillow/numpy/tqdm/PyYAML/filelock already in trellis-venv from TRELLIS install
pip install --no-deps "hydra-core>=1.3.2" "omegaconf>=2.2,<2.4" "antlr4-python3-runtime==4.9.*" "iopath>=0.1.10"

echo
echo "=== Sanity: torch and TRELLIS deps still intact ==="
python /mnt/d/trellis/_audit_deps.py

echo
echo "=== Downloading SAM 2.1 hiera_large checkpoint (856 MB) ==="
mkdir -p $HOME/sam2/checkpoints
cd $HOME/sam2/checkpoints
if [ ! -f "sam2.1_hiera_large.pt" ]; then
    wget -c -q --show-progress \
        https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_large.pt
fi
ls -lh sam2.1_hiera_large.pt

echo
echo "=== Build SAM2 model (CUDA init test) ==="
cd $HOME/sam2
python - <<'PY'
import os, torch
from sam2.build_sam import build_sam2
from sam2.sam2_image_predictor import SAM2ImagePredictor

ckpt = os.path.expanduser("~/sam2/checkpoints/sam2.1_hiera_large.pt")
cfg  = "configs/sam2.1/sam2.1_hiera_l.yaml"
model = build_sam2(cfg, ckpt, device="cuda")
print("OK  param count =", round(sum(p.numel() for p in model.parameters())/1e6, 1), "M")
print("OK  vram used  =", round(torch.cuda.memory_allocated()/1e9, 2), "GB")
PY

echo
echo "=== DONE ==="
