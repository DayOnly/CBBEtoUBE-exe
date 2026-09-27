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

r"""#stale-output-sweep -- our old conversions that no source makes any more
leave `meshes\!UBE`, on evidence, never on absence alone.

THE DEFECT. The output folder is never cleaned, so a mesh an earlier run
converted stays and wins its path in game after the planner stops making it
(37 bases / 103 files on the reported modlist). Moving every file no claim owns
is wrong too: the planner fails OPEN to a smaller plan (a failed NPC-outfit read
drops 48 pieces female NPCs wear), and absence cannot tell a lost piece from a
dropped one.

THE RULE. A manifest records which source made each base. A base moves only
when that source is gone or gave a POSITIVE reason to drop it; only on a full
run whose plan is complete; under a brake; whole; and only if the merge then
writes a Combined that does not name it -- else everything goes back.
`CBBE2UBE_NO_STALE_OUTPUT_SWEEP=1` does none of it;
`CBBE2UBE_STALE_OUTPUT_SWEEP_REPORT_ONLY=1` lists and never moves.
"""
import argparse
import json
import os
import struct
from pathlib import Path

import pytest

from src import auto_convert as ac
from src import esp, nif_convert, paths, user_warnings
from src import stale_sweep as ss
from src.esp import encode_subrecord, encode_zstring
from tests import test_npc_worn_nonplayable as npcw
from tests.test_coverage_ube_twin import _twin_of
from tests.test_selection_winner_playable import _armo, _plugin, _rec

OFF = "CBBE2UBE_NO_STALE_OUTPUT_SWEEP"
REPORT_ONLY = "CBBE2UBE_STALE_OUTPUT_SWEEP_REPORT_ONLY"
STARTED = 1_700_000_000.0
STAMP = ss.run_stamp(STARTED)
OLD = "armor/old/cuirass"
IRON = "armor/iron/cuirass"
WHOLE = ("_0.nif", "_1.nif", ".nif", ".tri", ".xml")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for v in (OFF, REPORT_ONLY, "CBBE2UBE_NO_NPC_WORN_NONPLAYABLE",
              "CBBE2UBE_NO_SELECTION_WINNER_PLAYABLE"):
        monkeypatch.delenv(v, raising=False)
    # The vanilla sweep's source is the game Data folder.
    monkeypatch.setattr(ac, "_vanilla_sweep_esps",
                        lambda p: [Path(p) / "Skyrim.esm"] if Path(p).name == "Data" else [])
    monkeypatch.setattr(ac, "_ARMO_WINNER_CACHE", {})
    monkeypatch.setattr(ac, "_ARMO_WINNER_UNREADABLE", {})
    monkeypatch.setattr(ac, "_STALE_ADOPTED", {})
    ss._set_pending(None)
    yield
    ss._set_pending(None)


class World:
    """A mods folder, our output mod in it, and the game Data folder."""

    def __init__(self, tmp_path):
        self.mods = tmp_path / "mods"
        self.mods.mkdir(parents=True)
        self.out = self.mods / "Output"
        self.ube = self.out / "meshes" / "!UBE"
        self.patches = self.out / "_unmerged_patches"
        self.data = tmp_path / "Data"

    def mesh(self, base, *suffixes):
        for s in suffixes:
            p = self.ube / (base + s)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(f"{base}{s}".encode())

    def has(self, base, s="_1.nif") -> bool:
        return (self.ube / (base + s)).is_file()

    def moved(self, base, s="_1.nif") -> Path:
        return self.out / "_superseded" / STAMP / "meshes" / "!UBE" / (base + s)

    def mod(self, name):
        (self.mods / name).mkdir(parents=True, exist_ok=True)
        return self.mods / name

    def record(self, bases, patches=None):
        ss.write_manifest(self.out, {"format": 1, "run_stamp": "earlier", "build": "",
                                     "bases": bases, "patches": patches or {}})

    def manifest(self):
        return json.loads((self.out / ss.MANIFEST_NAME).read_bytes())

    def report(self):
        return json.loads((self.out / "_superseded" / ss.REPORT_NAME).read_bytes())


@pytest.fixture
def w(tmp_path):
    world = World(tmp_path)
    world.mesh(IRON, "_0.nif", "_1.nif", ".tri")
    world.mesh(OLD, *WHOLE)
    return world


def _result(src, claims=(), reasons=None, esps=(), **kw):
    r = ac.AutoConvertResult(source_dir=Path(src), output_dir=Path("."))
    r.claimed_weight_bases = set(claims)
    r.dropped_base_reasons = dict(reasons or {})
    r.output_esps = [Path(e) for e in esps]
    for k, v in kw.items():
        setattr(r, k, v)
    return r


def _vanilla(w, claims=(IRON,)):
    return (w.data, _result(w.data, claims), None)


def _args(w, results, *, all_mods=True, enabled=None, excluded=(), **kw):
    ns = argparse.Namespace(
        sources=[s for s, _r, _e in results], plugins_only=False,
        merged_name="Combined.esp", unmerged_patch_subdir="_unmerged_patches",
        stale_sweep={"all_mods": all_mods, "warn_base": user_warnings.problem_count(),
                     "started": STARTED, "mods_root": str(w.mods),
                     "enabled": enabled, "excluded": list(excluded)})
    ns.__dict__.update(kw)
    return ns


def _state(**kw):
    s = {"mesh_index": {}, "bsa": (True, []), "npc_worn": frozenset(),
         "winner_np": {}, "covered": set(), "warns": user_warnings.problem_count()}
    s.update(kw)
    return s


def _sweep(w, results, args=None, state=None, claimed=()):
    args = args if args is not None else _args(w, results)
    return ac._stale_output_sweep(args, w.out, w.patches, results, set(claimed),
                                  state if state is not None else _state())


def _finish(w, results, args=None, merged=True):
    args = args if args is not None else _args(w, results)
    return ac._stale_output_sweep_finish(args, w.out, results, merged=merged)


def _combined(w, *models):
    """A written Combined with one armature per model path."""
    recs = [_rec(b"ARMA", 0x01000800 + i, encode_subrecord(b"MOD3", encode_zstring(m)))
            for i, m in enumerate(models)]
    _plugin(w.out / "Combined.esp", ARMA=recs)


# ============================================================ the move itself

def test_a_recorded_base_of_a_removed_source_moves_whole(w):
    """Bare, _0 and _1 NIFs, the .tri and the .xml all go, to the stamp folder
    under the same relative path; the current conversion stays."""
    w.record({OLD: "Old Mod"})
    results = [_vanilla(w)]
    assert _sweep(w, results) == 0
    for s in WHOLE:
        assert not w.has(OLD, s), s
        assert w.moved(OLD, s).read_bytes() == f"{OLD}{s}".encode()
    assert w.has(IRON) and w.has(IRON, ".tri")
    assert ss.pending() is not None, "held until the merge confirms"


def test_the_merge_keeps_the_moves_and_the_manifest_forgets_them(w):
    w.record({OLD: "Old Mod"})
    results = [_vanilla(w)]
    _sweep(w, results)
    assert _finish(w, results, merged=True) == 0
    assert ss.pending() is None and w.moved(OLD).is_file() and not w.has(OLD)
    journal = json.loads((w.out / "_superseded" / STAMP / ss.JOURNAL_NAME).read_bytes())
    assert journal["status"] == "kept"
    assert w.manifest()["bases"] == {IRON: "vanilla"}


