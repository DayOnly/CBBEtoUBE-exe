"""#numpy-block-cache: freed large numpy blocks are reused, arrays still hold what numpy promises, the cache keeps under
its budget and empties itself while the machine is low on memory -- and with the switch off nothing changes.

Each case that installs the handler runs in its own interpreter: a numpy data handler cannot be taken back out of a
process cleanly, and the pytest process must keep numpy's own."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import child_lifetime  # noqa: E402
from src import numpy_block_cache as nbc  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32" or not (_REPO / "native" / "npcache.pyd").is_file(),
                                reason="Windows-only; needs native/npcache.pyd (native/build_npcache.bat)")

_PRE = f"""
import json, sys, time
sys.path.insert(0, {str(_REPO)!r})
import numpy as np
from src import numpy_block_cache as nbc
def handler(a=None):
    return np._core.multiarray.get_handler_name(a) if a is not None else np._core.multiarray.get_handler_name()
"""


def _run(body: str, env_extra: "dict | None" = None) -> dict:
    import os
    env = {k: v for k, v in os.environ.items() if not k.startswith("CBBE2UBE_NUMPY_BLOCK_CACHE")}
    env.update(env_extra or {})
    out = subprocess.run([sys.executable, "-c", _PRE + body], capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_switched_off_by_default_numpy_keeps_its_own_handler():
    r = _run("print(json.dumps({'ok': nbc.install(), 'h': handler()}))")
    assert r == {"ok": False, "h": "default_allocator"}


def test_the_switch_installs_it():
    r = _run("print(json.dumps({'ok': nbc.install(), 'h': handler(np.ones(10**6))}))",
             {"CBBE2UBE_NUMPY_BLOCK_CACHE_MB": "64"})
    assert r == {"ok": True, "h": "npcache"}


def test_a_freed_block_is_handed_to_the_next_array_of_its_size():
    r = _run("""
nbc.install(64, 0)
a = np.empty(10**6); a[:] = 7; del a
s0 = nbc.stats()
b = np.empty(10**6)
s1 = nbc.stats()
print(json.dumps({'hits': s1['hits'] - s0['hits'], 'cached_before': s0['cached'], 'cached_after': s1['cached']}))
""")
    assert r["hits"] == 1 and r["cached_before"] >= 8 * 10**6 and r["cached_after"] == 0


def test_arrays_still_hold_what_numpy_promises():
    """zeros of a reused (dirty) block are zero; resize / fromiter / in-place resize (realloc) keep their data."""
    r = _run("""
nbc.install(64, 0)
for _ in range(20):
    a = np.empty(10**6); a[:] = 3.0; del a
    z = np.zeros(10**6); assert not z.any()
    d = np.resize(np.arange(300000.0), 700000); assert d[299999] == 299999 and d[300000] == 0
    x = np.fromiter(range(200000), float); assert x[-1] == 199999 and x.sum() == sum(range(200000))
    g = np.arange(100000.0); g.resize(400000, refcheck=False); assert g[99999] == 99999 and not g[100000:].any()
    small = np.arange(1000); assert small.sum() == 499500
print(json.dumps(dict(nbc.stats(), ok=True)))
""")
    assert r["ok"] and r["hits"] > 0


def test_the_cache_keeps_under_its_budget():
    r = _run("""
nbc.install(32, 0)
keep = [np.empty(10**6) for _ in range(20)]      # 20 x 8 MB live
del keep                                          # freed together: only 32 MB may stay
print(json.dumps(nbc.stats()))
""")
    assert r["cached"] <= 32 << 20 and r["peak_cached"] <= 32 << 20 and r["overflow"] > 0


def test_low_memory_empties_the_cache_and_stops_caching_until_it_recovers():
    """LOW above anything the machine has: the next check (at most every 100 ms) frees every cached block and
    suspends caching. LOW back at 0: it resumes."""
    r = _run("""
nbc.install(64, 0)
a = np.empty(10**6); del a
held = nbc.stats()['cached']
nbc.install(64, 10**9)                            # 1 PB: the machine is always 'low'
time.sleep(0.25)
b = np.empty(10**6); del b                        # a miss runs the check; the free must not cache
low = nbc.stats()
nbc.install(64, 0)
time.sleep(0.25)
c = np.empty(10**6); del c
back = nbc.stats()
print(json.dumps({'held': held, 'low_cached': low['cached'], 'low_suspended': low['suspended'],
                  'flushes': low['flushes'], 'back_cached': back['cached'], 'back_suspended': back['suspended']}))
