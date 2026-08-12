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

"""#identity-stb-collider -- a collider bone must never be weighted before its
skin-to-bone transform is known to exist.

Both collider builders did:

    ns.add_bone(b)
    try:    ns.set_skin_to_bone_xform(b, donor.get_shape_skin_to_bone(b))
    except: pass
    ns.setShapeWeights(b, pairs)      # <-- weights onto an IDENTITY transform

An identity STB skins those vertices to the ORIGIN. This project records that
as the explosion mode ("an identity STB = origin spike = explosion"), and it is
happening on a COLLIDER -- the surface the body's own physics collides against,
where a previous well-meant edit tore breasts off and dropped them through the
terrain.

These are SOURCE-level assertions rather than a live build: constructing a real
skinned NIF in a unit test needs the pynifly DLL and a donor body, and the
property being defended is an ORDERING one that is visible in the source. A
behavioural version would be better and is not cheap; this is the ratchet that
fits in the suite.
"""
import re
from pathlib import Path

import pytest

SRC = (Path(__file__).resolve().parents[1] / "src" / "nif_convert.py"
       ).read_text(encoding="utf-8")

BUILDERS = ("_add_butt_collider_patch", "_add_skirt_collider_proxy")


def _body(fn_name: str) -> str:
    i = SRC.index(f"def {fn_name}(")
    j = SRC.find("\ndef ", i + 1)
    return SRC[i:j if j > 0 else len(SRC)]


@pytest.mark.parametrize("fn", BUILDERS)
def test_transform_is_never_set_inside_a_swallowing_handler(fn):
    """The original defect, stated exactly: a `set_skin_to_bone_xform` whose
    failure is discarded, in a builder that then writes weights."""
    body = _body(fn)
    for m in re.finditer(r"set_skin_to_bone_xform", body):
        after = body[m.end():m.end() + 260]
        assert not re.search(r"except[^\n]*:\s*\n\s*pass", after), (
            f"{fn}: a skin-to-bone write is followed by a swallowing handler")


@pytest.mark.parametrize("fn", BUILDERS)
def test_no_bone_is_added_before_its_transform_is_read(fn):
    """`add_bone` must not run until the transform is in hand. Skipping a bone
    after adding it strands it with an empty weight list, which is
    `#zeroweight-bone-desync` -- an equip CTD -- so 'just skip the bad bone'
    is not available as a fix once the shape has been touched."""
    body = _body(fn)
    add = body.index("add_bone(")
    read = body.index("get_shape_skin_to_bone(")
    assert read < add, (
        f"{fn}: get_shape_skin_to_bone must be read before add_bone; "
        f"otherwise a failed read can only be handled by stranding a bone")


@pytest.mark.parametrize("fn", BUILDERS)
def test_an_unreadable_transform_aborts_the_whole_patch(fn):
    """Fail CLOSED. The alternative -- write the other bones -- leaves the
    affected vertices under-weighted, which displaces them; and the builder's
    own handler returns BEFORE the save, so aborting leaves the NIF on disk
    untouched."""
    body = _body(fn)
    assert re.search(r"raise RuntimeError\(\s*\n?\s*f?\"skin-to-bone", body), (
        f"{fn}: an unreadable skin-to-bone transform must abort the patch")


# --- NEGATIVE CONTROLS ----------------------------------------------------
# A ratchet that has never failed proves nothing. These run the same three
# detectors against the code as it ACTUALLY WAS and require each to fire. They
# are self-contained rather than mutating the real file, because a census may
# be importing it -- and a detector verified against a literal is verified
# against exactly the defect it claims to catch.

_OLD = '''
def _add_butt_collider_patch(p):
        for bname, pairs in (base.bone_weights or {}).items():
            sub = [(rep_pos[int(vi)], float(w)) for vi, w in pairs]
            if not sub:
                continue
            ns.add_bone(bname)
            try:
                ns.set_skin_to_bone_xform(
                    bname, base.get_shape_skin_to_bone(bname))
            except Exception:
                pass
            ns.setShapeWeights(bname, sub)
'''


def test_control_swallowing_handler_detector_fires_on_the_old_code():
    fired = False
    for m in re.finditer(r"set_skin_to_bone_xform", _OLD):
        after = _OLD[m.end():m.end() + 260]
        if re.search(r"except[^\n]*:\s*\n\s*pass", after):
            fired = True
    assert fired, "the detector cannot see the defect it was written for"


def test_control_ordering_detector_fires_on_the_old_code():
    add = _OLD.index("add_bone(")
    read = _OLD.index("get_shape_skin_to_bone(")
    assert not (read < add), "the ordering detector cannot see the old order"


def test_control_abort_detector_fires_on_the_old_code():
    assert not re.search(r"raise RuntimeError\(\s*\n?\s*f?\"skin-to-bone", _OLD), \
        "the abort detector would pass code that never aborts"
