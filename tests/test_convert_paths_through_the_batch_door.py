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

"""#batch-door -- one synthetic conversion per convert path, through the door
the batch uses, reading the written NIF back.

Audit F020 (2026-09-01): no collected test converted a NIF through either
entry point; the only caller was a script gated under __main__ that pytest
never collected. Every write-time defect class this project has recorded was
therefore invisible to the suite by construction. These tests dispatch a
synthetic garment through the batch worker's tuple door -- the door the batch
and the golden harness use (#single-vs-batch-parity) -- with absolute paths
and an output folder that has no `meshes` ancestor, and assert on the file
that was written: it reloads, its shapes are the source's in the source's
order plus the injected body, every bone in every palette carries weight, the
destination validator is clean, and the per-piece pass-failure list is empty.
Passes record instead of raise, so a dead pass is otherwise green.

WHAT A SPHERE CANNOT SHOW. No fit quality, no physics XML, no OSD (so no
`.tri` and no morph amplitude), no skeleton; and with no MO2 layout the
CBBE/UBE femalebody pair is absent, so the body-follow repair layer is off.
This catches a dead pass, an exception or a swallowed failure on either path,
and nothing about how well a piece fits. The golden check in docs/RELEASING.md is
the real-mesh counterpart, on the maintainer machine.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.nif_convert as nc                                    # noqa: E402
from src import auto_convert as ac                              # noqa: E402
from src import nif_convert_telemetry as tel                    # noqa: E402
from src.nif_convert_writer import validate_dst_nif             # noqa: E402
from tests import _converter_sources as _cs                     # noqa: E402
from tests.synthetic_nif import (build_skinned_shapes_nif,      # noqa: E402
                                 pynifly_available, uv_sphere)

pytestmark = pytest.mark.skipif(not pynifly_available(),
                                reason="pynifly native lib not available")

BODY_SLOT = nc.BIPED_SLOT32_BIT


@pytest.fixture
def world(tmp_path):
    """A UBE body reference (BaseShape + VirtualBody), a garment with no
    inline body, and one carrying an inline `3BA` body -- closed spheres,
    each skinned to two bones."""
    body, vbody = uv_sphere(10.0), uv_sphere(9.5)
    garment, inline = uv_sphere(12.0), uv_sphere(10.0)
    ref = build_skinned_shapes_nif(tmp_path / "ube" / "femalebody_1.nif",
                                   [("BaseShape", *body), ("VirtualBody", *vbody)])
    belt = build_skinned_shapes_nif(tmp_path / "src" / "belt_1.nif",
                                    [("Belt", *garment)])
    cuirass = build_skinned_shapes_nif(tmp_path / "src" / "cuirass_1.nif",
                                       [("Cuirass", *garment), ("3BA", *inline)])
    out = tmp_path / "out"          # absolute, and no `meshes` ancestor
    out.mkdir()
    return ref, belt, cuirass, out


def _convert(src, out, ref, slots=BODY_SLOT):
    """The batch door: the work tuple a pool worker receives."""
    dst = out / src.name
    res = ac._nif_convert_worker((str(src.resolve()), str(dst.resolve()),
                                  str(ref.resolve()), int(slots), None))
    return res, dst


def _reload(path):
    return nc._pynifly().NifFile(filepath=str(path))


def _assert_written_clean(res, src, dst, status):
    assert res.status == status, (res.status, res.reason)
    assert res.dropped_shapes == [], res.dropped_shapes
    assert res.dst_path is not None and Path(res.dst_path) == dst and dst.is_file()
    assert "PASS FAILED" not in res.reason, res.reason
    assert tel._piece_pass_failures() == []
    nif = _reload(dst)
    assert nif.shapes, "the written NIF has no shapes"
    for s in nif.shapes:
        weights = s.bone_weights
        assert weights, f"{s.name}: not skinned"
        unweighted = sorted(b for b, prs in weights.items() if not prs)
        assert unweighted == [], f"{s.name}: palette bones with no weight: {unweighted}"
        assert len(s.verts) > 0 and len(s.tris) > 0, s.name
    assert validate_dst_nif(dst, None, src) == []
    return nif


def test_the_copy_path_writes_the_garment_it_was_given(world):
    ref, belt, _cuirass, out = world
    res, dst = _convert(belt, out, ref)
    nif = _assert_written_clean(res, belt, dst, "converted (copy)")
    assert res.body_shapes == [] and res.armor_shapes == ["Belt"]
    assert [s.name for s in nif.shapes] == ["Belt"], (
        "the source shape, in its place, and nothing else")
    assert len(nif.shapes[0].verts) == len(uv_sphere(12.0)[0])


def test_the_body_swap_path_replaces_the_inline_body_with_the_ube_body(world):
    ref, _belt, cuirass, out = world
    res, dst = _convert(cuirass, out, ref)
    nif = _assert_written_clean(res, cuirass, dst, "converted (body-swap)")
    assert res.body_shapes == ["3BA"] and res.armor_shapes == ["Cuirass"]
    names = [s.name for s in nif.shapes]
    assert names == ["Cuirass", "BaseShape", "VirtualBody"], (
        f"{names}: the garment keeps index 0 (an alt-texture swap binds by "
        "index, #bug-09), the injected body follows it, the inline body is gone")
    ref_base = next(s for s in _reload(ref).shapes if s.name == "BaseShape")
    got_base = next(s for s in nif.shapes if s.name == "BaseShape")
    assert np.array_equal(np.asarray(got_base.verts, dtype=np.float32),
                          np.asarray(ref_base.verts, dtype=np.float32)), (
        "BaseShape verts are copied verbatim from the reference")


def test_a_pass_that_raises_is_reported_in_the_result_not_lost(world, monkeypatch):
    """A pool worker's module state and stderr never reach the parent; the
    returned result is the only channel. A pass that dies must show up there,
    by its label, and must not cost the piece."""
    ref, _belt, cuirass, out = world

    def planted(*_a, **_k):
        raise RuntimeError("planted: panel rigidity fell over")

    # Both callees of the panel-rigidity site: which one runs depends on the
    # early-clearance flag, and the planted failure must fire either way.
    assert _cs.patch(monkeypatch, "_partial_rigid_panels", planted) >= 1
    assert _cs.patch(monkeypatch, "_rigidify_within_clearance", planted) >= 1
    res, dst = _convert(cuirass, out, ref)
    assert res.status == "converted (body-swap)", (
        "one dead pass must not lose the piece", res.status, res.reason)
    assert dst.is_file()
    failures = tel._piece_pass_failures()
    assert any("panel-rigidity" in f and "planted" in f for f in failures), failures
    assert "PASS FAILED panel-rigidity" in res.reason and "planted" in res.reason, res.reason
