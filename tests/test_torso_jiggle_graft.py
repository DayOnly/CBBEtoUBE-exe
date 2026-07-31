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

"""#torso-jiggle-graft -- extend the body-jiggle graft to fitted TORSO garments.

In-game report that motivated it: a hide cuirass clips at chest and butt, "on the
side", and "especially under movement". Measured on the shipped mesh -- the body
under it (`BaseShape`) carries nine jiggle bones (`L/R Breast01-03`, `NPC L/R Butt`,
`NPC Belly`); the garment (`CuirassLight`) carries ZERO. The flesh travels, the
leather does not. No static clearance pass can reach that.

The garment failed FOUR gates in `_transfer_body_jiggle_to_fitted`, not one:

    collider gate   CuirassLight is a declared per-triangle collider
    chain_frac      0.373 > 0.05   (welded to its own simulated hide skirt)
    fit_frac        0.430 < 0.90   (that skirt hangs away from the body)
    leg_dom         0.023 <= 0.50  (it is a spine-dominant torso garment)

Three of those four were relaxed for the torso path. **The collider gate was NOT,
after an in-game test destroyed the attempt.** It was briefly relaxed behind a
standoff from the NIF's simulated cloth, reasoning that the cloth resting on the
collider was what needed protecting. In game the breasts tore off the body and
fell through the terrain: the collision partner that matters for a bust graft is
the BREAST, which collides against that very surface, so grafting breast motion
onto it closes a feedback loop. The graft region and the runaway collision are the
same place, which is why no standoff can exist. `#smp-collider-graft` holds
unconditionally.

These tests pin the gate structure and the weight cap. They are structural -- the
in-game judgement (does jiggle on a rigid leather cup look right?) is why the
whole feature ships OFF."""
import importlib

import pytest

import src.nif_convert as nc


def _reload(monkeypatch, value, legacy=None):
    if value is None:
        monkeypatch.delenv("CBBE2UBE_NO_TORSO_JIGGLE", raising=False)
    else:
        monkeypatch.setenv("CBBE2UBE_NO_TORSO_JIGGLE", value)
    if legacy is None:
        monkeypatch.delenv("CBBE2UBE_TORSO_JIGGLE", raising=False)
    else:
        monkeypatch.setenv("CBBE2UBE_TORSO_JIGGLE", legacy)
    return importlib.reload(nc)


@pytest.fixture(autouse=True)
def _clean_module():
    """Leave the module at its real default for every other test in the run."""
    yield
    import os
    os.environ.pop("CBBE2UBE_TORSO_JIGGLE", None)
    os.environ.pop("CBBE2UBE_NO_TORSO_JIGGLE", None)
    importlib.reload(nc)


# --- the flag itself ---------------------------------------------------------

def test_torso_jiggle_defaults_on(monkeypatch):
    """DEFAULT ON since 1.2. The original deferral (rubbery-cup aesthetics, judge
    in game) was answered in game: the motivating cuirass with the full graft via
    the bust collider split was confirmed GOOD. The 7c tear-off is structurally
    prevented (colliders are never grafted; the split provides a non-collider
    target). An env-gated default-OFF fix is a fix that never ships."""
    assert _reload(monkeypatch, None).TORSO_JIGGLE_TRANSFER is True


def test_torso_jiggle_opt_out_is_the_house_no_flag(monkeypatch):
    """NO_* opt-out, matching every other default-ON feature: the GUI registry
    can only emit '1'-or-unset, so a positive-name flag with a '0' opt-out made
    the checkbox a no-op in both directions (audit 2026-07-28)."""
    assert _reload(monkeypatch, "1").TORSO_JIGGLE_TRANSFER is False


def test_legacy_zero_spelling_still_honored(monkeypatch):
    """CBBE2UBE_TORSO_JIGGLE=0 was published with 1.2 -- it must keep working."""
    assert _reload(monkeypatch, None, legacy="0").TORSO_JIGGLE_TRANSFER is False
    assert _reload(monkeypatch, None, legacy="1").TORSO_JIGGLE_TRANSFER is True


def test_flag_accepts_the_usual_spellings(monkeypatch):
    for v in ("0", "false", "no", "OFF", ""):
        assert _reload(monkeypatch, v).TORSO_JIGGLE_TRANSFER is True, v
    for v in ("1", "true", "YES", "on"):
        assert _reload(monkeypatch, v).TORSO_JIGGLE_TRANSFER is False, v


