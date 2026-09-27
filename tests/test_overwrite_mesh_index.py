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

"""#overwrite-mesh-index -- the mesh index reads MO2's overwrite folder.

THE DEFECT. `build_mesh_index` walked only the enabled mods, while every other
lookup of what the game loads (`zeroed_body._layout_dirs`, the coverage step's
`_mesh_exists_anywhere`) puts overwrite first -- and BodySlide run through MO2
without an output mod writes its builds there. A mod shipping only BodySlide
projects had no loose mesh to convert, and a verified zeroed build in overwrite
could not be taken (#zeroed-output-source: "not in a mod folder").

THE RULE. Overwrite is indexed as a BodySlide output of an unnamed body: tier 2,
first among outputs -- it wins a mesh no mod ships loose, a mod's own mesh
still wins over it, and #zeroed-output-source may take a verified zeroed build
from it. The game Data folder's loose meshes stay out (launched from MO2 it is
the merged view of every mod). `CBBE2UBE_NO_OVERWRITE_MESH_INDEX=1` leaves
overwrite out.
"""
from pathlib import Path

import numpy as np
import pytest

from src import discovery
from src import nif_convert_bodyrefs as br
from src import zeroed_body as zb

OFF = "CBBE2UBE_NO_OVERWRITE_MESH_INDEX"
KEY = "armor/test/cuirass_1.nif"
STEM = "armor/test/cuirass"
BASE = "Base Armour"
OUT = "Example - BodySlide Output - 3BA"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    monkeypatch.setenv("CBBE2UBE_NO_ZEROED_OUTPUT_SOURCE", "1")
    discovery._ZOS_SAID.clear()


def _touch(p: Path, data=b"nif"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _index(tmp_path, order, keys=(KEY,)):
    return discovery.build_mesh_index(tmp_path / "mods", list(order),
                                      target_keys=set(keys),
                                      overwrite=tmp_path / "overwrite")


def test_a_mesh_only_overwrite_holds_is_found(tmp_path):
    ow = _touch(tmp_path / "overwrite" / "meshes" / KEY)
    (tmp_path / "mods" / BASE).mkdir(parents=True)
    assert _index(tmp_path, [BASE]) == {KEY: ow}


def test_a_mods_own_mesh_still_wins_over_overwrite(tmp_path):
    _touch(tmp_path / "overwrite" / "meshes" / KEY)
    own = _touch(tmp_path / "mods" / BASE / "meshes" / KEY)
    assert _index(tmp_path, [BASE]) == {KEY: own}


def test_overwrite_wins_over_the_other_bodyslide_outputs(tmp_path):
    ow = _touch(tmp_path / "overwrite" / "meshes" / KEY)
    _touch(tmp_path / "mods" / OUT / "meshes" / KEY)
    assert _index(tmp_path, [OUT]) == {KEY: ow}


def test_the_modlists_overwrite_is_what_the_steps_pass(tmp_path, monkeypatch):
    from src import auto_convert as ac
    from src import paths
    lay = paths.Layout(mods_root=tmp_path / "mods", instance_dir=tmp_path,
                       overwrite_dir=tmp_path / "overwrite")
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: lay)
    assert ac._modlist_overwrite(tmp_path / "mods") == tmp_path / "overwrite"
    assert ac._modlist_overwrite(tmp_path / "other" / "mods") is None


def test_switched_off_overwrite_is_not_read(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    _touch(tmp_path / "overwrite" / "meshes" / KEY)
    out = _touch(tmp_path / "mods" / OUT / "meshes" / KEY)
    assert _index(tmp_path, [OUT]) == {KEY: out}


# --- the zeroed build in overwrite --------------------------------------------

def _zeroed_body_in(tmp_path, monkeypatch, folder: Path):
    body_dir = folder / "meshes" / "actors" / "character" / "character assets"
    bodies = {w: _touch(body_dir / f"femalebody{w}.nif") for w in ("_0", "_1")}
    monkeypatch.setattr(zb, "zeroed_body", lambda kind, w="_1", **k: zb.ZeroedBody(
        bodies[w], "3BA", "Test Body", 0.0))
    monkeypatch.setattr(br, "_find_cbbe_base_body", lambda w="_1": bodies[w])
    monkeypatch.setattr(br, "ZEROED_BODY_REFS", True)


def test_a_zeroed_body_built_into_overwrite_provides(tmp_path, monkeypatch):
    _zeroed_body_in(tmp_path, monkeypatch, tmp_path / "overwrite")
    mods = tmp_path / "mods"
    assert discovery._zeroed_output_provider(
        mods, [BASE], set(), tmp_path / "overwrite") == (discovery.OVERWRITE_LABEL, "")
    got, why = discovery._zeroed_output_provider(mods, [BASE], set())
    assert got is None and "not in a mod folder" in why


def test_the_verified_zeroed_build_in_overwrite_is_taken(tmp_path, monkeypatch):
    """A mod's own (tier 0) piece gives way to the verified zeroed build that
    sits in overwrite, as it does to one in a BodySlide output mod."""
    monkeypatch.delenv("CBBE2UBE_NO_ZEROED_OUTPUT_SOURCE", raising=False)
    mods = tmp_path / "mods"
    for w in ("_0", "_1"):
        _touch(mods / BASE / "meshes" / f"{STEM}{w}.nif")
        _touch(tmp_path / "overwrite" / "meshes" / f"{STEM}{w}.nif")
    shape = {"Cuirass": np.array([[float(i), 1.0, 0.0] for i in range(4)])}
    monkeypatch.setattr(zb, "zeroed_garment",
                        lambda stem, files, dirs=None: zb.ZeroedGarment(
                            "Test Armor", 0.0, {"_0": shape, "_1": shape}))
    monkeypatch.setattr(zb, "_layout_dirs", lambda: [])
    monkeypatch.setattr(zb, "_nif_shapes",
                        lambda p: {n: v + 0.5 for n, v in shape.items()})
    monkeypatch.setattr(discovery, "_zeroed_output_provider",
                        lambda *a: (discovery.OVERWRITE_LABEL, ""))
    keys = {f"{STEM}{w}.nif" for w in ("_0", "_1")}
    idx = _index(tmp_path, [BASE], keys)
    assert {k: v.parents[3].name for k, v in idx.items()} == {
        k: "overwrite" for k in keys}
