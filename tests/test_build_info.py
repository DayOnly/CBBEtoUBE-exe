"""The run must be able to say which build it is and what it resolved every
setting to -- and a torn settings file must be LOUD, not defaults."""
from __future__ import annotations

import json

import pytest

from src import build_info as bi
from src import gui_settings as gs


def test_stamp_has_stable_keys_and_a_version():
    s = bi.stamp()
    assert {"version", "git", "dirty", "built_utc", "frozen", "exe_sha256"} <= set(s)
    assert s["version"] and s["version"] != "?"
    assert s["frozen"] is False          # this is a source run
    assert "build " in bi.stamp_line()


def test_effective_settings_cover_every_registry_key_and_apply_polarity():
    eff = bi.effective_settings(env={})
    assert set(eff) == {s.key for s in gs.SETTINGS}
    # every value is the default under an empty environment ...
    assert eff == gs.defaults()
    # ... and an env override resolves the way the converter reads it,
    # polarity included (a NO_* var set to 1 DISABLES the feature).
    inv = next(s for s in gs.SETTINGS if s.kind == "bool" and s.invert and s.env)
    plain = next(s for s in gs.SETTINGS if s.kind == "bool" and not s.invert and s.env)
    got = bi.effective_settings(env={inv.env: "1", plain.env: "1"})
    assert got[inv.key] is False
    assert got[plain.key] is True
    nd = bi.non_default_settings(env={inv.env: "1", plain.env: "1"})
    assert set(nd) <= {inv.key, plain.key}


def test_run_config_carries_build_settings_and_file_status():
    rc = bi.run_config()
    assert rc["build"]["version"]
    assert rc["settings_file"]["status"] in ("ok", "absent", "malformed")
    assert set(rc["effective"]) == {s.key for s in gs.SETTINGS}
    json.dumps(rc, default=str)          # must serialise for the report


def test_malformed_settings_file_is_reported_not_defaulted_silently(tmp_path):
    p = tmp_path / "CBBEtoUBE_settings.json"
    gs.save_values({"bust_morph_chord": False}, p)
    assert gs.load_status(p) == "ok"
    p.write_text(p.read_text(encoding="utf-8")[:20], encoding="utf-8")   # torn
    assert gs.load_status(p) == "malformed"
    assert gs.load_values(p) == gs.defaults()          # unchanged contract ...
    with pytest.MonkeyPatch.context() as mp:            # ... but the run says so
        mp.setattr(gs, "config_path", lambda: p)
        lines = "\n".join(bi.echo_lines())
    assert "SETTINGS FILE MALFORMED" in lines


def test_save_is_atomic_and_keeps_the_previous_good_file(tmp_path):
    p = tmp_path / "CBBEtoUBE_settings.json"
    assert gs.save_values({"bust_morph_chord": False}, p)
    first = p.read_bytes()
    assert gs.save_values({"bust_morph_chord": True}, p)
    bak = tmp_path / "CBBEtoUBE_settings.json.bak"
    assert bak.read_bytes() == first
    assert gs.load_values(p)["bust_morph_chord"] is True
    assert not list(tmp_path.glob("*.tmp*")), "temp file left behind"


def test_write_run_config_lands_next_to_the_summary(tmp_path):
    out = bi.write_run_config(tmp_path)
    assert out is not None and out.name == "conversion_settings.json"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "build" in data and "effective" in data


def test_write_run_config_names_the_pool_that_ran(tmp_path):
    """#report-checkpoint: the sidecar used to re-derive the worker count at
    write time, so it named a pool the run never used."""
    import json
    out = bi.write_run_config(tmp_path, workers=3)
    assert json.loads(out.read_text(encoding="utf-8"))["machine"]["workers"] == 3
