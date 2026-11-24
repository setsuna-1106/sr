#!/usr/bin/env python3
"""Remove signing-incompatible Finder metadata from our generated app, then verify.

Desktop/iCloud can attach FinderInfo after the bundler signs. Only the two
attributes forbidden by codesign are removed; quarantine/provenance are retained.
"""
from pathlib import Path
import argparse
import shutil
import subprocess

DESKTOP = Path(__file__).resolve().parents[1]
BUNDLE = DESKTOP / "src-tauri/target/release/bundle/macos/科研工作台.app"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, help="Copy the generated .app to this non-iCloud directory before verification")
    args = parser.parse_args()
    if not BUNDLE.is_dir():
        raise SystemExit(f"App bundle does not exist: {BUNDLE}")
    bundle = BUNDLE
    if args.stage:
        args.stage.mkdir(parents=True, exist_ok=True)
        bundle = args.stage.resolve() / BUNDLE.name
        if bundle.exists():
            raise SystemExit(f"Refusing to replace an existing staged app: {bundle}")
        shutil.copytree(BUNDLE, bundle, symlinks=True)
    removed = 0
    attributes = subprocess.run(["/usr/bin/xattr", "-r", str(bundle)],
                                check=True, capture_output=True, text=True).stdout
    seen = set()
    for row in attributes.splitlines():
        path, separator, name = row.rpartition(": ")
        if separator and name in {"com.apple.FinderInfo", "com.apple.ResourceFork"}:
            key = (str(Path(path).resolve()), name)
            if key not in seen:
                subprocess.run(["/usr/bin/xattr", "-d", name, path], check=True)
                seen.add(key)
                removed += 1
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", str(bundle)], check=True)
    print(f"Verified app bundle: {bundle}")
    print(f"Removed signing-incompatible Finder attributes: {removed}")


if __name__ == "__main__":
    main()
