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

"""Guards for the GUI settings registry -- the env-var polarity mapping above
all (a wrong invert/default silently flips a conversion feature)."""
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src import gui_settings as gs


def test_keys_unique():
    keys = [s.key for s in gs.SETTINGS]
    assert len(keys) == len(set(keys)), "duplicate setting key"


def test_defaults_at_rest_emit_no_managed_env():
    # Every setting at its default -> NO registry-managed CBBE2UBE_* var is set
    # (the code's own defaults apply). Nothing leaks.
    env = gs.apply_env(gs.defaults(), base_env={})
    managed = {s.env for s in gs.SETTINGS if s.env}
    assert not (managed & set(env)), f"default run set: {managed & set(env)}"


def test_default_on_feature_disables_via_no_flag():
    # conform: default ON, mapped to NO_CONFORM (invert). Turning it OFF sets =1.
    d = gs.defaults()
    assert d["conform_to_body"] is True
    env = gs.apply_env({**d, "conform_to_body": False}, base_env={})
    assert env["CBBE2UBE_NO_CONFORM"] == "1"
    # left ON -> var absent
    env2 = gs.apply_env({**d, "conform_to_body": True}, base_env={})
    assert "CBBE2UBE_NO_CONFORM" not in env2


def test_glow_source_skin_maps_to_effect_reskin_inverted():
    # The confusing one: feature ON (keep source skin) = default; OFF sets
    # CBBE2UBE_EFFECT_RESKIN=1 (revert to reskin).
    d = gs.defaults()
    assert d["glow_source_skin"] is True
    assert gs.apply_env({**d, "glow_source_skin": False}, {})["CBBE2UBE_EFFECT_RESKIN"] == "1"
    assert "CBBE2UBE_EFFECT_RESKIN" not in gs.apply_env(d, {})


def test_vanilla_sweep_default_on_disables_via_no_flag():
    # vanilla_sweep: default ON, mapped to NO_VANILLA_SWEEP (invert). OFF sets
    # =1 (skip the Data-dir source); ON leaves the var unset (sweep runs).
    d = gs.defaults()
    assert d["vanilla_sweep"] is True
    env = gs.apply_env({**d, "vanilla_sweep": False}, base_env={})
    assert env["CBBE2UBE_NO_VANILLA_SWEEP"] == "1"
    assert "CBBE2UBE_NO_VANILLA_SWEEP" not in gs.apply_env(d, {})


def test_default_off_feature_enables_via_positive_flag():
    # chain_to_softbody: default OFF, positive flag. Turning ON sets =1.
    d = gs.defaults()
    assert d["chain_to_softbody"] is False
    assert gs.apply_env({**d, "chain_to_softbody": True}, {})["CBBE2UBE_CHAIN_TO_SOFTBODY"] == "1"
    assert "CBBE2UBE_CHAIN_TO_SOFTBODY" not in gs.apply_env(d, {})


def test_managed_var_is_authoritative_over_stale_parent_env():
    # A stale parent value for a registry var is REMOVED when the UI is at
    # default, so the user's UI is the source of truth.
    stale = {"CBBE2UBE_NO_CONFORM": "1", "UNRELATED": "keep"}
    env = gs.apply_env(gs.defaults(), base_env=stale)
    assert "CBBE2UBE_NO_CONFORM" not in env      # popped -> default restored
    assert env["UNRELATED"] == "keep"            # non-registry var untouched


def test_numeric_override_only_when_changed():
    d = gs.defaults()
    assert "CBBE2UBE_SEAM_WELD_TOL" not in gs.apply_env(d, {})          # at default
    env = gs.apply_env({**d, "seam_weld_tol": 0.12}, {})
    assert env["CBBE2UBE_SEAM_WELD_TOL"] == "0.12"


def test_blank_path_does_not_set_env():
    assert "CBBE2UBE_UBE_BODY" not in gs.apply_env(gs.defaults(), {})
    env = gs.apply_env({**gs.defaults(), "ube_body": r"D:\body_1.nif"}, {})
    assert env["CBBE2UBE_UBE_BODY"] == r"D:\body_1.nif"


def test_tab_and_group_structure():
    assert "Armor" in gs.tabs_present()
    assert "Tuning" not in gs.tabs_present()   # folded into Armor
    groups = gs.groups_in_tab("Armor")
    assert "Fit and clearance" in groups and "Glow and effect shaders" in groups
    assert all(s.tab == "Armor" for s in gs.settings_in("Armor", "Seams"))
    # a numeric "tuning" knob now nests under its feature's Armor group
    seam_keys = [s.key for s in gs.settings_in("Armor", "Seams")]
    assert "seam_weld" in seam_keys and "seam_weld_tol" in seam_keys


