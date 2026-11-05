"""Build one native artifact on the current OS. Run on each platform separately."""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
RELEASE = ROOT / "release"


def run() -> None:
    RELEASE.mkdir(exist_ok=True)
    add_data = os.pathsep.join((str(ROOT / "app" / "frontend" / "dist"), "frontend/dist"))
    bundle_mode = "--onefile" if sys.platform == "win32" else "--onedir"
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", bundle_mode,
           "--name", "LungLabelTools", "--paths", str(ROOT / "app"),
           "--add-data", add_data, "--collect-submodules", "uvicorn",
           "--hidden-import", "scripts.make_synthetic_demo",
           "--hidden-import", "backports.tarfile",
           "--distpath", str(DIST), "--workpath", str(ROOT / "build"),
           "--specpath", str(ROOT / "build")]
    if sys.platform == "darwin":
        cmd.append("--windowed")
    cmd.append(str(ROOT / "desktop.py"))
    subprocess.run(cmd, cwd=ROOT, check=True)
    if sys.platform.startswith("linux"):
        target = RELEASE / f"LungLabelTools-linux-{platform.machine()}.tar.gz"
        with tarfile.open(target, "w:gz") as archive:
            archive.add(DIST / "LungLabelTools", arcname="LungLabelTools")
    elif sys.platform == "darwin":
        source = DIST / "LungLabelTools.app"
        if not source.is_dir():
            raise RuntimeError("MAC_APP_BUNDLE_MISSING")
        target = RELEASE / f"LungLabelTools-macos-{platform.machine()}.dmg"
        subprocess.run(["hdiutil", "create", "-volname", "LungLabelTools", "-srcfolder", str(source),
                        "-ov", "-format", "UDZO", str(target)], check=True)
    elif sys.platform == "win32":
        source = DIST / "LungLabelTools.exe"
        if not source.is_file():
            raise RuntimeError("WINDOWS_EXECUTABLE_MISSING")
        target = RELEASE / "LungLabelTools-windows-x86_64.exe"
        shutil.copy2(source, target)
    else:
        raise RuntimeError("UNSUPPORTED_PLATFORM")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (target.with_suffix(target.suffix + ".sha256")).write_text(f"{digest}  {target.name}\n")
    print(f"ARTIFACT={target.name} SHA256={digest}")


if __name__ == "__main__":
    run()
