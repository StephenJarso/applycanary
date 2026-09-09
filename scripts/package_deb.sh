#!/usr/bin/env bash
# Build a .deb package for the ApplyCanary desktop application.
#
# Expects the PyInstaller bundle at dist/ApplyCanary/
# Output: dist/ApplyCanary_<version>_amd64.deb
#
# Install:   sudo dpkg -i dist/ApplyCanary_0.2.0_amd64.deb
# Remove:    sudo dpkg -r applycanary

set -euo pipefail

VERSION="${1:-0.2.0}"
ARCH="amd64"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="${ROOT_DIR}/build/deb"
DIST_DIR="${ROOT_DIR}/dist"
PKG_NAME="applycanary"

echo "=== Building ApplyCanary Desktop .deb v${VERSION} ==="

# Source: the PyInstaller one-dir build
SRC_DIR="${DIST_DIR}/ApplyCanary"
if [ ! -d "${SRC_DIR}" ]; then
    echo "ERROR: ${SRC_DIR} not found. Run PyInstaller first."
    exit 1
fi

# Clean previous build
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}/DEBIAN"
mkdir -p "${BUILD_DIR}/opt/applycanary"
mkdir -p "${BUILD_DIR}/usr/share/applications"
mkdir -p "${BUILD_DIR}/usr/share/icons/hicolor/256x256/apps"
mkdir -p "${BUILD_DIR}/usr/bin"
mkdir -p "${DIST_DIR}"

# Debian control file
cat <<EOF > "${BUILD_DIR}/DEBIAN/control"
Package: ${PKG_NAME}
Version: ${VERSION}
Architecture: ${ARCH}
Maintainer: Stephen Jarso <stephen@applycanary.local>
Depends: libwebkit2gtk-4.1-0
Section: utils
Priority: optional
Description: AI Career Agent — job discovery, ATS scoring, interview prep
 ApplyCanary runs as a native desktop application on your machine.
 It polls job boards around the clock, scores openings against your
 resume, and prepares tailored applications. Bring your own LLM API
 key or use the server defaults.
EOF

# Copy the PyInstaller bundle into /opt/applycanary
cp -r "${SRC_DIR}/"* "${BUILD_DIR}/opt/applycanary/"
chmod 755 "${BUILD_DIR}/opt/applycanary/ApplyCanary"

# Launcher in /usr/bin
cat <<'LAUNCHER' > "${BUILD_DIR}/usr/bin/applycanary"
#!/bin/sh
exec /opt/applycanary/ApplyCanary "$@"
LAUNCHER
chmod 755 "${BUILD_DIR}/usr/bin/applycanary"

# Desktop entry (.desktop file)
cat <<EOF > "${BUILD_DIR}/usr/share/applications/applycanary.desktop"
[Desktop Entry]
Name=ApplyCanary
Comment=AI Career Agent — job discovery, ATS scoring, interview prep
Exec=/opt/applycanary/ApplyCanary
Icon=applycanary
Terminal=false
Type=Application
Categories=Office;Utility;
Keywords=jobs;career;resume;interview;
EOF

# Copy icon
if [ -f "${ROOT_DIR}/assets/icon_256.png" ]; then
    cp "${ROOT_DIR}/assets/icon_256.png" \
       "${BUILD_DIR}/usr/share/icons/hicolor/256x256/apps/applycanary.png"
fi

# Build .deb
DEB_FILE="${DIST_DIR}/ApplyCanary_${VERSION}_${ARCH}.deb"
dpkg-deb --build "${BUILD_DIR}" "${DEB_FILE}"

echo ""
echo "SUCCESS: ${DEB_FILE}"
ls -lh "${DEB_FILE}"
echo ""
echo "Install:  sudo dpkg -i ${DEB_FILE}"
echo "Remove:   sudo dpkg -r applycanary"
echo "Launch:   applycanary  or  find 'ApplyCanary' in your app menu"