def test_gui_registry_polarity_matches_the_flag():
    """The audit found the registry stale after the default flip: default=False,
    invert=False on a flag whose code default is ON -- so the checkbox could
    never turn the feature off, and apply_env's authoritative pop STRIPPED a
    user's system-level opt-out. Pin the corrected mapping end to end."""
    from src import gui_settings as gs
    s = next(x for x in gs.SETTINGS if x.key == "torso_jiggle")
    assert s.default is True
    assert s.invert is True
    assert s.env == "CBBE2UBE_NO_TORSO_JIGGLE"
    assert gs.env_string_for(s, True) is None    # checked -> unset -> feature ON
    assert gs.env_string_for(s, False) == "1"    # unchecked -> NO_=1 -> feature OFF


# --- the collider rule -------------------------------------------------------

def test_a_collider_is_never_grafted_even_with_the_flag_on():
    """THE regression guard for this feature.

    Relaxing this is what made the breasts tear off the body and fall through the
    terrain. A per-triangle collider covering the bust IS what the breast physics
    collides against; grafting breast motion onto it closes the loop. There is no
    standoff that helps, because the grafted region and the runaway collision are
    the same place. If someone re-introduces a conditional here, this fails."""
    import inspect
    src = inspect.getsource(nc._transfer_body_jiggle_to_fitted)
    i = src.index("if s.name in collider_names:")
    stmt = src[i:src.index("continue", i)]
    assert "TORSO_JIGGLE" not in stmt and "torso_mode" not in stmt, (
        "the collider skip must be UNCONDITIONAL -- not gated on the torso flag")
    # and no standoff machinery may come back
    assert "_TORSO_JIGGLE_CHAIN_CLEAR" not in src
    assert "d_chain" not in src


def test_the_standoff_machinery_is_gone_from_the_module():
    """Dead safety machinery reads as a supported path to the next person."""
    assert not hasattr(nc, "_TORSO_JIGGLE_CHAIN_CLEAR")
    assert not hasattr(nc, "_nif_chain_vert_cloud")





def test_simulated_cloth_is_never_grafted_regardless_of_mode():
    """Per-vertex soft-bodies and layered cloth stay excluded unconditionally. The
    flag relaxes chain_frac, whole-shape fit and leg dominance -- never a rule about
    simulated cloth."""
    import inspect
    src = inspect.getsource(nc._transfer_body_jiggle_to_fitted)
    i = src.index("if (s.name in softbody_names")
    skip = src[i:src.index("continue", i)]
    # `_skip_keys` IS the name list, narrowed per piece by #drape-xml-gate; the
    # soft-body / layered-cloth terms beside it must stay unconditional.
    assert "layered_cloth_names" in skip and "_skip_keys" in skip
    assert "TORSO_JIGGLE" not in skip, (
        "the soft-body / layered-cloth skip must not be conditional on the flag")
    assert "DRAPE_SKIP_XML_GATED" not in skip, (
        "nor on the XML gate -- #drape-xml-gate narrows the NAME list only and must "
        "never reach the structural soft-body / layered-cloth exclusions")


def test_chain_verts_are_skipped_per_vertex():
    """A vert a custom bone drives is simulated: it has no rest position to follow
    the body from, and rewriting it is a partition hazard. True in BOTH modes."""
    import inspect
    src = inspect.getsource(nc._transfer_body_jiggle_to_fitted)
    assert "if is_chain[i]:" in src
    assert "continue  # custom-chain vert" in src


# --- the torso acceptance gate ----------------------------------------------

def test_torso_mode_requires_all_three_conditions():
    """flag AND rigid-torso-armour AND a fit floor over the RIGID verts. Dropping
    any one of them admits a cape or a scarf."""
    import inspect
    src = inspect.getsource(nc._transfer_body_jiggle_to_fitted)
    i = src.index("torso_mode = bool(")
    gate = src[i:i + 320]
    assert "TORSO_JIGGLE_TRANSFER" in gate
    assert "_shape_is_rigid_torso_armor(s)" in gate
    assert "_TORSO_JIGGLE_FIT_FRAC" in gate
    assert "d[rigid_i]" in gate, (
        "the fit must be measured over the shape's rigid verts -- a garment welded "
        "to its own hanging skirt scores 0.43 over all of them and 0.67 over these")


