# Using CBBEtoUBE

A start-to-finish walkthrough: what to check before converting, how to run it,
**how to wire the output into your modlist** (the step that most often goes
wrong), and how to read a problem when one shows up.

`README.md` is the reference — commands, flags, layout, how the refit works.
This is the task guide. `REPORTING.md` covers filing a problem.

---

## 1. Before you convert

The converter reads your modlist through MO2's virtual file system, so it needs
the modlist set up first. Four things are **hard requirements in-game** — without
them the conversion succeeds and the armor is invisible when you play:

| Requirement | Why |
|---|---|
| **SkyPatcher** (SKSE plugin) | Every converted armature is attached via a SkyPatcher INI. There is **no ESP fallback**. |
| `iEnableArmorPatching=1` in `SKSE/Plugins/SkyPatcher.ini` | With `0` you get the identical symptom to not having SkyPatcher at all. |
| **RaceCompatibility** | Puts converted armatures on the UBE races at runtime. The Light build carries the RaceDispatcher. |
| **UBE + `UBE_AllRace.esp`** | The body and races the minted armatures point at. |

You also need the **UBE body built in BodySlide** — that built body is the shape
every armor is refitted onto. RaceMenu is needed for the body-morph data the
converter regenerates.

**Run "Check setup" in the GUI before your first convert.** It verifies all of
the above plus disk space and the body reference, and tells you what to fix. It
is much cheaper than discovering a missing dependency after an hour-long run.

---

## 2. Running it

Launch the exe with no arguments (or from MO2) for the GUI — that is the normal
path. The convert button runs the same pipeline as:

```
CBBEtoUBE.exe auto
```

**Always launch it through MO2**, not by double-clicking it in Explorer. The
converter finds your mods, load order, and game data through MO2's VFS; outside
it, it sees nothing.

A full modlist run takes a while — expect tens of minutes to a couple of hours
depending on how much armor you have. The log line

```
batch worker pool: N workers ...
```

tells you how many processes it will use. The default is bounded by BOTH your CPU
and your RAM (roughly 2 GB per worker), because oversubscribing memory makes the
run page to disk. Override with `--workers N` if you have a reason to.

To convert only part of the list while testing, use `--only-mods` (exact mod
folder names, repeat the flag or comma-separate). That turns an hour into
minutes and is the right way to test a change.

---

## 3. What you get

Everything lands in **one output mod folder** (default `CBBEtoUBE Auto`):

```
CBBEtoUBE Auto/
  meshes/!UBE/...                     the converted armor meshes
  SKSE/Plugins/SkyPatcher/armor/*.ini the armature links -- how armor reaches the game
  CBBE_to_UBE_Combined.esp            the merged patch plugin
  CBBE_to_UBE_Combined2.esp           ...and any further pieces (see below)
  _unmerged_patches/                  per-source patches (not loaded; inputs to the merge)
  conversion_report_<mod>.txt         per-source detail
  conversion_summary.txt              the run's coverage summary
```

At the end the log prints a failure count and a coverage summary. **`0 failure(s)`
is what you want**; warnings are usually cosmetic and named individually.

---

## 4. Wiring it into MO2 — do not skip this

The conversion producing files is not the same as the game using them.

1. **Refresh MO2** (F5). A brand-new output folder appears as a new mod.
2. **Enable the output mod**, and give it priority over the source armor mods so
   its meshes win.
3. **Enable EVERY Combined ESP.** This is the step people miss. Past the 2048-record
   ESL cap the patch is **split into numbered pieces** — `CBBE_to_UBE_Combined.esp`,
   `CBBE_to_UBE_Combined2.esp`, and so on. The log says so explicitly
   (`SPLIT: ... (enable ALL of them)`). Enabling only the first leaves part of your
   armor unpatched and invisible.

   The pieces are normally **ESL-flagged**, so they cost light slots rather than
   load-order slots — a large list producing three or four of them is expected and
   costs you nothing. A full-ESP downgrade is now a last resort and says so loudly
   in the log; if you see one, the count of pieces is not the problem.
4. **Position the plugin(s) to win** over the mods they patch.

