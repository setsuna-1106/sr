#!/usr/bin/env python3
"""Stable toolbox entry point for the repository-local PaperIR tool."""

from pathlib import Path
import runpy
import sys


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    implementation = repo / "projects" / "paper-ir" / "paper_ir.py"
    if not implementation.is_file():
        raise SystemExit(f"PaperIR implementation missing: {implementation}")
    runpy.run_path(str(implementation), run_name="__main__")


if __name__ == "__main__":
    main()
