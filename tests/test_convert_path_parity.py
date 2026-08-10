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

"""#convert-path-parity -- both convert paths must run the same passes.

THE DEFECT CLASS THIS LOCKS DOWN. `convert_nif` and `convert_nif_phase2` both
write final meshes, and which one a piece takes depends on whether it has a body
shape to swap. A pass wired into only one of them therefore splits the pack in
half silently: the meshes that took the other path are simply missing that fix,
and nothing on disk says so. It has happened twice and cost real time --
`#seam-weld-self` records "wiring it only into the chain left the copy-path
pieces torn ... the cuirass 0 split seams but its gauntlets 60, worst 2.95u,
boots 18, first-person cuirass 70", and the coherence repair carries the same
warning ("wiring the seam weld into only ONE of these two sites left 5% of the
pack torn last time; both sites, both passes").

Nothing enforced it. This does.

REACHABILITY, NOT DIRECT CALLS. Most passes are reached through `_copy_shape`
or `_reauthor_nif_fresh`, so a direct-call comparison reports six differences
that are all false. The check walks the call graph.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from src import nif_convert as nc

# Prefixes that mark a fit/geometry PASS -- the things that must not diverge.
PASS_PREFIXES = ("_match_", "_conform", "_repair", "_weld", "_transfer_",
                 "_graft", "_seed_", "_separate_", "_inflate", "_strip_",
                 "_refresh_")
ENTRY_A = "convert_nif"
ENTRY_B = "convert_nif_phase2"
# Guard against a vacuous pass. If the walk finds fewer passes than this, the
# analysis broke (renamed helpers, changed structure) and "they match" would be
# a statement about an empty set, not about the code.
MIN_PASSES = 20


def _module_ast():
    return ast.parse(Path(inspect.getfile(nc)).read_text(encoding="utf8"))


def _call_graph(tree):
    top = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

    def calls(fn):
        out = set()
        for n in ast.walk(fn):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            nm = getattr(f, "id", None) or getattr(f, "attr", None)
            if nm in top:
                out.add(nm)
        return out

    return {k: calls(v) for k, v in top.items()}


def _reachable(graph, start, stop=()):
    """Reachable set, NOT descending into `stop`.

    `convert_nif` DISPATCHES to `convert_nif_phase2` for body-swap pieces, so a
    naive walk from `convert_nif` swallows everything phase 2 reaches and the
    two sets compare equal no matter what -- the comparison would be vacuous in
    one direction by construction. The paths are ALTERNATIVES for a given piece,
    so the dispatch edge is a boundary, not an inclusion."""
    seen, stack = set(), [start]
    while stack:
        cur = stack.pop()
        if cur in stop and cur != start:
            continue
        for nxt in graph.get(cur, ()):
            if nxt not in seen:
                seen.add(nxt)
                if nxt not in stop:
                    stack.append(nxt)
    return seen


def _passes_from(graph, entry, stop=()):
    return {c for c in _reachable(graph, entry, stop)
            if any(c.startswith(p) or p in c for p in PASS_PREFIXES)}


# Passes that legitimately run on the phase-2 path only, each with the reason.
# A new name may ONLY be added here with a reason -- that is the whole point.
KNOWN_PHASE2_ONLY = {
    # Cross-shape passes: they take `shape_jobs`, the whole-piece view, and
    # decide layering / host relationships BETWEEN shapes. Only phase 2 builds
    # that structure; the copy path hands `_copy_shape` one shape at a time, so
    # there is nothing for them to operate on there.
    "_repair_layer_order": "needs shape_jobs (whole-piece view)",
    "_conform_cords_to_host": "needs shape_jobs (whole-piece view)",
    # NOT structurally excluded -- this one is per-shape and could run on the
    # copy path. No comment in the source explains the asymmetry, so it is
    # recorded as an open question rather than blessed. Wiring it in is a
    # behaviour change and needs its own A/B plus the clearance counter-metric,
    # not a quiet edit. See the 2026-08-05 path-parity review.
    "_inflate_cloth_over_bust_butt": "OPEN: per-shape, no documented reason",
}


def test_no_pass_runs_only_on_the_copy_path():
    """Nothing may run on `convert_nif` alone. That direction has no legitimate
    case: every piece phase 2 handles also has the geometry the copy path has."""
    g = _call_graph(_module_ast())
    a = _passes_from(g, ENTRY_A, stop={ENTRY_B})
    b = _passes_from(g, ENTRY_B)
    assert len(a) >= MIN_PASSES, (
        f"only {len(a)} passes reachable from {ENTRY_A}; the call-graph walk "
        f"has broken, so any 'paths agree' result would be vacuous")
    only_a = sorted(a - b)
    assert not only_a, (
        f"these passes run on the copy path but NOT on phase 2, which splits "
        f"the pack silently: {only_a}")


def test_phase2_only_passes_are_the_known_documented_set():
    """Phase 2 may run extra passes, but only ones already reasoned about.
    A NEW name here means someone wired a pass into one path and not the other
    -- the defect that tore 5% of the pack twice."""
    g = _call_graph(_module_ast())
    a = _passes_from(g, ENTRY_A, stop={ENTRY_B})
    b = _passes_from(g, ENTRY_B)
    extra = b - a
    undocumented = sorted(extra - set(KNOWN_PHASE2_ONLY))
    assert not undocumented, (
        f"new phase-2-only pass(es): {undocumented}\nWire them into "
        f"{ENTRY_A} too, or add them to KNOWN_PHASE2_ONLY WITH A REASON.")
    # and the list must not rot: every entry must still actually be phase2-only
    stale = sorted(set(KNOWN_PHASE2_ONLY) - extra)
    assert not stale, (
        f"KNOWN_PHASE2_ONLY lists {stale}, which now run on both paths (or no "
        f"longer exist). Remove the stale entries so the list keeps meaning "
        f"something.")


def test_the_parity_check_can_actually_fail():
    """GUARD THE GUARD. Delete one pass call from a copy of the source and the
    check must notice. Without this, a walk that silently stopped returning
    anything would keep reporting 'paths agree' forever."""
    graph = _call_graph(_module_ast())
    stop = {ENTRY_B}
    # The victim must be one phase 2 calls DIRECTLY, so that severing every
    # other caller leaves B's own edge intact -- otherwise the mutation removes
    # the pass from both sides and proves nothing.
    shared = sorted(_passes_from(graph, ENTRY_A, stop)
                    & _passes_from(graph, ENTRY_B)
                    & graph[ENTRY_B])
    assert shared, "no directly-called shared pass to mutate -- cannot verify"

    # Sever every call to one shared pass, so the copy path can no longer reach
    # it while phase 2 still does. `convert_nif` DISPATCHES to phase 2, so the
    # walk must stop there or A trivially contains B and this proves nothing --
    # that flaw is exactly what this test caught when it was first written.
    victim = shared[0]
    broken = dict(graph)
    for k in list(broken):
        if k != ENTRY_B:
            broken[k] = {c for c in broken[k] if c != victim}
    a = {c for c in _reachable(broken, ENTRY_A, stop)
         if any(c.startswith(p) or p in c for p in PASS_PREFIXES)}
    b = {c for c in _reachable(broken, ENTRY_B)
         if any(c.startswith(p) or p in c for p in PASS_PREFIXES)}
    assert victim not in a, f"mutation did not take for {victim!r}"
    assert victim in b, f"{victim!r} should still be reachable from {ENTRY_B}"
    assert (b - a), "the check would NOT have noticed a pass missing from one path"


def test_pass_prefixes_still_match_real_helpers():
    """The prefix list is the whole basis of the check; if a rename made it
    match nothing, both sets would be empty and equal."""
    g = _call_graph(_module_ast())
    found = _passes_from(g, ENTRY_B)
    assert len(found) >= MIN_PASSES, (
        f"PASS_PREFIXES matched only {len(found)} helpers -- update the list")
