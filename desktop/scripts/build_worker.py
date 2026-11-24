#!/usr/bin/env python3
"""Freeze the standard-library sr worker without copying any research assets.

Run from any directory. Dependencies stay in desktop/.build; each freeze uses a fresh temporary directory.
The complete onedir output is shipped so the app needs no system Python or PATH.
"""
from __future__ import annotations

import os
import argparse
import json
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

DESKTOP = Path(__file__).resolve().parents[1]
ROOT = DESKTOP.parent
BUILD = DESKTOP / ".build"
ENV = BUILD / "venv"
PYTHON = ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
OUTPUT = DESKTOP / "src-tauri/resources/worker"


def run(args: list[str], **kwargs: object) -> None:
    subprocess.run(args, check=True, cwd=DESKTOP, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, help="Write a clean worker into a new worker/ under this directory")
    stage = parser.parse_args().stage
    output = stage.resolve() / "worker" if stage else OUTPUT
    if stage and output.exists():
        raise SystemExit(f"Staged worker already exists: {output}")
    entry = ROOT / "scripts/sr_workbench.py"
    if not entry.is_file():
        raise SystemExit(f"Worker entrypoint does not exist: {entry}")
    sources = [ROOT / "scripts" / name for name in
               ("sr_workbench.py", "sr_workbench_store.py", "sr_workbench_trash.py", "sr_library.py", "sr_board.py")]
    def source_hashes() -> dict[str, str]:
        return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sources}
    original_hashes = source_hashes()
    BUILD.mkdir(exist_ok=True)
    env = dict(os.environ, PIP_CACHE_DIR=str(BUILD / "pip-cache"),
               PYINSTALLER_CONFIG_DIR=str(BUILD / "pyinstaller-cache"))
    if not PYTHON.exists():
        run([sys.executable, "-m", "venv", str(ENV)])
    locked = [line.strip().split("==", 1)
              for line in (DESKTOP / "requirements-build.txt").read_text().splitlines()
              if line.strip() and not line.lstrip().startswith("#")]
    result = subprocess.run([
        str(PYTHON), "-c",
        "import json,sys; from importlib.metadata import version; "
        "assert all(version(name)==expected for name,expected in json.loads(sys.argv[1]))",
        json.dumps(locked),
    ], capture_output=True)
    if result.returncode:
        run([str(PYTHON), "-m", "ensurepip", "--upgrade"], env=env)
        run([str(PYTHON), "-m", "pip", "install", "-r", str(DESKTOP / "requirements-build.txt")], env=env)
    output.parent.mkdir(parents=True, exist_ok=True)
    # A synced dist directory can reintroduce old binaries under names like "sr-worker 2".
    with TemporaryDirectory(prefix="sr-worker-freeze-") as build_dir:
        build = Path(build_dir)
        run([
            str(PYTHON), "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
            "--name", "sr-worker", "--console", "--paths", str(ROOT / "scripts"),
            "--distpath", str(build / "dist"), "--workpath", str(build / "work"),
            "--specpath", str(build), str(entry),
        ], env=env)
        if source_hashes() != original_hashes:
            raise SystemExit("Worker sources changed during packaging; rebuild from the completed source files.")
        source = build / "dist/sr-worker"
        (source / "build-info.json").write_text(json.dumps({
            "protocol_version": 1, "source_sha256": original_hashes, "build_dependencies": dict(locked),
        }, indent=2) + "\n")
        with TemporaryDirectory(prefix=".sr-stage-", dir=output.parent) as stage_dir:
            staged = Path(stage_dir) / "worker"
            shutil.copytree(source, staged, symlinks=True)
            if not stage and output.exists():
                shutil.rmtree(output)
            staged.rename(output)
    print(f"Frozen worker: {output / ('sr-worker.exe' if os.name == 'nt' else 'sr-worker')}")


if __name__ == "__main__":
    main()
