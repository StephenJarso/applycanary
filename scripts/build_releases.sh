#!/usr/bin/env bash
# Build all ApplyCanary production release packages (.deb, .zip, .exe standalone)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=========================================="
echo "   ApplyCanary Production Release Build   "
echo "=========================================="

cd "${ROOT_DIR}"

# 1. Run tests
echo "[1/3] Running automated tests..."
.venv/bin/python -m pytest -q

# 2. Build .deb package
echo "[2/3] Building Debian/Ubuntu package (.deb)..."
./scripts/package_deb.sh

# 3. Build standalone/portable executable packages
echo "[3/3] Building executable and portable packages..."
.venv/bin/python scripts/package_exe.py

echo ""
echo "=== Production Release Build Complete ==="
echo "Artifacts generated in ${ROOT_DIR}/dist:"
ls -lh "${ROOT_DIR}/dist"
