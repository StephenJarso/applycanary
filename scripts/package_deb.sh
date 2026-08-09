#!/usr/bin/env bash
# Build a native Debian/Ubuntu .deb package for ApplyCanary
#
# Output: dist/applycanary_<version>_amd64.deb
#
# Install:   sudo dpkg -i dist/applycanary_0.1.0_amd64.deb
# Remove:    sudo dpkg -r applycanary

set -euo pipefail

VERSION="0.1.0"
ARCH="amd64"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="${ROOT_DIR}/build/deb"
DIST_DIR="${ROOT_DIR}/dist"
PKG_NAME="applycanary"

echo "=== Building ApplyCanary .deb Release v${VERSION} ==="

# 1. Build frontend bundle
echo "Building React frontend..."
cd "${ROOT_DIR}/frontend"
npm ci --no-audit --no-fund
npm run build
cd "${ROOT_DIR}"

# 2. Clean previous build directory
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}/DEBIAN"
mkdir -p "${BUILD_DIR}/opt/applycanary"
mkdir -p "${BUILD_DIR}/usr/bin"
mkdir -p "${BUILD_DIR}/lib/systemd/system"
mkdir -p "${BUILD_DIR}/var/lib/applycanary"
mkdir -p "${DIST_DIR}"

# 3. Create Debian control file
cat <<EOF > "${BUILD_DIR}/DEBIAN/control"
Package: ${PKG_NAME}
Version: ${VERSION}
Architecture: ${ARCH}
Maintainer: Stephen Jarso <stephen@applycanary.local>
Depends: python3 (>= 3.10), python3-venv, systemd
Section: utils
Priority: optional
Description: Self-hosted job discovery, ATS resume tailoring, and application tracking.
 ApplyCanary runs on your own machine, polls job boards around the clock, scores
 openings against your resume, and prepares tailored applications.
EOF

# 4. Create post-installation script
cat <<EOF > "${BUILD_DIR}/DEBIAN/postinst"
#!/bin/sh
set -e

# Create applycanary user if not present
if ! id -u applycanary >/dev/null 2>&1; then
    useradd --system --user-group --no-create-home applycanary || true
fi

# Set directory permissions
mkdir -p /var/lib/applycanary
chown -R applycanary:applycanary /var/lib/applycanary /opt/applycanary

# Install python virtualenv if needed
if [ ! -d "/opt/applycanary/.venv" ]; then
    python3 -m venv /opt/applycanary/.venv
    /opt/applycanary/.venv/bin/pip install --no-cache-dir -r /opt/applycanary/requirements.txt
fi

systemctl daemon-reload || true
systemctl enable applycanary.service || true
echo "ApplyCanary v${VERSION} installed. Start service with: sudo systemctl start applycanary"
EOF
chmod 755 "${BUILD_DIR}/DEBIAN/postinst"

# 5. Create pre-removal script
cat <<EOF > "${BUILD_DIR}/DEBIAN/prerm"
#!/bin/sh
set -e
systemctl stop applycanary.service || true
systemctl disable applycanary.service || true
EOF
chmod 755 "${BUILD_DIR}/DEBIAN/prerm"

# 6. Create Systemd Service File
cat <<EOF > "${BUILD_DIR}/lib/systemd/system/applycanary.service"
[Unit]
Description=ApplyCanary Service
After=network.target

[Service]
Type=simple
User=applycanary
WorkingDirectory=/opt/applycanary
Environment="DATA_DIR=/var/lib/applycanary"
Environment="DATABASE_URL=sqlite:////var/lib/applycanary/applycanary.db"
ExecStart=/opt/applycanary/.venv/bin/python /opt/applycanary/run.py
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF

# 7. Copy Application Files
cp -r "${ROOT_DIR}/app" "${BUILD_DIR}/opt/applycanary/"
cp "${ROOT_DIR}/run.py" "${BUILD_DIR}/opt/applycanary/"
cp "${ROOT_DIR}/companies.yaml" "${BUILD_DIR}/opt/applycanary/"
cp "${ROOT_DIR}/requirements.txt" "${BUILD_DIR}/opt/applycanary/"
if [ -f "${ROOT_DIR}/.env.example" ]; then
    cp "${ROOT_DIR}/.env.example" "${BUILD_DIR}/opt/applycanary/.env.example"
fi

# 8. Create CLI wrapper in /usr/bin/applycanary
cat <<'EOF' > "${BUILD_DIR}/usr/bin/applycanary"
#!/bin/sh
if [ -d "/opt/applycanary/.venv" ]; then
    exec /opt/applycanary/.venv/bin/python /opt/applycanary/run.py "$@"
else
    exec python3 /opt/applycanary/run.py "$@"
fi
EOF
chmod 755 "${BUILD_DIR}/usr/bin/applycanary"

# 9. Build .deb package
DEB_FILE="${DIST_DIR}/applycanary_${VERSION}_${ARCH}.deb"
dpkg-deb --build "${BUILD_DIR}" "${DEB_FILE}"

echo "SUCCESS: Debian package created at ${DEB_FILE}"
ls -lh "${DEB_FILE}"
