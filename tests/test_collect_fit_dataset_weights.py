"""Each file is measured against the body of its OWN weight.

`collect_fit_dataset` used to build ONE body context -- the weight-1 body -- and
measure every garment against it, so a `_0` file's clearance columns would have
been in the wrong frame and its exposure rays would have started outside it. It
now sweeps both weights; this pins that a `_0` file gets the weight-0 context
and that every row says which weight it is.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from scripts.analysis import collect_fit_dataset as cfd  # noqa: E402


def test_each_file_is_measured_on_its_own_weights_body(tmp_path, monkeypatch):
    pack = tmp_path / "mods" / "CBBEtoUBE Auto" / "meshes" / "!UBE" / "a"
    pack.mkdir(parents=True)
    for w in ("0", "1"):
        (pack / ("piece_%s.nif" % w)).write_bytes(b"x")

    monkeypatch.setattr(cfd.paths, "discover_layout",
                        lambda: SimpleNamespace(mods_root=tmp_path / "mods"))
    monkeypatch.setattr(cfd.nc, "_find_ube_femalebody",
                        lambda weight="_1": Path("ube%s.nif" % weight))
    monkeypatch.setattr(cfd, "body_context", lambda weight="_1": "ctx" + weight)
    shape = SimpleNamespace(name="garment", verts=[(0.0, 0.0, 0.0)] * 300)
    monkeypatch.setattr(cfd.pynifly, "NifFile",
                        lambda filepath=None: SimpleNamespace(shapes=[shape]))
    seen = []

    def fake_row(s, rel, nf, ctx, col, soft, lay, do_rays):
        seen.append((Path(rel).name, ctx))
        return {"file": rel, "shape": s.name}

    monkeypatch.setattr(cfd, "shape_row", fake_row)
    out = tmp_path / "rows.jsonl"
    monkeypatch.setattr(sys, "argv", ["collect_fit_dataset", "--out", str(out),
                                      "--no-rays"])
    assert cfd.main() == 0

    assert sorted(seen) == [("piece_0.nif", "ctx_0"), ("piece_1.nif", "ctx_1")]
    lines = out.read_text(encoding="utf-8").splitlines()
    meta = json.loads(lines[0])
    assert meta["bodies"] == {"_1": "ube_1.nif", "_0": "ube_0.nif"}
    rows = [json.loads(line) for line in lines[1:]]
    assert {Path(r["file"]).name: r["weight"] for r in rows} == {
        "piece_0.nif": "_0", "piece_1.nif": "_1"}


def test_an_unresolvable_weight0_body_skips_its_files(tmp_path, monkeypatch):
    """Never measured on the other weight's body instead."""
    pack = tmp_path / "mods" / "CBBEtoUBE Auto" / "meshes" / "!UBE" / "a"
    pack.mkdir(parents=True)
    for w in ("0", "1"):
        (pack / ("piece_%s.nif" % w)).write_bytes(b"x")

    def fake_body_context(weight="_1"):
        if weight == "_0":
            raise FileNotFoundError("no weight-0 body")
        return "ctx" + weight

    monkeypatch.setattr(cfd.paths, "discover_layout",
                        lambda: SimpleNamespace(mods_root=tmp_path / "mods"))
    monkeypatch.setattr(cfd.nc, "_find_ube_femalebody",
                        lambda weight="_1": Path("ube%s.nif" % weight))
    monkeypatch.setattr(cfd, "body_context", fake_body_context)
    shape = SimpleNamespace(name="garment", verts=[(0.0, 0.0, 0.0)] * 300)
    monkeypatch.setattr(cfd.pynifly, "NifFile",
                        lambda filepath=None: SimpleNamespace(shapes=[shape]))
    seen = []
    monkeypatch.setattr(cfd, "shape_row",
                        lambda s, rel, *a: seen.append((Path(rel).name, a[1]))
                        or {"file": rel})
    out = tmp_path / "rows.jsonl"
    monkeypatch.setattr(sys, "argv", ["collect_fit_dataset", "--out", str(out),
                                      "--no-rays"])
    cfd.main()
    assert seen == [("piece_1.nif", "ctx_1")]
