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
    # CORRECTED 2026-08-22. This block used to read: "Cross-shape passes: they
    # take `shape_jobs`, the whole-piece view ... Only phase 2 builds that
    # structure; the copy path hands `_copy_shape` one shape at a time, so there
    # is nothing for them to operate on there."
    #
    # THAT REASON IS FALSE. The copy path builds `shape_jobs_p1`
    # (`nif_convert.py:6084`) with the same dict schema phase 2 builds at
    # `:27243` (src / verts / override_skin / verts_modified, plus g2s), and it
    # ALREADY runs four cross-shape passes over it -- including a ride
    # (`_ride_effect_overlays_on_plate`) and `_weld_cross_shape_seams`.
    # `_ride_layers_on_reference`'s other argument, `body_verts`, is
    # `body_verts_for_fit`, also in scope there.
    #
    # So these four are UNBLOCKED plumbing-wise and stay phase-2 only because
    # nobody has run the A/B -- a DEBT, not a structural fact. See BUG-02.
    #
    # AND A WARNING FOR WHOEVER EXTENDS THIS DICT: the tests below assert every
    # entry HAS a reason. Nothing can assert a reason is TRUE. These four were
    # wrong for four days and were quoted as fact in two memories and a bug-log
    # root cause. A reason here is a claim, not a finding.
    "_repair_layer_order": "DEBT: copy path HAS shape_jobs_p1; needs an A/B",
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
    # CORRECTED 2026-08-22 -- this block used to claim "THE COPY PATH DOES NO
    # BODY-DRIVEN GEOMETRY WORK. It warps by the CBBE->UBE body delta with
    # snap-outside and stops." **That is false.** The copy path also inflates
    # (`inflate_armor_outward`, slot-aware), conforms
    # (`conform_to_source_standoff` + `_finalize_physics_and_motion_match` ->
    # `_conform_fitted_to_body`), groove-smooths, and since 2026-08-22
    # rigidifies panels. The two paths share 53 of 64 passes.
    #
    # What is ACTUALLY true of the remaining entries is narrower: they need the
    # INJECTED body -- the surface a slot-32 piece HIDES and therefore must be
    # measured against. A copy-path piece is worn over the actor's own rendering
    # UBE body, which is why the gate for BUG-02 is the SLOT, not the path.
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
    # STRUCTURAL, not debt (re-derived 2026-08-22 when the first half of
    # #panel-rigidity was wired into the copy path): this is the SECOND half,
    # and it exists to recover the panel deformation the ANTI-POKE re-introduces.
    # `_rigidify_within_clearance` WAS listed here, on the grounds that it only
    # recovers what the anti-poke re-deforms and the copy path has no anti-poke.
    # That reason still holds for its POST-ANTI-POKE call site, which is still
    # phase-2 only -- but 2026-08-23 gave the function a SECOND job on both
    # paths (#panel-rigid-early-clearance), so it is no longer phase-2-only as a
    # FUNCTION and listing it here would be a false claim. The surviving
    # asymmetry is now pinned per CALL SITE by the test below, which is the
    # right granularity: a function can serve one role on both paths and another
    # on one.
    "_sync_bust_plate_follow_postwrite": "post-write, needs the injected body",
    # The layer-ride machinery. Its old reason -- "which only the whole-piece
    # `shape_jobs` view provides" -- is FALSE for the same reason as
    # `_repair_layer_order` above: the copy path builds `shape_jobs_p1` and
    # already rides overlays on it. DEBT, not a structural fact.
    "_ride_layers_on_reference": "DEBT: copy path HAS shape_jobs_p1 and "
                                 "body_verts_for_fit; needs an A/B",
    "_ride_disp_barycentric": "helper of the layer ride",
    "_feather_ride_disp": "helper of the layer ride",
    # `_weld_components` WAS here, with the note "wire it into the copy path
    # BEFORE the default is ever flipped, or the two paths will split the pack".
    # PAID 2026-08-22. The debt note keyed on the wrong event: the default was
    # never flipped, but the user's RECIPE carried `panel_rigidity = 0.75`, which
    # splits the pack exactly the same way -- 26% of pieces rigidified, 74% not.
    # `_partial_rigid_panels` now runs on the copy path too, so
    # `_weld_components` and `_locally_rigid_panel` reach both and are no longer
    # listed here.
    #
    # WHEN YOU ADD AN OPT-IN PASS TO ONE PATH: a recipe override is as dangerous
    # as a default flip. Do not write a debt note that only guards the default.
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


