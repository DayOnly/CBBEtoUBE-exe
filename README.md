# CBBEtoUBE

Batch-converts Skyrim SE armor and clothing built for **CBBE / 3BA** so it fits
the **UBE** body without clipping, and emits the plugin patches needed for the
converted meshes to show up in game. Pure-Python pipeline on top of `pynifly`
+ `numpy` + `scipy`.

It is **not** a Synthesis/Mutagen patcher. Clipping is a mesh-geometry problem,
not a plugin-record problem, so the tool rewrites NIF vertices and bone weights
directly, then generates the minimal ESP records that point the armor at the new
meshes.

## What it does

Given a Mod Organizer 2 setup, the full pipeline (`auto`):

1. **Discovers** candidate CBBE/3BA armor mods by walking the MO2 mod tree, and
   resolves every armor mesh through the full virtual file system (BodySlide
   output, BSAs, and loose files all count).
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
   **all** of them.
5. Adds **vanilla race coverage** and mints **`UBE_ModNonBody_Coverage.esp`** +
   **`UBE_ModBody_Coverage.esp`** (with active SkyPatcher INIs) so armatures
   defined by other mods — overhaul-rearmatured helmets, circlets, jewelry,
   and mod-defined body variants — still render on UBE races. **Both ESPs must
   be enabled in MO2**, or the covered items are invisible on UBE actors.

The result is a self-contained output mod (default name: `CBBEtoUBE Auto`) you
enable at the end of your load order, plus the two coverage ESPs above.

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
6. Carry through shader, textures, alpha, partitions, and physics rigging.

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
| `convert` | Convert one or more specific source mod folders |
| `scan` | Pre-flight: list candidate CBBE armor mods without converting |
| `merge` | Re-merge the per-mod patch ESPs into the combined plugin |
| `vanilla-compat` | (Re)build the vanilla UBE race-coverage plugin |
| `validate` | Re-load and sanity-check converted output |
| `discover-body-ref` | Locate a usable UBE body reference NIF |

Useful `auto` flags:

- `-o, --output` — output mod folder (default: `<mods>/CBBEtoUBE Auto`)
- `--workers N` — parallel worker processes (default: CPU − 1)
- `--copy-textures` — copy source textures into the output (off by default;
  textures otherwise resolve from the source mods via the VFS)
- `--only-mods NAME …` — reconvert **only** the named mod folders (repeat the
  flag or comma-separate). The merge still rebuilds the Combined ESP over
  *all* existing per-mod patches, so everything else keeps its current meshes
  and records. Needs a prior full run; skips the vanilla coverage passes
  unless `--force-vanilla` is given. The fast way to iterate on one armor.
- `--incremental` — skip mods whose output is already current (a code or body
  change forces a full reconvert automatically)
- `--list-only` — dry run: list the mods that *would* convert, then stop
- `--merged-name NAME` — filename of the merged Combined ESP
- `--no-winner-rebase` — don't rebase merged ARMO stats onto the load-order
  winner (default on: converted armor keeps Requiem/overhaul balance)
- `--no-vanilla-compat` / `--no-modded-nonbody` / `--no-vanilla-bodies` —
  skip individual coverage passes

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
    esp.py                # Skyrim SE ESP/ESM read + write
    hdt_xml_gen.py        # per-armor HDT-SMP collision XML generator
    vanilla_bsa_armor.py  # standalone vanilla body-armor conversion
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
  scripts/                # build + maintenance helpers (incl. diag_ruby_layers.py,
                          # a layer-order flip diagnostic usable on any converted armor)
  output/                 # local conversion output (gitignored)
  dist/CBBEtoUBE/         # built standalone executable
```

## Dependencies

- Python 3.10+
- `numpy`, `scipy` (pip — see `requirements.txt`)
- `pynifly` + `NiflyDLL.dll` (vendored in `.pynifly/`; not on PyPI)

## Building the exe

```
pip install pyinstaller
pyinstaller CBBEtoUBE.spec
```

(or `scripts\build_exe.ps1`, which installs PyInstaller if missing and takes
`-Clean`). Produces a self-contained onedir build at `dist/CBBEtoUBE/`
(`CBBEtoUBE.exe` + `_internal/`).

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
