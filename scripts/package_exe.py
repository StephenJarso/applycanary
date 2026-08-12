#!/usr/bin/env python3
"""Build standalone executable release (.exe / PyInstaller bundle / portable package) for ApplyCanary.

Usage:
    python3 scripts/package_exe.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT_DIR / "dist"
BUILD_DIR = ROOT_DIR / "build"


def build_frontend() -> None:
    print("=== Step 1: Building React Frontend ===")
    frontend_dir = ROOT_DIR / "frontend"
    subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=frontend_dir, check=True)
    subprocess.run(["npm", "run", "build"], cwd=frontend_dir, check=True)


def build_pyinstaller_bundle() -> bool:
    print("=== Step 2: Running PyInstaller Standalone Executable Build ===")
    spec_file = ROOT_DIR / "applycanary.spec"

    # Check if objdump is missing on linux
    if os.name != "nt" and not shutil.which("objdump"):
        print("Notice: 'objdump' not found on system. Skipping binary PyInstaller compile, using portable bundle.")
        return False

    try:
        # --noconfirm is required, not optional: without it PyInstaller prompts
        # before replacing a non-empty dist/applycanary and aborts on the closed
        # stdin of a script or CI runner. The failure was then swallowed below,
        # so the release silently kept whatever binary was already on disk.
        cmd = [
            sys.executable, "-m", "PyInstaller",
            "--clean", "--noconfirm", str(spec_file),
        ]
        subprocess.run(cmd, cwd=ROOT_DIR, check=True)

        archive_name = "applycanary_standalone_win64" if os.name == "nt" else "applycanary_standalone_linux"
        archive_path = DIST_DIR / archive_name
        shutil.make_archive(str(archive_path), "zip", DIST_DIR, "applycanary")
        print(f"\nSUCCESS: PyInstaller binary release created at {archive_path}.zip")
        return True
    except subprocess.CalledProcessError as err:
        # Never downgrade this to a warning: a release build that cannot produce
        # its headline artifact must fail loudly rather than publish a stale one.
        raise SystemExit(
            f"PyInstaller build failed (exit {err.returncode}). "
            "Fix the build or pass --portable-only to skip it deliberately."
        ) from err


def build_portable_bundle() -> None:
    print("=== Step 3: Creating Self-Contained Portable Package ===")
    portable_dir = BUILD_DIR / "applycanary_portable"
    if portable_dir.exists():
        shutil.rmtree(portable_dir)

    portable_dir.mkdir(parents=True)

    # Copy app files
    shutil.copytree(ROOT_DIR / "app", portable_dir / "app")
    shutil.copy(ROOT_DIR / "run.py", portable_dir / "run.py")
    shutil.copy(ROOT_DIR / "companies.yaml", portable_dir / "companies.yaml")
    shutil.copy(ROOT_DIR / "requirements.txt", portable_dir / "requirements.txt")
    if (ROOT_DIR / ".env.example").exists():
        shutil.copy(ROOT_DIR / ".env.example", portable_dir / ".env.example")

    # Add launcher script
    launcher_sh = portable_dir / "run.sh"
    launcher_sh.write_text(
        "#!/bin/sh\n"
        "if [ ! -d \".venv\" ]; then\n"
        "  python3 -m venv .venv\n"
        "  .venv/bin/pip install -r requirements.txt\n"
        "fi\n"
        ".venv/bin/python run.py \"$@\"\n"
    )
    launcher_sh.chmod(0o755)

    launcher_bat = portable_dir / "run.bat"
    launcher_bat.write_text(
        "@echo off\n"
        "if not exist .venv (\n"
        "  python -m venv .venv\n"
        "  .venv\\Scripts\\pip install -r requirements.txt\n"
        ")\n"
        ".venv\\Scripts\\python run.py %*\n"
    )

    zip_path = DIST_DIR / "applycanary_portable"
    shutil.make_archive(str(zip_path), "zip", BUILD_DIR, "applycanary_portable")
    print(f"SUCCESS: Portable package created at {zip_path}.zip")


def main() -> None:
    # --skip-frontend: the CI release workflow builds the bundle once and shares
    # it across platform jobs, so rebuilding here would need Node on every runner.
    skip_frontend = "--skip-frontend" in sys.argv
    portable_only = "--portable-only" in sys.argv

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    if not skip_frontend:
        build_frontend()
    if not portable_only:
        build_pyinstaller_bundle()
    build_portable_bundle()

    print("\n=== Release Artifacts Summary ===")
    for item in DIST_DIR.glob("*"):
        size_mb = item.stat().st_size / (1024 * 1024)
        print(f" - {item.name} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
