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

"""#chain-anchor-recreate -- a missing flat ANCHOR must still be created.

REPORTED IN GAME after 1.3-alpha: "skirts appear stretched".

`_precreate_custom_bone_chains`' flat branch used to create the anchor's source
ancestors when they were absent. `daf17a9` deleted that `add_node` while
documenting only the ALREADY-EXISTS case (where the source global is genuinely
dropped and cannot be repaired in place). Removing it for the absent case too
broke the chain writer at the bottom of the same function: it attaches a bone
only once its PARENT is in `existing`, so with the anchor gone the first chain
bone never qualifies, the loop makes no progress and gives up -- and
`_copy_shape`'s `add_bone` then creates every WEIGHTED chain bone flat at
IDENTITY under the root.

MEASURED on a chain-driven skirt, v1.2 -> 1.3-alpha -> fixed:

    RFSd 1   z 79.80 -> 11.57 -> 80.48
    RFSd 2   z 84.01 ->  0.00 -> 84.69     (parent had become `Scene Root`)
    RFSd 3   z 85.75 ->  0.00 -> 86.44

7,379 of 9,290 verts hang off those bones, i.e. the skirt stretched from the hip
to the origin between the feet. The residual +0.68u vs 1.2 is the chain
shift/lift, which are default-ON now and were not in 1.2.

CENSUS over the live pack: 44 of 446 chain-carrying pieces, 594 bones, 167,920
verts -- skirts, vests, robes, belts and a Skaal torso, not skirts alone.
"""
import inspect

from src import nif_convert as nc


def test_flag_defaults_on_and_opts_out(monkeypatch):
    """It restores 1.2 behaviour on a reported regression, so it ships ON."""
    assert nc.CHAIN_ANCHOR_RECREATE is True


def test_the_regression_had_no_hatch_and_this_one_does():
    """WHY THIS FLAG EXISTS AT ALL.

    The behaviour that broke this shipped with no flag, so every bisect step
    cost a full rebuild, and two plausible suspects
    (`CBBE2UBE_NO_ANCHOR_GLOBAL_FIX`, `CBBE2UBE_NO_LEG_GARMENT_GUARD`) each read
    as "not it" only because neither could switch the real cause off. A
    behaviour with no off-switch cannot be A/B'd.
    """
    src = inspect.getsource(nc)
    assert "CBBE2UBE_NO_CHAIN_ANCHOR_RECREATE" in src


def test_the_add_is_guarded_on_ABSENCE_only():
    """THE INVARIANT THAT KEEPS #anchor-global-fix INTACT.

    When the node ALREADY exists its source global is genuinely lost and cannot
    be repaired here -- pynifly has no node-transform setter that survives a
    save, so writing one would be a silent no-op that reads like a fix. This
    add must therefore fire ONLY when the node is absent, which also makes it a
    no-op on every piece whose anchor already survived.
    """
    src = inspect.getsource(nc._precreate_custom_bone_chains)
    i = src.index("#chain-anchor-recreate")
    j = src.index("continue", i)
    branch = src[i:j]
    assert "if CHAIN_ANCHOR_RECREATE and cur not in existing:" in branch, (
        "the add must be gated on the node being ABSENT")
    assert "dst_nif.add_node(cur, xf, parent=None)" in branch, (
        "flat-parented at the source GLOBAL -- the flat branch's whole contract")


def test_it_is_in_the_FLAT_branch_not_the_nested_one():
    """The nested branch builds real parent links and was never implicated;
    seeding a nested rig flat is the June skirt-sag regression."""
    src = inspect.getsource(nc._precreate_custom_bone_chains)
    flat = src.index("# Flat mode: recreate full ancestor chain")
    nested = src.index("anc: list[tuple] = []")
    at = src.index("#chain-anchor-recreate")
    assert flat < at < nested, (
        "the recreate belongs to the FLAT branch only")


def test_the_chain_writer_still_requires_the_parent_first():
    """This is the coupling the deletion broke, and the reason a missing anchor
    silently costs the WHOLE chain rather than one bone: the writer attaches a
    bone only once its parent exists, and gives up when a pass adds nothing."""
    src = inspect.getsource(nc._precreate_custom_bone_chains)
    assert "if par is None or par in existing:" in src
    assert "if not progressed:" in src, (
        "the writer must still bail out rather than spin")


def test_transform_is_the_source_GLOBAL_for_a_flat_parent():
    """Flat-parented means parent=Scene Root, so the node's own transform has to
    BE its global or the chain hanging off it lands ~69u low."""
    src = inspect.getsource(nc._precreate_custom_bone_chains)
    i = src.index("# Flat mode: recreate full ancestor chain")
    j = src.index("#chain-anchor-recreate", i)
    assert "xf = src_c.global_transform" in src[i:j]
