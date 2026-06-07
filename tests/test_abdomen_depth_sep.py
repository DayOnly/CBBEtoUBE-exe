"""Guard for the multi-layer abdomen/waist depth separation (#abdomen z-fight).
Three coplanar overlapping waist layers must be re-stacked to DISTINCT depths
(so they stop Z-fighting), with none pushed below the body-clearance floor."""
import numpy as np
from src import nif_convert as nc


def _grid_layer(y, n=12):
    """A small front-waist patch at clearance `y` (body plane at Y=0, +Y out)."""
    xs = np.linspace(-4, 4, n)
    zs = np.linspace(80, 90, n)
    pts = np.array([[x, y, z] for x in xs for z in zs], dtype=np.float64)
    return pts


def _body_plane(n=20):
    xs = np.linspace(-10, 10, n)
    zs = np.linspace(70, 96, n)
    bv = np.array([[x, 0.0, z] for x in xs for z in zs], dtype=np.float64)
    bn = np.tile(np.array([0.0, 1.0, 0.0]), (len(bv), 1))
    return bv, bn


def test_abdomen_depth_sep_stacks_three_coplanar_layers():
    bv, bn = _body_plane()
    # three layers all coplanar at +1.0 (Z-fighting)
    jobs = []
    for _ in range(3):
        jobs.append({"override_skin": {"weights": {"NPC Spine": []}},
                     "verts": _grid_layer(1.0), "verts_modified": False})
    pushed = nc._separate_abdomen_layered_cloth_depth(
        jobs, body_verts=bv, body_normals=bn)
    assert pushed > 0, "expected verts to be pushed apart"
    med = sorted(float(np.median(np.asarray(j["verts"])[:, 1])) for j in jobs)
    # distinct depths now (outward chain), base anchored at its original +1.0
    # (never pushed inward, so >= the body plane at 0)
    assert med[0] >= 1.0 - 1e-6, f"base layer must stay anchored: {med}"
    gaps = [med[i + 1] - med[i] for i in range(len(med) - 1)]
    assert all(g > 0.1 for g in gaps), f"layers not separated: {med}"


def test_abdomen_depth_sep_noop_single_layer():
    bv, bn = _body_plane()
    jobs = [{"override_skin": {"weights": {}}, "verts": _grid_layer(1.0),
             "verts_modified": False}]
    assert nc._separate_abdomen_layered_cloth_depth(jobs, bv, bn) == 0
    assert jobs[0]["verts_modified"] is False


def test_abdomen_depth_sep_noop_without_body():
    jobs = [{"override_skin": {"weights": {}}, "verts": _grid_layer(1.0)}] * 2
    assert nc._separate_abdomen_layered_cloth_depth(jobs, None, None) == 0
