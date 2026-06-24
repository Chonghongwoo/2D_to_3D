#!/usr/bin/env bash
set -euo pipefail
source "$HOME/trellis-venv/bin/activate"

echo "[kaolin] installing kaolin for torch-2.4.0_cu121..."
pip install --quiet kaolin -f https://nvidia-kaolin.s3.us-east-2.amazonaws.com/torch-2.4.0_cu121.html 2>&1 | tail -5

echo "[kaolin] verifying..."
python - <<'PY'
import kaolin
print("kaolin", kaolin.__version__)
from kaolin.utils.testing import check_tensor
print("check_tensor import: OK")
PY
echo "[kaolin] DONE"
