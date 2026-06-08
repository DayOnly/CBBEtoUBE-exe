# Third-Party Notices

CBBEtoUBE bundles and/or links the following third-party components. Each
retains its own copyright and license.

## PyNifly — `pyn` package + `NiflyDLL.dll` (GPL-3.0)

- Author: BadDog (BadDogSkyrim)
- Source: <https://github.com/BadDogSkyrim/PyNifly>
- License: GNU General Public License v3.0

PyNifly is vendored in `.pynifly/` and frozen into the built executable
(`dist/CBBEtoUBE/`). `NiflyDLL.dll` is the compiled binary of this project; its
**corresponding source is available at the URL above** (GPL-3.0 §6).

Because PyNifly is GPL-3.0 and is included here, **CBBEtoUBE as a whole is
licensed under GPL-3.0** (see [LICENSE](LICENSE)) to remain compatible.

`.pynifly/pyn/pynmathutils.py` is derived from the Python File Format
Interface (PyFFI) and carries the BSD license notice in its own header.

## Python runtime + scientific stack (in the PyInstaller bundle)

The standalone executable (`dist/CBBEtoUBE/_internal/`) embeds the CPython
runtime and the following libraries, each under its own permissive license:

- **CPython** runtime — Python Software Foundation License
- **NumPy** — BSD-3-Clause
- **SciPy** — BSD-3-Clause
- **OpenBLAS** (bundled with NumPy/SciPy) — BSD-3-Clause
- **Tcl/Tk** (for the GUI) — Tcl/Tk License (BSD-style)

Full license texts for these are distributed with each library; the project's
own license is GPL-3.0 (see [LICENSE](LICENSE)).
