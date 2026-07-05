# -*- mode: python ; coding: utf-8 -*-
# CBBEtoUBE - CBBE/3BA to UBE armor converter
# Copyright (C) 2026 DayOnly
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""PyInstaller spec for CBBEtoUBE.exe (the standalone CBBE/3BA -> UBE converter).

Build:  pyinstaller CBBEtoUBE.spec          (or: scripts\build_exe.ps1)
Output: dist\CBBEtoUBE\CBBEtoUBE.exe        (onedir bundle)

ONEDIR (not onefile) on purpose: the converter fans NIF work out across a
ProcessPoolExecutor. On Windows (spawn) every worker re-launches CBBEtoUBE.exe;
with onefile each of those re-launches would re-extract the whole ~hundreds-of-MB
bundle to a fresh temp dir (slow, wasteful, and racy). With onedir the workers
share the already-unpacked dist folder, so worker startup is instant.

Bundled bits that PyInstaller can't find on its own:
  * pyn (pynifly) — imported dynamically from .pynifly/ at runtime, invisible to
    static analysis, so it's added via pathex + collect_submodules('pyn').
  * NiflyDLL.dll — placed at the bundle root so pyn's own loader finds it at
    dirname(dirname(niflydll.__file__))/NiflyDLL.dll == <bundle>/NiflyDLL.dll.
"""
from PyInstaller.utils.hooks import collect_submodules

# pyn is a small, pure-Python package (no submodule imports bpy) — collecting
# all of it is safe and guarantees pynifly's relative imports resolve.
hiddenimports = collect_submodules("pyn")
# scipy.spatial.cKDTree (armor fit + overlay correspondence) and scipy.ndimage
# (distance_transform_edt -> UV gutter padding in the overlay transfer). The
# official scipy hook collects the spatial extensions, but name the surfaces we
# use explicitly so the frozen exe never ships without them (the overlay
# feature imports scipy.ndimage lazily, exactly the case PyInstaller misses).
hiddenimports += ["scipy.spatial", "scipy.spatial._ckdtree"]
hiddenimports += collect_submodules("scipy.ndimage")
# lz4 (frame) decompresses Skyrim SE BSA (v105) mesh entries so the converter
# can pull vanilla armor meshes straight from the base-game Skyrim - Meshes
# BSAs (standalone vanilla-armor coverage, no replacer mod required). It's
# imported lazily inside bsa_strings.read_file, so name it explicitly or the
# frozen exe ships without it and BSA mesh extraction silently returns None.
hiddenimports += ["lz4", "lz4.frame", "lz4.block"]
# Optional Tkinter GUI (the `gui` subcommand, src/gui.py). tkinter is imported
# lazily inside launch_gui(), so name src.gui + the tkinter submodules
# explicitly or the frozen exe ships without them and `CBBEtoUBE.exe gui` fails.
hiddenimports += ["src.gui", "src.gui_settings", "src.exclusions",
                  "src.preflight",
                  "tkinter", "tkinter.ttk",
                  "tkinter.scrolledtext", "tkinter.filedialog",
                  "tkinter.messagebox"]

a = Analysis(
    ["cbbe_to_ube_main.py"],
    pathex=[".", ".pynifly"],
    binaries=[(".pynifly/NiflyDLL.dll", ".")],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "bpy", "bpy_extras", "bmesh", "mathutils",  # Blender — never needed
        # tkinter is NOW bundled (the `gui` subcommand needs it).
        "matplotlib", "PIL", "pytest", "IPython", "pandas",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CBBEtoUBE",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX can corrupt numpy/scipy DLLs — leave off
    console=False,        # windowed: double-click / MO2 launches the GUI, no console window
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="CBBEtoUBE",
)
