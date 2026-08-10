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

"""#chain-rest-outside-body -- lift a chain whose REST POSE is inside the body.

The defect: chain bone globals move 0.000000u through the conversion while the
body grows, so the rest pose the HDT solver pulls toward ends up inside the
buttock. Measured on the vanilla studded cuirass, 8 of 63 bones sit inside the
body under the user's preset, mean 0.900u and max 2.000u deep.

What these tests pin, in order of how much a regression would cost:

  * ROOTS ONLY. Warping bones individually changes inter-bone rest lengths and
    is how a chain explodes (`docs/PIPELINE.md` §7). Every descendant's LOCAL
    transform must come through untouched.
  * THE WANT CAP. Without it the pass recruited two FRONT skirt chains sitting
    +3.63u clear of the body, because the belly's outward morph amplitude runs
    to ~6.5u there and drove a wanted clearance larger than the actual distance.
    A confident 2.0u push on cloth with nothing wrong with it.
  * THE FRAME GUARD. An armour NIF's skeleton bones are often (0,0,0)
    placeholders, in which case composed chain globals are PELVIS-RELATIVE and
    sampling a body surface with them is meaningless. "Nothing checkable" must
    read as a refusal, never as agreement.
  * the source NIF must not be edited -- `node.transform` is a VIEW, so writing
    through it corrupts the second weight file converted from the same load.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.nif_convert as nc  # noqa: E402


class _Xf:
    """Minimal stand-in for pynifly's TransformBuf: identity rotation, unit
    scale, a COPYABLE translation, and a tag that records whether the object
    the pass left behind is the original or a copy."""

    def __init__(self, t=(0.0, 0.0, 0.0), tag="src"):
        self.translation = tuple(float(x) for x in t)
        self.rotation = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        self.scale = 1.0
        self.tag = tag

    def copy(self):
        return _Xf(self.translation, tag="copy")


class _Node:
    def __init__(self, xf):
        self.global_transform = xf
        self.parent = None


# The fake body is the plane y == 0 with its outward normal pointing -y, so a
# bone's signed clearance is simply -y: negative y is OUTSIDE, positive y is
# INSIDE. Anchor at the origin, so a chain bone's global is its local sum.
_ANCHOR = "NPC Pelvis"


def _chain(a_root_y=-4.0, a_mid_y=-1.0, b_root_y=-6.0):
    """Chain A hangs where the caller says; chain B always clears comfortably."""
    return {
        "Skirt 1_00": (_Xf((0.0, a_root_y, 5.0)), _ANCHOR),
        "Skirt 1_01": (_Xf((0.0, a_mid_y - a_root_y, -5.0)), "Skirt 1_00"),
        "Skirt 1_02": (_Xf((0.0, 0.0, -8.0)), "Skirt 1_01"),
        "Skirt 2_00": (_Xf((3.0, b_root_y, 5.0)), _ANCHOR),
        "Skirt 2_01": (_Xf((0.0, 0.0, -6.0)), "Skirt 2_00"),
    }


class _FakeShape:
    """Carries the skin the frame guard cross-checks against, and the weights
    the "does this chain drive cloth" gate reads."""

    def __init__(self, positions, honest=True):
        self.name = "skirt"
        self.bone_names = list(positions)
        self.weights = {b: [(0, 1.0)] for b in positions}
        self._pos = positions
        self._honest = honest

    @property
    def bone_weights(self):
        return self.weights

    def get_shape_skin_to_bone(self, b):
        p = np.array(self._pos[b], float)
        if not self._honest:
            p = p + 25.0          # a frame the node tree does not agree with
        return _Xf(-p)            # inv(STB) then puts the bone back at p


class _FakeNif:
    def __init__(self, chain, honest_frame=True, skinned=True):
        self.nodes = {_ANCHOR: _Node(_Xf((0.0, 0.0, 0.0)))}
        pos = _true_globals(chain)
        self.shapes = ([_FakeShape(pos, honest_frame)] if skinned else [])


def _true_globals(chain):
    """Compose the chain the way the pass does, for the fake skin to agree."""
    out = {}

    def g(b):
        if b in out:
            return out[b]
        xf, par = chain[b]
        base = np.array(g(par)) if par in chain else np.zeros(3)
        out[b] = base + np.array(xf.translation, float)
        return out[b]

    for b in chain:
        g(b)
    return {b: tuple(v) for b, v in out.items()}


def _body(amp_value=0.5, n=400):
    """The plane y == 0, outward normal -y, with a uniform morph amplitude."""
    xs = np.linspace(-20, 20, 20)
    zs = np.linspace(-20, 20, n // 20)
    V = np.array([(x, 0.0, z) for x in xs for z in zs], float)
    N = np.tile(np.array([0.0, -1.0, 0.0]), (len(V), 1))
    amp = np.full(len(V), float(amp_value))
    return V, N, amp


def _run(chain, fake, amp_value=0.5, osd=True):
    """Call the pass with body discovery stubbed to the flat fake body."""
    V, N, amp = _body(amp_value)
    if not osd:
        amp = None
    saved = (nc.CHAIN_REST_LIFT, nc._find_user_preset_body,
             nc._cached_ube_body_verts, nc._cached_body_morph_amplitude,
             nc._find_ube_body_osd, nc._shape_global_to_skin)
    nc.CHAIN_REST_LIFT = True
    nc._find_user_preset_body = lambda *a, **k: "ube"
    nc._cached_ube_body_verts = lambda *a, **k: (None, V, N)
    nc._cached_body_morph_amplitude = lambda *a, **k: amp
    nc._find_ube_body_osd = lambda *a, **k: "osd"
    nc._shape_global_to_skin = lambda s: _Xf()
    try:
        return nc._lift_chain_roots_off_body(chain, fake)
    finally:
        (nc.CHAIN_REST_LIFT, nc._find_user_preset_body,
         nc._cached_ube_body_verts, nc._cached_body_morph_amplitude,
         nc._find_ube_body_osd, nc._shape_global_to_skin) = saved


# ------------------------------------------------------------------ the gate

def test_defaults_on_since_it_was_judged_in_motion():
    """Held OFF for ballooning, collapse, pull-to-origin and a skirt standing
    too far out -- all things that only look wrong IN MOTION. Judged there on
    the piece the defect was reported against (2026-08-11): "perfect"."""
    assert nc.CHAIN_REST_LIFT is True


def test_opts_out(monkeypatch):
    import importlib
    monkeypatch.setenv("CBBE2UBE_NO_CHAIN_REST_LIFT", "1")
    reloaded = importlib.reload(nc)
    try:
        assert reloaded.CHAIN_REST_LIFT is False
        ch = _chain(a_root_y=-4.0, a_mid_y=+1.0)
        assert reloaded._lift_chain_roots_off_body(ch, _FakeNif(ch)) == 0
    finally:
        monkeypatch.delenv("CBBE2UBE_NO_CHAIN_REST_LIFT", raising=False)
        importlib.reload(nc)


# ------------------------------------------------------- it lifts what it must

def test_a_chain_resting_inside_the_body_is_lifted_out():
    """Chain A's middle bone sits at y +1 -- 1u INSIDE. It must come out, and
    end up clear by the WANTED margin, not merely level with the skin: the
    margin is what covers the room the body still has to grow at runtime.
    Deliberately kept under CHAIN_LIFT_MAX so this tests the lift, not the cap
    (test_the_lift_is_capped covers that)."""
    ch = _chain(a_root_y=-4.0, a_mid_y=+1.0)
    assert _run(ch, _FakeNif(ch), amp_value=0.5) == 1
    want = nc.CHAIN_LIFT_BASE + 0.5
    assert want + 1.0 < nc.CHAIN_LIFT_MAX, "fixture must not reach the cap"
    g = _true_globals(ch)
    for b in ("Skirt 1_00", "Skirt 1_01", "Skirt 1_02"):
        assert -g[b][1] >= want - 1e-6, f"{b} still at clearance {-g[b][1]}"
    # and the worst bone lands ON the margin, not past it -- an overshoot is
    # paid for by the whole free-hanging chain (#standoff-counter-metric).
    assert np.isclose(-g["Skirt 1_01"][1], want)


def test_a_chain_that_already_clears_is_left_alone():
    ch = _chain(a_root_y=-8.0, a_mid_y=-8.0, b_root_y=-9.0)
    assert _run(ch, _FakeNif(ch), amp_value=0.5) == 0
    for b, (x, _p) in ch.items():
        assert x.tag == "src"


# --------------------------------------------- THE safety property: rigidity

def test_only_roots_are_rewritten():
    """A per-bone lift would change rest lengths and is how a chain explodes."""
    ch = _chain(a_root_y=-4.0, a_mid_y=+2.0)
    _run(ch, _FakeNif(ch))
    for b in ("Skirt 1_01", "Skirt 1_02"):
        assert ch[b][0].tag == "src", f"{b} was rewritten -- must be root-only"
    assert ch["Skirt 1_00"][0].tag == "copy"


def test_every_inter_bone_rest_length_within_the_chain_is_preserved_exactly():
    ch = _chain(a_root_y=-4.0, a_mid_y=+2.0)
    before = _true_globals(ch)
    _run(ch, _FakeNif(ch))
    after = _true_globals(ch)
    bones = ("Skirt 1_00", "Skirt 1_01", "Skirt 1_02")
    for i, x in enumerate(bones):
        for y in bones[i + 1:]:
            lb = np.linalg.norm(np.subtract(before[x], before[y]))
            la = np.linalg.norm(np.subtract(after[x], after[y]))
            assert abs(la - lb) < 1e-9, f"{x}-{y} changed by {la - lb}"


def test_the_source_transform_is_copied_not_mutated():
    """`node.transform` is a VIEW onto the source node -- writing through it
    edits the SOURCE nif, so the second weight file converted from the same
    load would inherit the lift twice."""
    ch = _chain(a_root_y=-4.0, a_mid_y=+2.0)
    originals = {b: x.translation for b, (x, _p) in ch.items()}
    keep = {b: x for b, (x, _p) in ch.items()}
    _run(ch, _FakeNif(ch))
    for b, x in keep.items():
        assert x.translation == originals[b], f"{b}: source object was mutated"


# ------------------------------------------------------------------ the caps

def test_the_lift_is_capped():
    ch = _chain(a_root_y=-4.0, a_mid_y=+40.0)
    before = np.array(ch["Skirt 1_00"][0].translation)
    assert _run(ch, _FakeNif(ch)) == 1
    d = np.array(ch["Skirt 1_00"][0].translation) - before
    assert np.isclose(np.linalg.norm(d), nc.CHAIN_LIFT_MAX), d


def test_a_morph_outlier_cannot_recruit_a_chain_that_already_clears():
    """REGRESSION. With the wanted clearance uncapped, a huge local morph
    amplitude (the belly reaches ~6.5u on the real body) drove a 2.0u push on
    FRONT skirt chains measured +3.63u clear of the skin. The wanted clearance
    is what bounds engagement, so it must be capped."""
    ch = _chain(a_root_y=-3.0, a_mid_y=-3.0, b_root_y=-3.5)
    assert nc.CHAIN_LIFT_WANT_MAX < 3.0, "the cap must sit below the test's clearance"
    assert _run(ch, _FakeNif(ch), amp_value=6.5) == 0
    for b, (x, _p) in ch.items():
        assert x.tag == "src"


def test_it_still_works_with_no_slider_data_at_all():
    """REGRESSION. With no OSD the morph term is a plain float, and the wanted
    clearance collapsed to a 0-d array -- so indexing it raised IndexError on
    the one path least likely to be exercised while developing. Falls back to
    the flat base margin."""
    ch = _chain(a_root_y=-4.0, a_mid_y=+1.0)
    assert _run(ch, _FakeNif(ch), osd=False) == 1
    g = _true_globals(ch)
    assert np.isclose(-g["Skirt 1_01"][1], nc.CHAIN_LIFT_BASE)


def test_a_negligible_lift_does_not_churn_the_file():
    ch = _chain(a_root_y=-0.75 - nc.CHAIN_LIFT_BASE,
                a_mid_y=-0.75 - nc.CHAIN_LIFT_BASE, b_root_y=-9.0)
    assert _run(ch, _FakeNif(ch), amp_value=0.75) == 0


# ------------------------------------------------- what is NOT a garment chain

def test_a_chain_carrying_no_cloth_is_never_lifted():
    """`NPC` was elected as a garment root on a real cuirass -- `custom_only`
    only means "not a skeleton bone". It skipped there on the arithmetic (its
    subtree sat 12.28u clear), not on a rule, and a root that qualifies
    translates whatever hangs under it. Note `_is_nif_root` does NOT match
    `NPC`, so a name predicate would not have caught it."""
    assert not nc._is_nif_root("NPC"), "predicate changed -- update this test"
    ch = {"NPC": (_Xf((0.0, +2.0, 5.0)), _ANCHOR),          # 2u INSIDE
          "Skirt 1_00": (_Xf((0.0, -8.0, 5.0)), _ANCHOR)}   # clear
    # only the skirt is skinned; `NPC` carries no cloth
    fake = _FakeNif(ch)
    fake.shapes[0].bone_names = ["Skirt 1_00"]
    fake.shapes[0].weights = {"Skirt 1_00": [(0, 1.0)]}
    assert _run(ch, fake) == 0
    assert ch["NPC"][0].tag == "src", "a chain with no cloth was translated"


def test_a_soft_body_bone_inside_a_chain_cannot_drive_the_lift():
    """Breast/butt/belly bones rest inside the body BY DESIGN. They cannot be
    ROOTS (`custom_only` drops them -- they are `_is_skeleton_bone`), but one
    sitting inside a garment chain's subtree would otherwise peg the lift and
    push the garment out to clear the body's own physics rig."""
    soft = next((n for n in ("NPC L Breast", "L Breast", "NPC L Butt",
                             "Breast_L01") if nc._is_soft_body_physics_bone(n)),
                None)
    assert soft, "no recognised soft-body bone name -- update this test"
    # the skirt clears comfortably; only the soft-body child is deep inside
    ch = {"Skirt 1_00": (_Xf((0.0, -8.0, 5.0)), _ANCHOR),
          soft: (_Xf((0.0, +10.0, 0.0)), "Skirt 1_00")}
    assert _run(ch, _FakeNif(ch)) == 0
    assert ch["Skirt 1_00"][0].tag == "src", "a soft-body bone drove a lift"


