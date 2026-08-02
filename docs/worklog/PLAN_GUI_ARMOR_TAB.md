# Plan: restructure the GUI Armor tab

The Armor tab holds 38 of the tool's 43 settings and has stopped being
scannable. This is the plan to fix it, in dependency order, with the
measurements that justify each step.

Status: PLANNED, nothing implemented.

---

## 1. Measured diagnosis

Rendered height computed from the registry against `_build_settings_tab`'s own
geometry (`wraplength=580` ≈ 88 chars/line, ~22px per control row, ~15px per
hint line):

    group                           n  tipChars tipLines     px
    Fit and conform                 9      1871       25    613
    Seams                           3       210        3    151
    Jiggle and physics transfer    16      4618       59   1277
    Glow and effect-shader          4       306        4    188
    HDT-SMP chains                  3       230        4    166
    Boots and parity                2       148        2    114
    Coverage                        1       259        3    107
    TOTAL                          38      7642            2616

**The tab is ~2616px tall — 3.7 screens at a 700px viewport.**

Four separate problems, and they are not equally important:

**(a) 86% of the text on the tab is always-visible tooltip prose.** 7642
characters of it. Every setting renders its full explanation inline as a wrapped
`Hint.TLabel`. This is the dominant cost by a wide margin — bigger than the
number of settings, the grouping, and everything else combined.

**(b) `Setting.advanced` is dead.** The field exists, is set on 6 numeric knobs
(`smp_antipoke_push`, `jiggle_clearance_gain`, `jiggle_clearance_max`,
`jiggle_transfer_factor`, `seam_weld_tol`, `glow_ride_max`), and
`src/gui.py` reads it **zero times**. The decluttering mechanism was designed
and never wired. (Third instance in this project of a field that exists but is
never read — worth a grep pass for others.)

**(c) The group taxonomy has drifted.** "Jiggle and physics transfer" is 16
settings and 1277px — half the tab — and is a junk drawer holding four unrelated
concerns. Chain settings are split across two groups. "Boots and parity" is two
unrelated settings sharing a name that describes neither. "Coverage" has one
member. And three settings are outright misfiled:

* `disable_softbody_scales` ("drop breast/butt/belly jiggle transfer") sits in
  **Fit and conform** — it is a jiggle kill-switch.
* `drape_xml_gate` ("fit robes/dresses that declare their own physics") sits in
  **Jiggle and physics transfer** — it is a scope gate deciding *which garments
  get fitted at all*, not a physics setting.
* `skin_influence_cap` (trim to the 4 influences the format allows) sits in
  **Fit and conform** — it is output correctness.

**(d) Risk is signalled inconsistently.** 14 default-OFF bools; 8 say
"experimental" in the label or tooltip, 6 do not (`disable_softbody_scales`,
`drape_xml_gate`, `source_follow`, `chain_to_softbody`, `static_chains`,
`nested_chain_anchors`). Some of these change physics and can crash the game on
equip; nothing in the UI distinguishes them from a safe shipped default. Given
this project's history of options that could not be enabled, could not be
validated, and therefore were never finished, legibility of risk is not cosmetic.

---

## 2. What NOT to do

**Do not delete or shorten the tooltips to save space.** They carry measured
numbers and in-game caveats that exist nowhere else in the codebase — the
specific clipping percentages a flag was judged on, which failure mode to look
for after enabling it, why a default is what it is. Losing them would cost more
than the clutter does. The fix is to **move them behind a disclosure**, not to
trim them.

**Do not start with the regrouping.** It is the visible problem but not the
dominant one. Presentation is 86% of the mass; fix that first and re-measure,
because a 900px tab with imperfect groups may not need a taxonomy change at all.
Doing the risky reshuffle first optimises the wrong term.

---

## 3. Safety facts (verified in source, not assumed)

* **Regrouping and retabbing cannot disturb a user's saved settings.**
  `save_values` writes `{key: value}` for non-defaults and `load_values` reads
  them back through `by_key()`. Neither touches `tab` or `group`. A setting can
  move anywhere in the UI and keep its saved value.
* **`_known_settings` must keep working.** It is what lets a later build name a
  genuinely new option instead of inferring it from absence — the guard against
  the 2026-07-27 failure (two new options defaulted OFF, the run looked normal
  for an hour, the work did not happen). Any registry change must leave
  `unseen_settings` intact.
* **Test coupling is one assertion.** `tests/test_gui_settings.py::
  test_tab_and_group_structure` names "Fit and conform", "Glow and
  effect-shader" and "Seams". Everything else keys on `s.key`.
* **`TABS` is `("Run", "Armor", "Overlays", "Paths", "Diagnostics")`** and
  `groups_in_tab` returns groups in order of first appearance in `SETTINGS`, so
  ordering the UI means ordering the tuple. There is no separate layout config.

---

## 4. The plan, in dependency order

### Stage 1 — collapse the tooltip wall (biggest win, no behaviour risk)

