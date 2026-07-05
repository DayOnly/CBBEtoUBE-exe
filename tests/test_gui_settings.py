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
    assert "Fit and conform" in groups and "Glow and effect-shader" in groups
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
