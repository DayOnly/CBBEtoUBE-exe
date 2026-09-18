# The TRI out-of-bounds entries: two defects, and a metric that hid both

Measured 2026-09-06 against the shipped pack (3673 NIFs, 2960 after the census's
`1stperson` / `_bsa_staging` exclusions). The pack census reported five TRI
offsets out of bounds. They are **two unrelated defects**, and the fix for one of
them had already shipped and was doing nothing.

    !UBE\armor\vigcgt\...\qparmrf1p.nif  :: ClothF1st    495 verts, tri maxidx  1544
    !UBE\armor\vigcgt\...\qpgntltsf.nif  :: GauntletsF  1544 verts, tri maxidx  3013
    !UBE\...\fittedebony\female\cuirass_0.nif :: BaseShape 3618 verts, maxidx 29297
    !UBE\...\fittedebony\female\cuirass_1.nif :: BaseShape 3618 verts, maxidx 29297
    !UBE\clothes\sheogorath\sheogorathoutfit.nif :: Outfit 2467 verts, maxidx 13705

---

## 1. #tri-variant-collision — the first fix was INERT, and why

`4a0b5f5` shipped `_tri_is_owning_variant` / `_tri_fits_variant`. The predicate
was right; the place it got its answer from was not. It asked

    Path(src_path).with_name(stem + "_0" + suffix).is_file()

— the sibling **next to the source**. The competing variant is routinely in a
**different mod**, and a BSA-resolved source is staged **alone**. Traced on
`clothes/sheogorath/sheogorathoutfit`:

    vanilla BSA        sheogorathoutfit.nif           2467 verts, staged ALONE
    a body-refit mod   sheogorathoutfit_0.nif/_1.nif  13706 verts + its own .tri

Both converge on one output stem, so both derive `sheogorathoutfit.tri`. The
guard saw no sibling beside the staged file, answered "nothing else claims this",
and let the 2467-vertex model point at the pair's 13706-vertex morphs.

**THE FIX IS TO RESOLVE THE SIBLING THE WAY SOURCES ARE RESOLVED.**
`auto_convert._variant_sources_by_base` groups `resolved_pairs` — which came out
of the same mod list / VFS / BSA chain that found the file being converted — into
`{base -> {suffix -> source path}}`, and that map rides the work-item tuple into
the worker as `variant_sources`. It has to be built in the parent: a conversion
worker is a spawned process that inherits none of those indices, and the
destination siblings it could see instead are being written concurrently by other
workers (which is what made this a race in the first place).

Two rules follow from having the whole set instead of one probe:

* **Ownership by priority `_0` > `_1` > no-suffix.** `_0` beats `_1` is measured
  and unchanged — picking `_1` cost an in-game nipple through a cuirass. The
  no-suffix variant now yields to either. That also closes a hole the probe could
  not see at all: with a `_1` but no `_0`, neither file ends in a claimed suffix,
  so **both** wrote the shared `.tri`. (Population of that hole in this pack: 0.)
* **Fit against the OWNER**, not against a hardcoded `_0`.

### What it acts on, pack-wide

    output NIFs                            3673
    no-suffix NIFs                          601
    no-suffix sharing a stem with a `_0`     16
      vertex counts AGREE  -> unchanged      10
      MISMATCH -> BODYTRI withheld            4
      no shared shape name -> kept, inert      2

