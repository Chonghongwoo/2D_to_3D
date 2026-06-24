#!/usr/bin/env bash
set -euo pipefail
source "$HOME/trellis-venv/bin/activate"

mkdir -p /mnt/d/trellis/_workdir/outputs

echo "[sanity] starting TRELLIS sanity run with 3 character images"
python /mnt/d/trellis/_run_trellis.py \
    --inputs \
        /mnt/d/trellis/assets/example_multi_image/character_1.png \
        /mnt/d/trellis/assets/example_multi_image/character_2.png \
        /mnt/d/trellis/assets/example_multi_image/character_3.png \
    --output /mnt/d/trellis/_workdir/outputs/sanity.glb \
    --steps-ss 8 --steps-slat 8

echo "[sanity] DONE"
ls -la /mnt/d/trellis/_workdir/outputs/sanity.glb 2>/dev/null || echo "no output file"
