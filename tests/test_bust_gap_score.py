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

"""`scripts/analysis/bust_gap_score.py` -- the bust-gap scorer, per convert path.

The two load-bearing tests are `test_degenerate_body_normals_abort_the_run` and
`test_a_copy_path_piece_is_measured_rather_than_dropped`, because those are the
two defects the harness was promoted to fix, and BOTH of them previously shipped
a believable wrong answer instead of an error:

  * the author's bust standoff read 0.000u off an all-zero normal array, which
    silently turns every "gap" into our own standoff -- a metric that can never
    say the author was looser;
  * a piece with no injected body was dropped, so the population was the
    body-swap path alone and a copy-path change scored as "ARM DID NOT FIRE".

`test_no_machine_specific_paths_or_mod_names` guards the promotion itself: this
file is tracked content, and the scratch original hardcoded one developer's
modlist path and one mod's name.
"""
import re
from pathlib import Path

import numpy as np
import pytest

from scripts.analysis import bust_gap_score as bg


_SRC = Path(bg.__file__).read_text(encoding="utf-8")


class _FakeShape:
    """The little of a pynifly shape this harness touches."""

    def __init__(self, verts, normals=None, name="Shape", textures=None,
                 tris=None):
        self.verts = np.asarray(verts, np.float64)
        self.normals = None if normals is None else np.asarray(normals,
                                                               np.float64)
        self.name = name
        self.textures = {} if textures is None else textures
        self.tris = tris


def _body_patch(n=40, z0=95.0):
    """A front-facing slab inside `body_zones.breast_mask`: +y normals, in band."""
    xs = np.linspace(-5.0, 5.0, n)
    zs = np.linspace(z0, z0 + 4.0, n)
    X, Z = np.meshgrid(xs, zs)
    V = np.column_stack([X.ravel(), np.full(X.size, 6.0), Z.ravel()])
    N = np.tile(np.array([0.0, 1.0, 0.0]), (len(V), 1))
    return V, N


def _ref(V, N):
    from scipy.spatial import cKDTree
    from src import body_zones as bz
    return (V, N, cKDTree(V), np.asarray(bz.breast_mask(V), bool))


# --------------------------------------------------------------- defect 2

def test_degenerate_body_normals_abort_the_run():
    """An all-zero normal array must STOP the measurement, not be normalised into
    a plausible-looking zero standoff. This is the BUG-00 fail-open shape."""
    V, _ = _body_patch()
    shape = _FakeShape(V, normals=np.zeros_like(V))
    with pytest.raises(SystemExit):
        bg._unit_body_normals(shape)


def test_good_body_normals_are_returned_as_unit_vectors():
    V, N = _body_patch()
    out = bg._unit_body_normals(_FakeShape(V, normals=N * 3.0))
    assert np.allclose(np.linalg.norm(out, axis=1), 1.0)


# --------------------------------------------------------------- the metric

def test_the_band_is_taken_on_the_body_not_as_a_slab_over_cloth():
    """Read through each cloth vertex's nearest BODY vertex. A z-slab over cloth
    catches sleeves and collars that merely pass through the same heights."""
    V, N = _body_patch()
    ref = _ref(V, N)
    # Cloth sitting a known 0.75u proud of the slab, over the band.
    cloth = V + N * 0.75
    med, pen, worst = bg.band_stats(cloth, ref)
    assert med == pytest.approx(0.75, abs=1e-6)
    assert pen == 0
    assert worst == pytest.approx(0.75, abs=1e-6)


def test_cloth_behind_the_skin_is_counted_as_penetration():
    V, N = _body_patch()
    ref = _ref(V, N)
    cloth = V + N * 0.5
    cloth[:10] = V[:10] - N[:10] * 0.4          # ten verts pushed inside
    med, pen, worst = bg.band_stats(cloth, ref)
    assert pen == 10
    assert worst == pytest.approx(-0.4, abs=1e-6)
    # The median is |standoff|, so it is not dragged negative by a few verts.
    assert med > 0.0


def test_a_shape_that_barely_grazes_the_band_says_nothing():
    """Below MIN_BAND the median is written by whichever few verts landed there,
    so the shape must be excluded rather than scored."""
    V, N = _body_patch()
    ref = _ref(V, N)
    few = (V + N * 0.5)[: bg.MIN_BAND - 1]
    assert bg.band_stats(few, ref) is None
    enough = (V + N * 0.5)[: bg.MIN_BAND + 5]
    assert bg.band_stats(enough, ref) is not None


