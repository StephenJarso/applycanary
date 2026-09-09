# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for ApplyCanary desktop application."""

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

added_files = [
    ('companies.yaml', '.'),
    ('frontend/dist', 'frontend/dist'),
    ('assets/icon.ico', 'assets'),
    ('assets/icon.svg', 'assets'),
]

# Every app submodule. The entrypoint imports app.main directly (not as a module
# string) so PyInstaller's graph finds most of these, but sources and notifiers
# are imported lazily by registry lookup and would otherwise be omitted -- failing
# only at runtime on the user's machine. Sweep rather than hand-list, so a newly
# added source is packaged without touching this file.
hidden_imports = collect_submodules('app') + [
    'uvicorn', 'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
    'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan', 'uvicorn.lifespan.on',
    'fastapi', 'sqlmodel', 'sqlalchemy', 'pydantic', 'pydantic_settings',
    'apscheduler', 'apscheduler.schedulers.asyncio',
    'apscheduler.triggers.cron', 'apscheduler.triggers.interval',
    'jinja2', 'html2text', 'docx', 'httpx',
    # pywebview and its platform backends
    'webview', 'webview.window', 'webview.platforms',
]

# Collect pywebview's static assets (CSS, JS for the webview renderer).
pywebview_datas = collect_data_files('webview')
added_files += [(src, dst) for src, dst in pywebview_datas]

a = Analysis(
    ['run_desktop.py'],
    pathex=['.'],
    binaries=[],
    datas=added_files,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ApplyCanary',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,           # no terminal window — it's a desktop app
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ApplyCanary',
)
