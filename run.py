#!/usr/bin/env python3
"""Entry point for PyInstaller builds (avoids relative import issues).

When double-clicked (no arguments), launches the GUI directly.
When run from terminal with args, acts as CLI.
"""
import sys
import os

# Ensure the package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# On Windows, hide the console window when launched by double-click
# (PyInstaller builds with --console so CLI output works from terminal)
if sys.platform == "win32" and len(sys.argv) <= 1:
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        buf = (ctypes.c_uint * 1)()
        count = kernel32.GetConsoleProcessList(buf, 1)
        # count == 1 means only our process -> double-click -> hide
        if count == 1:
            kernel32.ShowWindow(kernel32.GetConsoleWindow(), 0)
    except Exception:
        pass

# If double-clicked with no arguments, launch GUI directly
if len(sys.argv) <= 1:
    from deskctrl.gui import launch_gui
    launch_gui()
else:
    from deskctrl.cli import main
    main()
