"""#tidy-output and #zap-prune-morphs' hook: the output mod's run by-products, and the body morph rows of undrawn
vertices dropped once per finished pair."""
import types
from pathlib import Path

from src import auto_convert as ac
from src import body_zap as bz


def test_end_of_run_removes_staging_and_older_superseded_runs(tmp_path):
    out = tmp_path
    (out / "_bsa_staging" / "meshes").mkdir(parents=True)
    (out / "_bsa_staging" / "meshes" / "a.nif").write_bytes(b"x")
    sup = out / "_superseded"
    for d in ("20260927-093713", "20261013-174626", "20261018-120000-2"):
        (sup / d).mkdir(parents=True); (sup / d / "f.nif").write_bytes(b"x")
    (sup / "meshes").mkdir(); (sup / "meshes" / "kept.nif").write_bytes(b"x")
    (sup / "stale_output_report.json").write_text("{}")
    gone = ac._tidy_output_at_end(out)
    assert not (out / "_bsa_staging").exists()
    assert sorted(p.name for p in sup.iterdir()) == ["20261018-120000-2", "meshes", "stale_output_report.json"]
    assert len(gone) == 3


def test_start_of_run_clears_the_audit_log_only(tmp_path):
    (tmp_path / "standoff_audit.jsonl").write_text('{"kind": "chain"}\n')
    (tmp_path / "conversion_report.json").write_text("{}")
    ac._tidy_output_at_start(tmp_path)
    assert not (tmp_path / "standoff_audit.jsonl").exists()
    assert (tmp_path / "conversion_report.json").exists()


def test_tidy_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "_TIDY_OUTPUT", False)
    (tmp_path / "_bsa_staging").mkdir()
    assert ac._tidy_output_at_end(tmp_path) == [] and (tmp_path / "_bsa_staging").is_dir()


def test_prune_runs_once_per_pair_and_only_with_the_zap(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(bz, "prune_undrawn_body_morphs", lambda dst: calls.append(Path(dst).name) or 0)
    res = [types.SimpleNamespace(dst_path=tmp_path / "x_1.nif", status="converted (body-swap)"),
           types.SimpleNamespace(dst_path=tmp_path / "x_0.nif", status="converted (body-swap)"),
           types.SimpleNamespace(dst_path=tmp_path / "y_1.nif", status="skipped")]
    monkeypatch.setattr(bz, "active", lambda: False)
    ac._prune_zapped_morphs(res)
    assert calls == []                                   # no zap -> nothing to prune
    monkeypatch.setattr(bz, "active", lambda: True)
    ac._prune_zapped_morphs(res)
    assert calls == ["x_1.nif"]                          # one call for the x pair, none for a skipped piece
    assert bz.ZAP_PRUNE_MORPHS is True
