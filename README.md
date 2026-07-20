# CBBEtoUBE

Batch-converts Skyrim SE armor and clothing built for **CBBE / 3BA** so it fits
the **UBE** body without clipping, and emits the plugin patches needed for the
converted meshes to show up in game. It can also (opt-in) re-align CBBE/3BA
RaceMenu **body / hands / feet overlays** (tattoos, body paints) to the UBE UV
layout. Pure-Python pipeline on top of `pynifly` + `numpy` + `scipy`.

It is **not** a Synthesis/Mutagen patcher. Clipping is a mesh-geometry problem,
not a plugin-record problem, so the tool rewrites NIF vertices and bone weights
directly, then generates the minimal ESP records that point the armor at the new
meshes.

## What it does

Given a Mod Organizer 2 setup, the full pipeline (`auto`):

1. **Discovers** candidate CBBE/3BA armor mods by walking the MO2 mod tree, and
   resolves every armor mesh through the full virtual file system (BodySlide
   output, BSAs, and loose files all count). Only **player-equippable** armor on
   body slots is selected — non-equippable items (gore / dismemberment effect
   "armor" flagged non-playable) are skipped. Because UBE is a female body, only
   the **female** mesh of each piece is converted; the male mesh is skipped unless
   the piece is male-only (a female actor falls back to the male mesh, so it still
   needs the refit).
2. **Refits** each armor NIF to the UBE body (see *How the refit works*),
   preserving HDT-SMP physics chains, high-heel offsets, and body morphs.
   Armor that bakes exposed body skin (open cleavage, cutouts) is converted via
   a **body-swap**: the baked skin slice is dropped and the real UBE body is
   injected, so exposed skin morphs and jiggles like the actual body.
3. Writes the converted meshes under `meshes/!UBE/...` in a single output mod.
4. Generates a **per-mod UBE patch ESP** for each source, then merges them into
   one **ESL-flagged combined plugin** with a correct master order. If the
   merge outgrows the 2048-record ESL cap it **splits** into numbered pieces
   (`CBBE_to_UBE_Combined.esp`, `CBBE_to_UBE_Combined2.esp`, ...) — enable
   **all** of them. The plugin only *holds* the minted UBE armatures; an
   **active SkyPatcher INI** (shipped in `SKSE/Plugins/SkyPatcher/armor/`)
   attaches each one to its armor at runtime. The converter uses **no ESP
   overrides**, so **SkyPatcher (SKSE) is required** — without it converted
   armor is invisible.
5. Adds **race coverage** so armatures defined by other mods —
   overhaul-rearmatured helmets, circlets, jewelry, and mod-defined body
   variants — still render on UBE races. That coverage is folded **into the
   Combined plugin family**, so there are no standalone coverage plugins and
   nothing extra to enable beyond the Combined piece(s). The winner scan is
   also the sole generator for the merge, which roughly halves the ARMA count
   and so needs fewer ESL pieces.
6. **Coexists with armors that already have a UBE patch.** If another mod
   already gives an armor a UBE armature — a hand-made UBE patch, or another
   converter's output — that armor is left **entirely alone**. Adding a second
   armature would make the actor render two bodies for the slot (z-fighting /
   doubled cloth), and a hand-made patch is usually a better fit than an
   automatic conversion anyway. Both delivery styles are detected: plugins that
   define a UBE armature and reference it, and other mods' SkyPatcher
   `armorAddonsToAdd` INIs. The run prints how many armors were skipped this
   way. Output from **this tool** (any version, under any folder name) is never
   mistaken for a third-party patch.
7. **(Opt-in) RaceMenu overlay transfer.** Rebakes CBBE/3BA **body, hands, and
   feet** overlays (tattoos / body paints) into UBE's UV layout — UBE re-UVs the
   body, so CBBE-authored overlays otherwise land in the wrong place. The
   converted DDS are written **loose at their original texture paths** in the
   output mod, so RaceMenu picks them up by load order with no ESP (the output
   mod wins because it sits at the end of your load order). Off by default; turn
   it on with `--convert-overlays` / the GUI checkbox, or `--overlays-only` to
   refresh just the overlays without the slow armor reconvert.
   - **Multi-slot feet.** A tattoo that reuses one **body** texture on the **feet**
     slot can't share a single rebake (the feet need their own UV). For those, the
     pass bakes a feet-UV variant to a new path and **recompiles the RaceMenuBase
     script** so its `AddFeetPaint` points there. Needs the Papyrus compiler
     (auto-located via the registry / MO2 gamePath); cleanly skipped if absent.
     Feet-only overlays already convert at their own path and are left untouched.

