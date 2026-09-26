"""The guard that fails a test for leaving a converter module changed.

A fake assigned on a converter module without being undone stays there for
every later test in the process, so those tests pass or fail by the order the
suite runs in. `conftest.py` snapshots every `src.*` / `scripts.*` module
before each test and, once the test's own fixtures (monkeypatch included) are
torn down, puts back whatever the test left rebound and fails it by name --
tests/_module_guard.py. These tests plant each kind of leak, and each kind of
legitimate rebinding, on a throw-away module and read what the guard does.
"""
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


    class Thing:
        pass
''')


@pytest.fixture
def probe(tmp_path, monkeypatch):
    """A watched module (`src.` prefix) whose source is a real file, so the
    guard can read which names its own code rebinds."""
    path = tmp_path / "guard_probe.py"
    path.write_bytes(_PROBE_SRC.encode("utf-8"))
    mod = types.ModuleType("src._guard_probe")
    mod.__file__ = str(path)
    exec(compile(_PROBE_SRC, str(path), "exec"), mod.__dict__)
    monkeypatch.setitem(sys.modules, "src._guard_probe", mod)
    monkeypatch.delenv("GUARD_PROBE_OFF", raising=False)
    mg._RUN_TIME_NAMES.pop("src._guard_probe", None)
    return mod


def _reexec(mod):
    """What `importlib.reload` does: run the source again in the same dict."""
    exec(compile(_PROBE_SRC, mod.__file__, "exec"), mod.__dict__)


def _probe_snap(mod):
    return [s for s in mg.snapshot() if s[1] is mod]


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


def test_the_modules_own_run_time_cache_is_left_to_its_code(probe):
    """`global _LAZY` in the module's own source: its code rebinds it by
    design, so the rebinding is neither a leak nor undone."""
    assert "_LAZY" in mg.run_time_names(probe)
    snaps = _probe_snap(probe)
    probe.build_lazy()
    assert mg.settle(snaps) == []
    assert probe._LAZY == ("built",)


def test_a_reload_is_not_a_leak_and_the_originals_come_back(probe):
    """A reload re-executes every `def`: new objects, same code -- even the
    default `lambda`. Not a leak; the pre-test objects are put back so
    anything that imported them by name still holds the module's own."""
    snaps = _probe_snap(probe)
    real_helper, real_thing = probe.helper, probe.Thing
    probe.fill(1)
    _reexec(probe)
    assert probe.helper is not real_helper
    assert mg.settle(snaps) == []
    assert probe.helper is real_helper and probe.Thing is real_thing
    assert probe._MEMO == {1: 2}, "the emptied cache is the pre-test one again"


def test_a_reload_left_under_a_non_default_env_is_a_leak(probe, monkeypatch):
    snaps = _probe_snap(probe)
    monkeypatch.setenv("GUARD_PROBE_OFF", "1")
    _reexec(probe)
    leaks = mg.settle(snaps)
    assert [ln.split(":")[0] for ln in leaks] == ["src._guard_probe.SWITCH"]
    assert probe.SWITCH is True


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
import src.nif_convert as nc
import src.nif_convert_skinframe as sf

REAL = sf._shape_global_to_skin


def test_1_leaks_on_a_split_sibling():
    sf._shape_global_to_skin = lambda s: None


def test_2_sees_the_original():
    assert sf._shape_global_to_skin is REAL


def test_3_monkeypatch_is_undone_before_the_guard_looks(monkeypatch):
    monkeypatch.setattr(sf, "_shape_global_to_skin", lambda s: None)
    monkeypatch.setattr(nc, "CHAIN_REST_LIFT", not nc.CHAIN_REST_LIFT)
'''


def test_the_suite_fails_the_leaking_test_and_no_other(tmp_path):
    """End to end through pytest itself, with this suite's conftest loaded as
    a plugin: the test that leaks errors at teardown naming `module.attr`,
    the next test sees the original, and a monkeypatch-only test is clean --
    the guard is torn down after monkeypatch has undone its patches."""
    f = tmp_path / "test_planted_leak.py"
    f.write_bytes(_PLANTED.encode("utf-8"))
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-p", "tests.conftest", "--rootdir", str(tmp_path),
         "--basetemp", str(tmp_path / "bt"), str(f)],
        cwd=REPO, capture_output=True, text=True, timeout=300)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "3 passed, 1 error" in out, out
    errors = [ln for ln in out.splitlines() if ln.startswith(("ERROR ", "FAILED "))]
    assert len(errors) == 1, out
    assert errors[0].endswith("test_planted_leak.py::test_1_leaks_on_a_split_sibling"), out
    assert "src.nif_convert_skinframe._shape_global_to_skin: rebound" in out, out
