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

r"""#sweep-settle-before-postmerge -- the sweep's moves are kept or put back
before the alt-texture reconcile reads `meshes\!UBE`.

THE DEFECT. The stale-output sweep moves old bases out of `meshes\!UBE`
before coverage and the merge, pending confirmation. The alt-texture
reconcile ran right after the merge, while those files were gone, and the
moves were settled only at the end of the merge block. When the new Combined
still named a moved base -- the case the put-back exists for -- the colour
sets of that base were indexed against its absence (kept as authored, or
bound by name to another mod's copy), and then our NIF came back and the game
drew it with those indices.

THE RULE. The moves settle right after the merge writes the Combined, before
the reconcile and the other passes that read `meshes\!UBE`. The decision reads
only the Combined's model paths, which those passes never change, so what is
kept and what goes back is the same.
`CBBE2UBE_NO_SWEEP_SETTLE_BEFORE_POSTMERGE=1` settles at the end, as before.
"""
from pathlib import Path

import pytest

from src import auto_convert as ac
from src import preflight as pf
from src import stale_sweep as ss
from src import user_warnings
from src.esp import encode_subrecord, encode_zstring
from tests.test_run_warnings_reach_the_tally import _ns
from tests.test_selection_winner_playable import _plugin, _rec
from tests.test_stale_output_sweep import (
    IRON, OLD, WHOLE, World, _state, _vanilla)
# The autouse fixture of the sweep's own tests: clean switches and caches.
from tests.test_stale_output_sweep import _clean  # noqa: F401

SWITCH = "CBBE2UBE_NO_SWEEP_SETTLE_BEFORE_POSTMERGE"
IRON_M = "!UBE\\armor\\iron\\cuirass_1.nif"
OLD_M = "!UBE\\armor\\old\\cuirass_1.nif"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)
    monkeypatch.setattr(ac, "_RUN_FAILURES", [])


