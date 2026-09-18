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

"""The shared stage hook must not quietly hand a path a recorder it never had.

Step (0) of the two-path unification. The two paths' pass-boundary hooks looked
like duplicates and were NOT: the body-swap one feeds `_tracer` and `_chain`,
and `_chain` is the ROLLBACK checkpoint wrapping seam-weld / coherence / strap
/ short-edge. The copy path's fed only survival and dump.

So the danger in sharing them is not a typo, it is a BEHAVIOUR CHANGE wearing a
refactor's clothes: give the copy path a chain checkpoint and it silently gains
rollback semantics it has never had, on 78% of a pack, with no flag and no
verdict. That is what these tests exist to stop.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.nif_convert_telemetry import make_stage_hook   # noqa: E402
from tests import _converter_sources as cs              # noqa: E402


class _Rec:
    def __init__(self):
        self.calls = []

    def mark(self, label, v):
        self.calls.append(("mark", label, v))

    def checkpoint(self, label, v):
        self.calls.append(("checkpoint", label, v))


def test_a_path_gets_exactly_the_recorders_it_passes():
    surv, dump, chain, tracer = _Rec(), _Rec(), _Rec(), _Rec()
    copy_hook = make_stage_hook(surv=surv, dump=dump, skip_none=True)
    copy_hook("warp", [1])
    assert len(surv.calls) == 1 and len(dump.calls) == 1
    assert not chain.calls and not tracer.calls, (
        "the copy path must not touch a recorder it did not pass")

    swap_hook = make_stage_hook(tracer=tracer, chain=chain, surv=surv,
                                dump=dump, skip_none=False)
    swap_hook("warp", [1])
    assert len(tracer.calls) == 1 and len(chain.calls) == 1


def test_the_None_guard_is_PER_PATH_and_not_normalised():
    """The copy path skipped a None snapshot; the body-swap path did not, so it
    would call `chain.checkpoint(label, None)`. Folding that guard in for
    everyone changes WHEN the rollback chain records a checkpoint -- an
    unexamined difference, kept explicit rather than quietly unified."""
    chain, surv = _Rec(), _Rec()
    make_stage_hook(surv=surv, skip_none=True)("warp", None)
    assert not surv.calls, "skip_none=True must drop a None snapshot"

    make_stage_hook(chain=chain, skip_none=False)("warp", None)
    assert chain.calls == [("checkpoint", "warp", None)], (
        "skip_none=False must preserve the body-swap path's behaviour of "
        "passing a None snapshot through to the rollback chain")


def test_the_COPY_path_call_site_asks_for_no_chain_and_no_tracer():
    """The invariant, read off the call site itself rather than trusted.

    A future edit that adds `chain=` here is the behaviour change this whole
    file exists to catch, and it would otherwise be invisible: geometry would
    not move on the golden pieces, because rollback only fires when a later
    verify FAILS.
    """
    txt = cs.whole_text()
    m = re.search(r"_stage_p1 = make_stage_hook\((.*?)\)", txt, re.S)
    assert m, "the copy path's stage hook is no longer built by the factory"
    args = m.group(1)
    assert "chain=" not in args, (
        "the COPY path just acquired the rollback chain checkpoint. That is a "
        "behaviour change on ~78% of a pack, not a refactor -- give it a flag "
        "and an A/B, or take it out")
    assert "tracer=" not in args, "the copy path just acquired tracer semantics"
    assert "skip_none=True" in args, (
        "the copy path's None guard was dropped; it fed only survival/dump and "
        "returned early on a None snapshot")


def test_the_SWAP_path_call_site_keeps_both_recorders():
    txt = cs.whole_text()
    m = re.search(r"_stage = make_stage_hook\((.*?)\)", txt, re.S)
    assert m, "the body-swap path's stage hook is no longer built by the factory"
    args = m.group(1)
    for need in ("tracer=", "chain=", "surv=", "dump="):
        assert need in args, (
            f"the body-swap path lost `{need}` -- `chain` is the rollback "
            f"checkpoint, and losing it disarms the rollback silently")
