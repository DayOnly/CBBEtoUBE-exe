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

"""Every SkyPatcher armature link is emitted or COUNTED. Nothing vanishes.

WHY THIS EXISTS. A link recorded in a patch's `.skypatcher.json` sidecar and not
emitted into the INI is an armour piece with no UBE armature: it equips and
renders NOTHING on a UBE race. It is the quietest failure the converter has --
the mesh converts, the report says "converted", the ESP carries the minted ARMA,
and no other output ever mentions the piece.

It shipped. A pack was found (2026-08-11) in which one mod's SkyPatcher INI
carried 106 lines from the coverage pass and ZERO of the 114 links that mod's
own patch sidecar had recorded, so its slot-34 arms -- which the coverage pass
correctly skips, because deforming slots belong to the per-mod patch -- had no
armature from either side. The run reconciled 21,004 sidecar entries down to
10,297 INI lines and printed neither number, so nothing could have caught it
short of a user reporting one invisible piece.

All three drop paths were bare `continue`s. Two are legitimate and stay
(first-writer-wins across patches; render-identical armatures on one armour).
The third -- a link whose minted ARMA has no merged record -- is pathological
and now reports itself.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import ube_patcher as up  # noqa: E402


def _stats(**kw):
    base = dict(sp_links_seen=0, sp_links_emitted=0, sp_dropped_no_record=0,
                sp_dropped_duplicate_pair=0, sp_dropped_render_identical=0,
                sp_unreadable_sidecars=[])
    base.update(kw)
    return base


def test_a_run_with_no_links_says_nothing():
    """No sidecars is normal (a pack of pure passthrough accessories). Don't
    make a clean run noisier than it needs to be."""
    assert up.report_link_reconciliation(_stats()) == []


def test_the_happy_path_states_both_numbers():
    """The count that would have exposed the shipped defect is 'recorded ->
    emitted'. It has to be printed even when nothing is wrong, or nobody ever
    learns what the normal ratio looks like."""
    out = up.report_link_reconciliation(
        _stats(sp_links_seen=100, sp_links_emitted=90,
               sp_dropped_duplicate_pair=8, sp_dropped_render_identical=2))
    assert len(out) == 1, out
    assert "100 recorded -> 90 emitted" in out[0]
    assert not any("!!" in l for l in out), "legitimate drops must not warn"


def test_an_unresolved_link_is_reported_as_invisible_armour():
    """The pathological drop. It must name the consequence -- 'unresolved
    armature link' means nothing to anyone reading a build log."""
    out = up.report_link_reconciliation(
        _stats(sp_links_seen=10, sp_links_emitted=7, sp_dropped_no_record=3))
    warn = [l for l in out if "!!" in l]
    assert warn, out
    assert "INVISIBLE" in warn[0]
    assert "3" in warn[0]


def test_an_unreadable_sidecar_is_reported_not_swallowed():
    """A sidecar that fails to parse silently discards every link that patch
    recorded -- the whole mod goes invisible, not one piece."""
    out = up.report_link_reconciliation(
        _stats(sp_links_seen=5, sp_links_emitted=5,
               sp_unreadable_sidecars=["x.esp.skypatcher.json: JSONDecodeError"]))
    assert any("unreadable link sidecar" in l and "!!" in l for l in out), out


def test_the_accounting_must_balance_or_it_says_so():
    """The guard on the guard: emitted + the three reasons must equal recorded.
    If a FOURTH drop path is ever added without a counter, this is what notices
    -- otherwise the reconciliation itself becomes the thing that hides a leak."""
    out = up.report_link_reconciliation(
        _stats(sp_links_seen=100, sp_links_emitted=50))
    assert any("does not balance" in l for l in out), out


def test_a_balanced_run_does_not_claim_an_imbalance():
    """Negative control for the test above -- otherwise it would pass on a
    reconciliation that always cried imbalance."""
    out = up.report_link_reconciliation(
        _stats(sp_links_seen=100, sp_links_emitted=50,
               sp_dropped_duplicate_pair=30, sp_dropped_render_identical=15,
               sp_dropped_no_record=5))
    assert not any("does not balance" in l for l in out), out


def test_merge_returns_every_counter_the_report_reads():
    """The report and the merge must agree on key names. Two dicts wired by
    string keys drift silently: a renamed key would make the reconciliation
    read zeros forever and report a permanently clean run."""
    import inspect
    src = inspect.getsource(up.merge_patches)
    for key in ("sp_links_seen", "sp_links_emitted", "sp_dropped_no_record",
                "sp_dropped_duplicate_pair", "sp_dropped_render_identical",
                "sp_unreadable_sidecars"):
        assert f'"{key}"' in src, f"merge_patches no longer returns {key}"
        assert key in inspect.getsource(up.report_link_reconciliation), key


def test_the_split_aggregates_the_counters_across_pieces():
    """A split run reconciles per PIECE; without aggregation the whole-run
    number silently describes only the last piece."""
    import inspect
    src = inspect.getsource(up.merge_patches_split)
    for key in ("sp_links_seen", "sp_links_emitted", "sp_dropped_no_record"):
        assert key in src, f"merge_patches_split does not aggregate {key}"
