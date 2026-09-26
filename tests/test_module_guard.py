"""The guard that fails a test for leaving a converter module changed.

A fake assigned on a converter module without being undone stays there for
every later test in the process, so those tests pass or fail by the order the
suite runs in. `conftest.py` snapshots every `src.*` / `scripts.*` module
before each test and, once the test's own fixtures (monkeypatch included) are
torn down, puts back whatever the test left rebound and fails it by name --
tests/_module_guard.py. These tests plant each kind of leak, and each kind of
legitimate rebinding, on a throw-away module and read what the guard does.
"""
import functools
import importlib
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

from tests import _module_guard as mg

REPO = Path(__file__).resolve().parents[1]

_PROBE_SRC = textwrap.dedent('''
    import os

    SWITCH = not bool(os.environ.get("GUARD_PROBE_OFF"))
    _MEMO = {}
    _LAZY = None


    def helper(x, scale=lambda v: v):
        return scale(x) * 2


    def fill(key):
        _MEMO[key] = helper(key)


    def build_lazy():
        global _LAZY
        _LAZY = ("built",)


    def plant_late():
        global _LATE
        _LATE = 1


    class Thing:
        pass
''')


@pytest.fixture
def make_probe(tmp_path, monkeypatch):
    """Import a throw-away module as `src.<stem>` from a real file, so the
    guard can read which names its own code rebinds and `importlib.reload`
    works on it exactly as on a converter module."""
    import src
    monkeypatch.setattr(src, "__path__", [*src.__path__, str(tmp_path)])
    monkeypatch.delenv("GUARD_PROBE_OFF", raising=False)
    made = []

    def _make(stem="_guard_probe"):
        (tmp_path / f"{stem}.py").write_bytes(_PROBE_SRC.encode("utf-8"))
        mg._RUN_TIME_NAMES.pop(f"src.{stem}", None)
        made.append(stem)
        return importlib.import_module(f"src.{stem}")

    yield _make
    for stem in made:
        sys.modules.pop(f"src.{stem}", None)
        src.__dict__.pop(stem, None)


@pytest.fixture
def probe(make_probe):
    return make_probe()


def _probe_snap(*mods):
    return [s for s in mg.snapshot() if any(s[1] is m for m in mods)]


def test_a_bare_rebinding_is_reported_and_put_back(probe):
    snaps = _probe_snap(probe)
    real = probe.helper
    probe.helper = lambda x: 0
    leaks = mg.settle(snaps)
    assert len(leaks) == 1 and leaks[0].startswith("src._guard_probe.helper: rebound")
    assert probe.helper is real, "the original must be back for the next test"


def test_a_switch_set_by_hand_is_a_leak(probe):
    snaps = _probe_snap(probe)
    probe.SWITCH = False
    leaks = mg.settle(snaps)
    assert [ln.split(":")[0] for ln in leaks] == ["src._guard_probe.SWITCH"]
    assert probe.SWITCH is True


def test_added_and_deleted_names_are_reported_and_undone(probe):
    snaps = _probe_snap(probe)
    probe.planted = object()
    real = probe.Thing
    del probe.Thing
    leaks = sorted(mg.settle(snaps))
    assert leaks == ["src._guard_probe.Thing: deleted and not put back",
                     "src._guard_probe.planted: added and left behind"]
    assert not hasattr(probe, "planted") and probe.Thing is real


def test_a_cache_filled_in_place_is_not_a_leak(probe):
    snaps = _probe_snap(probe)
    probe.fill(3)
    assert probe._MEMO == {3: 6}
    assert mg.settle(snaps) == []


def test_the_modules_own_run_time_state_fails_no_test_but_is_put_back(probe):
    """`global _LAZY` in the module's own source: its code rebinds it by
    design, so no test is failed for it -- but neither the value its code
    built nor a fake a test assigned by hand reaches the next test, and a
    run-time global the module did not have before the test is removed."""
    assert {"_LAZY", "_LATE"} <= mg.run_time_names(probe)
    snaps = _probe_snap(probe)
    probe.build_lazy()
    probe.plant_late()
    assert mg.settle(snaps) == []
    assert probe._LAZY is None and not hasattr(probe, "_LATE")
    snaps = _probe_snap(probe)
    probe._LAZY = ("fake",)
    assert mg.settle(snaps) == []
    assert probe._LAZY is None


def test_a_reload_is_not_a_leak_and_the_originals_come_back(probe):
    """A reload re-executes every `def`: new objects, same code -- even the
    default `lambda`. Not a leak; the pre-test objects are put back so
    anything that imported them by name still holds the module's own."""
    snaps = _probe_snap(probe)
    real_helper, real_thing = probe.helper, probe.Thing
    probe.fill(1)
    importlib.reload(probe)
    assert probe.helper is not real_helper and probe._MEMO == {}
    assert mg.settle(snaps) == []
    assert probe.helper is real_helper and probe.Thing is real_thing
    assert probe._MEMO == {1: 2}, "the emptied cache is the pre-test one again"


def test_a_reload_left_under_a_non_default_env_is_a_leak(probe, monkeypatch):
    snaps = _probe_snap(probe)
    monkeypatch.setenv("GUARD_PROBE_OFF", "1")
    importlib.reload(probe)
    leaks = mg.settle(snaps)
    assert [ln.split(":")[0] for ln in leaks] == ["src._guard_probe.SWITCH"]
    assert probe.SWITCH is True