# ---------------------------------------------------------------------------
# THE HOLE THE COUNT CHECK ABOVE LEAVES OPEN
#
# `test_pass_prefixes_still_match_real_helpers` asserts only a COUNT. A new pass
# whose name matches no prefix keeps that count perfectly healthy and is invisible
# to BOTH divergence tests -- it lands in neither `a` nor `b`, so nothing can
# fire. That is how the list went stale on 2026-08-18, and a floor cannot fix it:
# the omissions were never about how many, but which.
#
# So classify STRUCTURALLY instead of by name -- a helper that writes mesh data
# is a stage -- and require every such helper to be either matched by the prefix
# list or named below with its path and a reason.
MIN_MESH_WRITERS = 15

# Mesh-writing helpers that are NOT body-driven fit passes.
#   name: (where it runs, why it is not a fit pass)
# `where` is one of "both" / "phase2" / "copy" / "entry", and is ASSERTED against
# the call graph below -- so an entry cannot sit here while quietly changing
# which path it runs on. Reasons come from each helper's own docstring.
NOT_A_FIT_PASS = {
    "convert_nif_phase2": (
        "entry", "the phase-2 entry point itself, not a pass"),
    "_copy_shape": (
        "both", "shared plumbing; most passes are reached THROUGH it"),
    "_reauthor_nif_fresh": (
        "both", "shared plumbing; the writer both paths end in"),
    "_install_skin": (
        "both", "bones/xforms/weights/partitions onto a fresh shape"),
    "_finalize_hdt_physics": (
        "both", "physics extra-data + authored XML; shared tail"),
    "_add_butt_collider_patch": (
        "both", "builds a collider; declines XML-undeclared bones"),
    "_add_skirt_collider_proxy": (
        "both", "builds a collider; declines XML-undeclared bones"),
    "_split_bust_collider_shape": (
        "both", "splits a collider shape; not a body fit"),
    "_normalize_partitions_on_disk": (
        "both", "partition hygiene on a post-save reload"),
    "_audit_registered_shape_declared_bones": (
        "both", "an AUDIT -- a guard, deliberately not a repair"),
    # The two body-INSTALL stages. Neither is a fit pass: they put geometry in
    # place, they do not conform anything to it. They are also the only pair in
    # this file that legitimately splits one to each path.
    "_inject_ube_baseshape": (
        "phase2", "installs the UBE body; the copy path has no body to install"),
    "_inject_ube_extremity_replacement": (
        "copy",
        "swaps a slot 33/37 CBBE-topology Hands/Feet shape for the UBE one. "
        "Phase 2 installs a whole body instead, so it needs no extremity-only "
        "swap. This is the ONE documented copy-path-only stage, and it is live: "
        "measured 2026-08-22, 172/172 Hands shapes in the output pack are UBE "
        "topology (15500 verts), 0 CBBE."),
}


def _mesh_writers(graph):
    """Reachable helpers that write mesh data -- the structural definition of a
    stage, independent of what anyone remembered to name it."""
    src = Path(inspect.getfile(nc)).read_text(encoding="utf8")
    bodies = {n.name: ast.get_source_segment(src, n) or ""
              for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)}
    writes = ("set_verts", "setShapeWeights", "override_verts",
              "transform_verts", "_copy_shape(", "atomic_nif_save", "save(")
    reach = _reachable(graph, ENTRY_A) | _reachable(graph, ENTRY_B)
    return {f for f in reach
            if any(w in bodies.get(f, "") for w in writes)
            and ("verts" in bodies.get(f, "") or "weights" in bodies.get(f, ""))}


