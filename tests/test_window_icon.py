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

"""The application icon: it must exist, be a real multi-size .ico, and be
findable in BOTH layouts.

The frozen build carries it twice for two different jobs -- as a PE resource
(what Explorer and MO2 draw for the file) and as a bundled data file (what Tk
needs to set the window and taskbar icon). Only the second has to be located at
runtime, and the two layouts differ: a source checkout has it in the repo root,
while PyInstaller 6 unpacks a onedir build's datas into `_internal/` rather
than beside the executable. That relocation is the failure this pins -- the
first attempt looked next to the exe and found nothing.
"""
import os
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REPO = Path(__file__).resolve().parent.parent
_ICO = _REPO / "assets" / "CBBEtoUBE.ico"
# Windows asks for each of these in a different context; a missing size is
# resampled from the nearest and looks soft.
_WANT_SIZES = {16, 32, 48, 64, 128, 256}


def test_the_icon_ships_in_the_repo():
    assert _ICO.is_file(), (
        f"{_ICO} is missing -- the spec's `icon=` points at it, so the build "
        f"fails without it. Regenerate: python scripts/make_icon.py <logo.png>")


def test_it_is_a_real_ico_with_every_size_windows_asks_for():
    """Parsed from the ICONDIR header rather than trusting the extension."""
    raw = _ICO.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", raw[:6])
    assert (reserved, kind) == (0, 1), "not an ICO container"
    assert count >= 1
    sizes = set()
    for i in range(count):
        w, h = raw[6 + i * 16], raw[7 + i * 16]
        # 0 means 256 in the ICO format.
        sizes.add((w or 256, h or 256)[0])
    missing = _WANT_SIZES - sizes
    assert not missing, f"icon is missing size(s) {sorted(missing)}; has {sorted(sizes)}"


def test_the_spec_points_at_it_and_bundles_it():
    spec = (_REPO / "CBBEtoUBE.spec").read_text(encoding="utf-8")
    assert 'icon="assets/CBBEtoUBE.ico"' in spec, (
        "the exe would build without its own icon resource")
    assert '("assets/CBBEtoUBE.ico", "assets")' in spec, (
        "the .ico must ALSO ride along as data -- Tk needs a real file on disk "
        "to set the window icon, the PE resource is not reachable from Python")


def test_icon_path_finds_it_in_a_source_checkout():
    from src import gui
    p = gui.icon_path()
    assert p is not None and p.is_file()
    assert p.resolve() == _ICO.resolve()


def test_icon_path_finds_it_under_internal_in_a_frozen_onedir_build(tmp_path,
                                                                    monkeypatch):
    """THE REGRESSION THIS EXISTS FOR. PyInstaller 6 unpacks datas to
    `_internal/`, so looking beside the executable finds nothing."""
    from src import gui
    exe_dir = tmp_path / "CBBEtoUBE"
    (exe_dir / "_internal" / "assets").mkdir(parents=True)
    (exe_dir / "_internal" / "assets" / "CBBEtoUBE.ico").write_bytes(b"\x00\x00\x01\x00")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "CBBEtoUBE.exe"))
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    p = gui.icon_path()
    assert p is not None, (
        "icon_path() did not look in _internal/ -- the window would silently "
        "keep Tk's default icon in every frozen build")
    assert p.parent.parent.name == "_internal"


def test_a_missing_icon_never_breaks_the_window(monkeypatch):
    """Decoration must never be able to fail the GUI."""
    from src import gui
    monkeypatch.setattr(gui, "icon_path", lambda: None)

    class _Win:
        def iconbitmap(self, *a, **k):
            raise AssertionError("must not be called when there is no icon")

    assert gui._apply_window_icon(_Win()) is False


def test_a_tk_that_rejects_the_icon_is_survived(monkeypatch):
    from src import gui
    monkeypatch.setattr(gui, "icon_path", lambda: _ICO)

    class _Win:
        def iconbitmap(self, *a, **k):
            raise RuntimeError("this Tk has no .ico support")

    assert gui._apply_window_icon(_Win()) is False


def test_the_window_actually_asks_for_the_icon(monkeypatch):
    """Control: the two negative tests above would both pass if the helper
    simply never called iconbitmap at all."""
    from src import gui
    monkeypatch.setattr(gui, "icon_path", lambda: _ICO)
    seen = {}

    class _Win:
        def iconbitmap(self, *a, **k):
            seen["args"] = (a, k)

    assert gui._apply_window_icon(_Win()) is True
    assert seen, "iconbitmap was never called"
    # Compare the ARGUMENT, not a repr of it -- a repr on Windows doubles the
    # backslashes and a substring test against it fails on a correct value.
    args, kwargs = seen["args"]
    passed = list(args) + list(kwargs.values())
    assert any(Path(str(v)).resolve() == _ICO.resolve() for v in passed), (
        f"iconbitmap got {passed!r}, not the bundled icon")


def test_launch_gui_sets_the_icon_on_its_root_window():
    """Pinned on the source: the helper exists and is wired to the root window,
    so a refactor that drops the call is caught."""
    import inspect
    from src import gui
    src = inspect.getsource(gui.launch_gui)
    assert "_apply_window_icon(root)" in src, (
        "launch_gui no longer applies the window icon")