def test_a_wraps_spy_without_a_reload_is_a_leak(probe):
    """A `functools.wraps` spy unwraps to the original's code -- which is what
    a reload's copy looks like. With no reload it is a fake like any other."""
    snaps = _probe_snap(probe)
    real = probe.fill
    calls = []

    @functools.wraps(real)
    def spy(*a, **k):
        calls.append(a)
        return real(*a, **k)

    probe.fill = spy
    leaks = mg.settle(snaps)
    assert [ln.split(":")[0] for ln in leaks] == ["src._guard_probe.fill"]
    assert "rebound" in leaks[0]
    assert probe.fill is real


def test_an_emptied_table_without_a_reload_is_a_leak(probe):
    """A fresh empty dict is what a reload rebuilds `_MEMO = {}` into; with no
    reload, emptying a filled table is a rebinding the next test inherits."""
    probe.fill(2)
    snaps = _probe_snap(probe)
    filled = probe._MEMO
    probe._MEMO = {}
    leaks = mg.settle(snaps)
    assert [ln.split(":")[0] for ln in leaks] == ["src._guard_probe._MEMO"]
    assert probe._MEMO is filled and filled == {2: 4}


def test_a_reload_excuses_only_the_module_that_was_reloaded(make_probe):
    """Reloading one module in a test does not excuse a spy, or an emptied
    table, on another module the test never reloaded."""
    a, b = make_probe("_guard_probe_a"), make_probe("_guard_probe_b")
    b.fill(5)
    snaps = _probe_snap(a, b)
    real = b.fill
    importlib.reload(a)
    b.fill = functools.wraps(real)(lambda *x, **k: real(*x, **k))
    b._MEMO = {}
    leaks = sorted(ln.split(":")[0] for ln in mg.settle(snaps))
    assert leaks == ["src._guard_probe_b._MEMO", "src._guard_probe_b.fill"]
    assert b.fill is real and b._MEMO == {5: 10}


def test_a_replaced_module_object_is_reported_and_put_back(probe):
    snaps = _probe_snap(probe)
    sys.modules["src._guard_probe"] = types.ModuleType("src._guard_probe")
    leaks = mg.settle(snaps)
    assert leaks == ["src._guard_probe: sys.modules entry replaced or removed"]
    assert sys.modules["src._guard_probe"] is probe


def test_interpreter_bookkeeping_and_a_submodule_import_are_not_leaks(probe):
    snaps = _probe_snap(probe)
    probe.__warningregistry__ = {"version": 0}
    sub = types.ModuleType("src._guard_probe.child")
    probe.child = sub
    assert mg.settle(snaps) == []


def test_the_real_converter_caches_are_recognised():
    """The names the converter's own code rebinds, read from its source --
    the skeleton caches are assigned through `_nc()` from a sibling."""
    nc = importlib.import_module("src.nif_convert")
    assert {"_SKELETON_BONES_CACHE", "_SKELETON_PARENTS_CACHE"} <= mg.run_time_names(nc)
    assert "CHAIN_REST_LIFT" not in mg.run_time_names(nc)


_PLANTED = '''
import functools

import src.nif_convert as nc
import src.nif_convert_skinframe as sf

REAL = sf._shape_global_to_skin
REAL_V = sf._verts_skin_to_world
TABLE = nc.NIPPLE_TIP_BONE_WEIGHTS


def test_1_leaks_on_a_split_sibling():
    sf._shape_global_to_skin = lambda s: None


def test_2_sees_the_original():
    assert sf._shape_global_to_skin is REAL


def test_3_monkeypatch_is_undone_before_the_guard_looks(monkeypatch):
    monkeypatch.setattr(sf, "_shape_global_to_skin", lambda s: None)
    monkeypatch.setattr(nc, "CHAIN_REST_LIFT", not nc.CHAIN_REST_LIFT)


def test_4_leaks_a_wraps_spy():
    sf._verts_skin_to_world = functools.wraps(REAL_V)(
        lambda *a, **k: REAL_V(*a, **k))


def test_5_leaks_an_emptied_table():
    nc.NIPPLE_TIP_BONE_WEIGHTS = {}


def test_6_leaves_per_piece_state_bound():
    nc._PIECE_HDT_XML_TEXT = "<system/>"


def test_7_sees_everything_put_back():
    assert sf._verts_skin_to_world is REAL_V
    assert nc.NIPPLE_TIP_BONE_WEIGHTS is TABLE and TABLE
    assert nc._PIECE_HDT_XML_TEXT is None
'''


def test_the_suite_fails_the_leaking_test_and_no_other(tmp_path):
    """End to end through pytest itself, with this suite's conftest loaded as
    a plugin: each test that leaks -- a bare fake, a wraps spy, an emptied
    table, none of them excused by a reload -- errors at teardown naming
    `module.attr`; the converter's per-piece state set by hand fails nothing;
    the later tests see every original; and a monkeypatch-only test is clean
    -- the guard is torn down after monkeypatch has undone its patches."""
    f = tmp_path / "test_planted_leak.py"
    f.write_bytes(_PLANTED.encode("utf-8"))
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-p", "tests.conftest", "--rootdir", str(tmp_path),
         "--basetemp", str(tmp_path / "bt"), str(f)],
        cwd=REPO, capture_output=True, text=True, timeout=300)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    errors = [ln.rsplit("::", 1)[-1] for ln in out.splitlines()
              if ln.startswith(("ERROR ", "FAILED "))]
    assert errors == ["test_1_leaks_on_a_split_sibling",
                      "test_4_leaks_a_wraps_spy",
                      "test_5_leaks_an_emptied_table"], out
    assert "7 passed, 3 errors" in out, out
    for leak in ("src.nif_convert_skinframe._shape_global_to_skin: rebound",
                 "src.nif_convert_skinframe._verts_skin_to_world: rebound",
                 "src.nif_convert.NIPPLE_TIP_BONE_WEIGHTS: rebound"):
        assert leak in out, out
    assert "_PIECE_HDT_XML_TEXT" not in out, out
