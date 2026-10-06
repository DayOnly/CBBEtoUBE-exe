"""#fx-seam-twin-prewrite: on a NIF with an effect shader -- which no post-write
pass may re-save -- the seam twins of a reskinned shape are joined in the skin
before the NIF's one write, behind the same author gate as #seam-twin-skin."""
from types import SimpleNamespace

import pytest

from src import nif_convert as nc
from src import nif_convert_weights as nw


@pytest.fixture(autouse=True)
def _fx_by_attribute(monkeypatch):
    # Which NIFs count as effect NIFs is `_nif_has_fx_shape`'s business; here a
    # stand-in NIF says so itself.
    monkeypatch.setattr(nc, "_nif_has_fx_shape", lambda nif: bool(getattr(nif, "fx", False)))


FX = SimpleNamespace(fx=True)
PLAIN = SimpleNamespace(fx=False)

# Verts 0 and 1 are the two sides of one seam; 2 is elsewhere.
POS = [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (5.0, 0.0, 0.0)]


def _shape(src_rows, name="Plate"):
    bw: dict = {}
    for i, r in enumerate(src_rows):
        for b, w in r.items():
            bw.setdefault(b, []).append((i, w))
    return SimpleNamespace(name=name, verts=POS, bone_weights=bw)


def _map(rows):
    out: dict = {}
    for i, r in enumerate(rows):
        for b, w in r.items():
            out.setdefault(b, []).append((i, w))
    return out


def _rows(wmap, n=3):
    r = [dict() for _ in range(n)]
    for b, prs in wmap.items():
        for i, w in prs:
            if w > 0:
                r[i][b] = w
    return r


AUTHOR_ALIKE = [{"Spine": 1.0}, {"Spine": 1.0}, {"Pelvis": 1.0}]
OURS_SPLIT = [{"Spine": 0.8, "Spine1": 0.2}, {"Spine": 0.4, "Spine1": 0.6},
              {"Pelvis": 1.0}]


def test_the_two_sides_of_a_seam_get_one_row_on_an_effect_nif():
    n, out = nw._unify_seam_twins_in_skin(_shape(AUTHOR_ALIKE), _map(OURS_SPLIT), src_nif=FX)
    r = _rows(out)
    assert n == 2
    assert r[0] == pytest.approx(r[1])


def test_the_shared_row_is_the_mean_of_the_two_sides():
    _n, out = nw._unify_seam_twins_in_skin(_shape(AUTHOR_ALIKE), _map(OURS_SPLIT), src_nif=FX)
    assert _rows(out)[0] == pytest.approx({"Spine": 0.6, "Spine1": 0.4})


def test_a_nif_without_an_effect_shader_is_left_to_the_post_write_pass():
    m = _map(OURS_SPLIT)
    n, out = nw._unify_seam_twins_in_skin(_shape(AUTHOR_ALIKE), m, src_nif=PLAIN)
    assert n == 0 and out is m


def test_a_seam_the_author_skinned_apart_is_left_apart():
    author = [{"Spine": 1.0}, {"Spine1": 1.0}, {"Pelvis": 1.0}]
    n, out = nw._unify_seam_twins_in_skin(_shape(author), _map(OURS_SPLIT), src_nif=FX)
    assert n == 0
    assert _rows(out)[0] != pytest.approx(_rows(out)[1])


def test_the_callers_map_is_not_changed():
    m = _map(OURS_SPLIT)
    before = {b: list(p) for b, p in m.items()}
    nw._unify_seam_twins_in_skin(_shape(AUTHOR_ALIKE), m, src_nif=FX)
    assert m == before


def test_other_vertices_keep_their_rows():
    _n, out = nw._unify_seam_twins_in_skin(_shape(AUTHOR_ALIKE), _map(OURS_SPLIT), src_nif=FX)
    assert _rows(out)[2] == {"Pelvis": 1.0}


def test_a_bone_keeps_its_last_carrier():
    # Spine1 lives only on vertex 1; the merged row would still carry it, but a
    # cap to four drops it when five bones meet. Its one carrier stays as it was.
    ours = [{"A": 0.3, "B": 0.3, "C": 0.2, "D": 0.2},
            {"A": 0.3, "B": 0.3, "C": 0.2, "Spine1": 0.2},
            {"Pelvis": 1.0}]
    _n, out = nw._unify_seam_twins_in_skin(_shape(AUTHOR_ALIKE), _map(ours), src_nif=FX)
    assert any(w > 1e-4 for _i, w in out["Spine1"])


def test_a_merged_row_is_capped_to_four_bones_and_sums_to_one():
    ours = [{"A": 0.4, "B": 0.3, "C": 0.3},
            {"D": 0.4, "E": 0.3, "A": 0.3},
            {"Pelvis": 1.0}]
    # every bone also has another carrier, so the cap may drop it
    ours[2] = {"B": 0.2, "C": 0.2, "D": 0.2, "E": 0.2, "Pelvis": 0.2}
    _n, out = nw._unify_seam_twins_in_skin(_shape(AUTHOR_ALIKE), _map(ours), src_nif=FX)
    r = _rows(out)[0]
    assert len(r) <= 4
    assert sum(r.values()) == pytest.approx(1.0)


