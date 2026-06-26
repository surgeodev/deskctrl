#!/usr/bin/env python3
"""Entry point for PyInstaller builds (avoids relative import issues).

When double-clicked (no arguments), launches the GUI directly.
When run from terminal with args, acts as CLI.
"""
import sys
import os

# Ensure the package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# If double-clicked with no arguments, launch GUI directly
if len(sys.argv) <= 1:
    from deskctrl.gui import launch_gui
    launch_gui()
else:
    from deskctrl.cli import cli
    cli()
