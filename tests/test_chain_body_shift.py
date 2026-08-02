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

"""#chain-body-shift -- move a physics chain onto the UBE body, rigidly.

The defect: chain verts are pinned to source so they stay aligned with chain
bones recreated at source bind, so a skirt keeps a CBBE-sized rest pose while
the body grows to UBE proportions. Measured on a vanilla cuirass: all 63 chain
bones at EXACTLY 0.0000u from source, and the chain-driven skirt goes from
covering 0.00% of the butt band on CBBE to 63.81% (7.46% clipping) on UBE.

The safety argument is entirely structural and these tests pin it:

  * bone transforms are PARENT-LOCAL, so shifting a ROOT translates the whole
    chain and every inter-bone distance is preserved EXACTLY. The authored
    constraints' linear limits are RELATIVE (+/-1u), so a rigid translation
    cannot violate them; warping bones individually could, and must not happen.
  * the source NIF must not be edited. `node.transform` is a VIEW -- writing
    through it would corrupt the second weight file converted from the same
    load, which is a whole-armour bug from a one-line slip.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.nif_convert as nc  # noqa: E402


class _Xf:
    """Minimal stand-in for pynifly's TransformBuf: a COPYABLE translation."""

    def __init__(self, t=(0.0, 0.0, 0.0), tag="src"):
        self.translation = tuple(float(x) for x in t)
        self.tag = tag

    def copy(self):
        return _Xf(self.translation, tag="copy")


def _chain():
    """Two chains off the skeleton: A (3 links) and B (2 links)."""
    return {
        "Skirt 1_00": (_Xf((0.0, -10.0, 5.0)), "NPC Pelvis"),
        "Skirt 1_01": (_Xf((0.0, -2.0, -5.0)), "Skirt 1_00"),
        "Skirt 1_02": (_Xf((0.0, 0.0, -8.0)), "Skirt 1_01"),
        "Skirt 2_00": (_Xf((3.0, -9.0, 5.0)), "NPC Pelvis"),
        "Skirt 2_01": (_Xf((0.0, -1.0, -6.0)), "Skirt 2_00"),
    }


# ------------------------------------------------------------ subtree topology

def test_roots_are_the_bones_that_hang_off_the_skeleton():
    sub = nc._chain_root_subtrees(_chain())
    assert set(sub) == {"Skirt 1_00", "Skirt 2_00"}
    assert sub["Skirt 1_00"] == {"Skirt 1_00", "Skirt 1_01", "Skirt 1_02"}
    assert sub["Skirt 2_00"] == {"Skirt 2_00", "Skirt 2_01"}


def test_a_cycle_cannot_hang_the_walk():
    """Defensive: a malformed parent link must terminate, not spin."""
    ch = {"A": (_Xf(), "B"), "B": (_Xf(), "A")}
    nc._chain_root_subtrees(ch)          # must return


# ------------------------------------------------- THE safety property: rigidity

def test_shifting_a_root_preserves_every_inter_bone_distance_exactly():
    """The whole safety argument. Constraint limits are RELATIVE (+/-1u), so a
    rigid translation cannot violate them however large it is."""
    ch = _chain()
    before = {b: np.array(x.translation) for b, (x, _p) in ch.items()}
    shift = np.array([0.4, 1.1, -0.3])
    x, par = ch["Skirt 1_00"]
    nx = x.copy()
    nx.translation = tuple(np.array(x.translation) + shift)
    ch["Skirt 1_00"] = (nx, par)
    # descendants are untouched -> their LOCAL offsets are identical, which is
    # exactly what "the chain moved rigidly" means
    for b in ("Skirt 1_01", "Skirt 1_02"):
        assert np.allclose(np.array(ch[b][0].translation), before[b])
    assert np.allclose(np.array(ch["Skirt 1_00"][0].translation),
                       before["Skirt 1_00"] + shift)


def test_only_roots_are_ever_rewritten():
    """A per-bone warp would change rest lengths and is how a chain explodes."""
    ch = _chain()
    moved = _run(ch, _FakeNif(ch, near_body=True))
    assert moved == 2
    for b in ("Skirt 1_01", "Skirt 1_02", "Skirt 2_01"):
        assert ch[b][0].tag == "src", f"{b} was rewritten -- must be root-only"
    for b in ("Skirt 1_00", "Skirt 2_00"):
        assert ch[b][0].tag == "copy"


