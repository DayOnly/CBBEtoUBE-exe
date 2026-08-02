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

"""The bone-driven SMP chain opt-in. #smp-chain-antipoke

`SMP_COLLISION_ONLY_ANTIPOKE` is scoped to shapes the XML declares as COLLIDERS,
and its comment says so. A cuirass whose single shape is a rigid torso welded to
an authored skirt is not a collider, so that flag cannot reach it -- which is why
this is a SEPARATE flag rather than a widening of that one. Widening a flag past
its documented scope is the failure its own comment warns about.

What matters here is that the two flags stay independent and that BOTH stay off
by default: the structurally identical `#smp-structural-relax` change was
measured on convex pauldrons and put MORE verts inside the body, so nothing in
this family gets a default until someone looks in game.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.nif_convert as nc  # noqa: E402


def test_chain_optin_defaults_off():
    """No default may change without an in-game A/B -- see the constant."""
    assert nc.SMP_CHAIN_ANTIPOKE is False


def test_collision_only_flag_now_defaults_on():
    """Promoted in 1.2 after a full-pack run and in-game use. The CHAIN flag
    beside it is a different, still-opt-in thing (see below)."""
    assert nc.SMP_COLLISION_ONLY_ANTIPOKE is True


def test_the_two_flags_are_independent():
    """Setting one must not imply the other.

    They admit different shapes for different reasons: one a declared collider
    with no per-vertex sim, the other a bone-driven chain garment. Collapsing
    them would silently widen the collider flag past its documented scope.

    Since 1.2 the collider flag defaults ON, so independence is shown the other
    way round: disabling it must NOT disable the chain flag.
    """
    import importlib
    import os
    prev = dict(os.environ)
    try:
        os.environ["CBBE2UBE_SMP_CHAIN_ANTIPOKE"] = "1"
        os.environ["CBBE2UBE_NO_SMP_ANTIPOKE"] = "1"
        m = importlib.reload(nc)
        assert m.SMP_CHAIN_ANTIPOKE is True
        assert m.SMP_COLLISION_ONLY_ANTIPOKE is False
    finally:
        os.environ.clear()
        os.environ.update(prev)
        importlib.reload(nc)


def test_chain_push_is_capped_not_the_default():
    """The 3.0 default is what spreads verts on convex regions."""
    assert nc.SMP_ANTIPOKE_MAX_PUSH == nc.ANTIPOKE_BUST_CLEAR
    assert nc.SMP_ANTIPOKE_MAX_PUSH < 3.0


def test_flag_reads_the_documented_env_var():
    import importlib
    import os
    prev = dict(os.environ)
    try:
        for val, want in (("1", True), ("true", True), ("on", True),
                          ("0", False), ("", False)):
            if val:
                os.environ["CBBE2UBE_SMP_CHAIN_ANTIPOKE"] = val
            else:
                os.environ.pop("CBBE2UBE_SMP_CHAIN_ANTIPOKE", None)
            assert importlib.reload(nc).SMP_CHAIN_ANTIPOKE is want, val
    finally:
        os.environ.clear()
        os.environ.update(prev)
        importlib.reload(nc)


def test_chain_push_is_separate_from_the_collision_only_budget():
    """Two paths, two budgets.

    SMP_ANTIPOKE_MAX_PUSH carries a SHARP optimum measured on the collision-only
    path (worse at 0.6, worse at 3.0). Reusing it for chain garments would have
    tied this path to someone else's measurement.
    """
    assert nc.SMP_CHAIN_ANTIPOKE_PUSH != nc.SMP_ANTIPOKE_MAX_PUSH
    assert nc.SMP_CHAIN_ANTIPOKE_PUSH == 2.0


def test_chain_push_reads_its_own_env_var():
    import importlib
    import os
    prev = dict(os.environ)
    try:
        os.environ["CBBE2UBE_SMP_CHAIN_PUSH"] = "1.5"
        m = importlib.reload(nc)
        assert m.SMP_CHAIN_ANTIPOKE_PUSH == 1.5
        # and does not disturb the collision-only budget
        assert m.SMP_ANTIPOKE_MAX_PUSH == m.ANTIPOKE_BUST_CLEAR
    finally:
        os.environ.clear()
        os.environ.update(prev)
        importlib.reload(nc)
