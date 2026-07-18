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

"""Every colour palette must be COMPLETE and LEGIBLE.

A palette is a plain dict of hex colours, so the two ways to break one are silent:
drop a key (the widget renders with Tk's default -- the white-list-on-dark-theme
class of bug) or pick colours that don't contrast (unreadable text nobody notices
until a user squints). Both are cheap to check, so check them.

Contrast uses the WCAG 2.x relative-luminance formula. The bar is 4.5:1 for pairs
that render normal-size text and 3.0:1 for large text / UI chrome, matching AA.
`onaccent`/`accent` is a real one, and the two original themes DON'T pass it: the
shared #3b7dd8 accent gives white button text only 4.11:1. That is pre-existing and
deliberately left alone (changing it alters themes the user already uses), so it is
recorded in _AA_EXCEPTIONS with its measured value rather than by quietly lowering the
bar. The floor there is asserted too, so the pair can never silently get WORSE. Fixing
it properly means the light and dark themes must solve the accent differently -- no
single blue clears 4.5 against white AND stays visible on a dark ground.

Also pins the picker list to the palette dict: it used to be a hard-coded
("Standard","Light","Dark") tuple, so a palette you forgot to add there existed
but could never be selected.
#gui-themes"""
import re

import pytest

from src.gui import _THEMES, THEME_KEYS, THEME_NAMES, THEME_LABELS

_HEX = re.compile(r"^#[0-9a-f]{6}$")

# (theme, fg, bg) pairs known NOT to meet the bar, with the ratio measured when they
# were recorded. Pre-existing and left alone by choice -- NOT a licence to add more.
# Each is pinned to its measured value so it cannot degrade unnoticed.
_AA_EXCEPTIONS = {
    ("light", "onaccent", "accent"): 4.11,   # white on #3b7dd8
    ("dark", "onaccent", "accent"): 4.11,    # white on #3b7dd8
}

# (foreground, background, minimum ratio). 4.5 = AA normal text; 3.0 = AA large
# text / UI component; 2.0 is a floor for intentionally-muted disabled text.
_CONTRAST_PAIRS = (
    ("fg", "bg", 4.5),            # body text
    ("logfg", "logbg", 4.5),      # the conversion log -- the most-read surface
    ("onaccent", "accent", 4.5),  # button labels drawn on the accent
    ("hint", "bg", 3.0),
    ("labelfg", "bg", 3.0),
    ("tabselfg", "tab", 3.0),
    ("accent", "bg", 3.0),        # the accent must be visible at all
    ("disabled", "bg", 2.0),      # muted on purpose, but not invisible
)


def _relative_luminance(hex_colour):
    def chan(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def _contrast(a, b):
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_contrast_helper_matches_known_values():
    """Guard the guard: black-on-white is exactly 21:1, and a colour against
    itself is 1:1. Without this a broken formula would silently pass everything."""
    assert _contrast("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
    assert _contrast("#3b7dd8", "#3b7dd8") == pytest.approx(1.0, abs=0.001)
    # the shared accent recorded in _AA_EXCEPTIONS: white on it is under the AA bar
    assert _contrast("#3b7dd8", "#ffffff") < 4.5


@pytest.mark.parametrize("name", sorted(_THEMES))
def test_palette_has_exactly_the_required_keys(name):
    """A missing key makes that widget fall back to Tk's default colour."""
    assert set(_THEMES[name]) == set(THEME_KEYS)


@pytest.mark.parametrize("name", sorted(_THEMES))
def test_palette_colours_are_lowercase_six_digit_hex(name):
    for key, value in _THEMES[name].items():
        assert _HEX.match(value), f"{name}.{key} = {value!r} is not #rrggbb"


@pytest.mark.parametrize("name", sorted(_THEMES))
def test_palette_is_legible(name):
    p = _THEMES[name]
    for fg, bg, minimum in _CONTRAST_PAIRS:
        ratio = _contrast(p[fg], p[bg])
        if (name, fg, bg) in _AA_EXCEPTIONS:
            continue        # covered by test_known_exceptions_do_not_regress
        assert ratio >= minimum, (
            f"{name}: {fg} on {bg} is {ratio:.2f}:1, needs {minimum}:1")


def test_known_exceptions_do_not_regress():
    """The recorded sub-AA pairs must stay exactly as measured. If someone darkens
    the accent they should clear the bar and DELETE the exception, not drift."""
    for (name, fg, bg), expected in _AA_EXCEPTIONS.items():
        got = _contrast(_THEMES[name][fg], _THEMES[name][bg])
        assert got == pytest.approx(expected, abs=0.01), (
            f"{name}: {fg} on {bg} moved to {got:.2f}:1 (was {expected}:1). "
            f"If it now clears its bar, remove it from _AA_EXCEPTIONS.")


def test_no_new_exceptions_creep_in():
    """Only the two pre-existing pairs are exempt. A NEW palette must pass outright."""
    assert set(_AA_EXCEPTIONS) == {
        ("light", "onaccent", "accent"), ("dark", "onaccent", "accent")}


def test_picker_offers_every_palette():
    """The picker list is DERIVED from the palettes -- a new palette can't be
    added-but-unselectable, which the old hard-coded tuple allowed."""
    assert THEME_NAMES == tuple(_THEMES)
    assert len(THEME_LABELS) == len(_THEMES)
    # the label round-trips to the key the way the picker looks it up
    for label in THEME_LABELS:
        assert label.strip().lower() in _THEMES


def test_default_theme_exists():
    """gui_settings defaults theme='standard'; _apply_theme falls back to it."""
    from src import gui_settings
    assert gui_settings.defaults()["theme"] in _THEMES