The result is a self-contained output mod (default name: `CBBEtoUBE Auto`) you
enable at the end of your load order. Coverage is inside the Combined plugin
family, so the Combined piece(s) are the only plugins to enable.

## How the refit works

For each shape in an armor NIF:

1. Find the closest point on the CBBE reference body for every armor vertex —
   a triangle on the CBBE mesh plus barycentric coordinates inside it.
2. Evaluate the same (triangle, barycentric) on the UBE reference body. The
   delta between the two surface points is the per-vertex deformation.
3. Apply the deformation to the armor vertex.
4. Copy bone weights from the nearest UBE reference vertices (a weighted blend
   across the k nearest neighbours) and renormalize.
5. Recompute normals + tangents.
6. Carry through shader (including additive glow / effect-shader overlays and
   their scroll/pulse animation), textures, vertex colors (an overlay's fade is
   a per-vertex alpha gradient), alpha, partitions, and physics rigging.

`_0.nif` (weight-0 / slim) and `_1.nif` (weight-1 / full) pairs are processed
together so the in-game weight slider keeps working.

### Fit-correction passes

The raw warp alone is not enough — UBE is larger than the 3BA build body, and
a per-shape warp scrambles things the source author got right. A stack of
correction passes runs after it (all in world frame, offset
`global_to_skin` transforms reconciled; non-identity scale/translation on
skinned shapes is baked into the verts):

- **Anti-poke / adaptive clearance** — body verts that poke through armor are
  cleared along the body normal; the clearance floor is *morph-aware* (tight
  in static zones, real clearance only where breast/butt/belly sliders move).
- **Body-motion match** — where armor hugs the body, each armor vertex copies
  the morph/pose delta of the body surface it covers (follow ratio ~1.0), so its
  clearance is preserved as sliders and physics move the body: the armor can
  neither be left behind (body pokes through) nor overshoot (armor balloons).
  Blends from exact-copy at the hugging surface to smoothed drape farther out.
- **Jiggle clearance** — clears armor against the body's *moving* envelope, not
  just its rest pose, so soft-body jiggle can't push skin through at the peak of
  motion. On by default; `CBBE2UBE_NO_JIGGLE_CLEARANCE=1` disables it.
- **Source-standoff conform** — a piece that hugged the 3BA body is reeled
  back to hug UBE instead of floating at the over-projected distance;
  pull-in only, with a bust-band exception so the nipple can't poke through.
- **Warp groove smoothing** — roughness-weighted smoothing of the warp
  *displacement* removes localized warp noise (breast "indent lines") without
  flattening real detail.
- **Multi-layer order restoration** — the warp's min-standoff clamp collapses
  a layered outfit's radial stacking (belts sink into corsets, trim sinks
  under breastplates). The pass re-imposes the **source** layer order
  per-region: each vertex's order constraints are gated by a local
  consistency field and bound to the specific partner vertices that sat
  below/above it *in the source*, so region-dependent stacking (fabric under
  a plate on the torso but tucked over it at the neckline) and three-sheet
  sandwiches survive, while genuine weaves stay co-planar. Lift-only, so it
  can never push cloth into the body. Set `CBBE2UBE_LAYER_DEBUG=1` for
  per-round resolution stats on stderr.
- **Cleavage passes** — co-planar bust layers (bra under fabric) are depth-
  separated (source-order-gated, so it never buries a layer the source put on
  top) and their bone weights synced so they jiggle identically under
  HDT-SMP instead of intersecting in motion.
