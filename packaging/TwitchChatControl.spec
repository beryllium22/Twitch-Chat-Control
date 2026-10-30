# Build only on Windows with the pinned build dependencies.
# An explicit data allowlist keeps local profiles, credentials and logs out.
from pathlib import Path

root = Path(SPECPATH).parent
public_docs = [
    "README.md",
    "docs/SECURITY.md",
    "docs/TWITCH_SETUP.md",
    "docs/PROFILES.md",
    "docs/VALIDATION.md",
]
analysis = Analysis(
    [str(root / "packaging" / "run_app.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[(str(root / name), "docs") for name in public_docs],
    hiddenimports=["websockets.sync.client"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "ruff", "pip", "setuptools", "unittest"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="TwitchChatControl",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    uac_admin=False,
    uac_uiaccess=False,
)
collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="TwitchChatControl",
)
