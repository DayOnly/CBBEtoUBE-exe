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

"""UBE body asset discovery from BodySlide ShapeData.

`_find_ube_template_body` and `_find_ube_body_osd` were 0.92-similar copies with
no test coverage at all, so consolidating them onto `_find_ube_shapedata` would
otherwise have been an unverified edit to the code that picks WHICH BODY the
whole conversion is fitted to.

The test that matters is `test_prefers_the_canonical_release_body`. The two-tier
name hint looks like belt-and-braces and is not: several outfit mods ship their
own UBE body variant whose filename also contains "ube" and "body", and picking
one of those instead of the canonical Release Body silently fits every armour in
the pack to the wrong reference.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import nif_convert as nc  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_cache_and_env(monkeypatch):
    monkeypatch.setattr(nc, "_BODY_DISCOVERY_CACHE", {})
    monkeypatch.delenv("CBBE2UBE_UBE_TEMPLATE", raising=False)
    monkeypatch.delenv("CBBE2UBE_UBE_OSD", raising=False)


def _stub(monkeypatch, answers):
    """Fake the mod scan. `answers` maps the hint tuple to a result."""
    seen = []

    def fake(pattern, name_substrs=()):
        seen.append((pattern, tuple(name_substrs)))
        return answers.get(tuple(name_substrs))

    monkeypatch.setattr(nc, "_glob_first_in_mods", fake)
    return seen


CANON = Path("mods/UBE/CalienteTools/BodySlide/ShapeData/UBE/Release Body.nif")
OUTFIT = Path("mods/Frock/CalienteTools/BodySlide/ShapeData/X/ube body.nif")


def test_prefers_the_canonical_release_body(monkeypatch):
    """Both hints match something; the canonical one must win."""
    _stub(monkeypatch, {("ube", "release", "body"): CANON,
                        ("ube", "body"): OUTFIT})
    assert nc._find_ube_shapedata("k", "NOPE", "nif") == CANON


def test_falls_back_to_the_loose_hint(monkeypatch):
    _stub(monkeypatch, {("ube", "body"): OUTFIT})
    assert nc._find_ube_shapedata("k", "NOPE", "nif") == OUTFIT


def test_both_tiers_are_tried_in_order(monkeypatch):
    seen = _stub(monkeypatch, {})
    assert nc._find_ube_shapedata("k", "NOPE", "nif") is None
    assert [h for _p, h in seen] == [("ube", "release", "body"),
                                     ("ube", "body")]


def test_extension_reaches_the_glob(monkeypatch):
    """The only real difference between the template and the OSD lookup."""
    seen = _stub(monkeypatch, {})
    nc._find_ube_shapedata("a", "NOPE", "osd")
    assert all(p.endswith("/*.osd") for p, _h in seen), seen


def test_env_override_wins_when_the_file_exists(monkeypatch, tmp_path):
    p = tmp_path / "custom.nif"
    p.write_bytes(b"x")
    _stub(monkeypatch, {("ube", "release", "body"): CANON})
    monkeypatch.setenv("MY_OVERRIDE", str(p))
    assert nc._find_ube_shapedata("k", "MY_OVERRIDE", "nif") == p


def test_env_override_pointing_at_nothing_is_ignored(monkeypatch, tmp_path):
    """A stale override must fall through to the scan, not disable discovery."""
    _stub(monkeypatch, {("ube", "release", "body"): CANON})
    monkeypatch.setenv("MY_OVERRIDE", str(tmp_path / "gone.nif"))
    assert nc._find_ube_shapedata("k", "MY_OVERRIDE", "nif") == CANON


def test_result_is_cached_and_the_scan_runs_once(monkeypatch):
    seen = _stub(monkeypatch, {("ube", "release", "body"): CANON})
    nc._find_ube_shapedata("k", "NOPE", "nif")
    n = len(seen)
    nc._find_ube_shapedata("k", "NOPE", "nif")
    assert len(seen) == n, "cache miss; this scans every mod per NIF"


def test_a_negative_result_is_cached_too(monkeypatch):
    seen = _stub(monkeypatch, {})
    assert nc._find_ube_shapedata("k", "NOPE", "nif") is None
    n = len(seen)
    assert nc._find_ube_shapedata("k", "NOPE", "nif") is None
    assert len(seen) == n, "re-scanned every mod to rediscover nothing"


def test_the_two_callers_do_not_share_a_cache_slot(monkeypatch):
    """Same hints, same shape -- only the key and extension differ. A shared key
    would hand the OSD lookup the template NIF."""
    _stub(monkeypatch, {("ube", "release", "body"): CANON})
    nif = nc._find_ube_template_body()
    monkeypatch.setattr(nc, "_glob_first_in_mods",
                        lambda p, name_substrs=(): Path("body.osd"))
    osd = nc._find_ube_body_osd()
    assert nif == CANON and osd == Path("body.osd")


def test_the_public_wrappers_use_their_documented_env_vars(monkeypatch,
                                                           tmp_path):
    a, b = tmp_path / "t.nif", tmp_path / "o.osd"
    a.write_bytes(b"x")
    b.write_bytes(b"x")
    _stub(monkeypatch, {})
    monkeypatch.setenv("CBBE2UBE_UBE_TEMPLATE", str(a))
    monkeypatch.setenv("CBBE2UBE_UBE_OSD", str(b))
    assert nc._find_ube_template_body() == a
    assert nc._find_ube_body_osd() == b
