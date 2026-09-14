# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ["stat_tracker.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("Stat_Template_DO_NOT_REMOVE.xlsx", "."),
        ("MemoryHandling/team_branding.json", "MemoryHandling"),
        ("advanced_tracking/engine/mss_character_ids.json", "advanced_tracking/engine"),
    ],
    hiddenimports=[
        "advanced_tracking.engine.collector",
        "advanced_tracking.engine.derive",
        "advanced_tracking.engine.live",
        "advanced_tracking.engine.tracking_io",
        "advanced_tracking.engine.ball_memory",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="sluggers-stat-tracker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