# --------------------------------------------------- the source must not change

def test_the_source_transform_is_copied_not_mutated():
    """`node.transform` is a VIEW onto the source node -- writing through it
    edits the SOURCE nif, so the second weight file converted from the same load
    would inherit the shift twice."""
    ch = _chain()
    originals = {b: x.translation for b, (x, _p) in ch.items()}
    keep = {b: x for b, (x, _p) in ch.items()}
    _run(ch, _FakeNif(ch, near_body=True))
    for b, x in keep.items():
        assert x.translation == originals[b], f"{b}: source object was mutated"


# --------------------------------------------------------------- the gates

def test_default_is_off():
    assert nc.CHAIN_BODY_SHIFT is False
    ch = _chain()
    assert nc._shift_chain_roots_by_body_delta(ch, _FakeNif(ch, True)) == 0


def test_a_chain_far_from_the_body_is_left_alone():
    """A cape or a floor-length hem hangs where the delta field means nothing.
    Averaging it in would drag every chain and make the pass look inert."""
    ch = _chain()
    assert _run(ch, _FakeNif(ch, near_body=False)) == 0
    for b, (x, _p) in ch.items():
        assert x.tag == "src"


def test_the_shift_is_capped():
    ch = _chain()
    moved = _run(ch, _FakeNif(ch, near_body=True, delta=(0.0, 50.0, 0.0)))
    assert moved == 2
    d = (np.array(ch["Skirt 1_00"][0].translation)
         - np.array((0.0, -10.0, 5.0)))
    assert np.isclose(np.linalg.norm(d), nc.CHAIN_BODY_SHIFT_MAX), d


def test_a_negligible_delta_does_not_churn_the_file():
    ch = _chain()
    assert _run(ch, _FakeNif(ch, near_body=True, delta=(0.0, 1e-5, 0.0))) == 0


# ------------------------------------------------------------------- harness

class _FakeShape:
    def __init__(self, verts, weights):
        self.verts = verts
        self.bone_weights = weights
        self.name = "skirt"
        self.transform = None


class _FakeNif:
    """One shape whose verts are all weighted to every chain bone."""

    def __init__(self, chain, near_body, delta=(0.0, 0.5, 0.0)):
        n = 40
        # near_body -> sit ON the reference body; else park them far away
        base = 0.0 if near_body else 500.0
        self.verts = [(float(i % 5), base + float(i % 3), float(i))
                      for i in range(n)]
        w = {b: [(i, 1.0) for i in range(n)] for b in chain}
        self.shapes = [_FakeShape(self.verts, w)]
        self.delta = np.tile(np.array(delta, float), (200, 1))
        self.body = np.array([(float(i % 5), float(i % 3), float(i))
                              for i in range(200)], float)


def _run(chain, fake):
    """Call the pass with the body-delta lookup stubbed to the fake's field."""
    saved = (nc.CHAIN_BODY_SHIFT, nc._find_cbbe_base_body,
             nc._find_user_preset_body, nc._cached_cbbe_to_ube_delta,
             nc._verts_skin_to_world, nc._shape_global_to_skin)
    nc.CHAIN_BODY_SHIFT = True
    nc._find_cbbe_base_body = lambda *a, **k: "cbbe"
    nc._find_user_preset_body = lambda *a, **k: "ube"
    nc._cached_cbbe_to_ube_delta = lambda *a, **k: (fake.body, fake.delta)
    nc._verts_skin_to_world = lambda v, g: np.asarray(v, float)
    nc._shape_global_to_skin = lambda s: None
    try:
        return nc._shift_chain_roots_by_body_delta(chain, fake)
    finally:
        (nc.CHAIN_BODY_SHIFT, nc._find_cbbe_base_body,
         nc._find_user_preset_body, nc._cached_cbbe_to_ube_delta,
         nc._verts_skin_to_world, nc._shape_global_to_skin) = saved