Then load a save and check a converted piece. Nothing about steps 1-4 is
automatic.

---

## 5. Verifying a run

- **Log**: `=== N failure(s), M warning(s) ===` — investigate any failures.
- **`conversion_summary.txt`**: how many armors were covered.
- **The link count.** The merge summary ends with
  `FULL SKYPATCHER: N armor record(s) -> ...CBBE_to_UBE_Combined.ini`. **N should be
  in the thousands and roughly match the armors you converted.** If it instead says
  `no links this run`, delivery is dead for every armor — nothing is applied — and
  the merge will have deleted the INI (correctly: a stale INI points at FormIDs the
  rebuilt plugin has reassigned).

  This line is worth reading every run, because everything around it still looks
  healthy when it fails — ESL flag, split, master count and ARMA total are all
  reported normally, and the failure is one line in a log thousands long.
- **In game**: equip a converted piece. If it renders, delivery works end to end.

**Force a mesh reload before judging anything.** Skyrim caches a worn armor's
mesh, so a fresh conversion can look identical to the old one until you re-equip
the item, or save → main menu → load. This has repeatedly faked "the fix did not
apply".

---

## 6. When something looks wrong

Work down this table — the first two questions eliminate most cases.

| Symptom | Most likely cause | What to do |
|---|---|---|
| **Everything** converted is invisible | SkyPatcher missing, or `iEnableArmorPatching=0` | Check that first, always. It is the single delivery path. |
| **Everything** invisible **and SkyPatcher is fine** | The run produced no links, so there is no INI to apply | Check `SKSE/Plugins/SkyPatcher/armor/CBBE_to_UBE_Combined.ini` **exists** and search the log for `FULL SKYPATCHER: no links this run`. See §5 — the rest of the merge summary looks healthy when this happens. |
| **Some** armors invisible | Combined ESP pieces not all enabled; output mod not winning; or that armor was skipped | Check step 4; then search the SkyPatcher INI for the armor's plugin name. |
| One armor invisible, others fine | Its meshes may come from another mod that needs building in BodySlide | Check whether the source ships built meshes or only BodySlide shape data. |
| Clipping **while standing still** | Static fit/clearance | Note the armor and the body region. |
| Clipping **only while moving** | The armor has no clearance for how far the body *travels* — it was fitted against a body standing still | A different fix class from static clipping. See §7: try **Bust clearance on SMP collider armor** first, then its push budget. Note which motion triggers it. |
| Body shows **only when zoomed out** | Distance z-fighting, not clipping | Do the zoom test first; it is cosmetic and not worth chasing as a clip. |
| Crash **on equipping** a piece | Physics/skinning defect | Note the exact armor and keep the crash log — this is high priority. |

When reporting, the three things that matter most: **which armor**, **what you
were doing** (standing / moving / zoomed out), and whether it is **better, worse,
or unchanged** versus the previous conversion. See `REPORTING.md`.

---

## 7. Turning a behaviour on or off

**Use the GUI settings for this.** Most fit-correction passes appear as a
checkbox under the **Armor** tab, and the GUI hands the corresponding variable to
the run. That matters because **MO2 does not pass your environment through to the
launched program** — exporting a variable in a terminal and then launching from
MO2 has no effect on the run. Anything you set outside the GUI only applies when
you run the converter directly from a shell.

Settings persist to `CBBEtoUBE_settings.json` next to the exe (only your
overrides are stored, so defaults keep tracking the build), and it survives a
redeploy.

Several settings are worth calling out, because they ship **off** and they target the
hardest symptom to fix — chest or butt clipping that only shows up **in motion**.