def test_a_merge_that_did_not_write_the_combined_puts_every_file_back(w):
    """An old Combined still names the moved meshes: a missing-mesh crash."""
    w.record({OLD: "Old Mod"})
    results = [_vanilla(w)]
    _sweep(w, results)
    assert _finish(w, results, merged=False) >= 1
    for s in WHOLE:
        assert w.has(OLD, s) and not w.moved(OLD, s).exists(), s
    journal = json.loads((w.out / "_superseded" / STAMP / ss.JOURNAL_NAME).read_bytes())
    assert journal["status"] == "put back"
    assert w.manifest()["bases"] == {IRON: "vanilla", OLD: "Old Mod"}, "still recorded"


def test_a_combined_that_names_a_moved_mesh_puts_it_back(w):
    w.record({OLD: "Old Mod"})
    results = [_vanilla(w)]
    _sweep(w, results)
    _combined(w, "!UBE\\armor\\iron\\cuirass_1.nif", "meshes\\!UBE\\Armor\\Old\\Cuirass_1.nif")
    assert _finish(w, results, merged=True) >= 1
    assert w.has(OLD) and not w.moved(OLD).exists()


def test_a_combined_naming_only_current_meshes_keeps_the_moves(w):
    w.record({OLD: "Old Mod"})
    results = [_vanilla(w)]
    _sweep(w, results)
    _combined(w, "!UBE\\armor\\iron\\cuirass_1.nif", "armor\\old\\cuirass_1.nif")
    assert _finish(w, results, merged=True) == 0
    assert not w.has(OLD) and w.moved(OLD).is_file()


def test_a_run_that_stops_before_the_merge_puts_every_file_back(w):
    w.record({OLD: "Old Mod"})
    _sweep(w, [_vanilla(w)])
    assert ac._stale_output_sweep_abandoned() >= 1
    assert w.has(OLD) and ss.pending() is None


def test_a_run_that_was_killed_is_put_back_by_the_next_one(w):
    """A journal left 'waiting for the merge' belongs to a run that died between
    its moves and its merge; the next full run puts those files back first."""
    w.record({OLD: "Old Mod"})
    results = [_vanilla(w)]
    _sweep(w, results)
    ss._set_pending(None)                       # the process died here
    assert not w.has(OLD)
    back = ss.recover_interrupted(w.out)
    assert [s for s, _f in back] == [STAMP] and w.has(OLD) and w.has(OLD, ".xml")
    assert ss.recover_interrupted(w.out) == [], "settled once"


def test_a_file_in_use_leaves_its_base_whole(w, monkeypatch):
    """All or nothing per base: the files already moved go back."""
    w.record({OLD: "Old Mod"})
    real = os.replace

    def _replace(a, b):
        if str(a).endswith("cuirass_1.nif") and "_superseded" not in str(a):
            raise PermissionError(13, "in use")
        return real(a, b)
    monkeypatch.setattr(ss.os, "replace", _replace)
    assert _sweep(w, [_vanilla(w)]) >= 1
    for s in WHOLE:
        assert w.has(OLD, s), s
    assert ss.pending() is None


def test_a_base_that_cannot_go_back_is_named_torn(w, monkeypatch):
    w.record({OLD: "Old Mod"})
    real = os.replace

    def _replace(a, b):
        if str(a).endswith("cuirass_1.nif") or "_superseded" in str(a):
            raise PermissionError(13, "in use")
        return real(a, b)
    monkeypatch.setattr(ss.os, "replace", _replace)
    files = sorted(w.ube.glob("armor/old/*"))
    moved, err, torn = ss.move_group(files, w.out, w.out / "_superseded" / "x")
    assert moved == [] and "PermissionError" in err and torn


def test_an_existing_stamp_folder_is_never_reused(w):
    (w.out / "_superseded" / STAMP).mkdir(parents=True)
    assert ss.new_stamp_dir(w.out, STAMP).name == f"{STAMP}-2"


def test_a_second_run_finds_nothing_to_move(w):
    w.record({OLD: "Old Mod"})
    results = [_vanilla(w)]
    _sweep(w, results)
    _finish(w, results)
    before = sorted(p.name for p in (w.out / "_superseded").iterdir())
    _sweep(w, results)
    assert ss.pending() is None
    assert sorted(p.name for p in (w.out / "_superseded").iterdir()) == before


# ============================================================ what may move

def test_a_source_that_ran_and_gave_a_positive_reason_moves_its_base(w):
    w.mod("Set Mod")
    w.record({OLD: "Set Mod"})
    results = [_vanilla(w), (w.mods / "Set Mod",
                             _result(w.mods / "Set Mod", reasons={OLD: "third-party covered"}),
                             None)]
    _sweep(w, results)
    assert not w.has(OLD) and w.moved(OLD).is_file()


def test_a_source_that_ran_and_resolved_nothing_holds_its_bases(w):
    """Absence is the signature of a failure too: no reason, no move."""
    w.mod("Set Mod")
    w.record({OLD: "Set Mod"})
    results = [_vanilla(w), (w.mods / "Set Mod", _result(w.mods / "Set Mod"), None)]
    _sweep(w, results)
    assert w.has(OLD) and ss.pending() is None
    row = next(d for d in w.report()["bases"] if d["key"] == OLD)
    assert row["action"] == "hold" and "no reason" in row["reason"]


def test_a_reason_that_is_not_a_positive_one_holds(w):
    w.mod("Set Mod")
    w.record({OLD: "Set Mod"})
    results = [_vanilla(w), (w.mods / "Set Mod",
                             _result(w.mods / "Set Mod", reasons={OLD: "crash guard"}), None)]
    _sweep(w, results)
    assert w.has(OLD)


@pytest.mark.parametrize("how", ["removed", "disabled", "excluded"])
def test_a_gone_source_moves_its_bases(w, how):
    enabled, excluded = None, ()
    if how != "removed":
        w.mod("Old Mod")
    if how == "disabled":
        enabled = ["Something Else"]
    if how == "excluded":
        excluded = ("old mod",)
    w.record({OLD: "Old Mod"})
    results = [_vanilla(w)]
    _sweep(w, results, _args(w, results, enabled=enabled, excluded=excluded))
    assert not w.has(OLD)
    assert next(d for d in w.report()["bases"] if d["key"] == OLD)["reason"] == \
        f"source mod {how}"


def test_a_present_source_that_was_not_selected_moves_only_on_a_reason(w, monkeypatch):
    """Enabled and present but not a source this run: its planner is asked,
    with the batch's own inputs, why it no longer plans the base."""
    w.mod("Old Mod")
    w.record({OLD: "Old Mod"})
    results = [_vanilla(w)]
    seen = {}

    def _plan(mod_dir, **k):
        seen.update(k)
        k["drop_reasons"][OLD] = "non-playable"
        return set()
    monkeypatch.setattr(ac, "_player_armor_mesh_bases", _plan)
    worn = frozenset({("x.esp", 1)})
    _sweep(w, results, state=_state(npc_worn=worn))
    assert not w.has(OLD) and seen["npc_worn_armos"] is worn


def test_a_present_source_that_was_not_selected_holds_without_a_reason(w, monkeypatch):
    w.mod("Old Mod")
    w.record({OLD: "Old Mod"})
    monkeypatch.setattr(ac, "_player_armor_mesh_bases", lambda d, **k: {OLD})
    _sweep(w, [_vanilla(w)])
    assert w.has(OLD)


