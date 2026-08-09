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
        cmd = [sys.executable, "-m", "PyInstaller", "--clean", str(spec_file)]
        subprocess.run(cmd, cwd=ROOT_DIR, check=True)

        archive_name = "applycanary_standalone_win64" if os.name == "nt" else "applycanary_standalone_linux"
        archive_path = DIST_DIR / archive_name
        shutil.make_archive(str(archive_path), "zip", DIST_DIR, "applycanary")
        print(f"\nSUCCESS: PyInstaller binary release created at {archive_path}.zip")
        return True
    except Exception as err:
        print(f"Warning: PyInstaller build failed ({err}). Falling back to portable bundle.")
        return False


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
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    build_frontend()
    pyinstaller_success = build_pyinstaller_bundle()
    build_portable_bundle()

    print("\n=== Release Artifacts Summary ===")
    for item in DIST_DIR.glob("*"):
        size_mb = item.stat().st_size / (1024 * 1024)
        print(f" - {item.name} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
