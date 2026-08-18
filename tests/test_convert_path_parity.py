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
# Prefixes that mark a fit/geometry PASS -- the things that must not diverge.
#
# WIDENED 2026-08-18. The list used to end at "_refresh_", and the omissions
# were not harmless: the check saw 6 of the 35 stages the body-swap path calls
# and the copy path does not. `_inflate_cloth_over_bust_butt` was recorded here
# as an OPEN question purely because its name starts with "_inflate" -- while
# `clear_armor_outside_body`, the OTHER BRANCH OF ITS OWN if/elif, was invisible.
# A guard that reports three divergences when there are a dozen is worse than
# no guard, because it is read as assurance.
PASS_PREFIXES = ("_match_", "_conform", "_repair", "_weld", "_transfer_",
                 "_graft", "_seed_", "_separate_", "_inflate", "_strip_",
                 "_refresh_", "_ride_", "_rigidify", "_sync_", "_cap_",
                 "clear_armor", "rebury_", "fit_armor", "bake_preset",
                 "repair_collapsed", "_recompute_")
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
    # RESOLVED 2026-08-18 -- this was recorded as "OPEN: per-shape, no
    # documented reason". It is not an anomaly. It is the `elif` branch of the
    # SAME if/elif as `clear_armor_outside_body`: the anti-poke moves every
    # vert and so is skipped for physics cloth, and this covers the bust/butt
    # bands for that cloth instead. Both branches are body-swap-only. It looked
    # asymmetric only because the old prefix list could see this name and not
    # its sibling.
    "_inflate_cloth_over_bust_butt": "soft-cloth branch of the anti-poke "
                                     "if/elif; whole stage is body-driven",

    # ---- THE BODY-DRIVEN GEOMETRY STAGE ------------------------------------
    #
    # One structural reason covers this block, and it is the real shape of the
    # two paths: THE COPY PATH DOES NO BODY-DRIVEN GEOMETRY WORK. It warps by
    # the CBBE->UBE body delta with snap-outside (`min_standoff=
    # ARMOR_TO_SKIN_BUFFER`) and stops. Every pass that needs the UBE body as a
    # TARGET SURFACE -- clearance, ride, rigidify, rebury, preset bake -- can
    # only exist where the body was injected.
    #
    # These were ALL invisible to this check until the prefix list was widened
    # on 2026-08-18. They are documented, not blessed: wiring any of them into
    # the copy path is a behaviour change over the ~78% of the pack that takes
    # it, and needs an A/B plus the clearance counter-metric.
    "clear_armor_outside_body": "anti-poke; body-driven clearance",
    "rebury_authored_verts": "needs BOTH source and UBE body to restore "
                             "authored insideness",
    "fit_armor_to_ube_body": "the body-swap fit itself",
    "bake_preset_into_armor": "bakes the preset onto the injected body",
    "_rigidify_within_clearance": "needs the body clearance field",
    "_sync_bust_plate_follow_postwrite": "post-write, needs the injected body",
    # The layer-ride machinery rides shapes on a reference layer, which only
    # the whole-piece `shape_jobs` view provides -- same reason as
    # `_repair_layer_order` above.
    "_ride_layers_on_reference": "needs shape_jobs (whole-piece view)",
    "_ride_disp_barycentric": "helper of the layer ride",
    "_feather_ride_disp": "helper of the layer ride",
    # #panel-rigidity. Per-shape, so it COULD run on the copy path -- it is here
    # only because it is OPT-IN (`CBBE2UBE_PANEL_RIGIDITY`, default 0) and has
    # not had its first in-game verdict, and wiring an unjudged pass into both
    # paths doubles the blast radius of a mistake. THIS ENTRY IS A DEBT, NOT A
    # BLESSING: wire it into the copy path BEFORE the default is ever flipped,
    # or the two paths will split the pack -- the defect this file exists to
    # catch.
    "_weld_components": "OPT-IN #panel-rigidity, unjudged; wire into the copy "
                        "path before any default flip",
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