def test_an_unrecorded_base_never_moves(w):
    """No manifest entry: not ours as far as any run knows (a first run, or a
    file placed by hand)."""
    w.record({})
    results = [_vanilla(w)]
    _sweep(w, results)
    assert w.has(OLD)
    row = next(d for d in w.report()["bases"] if d["key"] == OLD)
    assert row["action"] == "hold" and row["source"] is None


def test_the_first_run_only_reports_and_records_what_it_can(w):
    """No manifest yet: report-only by construction. A base a source of this
    run gives a positive reason for is recorded now, and the next run moves it."""
    w.mod("Set Mod")
    results = [_vanilla(w), (w.mods / "Set Mod",
                             _result(w.mods / "Set Mod", reasons={OLD: "female-only"}), None)]
    _sweep(w, results)
    assert w.has(OLD) and ss.pending() is None
    assert any("no earlier run" in why for why in w.report()["report_only"])
    _finish(w, results)
    assert w.manifest()["bases"] == {IRON: "vanilla", OLD: "Set Mod"}
    _sweep(w, results)
    assert not w.has(OLD)


def test_a_claimed_base_is_never_swept(w):
    """Claimed this run -- here through its `_1` only, so the `_0` the partner
    fill wrote belongs to it too and stays."""
    w.record({OLD: "Old Mod"})
    claimed = [(w.ube / "armor" / "old" / "cuirass_1.nif").resolve()]
    _sweep(w, [_vanilla(w)], claimed=claimed)
    assert w.has(OLD, "_0.nif") and w.has(OLD, "_1.nif")


def test_a_claim_matches_whatever_the_case(w):
    """The game's paths are case-blind; a claim written in other case still owns
    the base."""
    w.record({OLD: "Old Mod"})
    _sweep(w, [_vanilla(w)], claimed=[w.ube.resolve() / "Armor" / "OLD" / "Cuirass_1.NIF"])
    assert w.has(OLD)


def test_a_base_left_to_its_builder_is_not_swept(w):
    """#skip-built-ube-path owns it: moved already, or warned about."""
    w.record({OLD: "Old Mod"})
    results = [_vanilla(w), (w.mods / "X", _result(w.mods / "X",
                                                   superseded_weight_bases={OLD}), None)]
    _sweep(w, results)
    assert w.has(OLD)


def test_a_morph_file_goes_with_the_mesh_it_was_named_after(w):
    """The converter names the .tri after the mesh stem without _0/_1, so a mesh
    whose name carries a second `.nif` (`x.nif_1.nif`) has `x.nif.tri`: one base."""
    w.mesh("armor/odd/gloves.nif", "_0.nif", "_1.nif", ".tri")
    inv = ss.inventory(w.out)
    assert sorted(p.name for p in inv.bases["armor/odd/gloves.nif"]) == [
        "gloves.nif.tri", "gloves.nif_0.nif", "gloves.nif_1.nif"]
    assert "armor/odd/gloves" not in inv.bases
    assert sorted(p.name for p in inv.bases[OLD]) == sorted(
        "cuirass" + s for s in WHOLE)


def test_a_file_of_another_type_is_left_alone_and_listed(w):
    w.record({OLD: "Old Mod"})
    (w.ube / "armor" / "old" / "notes.txt").write_bytes(b"x")
    _sweep(w, [_vanilla(w)])
    assert (w.ube / "armor" / "old" / "notes.txt").is_file() and not w.has(OLD)
    assert w.report()["other_files"] == ["armor/old/notes.txt"]


def test_a_file_whose_real_path_leaves_the_output_holds_its_base(w, tmp_path):
    outside = tmp_path / "elsewhere.tri"
    outside.write_bytes(b"x")
    link = w.ube / "armor" / "old" / "cuirass.tri"
    link.unlink()
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks on this machine")
    w.record({OLD: "Old Mod"})
    _sweep(w, [_vanilla(w)])
    assert w.has(OLD) and outside.is_file()


# ============================================================ the gates

def test_the_brake_moves_nothing_past_the_limit(w):
    many = [f"armor/gone/p{i:02d}" for i in range(ss.BRAKE_FLOOR + 1)]
    for b in many:
        w.mesh(b, "_1.nif")
    w.record({b: "Old Mod" for b in many} | {OLD: "Old Mod"})
    assert _sweep(w, [_vanilla(w)]) >= 1
    assert all(w.has(b) for b in many) and w.has(OLD)
    assert any("brake" in why for why in w.report()["report_only"])


def test_under_the_brake_everything_decided_moves(w):
    few = [f"armor/gone/p{i:02d}" for i in range(ss.BRAKE_FLOOR - 1)]
    for b in few:
        w.mesh(b, "_1.nif")
    w.record({b: "Old Mod" for b in few} | {OLD: "Old Mod"})
    _sweep(w, [_vanilla(w)])
    assert not any(w.has(b) for b in few) and not w.has(OLD)


def test_the_brake_scales_with_the_output():
    assert ss.brake_limit(0) == ss.BRAKE_FLOOR
    assert ss.brake_limit(1973) == 49


def _gap(name, w, monkeypatch):
    """(results, args, state) for a run with one shrink-direction fallback."""
    results = [_vanilla(w)]
    state = _state()
    args = None
    if name == "mesh-index":
        state["mesh_index"] = None
    elif name == "archive-index":
        state["bsa"] = (False, [])
    elif name == "archive-unreadable":
        state["bsa"] = (True, ["Broken.bsa"])
    elif name == "npc-worn":
        state["npc_worn"] = None
    elif name == "winner-map":
        state["winner_np"] = None
    elif name == "winner-unreadable":
        monkeypatch.setitem(ac._ARMO_WINNER_CACHE, ("k",), {})
        monkeypatch.setitem(ac._ARMO_WINNER_UNREADABLE, ("k",), ["Junk.esp"])
    elif name == "no-vanilla":
        results = []
    elif name == "vanilla-claims-nothing":
        results = [_vanilla(w, claims=())]
    elif name == "warning":
        state["warns"] += 1
    elif name == "failed-source":
        results.append((w.mods / "X", None, RuntimeError("boom")))
    elif name == "mesh-error":
        bad = nif_convert.ConvertResult(Path("a.nif"), None, "error", "boom")
        results.append((w.mods / "X", _result(w.mods / "X", nif_results=[bad]), None))
    elif name == "plugin-failed":
        results.append((w.mods / "X", _result(w.mods / "X", esp_gen_failures=["x.esp"]), None))
    elif name == "unreadable-output":
        results.append((w.mods / "X", _result(w.mods / "X",
                                              nif_load_failures=[Path("a.nif")]), None))
    elif name == "crash-class":
        results.append((w.mods / "X", _result(w.mods / "X",
                                              nif_invariant_warnings=["zero verts"]), None))
    elif name == "source-did-not-run":
        args = _args(w, results)
        args.sources = list(args.sources) + [w.mods / "Y"]
    return results, args, state


GAPS = ["mesh-index", "archive-index", "archive-unreadable", "npc-worn", "winner-map",
        "winner-unreadable", "no-vanilla", "vanilla-claims-nothing", "warning",
        "failed-source", "mesh-error", "plugin-failed", "unreadable-output",
        "crash-class", "source-did-not-run"]


