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

"""#chain-body-shift telemetry and the region-bias knob.

Telemetry is not a nicety here. The pass moves BONES, so a clip test on
`shape.verts` shows nothing whether it worked or not, and a pool worker's print
can be discarded outright by the frozen exe -- a run can look silent while every
chain moved, or look identical while none did. The SKIP records matter as much
as the moves: "no chains were eligible" and "the pass never ran" are different
answers that otherwise look the same.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import src.fit_metrics as fm  # noqa: E402
import src.nif_convert as nc  # noqa: E402

# Reuse the fixtures rather than cloning them: a second copy of the chain and
# fake-nif builders would drift from the ones the safety tests pin.
from test_chain_body_shift import _chain, _FakeNif  # noqa: E402


def _run(chain, fake, dst_path, bias=0.0):
    saved = (nc.CHAIN_BODY_SHIFT, nc.CHAIN_BODY_SHIFT_BIAS,
             nc._find_cbbe_base_body, nc._find_user_preset_body,
             nc._cached_cbbe_to_ube_delta, nc._verts_skin_to_world,
             nc._shape_global_to_skin)
    nc.CHAIN_BODY_SHIFT = True
    nc.CHAIN_BODY_SHIFT_BIAS = bias
    nc._find_cbbe_base_body = lambda *a, **k: "cbbe"
    nc._find_user_preset_body = lambda *a, **k: "ube"
    nc._cached_cbbe_to_ube_delta = lambda *a, **k: (fake.body, fake.delta)
    nc._verts_skin_to_world = lambda v, g: np.asarray(v, float)
    nc._shape_global_to_skin = lambda s: None
    try:
        return nc._shift_chain_roots_by_body_delta(chain, fake,
                                                   dst_path=dst_path)
    finally:
        (nc.CHAIN_BODY_SHIFT, nc.CHAIN_BODY_SHIFT_BIAS,
         nc._find_cbbe_base_body, nc._find_user_preset_body,
         nc._cached_cbbe_to_ube_delta, nc._verts_skin_to_world,
         nc._shape_global_to_skin) = saved


def _records(tmp_path):
    p = fm.sink_path(tmp_path / "meshes" / "x" / "a.nif")
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def _dst(tmp_path):
    d = tmp_path / "meshes" / "x"
    d.mkdir(parents=True, exist_ok=True)
    return d / "a.nif"


def test_every_moved_chain_is_recorded_with_its_magnitude(tmp_path):
    ch = _chain()
    moved = _run(ch, _FakeNif(ch, near_body=True), _dst(tmp_path))
    assert moved == 2
    recs = [r for r in _records(tmp_path) if r.get("kind") == "chain_shift"]
    got = {r["root"]: r for r in recs if r.get("moved")}
    assert set(got) == {"Skirt 1_00", "Skirt 2_00"}
    for r in got.values():
        assert r["mag"] > 0
        assert len(r["shift"]) == 3
        assert r["near"] >= nc.CHAIN_BODY_SHIFT_MIN_VERTS


def test_a_skip_is_recorded_with_its_reason(tmp_path):
    """"No chain was eligible" must be distinguishable from "the pass never
    ran" -- silence cannot tell those apart, and they need opposite responses."""
    ch = _chain()
    moved = _run(ch, _FakeNif(ch, near_body=False), _dst(tmp_path))
    assert moved == 0
    recs = [r for r in _records(tmp_path) if r.get("kind") == "chain_shift"]
    assert recs, "a run that moved nothing recorded nothing -- indistinguishable"
    assert all(r.get("skipped") for r in recs)
    assert all("near" in r for r in recs)


def test_telemetry_never_gates_the_shift(tmp_path):
    """A missing dst path must cost the record, never the fix."""
    ch = _chain()
    moved = _run(ch, _FakeNif(ch, near_body=True), None)
    assert moved == 2


def test_records_carry_a_path_tail_not_just_a_bare_filename(tmp_path):
    """Filenames repeat across mods; a bare name cannot be traced to a piece."""
    ch = _chain()
    _run(ch, _FakeNif(ch, near_body=True), _dst(tmp_path))
    recs = [r for r in _records(tmp_path) if r.get("kind") == "chain_shift"]
    assert recs and all("/" in r.get("path", "") for r in recs)


