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

Which release that is, the DLL's hash and reported version, and every local change
to the `pyn` package (as a patch against the release) are recorded in the source
repository's [.pynifly/UPSTREAM.md](.pynifly/UPSTREAM.md);
`tests/test_pynifly_provenance.py` checks the record against the vendored files.

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
- **python-lz4** — BSD-2-Clause, Copyright (c) 2012-2013, Steeve Morin.
  Required for reading Skyrim SE BSA archives, which are LZ4-frame compressed.
- **libffi** — MIT
- **Microsoft Visual C++ runtime** — redistributable under the Microsoft
  Software License Terms accompanying the compiler

Where those texts are, inside the built bundle's `_internal/` folder:

- `licenses/CPython-LICENSE.txt` — the Python Software Foundation License, and
  the notices for what CPython itself bundles, libffi among them.
- `licenses/SciPy-LICENSE.txt` — SciPy ships no `dist-info` in the bundle, so its
  text is copied in at build time.
- `licenses/Tcl-Tk-license.terms` — the Tcl/Tk license; one text covers both.
- `numpy-<version>.dist-info/LICENSE.txt` — NumPy's own, which also carries
  OpenBLAS and LAPACK.
- `lz4-<version>.dist-info/licenses/LICENSE` — python-lz4's own.
- The Microsoft Visual C++ runtime is redistributed under Microsoft's terms,
  which accompany the redistributable itself:
  <https://visualstudio.microsoft.com/license-terms/>.

`tests/test_notices_cover_bundle.py` reads the executable's own module archive and
fails if it carries a package this file does not name, or if one of those texts is
missing from the bundle. The project's own license is GPL-3.0-or-later (see
[LICENSE](LICENSE)), a copy of which ships inside the executable's folder
alongside this file.

### Deliberately NOT bundled

OpenSSL is excluded from the frozen build. The 1.1.x series that the CPython
build links carries an advertising clause the FSF considers incompatible with
the GPL, and nothing in this project performs networking or hashing — so
`ssl`/`hashlib` (and with them `libssl`/`libcrypto`) are excluded in
`CBBEtoUBE.spec` rather than relying on a license exception. If future code
needs either module, that exclusion has to be revisited together with this
notice.