def _full_run(tmp_path, monkeypatch, *models, coverage_ok=True):
    """`_cmd_convert` on a full run whose sweep moves OLD; the merge writes a
    Combined naming `models`. Returns (world, what each post-merge reader saw,
    the order of the steps)."""
    w = World(tmp_path)
    w.mesh(IRON, "_0.nif", "_1.nif", ".tri")
    w.mesh(OLD, *WHOLE)
    w.record({OLD: "Old Mod", IRON: "vanilla"})
    src = tmp_path / "SomeMod"
    src.mkdir()
    for var in ("CBBE2UBE_MO2_INI", "CBBE2UBE_GAME_DATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CBBE2UBE_MODS_ROOT", str(w.mods))
    monkeypatch.setenv("CBBE2UBE_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.setenv("CBBE2UBE_RUN_LOG", str(tmp_path / "run.log"))
    monkeypatch.setattr(ac.paths, "enabled_mods", lambda lay: set())
    monkeypatch.setattr(ac, "_third_party_ube_covered_armos", lambda *a, **k: set())
    monkeypatch.setattr(pf, "_locate_in_mods_or_data", lambda *a, **k: tmp_path / "x.dll")
    monkeypatch.setattr(pf, "_skypatcher_armor_patching", lambda p: True)
    seen: dict = {}
    order: list = []
    patch = w.patches / "SomeMod (CBBEtoUBE src).esp"

    def _converted(source_dir, *a, **k):
        w.patches.mkdir(parents=True, exist_ok=True)
        _plugin(patch, ARMA=[_rec(b"ARMA", 0x01000800, encode_subrecord(
            b"MOD3", encode_zstring(IRON_M)))])
        r = ac.AutoConvertResult(source_dir=Path(source_dir), output_dir=w.out)
        r.claimed_weight_bases = {IRON}
        r.output_esps = [patch]
        return r

    real_sweep = ac._stale_output_sweep

    def _sweep(args, output, patches_dir, results, claimed, state):
        # The batch's own state and a vanilla sweep: a complete plan (the
        # stand-in batch's own warnings are not the sweep's business here).
        args.stale_sweep["warn_base"] = user_warnings.problem_count()
        n = real_sweep(args, output, patches_dir, [_vanilla(w)] + list(results),
                       claimed, _state(warns=user_warnings.problem_count()))
        order.append("sweep")
        assert not w.has(OLD), "the sweep moved OLD"
        return n

    def _coverage(*a, **k):
        _plugin(w.patches / "UBE_ModBody_Coverage UBE patch.esp")
        return coverage_ok, 1, True

    def _merge(paths, out, **k):
        recs = [_rec(b"ARMA", 0x01000900 + i, encode_subrecord(b"MOD3", encode_zstring(m)))
                for i, m in enumerate(models)]
        _plugin(Path(out), ARMA=recs)
        order.append("merge")
        return {}

    def _reader(name, ret):
        def _read(*a, **k):
            seen[name] = w.has(OLD)
            order.append(name)
            return ret
        return _read

    monkeypatch.setattr(ac, "auto_convert_mod", _converted)
    monkeypatch.setattr(ac, "_complete_weight_partners", lambda *a, **k: (0, 0))
    monkeypatch.setattr(ac, "_stale_output_sweep", _sweep)
    monkeypatch.setattr(ac.ube_patcher, "restore_female_models", lambda *a, **k: {})
    monkeypatch.setattr(ac, "_emit_unified_coverage_patches", _coverage)
    monkeypatch.setattr(ac.ube_patcher, "merge_patches_split", _merge)
    monkeypatch.setattr(ac.ube_patcher, "reconcile_alt_texture_indices_all",
                        _reader("reconcile", 0))
    monkeypatch.setattr(ac.ube_patcher, "fix_spurious_hand_slot",
                        _reader("hands", {}))
    monkeypatch.setattr(ac.ube_patcher, "postflight_validate_combined",
                        _reader("postflight", {"ctd": [], "soft": [], "pieces": []}))
    args = _ns([src], w.out)
    args.merged_name = "Combined.esp"
    args.stale_sweep = {"all_mods": True, "warn_base": user_warnings.problem_count(),
                        "started": 1_700_000_000.0, "mods_root": str(w.mods),
                        "enabled": None, "excluded": []}
    ac._cmd_convert(args)
    return w, seen, order


def test_a_put_back_is_done_before_the_reconcile_reads_the_meshes(tmp_path, monkeypatch):
    """The new Combined still names OLD: its files go back, and the reconcile,
    the hands-slot fix and the postflight all see our NIF."""
    w, seen, order = _full_run(tmp_path, monkeypatch, IRON_M, OLD_M)
    assert order[:2] == ["sweep", "merge"]
    assert seen == {"reconcile": True, "hands": True, "postflight": True}
    assert w.has(OLD) and not w.moved(OLD).exists()
    assert "stale sweep put back" in [e["kind"] for e in ac._RUN_FAILURES]


def test_kept_moves_stay_kept(tmp_path, monkeypatch):
    """The new Combined names only current meshes: the moves are kept, as they
    were when they settled at the end."""
    w, seen, _order = _full_run(tmp_path, monkeypatch, IRON_M)
    assert seen["reconcile"] is False
    assert not w.has(OLD) and w.moved(OLD).is_file()
    journal = w.out / "_superseded" / w.moved(OLD).relative_to(
        w.out / "_superseded").parts[0] / ss.JOURNAL_NAME
    assert '"kept"' in journal.read_text(encoding="utf-8")
    assert w.manifest()["bases"] == {IRON: "SomeMod"}


def test_the_manifest_is_still_written_after_an_early_settle(tmp_path, monkeypatch):
    w, _seen, _order = _full_run(tmp_path, monkeypatch, IRON_M, OLD_M)
    assert w.manifest()["bases"] == {IRON: "SomeMod", OLD: "Old Mod"}


def test_settling_twice_is_a_no_op(tmp_path, monkeypatch):
    """The finish after an early settle finds nothing pending."""
    w, _seen, _order = _full_run(tmp_path, monkeypatch, IRON_M, OLD_M)
    kinds = [e["kind"] for e in ac._RUN_FAILURES]
    assert kinds.count("stale sweep put back") == 1
    assert ss.pending() is None


def test_switched_off_the_reconcile_sees_the_mesh_missing(tmp_path, monkeypatch):
    monkeypatch.setenv(SWITCH, "1")
    w, seen, _order = _full_run(tmp_path, monkeypatch, IRON_M, OLD_M)
    assert seen == {"reconcile": False, "hands": False, "postflight": False}
    assert w.has(OLD), "the finish still puts it back"