Add a short `hint` field (one line, ≤80 chars) rendered inline. The existing
long `tooltip` moves behind a disclosure — hover balloon, or a "?" toggle that
expands in place. Nothing is deleted.

Expected: ~2616px → ~1000px, roughly 3.7 screens → 1.4, with every word still
reachable.

Writing 38 hints is the bulk of the work and is mechanical: most existing
tooltips open with a sentence that already *is* the hint.

### Stage 2 — wire `advanced` (the field already exists)

Hide the 7 numeric knobs behind a per-tab "Show advanced" checkbox, and indent
them under the toggle they tune when shown. Removes 7 rows plus their hints from
the default view.

Fixes a real bug on the way: `chest_follow_unknown` is the knob for
`chest_follow` but currently renders four rows away from it, so it reads as an
independent option.

**Re-measure here.** If the tab is comfortable, stop — stages 3 and 4 are
optional and carry more risk than these two.

### Stage 3 — re-cut the groups

Proposed taxonomy, cut by *what the setting acts on* rather than by how it was
historically added. Knobs shown in parentheses are `advanced` and hidden by
default.

| group | settings |
|---|---|
| Fit and clearance | `conform_to_body`, `antipoke_smooth`, `layered_antipoke`, `smp_antipoke` (`smp_antipoke_push`), `unified_offset` |
| Body follow and morphs | `chest_follow` (`chest_follow_unknown`), `source_follow`, `rigid_majority_softbody` |
| What gets fitted | `drape_xml_gate`, `vanilla_sweep` |
| Jiggle transfer | `jiggle_transfer` (`jiggle_transfer_factor`), `torso_jiggle`, `butt_jiggle`, `chest_jiggle`, `disable_softbody_scales`, `jiggle_clearance` (`jiggle_clearance_gain`, `jiggle_clearance_max`) |
| Physics chains (HDT-SMP) | `chain_to_softbody`, `static_chains`, `nested_chain_anchors`, `chain_body_shift`, `chain_torso`, `leg_chain_guard` |
| Limbs and extremities | `leg_bend_match`, `boot_far_thigh` |
| Seams | `seam_weld` (`seam_weld_tol`), `seam_skin_match` |
| Glow and effect shaders | `glow_source_skin`, `glow_anim`, `glow_ride` (`glow_ride_max`) |
| Output checks | `skin_influence_cap`, `weight_parity_check` |

Nine groups of 2–6 visible rows each, no group over 6, no junk drawer, chains
unified, the three misfilings corrected. 31 bools + 7 knobs = 38, all accounted
for.

~~One open question: **`vanilla_sweep` probably does not belong on the Armor tab
at all.**~~ **DONE (`e9a763e`)** — moved to the Run tab, into the "Convert armor"
section beside the mod selection it extends, taking the one-item "Coverage" group
off Armor with it. The Run tab is hand-built rather than generated, so this added
`gui.py::_registry_check` — a registry-backed checkbox rendered outside the
generated tabs, same binding and persistence, registry still the single source of
label/tooltip/default/env. It joins `sel_widgets` and locks during a run, because
settings are read once at child launch.

That leaves **8 groups and 37 settings** on Armor for stage 3.

It also added `test_no_orphaned_settings`, which is worth keeping independent of
this plan: a setting on a tab nobody builds renders nowhere, so it can never be
enabled, validated, or finished. Verified against a planted orphan and against
the realistic slip (moving a setting to Run without hand-rendering it).

### Stage 4 — signal stability consistently

Replace the ad-hoc "(experimental)" in 8 of 14 labels with a `stability` field
rendered as a small badge:

* **stable** — shipped default, validated in game (the 17 default-ON settings)
* **opt-in** — off by default, measured, no known instability
* **experimental** — changes physics or geometry in ways that can crash on equip
  or collapse cloth; needs an in-game verdict, not just a clipping number

Then classify the 6 unlabelled default-OFF settings, which is a real review, not
a rename — it forces an explicit answer to "is this safe to tick?" for each.

---

## 5. Optional: split the tab

Even restructured, Armor holds 38 of 43 settings. A two-tab split —
**Armor · Fit** (fit/clearance, follow, scope, limbs, seams, checks) and
**Armor · Physics** (jiggle, chains) — maps to the two genuine domains and
halves each page.

Deliberately last, and only if stages 1–3 leave it still crowded. It is the most
visible change and the least necessary one, and per §3 it is free to defer:
moving a setting between tabs never disturbs a saved value.

---

## 6. Acceptance

1. Rendered-height estimate re-run after each stage; stage 1+2 target ≤1200px.
2. No tooltip text deleted — diff the registry's total tooltip characters before
   and after; it must not fall.
3. `tests/test_gui_settings.py` updated, suite green.
4. Round-trip a settings file written by the *current* build through the new one:
   every value preserved, `unseen_settings` reports nothing spurious.
5. Launch the GUI and confirm every group renders and every control still binds —
   the registry is data, so a typo in a group name silently orphans a setting
   into a new group rather than erroring.
