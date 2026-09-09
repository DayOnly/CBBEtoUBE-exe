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

"""The checker behind the `#pair-tri-names` prediction.

Promoted from a scratchpad probe the day it was written, because the
consolidation plan states a FALSIFIABLE prediction -- `pairs where a half loses
its morph` going 54 -> 1 once the flag is armed -- and a prediction whose
checker lives in a gitignored folder is one nobody else can falsify. That trap
has already hidden three "mandatory" tools on this project.

These tests cover the parts that do not need a converted pack: the pairing, the
BODYTRI path resolution (two different prefixes, both real), and the refusals.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "analysis"))

from scripts.analysis import weight_pair_tri_names as w   # noqa: E402


def _touch(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\0")


class TestPairing:
    def test_it_groups_the_two_halves_of_one_garment(self, tmp_path):
        _touch(tmp_path / "a" / "robe_0.nif")
        _touch(tmp_path / "a" / "robe_1.nif")
        got = w.pairs_under(tmp_path)
        assert len(got) == 1
        (d, base), have = next(iter(got.items()))
        assert base == "robe" and set(have) == {"0", "1"}

    def test_a_weight_agnostic_mesh_is_not_a_pair(self, tmp_path):
        """`name.nif` with no suffix is shipped deliberately and must not be
        invented into a half of something."""
        _touch(tmp_path / "a" / "helmet.nif")
        assert w.pairs_under(tmp_path) == {}

    def test_halves_in_different_folders_are_different_garments(self, tmp_path):
        """Keyed by (dir, base): two mods can ship the same basename."""
        _touch(tmp_path / "a" / "robe_0.nif")
        _touch(tmp_path / "b" / "robe_1.nif")
        assert len(w.pairs_under(tmp_path)) == 2

    def test_a_lone_half_is_kept_so_it_can_be_COUNTED_as_an_exclusion(
            self, tmp_path):
        """It must reach `score`, which counts it -- silently dropping it here
        would make the population smaller with nothing to show for it."""
        _touch(tmp_path / "a" / "robe_1.nif")
        got = w.pairs_under(tmp_path)
        assert len(got) == 1 and set(next(iter(got.values()))) == {"1"}


class TestTriPathResolution:
    def test_both_real_prefixes_are_stripped(self, tmp_path):
        """A BODYTRI string is stored game-relative and carries one of two
        prefixes in this pack. Joining it on unstripped looks for
        `<out>/!UBE/!UBE/...` and finds nothing -- which reads as 'no tri',
        i.e. a CLEAN result, so getting this wrong hides the defect."""
        seen = {}
        for pre in ("!UBE\\", "meshes\\"):
            cache = {}
            got = w.tri_shape_names(tmp_path, pre + "armor\\x\\y.tri", cache)
            seen[pre] = got
        # neither file exists, so both are None -- what matters is that neither
        # RAISED and both were cached under their own key
        assert seen["!UBE\\"] is None and seen["meshes\\"] is None

    def test_an_unreadable_tri_is_None_not_an_empty_set(self, tmp_path):
        """None means "could not read"; an empty set would mean "read it, it
        names nothing", and the caller treats those differently -- an
        unreadable tri must not be scored as a dead one."""
        p = tmp_path / "armor" / "x.tri"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"not a tri")
        assert w.tri_shape_names(tmp_path, "!UBE\\armor\\x.tri", {}) is None

    def test_the_result_is_cached_per_bodytri_string(self, tmp_path):
        cache = {}
        w.tri_shape_names(tmp_path, "!UBE\\a.tri", cache)
        assert "!UBE\\a.tri" in cache
        cache["!UBE\\a.tri"] = {"SENTINEL"}
        assert w.tri_shape_names(tmp_path, "!UBE\\a.tri", cache) == {"SENTINEL"}


class TestRefusals:
    def test_no_argument_prints_usage_and_exits_2(self, capsys):
        assert w.main([]) == 2
        assert "#pair-tri-names" in capsys.readouterr().out

    def test_a_dir_with_no_output_exits_3_not_0(self, tmp_path, capsys):
        """"Nothing measured" must never read as "measured and clean"."""
        assert w.main([str(tmp_path)]) == 3
        assert "0/0 is not a pass" in capsys.readouterr().out

    def test_a_MISTYPED_path_is_refused_rather_than_walked(self, tmp_path):
        """An earlier draft fell back to the directory it was handed when
        neither candidate root existed, so a typo produced a confident number
        about the wrong files."""
        (tmp_path / "not-the-pack").mkdir()
        assert w.main([str(tmp_path / "not-the-pack")]) == 3

    def test_the_UBE_root_itself_is_accepted(self, tmp_path):
        """Both spellings are in use: the mod dir, and the !UBE root."""
        import pytest
        _touch(tmp_path / "meshes" / "!UBE" / "a" / "robe_0.nif")
        _touch(tmp_path / "meshes" / "!UBE" / "a" / "robe_1.nif")
        for arg in (tmp_path, tmp_path / "meshes" / "!UBE"):
            # Both find the population. The NIFs are stubs, so every pair is
            # EXCLUDED as unreadable -- see the test below for why that must
            # not read as clean.
            with pytest.raises(SystemExit) as e:
                w.main([str(arg)])
            assert e.value.code == 3

    def test_a_run_where_EVERY_pair_is_excluded_is_not_a_pass(self, tmp_path):
        """CAUGHT BY THIS TEST, in this tool, on the day it was written.

        The floor was on `pairs examined`, which counts exclusions too, so a
        run whose every pair failed to load printed
        `PIECES WHERE ONE HALF'S MORPH IS DEAD : 0   OK` and exited clean --
        0/0 reading as a pass, which is the one rule this project enforces
        everywhere. The floor is now on `pairs SCORED`.
        """
        import pytest
        _touch(tmp_path / "meshes" / "!UBE" / "a" / "robe_0.nif")
        _touch(tmp_path / "meshes" / "!UBE" / "a" / "robe_1.nif")
        with pytest.raises(SystemExit) as e:
            w.main([str(tmp_path)])
        assert e.value.code == 3

    def test_the_scored_count_is_what_the_floor_uses(self):
        """Pin the two counters apart, so a later edit cannot quietly floor on
        the examined count again."""
        src = Path(w.__file__).read_text(encoding="utf-8", errors="replace")
        assert 'require_population(range(stat["pairs SCORED"])' in src
        assert '"pairs SCORED"] += 1' in src

    def test_a_bad_limit_is_refused_rather_than_ignored(self, tmp_path):
        assert w.main([str(tmp_path), "--limit", "banana"]) == 2


def test_the_prediction_stays_written_down_in_the_tool():
    """The tool exists to check ONE stated number. If the docstring loses it,
    the next reader cannot tell what result would falsify the fix."""
    src = Path(w.__file__).read_text(encoding="utf-8", errors="replace")
    assert "54 -> 1" in src
    assert "pairs where a half loses its morph" in src
    assert "5 shapes vs 4" in src, (
        "the one pair the fix deliberately refuses must stay named, or its "
        "survival will read as a failure of the fix")