def test_a_shape_entirely_outside_the_band_is_excluded():
    """The band belongs to the BODY, so this needs a body that HAS an
    out-of-band region -- a slab that is entirely in-band would attribute even
    distant cloth to a breast vertex, which is the correct behaviour of the
    nearest-body-vertex rule and not what this test is about."""
    V, N = _body_patch()
    low = V + np.array([0.0, 0.0, -60.0])           # thighs: outside the band
    V2 = np.vstack([V, low])
    N2 = np.vstack([N, N])
    ref = _ref(V2, N2)
    assert bool(ref[3][:len(V)].all()) and not bool(ref[3][len(V):].any()), (
        "fixture must have exactly one in-band half")
    skirt = low + N * 0.5
    assert bg.band_stats(skirt, ref) is None


# --------------------------------------------------------------- defect 1

def test_a_copy_path_piece_is_measured_rather_than_dropped(monkeypatch):
    """No injected body means COPY path, and it must fall back to the external
    UBE body. Dropping it is what silently reduced the population to one path."""
    V, N = _body_patch()
    sentinel = _ref(V, N)
    monkeypatch.setattr(bg, "_external_body", lambda w: sentinel)

    class _Nif:
        shapes = [_FakeShape(V[:50], name="Cuirass")]

    ref, is_swap = bg._body_for(_Nif(), "_1")
    assert is_swap is False
    assert ref is sentinel


def test_an_injected_body_marks_the_piece_body_swap(monkeypatch):
    big = np.zeros((20001, 3))
    big[:, 2] = 96.0
    name = sorted(bg.BODY_NAMES)[0]

    class _Nif:
        shapes = [_FakeShape(big, normals=np.tile([0.0, 1.0, 0.0],
                                                  (len(big), 1)), name=name)]

    monkeypatch.setattr(bg, "_external_body",
                        lambda w: pytest.fail("must not reach the external body"))
    _ref_out, is_swap = bg._body_for(_Nif(), "_1")
    assert is_swap is True


def test_the_two_paths_are_reported_separately():
    """A pooled median lets whichever path has more shapes write the verdict."""
    assert "body-swap" in _SRC and "copy path" in _SRC
    assert "_print_table" in _SRC


# --------------------------------------------------------------- population

def test_a_collision_proxy_is_not_scored_as_garment():
    assert bg._renders(_FakeShape([[0, 0, 0]], textures={"0": ""})) is False
    assert bg._renders(_FakeShape([[0, 0, 0]], textures={})) is False
    assert bg._renders(_FakeShape([[0, 0, 0]], textures={"0": "a.dds"})) is True


def test_an_empty_population_is_a_failure_not_a_pass():
    """0/0 is not a pass -- it must return non-zero and say so."""
    assert "0/0 IS NOT A PASS" in _SRC


def test_the_arm_fired_assertion_is_read_off_geometry():
    """The frozen exe drops pool-worker prints, so an empty log is not an unfired
    pass. The assertion has to come from the meshes."""
    assert "NOT FIRED" in _SRC
    assert "MOVED_EPS" in _SRC


# --------------------------------------------------------------- promotion

def test_no_machine_specific_paths_or_mod_names():
    """Tracked content carries no absolute local path and no mod name. The scratch
    original hardcoded both, which makes a harness useless on any other machine
    and publishes a local directory layout."""
    body = "\n".join(line for line in _SRC.splitlines()
                     if not line.lstrip().startswith("#"))
    assert not re.search(r"[A-Za-z]:\\\\|[A-Za-z]:/", body), (
        "an absolute Windows path is hardcoded")
    assert "Modlists" not in body
    assert "D:\\" not in body and "D:/" not in body


def test_the_bodies_come_from_canonical_body():
    """Both reference bodies are discovered, not named -- `canonical_body` already
    resolves the mods root the way the converter itself does."""
    assert "canonical_body" in _SRC
    assert "canonical_cbbe" in _SRC
    assert "find_source" in _SRC


def test_the_author_source_is_matched_on_garment_names():
    """'The last mod providing this path' has already inverted a measured delta
    from +0.7 to +83.3 -- from 'the author did it' to 'we did it'."""
    assert "find_source" in bg.author_baseline.__doc__
    assert "garment" in bg.author_baseline.__doc__.lower()


def test_it_runs_as_a_module_and_reports_usage():
    assert bg.main([]) == 2
