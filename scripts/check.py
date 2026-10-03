#!/usr/bin/env python3
"""Run all local regression suites without touching live research assets."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    suite = unittest.TestSuite()
    for directory in (ROOT / "scripts/tests", ROOT / "projects/paper-ir/tests"):
        suite.addTests(unittest.TestLoader().discover(str(directory)))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())