@pytest.mark.parametrize("gap", GAPS)
def test_an_incomplete_plan_moves_nothing_and_says_so(w, monkeypatch, capsys, gap):
    w.record({OLD: "Old Mod"})
    results, args, state = _gap(gap, w, monkeypatch)
    assert _sweep(w, results, args, state) >= 1
    assert w.has(OLD) and ss.pending() is None
    rep = w.report()
    assert rep["plan_complete"] is False and rep["plan_gaps"]
    assert "!! stale-output sweep: 1 old conversion(s) NOT moved" in capsys.readouterr().out


def test_a_complete_plan_is_complete(w):
    """The control for every gate above: the same run, nothing missing."""
    w.record({OLD: "Old Mod"})
    _sweep(w, [_vanilla(w)])
    assert w.report()["plan_complete"] is True and not w.has(OLD)


def test_switched_off_the_worn_set_is_no_gap(w, monkeypatch):
    monkeypatch.setenv("CBBE2UBE_NO_NPC_WORN_NONPLAYABLE", "1")
    w.record({OLD: "Old Mod"})
    _sweep(w, [_vanilla(w)], state=_state(npc_worn=None))
    assert not w.has(OLD)


def test_a_run_of_selected_mods_moves_nothing_and_still_records(w, capsys):
    w.record({OLD: "Old Mod"})
    results = [_vanilla(w)]
    args = _args(w, results, all_mods=False)
    assert _sweep(w, results, args) == 0
    assert w.has(OLD) and not (w.out / "_superseded").exists()
    assert "only a run of all mods" in capsys.readouterr().out
    _finish(w, results, args)
    assert w.manifest()["bases"] == {IRON: "vanilla", OLD: "Old Mod"}


def test_a_plugins_only_refresh_moves_nothing(w):
    w.record({OLD: "Old Mod"})
    results = [_vanilla(w)]
    _sweep(w, results, _args(w, results, plugins_only=True))
    assert w.has(OLD)


def test_report_only_lists_and_moves_nothing(w, monkeypatch):
    monkeypatch.setenv(REPORT_ONLY, "1")
    w.record({OLD: "Old Mod"})
    results = [_vanilla(w)]
    _sweep(w, results)
    assert w.has(OLD) and ss.pending() is None
    rep = w.report()
    assert [d["action"] for d in rep["bases"] if d["key"] == OLD] == ["move"]
    assert rep["moved_to"] is None
    _finish(w, results)
    assert (w.out / ss.MANIFEST_NAME).is_file()


