import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
SPEC = ROOT / "sluggers-stat-tracker.spec"
NAME = "sluggers-stat-tracker"
RELEASE_FILES = {
    ROOT / "Stat_Template_DO_NOT_REMOVE.xlsx": DIST / "Stat_Template_DO_NOT_REMOVE.xlsx",
    ROOT / "MemoryHandling" / "team_branding.json": DIST / "MemoryHandling" / "team_branding.json",
}


def detect_platform() -> str:
    """Return the normalized supported platform name."""
    system = platform.system()
    if system == "Windows":
        return "windows"
    if system == "Linux":
        return "linux"
    raise RuntimeError(
        f"Unsupported build platform: {system}. "
        "dolphin-memory-engine supports this app on Windows and Linux only."
    )


def clean() -> None:
    """Remove old build artifacts."""
    for directory in (BUILD, DIST):
        if directory.exists():
            print(f"Cleaning {directory}")
            shutil.rmtree(directory)


def sync_deps() -> None:
    """Install locked project and development dependencies with uv."""
    print("Installing dependencies...")
    subprocess.run(["uv", "sync", "--locked"], cwd=ROOT, check=True)


def build() -> None:
    """Run PyInstaller to produce a single-file console executable."""
    cmd = [
        "uv",
        "run",
        "pyinstaller",
        "--clean",
        "--noconfirm",
        str(SPEC),
    ]
    print(f"Command: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=ROOT, check=True)


def copy_release_files() -> None:
    """Copy user-visible runtime assets beside the executable."""
    for source, destination in RELEASE_FILES.items():
        if not source.exists():
            raise FileNotFoundError(f"Required release file not found: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        print(f"Copying {source.relative_to(ROOT)} -> {destination.relative_to(ROOT)}")
        shutil.copy2(source, destination)


def main() -> None:
    build_platform = detect_platform()
    print(f"Platform: {build_platform}")
    print(f"Python:   {sys.version}")
    print()

    clean()
    sync_deps()
    build()
    copy_release_files()

    executable_name = f"{NAME}.exe" if build_platform == "windows" else NAME
    executable_path = DIST / executable_name
    if not executable_path.exists():
        print(f"ERROR: Expected output not found at {executable_path}")
        sys.exit(1)

    size_mb = executable_path.stat().st_size / (1024 * 1024)
    print()
    print(f"Build complete: {executable_path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
