# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all, collect_data_files

SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
ROOT_DIR = os.path.dirname(SPEC_DIR)

wv_datas, wv_bins, wv_hidden = collect_all("webview")
pn_datas, pn_bins, pn_hidden = collect_all("pythonnet")
cl_datas, cl_bins, cl_hidden = collect_all("clr_loader")
gq_datas, gq_bins, gq_hidden = collect_all("groq")
ctk_datas = collect_data_files("customtkinter")

datas = ctk_datas + wv_datas + pn_datas + cl_datas + gq_datas + [
    (os.path.join(ROOT_DIR, "assets", "hushflow.ico"), "assets"),
    (os.path.join(ROOT_DIR, "assets", "hushflow_logo.png"), "assets"),
    (os.path.join(ROOT_DIR, "whisper_flow.ico"), "."),
]

binaries = wv_bins + pn_bins + cl_bins + gq_bins + [
    (os.path.join(ROOT_DIR, "bin", "whisper-server.exe"), "bin"),
    (os.path.join(ROOT_DIR, "bin", "whisper-cli.exe"), "bin"),
]

hiddenimports = list(
    set(
        wv_hidden
        + pn_hidden
        + cl_hidden
        + gq_hidden
        + [
            "asr_pipeline",
            "groq",
            "httpx",
            "httpcore",
            "anyio",
            "pydantic",
            "webview",
            "pythonnet",
            "clr_loader",
            "customtkinter",
            "darkdetect",
            "pystray._win32",
            "PIL._tkinter_finder",
            "scipy.io.wavfile",
            "win32timezone",
            "win32api",
            "win32con",
            "win32gui",
            "keyboard",
            "sounddevice",
            "pyperclip",
            "psutil",
            "requests",
        ]
    )
)

a = Analysis(
    [os.path.join(ROOT_DIR, "hushflow_main.py")],
    pathex=[ROOT_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "pandas", "pytest", "torch", "transformers"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HushFlow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT_DIR, "assets", "hushflow.ico"),
    version=os.path.join(SPEC_DIR, "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="HushFlow",
)