def test_switched_off_there_is_no_manifest_no_report_and_no_move(w, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    results = [_vanilla(w)]
    assert _sweep(w, results) == 0 and _finish(w, results) == 0
    assert w.has(OLD)
    assert not (w.out / ss.MANIFEST_NAME).exists()
    assert not (w.out / "_superseded").exists()


def test_a_run_without_the_sweep_context_writes_nothing(w):
    """`convert` on its own (no `auto`) passes no context."""
    results = [_vanilla(w)]
    args = _args(w, results)
    del args.stale_sweep
    assert _sweep(w, results, args) == 0 and _finish(w, results, args) == 0
    assert not (w.out / ss.MANIFEST_NAME).exists()


# ============================================================ per-source patches

def _patch_set(w, name):
    w.patches.mkdir(parents=True, exist_ok=True)
    for s in ("", ".espgen.json", ".skypatcher.json"):
        (w.patches / (name + s)).write_bytes(b"x")


def test_a_patch_set_of_a_removed_source_moves_with_its_sidecars(w):
    old, cur = "Old (CBBEtoUBE src).esp", "Iron (CBBEtoUBE src).esp"
    _patch_set(w, old)
    _patch_set(w, cur)
    (w.patches / "UBE_ModBody_Coverage UBE patch.esp").write_bytes(b"x")
    w.record({OLD: "Old Mod"}, {old: "Old Mod"})
    results = [(w.data, _result(w.data, (IRON,), esps=[w.patches / cur]), None)]
    _sweep(w, results)
    dest = w.out / "_superseded" / STAMP / "_unmerged_patches"
    for s in ("", ".espgen.json", ".skypatcher.json"):
        assert (dest / (old + s)).is_file() and not (w.patches / (old + s)).exists()
        assert (w.patches / (cur + s)).is_file()
    assert (w.patches / "UBE_ModBody_Coverage UBE patch.esp").is_file()
    _finish(w, results, merged=False)
    assert (w.patches / old).is_file(), "put back with the meshes"


def test_a_patch_set_of_a_source_that_ran_stays(w):
    old = "Old (CBBEtoUBE src).esp"
    _patch_set(w, old)
    w.mod("Old Mod")
    w.record({}, {old: "Old Mod"})
    _sweep(w, [_vanilla(w), (w.mods / "Old Mod", _result(w.mods / "Old Mod"), None)])
    assert (w.patches / old).is_file()


def test_patches_at_the_mod_root_are_never_moved(w):
    old = "Old (CBBEtoUBE src).esp"
    w.out.mkdir(parents=True, exist_ok=True)
    (w.out / old).write_bytes(b"x")
    w.record({}, {old: "Old Mod"})
    results = [_vanilla(w)]
    ac._stale_output_sweep(_args(w, results), w.out, w.out, results, set(), _state())
    assert (w.out / old).is_file()


# ============================================================ the manifest

def test_the_manifest_records_claims_patches_and_carries_what_is_on_disk(w):
    w.record({OLD: "Old Mod", "armor/gone/x": "Gone Mod"}, {"Gone.esp": "Gone Mod"})
    results = [(w.data, _result(w.data, (IRON,), esps=[w.patches / "Iron.esp"]), None)]
    _finish(w, results, _args(w, results, all_mods=False))
    m = w.manifest()
    assert m["format"] == 1 and m["run_stamp"] == STAMP and "build" in m
    assert m["bases"] == {IRON: "vanilla", OLD: "Old Mod"}, "gone from disk: forgotten"
    assert m["patches"] == {"Iron.esp": "vanilla"}


def test_a_claim_this_run_replaces_the_recorded_source(w):
    w.record({IRON: "Old Mod"})
    results = [_vanilla(w)]
    _finish(w, results, _args(w, results, all_mods=False))
    assert w.manifest()["bases"][IRON] == "vanilla"


@pytest.mark.parametrize("raw", [b"{", b"[]", b'{"format": 9, "bases": {}}'])
def test_an_unreadable_manifest_records_nothing_that_can_move(w, raw):
    w.out.mkdir(parents=True, exist_ok=True)
    (w.out / ss.MANIFEST_NAME).write_bytes(raw)
    m, problem = ss.read_manifest(w.out)
    assert m is None and problem
    _sweep(w, [_vanilla(w)])
    assert w.has(OLD)


# ============================================================ the planner's reasons

def _one_mod(tmp_path, armas, armos):
    mod = tmp_path / "mods" / "Armour Mod"
    (mod / "meshes").mkdir(parents=True)
    _plugin(mod / "Armour.esp", ARMA=armas, ARMO=armos)
    return mod


def _arma(fid, *models, race=npcw.DEFAULT_RACE):
    p = encode_subrecord(b"EDID", encode_zstring(f"AA{fid:X}"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", npcw.BODY, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", race))
    for sig, m in models:
        p += encode_subrecord(sig, encode_zstring(m))
    return _rec(b"ARMA", fid, p)


def _plan(mod, **k):
    why: dict = {}
    got = ac._player_armor_mesh_bases(mod, include_candidate_slots=True,
                                      drop_reasons=why, **k)
    assert got == ac._player_armor_mesh_bases(mod, include_candidate_slots=True, **k), \
        "asking for reasons never changes the plan"
    return got, why


def test_a_non_playable_armature_gives_its_reason(tmp_path):
    mod = _one_mod(tmp_path, [_arma(0x01000800, (b"MOD3", "armor\\gore\\cut_1.nif"))],
                   [_armo(0x01000900, 0x01000800, nonplayable=True)])
    assert _plan(mod) == (set(), {"armor/gore/cut": "non-playable"})


def test_a_third_party_covered_armature_gives_its_reason(tmp_path):
    mod = _one_mod(tmp_path, [_arma(0x01000800, (b"MOD3", "armor\\set\\body_1.nif"))],
                   [_armo(0x01000900, 0x01000800, nonplayable=False)])
    assert _plan(mod, ube_covered_armos={("armour.esp", 0x000900)}) == (
        set(), {"armor/set/body": "third-party covered"})


def test_the_male_mesh_the_female_only_rule_drops_gives_its_reason(tmp_path):
    mod = _one_mod(tmp_path, [_arma(0x01000800, (b"MOD2", "armor\\set\\m\\body_1.nif"),
                                    (b"MOD3", "armor\\set\\f\\body_1.nif"))],
                   [_armo(0x01000900, 0x01000800, nonplayable=False)])
    assert _plan(mod, mesh_resolves=lambda b: b == "armor/set/f/body") == (
        {"armor/set/f/body"}, {"armor/set/m/body": "female-only"})


def test_a_base_another_armature_plans_has_no_reason(tmp_path):
    shared = (b"MOD3", "armor\\set\\body_1.nif")
    mod = _one_mod(tmp_path, [_arma(0x01000800, shared), _arma(0x01000801, shared)],
                   [_armo(0x01000900, 0x01000800, nonplayable=True),
                    _armo(0x01000901, 0x01000801, nonplayable=False)])
    assert _plan(mod) == ({"armor/set/body"}, {})


def test_an_armature_another_rule_rejects_gives_no_reason(tmp_path):
    """A non-playable armature of another race: the race rule took it out
    first, so 'non-playable' is not why it is absent."""
    mod = _one_mod(tmp_path, [_arma(0x01000800, (b"MOD3", "armor\\x\\body_1.nif"),
                                    race=npcw.NORD)],
                   [_armo(0x01000900, 0x01000800, nonplayable=True)])
    assert _plan(mod) == (set(), {})


def test_the_planner_records_its_claims_and_reasons(tmp_path, monkeypatch):
    mod = npcw._outfit_mod(tmp_path)
    worn = npcw._worn(mod / "Outfit.esp")
    monkeypatch.setattr(ac, "_nif_convert_worker",
                        lambda it: nif_convert.ConvertResult(it[0], it[1], "converted (copy)"))
    ref = tmp_path / "ref.nif"
    ref.write_bytes(b"x")
    r = ac.auto_convert_mod(mod, tmp_path / "out", ube_body_ref_path=ref,
                            master_data_dirs=[], nif_workers=1, npc_worn_armos=worn,
                            claimed_dst_paths=set(),
                            built_ube_twin=_twin_of("armor/outfit/boots_1.nif"))
    assert r.claimed_weight_bases == {"armor/outfit/dress"}
    assert r.dropped_base_reasons == {"armor/outfit/severed": "non-playable",
                                      "armor/outfit/boots": "built twin"}


def test_a_collision_is_the_earlier_sources_claim(tmp_path, monkeypatch):
    mod = npcw._outfit_mod(tmp_path)
    monkeypatch.setattr(ac, "_nif_convert_worker",
                        lambda it: nif_convert.ConvertResult(it[0], it[1], "converted (copy)"))
    ref = tmp_path / "ref.nif"
    ref.write_bytes(b"x")
    out = tmp_path / "out"
    taken = {(out / "meshes" / "!UBE" / "armor" / "outfit" / "boots_1.nif").resolve()}
    r = ac.auto_convert_mod(mod, out, ube_body_ref_path=ref, master_data_dirs=[],
                            nif_workers=1, claimed_dst_paths=taken)
    assert "armor/outfit/boots" not in r.claimed_weight_bases


# ============================================================ fallbacks now visible

def test_an_archive_the_index_cannot_list_is_named(tmp_path):
    d = tmp_path / "Mod"
    d.mkdir()
    (d / "Broken.bsa").write_bytes(b"not an archive")
    idx = ac._BsaMeshIndex([d], None)
    assert idx.contains("armor/x_1.nif") is False
    assert idx.skipped == ["Broken.bsa"]
    other = ac._BsaMeshIndex([d], None)
    assert other.adopt_listing(idx) and other.skipped == ["Broken.bsa"]
    assert ac._stale_bsa_state(other) == (True, ["Broken.bsa"])
    assert ac._stale_bsa_state(None) == (False, [])


def test_the_playability_map_names_the_plugins_it_could_not_read(tmp_path, monkeypatch):
    lay = paths.Layout(mods_root=tmp_path, instance_dir=tmp_path)
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: lay)
    monkeypatch.setattr(paths, "active_plugins_ordered", lambda l: ["Junk.esp"])
    monkeypatch.setattr(paths, "enabled_mods_ordered", lambda l: ["Junk Mod"])
    monkeypatch.setattr(paths, "_plugin_file_index_root",
                        lambda l: {"junk.esp": tmp_path / "Junk.esp"})
    monkeypatch.setattr(ac, "_armo_winner_nonplayable", lambda p: ({}, ["Junk.esp"]))
    assert ac._batch_armo_winner_nonplayable() == {}
    assert ac._armo_winner_unreadable() == ["Junk.esp"]


def test_only_problem_warnings_are_counted():
    n = user_warnings.problem_count()
    user_warnings.warn("x", consequence="y", level=user_warnings.NOTE)
    assert user_warnings.problem_count() == n
    user_warnings.warn("x", consequence="y")
    assert user_warnings.problem_count() == n + 1


# ============================================================ where it runs

