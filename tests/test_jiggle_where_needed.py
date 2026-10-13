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

"""#jiggle-where-needed: a rigid torso plate keeps body-jiggle follow only
where the bouncing body would come through it.

Reported in game on a fluted cuirass: the passes that copy the body's breast
chain onto a fitted plate made it fold over the breasts while moving; its author
shipped it rigid, and rigid was confirmed in game (no folds, no poke). The
helpers are tested on a sphere 'breast' under a sphere-cap 'plate'."""
import numpy as np

import src.nif_convert as nc
from src import nif_convert_weights as nw


def test_kill_switch_is_a_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(nc, "JIGGLE_WHERE_NEEDED", False)
    f = tmp_path / "x_1.nif"
    f.write_bytes(b"not a nif")
    assert nw._rigid_plate_jiggle_where_needed(f) == 0
    assert f.read_bytes() == b"not a nif"


# ---- #jiggle-skin-source: whose skin is under the plate -------------------

def _plate_nif(path):
    from tests.synthetic_nif import build_skinned_shapes_nif, uv_sphere
    v, t, nrm = uv_sphere(9.0, centre=(0.0, 0.0, 95.0))
    return build_skinned_shapes_nif(path, [("Plate", v, t, nrm)],
                                    bones=("NPC Spine2 [Spn2]", "L Breast02"))


def test_body_slot_without_body_copy_goes_rigid(tmp_path):
    # Slot 32 hides the actor's body and the piece ships no body of its own:
    # no skin can come through, so the converter-added follow comes off.
    from tests.synthetic_nif import pynifly_available
    if not pynifly_available():
        return
    f = _plate_nif(tmp_path / "meshes" / "x" / "plate_1.nif")
    n = nw._rigid_plate_jiggle_where_needed(f, biped_slots=nc.BIPED_SLOT32_BIT)
    assert n > 0
    s = nc._pynifly().NifFile(filepath=str(f)).shapes[0]
    assert "L Breast02" not in (s.bone_names or [])
    assert all(abs(w - 1.0) < 1e-3 for _, w in s.bone_weights["NPC Spine2 [Spn2]"])


def test_actor_body_or_unknown_slots_left_alone(tmp_path):
    # Worn over the actor's own body (or slots unknown): we cannot see that
    # body's fit here, so the follow is left as it is.
    from tests.synthetic_nif import pynifly_available
    if not pynifly_available():
        return
    for slots in (0, 1 << 26):
        f = _plate_nif(tmp_path / f"s{slots}" / "meshes" / "x" / "plate_1.nif")
        before = f.read_bytes()
        assert nw._rigid_plate_jiggle_where_needed(f, biped_slots=slots) == 0
        assert f.read_bytes() == before


def test_body_slot_with_its_own_body_copy_left_alone(tmp_path):
    # A visible injected BaseShape IS skin under the plate: trimming the follow
    # there measured as more exposed skin, so the pass must not touch it.
    from tests.synthetic_nif import build_skinned_shapes_nif, pynifly_available, uv_sphere
    if not pynifly_available():
        return
    pv, pt, pn = uv_sphere(9.0, centre=(0.0, 0.0, 95.0))
    bv, bt, bn = uv_sphere(8.0, centre=(0.0, 0.0, 95.0))
    f = build_skinned_shapes_nif(tmp_path / "meshes" / "x" / "plate_1.nif",
                                 [("Plate", pv, pt, pn), ("BaseShape", bv, bt, bn)],
                                 bones=("NPC Spine2 [Spn2]", "L Breast02"))
    before = f.read_bytes()
    assert nw._rigid_plate_jiggle_where_needed(f, biped_slots=nc.BIPED_SLOT32_BIT) == 0
    assert f.read_bytes() == before
