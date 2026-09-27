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

r"""#plan-order-results -- a report lists a source's pieces in plan order.

The merge review of the batch-wide schedule compared conversion_report.json
between the two schedules: every count and set matched, but `pass_effects` and
`pass_failure_pieces` listed pieces in the order their answers arrived
(largest first on the batch-wide schedule, a worker race one source at a
time), and a source's patch-validator notes came after its NIF notes because
its patch is written at its finish. Each source's results are now sorted into
plan order once its NIFs are in, and its patch notes go where one source at a
time writes them.

Pinned here through the real planning steps: the order of the results and of
the effects list on the batch-wide schedule and one source at a time
(switched off, the arrival order is back -- the control); the notes of a
source whose patch writes a note, with a planning note after the patch point,
the same on both schedules; and the sort's own keys.
"""
from pathlib import Path

import pytest

from src import auto_convert as ac
from src.nif_convert import ConvertResult
from tests.test_global_schedule import SWITCH as SCHEDULE, _Pool
from tests.test_npc_worn_nonplayable import load_order  # noqa: F401 (fixture)
from tests.test_planned_folders import _admit

SWITCH = "CBBE2UBE_NO_PLAN_ORDER_RESULTS"
PLAN = ["a_1.nif", "b_1.nif", "c_1.nif"]
_WRITE_PATCHES = ac._write_source_patches


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)
    monkeypatch.delenv(SCHEDULE, raising=False)


def _worker(item):
    Path(item[1]).parent.mkdir(parents=True, exist_ok=True)
    Path(item[1]).write_bytes(b"converted")
    return ConvertResult(src_path=Path(item[0]), dst_path=str(item[1]),
                         status="converted (copy)",
                         reason="CHANGED BY #some-pass (moved 3 verts)")


def _plan(tmp_path, monkeypatch, *, collide=None):
    """One source planning a (small), b (largest), c (middle): largest first
    delivers b, c, a. `collide`: a piece an earlier source already claimed."""
    sizes = {"a_1.nif": 1, "b_1.nif": 4096, "c_1.nif": 512}
    pairs = []
    for name in PLAN + ([collide] if collide else []):
        src = tmp_path / "src" / name
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"x" * sizes.get(name, 1))
        pairs.append((src, f"armor/set/{name}"))
    monkeypatch.setattr(ac, "_resolve_armor_meshes", lambda *a, **k: list(pairs))
    monkeypatch.setattr(ac, "_nif_convert_worker", _worker)
    _admit(monkeypatch)
    ref = tmp_path / "ube_ref.nif"
    ref.write_bytes(b"x")
    return ref


def _convert(load_order, tmp_path, monkeypatch, *, one_schedule, collide=None,
             patch_note=False):
    ref = _plan(tmp_path, monkeypatch, collide=collide)
    if patch_note:
        def _with_a_note(result, *a, **k):
            out = _WRITE_PATCHES(result, *a, **k)
            result.notes.append("patch validator: a note of the patch")
            return out
        monkeypatch.setattr(ac, "_write_source_patches", _with_a_note)
    out = tmp_path / "out"
    claimed: set = set()
    if collide:
        claimed.add((out / "meshes" / "!UBE" / "armor" / "set" / collide).resolve())
    mgr = ac._NifPool(1, pool_factory=lambda: _Pool())
    kw = dict(nif_pool=mgr, claimed_dst_paths=claimed, ube_body_ref_path=ref,
              master_data_dirs=[])
    if one_schedule:
        results: list = []
        ac._convert_sources_global(
            [load_order], results, mgr, claimed,
            make_steps=lambda s: ac._auto_convert_mod_steps(
                s, out, batch_schedule=True, **kw),
            convert_serial=lambda s: pytest.fail("no serial retry here"),
            checkpoint=lambda view, progress: None)
        (_s, r, err), = results
        assert err is None
        return r
    return ac.auto_convert_mod(load_order, out, **kw)


def _names(r):
    return [Path(x.dst_path).name for x in r.nif_results]


@pytest.mark.parametrize("one_schedule", [True, False],
                         ids=["one-schedule", "one-source-at-a-time"])
def test_the_results_and_the_effects_list_are_in_plan_order(
        load_order, tmp_path, monkeypatch, one_schedule):
    r = _convert(load_order, tmp_path, monkeypatch, one_schedule=one_schedule)
    assert _names(r) == PLAN
    assert ac.count_pass_effects(r.nif_results) == {"#some-pass": PLAN}


def test_switched_off_the_results_keep_their_arrival_order(load_order, tmp_path, monkeypatch):
    """The control: this test sees the order. Switched off, the batch-wide
    schedule's largest-first delivery is what the report lists."""
    monkeypatch.setenv(SWITCH, "1")
    r = _convert(load_order, tmp_path, monkeypatch, one_schedule=True)
    assert _names(r) == ["b_1.nif", "c_1.nif", "a_1.nif"]


def _notes(r, base=None):
    """The notes without the NIF phase's own, which carries its timing, and
    with the arm's own folder named alike."""
    return [n.replace(str(base), "<arm>") if base else n for n in r.notes
            if not n.startswith("NIF conversion:")]


def test_the_patch_notes_sit_where_one_source_at_a_time_puts_them(
        load_order, tmp_path, monkeypatch):
    s, b = tmp_path / "s", tmp_path / "b"
    serial = _notes(_convert(load_order, s, monkeypatch, one_schedule=False,
                             collide="d_1.nif", patch_note=True), s)
    batch = _notes(_convert(load_order, b, monkeypatch, one_schedule=True,
                            collide="d_1.nif", patch_note=True), b)
    at = serial.index("patch validator: a note of the patch")
    after = [n for n in serial[at:] if n.startswith("NIF collisions skipped")]
    assert after, f"control: a planning note follows the patch point: {serial}"
    assert batch == serial


def test_switched_off_the_patch_notes_come_last(load_order, tmp_path, monkeypatch):
    """The control for the notes: switched off, the batch-wide schedule's patch
    notes follow the planning notes after the patch point."""
    monkeypatch.setenv(SWITCH, "1")
    batch = _notes(_convert(load_order, tmp_path, monkeypatch, one_schedule=True,
                            collide="d_1.nif", patch_note=True))
    assert batch.index("patch validator: a note of the patch") > max(
        i for i, n in enumerate(batch) if n.startswith("NIF collisions skipped"))


def test_the_sort_places_by_destination_then_source_then_reason():
    src = Path("one_source.nif")
    items = [(src, Path("o") / n) for n in ("x_1.nif", "y_1.nif", "z_1.nif")]
    got = [ConvertResult(src_path=src, dst_path=str(Path("o") / "z_1.nif"),
                         status="converted (copy)"),
           ConvertResult(src_path=src, dst_path=None, status="skipped",
                         reason="no armor shapes"),
           ConvertResult(src_path=Path("stranger.nif"), dst_path=None,
                         status="error", reason="error: b"),
           ConvertResult(src_path=src, dst_path=None, status="error",
                         reason="error: a"),
           ConvertResult(src_path=src, dst_path=str(Path("o") / "y_1.nif"),
                         status="converted (copy)")]
    for order in (got, list(reversed(got))):
        res = list(order)
        ac._in_plan_order(res, items)
        assert [(r.dst_path and Path(r.dst_path).name, r.status, r.reason)
                for r in res] == [
            (None, "error", "error: a"),         # by its source: item 0
            (None, "skipped", "no armor shapes"),
            ("y_1.nif", "converted (copy)", ""),
            ("z_1.nif", "converted (copy)", ""),
            (None, "error", "error: b")]         # nothing planned it: last