- **Fitted-cloth body conform** — a skin-tight garment (leggings, pantyhose,
  bodysuit) has to deform *with* the body or the body clips through it where a
  limb swings most (measured: a pantyhose's inner-back-thigh followed the
  leg-swing bone only ~54% as much as the body's ~65%). Garment-class shapes —
  detected, never hardcoded per armor: they carry soft-body jiggle weight, hug
  the body, and aren't physics chains — have their *divergent* verts conformed
  to the body's own per-vertex skinning, gated by a per-bone weight delta so
  already-matched verts are left alone and the per-vert bone set can only shrink
  (partition-safe). Rigid plate armor carries no jiggle weight / stands off the
  body, so it's excluded and stays rigid. SMP per-triangle colliders are excluded
  *precisely* — read from the armor's own HDT-SMP XML, not by name — so the conform
  can never re-weight a collision proxy (which would re-graft the over-jiggle the
  reskin pass avoids). Disable with `CBBE2UBE_NO_CONFORM=1`; tune the gates via
  `CBBE2UBE_CONFORM_*`.
- **Soft-cloth bust/butt inflation** — the anti-poke can't move sim-driven verts
  (it would disturb the physics), so for cloth whose bust/butt is genuinely
  physics-driven the breast/butt bands are nudged outward instead, so the larger
  UBE body can't punch through the sim. A *rigid* bust (an HDT-rigged robe whose
  chains drive only the skirt) is excluded so it isn't ballooned. Disable with
  `CBBE2UBE_NO_SOFTCLOTH_INFLATE=1`.
- **Z-fight split, degenerate-triangle repair, normal recompute** — final
  cleanup so moved verts don't shimmer, pinch flat, or shade wrong.
- **Physics & morphs carried through** — HDT-SMP chains are blended back
  un-warped (no collapsed skirts), BODYTRI morph data is regenerated to match
  the new geometry, and hem verts grazing foot bones are kept off the
  hand/foot misclassification path.

## Quick start (standalone exe)

The built executable is self-contained (`pynifly` + `NiflyDLL.dll` are bundled).

```
# graphical front-end (default — what MO2 launches or a double-click opens)
dist\CBBEtoUBE\CBBEtoUBE.exe

# headless one-click pipeline over the whole modlist
dist\CBBEtoUBE\CBBEtoUBE.exe auto
```