def _drive_convert(tmp_path, monkeypatch, *, coverage_ok=True):
    """`_cmd_convert` with one converted source and every step after the batch
    stubbed to record its turn."""
    from tests.test_run_warnings_reach_the_tally import _ns
    from src import preflight as pf
    mods = tmp_path / "mods"
    mods.mkdir()
    src = tmp_path / "SomeMod"
    src.mkdir()
    out = tmp_path / "out"
    for var in ("CBBE2UBE_MO2_INI", "CBBE2UBE_GAME_DATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CBBE2UBE_MODS_ROOT", str(mods))
    monkeypatch.setenv("CBBE2UBE_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.setenv("CBBE2UBE_RUN_LOG", str(tmp_path / "run.log"))
    monkeypatch.setattr(ac.paths, "enabled_mods", lambda lay: set())
    monkeypatch.setattr(ac, "_third_party_ube_covered_armos", lambda *a, **k: set())
    monkeypatch.setattr(pf, "_locate_in_mods_or_data", lambda *a, **k: tmp_path / "x.dll")
    monkeypatch.setattr(pf, "_skypatcher_armor_patching", lambda p: True)
    order = []

    def _converted(source_dir, *a, **k):
        (out / "_unmerged_patches").mkdir(parents=True, exist_ok=True)
        (out / "_unmerged_patches" / "SomeMod (CBBEtoUBE src).esp").write_bytes(b"")
        return ac.AutoConvertResult(source_dir=Path(source_dir), output_dir=out)

    def _coverage(*a, **k):
        order.append("coverage")
        (out / "_unmerged_patches" / "UBE_ModBody_Coverage UBE patch.esp").write_bytes(b"")
        return coverage_ok, 1, True
    monkeypatch.setattr(ac, "auto_convert_mod", _converted)
    monkeypatch.setattr(ac, "_complete_weight_partners",
                        lambda *a, **k: order.append("fill") or (0, 0))
    monkeypatch.setattr(ac.ube_patcher, "restore_female_models",
                        lambda *a, **k: order.append("restore") or {})
    monkeypatch.setattr(ac, "_emit_unified_coverage_patches", _coverage)
    monkeypatch.setattr(ac.ube_patcher, "merge_patches_split",
                        lambda *a, **k: order.append("merge") or {})
    monkeypatch.setattr(ac, "_stale_output_sweep",
                        lambda *a, **k: order.append("sweep") or 0)
    monkeypatch.setattr(ac, "_stale_output_sweep_finish",
                        lambda *a, merged, **k: order.append(("finish", merged)) or 0)
    monkeypatch.setattr(ac, "_stale_output_sweep_failover",
                        lambda *a, **k: order.append("failover") or 0)
    real_list = ac._per_source_patch_paths
    monkeypatch.setattr(ac, "_per_source_patch_paths",
                        lambda d: order.append("list") or real_list(d))
    ac._cmd_convert(_ns([src], out))
    return order


def test_the_sweep_runs_after_the_fill_and_before_the_restore_and_coverage(
        tmp_path, monkeypatch):
    order = _drive_convert(tmp_path, monkeypatch)
    assert order == ["fill", "sweep", "restore", "coverage", "merge", ("finish", True)]


def test_a_merge_from_the_per_source_fallback_does_not_confirm_the_moves(
        tmp_path, monkeypatch):
    order = _drive_convert(tmp_path, monkeypatch, coverage_ok=False)
    assert order[-1] == ("finish", False)


def test_the_fallback_puts_the_moves_back_before_it_lists_the_patches(
        tmp_path, monkeypatch):
    """Coverage failed: the per-source patches are listed only after every moved
    file is back; a merge from coverage never fails over."""
    order = _drive_convert(tmp_path, monkeypatch, coverage_ok=False)
    assert order[order.index("coverage"):] == [
        "coverage", "failover", "list", "merge", ("finish", False)]
    (tmp_path / "ok").mkdir()
    assert "failover" not in _drive_convert(tmp_path / "ok", monkeypatch)


def _auto(tmp_path, monkeypatch, fake_convert, **kw):
    import types
    monkeypatch.setattr(ac.paths, "discover_layout",
                        lambda: types.SimpleNamespace(game_data_dirs=[]))
    monkeypatch.setattr(ac.paths, "export_to_env", lambda lay: None)
    monkeypatch.setattr(ac.paths, "mods_root", lambda: tmp_path)
    monkeypatch.setattr(ac.paths, "enabled_mods", lambda lay: None)
    monkeypatch.setattr(ac.paths, "enabled_mods_ordered", lambda lay: [])
    monkeypatch.setattr(ac, "_body_mod_names", lambda mr: set())
    monkeypatch.setattr(ac, "_drop_ube_native_candidates", lambda c: c)
    fake = [{"name": "ModA", "path": tmp_path / "ModA", "armor_nifs": 1, "esps": 1},
            {"name": "ModB", "path": tmp_path / "ModB", "armor_nifs": 1, "esps": 1}]
    monkeypatch.setattr(ac, "_find_armor_mod_dirs", lambda *a, **k: list(fake))
    monkeypatch.setattr(ac, "_cmd_convert", fake_convert)
    args = argparse.Namespace(output=tmp_path / "out", workers=1, no_textures=False,
                              merged_name="C.esp", list_only=False, **kw)
    return ac._cmd_auto(args)


class _Stop(Exception):
    pass


@pytest.mark.parametrize("only, full", [(None, True), (["ModA"], False)])
def test_auto_says_whether_the_run_is_of_all_mods(tmp_path, monkeypatch, only, full):
    seen = {}

    def _conv(conv):
        seen.update(conv.stale_sweep)
        raise _Stop()
    with pytest.raises(_Stop):
        _auto(tmp_path, monkeypatch, _conv, only_mods=only)
    assert seen["all_mods"] is full
    assert seen["mods_root"] == str(tmp_path) and "out" in seen["excluded"]


def test_auto_puts_back_what_a_crashed_convert_moved(tmp_path, monkeypatch):
    w = World(tmp_path / "w")
    w.mesh(OLD, "_1.nif")
    w.record({OLD: "Old Mod"})

    def _conv(conv):
        _sweep(w, [_vanilla(w)])
        assert not w.has(OLD)
        raise _Stop()
    with pytest.raises(_Stop):
        _auto(tmp_path, monkeypatch, _conv, only_mods=None)
    assert w.has(OLD) and ss.pending() is None


# ============================================================ the fallback merge

OLD_PATCH, CUR_PATCH = "Old (CBBEtoUBE src).esp", "Iron (CBBEtoUBE src).esp"
IRON_M = "!UBE\\armor\\iron\\cuirass_1.nif"
OLD_M = "!UBE\\armor\\old\\cuirass_1.nif"


def _patch_esp(w, name, *models, fallbacks=None, where=None):
    """A per-source patch set whose armatures name `models`, one each."""
    folder = where if where is not None else w.patches
    folder.mkdir(parents=True, exist_ok=True)
    recs = [_rec(b"ARMA", 0x01000800 + i, encode_subrecord(b"MOD3", encode_zstring(m)))
            for i, m in enumerate(models)]
    _plugin(folder / name, ARMA=recs)
    (folder / (name + ".espgen.json")).write_bytes(b"{}")
    if fallbacks is not None:
        (folder / (name + ".male_fallbacks.json")).write_bytes(
            json.dumps(fallbacks).encode())
    return folder / name


def _snapshot(w) -> dict:
    """Every file of the output a run without the sweep has too, by content."""
    out = {}
    for p in sorted(w.out.rglob("*")):
        rel = p.relative_to(w.out).as_posix()
        if p.is_file() and not rel.startswith("_superseded/") and rel != ss.MANIFEST_NAME:
            out[rel] = p.read_bytes()
    return out


def _missing_meshes(w, patch_paths) -> list:
    """The `!UBE` meshes the armatures of `patch_paths` name that are not on disk:
    each one a missing-mesh crash in a Combined merged from them."""
    return [m for p in patch_paths for m, _b in ss._ube_models(p)
            if not (w.out / "meshes" / m.replace("\\", "/")).is_file()]


def _fallback_world(w):
    """A removed source's base and its patch set (which names it) beside the
    patch set of a source that ran: the sweep moves the first two."""
    _patch_esp(w, OLD_PATCH, OLD_M)
    _patch_esp(w, CUR_PATCH, IRON_M)
    w.record({OLD: "Old Mod"}, {OLD_PATCH: "Old Mod"})
    return [(w.data, _result(w.data, (IRON,), esps=[w.patches / CUR_PATCH]), None)]


def test_a_fallback_merge_takes_what_a_run_without_the_sweep_takes(w, capsys):
    """Coverage failed: every moved file is back before the per-source patches
    are listed, so the fallback merges the moved patch set, as a run without
    the sweep does, and says so."""
    results = _fallback_world(w)
    without, before = ac._per_source_patch_paths(w.patches), _snapshot(w)
    _sweep(w, results)
    assert not w.has(OLD) and not (w.patches / OLD_PATCH).exists(), "the control: moved"
    assert ac._stale_output_sweep_failover(w.out, w.patches) >= 1
    assert ss.pending() is None
    assert ac._per_source_patch_paths(w.patches) == without and _snapshot(w) == before
    assert "falls back to the per-source patches" in capsys.readouterr().out
    assert w.report()["moved_to"] is None
    assert _finish(w, results, merged=False) == 0, "nothing is left to put back"
    assert w.manifest()["bases"][OLD] == "Old Mod", "still recorded: a later run moves it"


def test_a_fallback_without_moves_changes_nothing(w):
    results = _fallback_world(w)
    before = _snapshot(w)
    assert ac._stale_output_sweep_failover(w.out, w.patches) == 0
    assert _snapshot(w) == before


def test_the_fallback_restore_ends_where_one_pass_over_the_whole_folder_does(
        tmp_path, monkeypatch):
    """The female-model restore ran while a moved female mesh was away; after
    the put-back it runs again, and the patch is byte for byte the one a run
    without the sweep leaves."""
    fem = "armor/old/f/cuirass"
    fb = [{"orig": "armor\\old\\f\\cuirass_1.nif", "slot": "MOD3", "fid": 0x01000800,
           "to": IRON_M}]
    snaps = {}
    for sweep in (False, True):
        world = World(tmp_path / str(sweep))
        world.mesh(IRON, "_0.nif", "_1.nif")
        world.mesh(fem, "_0.nif", "_1.nif")
        _patch_esp(world, CUR_PATCH, IRON_M, fallbacks=fb)
        world.record({fem: "Old Mod"})
        results = [(world.data, _result(world.data, (IRON,),
                                        esps=[world.patches / CUR_PATCH]), None)]
        if sweep:
            monkeypatch.delenv(OFF, raising=False)
        else:
            monkeypatch.setenv(OFF, "1")
        _sweep(world, results)
        ac.ube_patcher.restore_female_models(world.patches, world.out)
        if sweep:
            assert not world.has(fem) and [b for _m, b in ss._ube_models(
                world.patches / CUR_PATCH)] == [IRON], "the control: restored nothing"
        ac._stale_output_sweep_failover(world.out, world.patches)
        snaps[sweep] = _snapshot(world)
    assert snaps[True] == snaps[False]
    assert [b for _m, b in ss._ube_models(tmp_path / "True" / "mods" / "Output" /
                                          "_unmerged_patches" / CUR_PATCH)] == [fem]


# ============================================================ patches left in place

S_PATCH = "S (CBBEtoUBE src).esp"
B1, B2 = "armor/smod/cuirass", "armor/smod/boots"


def _not_selected_world(w, monkeypatch, reasons):
    """A source selection no longer picks: two recorded bases and its old patch
    set, which names both. Its planner, asked, gives `reasons`."""
    w.mod("S Mod")
    w.mesh(B1, "_0.nif", "_1.nif")
    w.mesh(B2, "_0.nif", "_1.nif")
    _patch_esp(w, S_PATCH, "!UBE\\armor\\smod\\cuirass_1.nif",
               "!UBE\\armor\\smod\\boots_1.nif")
    _patch_esp(w, CUR_PATCH, IRON_M)
    w.record({B1: "S Mod", B2: "S Mod"}, {S_PATCH: "S Mod"})

    def _plan(mod_dir, **k):
        k["drop_reasons"].update(reasons)
        return set()
    monkeypatch.setattr(ac, "_player_armor_mesh_bases", _plan)
    return [(w.data, _result(w.data, (IRON,), esps=[w.patches / CUR_PATCH]), None)]


def test_a_source_whose_old_patch_stays_keeps_its_meshes_across_runs(w, monkeypatch):
    """Run N: the source drops one of its two bases, so its patch set stays --
    and the base stays with it. Run N+1's coverage fails: the fallback merges
    that patch, and every mesh it names is on disk."""
    results = _not_selected_world(w, monkeypatch, {B1: "non-playable"})
    _sweep(w, results)                                  # run N, merged from coverage
    _combined(w, IRON_M)
    _finish(w, results, merged=True)
    assert w.has(B1) and w.has(B2) and (w.patches / S_PATCH).is_file()
    row = next(d for d in w.report()["bases"] if d["key"] == B1)
    assert row["action"] == "hold" and S_PATCH in row["reason"]
    _sweep(w, results)                                  # run N+1, coverage fails
    ac._stale_output_sweep_failover(w.out, w.patches)
    fallback = ac._per_source_patch_paths(w.patches)
    assert w.patches / S_PATCH in fallback
    assert _missing_meshes(w, fallback) == []


def test_a_source_whose_every_base_goes_moves_with_its_patch_set(w, monkeypatch):
    """All or nothing, the other half: every base has a reason, so the patch
    set moves too, and no patch left in place names a moved mesh."""
    results = _not_selected_world(w, monkeypatch, {B1: "non-playable", B2: "non-playable"})
    _sweep(w, results)
    _combined(w, IRON_M)
    assert _finish(w, results, merged=True) == 0
    assert not w.has(B1) and not w.has(B2) and not (w.patches / S_PATCH).exists()
    assert _missing_meshes(w, ac._per_source_patch_paths(w.patches)) == []


@pytest.mark.parametrize("names_it", [True, False])
def test_a_patch_left_in_place_holds_every_base_it_names(w, names_it):
    """An old patch set no run recorded stays; a base one of its armatures
    names stays with it. The control names another mesh: the base moves."""
    _patch_esp(w, "Stray (CBBEtoUBE src).esp", OLD_M if names_it else IRON_M)
    w.record({OLD: "Old Mod"})
    _sweep(w, [_vanilla(w)])
    assert w.has(OLD) is names_it


def test_a_base_held_by_another_patch_keeps_its_whole_source(w, monkeypatch):
    """Every base of the source has a reason, but a stray patch left in place
    names one of them: that base stays, so the source's patch set stays, and so
    does its other base -- decided again until nothing changes."""
    results = _not_selected_world(w, monkeypatch, {B1: "non-playable", B2: "non-playable"})
    _patch_esp(w, "Stray (CBBEtoUBE src).esp", "!UBE\\armor\\smod\\boots_1.nif")
    _sweep(w, results)
    assert w.has(B1) and w.has(B2) and (w.patches / S_PATCH).is_file()
    assert ss.pending() is None


def test_a_patch_left_in_place_that_cannot_be_read_holds_every_move(w):
    w.patches.mkdir(parents=True)
    (w.patches / "Stray (CBBEtoUBE src).esp").write_bytes(b"not a plugin")
    w.record({OLD: "Old Mod"})
    _sweep(w, [_vanilla(w)])
    assert w.has(OLD)
    row = next(d for d in w.report()["bases"] if d["key"] == OLD)
    assert row["action"] == "hold" and "could not be read" in row["reason"]


def test_a_patch_set_that_cannot_move_keeps_its_sources_meshes(w, monkeypatch):
    results = _fallback_world(w)
    real = os.replace

    def _replace(a, b):
        if str(a).endswith(OLD_PATCH) and "_superseded" not in str(a):
            raise PermissionError(13, "in use")
        return real(a, b)
    monkeypatch.setattr(ss.os, "replace", _replace)
    assert _sweep(w, results) >= 1
    assert w.has(OLD) and (w.patches / OLD_PATCH).is_file() and ss.pending() is None
    row = next(d for d in w.report()["bases"] if d["key"] == OLD)
    assert row["action"] == "hold" and OLD_PATCH in row["reason"]


@pytest.mark.parametrize("recorded", [True, False])
def test_patches_at_the_mod_root_keep_their_sources_meshes(w, recorded):
    """Root-write mode never moves a patch, so a source whose patch sits there
    keeps its meshes; the control, a patch no run recorded naming nothing."""
    _patch_esp(w, OLD_PATCH, where=w.out)
    w.record({OLD: "Old Mod"}, {OLD_PATCH: "Old Mod"} if recorded else {})
    results = [_vanilla(w)]
    ac._stale_output_sweep(_args(w, results), w.out, w.out, results, set(), _state())
    assert w.has(OLD) is recorded


# ============================================================ isolation

@pytest.mark.parametrize("bases, patches", [
    ({OLD: ["Old Mod"]}, {}), ({OLD: {"a": 1}}, {}), ({OLD: None}, {}),
    ({OLD: "Old Mod"}, {OLD_PATCH: ["Old Mod"]}),
    ({OLD: ""}, {}), ({OLD: "  "}, {}), ({OLD: "..\\Old Mod"}, {}),
    ({OLD: "Old Mod"}, {OLD_PATCH: ""})])
def test_a_manifest_holding_other_than_names_is_no_manifest(w, capsys, bases, patches):
    """A hand edit or a foreign write: nothing it records moves, a named warning
    says so, and the finish writes the record anew."""
    w.out.mkdir(parents=True, exist_ok=True)
    (w.out / ss.MANIFEST_NAME).write_bytes(json.dumps(
        {"format": 1, "bases": bases, "patches": patches}).encode())
    results = [_vanilla(w)]
    assert _sweep(w, results) >= 1
    assert w.has(OLD) and ss.pending() is None
    assert ("!! stale-output sweep: the conversion manifest has an entry that is not "
            "a mod name") in capsys.readouterr().out
    assert _finish(w, results) == 0
    m, problem = ss.read_manifest(w.out)
    assert problem == "" and m["bases"] == {IRON: "vanilla"}


def test_an_error_mid_move_puts_back_everything_and_the_run_goes_on(w, monkeypatch, capsys):
    """The patch set moved, then a file of the base raised an error no one
    expected, and its own roll-back failed too: every file still comes back
    (the journal lists them), and the finish finds nothing to settle."""
    results = _fallback_world(w)
    before = _snapshot(w)
    real = os.replace
    seen = {"fwd": 0, "back": 0}

    def _replace(a, b):
        mine = "cuirass" in Path(a).name
        if mine and "_superseded" not in str(a):
            seen["fwd"] += 1
            if seen["fwd"] == 2:
                raise RuntimeError("boom")
        elif mine and seen["fwd"] == 2 and not seen["back"]:
            seen["back"] = 1
            raise RuntimeError("roll-back failed")
        return real(a, b)
    monkeypatch.setattr(ss.os, "replace", _replace)
    assert _sweep(w, results) >= 1
    assert seen == {"fwd": 2, "back": 1}, "the control: the error hit mid-base"
    assert ss.pending() is None and _snapshot(w) == before
    assert "stopped by an error (RuntimeError: boom)" in capsys.readouterr().out
    assert _finish(w, results, merged=True) == 0


def test_a_put_back_also_brings_back_a_torn_base(w, monkeypatch):
    """A base whose move and roll-back both failed left one file in the stamp
    folder; when the merge does not confirm the run's moves, that file comes
    back with the rest."""
    results = _fallback_world(w)
    before = _snapshot(w)
    real = os.replace
    seen = {"fwd": 0, "back": 0}

    def _replace(a, b):
        mine = "cuirass" in Path(a).name
        if mine and "_superseded" not in str(a):
            seen["fwd"] += 1
            if seen["fwd"] == 2:
                raise PermissionError(13, "in use")
        elif mine and seen["fwd"] == 2 and not seen["back"]:
            seen["back"] = 1
            raise PermissionError(13, "in use")
        return real(a, b)
    monkeypatch.setattr(ss.os, "replace", _replace)
    _sweep(w, results)
    assert seen == {"fwd": 2, "back": 1} and not w.has(OLD, ".nif"), "the control: torn"
    assert ss.pending() is not None, "the patch set moved"
    _finish(w, results, merged=False)
    assert _snapshot(w) == before


def test_a_file_that_cannot_go_back_is_named_once(tmp_path):
    """The settle passes the moved pairs AND the planned ones, so a pair that
    moved is listed twice; a file that cannot go back is still one file."""
    orig, moved = tmp_path / "a" / "x_1.nif", tmp_path / "s" / "x_1.nif"
    other, other_to = tmp_path / "a" / "y_1.nif", tmp_path / "s" / "y_1.nif"
    for p in (orig, moved, other_to):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"nif")
    pairs = [(orig, moved), (other, other_to)]
    assert ss.put_back(pairs + pairs) == [str(orig)], "taken again: named once"
    assert other.is_file() and not other_to.exists()


