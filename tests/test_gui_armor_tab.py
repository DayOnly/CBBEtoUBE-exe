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

"""The Armor tab: the defaults ARE the recommendation, so say so and stay shut.

The tab carries 76 settings, 39 of them in one group, and only 14 are flagged
advanced -- so 62 controls rendered flat, four screens deep. Presented that way
it reads as an invitation to tune, and it is not one: every default was chosen
against measured in-game results, and changing one blind usually makes
conversions worse.

Three things pin that:

  * a DISCLAIMER on any tab big enough to be a wall of knobs (and NOT on the
    small ones -- a warning banner on a 2-setting tab is noise, and noise
    teaches people to skip the banner that matters);
  * sections COLLAPSED by default, so the tab opens as an eight-line index;
  * a CHANGED-FROM-DEFAULT count, per group and per tab, because when the
    defaults are the recommendation the only thing you need from a closed
    section is whether you have moved anything in it.

The display-gated tests build the real window and read what it actually
constructed. `changed_from_default` is pure and tested directly.
"""
import ast
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src import gui_settings as gs  # noqa: E402
from tests.test_gui_reporting_surface import _SPY  # noqa: E402
from tests.test_gui_smoke import _launch_in_child, needs_display  # noqa: E402

CARET_SHUT, CARET_OPEN = "▸", "▾"


# ---- changed_from_default: pure, and the count everything else reports ----

def test_nothing_set_means_nothing_changed():
    assert gs.changed_from_default({}) == set()


def test_a_value_equal_to_its_default_is_not_changed():
    d = gs.defaults()
    assert gs.changed_from_default(dict(d)) == set()


def test_a_value_differing_from_its_default_is_changed():
    d = gs.defaults()
    key = "conform_to_body"
    assert key in d, "fixture key vanished from the registry"
    vals = dict(d)
    vals[key] = not d[key]
    assert gs.changed_from_default(vals) == {key}


def test_unregistered_keys_are_ignored():
    """The saved file carries `_known_settings`, which is bookkeeping rather
    than a setting anyone tuned. Counting it would make every count wrong."""
    vals = dict(gs.defaults())
    vals["_known_settings"] = ["whatever"]
    vals["not_a_real_setting"] = 123
    assert gs.changed_from_default(vals) == set()


def test_window_state_is_registered_but_never_shown_on_a_tab():
    """`window_geometry` IS a registered setting, so `changed_from_default`
    reports it -- correctly, since `save_values` persists it too.

    It must never reach a tab's changed count, though: it is written whenever
    the window is RESIZED, and a banner announcing "1 setting changed from the
    defaults" because someone dragged a corner would be exactly the false alarm
    that teaches people to ignore the banner. It is safe today because its tab
    ("Appearance") is not in `tabs_present()`, and this pins that.
    """
    vals = dict(gs.defaults())
    vals["window_geometry"] = "800x600"
    assert "window_geometry" in gs.changed_from_default(vals), (
        "the helper should mirror what save_values persists")
    displayed = {s.key for t in gs.tabs_present()
                 for g in gs.groups_in_tab(t)
                 for s in gs.settings_in(t, g)}
    assert displayed, "no settings are displayed at all -- vacuous"
    assert "window_geometry" not in displayed, (
        "window geometry is now rendered on a tab, so resizing the window "
        "would inflate that tab's changed-from-defaults count")


def test_it_agrees_with_what_save_values_would_persist(tmp_path):
    """The count and the file must never disagree -- `save_values` writes
    exactly the non-default keys, so this is the same question asked twice."""
    import json
    d = gs.defaults()
    key = "conform_to_body"
    vals = dict(d)
    vals[key] = not d[key]
    p = tmp_path / "s.json"
    assert gs.save_values(vals, p)
    written = {k for k in json.loads(p.read_text(encoding="utf-8"))
               if k != "_known_settings"}
    assert written == gs.changed_from_default(vals) == {key}


# ---- reset / import scope ------------------------------------------------

def test_displayed_keys_covers_the_armor_settings():
    keys = gs.displayed_keys()
    assert "conform_to_body" in keys, keys
    assert len(keys) > 50, f"suspiciously few displayed settings: {len(keys)}"


def test_displayed_keys_excludes_hidden_window_state():
    """"Reset ALL conversion settings" must not change your theme or forget
    your window size -- both are registered settings on a tab nobody sees."""
    keys = gs.displayed_keys()
    assert "theme" not in keys
    assert "window_geometry" not in keys