# ------------------------------------------------------------ the frame guard

def test_an_untrusted_frame_refuses_to_lift():
    """Composed chain globals can be PELVIS-RELATIVE when the armour's skeleton
    bones are (0,0,0) placeholders. Sampling a body there yields a confident
    wrong lift, which is worse than no lift."""
    ch = _chain(a_root_y=-4.0, a_mid_y=+2.0)
    assert _run(ch, _FakeNif(ch, honest_frame=False)) == 0
    for b, (x, _p) in ch.items():
        assert x.tag == "src"


def test_nothing_checkable_is_a_refusal_not_agreement():
    """No skinned chain bone anywhere in the file means the frame was never
    corroborated. `checked == 0` must not pass the guard."""
    ch = _chain(a_root_y=-4.0, a_mid_y=+2.0)
    assert _run(ch, _FakeNif(ch, skinned=False)) == 0


def test_the_frame_guard_accepts_an_agreeing_file():
    """The negative control for the two tests above: same fixture, honest skin,
    and the lift must happen -- otherwise they would pass for the wrong reason."""
    ch = _chain(a_root_y=-4.0, a_mid_y=+2.0)
    assert _run(ch, _FakeNif(ch, honest_frame=True)) == 1


# --------------------------------------------------------- globals composition

def test_globals_compose_through_the_chain_dict_not_the_source_tree():
    """The pelvis re-anchor and #chain-body-shift both mutate `chain` before
    this pass runs. Reading positions off the source node tree would discard
    their effect and measure a rig that is not the one being written."""
    ch = _chain(a_root_y=-4.0, a_mid_y=-1.0)
    xf, par = ch["Skirt 1_00"]
    moved = _Xf((xf.translation[0], xf.translation[1] - 3.0,
                 xf.translation[2]))
    ch["Skirt 1_00"] = (moved, par)
    g = nc._chain_rest_globals(ch, {_ANCHOR: _Node(_Xf((0.0, 0.0, 0.0)))})
    assert np.isclose(g["Skirt 1_00"][1], -7.0)
    assert np.isclose(g["Skirt 1_01"][1], -4.0), "the child must follow the root"


def test_a_cycle_cannot_hang_the_walk():
    ch = {"A": (_Xf(), "B"), "B": (_Xf(), "A")}
    nc._chain_rest_globals(ch, {})          # must return