def test_a_group_that_raises_puts_its_own_files_back_first(w, monkeypatch):
    """An error that is not a file error still leaves the base whole before it
    propagates."""
    real = os.replace
    seen = {"n": 0}

    def _replace(a, b):
        if "_superseded" not in str(a):
            seen["n"] += 1
            if seen["n"] == 3:
                raise RuntimeError("boom")
        return real(a, b)
    monkeypatch.setattr(ss.os, "replace", _replace)
    files = sorted(w.ube.glob("armor/old/*"))
    with pytest.raises(RuntimeError):
        ss.move_group(files, w.out, w.out / "_superseded" / "x")
    assert seen["n"] == 3 and all(f.is_file() for f in files)


def test_an_error_after_the_moves_puts_them_back(w, monkeypatch):
    results = _fallback_world(w)
    before = _snapshot(w)

    def _boom(*a, **k):
        raise KeyError("listing")
    monkeypatch.setattr(ac, "_stale_print_list", _boom)
    assert _sweep(w, results) == 1
    assert ss.pending() is None and _snapshot(w) == before


def test_an_error_while_deciding_moves_nothing_and_records_no_adoption(w, monkeypatch):
    w.mod("Set Mod")
    results = [_vanilla(w), (w.mods / "Set Mod",
                             _result(w.mods / "Set Mod", reasons={OLD: "female-only"}), None)]
    real = ss.decide_patches

    def _boom(*a, **k):
        raise TypeError("x")
    monkeypatch.setattr(ss, "decide_patches", _boom)
    assert _sweep(w, results) == 1
    monkeypatch.setattr(ss, "decide_patches", real)
    assert w.has(OLD) and ss.pending() is None and ac._STALE_ADOPTED == {}
