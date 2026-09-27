"""The body lookups are cached per input, not per process (#body-cache-by-inputs).

Found 2026-09-26 by an order-dependent suite failure: the discovery cache was
keyed on the weight alone ('ube_1'), so the first answer in a process was its
answer for good. The settings window is such a process: its launch-time Check
setup resolved the UBE body, and a re-check after a Paths-tab pick (which
reaches the window as CBBE2UBE_UBE_BODY through SettingsOverlay) still reported
the launch body -- or 'missing' when there had been none -- and the Select
list's UBE-native scan measured against it too. Each entry is now keyed on the
override variables its finder reads, the variables that choose the MO2
instance, mods folder and game Data, and the zeroed switch: a changed input
resolves again, an unchanged one still hits (the scans walk the mods tree).
CBBE2UBE_NO_BODY_CACHE_BY_INPUTS=1 restores the weight-only keys.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import auto_convert as ac  # noqa: E402
from src import gui_settings as gs  # noqa: E402
from src import nif_convert_bodyrefs as br  # noqa: E402
from src import preflight as pf  # noqa: E402

_BODY_VARS = ("CBBE2UBE_CBBE_BODY_0", "CBBE2UBE_CBBE_BODY_1", "CBBE2UBE_UBE_BODY",
              "CBBE2UBE_UBE_BODY_0", "CBBE2UBE_UBE_BODY_1", "CBBE2UBE_UBE_TEMPLATE",
              "CBBE2UBE_UBE_OSD", "CBBE2UBE_NO_BODY_CACHE_BY_INPUTS")


@pytest.fixture(autouse=True)
def calls(monkeypatch, tmp_path):
    """No real scan: every walk the finders can make is counted and answers
    from `tmp_path`. Returns the call log."""
    br._BODY_DISCOVERY_CACHE.clear()        # mutate, never rebind: shared object
    ac._BODY_TREE_CACHE.clear()
    for v in _BODY_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("CBBE2UBE_MODS_ROOT", str(tmp_path))
    monkeypatch.delenv("CBBE2UBE_MO2_INI", raising=False)
    monkeypatch.delenv("CBBE2UBE_GAME_DATA", raising=False)
    monkeypatch.setattr(br, "ZEROED_BODY_REFS", True)
    log = []

    def zeroed(kind, weight):
        if not br.ZEROED_BODY_REFS:
            return None
        log.append(("zeroed", kind, weight))
        return tmp_path / f"zeroed_{kind}{weight}.nif"

    def glob_first(pattern, name_substrs=None):
        log.append(("glob", pattern))
        return tmp_path / "found.nif"

    monkeypatch.setattr(br, "_zeroed_ref", zeroed)
    monkeypatch.setattr(br, "_glob_first_in_mods", glob_first)
    yield log
    br._BODY_DISCOVERY_CACHE.clear()
    ac._BODY_TREE_CACHE.clear()


def _file(tmp_path, name):
    p = tmp_path / name
    p.write_bytes(b"nif")
    return p


# --- the product path: the window's Check setup --------------------------------

def test_check_setup_follows_a_ube_body_picked_after_the_launch_check(
        monkeypatch, tmp_path):
    """The window's launch check, then a pick on the Paths tab, then Check
    setup again -- one process, as the window is."""
    monkeypatch.setattr(ac, "_find_ube_body_ref", lambda: None)
    monkeypatch.setattr(br, "_zeroed_ref", lambda kind, weight: None)
    monkeypatch.setattr(br, "_glob_first_in_mods", lambda *a, **k: None)
    monkeypatch.setattr(br, "_iter_femalebody_nifs", lambda w: iter(()))
    with gs.SettingsOverlay(gs.defaults()):
        assert pf._probe_ube_body() is None, "nothing to find before the pick"
    picked = _file(tmp_path, "picked_femalebody_1.nif")
    with gs.SettingsOverlay({**gs.defaults(), "ube_body": str(picked)}):
        assert pf._probe_ube_body() == picked


def test_check_setup_drops_a_pick_that_was_cleared(tmp_path, calls):
    picked = _file(tmp_path, "picked_femalebody_1.nif")
    with gs.SettingsOverlay({**gs.defaults(), "ube_body": str(picked)}):
        assert pf._probe_ube_body() == picked
    with gs.SettingsOverlay(gs.defaults()):
        assert pf._probe_ube_body() == tmp_path / "zeroed_ube_1.nif"


# --- each finder follows its own inputs --------------------------------------------

@pytest.mark.parametrize("var", ["CBBE2UBE_UBE_BODY_1", "CBBE2UBE_UBE_BODY"])
def test_the_ube_body_follows_a_changed_pick(monkeypatch, tmp_path, var):
    a = _file(tmp_path, "a_femalebody_1.nif")
    b = _file(tmp_path, "b_femalebody_1.nif")
    monkeypatch.setenv(var, str(a))
    assert br._find_ube_femalebody("_1") == a
    monkeypatch.setenv(var, str(b))
    assert br._find_ube_femalebody("_1") == b
    monkeypatch.delenv(var)
    assert br._find_ube_femalebody("_1") == tmp_path / "zeroed_ube_1.nif"


def test_the_cbbe_body_follows_a_changed_pick(monkeypatch, tmp_path):
    a = _file(tmp_path, "a_femalebody_0.nif")
    b = _file(tmp_path, "b_femalebody_0.nif")
    monkeypatch.setenv("CBBE2UBE_CBBE_BODY_0", str(a))
    assert br._find_cbbe_base_body("_0") == a
    monkeypatch.setenv("CBBE2UBE_CBBE_BODY_0", str(b))
    assert br._find_cbbe_base_body("_0") == b
    monkeypatch.delenv("CBBE2UBE_CBBE_BODY_0")
    assert br._find_cbbe_base_body("_0") == tmp_path / "zeroed_cbbe_0.nif"


@pytest.mark.parametrize("var, finder", [
    ("CBBE2UBE_UBE_TEMPLATE", br._find_ube_template_body),
    ("CBBE2UBE_UBE_OSD", br._find_ube_body_osd)])
def test_the_shapedata_assets_follow_a_changed_override(monkeypatch, tmp_path, var, finder):
    assert finder() == tmp_path / "found.nif"
    mine = _file(tmp_path, "mine.bin")
    monkeypatch.setenv(var, str(mine))
    assert finder() == mine


def test_a_different_mods_folder_is_scanned_again(monkeypatch, tmp_path, calls):
    """The user's BodySlide build is found by walking the mods folder; another
    instance is another answer."""
    br._find_user_preset_body("_1")
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("CBBE2UBE_MODS_ROOT", str(other))
    br._find_user_preset_body("_1")
    assert sum(c[0] == "glob" for c in calls) == 2, calls


def test_the_zeroed_switch_is_part_of_the_answer(monkeypatch, tmp_path, calls):
    br._find_cbbe_base_body("_1")
    monkeypatch.setattr(br, "ZEROED_BODY_REFS", False)
    monkeypatch.setattr(br, "_iter_femalebody_nifs", lambda w: iter(()))
    assert br._find_cbbe_base_body("_1") is None, "the zeroed answer outlived its switch"


# --- the speed win stays ---------------------------------------------------------

def test_an_unchanged_pick_resolves_once(monkeypatch, tmp_path, calls):
    for _ in range(5):
        for w in ("_0", "_1"):
            br._find_cbbe_base_body(w)
            br._find_ube_femalebody(w)
            br._find_user_preset_body(w)
        br._find_ube_template_body()
        br._find_ube_body_osd()
    zeroed = [c for c in calls if c[0] == "zeroed"]
    assert sorted(zeroed) == [("zeroed", "cbbe", "_0"), ("zeroed", "cbbe", "_1"),
                              ("zeroed", "ube", "_0"), ("zeroed", "ube", "_1")], calls
    assert sum(c[0] == "glob" for c in calls) == 4, calls   # 2 presets + 2 assets


def test_a_pick_set_back_hits_its_first_answer(monkeypatch, tmp_path, calls):
    a = _file(tmp_path, "a_femalebody_1.nif")
    monkeypatch.setenv("CBBE2UBE_UBE_BODY_1", str(a))
    br._find_ube_femalebody("_1")
    monkeypatch.delenv("CBBE2UBE_UBE_BODY_1")
    br._find_ube_femalebody("_1")
    monkeypatch.setenv("CBBE2UBE_UBE_BODY_1", str(a))
    br._find_ube_femalebody("_1")
    monkeypatch.delenv("CBBE2UBE_UBE_BODY_1")
    br._find_ube_femalebody("_1")
    assert [c for c in calls if c[0] == "zeroed"] == [("zeroed", "ube", "_1")], calls


# --- the window's UBE-native scan ----------------------------------------------------

def test_the_ube_native_scan_measures_against_the_current_bodies(monkeypatch, tmp_path):
    builds = []
    a = _file(tmp_path, "a_femalebody_1.nif")
    b = _file(tmp_path, "b_femalebody_1.nif")

    def verts(p):
        builds.append(Path(p).name)
        return None, np.zeros((4, 3)) + (1.0 if Path(p) == a else 2.0)

    monkeypatch.setattr(ac.nif_convert, "_cached_ube_body_verts", verts)
    monkeypatch.setattr(ac, "_largest_shape_verts", lambda p: np.zeros((4, 3)))
    monkeypatch.setenv("CBBE2UBE_UBE_BODY_1", str(a))
    first = ac._body_trees()[0]
    assert ac._body_trees()[0] is first, "an unchanged pick rebuilt the trees"
    monkeypatch.setenv("CBBE2UBE_UBE_BODY_1", str(b))
    second = ac._body_trees()[0]
    assert builds == [a.name, b.name], builds
    assert float(second.data[0][0]) == 2.0


# --- the off-switch --------------------------------------------------------------------

def test_switched_off_the_first_answer_is_kept(monkeypatch, tmp_path):
    monkeypatch.setenv("CBBE2UBE_NO_BODY_CACHE_BY_INPUTS", "1")
    a = _file(tmp_path, "a_femalebody_1.nif")
    b = _file(tmp_path, "b_femalebody_1.nif")
    monkeypatch.setenv("CBBE2UBE_UBE_BODY_1", str(a))
    assert br._find_ube_femalebody("_1") == a
    monkeypatch.setenv("CBBE2UBE_UBE_BODY_1", str(b))
    assert br._find_ube_femalebody("_1") == a
    assert "ube_1" in br._BODY_DISCOVERY_CACHE, "the old weight-only key"


def test_switched_off_the_scan_keeps_its_first_trees(monkeypatch, tmp_path):
    monkeypatch.setenv("CBBE2UBE_NO_BODY_CACHE_BY_INPUTS", "1")
    builds = []

    def verts(p):
        builds.append(p)
        return None, np.zeros((4, 3))

    monkeypatch.setattr(ac.nif_convert, "_cached_ube_body_verts", verts)
    monkeypatch.setattr(ac, "_largest_shape_verts", lambda p: np.zeros((4, 3)))
    monkeypatch.setenv("CBBE2UBE_UBE_BODY_1", str(_file(tmp_path, "a_1.nif")))
    ac._body_trees()
    br._BODY_DISCOVERY_CACHE.clear()
    monkeypatch.setenv("CBBE2UBE_UBE_BODY_1", str(_file(tmp_path, "b_1.nif")))
    ac._body_trees()
    assert len(builds) == 1, builds
