#!/usr/bin/env python3
"""Entry point for PyInstaller builds (avoids relative import issues)."""
import sys
import os

# Ensure the package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from deskctrl.cli import cli

if __name__ == "__main__":
    cli()
