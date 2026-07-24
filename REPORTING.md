# Reporting a problem

Two ways to send a report. **Both use the same format** — pick whichever is
easier for you, and don't send both.

| | Best for |
| --- | --- |
| **[File a GitHub issue](#option-1--github-fastest-to-act-on)** | Anything that needs tracking or a fix |
| **[Paste into chat](#option-2--paste-into-chat)** | Quick questions, "is this normal?", screenshots |

Either way, start in the GUI: click **Copy report**. It fills in your version
and the last run's numbers and puts the whole thing on your clipboard. Then
fill in the two `<...>` lines and the `[ ]` checkboxes.

---

## First: two things that cause most invisible-armor reports

Check these before writing anything. They account for nearly every
"converted armor doesn't show up" report:

1. **SkyPatcher must be installed.** The converter attaches every armature
   through a SkyPatcher INI, never an ESP override. Without SkyPatcher, every
   converted piece is invisible.
2. **`iEnableArmorPatching=1`** in `SKSE/Plugins/SkyPatcher.ini`. Set to `0` it
   produces the *exact same symptom* as SkyPatcher being missing entirely.

Then confirm **every** `CBBE_to_UBE_Combined*.esp` is enabled. The merge splits
into numbered pieces when it outgrows the ESL cap, and one disabled piece means
a chunk of missing armor.

Running **Check setup** in the GUI verifies all of this for you.

---

## Option 1 — GitHub (fastest to act on)

These links open the right form with your version already filled in:

- **[The converter errored or crashed](https://github.com/DayOnly/CBBEtoUBE-exe/issues/new?template=bug_report.yml)**
- **[Armor looks wrong in game](https://github.com/DayOnly/CBBEtoUBE-exe/issues/new?template=conversion_problem.yml)**
- **[Feature request](https://github.com/DayOnly/CBBEtoUBE-exe/issues/new?template=feature_request.yml)**

The form asks for everything below, so you can fill it in directly instead of
pasting. Drag your diagnostics zip into the issue to attach it.

## Option 2 — Paste into chat

Click **Copy report** in the GUI and paste. It looks like this:

```
CBBEtoUBE problem report
========================
Version:  1.1.1
Type:     Conversion problem - output looks wrong in game
Symptom:  Armor is invisible / does not render

WHAT HAPPENS
  <what you see, on which armor, and when>

AFFECTED ARMOR / SOURCE MOD
  <armor piece, and the mod it came from>

PREREQUISITES  (put an x in the brackets once checked)
  [ ] SkyPatcher installed
  [ ] iEnableArmorPatching=1 in SKSE/Plugins/SkyPatcher.ini
  [ ] every CBBE_to_UBE_Combined*.esp enabled
  [ ] UBE + UBE_AllRace.esp, RaceCompatibility, RaceMenu installed
  [ ] output mod last in load order and winning its file conflicts

LAST RUN
  source mods:     37
  converted ok:    35
  armor nifs:      412
  esp patches:     35
  hard failures:   1
  nif errors:      0
  load failures:   0
  zero-mesh mods:  1 (SomeArmorMod)
  weight-partner warnings: 1  <- invisibility risk
  FAILED: OtherMod: RuntimeError('bad nif')

DIAGNOSTICS
  [ ] CBBEtoUBE_diagnostics_<timestamp>.zip  (GUI -> Export diagnostics)
```

**Wrap it in a code fence when you paste it into Discord** — put a line with
three backticks above and below. Without the fence Discord collapses the
indentation and eats the `[ ]` boxes, and it becomes much harder to read.

The format is plain ASCII on purpose, so it survives a paste anywhere.

---

## Attach the diagnostics zip

Click **Export diagnostics** in the GUI. It writes
`CBBEtoUBE_diagnostics_<timestamp>.zip` and opens the folder. Inside:

| File | What it is |
| --- | --- |
| `REPORT.txt` | The same report as above, already filled in |
| `gui_log.txt` | The run log as the GUI saw it |
| `settings.json` | Your conversion settings |
| `exclusions.json` | Armors you excluded |
| `layout.json` | Discovered MO2 mods root, profile, game data dirs |
| `preflight.txt` | A fresh **Check setup** run |

> **Look inside before you post it.** It contains your MO2 paths, your profile
> name, and the names of mods in your load order. GitHub issues are public and
> so is most of Discord. Nothing in it is secret, but it is *yours* — decide
> that deliberately rather than by accident.

A normal run also leaves these behind, useful if the GUI won't start:

- `CBBEtoUBE_last_run.log`, `CBBEtoUBE_last_failures.json` — next to the exe
- `conversion_report.json`, `conversion_summary.txt`,
  `conversion_report_<mod>.txt` — at the output mod root

## What makes a report actionable

The difference between a report that gets fixed and one that stalls is almost
always specificity:

- **Name the armor and the mod it came from.** "Some armors clip" cannot be
  reproduced; "the left pauldron on X from mod Y clips at the shoulder" can.
- **Say what you expected.** Some things that look wrong are deliberate — armor
  another mod already patched for UBE is skipped on purpose, and male meshes are
  skipped unless the piece is male-only.
- **Screenshots for anything visual.** For clipping, gaps, or seams, one
  screenshot beats a paragraph. Say which body slider and weight if it only
  happens at particular settings.
- **One report per problem.** Two unrelated bugs in one thread means one of
  them gets forgotten.

## Why there's no "send report" button

The shipped exe has no network stack at all. The build deliberately excludes the
`ssl` extension: OpenSSL 1.1.x's license is incompatible with GPL-3.0, and this
project vendors GPL-3.0 PyNifly. Removing it is what keeps the binary legally
distributable, so an automatic upload isn't a missing feature — it's a
consequence of that license decision, and it isn't coming back.

Nothing is collected, transmitted, or phoned home. Every file above stays on
your machine until you choose to attach it.