def test_persistence_round_trip_only_non_default(tmp_path):
    p = tmp_path / "settings.json"
    vals = gs.defaults()
    vals["conform_to_body"] = False       # non-default bool
    vals["seam_weld_tol"] = 0.12          # non-default float
    assert gs.save_values(vals, path=p)
    import json
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    # VALUES are still only the non-defaults -- that contract is unchanged. The one
    # extra entry is `_known_settings`: the record of which options THIS build
    # offered, which is what lets a later build NAME a newly-added option instead of
    # mistaking it for one deliberately left off (see test_unseen_settings.py).
    baseline = on_disk.pop(gs.KNOWN_KEYS_FIELD)
    assert set(baseline) == {s.key for s in gs.SETTINGS}
    assert on_disk == {"conform_to_body": False, "seam_weld_tol": 0.12}
    loaded = gs.load_values(path=p)
    assert loaded["conform_to_body"] is False
    assert loaded["seam_weld_tol"] == 0.12
    assert loaded["glow_source_skin"] is True     # untouched -> default


def test_load_missing_file_is_pure_defaults(tmp_path):
    assert gs.load_values(path=tmp_path / "nope.json") == gs.defaults()


def test_load_ignores_unknown_keys_and_coerces(tmp_path):
    p = tmp_path / "s.json"
    p.write_text('{"conform_to_body": 0, "seam_weld_tol": "0.2", "bogus": 1}',
                 encoding="utf-8")
    v = gs.load_values(path=p)
    assert v["conform_to_body"] is False          # 0 -> bool
    assert v["seam_weld_tol"] == 0.2              # "0.2" -> float
    assert "bogus" not in v


def test_saved_config_applies_through_env(tmp_path):
    # End-to-end: save a config, reload it, and confirm the env reflects it.
    p = tmp_path / "s.json"
    gs.save_values({**gs.defaults(), "glow_source_skin": False}, path=p)
    env = gs.apply_env(gs.load_values(path=p), base_env={})
    assert env["CBBE2UBE_EFFECT_RESKIN"] == "1"


# Tabs whose contents gui.py GENERATES from the registry (_build_settings_tab).
# A setting on any other tab must be rendered by hand, or it is invisible.
_GENERATED_TABS = {"Armor", "Paths", "Diagnostics"}


def _gui_source() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent
            / "src" / "gui.py").read_text(encoding="utf-8", errors="replace")


def test_no_orphaned_settings():
    """Every setting must be reachable in the UI.

    A setting on a tab nobody builds renders nowhere, so it can never be
    switched on, never validated in game, and never finished -- the deadlock
    docs/PIPELINE.md rule 1 exists to prevent. Settings outside the generated
    tabs (vanilla_sweep on Run, theme on Appearance) are hand-rendered, so
    require the key to appear in gui.py.
    """
    src = _gui_source()
    orphans = [s.key for s in gs.SETTINGS
               if s.tab not in _GENERATED_TABS and f'"{s.key}"' not in src]
    assert not orphans, f"settings rendered by nothing: {orphans}"


def test_vanilla_sweep_lives_on_the_run_tab():
    """It adds a SOURCE to the run rather than changing how a garment is
    fitted, so it belongs beside the mod selection it extends."""
    s = gs.by_key()["vanilla_sweep"]
    assert (s.tab, s.group) == ("Run", "Convert armor")
    assert s.default is True
    # moving it must not change what the converter sees
    assert "CBBE2UBE_NO_VANILLA_SWEEP" not in gs.apply_env(gs.defaults(), {})
    off = gs.apply_env({**gs.defaults(), "vanilla_sweep": False}, {})
    assert off["CBBE2UBE_NO_VANILLA_SWEEP"] == "1"
    # and it must be off the Armor tab, taking the one-item group with it
    assert "Coverage" not in gs.groups_in_tab("Armor")
    assert all(s.key != "vanilla_sweep" for s in gs.settings_in("Armor", "Coverage"))


