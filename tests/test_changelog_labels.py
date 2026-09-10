"""Every setting name the CHANGELOG tells a user to look for must be a real
GUI label.

The Unreleased section says 'Turn it on with "..."' and '*...* stays off';
one of those quoted a name that existed in no Setting row (2026-09-01
audit), so a user following the changelog could not find the switch.
"""
from __future__ import annotations

import re
from pathlib import Path

from src import gui_settings as gs

ROOT = Path(__file__).resolve().parents[1]
QUOTED = re.compile(r'Turn it on with "([^"]+)"')
STAYS_OFF = re.compile(r"^\*([^*]+)\* (?:\(Advanced\) )?stays off", re.M)
SETTING_LINE = re.compile(r'^Setting: "([^"]+)"', re.M)


def setting_names(text: str) -> list[str]:
    """Every setting name the CHANGELOG tells a user to go and find, ANYWHERE.

    This used to read only the first `## ` section, asserting it was
    `## Unreleased`, and the calling test then required at least three names.
    Both halves broke at release time and neither had anything to do with the
    rule being enforced:

      * cutting a release the way 9e435e8 cut 1.3 -- renaming `## Unreleased` to
        `## <version> - <date>` -- made the assert fail on the new heading. That
        test did not exist when 1.3 was cut; it arrived thirteen days later, so
        the template and the check have never been run against each other.
      * opening a fresh EMPTY `## Unreleased` above the dated section instead
        moves every name out of the scanned region, and the `>= 3` floor then
        fails on a changelog that is perfectly correct.

    A name in a released section is exactly as capable of pointing at a GUI row
    that has since been renamed -- more so, since it is older -- so scanning the
    whole file is both the wider check and the one with no release-day edge.
    """
    found = (QUOTED.findall(text) + STAYS_OFF.findall(text)
             + SETTING_LINE.findall(text))
    return [" ".join(n.split()) for n in found]        # quotes wrap across lines


def test_every_changelog_setting_name_is_a_gui_label():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    names = setting_names(text)
    assert len(names) >= 3, "the extractor found almost nothing -- check the patterns"
    labels = {s.label for s in gs.SETTINGS}
    missing = [n for n in names if n not in labels]
    assert not missing, (
        "CHANGELOG names settings that no GUI row is labelled with:\n  "
        + "\n  ".join(missing))


def test_the_check_can_actually_fail():
    fake = '# Changelog\n\n## Unreleased\n\nTurn it on with "No such switch".\n\n## 1.3\n'
    assert setting_names(fake) == ["No such switch"]
    assert "No such switch" not in {s.label for s in gs.SETTINGS}


def test_a_name_in_a_RELEASED_section_is_scored_too():
    """The regression the rewrite above is for. Cutting a release moves every
    name out of `## Unreleased`; if the scan followed that heading, the whole
    file would go unchecked the moment it shipped."""
    fake = ('# Changelog\n\n## Unreleased\n\n_Nothing yet._\n\n'
            '## 1.4 - 2026-09-09\n\nSetting: "No such switch" (Armor), now on.\n')
    assert setting_names(fake) == ["No such switch"]