def test_leg_path_gates_are_untouched_when_torso_mode_is_off():
    """The default path must keep chain_frac, whole-shape fit and leg dominance.
    This is the regression that would silently widen a shipped default."""
    import inspect
    src = inspect.getsource(nc._transfer_body_jiggle_to_fitted)
    i = src.index("if not torso_mode:")
    body = src[i:i + 800]
    assert "chain_frac > _CONFORM_CHAIN_MAX" in body
    assert "_CONFORM_FIT_FRAC" in body
    assert "leg_dom <= 0.5" in body


def test_torso_fit_floor_separates_fitted_plate_from_hanging_cloth():
    """Calibrated on 134 torso shapes in the shipped output: capes, scarves,
    pauldrons and scabbards land at 0.00-0.03; corsets, bras and chest plates at
    1.00; the motivating cuirass at 0.67. The floor must sit in that gap."""
    assert 0.05 < nc._TORSO_JIGGLE_FIT_FRAC < 0.66, (
        "below 0.05 admits capes and scabbards; above 0.66 rejects the very "
        "garment the feature was built for")




# --- the weight invariant ----------------------------------------------------

def test_graft_rows_are_capped_before_the_bones_are_added():
    """#weight-write-invariant + #zeroweight-bone-desync, in that order.

    The graft can push a row to 5 influences; the save keeps the largest 4 and does
    NOT renormalise, so the row ships light -- measured 26 verts down to 0.9655 on
    the hide cuirass before the cap, 1 vert at 1.0019 after. And the cap MUST run
    before `addable`, or a graft the cap discards still gets add_bone'd and ships a
    zero-weight bone: exactly the ordering P6 had to learn on the leg pass."""
    import inspect
    src = inspect.getsource(nc._transfer_body_jiggle_to_fitted)
    cap = src.index("_cap_and_renormalise_rows(")
    add = src.index("addable = [(jb, stb)")
    loop = src.index("grafted_rows.append(i)")
    assert loop < cap < add, (
        "cap must run after the graft loop and BEFORE the bones are chosen")
    assert "rows=grafted_rows" in src[cap:cap + 200], (
        "the cap must be restricted to the rows this pass touched, not all verts")


def test_write_never_empties_an_existing_bone():
    """A bone left in the shape's list with no weight is dropped from the
    regenerated partition palette, so a per-vert index runs past it on equip. The
    cap can now zero an existing bone on every row it touched, so the write needs
    the same guard the leg pass carries."""
    import inspect
    src = inspect.getsource(nc._transfer_body_jiggle_to_fitted)
    i = src.index("for bn in sorted(touched):")
    body = src[i:src.index("s.setShapeWeights(bn, pairs)", i)]
    assert "if not pairs and bn in existing:" in body
    assert "continue" in body, (
        "the guard must SKIP the write, leaving the bone as it was -- not write "
        "an empty weight list")
    assert "#zeroweight-bone-desync" in body


def test_weight_write_order_is_deterministic():
    """#deterministic-weight-write. `touched` is a SET of bone names, so
    iterating it directly made the WRITE ORDER depend on PYTHONHASHSEED -- and
    write order decides which influence survives when a row still overflows the
    4-slot limit at save time.

    Measured before the fix on a vanilla light cuirass: ONE vertex kept
    `HideSkirt 6_01` at seed 0 and `HideSkirt 5_01` at seed 2 (near-tied at
    0.037872 / 0.038025), moving that bone's weight total by 0.038. That made
    the golden harness flag the piece on roughly half of all runs, and a gate
    that cries wolf gets ignored -- so a 0.2% wobble cost far more than its
    size. Bisected to this pass with CBBE2UBE_NO_JIGGLE_TRANSFER.
    """
    import inspect
    src = inspect.getsource(nc._transfer_body_jiggle_to_fitted)
    assert "for bn in sorted(touched):" in src
    assert "for bn in touched:" not in src, (
        "iterating the bone-name set directly reintroduces hash-seed-dependent "
        "write order")


def test_new_bone_emit_gate_still_present():
    """The sibling gate that stops an ADDED bone shipping with no written weight.
    It and the cap solve different halves of the same failure."""
    import inspect
    src = inspect.getsource(nc._transfer_body_jiggle_to_fitted)
    assert "any(vw[i].get(jb, 0.0) > 1e-4 for i in range(n))" in src
