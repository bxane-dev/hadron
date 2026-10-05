# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

ctk_datas, ctk_bins, ctk_hidden = collect_all("customtkinter")

a = Analysis(
    ["hadron_dashboard.py"],
    pathex=[],
    binaries=ctk_bins,
    datas=ctk_datas,
    hiddenimports=ctk_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Hadron",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["assets/hadron.ico"],
)
