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

"""Guards on the follow harness's REFUSALS.

The harness exists to answer "does this garment track the body". Its failure
mode is not a wrong number, it is a PLAUSIBLE number computed from nothing --
which already cost two wrong conclusions:

  * picking "the body" as the largest shape in the NIF measured a boot against
    ITSELF, because boots and gauntlets ship with no injected BaseShape (110 of
    150 sampled outputs have none).
  * scoring SMP-simulated cloth reported a skirt as "100% failing" for correct
    behaviour, and that band was written up as a real defect.

Every test here pins a refusal or a label that would have caught one of those.
"""
import importlib.util
import inspect
from pathlib import Path

import numpy as np
import pytest

_SRC = Path(__file__).resolve().parent.parent / "scripts" / "follow_bands.py"


def _load():
    spec = importlib.util.spec_from_file_location("follow_bands", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def fb():
    return _load()


def test_never_picks_the_largest_shape_as_the_body(fb):
    """THE bug this harness was rewritten for.

    'max(shapes, key=len(verts))' on a NIF with no BaseShape returns the
    garment, so the garment is compared to itself and every band reads clean.
    The resolver must go BaseShape -> external reference -> refuse, never
    'largest shape in this NIF'.
    """
    src = inspect.getsource(fb.resolve_body)
    assert "BaseShape" in src
    assert "_find_ube_body_ref" in src
    body = inspect.getsource(fb.main)
    assert "max(" not in body or "key=lambda s: len(s.verts)" not in body


def test_refuses_a_body_that_is_actually_a_garment(fb):
    """A resolved 'body' with a garment's vertex count is the self-comparison
    bug arriving by a different route (an explicit --body pointing at armour)."""
    src = inspect.getsource(fb.main)
    assert "MIN_BODY_VERTS" in src
    assert fb.MIN_BODY_VERTS >= 5000


def test_refuses_when_nothing_was_measured(fb):
    """An empty result is not a clean result. Without this, a typo'd shape name
    prints a header and exits 0."""
    src = inspect.getsource(fb.main)
    assert "nothing was measured" in src


def test_refuses_without_a_skeleton(fb):
    """Armor NIFs carry a FLAT bone list. With no skeleton hierarchy everything
    below the posed joint fails to move, which reads exactly like a follow
    failure -- a false defect, not a missing measurement."""
    src = inspect.getsource(fb.main)
    assert "CBBE2UBE_SKELETON_NIF" in src
    assert "REFUSED" in src


def test_refusal_is_a_nonzero_exit(fb):
    """A refusal that exits 0 can be scripted past in a loop and read as a
    clean run."""
    assert issubclass(fb.HarnessRefusal, SystemExit)
    with pytest.raises(SystemExit) as exc:
        raise fb.HarnessRefusal("REFUSED: test")
    assert exc.value.code != 0


def test_identity_self_test_is_a_refusal_not_a_warning(fb):
    src = inspect.getsource(fb.main)
    i = src.index("identity self-test")
    assert "REFUSED" in src[max(0, i - 400):i + 200]


def test_chain_bones_are_xml_declared_AND_foreign_to_the_body(fb):
    """The narrow predicate is load-bearing.

    A generated physics XML declares body bones (Spine2, Clavicle...) as
    collision bodies, so 'weighted to any bone the XML names' marks 100% of
    every band simulated and hides the real contamination -- measured: it
    reported 100% where the true figures are armhole 3%, under-bust 1%,
    side 73%. Same over-broad-predicate mistake that made an XML-bone row gate
    reject 1601 of 1601 rows.
    """
    src = inspect.getsource(fb.main)
    assert "b in driven and b not in BW" in src


def test_simulated_bands_are_labelled_and_excluded_from_the_verdict(fb):
    """Kinematic follow does not describe a vert an XML chain drives at
    runtime. Reporting one without the label is how a correct-behaving skirt
    became a logged defect."""
    src = inspect.getsource(fb.main)
    assert "SIMULATED" in src
    assert "excluding simulated" in src


def test_band_table_covers_torso_and_leg(fb):
    """Two separate scratchpad scripts drifted apart; one implementation."""
    for band in ("armhole", "side", "under-bust", "bust",
                 "hip", "thigh", "knee", "calf"):
        assert band in fb.BANDS, band
        sel, poses = fb.BANDS[band]
        assert callable(sel) and poses


def test_stationary_verts_are_gated_out(fb):
    """Scoring verts whose covered body point does not move is the dilution
    that made an earlier census read 54% artifact."""
    assert fb.MIN_BODY_MOVE > 0
    assert "MIN_BODY_MOVE" in inspect.getsource(fb.main)


def test_only_the_known_band_pair_overlaps(fb):
    """The bands are anatomical, NOT a partition -- pinned so nobody treats two
    rows as independent evidence about disjoint verts.

    `side` (z 92-103, |x|>=8) and `bust` (z 90-102, y>0) genuinely share the
    front-lateral chest: measured on a synthetic body-sized cloud, 553 verts,
    46% of `side` and 61% of `bust`. That is the ONLY overlapping pair, and
    this test fails if a band edit creates another one.
    """
    rng = np.random.default_rng(0)
    n = 20000
    v = np.stack([rng.uniform(-28, 28, n), rng.uniform(-15, 15, n),
                  rng.uniform(0, 130, n)], axis=1)
    names = list(fb.BANDS)
    masks = {k: fb.BANDS[k][0](v) for k in names}
    overlapping = set()
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            if (masks[a] & masks[b]).any():
                overlapping.add(frozenset((a, b)))
    assert overlapping == {frozenset(("side", "bust"))}, (
        f"band overlap changed: {[tuple(sorted(p)) for p in overlapping]}")
