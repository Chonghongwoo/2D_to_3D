#!/usr/bin/env bash
set -euo pipefail
source "$HOME/trellis-venv/bin/activate"

echo "[fix] downgrading transformers to <5..."
pip install -q 'transformers<5' 'tokenizers<0.21' 2>&1 | tail -5

echo "[fix] verifying..."
python - <<'PY'
import transformers
print("transformers", transformers.__version__)
from transformers import CLIPTextModel, AutoTokenizer
print("CLIPTextModel + AutoTokenizer import: OK")
PY
echo "[fix] DONE"
