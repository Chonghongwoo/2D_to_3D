#!/usr/bin/env bash
# Install Ubuntu apt packages needed for TRELLIS
set -euo pipefail
echo "[apt] whoami: $(whoami)"
echo "[apt] updating apt index..."
apt-get update -qq 2>&1 | tail -5
echo "[apt] installing python3.10-venv, build-essential, git, dos2unix..."
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3.10-venv python3.10-dev python3-pip build-essential git curl ca-certificates dos2unix libgl1 libglib2.0-0 2>&1 | tail -10
echo "[apt] DONE"
python3 -m venv --help > /dev/null && echo "[apt] venv module OK"
