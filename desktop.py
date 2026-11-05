"""Local desktop launcher for the research CT annotation server.

This launcher never packages patient data. Image rendering happens in the browser;
CUDA visibility is reported separately from browser WebGL acceleration.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))

import uvicorn

from annotator.app import create_app, hash_password, import_manifest
from annotator.db import connect


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def user_data_root() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return base / "LungLabelTools"


def cuda_status() -> dict:
    command = shutil.which("nvidia-smi")
    if not command:
        return {"device_visible": False, "reason": "NVIDIA_SMI_NOT_FOUND"}
    try:
        result = subprocess.run([command, "--query-gpu=name", "--format=csv,noheader"],
                                capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {"device_visible": False, "reason": "NVIDIA_QUERY_FAILED"}
    names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    visible = result.returncode == 0 and bool(names)
    return {"device_visible": visible, "gpu_count": len(names) if visible else 0,
            "reason": "OK" if visible else "NVIDIA_QUERY_FAILED" if result.returncode else "NO_VISIBLE_GPU"}


def wait_and_open(port: int) -> None:
    url = f"http://127.0.0.1:{port}/"
    for _ in range(80):
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except Exception:
            time.sleep(0.25)


def main() -> None:
    parser = argparse.ArgumentParser(description="Lung Label Tools local research viewer")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--data-root", type=Path, action="append")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--demo", action="store_true", help="Create synthetic-only demonstration cases")
    parser.add_argument("--import-manifest", type=Path, help="Import an approved case manifest, then exit")
    parser.add_argument("--add-user", help="Create a local reader/admin account, then exit")
    parser.add_argument("--role", choices=("admin", "reader1", "reader2", "adjudicator"), default="admin")
    args = parser.parse_args()
    status = cuda_status()
    capabilities = {"platform": platform.system(), "architecture": platform.machine(),
                    "three_dimensional_view": "browser WebGL2 when available",
                    "browser_hardware_rendering": "detected in browser, not inferred from CUDA",
                    "cuda": status, "server_cuda_acceleration": False}
    if args.diagnose:
        print(json.dumps(capabilities, sort_keys=True))
        return
    if not 1 <= args.port <= 65535:
        parser.error("INVALID_PORT")
    demo_mode = args.demo or (not args.db and not args.data_root and not args.import_manifest and not args.add_user)
    if demo_mode:
        from scripts.make_synthetic_demo import main as make_demo
        demo_root = user_data_root() / "synthetic_demo"
        os.environ["ANNOTATOR_DEMO_ROOT"] = str(demo_root)
        make_demo()
        os.environ["ANNOTATOR_DIRECT_USER"] = "demo_reader1"
        db = demo_root / "annotations.sqlite3"
        roots = (demo_root / "data",)
    else:
        if not args.db or not args.data_root:
            parser.error("REAL_DATA_REQUIRES_DB_AND_APPROVED_DATA_ROOT")
        db = args.db.resolve()
        roots = tuple(p.resolve(strict=True) for p in args.data_root)
        db.parent.mkdir(parents=True, exist_ok=True)
    if args.import_manifest:
        print(json.dumps(import_manifest(db, args.import_manifest.resolve(strict=True), roots), sort_keys=True))
        return
    if args.add_user:
        password = getpass.getpass("New local password: ")
        if len(password) < 12:
            raise SystemExit("PASSWORD_TOO_SHORT")
        create_app(db, roots)
        with connect(db) as connection:
            connection.execute("INSERT INTO users(username,role,password_hash) VALUES (?,?,?)",
                               (args.add_user, args.role, hash_password(password)))
        print("USER_CREATED")
        return
    frontend = resource_root() / "frontend" / "dist"
    if not frontend.is_dir() and not getattr(sys, "_MEIPASS", None):
        frontend = resource_root() / "app" / "frontend" / "dist"
    if not (frontend / "index.html").is_file():
        raise SystemExit("FRONTEND_BUILD_MISSING")
    app = create_app(db, roots, frontend)
    print(json.dumps({"url": f"http://127.0.0.1:{args.port}/", "mode": "synthetic_demo" if demo_mode else "real_data",
                      "cuda_visible": status["device_visible"],
                      "server_cuda_acceleration": False}, sort_keys=True), flush=True)
    if not args.no_browser:
        threading.Thread(target=wait_and_open, args=(args.port,), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
