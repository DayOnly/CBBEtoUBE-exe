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


def unreleased_names(text: str) -> list[str]:
    head = text.split("\n## ", 2)[1]          # the first "## " section = Unreleased
    assert head.startswith("Unreleased"), head[:40]
    found = QUOTED.findall(head) + STAYS_OFF.findall(head) + SETTING_LINE.findall(head)
    return [" ".join(n.split()) for n in found]        # quotes wrap across lines


def test_every_changelog_setting_name_is_a_gui_label():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    names = unreleased_names(text)
    assert len(names) >= 3, "the extractor found almost nothing -- check the patterns"
    labels = {s.label for s in gs.SETTINGS}
    missing = [n for n in names if n not in labels]
    assert not missing, (
        "CHANGELOG names settings that no GUI row is labelled with:\n  "
        + "\n  ".join(missing))


def test_the_check_can_actually_fail():
    fake = '# Changelog\n\n## Unreleased\n\nTurn it on with "No such switch".\n\n## 1.3\n'
    assert unreleased_names(fake) == ["No such switch"]
    assert "No such switch" not in {s.label for s in gs.SETTINGS}