def test_a_body_shape_is_not_touched(monkeypatch):
    monkeypatch.setattr(nc, "RESKIN_SKIP_NAMES", {"Plate"})
    n, _out = nw._unify_seam_twins_in_skin(_shape(AUTHOR_ALIKE), _map(OURS_SPLIT), src_nif=FX)
    assert n == 0


def test_the_nif_is_read_from_the_source_shape_when_not_given():
    s = _shape(AUTHOR_ALIKE)
    s.file = FX
    n, _out = nw._unify_seam_twins_in_skin(s, _map(OURS_SPLIT))
    assert n == 2


def test_the_switch_leaves_the_twins_alone(monkeypatch):
    monkeypatch.setattr(nc, "FX_SEAM_TWIN_PREWRITE", False)
    n, _out = nw._unify_seam_twins_in_skin(_shape(AUTHOR_ALIKE), _map(OURS_SPLIT), src_nif=FX)
    assert n == 0


def test_the_seam_twin_switch_turns_it_off_too(monkeypatch):
    monkeypatch.setattr(nc, "SEAM_TWIN_SKIN", False)
    n, _out = nw._unify_seam_twins_in_skin(_shape(AUTHOR_ALIKE), _map(OURS_SPLIT), src_nif=FX)
    assert n == 0


def test_the_switch_is_on_by_default():
    assert nc.FX_SEAM_TWIN_PREWRITE is True


# --------------------------------------------------------------------------
# The roughness cap runs after the write on an effect NIF too, and scores each
# vertex against its own topological neighbours -- different ones on the two
# sides of a seam -- so it split the twins the pre-write join had tied.
# --------------------------------------------------------------------------
import os  # noqa: E402
import sys  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tests import _converter_sources as _cs  # noqa: E402
from test_coincident_skin_match import FakeNif, FakeShape  # noqa: E402

PELV, SPINE, SPINE1, SPINE2 = ("NPC Pelvis [Pelv]", "NPC Spine [Spn0]",
                               "NPC Spine1 [Spn1]", "NPC Spine2 [Spn2]")
_SMOOTH = {SPINE1: 0.6, PELV: 0.4}


def _seam(rows):
    # Two triangles cut apart along a UV seam: vertex 1 and vertex 4 are one point.
    verts = [(0.0, 0.0, 80.0), (1.0, 0.0, 81.0), (2.0, 0.0, 82.0),
             (0.0, 1.0, 80.0), (1.0, 0.0, 81.0), (2.0, 1.0, 82.0)]
    sh = FakeShape("Plate", verts, rows)
    sh.tris = [(0, 1, 2), (3, 4, 5)]
    return sh


def _cap(dst, src, fx):
    class _Pyn:
        @staticmethod
        def NifFile(filepath=None):
            return FakeNif([dst]) if str(filepath) == "dst.nif" else FakeNif([src])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(nc, "_pynifly", lambda: _Pyn)
        mp.setattr(nc, "_hide_virtual_body", lambda nf: False)
        mp.setattr(nc, "_nif_has_fx_shape", lambda nf: fx)
        _cs.patch(mp, "atomic_nif_save", lambda nf, p: None)
        nc._cap_weight_roughness_to_author("dst.nif", src_nif_path="src.nif")
    return dst._rows


_OURS = [dict(_SMOOTH), {SPINE2: 0.7, SPINE: 0.3}, dict(_SMOOTH),
         dict(_SMOOTH), {SPINE2: 0.7, SPINE: 0.3}, dict(_SMOOTH)]


def test_the_roughness_cap_keeps_seam_twins_tied_on_an_effect_nif():
    # Side A's neighbours pull vertex 1 one way; side B's twin is made rougher
    # than its own neighbours by a different amount, so untied they part.
    ours = [dict(r) for r in _OURS]
    ours[3] = {SPINE2: 0.5, SPINE: 0.5}
    rows = _cap(_seam(ours), _seam([dict(_SMOOTH)] * 6), fx=True)
    assert rows[1] == pytest.approx(rows[4])


def test_without_the_tie_the_cap_parts_them():
    # the control: the case above really is one the cap splits
    ours = [dict(r) for r in _OURS]
    ours[3] = {SPINE2: 0.5, SPINE: 0.5}
    rows = _cap(_seam(ours), _seam([dict(_SMOOTH)] * 6), fx=False)
    assert rows[1] != pytest.approx(rows[4])


def test_the_shape_writer_joins_the_twins_before_it_installs_the_reskin():
    import inspect
    from src import nif_convert_writer as w
    src = inspect.getsource(w._copy_shape)
    call = src.find("_unify_seam_twins_in_skin(")
    assert call >= 0, "the pre-write join is not wired into _copy_shape"
    assert call < src.find("_install_skin(new_shape", call - 2000 if call > 2000 else 0)
    assert "src_shape, weights_map)" in src[call:call + 200]