""")
    assert r["held"] > 0
    assert r["low_cached"] == 0 and r["low_suspended"] == 1 and r["flushes"] >= 1
    assert r["back_suspended"] == 0 and r["back_cached"] > 0


def test_a_missing_module_changes_nothing(monkeypatch):
    monkeypatch.setattr(nbc, "_MOD", None)
    monkeypatch.setattr(nbc, "_load", lambda: None)
    assert nbc.install(64, 3072) is False


def test_every_pool_worker_installs_it_into_the_parents_tally(monkeypatch):
    seen = []
    monkeypatch.setattr(child_lifetime, "run_at_full_speed", lambda: seen.append("speed") or True)
    monkeypatch.setattr(nbc, "install", lambda *a, **k: seen.append(("cache", k.get("share"))) or False)
    child_lifetime.worker_init("Local\tally")
    assert seen == ["speed", ("cache", "Local\tally")]


_HOLDER = """
nbc.install(64, 0, share=sys.argv[1])
a = np.empty(10**6); del a                        # 8 MB now cached
print(json.dumps(nbc.stats()), flush=True)
cmd = sys.stdin.readline().strip()
if cmd == "flush":
    nbc.flush(); print(json.dumps(nbc.stats()), flush=True); sys.stdin.readline()
"""


def _holder(name):
    import os
    env = {k: v for k, v in os.environ.items() if not k.startswith("CBBE2UBE_NUMPY_BLOCK_CACHE")}
    p = subprocess.Popen([sys.executable, "-c", _PRE + _HOLDER, name], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         text=True, env=env)
    return p, json.loads(p.stdout.readline())


def test_workers_publish_what_they_hold_and_the_tally_follows_a_flush():
    """Two worker-like processes each hold 8 MB; the parent reads both through the shared tally, sees one drop to 0
    when it flushes (what a worker does at the end of every piece), and counts only the pids it asks for."""
    name = nbc.share_name() + "-test-a"
    reader = nbc._load()
    reader.share(name, 0)
    p1, s1 = _holder(name)
    p2, s2 = _holder(name)
    try:
        assert s1["cached"] > 0 and s2["cached"] > 0
        assert reader.shared_cached([p1.pid, p2.pid]) == s1["cached"] + s2["cached"]
        assert reader.shared_cached([p2.pid]) == s2["cached"]
        assert reader.shared_cached([]) == 0
        p1.stdin.write("flush\n"); p1.stdin.flush()
        assert json.loads(p1.stdout.readline())["cached"] == 0
        assert reader.shared_cached([p1.pid, p2.pid]) == s2["cached"]
    finally:
        for p in (p1, p2):
            p.kill(); p.wait()


def test_a_killed_workers_slot_is_never_counted_and_is_taken_over():
    """A worker killed while holding blocks leaves its slot behind. The scheduler asks only for the live pool's pids,
    and the next worker to claim a slot may take the dead one's."""
    name = nbc.share_name() + "-test-b"
    reader = nbc._load()
    reader.share(name, 0)
    dead, held = _holder(name)
    dead.kill(); dead.wait()
    live, s = _holder(name)
    try:
        assert reader.shared_cached([live.pid]) == s["cached"]
        assert reader.shared_cached([dead.pid]) in (0, held["cached"])   # whichever the slot reuse left
    finally:
        live.kill(); live.wait()


def test_the_scheduler_counts_cached_blocks_only_when_they_are_freed_before_its_reserve(monkeypatch):
    class _Fake:
        def shared_cached(self, pids):
            return (3 << 30) if pids else 0
    monkeypatch.setattr(nbc, "_MOD", _Fake())
    monkeypatch.setattr(nbc, "LOW_MB", 3072)
    assert nbc.reclaimable_gb([1, 2], 2.0) == 3.0
    assert nbc.reclaimable_gb([], 2.0) == 0.0
    monkeypatch.setattr(nbc, "LOW_MB", 2048)                  # freed only AT the reserve: too late, not counted
    assert nbc.reclaimable_gb([1, 2], 2.0) == 0.0
    monkeypatch.setattr(nbc, "_MOD", None)                    # switched off
    monkeypatch.setattr(nbc, "LOW_MB", 3072)
    assert nbc.reclaimable_gb([1, 2], 2.0) == 0.0


@pytest.mark.parametrize("runner", ["_run_unit", "_run_one"])
def test_a_worker_empties_its_cache_at_the_end_of_every_piece(monkeypatch, runner):
    """An idle worker holds nothing the scheduler could be waiting on -- even when the piece raised."""
    from src import auto_convert as ac
    flushed = []
    monkeypatch.setattr(nbc, "flush", lambda: flushed.append(1))
    run = getattr(ac, runner)
    ok = (lambda f: run(f, [1, 2])) if runner == "_run_unit" else (lambda f: run(f, 1))
    ok(lambda it: it)
    assert flushed == [1]

    def boom(_it):
        raise RuntimeError("piece failed")
    with pytest.raises(RuntimeError):
        ok(boom)
    assert flushed == [1, 1]


def test_both_switches_keep_an_incremental_run_from_reusing_nifs():
    """Not proven byte-neutral by a test, so they belong in the --incremental fingerprint."""
    from src import auto_convert as ac
    hashed = {n for g in ac._FINGERPRINT_HASHED_GROUPS.values() for n in g.get("names", ())}
    assert {"CBBE2UBE_NUMPY_BLOCK_CACHE_MB", "CBBE2UBE_NUMPY_BLOCK_CACHE_LOW_MB"} <= hashed
    assert not ({"CBBE2UBE_NUMPY_BLOCK_CACHE_MB", "CBBE2UBE_NUMPY_BLOCK_CACHE_LOW_MB"} & ac._FINGERPRINT_PLUMBING)