Four pieces, 0.11% of the pack. Each is a first-person or world model that was
being handed morph indices past the end of its own vertex array; a variant that
does not fit gets no BODYTRI, which is strictly better. One of the four
(`minerboots_f`, 400 verts against the pair's 1232) was NOT among the five: its
tri's highest index happened to land under 400. The guard checks the invariant —
topology agreement — rather than the symptom, so it catches that one too.

The two "no shared shape name" cases keep their BODYTRI: it points at a tri that
names none of their shapes, so it is inert either way. That is the dead-slider
class below, not this one.

---

## 2. A shape named `BaseShape` that is NOT the body

The `cuirass_0`/`_1` entries are a different defect entirely, and nothing to do
with weight variants. The **author** named one of their own armour shapes
`BaseShape`:

    source cuirass_0.nif: belt 507, LegGuard 840, ironbelt 1150, cuirass2 270,
                          skirt 860, pauldron 342, arm 422, BaseShape 3618,
                          CuirassLong 575

`_collect_tri_inputs` split the shapes by NAME against `UBE_BODY_INJECT_NAMES`,
so that 3618-vertex cuirass part was pulled out of the armour set and handed to
`generate_armor_tri` as a body shape — which embeds the UBE body's OSD morphs
**verbatim**, indexing to 29297. `generate_armor_tri` cannot catch it downstream:
its `#osd-bounds` filter bounds those offsets against the body REFERENCE's vert
count, on the assumption its own comment states — *"body_verts shares the injected
BaseShape's topology"*.

**The injected body is now identified by TOPOLOGY.** A shape carrying the name at
a different vertex count is an authored shape that merely shares it, and goes in
the armour set to get its own propagated morphs. `VirtualBody` stays out of both
sets whatever it measures: it is the SMP collision proxy we generate ourselves,
its topology is per-piece by construction, and `generate_armor_tri` only ever
emits a verbatim body TriShape for `BaseShape` anyway.

    cuirass.tri   BaseShape entry   before: 202 morphs, maxidx 29297  (7.6 MB)
                                     after: 193 morphs, maxidx  3617  (2.0 MB)

Population pack-wide: 4 NIFs have a `BaseShape` that is not 29298 verts (2 at
3618, 2 at 502).

---

## 3. The census could not see either fix — two metric defects

**It scored the tri the STEM derives, not the tri the NIF points at.** After the
fix the three colliding variants ship with no BODYTRI at all, so nothing asks
NioOverride to move a vertex they lack — and the stem-derived tri still sits next
to them, so the old metric still reported all three as out of bounds. A fix that
works would have read exactly as inert as the one that did not. `pack_census` now
resolves each NIF's own BODYTRI and scores that, counting "NIFs with no BODYTRI"
as an explicit exclusion.

    the three colliding NIFs, candidate output
      OOB by STEM (old metric)          3
      OOB by the NIF's OWN BODYTRI      0

**It skipped any tri shape the NIF lacks** (`if n is None: continue`), so a tri
written for a different outfit scored CLEAN. New column, measured on the shipped
pack (2951 NIFs with a tri): 2904 agree, **12 partly disagree, 35 share no shape
name at all**. The majority are a `_1` NIF whose tri was written by its `_0`
partner under `_0`-side shape names — `nif BootsF:1 | tri BootsF:0`,
`nif BodyF:1,RobeF:1 | tri BodyF:0,RobeF:0`. One is a whole different outfit:
`qparmrf.nif` ships `ArmorF`/`PantsF`/`ClothF` beside a tri defining
`Top`/`Pants`/`ChestMetal`/`ArmMetal`/`ShoulderMetal`. The morph never matches at
runtime either way, so this class is **dead sliders, not dangerous ones** — but it
must not read as a pass.

---

## Verified end to end, on the real data

Three source mods run through `auto --only-mods` (the batch path, live MO2 ini,
`PYTHONHASHSEED=1`, the five live flags echoed by `active flags (5)`), covering
all five entries:

    NIFs scored                              111
    NIFs with NO BODYTRI (named, excluded)     3   qparmrf1p, qpgntltsf,
                                                   sheogorathoutfit
    OOB by STEM (old metric)                   3
    OOB by the NIF's OWN BODYTRI               0

**Geometry-neutral where it acts.** Against the shipped pack, the three files the
fix touches differ at **0.000000000 u** — bit-exact vertices, differing only in
the BODYTRI extra data. (12 other files in that comparison DO move, up to 1.72u:
the shipped pack was built by exe `6df7de6c`, before `#authored-nipple-exempt`.
Those are that fix's effect, not this one's.)
