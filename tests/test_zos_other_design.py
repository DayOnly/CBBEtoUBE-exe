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

"""#zos-other-design (GitHub issue #32): a garment difference between today's
source and the verified build keeps today's source only when NO other mod that
ships the piece loose matches the build.

The defect: a retexture mod above an SMP mod in the load order ships an older
model of a cuirass at the same path. The user's zeroed build -- what the game
loads on CBBE -- is the SMP mod's design shape for shape, but rule 5 read the
retexture's different shapes as "the build is another design" and kept the
retexture: UBE wearers got another armour, and the SMP mod's physics XML drove
chains that mesh never had.

The selection fixture is tests/test_zeroed_output_source.py's, with a third mod.
"""
import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import discovery  # noqa: E402
from src import zeroed_body as zb  # noqa: E402
from tests.test_zeroed_output_source import (  # noqa: E402
    BASE_MOD, OUT_MOD, STEM, Selection, _touch)

RETEX = "Retexture Mod"
DIFFER = "the build's shapes differ from today's source"
CLOTH_TEX = "textures\\armor\\test\\cuirass.dds"
BODY_TEX = "textures\\actors\\character\\female\\femalebody_1.dds"

BUILD = {"Cuirass": 4, "Pants": 4, "Tassets": 6}
OLD_MODEL = {"Cuirass": 4, "Pants": 9}            # no tassets, another Pants


def _shapes(counts, fill):
    return {n: np.full((k, 3), fill, dtype=np.float64) for n, k in counts.items()}


def _setup(root, monkeypatch, *, base=None, retex=OLD_MODEL, weights=("_0", "_1"),
           tex=None, physics=None):
    """RETEX wins the piece among the loose mods; BASE_MOD ships `base` (default:
    the build's design) below it; OUT_MOD is the verified build."""
    sel = Selection(root, monkeypatch)
    for w in ("_0", "_1"):
        (sel.mods / BASE_MOD / "meshes" / f"{STEM}{w}.nif").unlink()
    for w in weights:
        _touch(sel.mods / BASE_MOD / "meshes" / f"{STEM}{w}.nif")
    for w in ("_0", "_1"):
        _touch(sel.mods / RETEX / "meshes" / f"{STEM}{w}.nif")
    bld = _shapes(BUILD, 0.0)
    shapes = {RETEX: _shapes(retex, 1.0), BASE_MOD: _shapes(base or BUILD, 0.5),
              OUT_MOD: bld}

    def mod_of(p):
        s = str(p).lower()
        return next(m for m in (OUT_MOD, RETEX, BASE_MOD) if m.lower() in s)

    monkeypatch.setattr(zb, "zeroed_garment", lambda stem, files, dirs=None:
                        zb.ZeroedGarment("Test Armor", 0.0, {w: bld for w in ("_0", "_1")}))
    monkeypatch.setattr(zb, "_nif_shapes", lambda p: dict(shapes[mod_of(p)]))
    tx = tex or {}
    monkeypatch.setattr(zb, "_nif_diffuse", lambda p: dict(tx.get(mod_of(p), {})))
    if physics is not None:
        monkeypatch.setattr(discovery, "_declares_physics", lambda p: physics[mod_of(p)])
    monkeypatch.delenv("CBBE2UBE_NO_ZOS_OTHER_DESIGN", raising=False)
    return sel


def _owner(sel):
    return set(Selection.owners(sel.index(order=(OUT_MOD, RETEX, BASE_MOD))).values())


def test_the_build_is_taken_when_a_lower_mod_ships_its_design(tmp_path, monkeypatch, capsys):
    sel = _setup(tmp_path, monkeypatch)
    assert _owner(sel) == {OUT_MOD}
    err = capsys.readouterr().err
    assert (f"[zeroed-output-source] moved meshes/{STEM}: today's source is another "
            f"design -- {RETEX} ships another design") in err
    assert f"the build is {BASE_MOD}'s" in err
    rep = discovery.zeroed_output_source_report()
    assert rep["moved"] == [STEM]
    assert STEM in rep["moved_over_other_design"]
    assert rep["moved_over_body_shapes"] == {}
    assert "(1 where today's source is another design)" in err


def test_no_loose_mod_with_the_builds_design_keeps_todays_source(tmp_path, monkeypatch):
    sel = _setup(tmp_path, monkeypatch, base={"Cuirass": 4, "Pants": 4})   # no tassets
    assert _owner(sel) == {RETEX}
    rep = discovery.zeroed_output_source_report()
    assert rep["kept"] == {DIFFER: [STEM]}
    assert rep["moved_over_other_design"] == {}


def test_the_switch_keeps_rule_5_as_it_was(tmp_path, monkeypatch):
    sel = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("CBBE2UBE_NO_ZOS_OTHER_DESIGN", "1")
    assert _owner(sel) == {RETEX}


def test_a_lower_mod_with_one_weight_only_is_not_that_piece(tmp_path, monkeypatch):
    sel = _setup(tmp_path, monkeypatch, weights=("_1",))
    assert _owner(sel) == {RETEX}


def test_a_lower_mod_that_differs_from_the_build_only_in_a_body_still_matches(tmp_path, monkeypatch):
    sel = _setup(tmp_path, monkeypatch, base={**BUILD, "FemaleUnderwearBody:0": 3},
                 tex={BASE_MOD: {"FemaleUnderwearBody:0": BODY_TEX, "Cuirass": CLOTH_TEX},
                      OUT_MOD: {"Cuirass": CLOTH_TEX}})
    assert _owner(sel) == {OUT_MOD}


def test_a_physics_change_is_still_refused_first(tmp_path, monkeypatch):
    """Rule 4 is asked of today's source before any shape: a static retexture
    against an SMP build stays a refused physics change (#zeroed-smp-gain is off)."""
    monkeypatch.delenv("CBBE2UBE_ZEROED_SMP_GAIN", raising=False)
    sel = _setup(tmp_path, monkeypatch,
                 physics={RETEX: False, BASE_MOD: True, OUT_MOD: True})
    assert _owner(sel) == {RETEX}
    rep = discovery.zeroed_output_source_report()
    assert DIFFER not in rep["kept"]


def test_the_index_hands_the_lower_loose_providers_over_in_priority_order(tmp_path, monkeypatch):
    sel = _setup(tmp_path, monkeypatch)
    seen = {}

    def spy(index, win_tier, mods_root, enabled, skip, overwrite=None, alternates=None):
        seen.update(alternates or {})

    monkeypatch.setattr(discovery, "_prefer_zeroed_outputs", spy)
    third = "Third Mod"
    for w in ("_0", "_1"):
        _touch(sel.mods / third / "meshes" / f"{STEM}{w}.nif")
    sel.index(order=(OUT_MOD, RETEX, BASE_MOD, third))
    key = f"{STEM}_1.nif"
    assert [m for m, _p in seen[key]] == [BASE_MOD, third]


def test_a_build_output_is_never_an_alternate(tmp_path, monkeypatch):
    sel = _setup(tmp_path, monkeypatch)
    seen = {}

    def spy(index, win_tier, mods_root, enabled, skip, overwrite=None, alternates=None):
        seen.update(alternates or {})

    monkeypatch.setattr(discovery, "_prefer_zeroed_outputs", spy)
    # No target keys: with them the walk stops before it reaches the output.
    discovery.build_mesh_index(sel.mods, [OUT_MOD, RETEX, BASE_MOD])
    assert seen, "the index handed over no alternates at all"
    assert all(m != OUT_MOD for v in seen.values() for m, _p in v)
