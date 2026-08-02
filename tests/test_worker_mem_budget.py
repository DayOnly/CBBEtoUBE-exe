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


def _dc(monkeypatch, *, gb, cpus, budget=None):
    monkeypatch.setattr(ac, "_total_physical_gb", lambda: gb)
    monkeypatch.setattr(os, "cpu_count", lambda: cpus)
    if budget is None:
        monkeypatch.delenv("CBBE2UBE_WORKER_MEM_GB", raising=False)
    else:
        monkeypatch.setenv("CBBE2UBE_WORKER_MEM_GB", budget)
    return ac.default_worker_count()


def test_ram_caps_the_pool_below_cpu_count(monkeypatch):
    # The measured case: 24 threads, 32 GB. Old default 23 -> thrashing.
    assert _dc(monkeypatch, gb=31.8, cpus=24) == 16


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


def test_total_physical_gb_is_plausible_on_this_machine():
    # Stdlib-only probe (psutil is EXCLUDED from the frozen spec, so it must
    # never be the mechanism here).
    gb = ac._total_physical_gb()
    assert gb is None or 0.5 < gb < 4096


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
    src = inspect.getsource(nc.convert_nif_phase2)
    i = src.index("_smp_relax = (")
    gate = src[i:i + 220]
    assert "s.name in hdt_collider_names" in gate, (
        "the shape must itself be a declared per-triangle collider")
    assert "not hdt_softbody_names" in gate, (
        "and the NIF must contain no simulated cloth whose rest state rests "
        "against that collider")
