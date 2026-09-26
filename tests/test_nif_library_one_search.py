"""#nif-library-one-search: every NIF read uses the library the conversion uses.

src/nif_io.py imported pynifly once, from PYNIFLY_PATH alone when that was
set; nif_convert._pynifly() looks in the repo's `.pynifly/`. With a
PYNIFLY_PATH that no longer held the library, the conversion ran while every
nif_io read failed ("'NoneType' object has no attribute 'NifFile'"), so the
zeroed-body check called the game's bodies unreadable and the run fell back
to finding bodies by name. The split is reproduced in a child process (the
import happens once per process); CBBE2UBE_NO_NIF_LIBRARY_RETRY=1 is the
control that keeps it.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import nif_io  # noqa: E402
from src import zeroed_body as zb  # noqa: E402

_HAS_VENDORED = (_REPO / ".pynifly" / "pyn").is_dir()

_CHILD = textwrap.dedent("""
    import json, os, sys
    sys.path.insert(0, os.getcwd())
    from src import nif_io
    out = {"at_import": nif_io.pynifly is not None}
    lib = nif_io.library()
    out["library"] = lib is not None
    nif = sys.argv[1]
    try:
        out["shapes"] = len(nif_io.open_nif_retry(nif, attempts=1).shapes)
    except Exception as e:
        out["open_error"] = type(e).__name__ + ": " + str(e)
    if lib is not None:
        from src import nif_convert
        out["same_as_conversion"] = nif_convert._pynifly() is lib
    print(json.dumps(out))
""")


def _nif(tmp_path) -> Path:
    """An empty but valid NIF, written with the library this process has."""
    lib = nif_io.library()
    if lib is None:
        pytest.skip("pynifly is not importable here")
    p = tmp_path / "empty.nif"
    nf = lib.NifFile()
    nf.initialize("SKYRIMSE", str(p))
    nf.save()
    return p


def _child(tmp_path, nif: Path, **env) -> dict:
    import json
    e = {k: v for k, v in os.environ.items()
         if not k.startswith("CBBE2UBE_") and k != "PYNIFLY_PATH"}
    e["PYNIFLY_PATH"] = str(tmp_path / "moved-away")    # a path that is gone
    e.update(env)
    script = tmp_path / "child.py"
    script.write_text(_CHILD, encoding="utf-8")
    r = subprocess.run([sys.executable, "-B", str(script), str(nif)],
                       cwd=str(_REPO), env=e, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    if got["at_import"]:
        pytest.skip("pyn is importable without the vendored folder here -- "
                    "the stale-path split cannot occur")
    return got


@pytest.mark.skipif(not _HAS_VENDORED, reason="no vendored .pynifly")
def test_a_stale_library_path_still_reads_nifs_with_the_conversions_library(tmp_path):
    got = _child(tmp_path, _nif(tmp_path))
    assert got["library"] is True
    assert got.get("shapes") == 0, got.get("open_error")
    assert got["same_as_conversion"] is True


@pytest.mark.skipif(not _HAS_VENDORED, reason="no vendored .pynifly")
def test_the_switch_keeps_the_single_import(tmp_path):
    got = _child(tmp_path, _nif(tmp_path), CBBE2UBE_NO_NIF_LIBRARY_RETRY="1")
    assert got["library"] is False
    assert "NoneType" in got["open_error"]


# --- the zeroed-body probe says WHY it could not check a body --------------

BODY_DIR = "meshes/actors/character/character assets"
VERTS = np.array([[float(i), 0.0, 0.0] for i in range(4)])


def _modlist(tmp_path, monkeypatch):
    """A game-loaded body whose read fails, next to a slider set that builds it."""
    root = tmp_path / "mods"
    body = root / "Out" / BODY_DIR
    body.mkdir(parents=True)
    for w in ("_0", "_1"):
        (body / f"femalebody{w}.nif").write_bytes(b"nif")
    sets = root / "Body Mod" / "CalienteTools" / "BodySlide" / "SliderSets"
    sets.mkdir(parents=True)
    (sets / "Body.osp").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<SliderSetInfo version="1">\n'
        '  <SliderSet name="Body"><DataFolder>Body</DataFolder>'
        '<SourceFile>Body.nif</SourceFile>'
        '<OutputPath>meshes\\actors\\character\\character assets</OutputPath>'
        '<OutputFile GenWeights="true">femalebody</OutputFile>'
        '<Shape target="Body">Body</Shape></SliderSet>\n</SliderSetInfo>\n',
        encoding="utf-8")
    monkeypatch.setitem(zb.KINDS, "cbbe", (BODY_DIR, "femalebody", len(VERTS), "CBBE 3BA"))

    def no_read(p):
        raise AttributeError("'NoneType' object has no attribute 'NifFile'")
    monkeypatch.setattr(zb, "_shape_sizes", no_read)
    zb._CACHE.clear()
    return root


def test_a_missing_library_is_not_reported_as_an_unreadable_body(tmp_path, monkeypatch):
    root = _modlist(tmp_path, monkeypatch)
    monkeypatch.setattr(nif_io, "library", lambda: None)
    with pytest.raises(zb.ZeroedBodyError) as ei:
        zb.zeroed_body("cbbe", "_1", mods_root=root, order=["Out", "Body Mod"])
    msg = str(ei.value)
    assert "NIF library (pynifly) is not loaded" in msg
    assert "unreadable" not in msg


def test_with_the_library_loaded_a_failed_read_is_an_unreadable_body(tmp_path, monkeypatch):
    root = _modlist(tmp_path, monkeypatch)
    monkeypatch.setattr(nif_io, "library", lambda: object())
    with pytest.raises(zb.ZeroedBodyError, match="unreadable"):
        zb.zeroed_body("cbbe", "_1", mods_root=root, order=["Out", "Body Mod"])


def test_the_body_list_says_the_library_is_missing(tmp_path, monkeypatch):
    root = _modlist(tmp_path, monkeypatch)
    pair = {w: root / "Out" / BODY_DIR / f"femalebody{w}.nif" for w in ("_0", "_1")}
    monkeypatch.setattr(nif_io, "library", lambda: None)
    c = zb._candidate(None, "cbbe", "Out", pair, True, {})
    assert c.status == "unreadable" and "NIF library (pynifly) is not loaded" in c.reason
    monkeypatch.setattr(nif_io, "library", lambda: object())
    c = zb._candidate(None, "cbbe", "Out", pair, True, {})
    assert c.status == "unreadable" and "is unreadable (AttributeError)" in c.reason
