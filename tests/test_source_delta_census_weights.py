"""Each file is posed on the bodies of its OWN weight, on both sides of the delta.

`source_delta_census` used to pose every garment on the weight-1 bodies, so a
`_0` file would have been scored in the wrong frame on both the converted and
the source side -- a believable delta, never an error. It now reads both
weights; this pins that a `_0` file really does get the weight-0 bodies, that
every row says which weight it is, and that a missing weight-0 body skips the
file instead of falling back to weight 1.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from scripts.analysis import source_delta_census as sdc  # noqa: E402


def _conv(worst):
    """analyse_with_body's shape: one region, below MIN_COV so no delta row."""
    return ({"r": {"worst_pct": worst, "worst_pose": "p",
                   "covered_at_bind": 0, "per_pose": []}}, 0.0)


def _run(tmp_path, monkeypatch, canonical_ube):
    """Run main() over one garment at both weights; return (bodies used, rows)."""
    pack = tmp_path / "mods" / "CBBEtoUBE Auto" / "meshes" / "!UBE" / "a"
    pack.mkdir(parents=True)
    for w in ("0", "1"):
        (pack / ("piece_%s.nif" % w)).write_bytes(b"x")
    body_name = next(iter(sdc.UBE_BODY_INJECT_NAMES))

    monkeypatch.delenv("CBBE2UBE_OUT_MOD", raising=False)
    monkeypatch.setattr(sdc.paths, "discover_layout",
                        lambda: SimpleNamespace(mods_root=tmp_path / "mods"))
    monkeypatch.setattr(sdc.pynifly, "NifFile",
                        lambda _p: SimpleNamespace(
                            shapes=[SimpleNamespace(name=body_name)]))
    monkeypatch.setattr(sdc, "canonical_cbbe",
                        lambda _root, weight="_1": ("cbbe%s.nif" % weight, "3BA"))
    monkeypatch.setattr(sdc, "canonical_ube", canonical_ube)
    monkeypatch.setattr(sdc, "converted_garment_names", lambda _p: ["g"])
    monkeypatch.setattr(sdc, "find_source",
                        lambda rel, _g, _m: Path("mods", "src", "meshes", "a",
                                                 Path(rel).name))
    used = []

    def fake_analyse(p, _gnames, body_path, _shape, sample=300):
        used.append((Path(p).name, body_path))
        return _conv(10.0)                  # over the gate: the SOURCE side runs too

    monkeypatch.setattr(sdc, "analyse_with_body", fake_analyse)
    out = tmp_path / "rows.jsonl"
    monkeypatch.setattr(sys, "argv", ["source_delta_census", "--out", str(out)])
    sdc.main()
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    return used, rows


def test_each_file_is_posed_on_its_own_weights_bodies(tmp_path, monkeypatch):
    used, rows = _run(tmp_path, monkeypatch,
                      lambda weight="_1": ("ube%s.nif" % weight, "BaseShape"))

    # converted side AND source side, each on the file's own weight
    assert ("piece_0.nif", "ube_0.nif") in used
    assert ("piece_0.nif", "cbbe_0.nif") in used
    assert ("piece_1.nif", "ube_1.nif") in used
    assert ("piece_1.nif", "cbbe_1.nif") in used
    assert not any(n == "piece_0.nif" and b.endswith("_1.nif") for n, b in used)
    assert {Path(r["armor"]).name: r["weight"] for r in rows} == {
        "piece_0.nif": "_0", "piece_1.nif": "_1"}


def test_a_missing_weight0_body_skips_its_files(tmp_path, monkeypatch, capsys):
    """Never posed on the weight-1 bodies instead: that prints a believable
    delta in the wrong frame."""
    def ube(weight="_1"):
        if weight == "_0":
            raise FileNotFoundError("no weight-0 sibling")
        return ("ube%s.nif" % weight, "BaseShape")

    used, rows = _run(tmp_path, monkeypatch, ube)

    assert not any(n == "piece_0.nif" for n, _b in used)
    assert [Path(r["armor"]).name for r in rows] == ["piece_1.nif"]
    out = capsys.readouterr().out
    assert "    1 skipped: no reference body for that weight" in out
    assert "weight 0: 0 scored" in out
