"""MO2's overwrite folder is read from ModOrganizer.ini, not guessed beside Mods.

MO2 sets Mods, Overwrite and Profiles independently. With only Mods moved to
another drive, `<mods>/../overwrite` points at nothing, so BodySlide output in
the real overwrite (its default target under MO2) was invisible to anything
that guessed -- and the zeroed-body resolver could return a body the game does
not load.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import paths  # noqa: E402


def _instance(tmp_path, extra=""):
    base = tmp_path / "instance"
    mods = tmp_path / "other drive" / "mods"
    for d in (base, mods, base / "overwrite"):
        d.mkdir(parents=True, exist_ok=True)
    ini = base / "ModOrganizer.ini"
    ini.write_text("[Settings]\nbase_directory=" + str(base).replace("\\", "/")
                   + "\nmod_directory=" + str(mods).replace("\\", "/") + "\n" + extra,
                   encoding="utf-8")
    return base, mods, ini


def test_a_moved_mods_folder_keeps_overwrite_at_the_base_directory(tmp_path, monkeypatch):
    base, mods, ini = _instance(tmp_path)
    monkeypatch.delenv(paths.MODS_ROOT_ENV, raising=False)
    monkeypatch.setenv(paths.MO2_INI_ENV, str(ini))
    lay = paths.discover_layout()
    assert lay.mods_root == mods
    assert paths.overwrite_dir(lay) == base / "overwrite"


def test_an_explicit_overwrite_directory_is_honoured(tmp_path, monkeypatch):
    base, _mods, ini = _instance(tmp_path, "overwrite_directory=%BASE_DIR%/ow2\n")
    monkeypatch.delenv(paths.MODS_ROOT_ENV, raising=False)
    monkeypatch.setenv(paths.MO2_INI_ENV, str(ini))
    assert paths.overwrite_dir(paths.discover_layout()) == base / "ow2"


def test_without_an_ini_the_guess_beside_mods_is_the_fallback(tmp_path):
    mods = tmp_path / "mods"
    assert paths.overwrite_dir(paths.Layout(mods_root=mods)) == tmp_path / "overwrite"
    assert paths.overwrite_dir(paths.Layout()) is None