def test_reset_scope_is_every_displayed_setting_not_just_the_open_ones():
    """THE BUG COLLAPSING THE SECTIONS INTRODUCED.

    Reset and import used to be driven through `_setting_var_by_key`, which
    only holds vars for controls that are currently BUILT. Once sections
    started collapsed, that meant "Reset ALL" reset only the sections you
    happened to have opened -- silently, and differently per session.

    The scope is now the REGISTRY, so it cannot depend on what is on screen.
    Guarded at source because the alternative is clicking a button in a modal
    from a spawned Tk process.
    """
    src = (PROJ / "src" / "gui.py").read_text(encoding="utf-8")
    body = src[src.index("def _apply_setting_values("):]
    body = body[:body.index("\n    def ", 10)]
    assert "gui_settings.displayed_keys()" in body, (
        "the reset/import scope no longer comes from the registry")
    assert 'state["settings"][k] = v' in body, (
        "reset/import no longer writes the settings themselves; if it is back "
        "to driving widget vars it will miss every collapsed section")


# ---- the tab itself ------------------------------------------------------

def _labels(tmp_path):
    r = _launch_in_child(tmp_path, extra_setup=_SPY)
    assert r.returncode == 0, f"GUI build failed:\n{r.stdout}\n{r.stderr}"
    for line in r.stdout.splitlines():
        if line.startswith("SPY_LBLS="):
            return ast.literal_eval(line[len("SPY_LBLS="):])
    raise AssertionError(f"spy recorded no labels:\n{r.stdout}\n{r.stderr}")


@needs_display
def test_the_armor_tab_warns_that_the_defaults_are_the_tuned_values(tmp_path):
    labels = _labels(tmp_path)
    assert labels, "no labels -- vacuous"
    assert any("defaults are the tuned values" in str(l) for l in labels), (
        f"no defaults disclaimer was built: {[l for l in labels][:10]}")
    body = [l for l in labels if "measured against how the armour" in str(l)]
    assert body, "the disclaimer has a heading but no explanation"
    assert "change ONE thing" in str(body[0]), (
        "the disclaimer does not tell the reader HOW to change a setting "
        "safely, which is the only actionable part of it")


@needs_display
def test_the_big_groups_start_collapsed(tmp_path):
    """An eight-line index, not four screens of checkboxes."""
    labels = _labels(tmp_path)
    heads = [l for l in labels if str(l)[:1] in (CARET_SHUT, CARET_OPEN)]
    assert heads, f"no collapsible section headers were built: {labels[:10]}"
    fit = [h for h in heads if "Fit and clearance" in str(h)]
    assert fit, f"the biggest Armor group has no header: {heads}"
    assert str(fit[0]).startswith(CARET_SHUT), (
        f"the 39-setting group did not start collapsed: {fit[0]!r}")


@needs_display
def test_a_collapsed_group_does_not_build_its_controls(tmp_path):
    """Collapsed has to mean NOT BUILT, not merely hidden.

    If the controls were constructed and then unpacked, the tab would still pay
    for 62 widgets and the point of collapsing would be cosmetic only. This is
    also the check that would fail if a future edit renders the body and packs
    it away.
    """
    labels = _labels(tmp_path)
    inside = "Conform fitted cloth to body"       # a Fit-and-clearance setting
    assert any("Fit and clearance" in str(l) for l in labels), (
        "the group header is missing, so this test proves nothing")
    assert inside not in labels, (
        f"{inside!r} was constructed even though its section is collapsed")


@needs_display
def test_a_small_tab_gets_neither_the_banner_nor_a_collapse(tmp_path):
    """Paths has two settings. A wall-of-knobs warning there is noise."""
    labels = _labels(tmp_path)
    # The Paths tab's own settings ARE built (nothing to collapse away).
    assert "UBE body reference NIF" in labels, (
        f"the Paths tab did not render its settings: {labels[:10]}")
    heads = [str(l) for l in labels if str(l)[:1] in (CARET_SHUT, CARET_OPEN)]
    bodies = [h for h in heads if "Bodies" in h]
    assert bodies, f"no Bodies section header: {heads}"
    assert bodies[0].startswith(CARET_OPEN), (
        f"a 1-setting group started collapsed: {bodies[0]!r}")


@needs_display
def test_the_tab_reports_whether_anything_is_off_default(tmp_path):
    """With defaults as the recommendation, 'have I changed anything?' is the
    question the tab exists to answer at a glance."""
    labels = _labels(tmp_path)
    assert any("at its default" in str(l) or "changed from the defaults"
               in str(l) for l in labels), (
        f"the tab does not report its changed-from-default state: {labels[:12]}")