| Setting (Armor tab) | What it does |
|---|---|
| **Bust clearance on SMP collider armor** | An armour whose physics config names it only as a *collider* currently gets no bust clearance at all, so the body pushes straight through it. This gives it clearance. Try this **first** for a cuirass that clips at the chest while moving. Measured 6.3% → 3.3% exposed on one such cuirass. |
| **...its push budget (units)** | How far that pass may move a vertex outward. The default `1.0` was tuned against a body standing still, while the body's breast physics is allowed several times that much travel — so if clearance helps but falls short, raise this a step at a time. Too large spreads vertices on rounded areas. |
| **Chest/butt jiggle on fitted torso armor** | Makes a fitted corset or bra *follow* the body's breast and butt instead of staying rigid. **It deliberately skips any armour that is also a physics collider** — grafting body motion onto a collider the body collides against causes a runaway feedback loop, so that case is permanently excluded. It therefore does nothing for a cuirass with its own physics; those want the clearance setting above. |
| **Chest follow ratio** | Lets a fitted top track the body's breast motion by the amount its own clearance actually needs, instead of a fixed cap that leaves it following about a third of the body. This is the switch the two below hang off — neither does anything without it. |
| **...its ceiling for unrecognised materials** | How much motion a top may follow when its material can't be identified from its name or texture. `0.35` (default) treats it like metal; `1.0` treats it like cloth. **In a large pack most armour is unidentifiable, and this is what limits it** — of the pieces whose clearance says they need to follow more than they're allowed to, roughly 70% are unlabelled rather than actually metal. Raise it if chests still clip in motion; lower it if stiff armour starts looking rubbery. |
| **Chest follow on skirt-welded cuirasses** | Some cuirasses are one piece with their own physics skirt, which drags the whole piece below the "hugs the body" test. This judges such a piece on its non-skirt part. **Unproven: on every armour tested it changed nothing** — the ceiling setting above it is what actually moves these pieces. Left in as an off-by-default experiment. |

The measured win on a skirt-welded cuirass came from the **ceiling** setting alone:
bare skin visible under motion went from **71% to 9%**.

**What the skirt-welded setting will *not* fix: the butt.** On a cuirass like that,
the part covering the butt *is* the simulated skirt. Nothing this converter writes
into the mesh moves it — the skirt's position at runtime comes from the physics
engine, not from the weights. If such a piece pokes at the butt, that is a physics
problem and the settings on this page cannot reach it.

**Why "in motion" is its own category.** Armour is fitted against a body that is
standing still, but the body you actually see is animated and physics-driven. A
piece can be measurably clear at rest and still be passed straight through once the
breast or butt starts moving. That is why the triage table above separates "clipping
while standing still" from "clipping only while moving" — they are different faults
with different fixes, and a fix for one does nothing for the other.

For a shell run, the same off-switches follow one pattern:

```
CBBE2UBE_NO_<FEATURE>=1
```

Useful ones when triaging: `CBBE2UBE_NO_WEIGHT_INVARIANT`,
`CBBE2UBE_NO_LEG_BEND_MATCH`, `CBBE2UBE_NO_JIGGLE_TRANSFER`,
`CBBE2UBE_NO_CONFORM`, `CBBE2UBE_NO_SELFINT_REPAIR`, `CBBE2UBE_NO_VANILLA_SWEEP`.
Search `src/` for `CBBE2UBE_NO_` for the full set.

**Bisecting is the fastest way to identify a fit problem**: convert one affected
armor with a pass disabled and compare. If the problem disappears, you have
named the pass responsible — include that in your report, it is worth more than
a screenshot alone.

Path overrides, if auto-discovery gets it wrong: `CBBE2UBE_MO2_INI`,
`CBBE2UBE_MODS_ROOT`, `CBBE2UBE_GAME_DATA`.

For the reference bodies, note the two are **not** spelled the same way:

| | |
|---|---|
| UBE body | `CBBE2UBE_UBE_BODY` (one path for both weights), or `CBBE2UBE_UBE_BODY_0` / `_1` |
| CBBE/3BA body | `CBBE2UBE_CBBE_BODY_0` **and** `CBBE2UBE_CBBE_BODY_1` — weight-suffixed only; the bare name is **not** read |

---

## 8. Re-running

Re-running is safe and is the normal workflow — the output mod is rewritten in
place. Two things worth knowing:

- Converting again after changing your **modlist** picks up the new mods.
- Converting again after changing the **UBE body** in BodySlide re-fits
  everything to the new shape. Do this whenever you change your body preset,
  or armor will be fitted to a body you no longer use.

If a run is interrupted, just run it again — outputs are written atomically, so
a killed run cannot leave a half-written mesh or plugin behind.