# ------------------------------------------------- roots must be GARMENT bones

def test_a_skeleton_ancestor_is_never_elected_a_root():
    """REGRESSION, found on a real cuirass. `chain` carries the skeleton
    ancestors a garment chain hangs off so the hierarchy can be recreated. The
    plain "parent not in chain" rule then elects THOSE as roots -- the measured
    result was `NPC Pelvis [Pelv]` and `NPC Spine [Spn0]`, i.e. the pass
    computed a translation for the actor's pelvis. It was inert only because
    those nodes already exist in the destination and the write loop skips them;
    that is luck, not a design."""
    from test_chain_body_shift import _Xf
    chain = {
        "NPC Pelvis [Pelv]": (_Xf((0.0, 0.0, 70.0)), "NPC Spine [Spn0]"),
        "NPC Spine [Spn0]": (_Xf((0.0, 0.0, 80.0)), "NPC"),
        "skirt1 01": (_Xf((0.0, -8.0, 4.0)), "NPC Pelvis [Pelv]"),
        "skirt1 02": (_Xf((0.0, -1.0, -6.0)), "skirt1 01"),
    }
    roots = nc._chain_root_subtrees(chain, custom_only=True)
    assert set(roots) == {"skirt1 01"}, roots
    assert roots["skirt1 01"] == {"skirt1 01", "skirt1 02"}

    # the permissive rule is what produced the defect -- keep it demonstrated
    loose = nc._chain_root_subtrees(chain)
    assert "NPC Spine [Spn0]" in loose


def test_a_garment_root_under_a_garment_parent_is_not_a_root():
    from test_chain_body_shift import _Xf
    chain = {
        "skirt1 01": (_Xf((0.0, -8.0, 4.0)), "NPC Pelvis [Pelv]"),
        "skirt1 02": (_Xf((0.0, -1.0, -6.0)), "skirt1 01"),
        "NPC Pelvis [Pelv]": (_Xf((0.0, 0.0, 70.0)), "NPC"),
    }
    assert set(nc._chain_root_subtrees(chain, custom_only=True)) == {"skirt1 01"}


# ------------------------------------------------------------------ bias knob

def test_bias_defaults_to_the_validated_mean():
    assert nc.CHAIN_BODY_SHIFT_BIAS == 0.0


def test_bias_moves_the_shift_toward_the_largest_delta(tmp_path):
    """One rigid translation cannot serve a skirt wrapping unevenly-grown hips.
    At bias 0 the shift is the mean over all near verts; raising it averages
    only the top slice by magnitude, so the result must grow -- that is the
    whole point of the knob, and it is why it ships off."""
    ch = _chain()
    fake = _FakeNif(ch, near_body=True)
    # a field that is small nearly everywhere and large in one region
    fake.delta = np.tile(np.array([0.0, 0.1, 0.0]), (200, 1))
    fake.delta[:20] = np.array([0.0, 2.0, 0.0])

    ch_a = _chain()
    _run(ch_a, fake, _dst(tmp_path), bias=0.0)
    ch_b = _chain()
    _run(ch_b, fake, _dst(tmp_path), bias=0.9)

    a = np.linalg.norm(np.array(ch_a["Skirt 1_00"][0].translation)
                       - np.array(_chain()["Skirt 1_00"][0].translation))
    b = np.linalg.norm(np.array(ch_b["Skirt 1_00"][0].translation)
                       - np.array(_chain()["Skirt 1_00"][0].translation))
    assert b > a, f"bias did not favour the large-delta region ({b} vs {a})"


def test_the_cap_is_recorded_when_it_bites(tmp_path):
    """A capped shift is a DIFFERENT outcome from a satisfied one and has to be
    visible -- otherwise a systematically under-shifted pack reads as healthy."""
    ch = _chain()
    fake = _FakeNif(ch, near_body=True)
    fake.delta = np.tile(np.array([0.0, 99.0, 0.0]), (200, 1))
    _run(ch, fake, _dst(tmp_path))
    recs = [r for r in _records(tmp_path)
            if r.get("kind") == "chain_shift" and r.get("moved")]
    assert recs and all(r["capped"] for r in recs)
    assert all(r["applied"] <= nc.CHAIN_BODY_SHIFT_MAX + 1e-6 for r in recs)