Running the exe with **no arguments** launches the GUI — the default when MO2
runs it or you double-click it. Run the headless one-click pipeline directly
with the `auto` subcommand (what the GUI's convert button runs under the hood).
Point MO2 at `CBBEtoUBE.exe` and the tool auto-discovers the modlist layout.

## Running from source

```
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python cbbe_to_ube_main.py            # launches the GUI (default)
python cbbe_to_ube_main.py auto       # headless full pipeline
```

### Installing pynifly

`pynifly` is BadDog's Python binding for `nifly`, distributed alongside the
PyNifly Blender plugin:

  https://github.com/BadDogSkyrim/PyNifly

Place `pynifly` (the `pyn` package) plus `NiflyDLL.dll` in `.pynifly/` at the
repo root (already vendored here), or on `sys.path`.

## Commands

| Command | Purpose |
|---|---|
| *(none)* / `gui` | Tkinter front-end — the default when run with no arguments |
| `auto` | Full headless pipeline: convert all candidate mods, merge, add coverage |
| `convert -o OUT SRC …` | Convert one or more specific source mod folders |
| `scan MODS_ROOT` | Pre-flight: list candidate CBBE armor mods without converting |
| `merge -o OUT PATCH PATCH …` | Re-merge the per-mod patch ESPs into the combined plugin (needs 2+ patches) |
| `validate MOD_DIR` | Re-load and sanity-check converted output |
| `discover-body-ref` | Locate a usable UBE body reference NIF |

Arguments shown in caps are required — `auto` is the only subcommand that
takes none.

Useful `auto` flags:

- `-o, --output` — output mod folder (default: `<mods>/CBBEtoUBE Auto`)
- `--workers N` — parallel worker processes (default: CPU − 1)
- `--copy-textures` — copy source textures into the output (off by default;
  textures otherwise resolve from the source mods via the VFS)
- `--only-mods NAME …` — reconvert **only** the named mod folders (repeat the
  flag or comma-separate). The merge still rebuilds the Combined ESP over
  *all* existing per-mod patches, so everything else keeps its current meshes
  and records. Needs a prior full run. The fast way to iterate on one armor.
- `--incremental` — skip mods whose output is already current (a code or body
  change forces a full reconvert automatically)
- `--list-only` — dry run: list the mods that *would* convert, then stop
- `--merged-name NAME` — filename of the merged Combined ESP
- `--exclude-mods NAME …` — never convert the named mod folders. Use this for
  armor **already built for UBE**: converting it again would double-convert and
  break it.
- `--no-ube-native-scan` — turn off the geometry check that skips mods whose
  armor **already fits the UBE body**. Meshes under `meshes/!UBE/` are skipped
  by path regardless; this check is the backstop for UBE-native armor shipped
  at ordinary paths, where nothing in the name or path gives it away. It is
  deliberately conservative — only a decisive fit against the UBE body skips a
  mod, an ambiguous one converts as normal — because wrongly skipping a real
  CBBE mod leaves its armor unfitted in game. Use this if it ever misjudges.
- `--plugins-only` — rebuild only the plugins (ESP + SkyPatcher INI) from the
  last run's snapshots, skipping all mesh work. Minutes instead of hours when
  only the plugin side changed.
- `--no-auto-merge` — convert without rebuilding the Combined ESP
- `--no-textures` — skip the texture copy
- `--convert-overlays` — also rebake CBBE/3BA RaceMenu **body/hands/feet
  overlays** (tattoos, body paints) into UBE UV space (loose DDS in the output
  mod; RaceMenu loads them by load order, no ESP). **Needs `texconv`** — see
  Requirements.
- `--overlays-only` — run **only** the overlay rebake, skipping the armor
  convert/merge entirely (implies `--convert-overlays`) — the fast refresh once
  armor is already converted
- `--overlay-copy` — non-destructive overlay mode that keeps the originals
  working on non-UBE races (needs the Papyrus compiler)
- `--overlay-skip-male`, `--overlay-mods NAME …`, `--overlay-exclude-mods NAME …`
  — limit which overlay packs get processed

### Discovery overrides

The tool anchors on `ModOrganizer.ini` (walking up from the exe / CWD).
When running it from outside the instance, point it explicitly:

- `CBBE2UBE_MO2_INI` — path to the instance's `ModOrganizer.ini`
- `CBBE2UBE_MODS_ROOT` — the `mods/` folder directly (skips INI parsing)
- `CBBE2UBE_GAME_DATA` — game `Data` folder(s), `;`-separated
- `CBBE2UBE_LAYER_DEBUG=1` — per-round stats from the layer-order pass

## Reference bodies

The converter needs the source (CBBE) and target (UBE) base body meshes — the
CBBE 3BA body and the matching UBE body built via BodySlide (the
`femalebody_0.nif` / `femalebody_1.nif` pair from each), for example:

- CBBE: `<modlist>/mods/CBBE 3BA (3BBB)/meshes/actors/character/character assets/femalebody_{0,1}.nif`
- UBE (BodySlide output): `<modlist>/mods/<UBE BodySlide Output>/meshes/actors/character/character assets/femalebody_{0,1}.nif`

The `auto` pipeline auto-discovers both from the modlist (and `convert` takes
`--ube-body-ref` to pin the UBE reference explicitly). The low-level
single-NIF CLI (`src/cli.py`) takes the parent folders via
`--cbbe-dir` / `--ube-dir`, defaulting to the same auto-discovery.

## Layout

```
cbbe-to-ube/
  cbbe_to_ube_main.py     # entry point (-> src.auto_convert.main)
  CBBEtoUBE.spec          # PyInstaller build spec
  .pynifly/               # vendored pynifly (pyn) + NiflyDLL.dll
  src/
    auto_convert.py       # the full MO2-aware pipeline (the exe's main)
    nif_convert.py        # core CBBE/3BA -> UBE NIF conversion (refit, physics, bakes)
    ube_patcher.py        # generate UBE patch ESP from a source armor ESP
    overlay_transfer.py   # rebake CBBE/3BA RaceMenu overlays (tattoos) to UBE UV
    esp.py                # Skyrim SE ESP/ESM read + write
    atomic_io.py          # crash-safe atomic writes for all game-loaded output
    hdt_xml_gen.py        # per-armor HDT-SMP collision XML generator
    discovery.py / paths.py   # MO2 mod-tree discovery + layout auto-detect
    bsa_strings.py        # localized ARMO names from .STRINGS tables
    tri.py / osd.py / sliderset_gen.py   # BODYTRI / OutfitStudio / slider data
    hh_offset.py / nif_patch.py          # high-heel offset, binary NIF patching
    preview.py            # headless morph-preview renderer
    gui.py                # Tkinter GUI
    build_mod.py          # output-mod assembly helpers
    cli.py / refit.py / correspondence.py / weights.py / nif_io.py
                          # low-level single-armor refit interface
  tests/
  scripts/                # build + maintenance helpers (exe build, coverage-ESP
                          # regen, output health / QA checks, install)
  output/                 # local conversion output (gitignored)
  dist/CBBEtoUBE/         # built standalone executable
```

## Dependencies

**To run the converter (dev machine):**

- Python 3.10+
- `numpy`, `scipy`, `lz4` (pip — see `requirements.txt`). `lz4` is **not
  optional**: Skyrim SE BSA archives are LZ4-frame compressed, and without it
  BSA mesh reads silently return nothing, so vanilla-armor conversion produces
  no output.
- `pynifly` + `NiflyDLL.dll` (vendored in `.pynifly/`; not on PyPI)
- **`texconv`** — only for the overlay features (`--convert-overlays` /
  `--overlays-only`). Put it in the MO2 `tools/` folder or beside the exe, or
  point `CBBE2UBE_TEXCONV` at it. Without it the overlay pass is skipped.
- **Papyrus compiler** — only for `--overlay-copy` and the multi-slot feet
  overlay path. Auto-located via the registry / MO2 gamePath; override with
  `CBBE2UBE_PAPYRUS_COMPILER`.

**In-game (the converted output requires these on the target modlist):**

- **SkyPatcher** (SKSE plugin) — the converter attaches every armature via a
  SkyPatcher INI, not ESP overrides, so converted armor is invisible without it.
  This is a hard dependency with no fallback. It also needs
  **`iEnableArmorPatching=1`** in `SKSE/Plugins/SkyPatcher.ini` — with it set to
  `0` you get exactly the same symptom as not having SkyPatcher at all (every
  converted piece invisible, no other diagnostic). The converter's setup check
  fails on both cases.
- **RaceCompatibility** — a UBE prerequisite; the Light build carries the
  RaceDispatcher that puts converted armatures on the UBE races at runtime.
- **UBE** and its **`UBE_AllRace.esp`** — the target body/race the minted
  armatures point at.
- **RaceMenu** — drives the BODYTRI body-morph data the converter regenerates
  (and the optional overlay transfer).

Run **Check setup** in the GUI to verify all of the above before converting.

## Building the exe

```
pip install pyinstaller
pyinstaller CBBEtoUBE.spec
```

(or `scripts\build_exe.ps1`, which installs PyInstaller if missing and takes
`-Clean`). Produces a self-contained onedir build at `dist/CBBEtoUBE/`
(`CBBEtoUBE.exe` + `_internal/`).

`LICENSE` and `THIRD-PARTY-NOTICES.md` are bundled into the build so the binary
is distributed with its licence; PyInstaller places them in
`dist/CBBEtoUBE/_internal/`. The build also deliberately **excludes** the `_ssl`
and `_hashlib` extensions — they are the only things that pull in OpenSSL, whose
1.1.x licence is GPL-incompatible, and nothing here needs them. Note the
exclusion targets those C extensions, **not** the pure-Python `hashlib` module,
which the stdlib imports unconditionally.

## License

CBBEtoUBE is licensed under the **GNU General Public License v3.0** — see
[LICENSE](LICENSE). Copyright (C) 2026 DayOnly.

This project uses BadDog's **PyNifly** (the `pyn` package + `NiflyDLL.dll`),
which is **GPL-3.0** — source: <https://github.com/BadDogSkyrim/PyNifly>.
Because PyNifly is GPL-3.0 and is included here (vendored in `.pynifly/` and
frozen into the built exe), CBBEtoUBE as a whole is distributed under GPL-3.0
to stay license-compatible. `NiflyDLL.dll` is the compiled binary of that
project; its corresponding source is available at the link above.

Other bundled components — numpy, scipy, OpenBLAS, Tcl/Tk in the PyInstaller
bundle (`dist/CBBEtoUBE/_internal/`) — are under their own permissive
(BSD-style) licenses. See [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
