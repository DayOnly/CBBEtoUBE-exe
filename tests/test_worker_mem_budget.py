# CBBEtoUBE - CBBE/3BA to UBE armor converter
# Copyright (C) 2026 DayOnly
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""#worker-mem-budget -- the default worker count is bounded by RAM as well as CPUs.

`cpu_count() - 1` assumes memory is free. Measured mid-reconvert on a 24-thread /
32 GB box: that default asked for 23 workers holding 58.9 GB of private bytes
against 31.8 GB physical, so Windows paged ~36 GB out and faulted it back at
200-744 pages/sec. The cap keeps the pool inside real memory.

These tests pin the SELECTION LOGIC only. The throughput benefit is UNPROVEN: a
20-mod A/B came back a tie (2.3%, inside noise) because it never reproduced the
pressure. Don't add a test asserting "16 is faster" -- see
`auto_convert.default_worker_count` for the full record."""
import os

from src import auto_convert as ac


def _dc(monkeypatch, *, gb, cpus, budget=None, commit_free=None):
    """Pin BOTH memory readings, so the answer cannot depend on the box the
    suite happens to run on.

    `commit_free` defaults to effectively unlimited (#commit-headroom) so the
    RAM-cap cases below measure the RAM cap alone. A CI runner has a real page
    file and real commit pressure, and leaving that unpatched would make these
    assertions pass or fail according to the agent's spare memory -- the exact
    class of test that reports a number nobody else can reproduce."""
    st = None if gb is None else {
        "total_gb": gb,
        "avail_gb": gb / 2.0,
        "commit_limit_gb": gb * 2.0,
        "commit_free_gb": 1e6 if commit_free is None else commit_free}
    monkeypatch.setattr(ac, "_memory_status", lambda: st)
    monkeypatch.setattr(os, "cpu_count", lambda: cpus)
    if budget is None:
        monkeypatch.delenv("CBBE2UBE_WORKER_MEM_GB", raising=False)
    else:
        monkeypatch.setenv("CBBE2UBE_WORKER_MEM_GB", budget)
    return ac.default_worker_count()


def test_ram_caps_the_pool_below_cpu_count(monkeypatch):
    # The measured case: 24 threads, 32 GB. Old default 23 -> thrashing.
    # 15, not 16: the count FLOORS now. `int(31.8/2.0 + 0.5)` rounded a 15.9
    # entitlement UP to 16 workers x 2.0 = 32.0 GB, i.e. 101% of a machine that
    # reports 31.8 -- granting more than the box physically has, before Windows,
    # MO2, the GUI or the child converter took a byte. #worker-mem-budget
    assert _dc(monkeypatch, gb=31.8, cpus=24) == 15


def test_the_pool_never_exceeds_the_machines_own_ram(monkeypatch):
    """The property the round-to-nearest broke, stated directly.

    This is the regression guard that matters: whatever the arithmetic, the
    pool's nominal claim must not exceed physical RAM. It is deliberately a
    PROPERTY over a sweep and not another hand-computed row, because the
    previous defect was invisible in exactly the rows someone chose to write
    down."""
    for gb in (4.0, 7.85, 8.0, 12.0, 15.85, 16.0, 24.0, 31.8, 32.0, 64.0):
        for cpus in (4, 8, 12, 16, 24, 32):
            n = _dc(monkeypatch, gb=gb, cpus=cpus)
            assert n >= 1
            # n == 1 is the floor and may legitimately overcommit a tiny box.
            assert n == 1 or n * ac.WORKER_MEM_BUDGET_GB <= gb, (
                f"{n} workers x {ac.WORKER_MEM_BUDGET_GB} GB exceeds {gb} GB "
                f"of RAM at {cpus} threads")


def test_cpu_caps_the_pool_when_ram_is_plentiful(monkeypatch):
    # 128 GB but only 8 threads: RAM allows 64, CPUs allow 7. CPU wins.
    assert _dc(monkeypatch, gb=128.0, cpus=8) == 7


def test_small_ram_machine_gets_a_small_pool(monkeypatch):
    assert _dc(monkeypatch, gb=8.0, cpus=16) == 4
    assert _dc(monkeypatch, gb=16.0, cpus=16) == 8


def test_never_returns_zero_on_a_tiny_machine(monkeypatch):
    assert _dc(monkeypatch, gb=0.5, cpus=2) == 1


def test_unknown_ram_falls_back_to_the_old_default(monkeypatch):
    # Must not silently collapse to 1 worker when RAM can't be read.
    assert _dc(monkeypatch, gb=None, cpus=24) == 23


def test_budget_is_tunable_for_ab_testing(monkeypatch):
    assert _dc(monkeypatch, gb=32.0, cpus=24, budget="4") == 8
    # A non-positive budget disables the RAM cap entirely.
    assert _dc(monkeypatch, gb=32.0, cpus=24, budget="0") == 23


def test_garbage_budget_env_does_not_crash(monkeypatch):
    assert _dc(monkeypatch, gb=32.0, cpus=24, budget="not-a-number") == 16


# --- #commit-headroom -------------------------------------------------------
#
# Windows fails an allocation against the COMMIT LIMIT (physical RAM + page
# file), not against free RAM, so untouched-but-committed bytes count in full.
# Two machines with the same RAM behave completely differently depending on
# whether the page file is system-managed or pinned small -- which is why the
# same build produces memory errors for some users and not others. The RAM cap
# alone cannot see that; this guard can, and may only ever LOWER the count.


def test_a_healthy_page_file_does_not_change_anything(monkeypatch):
    """The guard must be invisible on a normally-configured machine.

    If this ever starts binding on a stock setup it is a throughput regression
    affecting every user, so it is pinned rather than left to chance."""
    assert _dc(monkeypatch, gb=31.8, cpus=24, commit_free=88.0) == 15
    assert _dc(monkeypatch, gb=15.85, cpus=16, commit_free=28.0) == 7


def test_a_disabled_page_file_shrinks_the_pool(monkeypatch):
    """With the page file off, commit limit ~= RAM and the pool must shrink.

    16 GB / 16 threads with 9.5 GB committable. The RAM cap alone still says 7,
    which at the MEASURED 2.0 GB peak per worker plus 3.0 for the parent and
    0.75 for the window is 17.8 GB against 9.5 -- a hard MemoryError, not
    paging.

    2, not the 5 this asserted before 2026-09-11. The constants were re-priced
    from a real mid-run reading: per-worker commit is 1.05-1.98 GB on sampled
    populations (3.79 GB on the largest single source), not the 1.0 GB derived
    by subtraction, and the parent peaks at 2.7-2.8 GB rather than the 1.0 it
    was charged as one more worker."""
    assert _dc(monkeypatch, gb=15.85, cpus=16, commit_free=9.5) == 2
    assert _dc(monkeypatch, gb=15.85, cpus=16, commit_free=9.5) < \
        _dc(monkeypatch, gb=15.85, cpus=16, commit_free=64.0)


def test_the_guard_can_only_lower_never_raise(monkeypatch):
    """A vast page file must not license a pool the RAM cap refused.

    The failure this forbids is a 16 GB box with a 200 GB page file being told
    it may run 60 workers: it would not raise MemoryError, it would page itself
    to a standstill for hours, which is a worse user experience than the error."""
    for cf in (1.0, 8.0, 50.0, 1000.0):
        assert _dc(monkeypatch, gb=15.85, cpus=16, commit_free=cf) <= 7


def test_the_guard_never_returns_zero_or_negative(monkeypatch):
    """The room term goes negative on a nearly-full machine.

    Returning 0 would build an empty pool and 'convert' a modlist by doing
    nothing; a negative would raise inside ProcessPoolExecutor. Either is worse
    than one slow worker."""
    for cf in (0.05, 0.5, 1.0, 2.0, 2.5):
        assert _dc(monkeypatch, gb=15.85, cpus=16, commit_free=cf) >= 1


def test_a_machine_that_cannot_report_commit_still_works(monkeypatch):
    """Linux and any failed probe report None -- fall back to the RAM cap."""
    st = {"total_gb": 31.8, "avail_gb": 16.0,
          "commit_limit_gb": None, "commit_free_gb": None}
    monkeypatch.setattr(ac, "_memory_status", lambda: st)
    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    monkeypatch.delenv("CBBE2UBE_WORKER_MEM_GB", raising=False)
    assert ac.default_worker_count() == 15


def test_memory_status_reports_commit_on_this_machine():
    """The probe must actually read the two commit counters.

    `ullTotalPageFile` / `ullAvailPageFile` sat declared-but-unread in the
    struct for the life of the project. A probe that silently returns None for
    them would make every guard above dead code on real hardware while the
    monkeypatched tests stayed green -- 0/0 is not a pass."""
    st = ac._memory_status()
    if st is None:                       # non-Windows / probe unavailable
        return
    assert st["total_gb"] and 0.5 < st["total_gb"] < 4096
    if os.name == "nt":
        assert st["commit_limit_gb"] and st["commit_free_gb"] is not None
        # The commit limit is physical RAM plus the page file, so it can never
        # be less than RAM. A smaller value means the fields are misread.
        assert st["commit_limit_gb"] >= st["total_gb"] * 0.9
        assert 0 <= st["commit_free_gb"] <= st["commit_limit_gb"]


# --- #smp-collision-only-antipoke -------------------------------------------

def test_smp_antipoke_flag_defaults_off(monkeypatch):
    """The clearance-pass relaxation for SMP-rigged shapes must stay OPT-IN.

    Running anti-poke on SMP shapes was built, measured and reverted once before
    (it fixes flat regions but spreads verts on convex ones). The traced hide
    cuirass improves 6.3% -> 3.3% exposed with it on, but that is a partial win
    with a sharp optimum, so it ships off until confirmed in-game."""
    import importlib
    monkeypatch.setenv("CBBE2UBE_NO_SMP_ANTIPOKE", "1")
    nc = importlib.reload(importlib.import_module("src.nif_convert"))
    assert nc.SMP_COLLISION_ONLY_ANTIPOKE is False
    # Push budget is the clearance target, NOT the 3.0 default: measured 6.9% ->
    # 2.4% at 1.0 but WORSE (8.1%) at 0.6 and only 5.7% at 3.0.
    assert nc.SMP_ANTIPOKE_MAX_PUSH == nc.ANTIPOKE_BUST_CLEAR


def test_smp_antipoke_push_is_tunable_without_a_rebuild(monkeypatch):
    """The push budget must be adjustable separately from the clearance.

    They answer different questions -- clearance is "how far off the body at rest",
    the budget is "how far does the body TRAVEL". The live body config permits the
    breast chain -6.0..+3.0 units of linear travel against a clearance of 1.0, so
    the budget is several times short of the motion it exists to absorb. Raising it
    is the next lever, and trying one step must not cost a rebuild."""
    import importlib
    monkeypatch.setenv("CBBE2UBE_SMP_ANTIPOKE_PUSH", "2.5")
    nc = importlib.reload(importlib.import_module("src.nif_convert"))
    assert nc.SMP_ANTIPOKE_MAX_PUSH == 2.5
    monkeypatch.delenv("CBBE2UBE_SMP_ANTIPOKE_PUSH", raising=False)
    nc = importlib.reload(nc)
    assert nc.SMP_ANTIPOKE_MAX_PUSH == nc.ANTIPOKE_BUST_CLEAR, (
        "unset must fall back to the measured default, not to 0.0")


def test_every_playtestable_toggle_is_reachable_from_the_gui():
    """#env-only-is-dead-on-arrival.

    MO2 does not pass the environment to the program it launches, so a DEFAULT-OFF
    behaviour gated only on a CBBE2UBE_* variable can never be switched on by a
    normal user -- it can be built, measured, documented, and never once executed.
    That is exactly what happened to the SMP anti-poke opt-in, which sat unused
    carrying a recorded 6.3% -> 3.3% result, and to the XML bone remap, whose status
    was "OFF pending an in-game A/B" -- a test that was impossible to run.

    Numeric tuning knobs are fine env-only. A toggle someone intends to play-test is
    not: without a row, the intent is not real."""
    from src import gui_settings as gs
    rows = {s.env for s in gs.SETTINGS if s.env}
    for env in ("CBBE2UBE_NO_SMP_ANTIPOKE", "CBBE2UBE_SMP_ANTIPOKE_PUSH",
                "CBBE2UBE_NO_SKIN_INFLUENCE_CAP"):
        assert env in rows, f"{env} is unreachable from an MO2 launch"


def test_smp_antipoke_flag_opt_in(monkeypatch):
    import importlib
    monkeypatch.delenv("CBBE2UBE_NO_SMP_ANTIPOKE", raising=False)
    nc = importlib.reload(importlib.import_module("src.nif_convert"))
    assert nc.SMP_COLLISION_ONLY_ANTIPOKE is True
    monkeypatch.setenv("CBBE2UBE_NO_SMP_ANTIPOKE", "1")
    importlib.reload(nc)          # leave the module clean for other tests


def test_smp_antipoke_relaxation_is_collision_only(monkeypatch):
    """The constant is named SMP_COLLISION_ONLY_ANTIPOKE; the gate must match it.

    It previously read `FLAG and s.name not in hdt_softbody_names`, which admitted
    EVERY shape that is not a per-vertex soft-body -- including bone-driven SMP
    chain garments, which ARE simulated (the skirt-collapse family). An off-by-
    default flag whose name overstates its scope is how a bad default gets adopted
    later."""
    import inspect
    import src.nif_convert as nc
    from tests import _converter_sources as _cs
    src = _cs.orchestrator_source(nc.convert_nif_phase2)   # the loop is lifted; splice it back
    i = src.index("_smp_relax = (")
    gate = src[i:i + 220]
    assert "s.name in hdt_collider_names" in gate, (
        "the shape must itself be a declared per-triangle collider")
    assert "not hdt_softbody_names" in gate, (
        "and the NIF must contain no simulated cloth whose rest state rests "
        "against that collider")