def _escapees(writers, allow):
    return sorted(f for f in writers
                  if not any(f.startswith(p) or p in f for p in PASS_PREFIXES)
                  and f not in allow)


def test_no_mesh_writing_stage_escapes_the_prefix_list():
    """A new stage must not be able to hide from this file just by being named
    something the prefix list does not happen to cover."""
    g = _call_graph(_module_ast())
    writers = _mesh_writers(g)
    assert len(writers) >= MIN_MESH_WRITERS, (
        f"only {len(writers)} mesh-writing helpers found; the classifier has "
        f"broken, so 'nothing escaped' would be a claim about an empty set")
    escaped = _escapees(writers, set(NOT_A_FIT_PASS))
    assert not escaped, (
        f"mesh-writing helper(s) invisible to the parity check: {escaped}\n"
        f"Add a PASS_PREFIXES prefix so divergence IS checked, or add to "
        f"NOT_A_FIT_PASS with its path and a reason if it is not a fit pass.")


def test_the_not_a_fit_pass_allowlist_matches_reality():
    """Every allowlisted helper must still exist AND still run where it says.
    Otherwise the allowlist becomes the blind spot it was added to remove."""
    g = _call_graph(_module_ast())
    a = _reachable(g, ENTRY_A, stop={ENTRY_B})
    b = _reachable(g, ENTRY_B)
    missing = sorted(n for n in NOT_A_FIT_PASS if n not in g)
    assert not missing, (
        f"NOT_A_FIT_PASS names helpers that no longer exist: {missing}")
    wrong = []
    for n, (where, _why) in sorted(NOT_A_FIT_PASS.items()):
        if where == "entry":
            continue  # the dispatch boundary; in `a` by construction
        actual = ("both" if (n in a and n in b) else
                  "copy" if n in a else
                  "phase2" if n in b else "UNREACHABLE")
        if actual != where:
            wrong.append(f"{n}: documented {where!r}, actually {actual!r}")
    assert not wrong, (
        "allowlisted helper(s) changed which path they run on:\n  "
        + "\n  ".join(wrong)
        + "\nThat is a real divergence: fix the code, or update the entry.")


def test_the_escape_check_can_actually_fail():
    """GUARD THE GUARD: prove the escape check notices a mesh-writing stage that
    matches no prefix and sits on no allowlist."""
    g = _call_graph(_module_ast())
    writers = _mesh_writers(g)
    victims = sorted(writers & set(NOT_A_FIT_PASS))
    assert victims, "no allowlisted mesh writer to mutate -- cannot verify"
    victim = victims[0]
    assert victim in _escapees(writers, set(NOT_A_FIT_PASS) - {victim}), (
        f"dropping {victim!r} from the allowlist did NOT make it escape -- the "
        f"check cannot fail and proves nothing")


# ---------------------------------------------------------------------------
# #panel-rigidity path parity, wired 2026-08-22.
#
# The recipe carried `panel_rigidity = 0.75` while every pass implementing it
# was body-swap-only, so layered plates were straightened on ~26% of the pack
# and not the other ~74%. The old debt note said "wire it in before the DEFAULT
# is ever flipped" -- but a USER RECIPE OVERRIDE splits the pack identically and
# nothing was watching for that. These tests pin the fix.
# ---------------------------------------------------------------------------

