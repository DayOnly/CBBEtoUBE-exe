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

"""The `--incremental` floor must see CONFIG changes, not just file mtimes.

WHY THESE TESTS EXIST. The floor used to compare only mtimes: source NIF,
converter code, body ref. Meanwhile `nif_convert.py` reads 135 distinct
`CBBE2UBE_*` environment variables. Flipping one and re-running incrementally
printed "reusing N up-to-date NIFs" while the changed setting never reached a
single mesh -- a clean-looking result that measured nothing, which is this
project's documented dominant failure mode. That gap is what kept incremental
reuse opt-in.

Every test here pairs a POSITIVE (the thing that should invalidate does) with a
NEGATIVE (the thing that should NOT invalidate doesn't). A fingerprint that
changes on everything is as useless as one that changes on nothing: the first
makes reuse never apply, the second makes it unsafe. Both failures are silent
without the pair.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import auto_convert as ac  # noqa: E402


def _args(**kw):
    base = dict(
        ube_body_ref=None, convert_overlays=False, overlay_copy=False,
        overlay_skip_male=False, overlays_only=False, no_textures=False,
        copy_textures=False, no_ube_native_scan=False,
        # deliberately-ignored knobs, present so the test proves they are ignored
        workers=8, incremental=True, plugins_only=False, list_only=False,
        render_previews=False, merged_name="CBBE_to_UBE_Combined.esp",
        esp_name=None, no_auto_merge=False, unmerged_patch_subdir=None,
        only_mods=None, exclude_mods=None, overlay_mods=None,
        overlay_exclude_mods=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No stray CBBE2UBE_* from the developer's shell may leak into a test --
    that would make results depend on who ran them."""
    for k in list(os.environ):
        if k.startswith("CBBE2UBE_"):
            monkeypatch.delenv(k, raising=False)


def test_fingerprint_stable_when_nothing_changes():
    a = _args()
    assert ac._nif_config_fingerprint(a) == ac._nif_config_fingerprint(a)


@pytest.mark.parametrize("var,val", [
    ("CBBE2UBE_CHEST_FOLLOW_MARGIN", "0.42"),
    ("CBBE2UBE_BUTT_STRENGTH", "1.5"),
    ("CBBE2UBE_ANTIPOKE_SMOOTH", "0"),
    ("CBBE2UBE_NO_STANDOFF_AUDIT", "1"),
    ("CBBE2UBE_SOME_FLAG_ADDED_IN_FUTURE", "7"),   # prefix scan, not a list
])
def test_env_change_changes_fingerprint(monkeypatch, var, val):
    """POSITIVE: any CBBE2UBE_* var must invalidate.

    The last case is the point of scanning by PREFIX: a hand-maintained list of
    135 names goes stale the first time a knob is added, which is exactly how
    the project notes' four-module whitelist ended up wrong (it predated
    `nif_convert` importing `fit_metrics`). A prefix scan cannot go stale.
    """
    before = ac._nif_config_fingerprint(_args())
    monkeypatch.setenv(var, val)
    assert ac._nif_config_fingerprint(_args()) != before


def test_env_value_change_not_just_presence(monkeypatch):
    monkeypatch.setenv("CBBE2UBE_BUST_CLEAR", "1.0")
    one = ac._nif_config_fingerprint(_args())
    monkeypatch.setenv("CBBE2UBE_BUST_CLEAR", "2.0")
    assert ac._nif_config_fingerprint(_args()) != one


def test_unrelated_env_ignored(monkeypatch):
    """NEGATIVE: a fingerprint that moves on unrelated env is useless."""
    before = ac._nif_config_fingerprint(_args())
    monkeypatch.setenv("PATH", os.environ.get("PATH", "") + os.pathsep + "/tmp/x")
    monkeypatch.setenv("SOME_OTHER_TOOL_SETTING", "9")
    assert ac._nif_config_fingerprint(_args()) == before


@pytest.mark.parametrize("field,val", [
    ("ube_body_ref", "C:/other/body_1.nif"),
    ("convert_overlays", True),
    ("no_textures", True),
    ("no_ube_native_scan", True),
])
def test_nif_relevant_arg_changes_fingerprint(field, val):
    assert ac._nif_config_fingerprint(_args(**{field: val})) \
        != ac._nif_config_fingerprint(_args())


@pytest.mark.parametrize("field,val", [
    ("workers", 1),
    ("merged_name", "Something_Else.esp"),
    ("render_previews", True),
    ("only_mods", ["JustOneMod"]),
    ("exclude_mods", ["SomeMod"]),
])
def test_non_nif_arg_ignored(field, val):
    """NEGATIVE: ESP-only, cosmetic and mod-SELECTION flags must not
    invalidate. Selection changes which NIFs are produced, not what any single
    NIF's bytes are; folding it in would make `--only-mods X` iteration blow
    the whole cache and cost the default its usefulness."""
    assert ac._nif_config_fingerprint(_args(**{field: val})) \
        == ac._nif_config_fingerprint(_args())


def test_stamp_mtime_is_stable_when_config_unchanged(tmp_path):
    """THE failure mode that would make this silently useless: if the stamp is
    rewritten every run, its mtime is always 'now', the floor always beats
    every cached NIF, and reuse never happens while still reporting success."""
    fp = ac._nif_config_fingerprint(_args())
    first = ac._config_stamp_mtime(tmp_path, fp)
    time.sleep(0.05)
    assert ac._config_stamp_mtime(tmp_path, fp) == first


def test_stamp_mtime_advances_when_config_changes(tmp_path, monkeypatch):
    first = ac._config_stamp_mtime(tmp_path, ac._nif_config_fingerprint(_args()))
    time.sleep(0.05)
    monkeypatch.setenv("CBBE2UBE_CHEST_FOLLOW_MARGIN", "0.99")
    second = ac._config_stamp_mtime(tmp_path, ac._nif_config_fingerprint(_args()))
    assert second > first, "a changed setting must push the floor forward"


def test_stamp_failure_fails_safe(tmp_path, monkeypatch):
    """An unwritable stamp must invalidate everything (return ~now), never
    silently permit reuse against an unknown config."""
    def boom(*a, **k):
        raise OSError("read-only")
    monkeypatch.setattr(Path, "write_text", boom)
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    got = ac._config_stamp_mtime(tmp_path, "deadbeef")
    assert got >= time.time() - 5.0


def test_env_prefix_scan_covers_the_real_converter_surface():
    """Guards the CLAIM this design rests on: that a prefix scan actually
    covers what nif_convert reads. If someone starts reading settings under a
    different prefix, this fails loudly instead of the fingerprint quietly
    going blind to them."""
    import re
    src = (_REPO / "src" / "nif_convert.py").read_text(encoding="utf-8",
                                                       errors="replace")
    names = set(re.findall(r'os\.environ(?:\.get)?\(\s*"([A-Z0-9_]+)"', src))
    assert names, "found no env reads at all -- the regex or the file moved"
    stray = sorted(n for n in names if not n.startswith("CBBE2UBE_"))
    assert not stray, (
        f"nif_convert reads env var(s) the CBBE2UBE_ prefix scan cannot see: "
        f"{stray}. Either rename them or widen _nif_config_fingerprint.")
