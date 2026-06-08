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
3. Writes the converted meshes under `meshes/!UBE/...` in a single output mod.
4. Generates a **per-mod UBE patch ESP** for each source, then merges them into
   one **ESL-flagged combined plugin** with a correct master order.
5. Adds **vanilla race coverage** and **modded non-body coverage** (shields,
   cloaks, helmets, etc.) so armatures defined by other mods still render on UBE
   races.

The result is a self-contained output mod (default name: `CBBEtoUBE Auto`) you
enable at the end of your load order.

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

## Quick start (standalone exe)

The built executable is self-contained (`pynifly` + `NiflyDLL.dll` are bundled).

```
# full one-click pipeline over the whole modlist
dist\CBBEtoUBE\CBBEtoUBE.exe

# graphical front-end
dist\CBBEtoUBE\CBBEtoUBE.exe gui
```

Running the exe with **no arguments** runs the full `auto` pipeline — this is
what an MO2 executable entry invokes. Point MO2 at `CBBEtoUBE.exe` and the tool
auto-discovers the modlist layout.

## Running from source

```
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python cbbe_to_ube_main.py            # = the `auto` full pipeline
python cbbe_to_ube_main.py gui        # the GUI
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
| *(none)* / `auto` | Full pipeline: convert all candidate mods, merge, add coverage |
| `convert` | Convert one or more specific source mod folders |
| `gui` | Tkinter front-end |
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
- `--only-mods NAME …` — convert just the named mods
- `--incremental` — skip mods whose output is already current
- `--list-only` — dry run: list the mods that *would* convert, then stop
- `--no-vanilla-compat` / `--no-modded-nonbody` / `--no-vanilla-bodies` —
  skip individual coverage passes

## Reference bodies

The converter needs the source (CBBE) and target (UBE) base body meshes — the
CBBE 3BA body and the matching UBE body built via BodySlide (the
`femalebody_0.nif` / `femalebody_1.nif` pair from each), for example:

- CBBE: `<modlist>/mods/CBBE 3BA (3BBB)/meshes/actors/character/character assets/femalebody_{0,1}.nif`
- UBE (BodySlide output): `<modlist>/mods/<UBE BodySlide Output>/meshes/actors/character/character assets/femalebody_{0,1}.nif`

The `auto` pipeline auto-discovers these from the modlist; for the low-level
tools, copy or symlink them into `references/cbbe/` and `references/ube/`, or
pass `--cbbe-ref` / `--ube-ref`.

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
    cli.py / refit.py / correspondence.py / weights.py / nif_io.py
                          # low-level single-armor refit interface
  tests/
  scripts/                # build + maintenance helpers
  references/             # CBBE + UBE base body NIFs (gitignored)
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

Produces a self-contained onedir build at `dist/CBBEtoUBE/` (`CBBEtoUBE.exe` +
`_internal/`).

## License

CBBEtoUBE is licensed under the **GNU General Public License v3.0** — see
[LICENSE](LICENSE). Copyright (C) 2026 DayOnly.

Bundled third-party components keep their own licenses: `pynifly` /
`NiflyDLL.dll` (BadDog's PyNifly — confirm its terms upstream before
redistributing), and numpy / scipy / OpenBLAS / Tcl-Tk inside the PyInstaller
bundle under `dist/CBBEtoUBE/_internal/`.