def test_panel_rigidity_runs_on_both_paths():
    """The first half of #panel-rigidity must be reachable from BOTH entries."""
    g = _call_graph(_module_ast())
    a = _reachable(g, ENTRY_A, stop={ENTRY_B})
    b = _reachable(g, ENTRY_B)
    for fn in ("_partial_rigid_panels", "_weld_components",
               "_locally_rigid_panel"):
        assert fn in a, (
            f"{fn} is no longer reachable from {ENTRY_A}: #panel-rigidity is "
            f"back to splitting the pack (26% rigidified, 74% not)")
        assert fn in b, f"{fn} unreachable from {ENTRY_B}"


def test_the_second_half_is_absent_for_a_REASON_THAT_IS_TRUE():
    """The RECOVERY role stays phase-2 only, and its reason stays true.

    `_rigidify_within_clearance` now has two jobs. The original one -- recover
    what the ANTI-POKE re-deforms -- belongs to phase 2 alone, and its reason is
    that the copy path has no anti-poke to recover from. The second, added
    2026-08-23, runs the SAME solver at the EARLY panel-rigidity site on BOTH
    paths so the pass never hands the anti-poke penetration to clean up.

    So the assertion is per CALL SITE, not per function: reachability alone
    would now report "on both paths" and silently stop checking the asymmetry
    that still exists. A reason here is a CLAIM -- four were false for four days
    -- so where it can be machine-checked, check it.
    """
    import ast as _ast
    g = _call_graph(_module_ast())
    a = _reachable(g, ENTRY_A, stop={ENTRY_B})
    b = _reachable(g, ENTRY_B)
    assert "clear_armor_outside_body" in b and "clear_armor_outside_body" not in a, (
        "the anti-poke now runs on the copy path too, so the stated reason for "
        "keeping the RECOVERY call phase-2-only is no longer true -- wire it in "
        "as well, or rewrite the reason")

    tree = _module_ast()
    calls = [n for n in _ast.walk(tree)
             if isinstance(n, _ast.Call)
             and getattr(n.func, "id", None) == "_rigidify_within_clearance"]
    assert len(calls) == 5, (
        f"expected 5 call sites -- the EARLY solver at all four panel-rigidity "
        f"sites (main + fine-animation, on each convert path) plus the ONE "
        f"phase-2 recovery call after the anti-poke -- found {len(calls)}. If a "
        f"site was added or removed, decide which role it plays and update this "
        f"test rather than the count.")

    # Every early site must have kept its blind fallback, or the flag stops
    # being an opt-in and the OFF path is no longer byte-identical.
    blind = [n for n in _ast.walk(tree)
             if isinstance(n, _ast.Call)
             and getattr(n.func, "id", None) == "_partial_rigid_panels"]
    assert len(blind) == 4, (
        f"expected the blind form to remain as the OFF-path fallback at all "
        f"four early sites, found {len(blind)}")

    # The recovery call is the one taking the CURRENT verts straight after the
    # anti-poke; the two early calls are guarded by the opt-in flag. Tell them
    # apart by the guard, not by line order, which shifts with any edit above.
    src = Path(inspect.getfile(nc)).read_text(encoding="utf8")
    guarded = src.count("PANEL_RIGID_EARLY_CLEAR")
    assert guarded >= 5, (
        "the early-clearance sites must stay behind their flag: found "
        f"{guarded} occurrence(s) of the guard")


def test_the_copy_path_call_passes_the_same_gates_as_phase2():
    """Same skip_mask + min_verts contract on both sides. Rigidifying an SMP
    chain vert would fight the sim, so the mask is not optional."""
    import ast as _ast
    tree = _module_ast()
    calls = [n for n in _ast.walk(tree)
             if isinstance(n, _ast.Call)
             and getattr(n.func, "id", None) == "_partial_rigid_panels"]
    assert len(calls) >= 2, (
        f"expected a _partial_rigid_panels call on each path, found {len(calls)}")
    for c in calls:
        kw = {k.arg for k in c.keywords}
        assert "skip_mask" in kw and "min_verts" in kw, (
            f"a _partial_rigid_panels call at line {c.lineno} omits skip_mask/"
            f"min_verts -- the two paths would rigidify different populations")