def test_every_setting_is_in_exactly_one_layout_group():
    """LAYOUT drives display order. A key it omits still renders (at the end of
    its group), but a key in the WRONG group would render twice or not at all.
    """
    for tab, groups in gs.LAYOUT.items():
        listed = [k for _g, keys in groups for k in keys]
        assert len(listed) == len(set(listed)), "a key is listed twice in LAYOUT"
        by_group = {g: set(keys) for g, keys in groups}
        for s in gs.SETTINGS:
            if s.tab != tab:
                continue
            for g, keys in by_group.items():
                if s.key in keys:
                    assert s.group == g, (
                        f"{s.key} is in LAYOUT group {g!r} but Setting.group "
                        f"says {s.group!r}")
        # every LAYOUT key must be a real setting on that tab
        real = {s.key for s in gs.SETTINGS if s.tab == tab}
        assert not (set(listed) - real), f"LAYOUT names non-existent: {set(listed) - real}"


def test_layout_nests_each_knob_under_the_toggle_it_tunes():
    """The bug this fixes: chest_follow_unknown rendered four rows from
    chest_follow, so it read as an independent option."""
    keys = [s.key for s in gs.settings_in("Armor", "Body follow and morphs")]
    assert keys.index("chest_follow_unknown") == keys.index("chest_follow") + 1
    keys = [s.key for s in gs.settings_in("Armor", "Seams")]
    assert keys.index("seam_weld_tol") == keys.index("seam_weld") + 1


def test_every_numeric_knob_is_advanced():
    """`advanced` hides tuning knobs. One knob left un-marked would be the lone
    spinbox still shown, which reads as a deliberate promotion."""
    for s in gs.SETTINGS:
        if s.kind in ("float", "int"):
            assert s.advanced, f"{s.key} is a numeric knob but not advanced"


def test_hint_is_one_short_line_and_never_replaces_the_tooltip():
    for s in gs.SETTINGS:
        h = gs.hint_for(s)
        assert len(h) <= gs.HINT_MAX + 1, f"{s.key} hint too long: {len(h)}"
        assert "\n" not in h, f"{s.key} hint is multi-line"
        if s.tooltip:
            assert h, f"{s.key} has a tooltip but no hint"
    # the full text must still be there -- the point is to MOVE it, not trim it
    assert len(gs.by_key()["rigid_majority_softbody"].tooltip) > 300


def test_hint_falls_back_to_first_sentence():
    s = gs.Setting("x", "X", "Armor", "G", tooltip="First one. Second one here.")
    assert gs.hint_for(s) == "First one."
    s2 = gs.Setting("y", "Y", "Armor", "G", hint="explicit", tooltip="Long. More.")
    assert gs.hint_for(s2) == "explicit"
    long = "w" * (gs.HINT_MAX + 40) + ". tail."
    assert gs.hint_for(gs.Setting("z", "Z", "Armor", "G", tooltip=long)).endswith("…")


def test_unseen_settings_ignores_cosmetic_options(tmp_path):
    """The warning exists for options that change a CONVERSION. A setting with
    no env var cannot, so flagging it would be a false alarm -- and a warning
    that cries wolf about window size is how a real one gets skimmed past."""
    p = tmp_path / "s.json"
    gs.save_values(gs.defaults(), path=p)
    import json
    raw = json.loads(p.read_text(encoding="utf-8"))
    cosmetic = [s.key for s in gs.SETTINGS if not s.env]
    behavioural = [s.key for s in gs.SETTINGS if s.env]
    assert cosmetic and behavioural, "need both kinds for this test to mean anything"
    # forget one of each
    raw[gs.KNOWN_KEYS_FIELD] = [k for k in raw[gs.KNOWN_KEYS_FIELD]
                                if k not in (cosmetic[0], behavioural[0])]
    p.write_text(json.dumps(raw), encoding="utf-8")
    _baseline, new = gs.unseen_settings(path=p)
    keys = {s.key for s in new}
    assert behavioural[0] in keys, "a real option must still be named"
    assert cosmetic[0] not in keys, "a cosmetic option must not raise the alarm"


# Flags promoted to DEFAULT ON in 1.2, after a full-pack conversion and in-game
# use. Each needs BOTH halves flipped: the registry default AND the source
# constant's polarity. Flipping only the registry would show ON in the GUI while
# the converter still ran with the feature off -- the exact silent mismatch this
# module's docstring warns about.
PROMOTED_IN_1_2 = {
    "chest_follow":   "CBBE2UBE_NO_CHEST_FOLLOW",
    "drape_xml_gate": "CBBE2UBE_NO_DRAPE_XML_GATE",
    "smp_antipoke":   "CBBE2UBE_NO_SMP_ANTIPOKE",
    "source_follow":  "CBBE2UBE_NO_SOURCE_FOLLOW",
}


