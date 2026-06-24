#!/usr/bin/env bash
# ⚠️  DEPRECATED — DO NOT RUN  ⚠️
# This version uses `pip install -e .` WITHOUT --no-deps, which causes pip to
# pull in torch 2.12 + CUDA 13.0 and REPLACE the pinned torch 2.4.0+cu121.
# That breaks xformers / kaolin / spconv / TRELLIS — every wheel was built
# against the torch 2.4 ABI.
#
#   ✅  Use _install_sam2_safe.sh  instead.
#
# This file is kept only as a record of the failed attempt.

echo "ERROR: do not run this script. Use _install_sam2_safe.sh instead."
echo "       (this version replaces torch 2.4 with 2.12 and breaks TRELLIS)"
exit 2

set -e

echo "=== Activating trellis-venv ==="
source $HOME/trellis-venv/bin/activate
python -c "import torch; print(f'torch={torch.__version__} cuda={torch.cuda.is_available()}')"

echo
echo "=== Cloning SAM2 (facebookresearch/sam2) ==="
cd $HOME
if [ ! -d "sam2" ]; then
    git clone https://github.com/facebookresearch/sam2.git
fi
cd sam2

echo
echo "=== pip install -e . (editable install) ==="
pip install -e . --no-build-isolation

echo
echo "=== Downloading SAM 2.1 hiera_large checkpoint (856 MB) ==="
mkdir -p checkpoints
cd checkpoints
if [ ! -f "sam2.1_hiera_large.pt" ]; then
    wget -q --show-progress \
        https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_large.pt
fi

echo
echo "=== Sanity test ==="
cd $HOME/sam2
python -c "
import torch
from sam2.build_sam import build_sam2
from sam2.sam2_image_predictor import SAM2ImagePredictor

ckpt = '$HOME/sam2/checkpoints/sam2.1_hiera_large.pt'
cfg  = 'configs/sam2.1/sam2.1_hiera_l.yaml'
model = build_sam2(cfg, ckpt, device='cuda')
predictor = SAM2ImagePredictor(model)
print('SAM2 ready. param count =', sum(p.numel() for p in model.parameters())/1e6, 'M')
"

echo
echo "=== DONE ==="
echo "Checkpoint: $HOME/sam2/checkpoints/sam2.1_hiera_large.pt"