def test_promoted_flags_are_default_on_with_a_no_kill_switch():
    reg = gs.by_key()
    for key, env in PROMOTED_IN_1_2.items():
        s = reg[key]
        assert s.default is True, f"{key} should ship ON"
        assert s.env == env, f"{key} must use the NO_ form, got {s.env}"
        assert s.invert is True, f"{key} env must DISABLE, not enable"


def test_promoted_flags_emit_nothing_at_default_and_a_kill_var_when_off():
    d = gs.defaults()
    env_on = gs.apply_env(d, {})
    for key, var in PROMOTED_IN_1_2.items():
        assert var not in env_on, f"{var} leaked while {key} is at its default"
        off = gs.apply_env({**d, key: False}, {})
        assert off[var] == "1", f"unticking {key} must set {var}=1"


def test_the_source_constants_actually_default_on():
    """The half a registry-only edit would miss. Reads the real module."""
    import importlib
    import src.nif_convert as nc
    importlib.reload(nc)
    for name in ("CHEST_FOLLOW_RATIO", "DRAPE_SKIP_XML_GATED",
                 "SOURCE_FOLLOW_CEILING", "SMP_COLLISION_ONLY_ANTIPOKE"):
        assert getattr(nc, name) is True, f"{name} is not ON by default"


def test_rigid_majority_softbody_still_ships_off():
    """It changes physics and has no in-game verdict. PIPELINE.md rule 8: ship a
    flag off and get a verdict before defaulting it on."""
    s = gs.by_key()["rigid_majority_softbody"]
    assert s.default is False
    assert s.invert is False and s.env == "CBBE2UBE_RIGID_MAJORITY_SOFTBODY"


def test_every_gui_env_is_read_by_src():
    """A GUI row whose env var nothing reads is a toggle that does nothing.

    `warp_delta_outlier` shipped that way: the row wrote
    CBBE2UBE_WARP_DELTA_OUTLIER while the code read
    CBBE2UBE_NO_WARP_DELTA_OUTLIER, so the checkbox was inert in BOTH
    directions and displayed a default-ON feature as off. The registry's own
    docstring promises this mapping is verified against the source; this is
    that verification, as a ratchet rather than a promise.
    """
    import re
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "src"
    raw, helper = set(), set()
    for f in src.glob("*.py"):
        text = f.read_text(encoding="utf-8", errors="replace")
        # Raw reads AND the _flag()/_knob() helpers the 2026-08-18 idiom
        # collapse routed most reads through.
        raw |= set(re.findall(
            r'os\.environ\.get\(\s*["\'](CBBE2UBE_[A-Z0-9_]+)["\']', text))
        helper |= set(re.findall(
            r'_(?:flag|knob)\(\s*["\'](CBBE2UBE_[A-Z0-9_]+)["\']', text))
    read = raw | helper
    # PER-IDIOM floors: an aggregate floor cannot fire when ONE idiom goes
    # blind, because the other alone clears it. Today 11 GUI-exposed envs are
    # raw-only, so raw-blindness happens to fail this test through `dead` --
    # but as the idiom collapse continues that accident disappears and the
    # guard would go silent. Measured 2026-08-18: helper 289, raw 34.
    assert len(helper) >= 250, (
        f"only {len(helper)} _flag/_knob env reads found across src/ -- that "
        f"idiom's regex went blind; widen it")
    assert len(raw) >= 25, (
        f"only {len(raw)} raw os.environ env reads found across src/ -- that "
        f"idiom's regex went blind (if the last raw reads genuinely migrated, "
        f"delete the raw regex deliberately rather than lowering this)")
    exposed = {s.env for s in gs.SETTINGS if s.env}
    dead = sorted(e for e in exposed if e not in read)
    assert not dead, f"GUI rows whose env nothing in src/ reads: {dead}"

# A "NO_* env <=> invert=True" ratchet was written here and REMOVED the same
# day: it fails on three rows that are all correct, because this registry
# expresses a negation three different ways -- a NO_ var
# (`CBBE2UBE_NO_SOFTBODY_SCALES`), a KEEP_ var
# (`CBBE2UBE_KEEP_BOOT_THIGH_SCALE`), and a negatively-phrased LABEL ("Disable
# soft-body scale bones", whose feature really is the disabling). A test that
# fails on correct design only teaches people to suppress it. The property
# worth ratcheting is the one above: an env var nothing reads is always a bug.
