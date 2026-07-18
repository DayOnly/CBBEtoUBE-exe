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

r"""Find refit candidates by walking an MO2 mod tree.

Mode of operation:

  1. Read the MO2 profile's modlist.txt to learn which mods are enabled and
     in what priority order. MO2 convention: TOP of the file = TOP of the
     GUI = HIGHEST priority (wins file conflicts).
  2. Walk each enabled mod's `meshes\` folder in priority order. The first
     mod to provide a given relative path is the "winning provider" for that
     path; later occurrences are skipped (they lose to the higher-priority
     mod that already claimed the path).
  3. For each winning NIF, decide whether it's a CBBE 3BA armor by loading
     it and looking for a shape named "3BA" with the canonical 18,436-vertex
     count. If present, it's an inline-body CBBE 3BA armor and we'd refit it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from . import nif_io


log = logging.getLogger("discovery")

# Canonical CBBE 3BA female body vertex count. Used as a fast classifier for
# "is this NIF CBBE 3BA armor with an inline body?"
CBBE_3BA_VERT_COUNT = 18436

# Shape names that identify a source bundling the canonical CBBE/3BA body (any
# vert count -- a merged BodySlide project keeps the '3BA' body shape). Used by
# build_mesh_index's within-tier body-match preference. #body-match-source
_CANONICAL_BODY_SHAPE_NAMES = frozenset({"3BA"})
# Diffuse-texture markers that identify a nude body-skin shape (mirrors
# nif_convert._BODY_SKIN_TEXTURE_MARKERS). A shape carrying one of these IS a body,
# even at low poly -- so a bespoke body (an HDT-SMP mod's own physics body) is
# distinguishable from cloth. A source with NO body-skin shape (e.g. a physics robe
# that bundles no body) is NOT a bespoke-body source and is left alone.
_BODY_SKIN_TEXTURE_MARKERS = (
    "femalebody", "malebody", "bodyfemale", "bodymale", "femaleskin",
)
# A bespoke BODY is a real, sizable body-skin shape. These floors separate it from an
# exposed-skin SLICE (baked hands/neck skin on a robe -- body-tex'd but tiny, e.g.
# 46 verts / z-range 5) that must NOT count as a bundled body. The vert floor alone
# rejects slices; the z floor keeps it a body (a full HDT physics body is 1397v /
# z103; a partial-torso underwear body 562v / z38 still counts and still mismatches
# the target's bust).
_BESPOKE_BODY_MIN_VERTS = 500
_BESPOKE_BODY_MIN_Z_RANGE = 35.0
_SENTINEL = object()


@dataclass
class WinningNif:
    relative_path: Path        # path under Data\ (e.g. meshes\Armor\Iron\F\Cuirass_1.nif)
    source_path:   Path        # absolute path on disk in the winning mod folder
    provider_mod:  str         # mod name (folder under mods/)
    has_3ba_body:  bool        # True if the NIF contains a 3BA-named shape at the CBBE 3BA vert count


def parse_enabled_mods(profile_dir: Path) -> list[str]:
    """Read modlist.txt and return enabled mod names in priority order
    (highest priority first, matching MO2 GUI top-to-bottom).
    """
    ml = profile_dir / "modlist.txt"
    if not ml.exists():
        raise FileNotFoundError(f"modlist.txt not found at {ml}")
    out: list[str] = []
    for raw in ml.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("+"):
            out.append(line[1:])
        # '-' = disabled, '*' = backup, '_separator' suffix = MO2 separator
        # All ignored: only '+' enabled mods provide files.
    return out


def find_winning_nifs(
    mods_root: Path,
    profile_dir: Path,
    *,
    skip_mods: tuple[str, ...] = (),
    path_prefixes: tuple[str, ...] = ("meshes\\armor\\", "meshes\\clothes\\",
                                       "meshes\\dlc01\\armor\\", "meshes\\dlc02\\armor\\",
                                       "meshes\\dlc01\\clothes\\", "meshes\\dlc02\\clothes\\",
                                       "meshes\\creationclub\\"),
    classify: bool = True,
) -> list[WinningNif]:
    """Discover NIFs to consider refitting.

    Args:
        mods_root: e.g. <modlist>\\mods
        profile_dir: e.g. <modlist>\\profiles\\<ProfileName>
        skip_mods: mod folder names to ignore (e.g. the output mod itself)
        path_prefixes: only consider relative paths starting with one of these
            (lowercased). Defaults cover armor/clothes/DLC/CC scoped to female
            outfits.
        classify: if True, load each winning NIF and set has_3ba_body. Costs
            ~50-200ms per NIF; skip with classify=False for a fast dry-run.
    """
    enabled = parse_enabled_mods(profile_dir)
    skip_set = set(skip_mods)
    seen: dict[str, WinningNif] = {}

    for mod_name in enabled:
        if mod_name in skip_set:
            continue
        mod_path = mods_root / mod_name
        meshes_dir = mod_path / "meshes"
        if not meshes_dir.is_dir():
            continue
        for nif_abs in meshes_dir.rglob("*.nif"):
            rel = nif_abs.relative_to(mod_path)
            rel_key = str(rel).lower()
            if not rel_key.startswith(path_prefixes):
                continue
            if rel_key in seen:
                continue   # a higher-priority mod already claimed this path
            seen[rel_key] = WinningNif(
                relative_path=rel,
                source_path=nif_abs,
                provider_mod=mod_name,
                has_3ba_body=False,
            )

    if classify:
        for i, win in enumerate(seen.values(), 1):
            try:
                win.has_3ba_body = _has_3ba_body(win.source_path)
            except Exception as e:
                log.warning("classify %s: %s", win.source_path, e)
            if i % 50 == 0:
                log.info("classified %d/%d", i, len(seen))

    return list(seen.values())


def _has_3ba_body(nif_path: Path) -> bool:
    """Cheap classifier: open the NIF and look for a shape named '3BA' with
    exactly CBBE_3BA_VERT_COUNT verts.
    """
    nif = nif_io.load_nif(nif_path)
    for s in nif.shapes:
        if s.name == "3BA" and len(s.verts) == CBBE_3BA_VERT_COUNT:
            return True
    return False


def build_mesh_index(
    mods_root: Path,
    enabled_mods: list[str],
    *,
    target_keys: "set[str] | None" = None,
    skip_mods: "tuple[str, ...] | set[str]" = (),
) -> dict[str, Path]:
    """Map each ``meshes\\``-relative NIF path (lowercase, forward-slash, e.g.
    ``'armor/foo/bar_1.nif'``) to the winning provider's absolute file across
    all enabled mods in MO2 priority order. Resolves through the full VFS so
    meshes that live in a replacer or BodySlide output mod are found correctly.

    Args:
      mods_root: ``<modlist>/mods``.
      enabled_mods: enabled mod folder names, HIGHEST priority first
        (``paths.enabled_mods_ordered`` order). First provider wins.
      target_keys: if given, only index NIFs whose key is in this set, and stop
        walking once every target is found — bounds the walk on huge modlists.
        Keys are lowercase forward-slash ``meshes\\``-relative paths WITH the
        ``.nif`` and any ``_0``/``_1`` weight suffix.
      skip_mods: mod folder names to skip (e.g. the converter's own output mod).
    """
    skip = {m.lower() for m in skip_mods}  # case-insensitive: mod folder names
    index: dict[str, Path] = {}            # and skip entries can differ in case
    win_tier: dict[str, int] = {}          # rel -> tier of the current winner
    remaining = set(target_keys) if target_keys is not None else None
    # DEPRIORITIZE BodySlide-output mods as the armour SOURCE. A BodySlide output
    # is the mesh morphed to a specific BODY PRESET; using a 3BA/HIMBO output as
    # the source for a UBE conversion bakes the wrong body's shape into the result
    # (layers squashed together -> clipping). The base mod (what BodySlide BUILDS
    # from) is the clean source. So resolve in 3 tiers, preserving MO2 priority
    # within each: (0) non-output mods, (1) UBE outputs, (2) other-body outputs.
    # A BodySlide output still wins for a mesh NOTHING else provides (no regression
    # for armour whose mesh only ships as a built output). #bodyslide-source
    def _tier(name: str) -> int:
        nl = name.lower()
        if "bodyslide output" not in nl:
            return 0
        return 1 if "ube" in nl else 2
    # WITHIN a tier, prefer a provider that bundles the CANONICAL CBBE/3BA body over
    # one bundling a bespoke body (e.g. an HDT-SMP "vanilla armours" mod ships its own
    # slim/large physics body). The armour is authored FLUSH on whatever body it
    # bundles; if that body's proportions differ from the UBE target the piece is left
    # standing off (soft-body cloth is kept at its source position -> a visible gap at
    # the bust). A canonical-3BA source matches the pipeline's assumptions and the UBE
    # target, so it converts flush. Measured: an HDT-SMP fur cuirass's bundled body bust +9.88u ->
    # +1.77u standoff; the 3BA-body source (+5.70u ~= UBE +5.74u) -> ~flush.
    # Off with CBBE2UBE_NO_BODYMATCH_SELECT=1. #body-match-source
    import os as _os
    _bodymatch = (_os.environ.get("CBBE2UBE_NO_BODYMATCH_SELECT", "").strip().lower()
                  not in ("1", "true", "yes", "on"))
    _prov_cache: dict[Path, "tuple[bool, bool] | None"] = {}

    def _body_provenance(path: Path) -> "tuple[bool, bool] | None":
        """(has_canonical_body, has_bespoke_body) for a source NIF, or None on open
        failure. canonical = a '3BA'-named body shape. bespoke = a body-skin-textured
        shape that is NOT canonical (an HDT-SMP mod's own physics body, a slim/large
        preset body). A source with neither (e.g. a physics robe that bundles no body)
        is left untouched by the swap rule."""
        cached = _prov_cache.get(path, _SENTINEL)
        if cached is not _SENTINEL:
            return cached
        result: "tuple[bool, bool] | None"
        try:
            nf = nif_io.open_nif_retry(str(path))
            has_canon = has_bespoke = False
            for s in nf.shapes:
                nm = s.name or ""
                if nm in _CANONICAL_BODY_SHAPE_NAMES:
                    has_canon = True
                    continue
                try:
                    tex = dict(getattr(s, "textures", {}) or {})
                except Exception:
                    tex = {}
                diff = (tex.get("Diffuse") or tex.get("0")
                        or next((v for v in tex.values() if v), "")).lower()
                if not any(m in diff for m in _BODY_SKIN_TEXTURE_MARKERS):
                    continue
                # Body-skin diffuse -> could be a bespoke body OR just an exposed-skin
                # slice. Require full-body geometry so a robe's baked hand/neck skin
                # isn't mistaken for a bundled body (which would wrongly drop the
                # source's SMP physics).
                try:
                    vs = s.verts
                    if len(vs) < _BESPOKE_BODY_MIN_VERTS:
                        continue
                    zs = [float(v[2]) for v in vs]
                    if (max(zs) - min(zs)) >= _BESPOKE_BODY_MIN_Z_RANGE:
                        has_bespoke = True
                except Exception:
                    pass
            result = (has_canon, has_bespoke)
        except Exception:
            result = None
        _prov_cache[path] = result
        return result

    found_max_tier = -1
    ordered_mods = sorted(enabled_mods, key=_tier)  # stable: keeps priority in-tier
    for mod_name in ordered_mods:
        mtier = _tier(mod_name)
        # Early-stop only once every referenced mesh is found AND no still-unwalked
        # mod could out-rank a current winner. With body-match on, a later SAME-tier
        # mod can still replace a winner, so we must finish every tier <= the deepest
        # tier any winner came from before stopping.
        if remaining is not None and not remaining and mtier > found_max_tier:
            break
        if mod_name.lower() in skip:
            continue
        meshes_dir = mods_root / mod_name / "meshes"
        if not meshes_dir.is_dir():
            continue
        try:
            nifs = meshes_dir.rglob("*.nif")
        except OSError:
            continue
        for nif in nifs:
            try:
                rel = nif.relative_to(meshes_dir).as_posix().lower()
            except (ValueError, OSError):
                continue
            if target_keys is not None and rel not in target_keys:
                continue
            if rel not in index:           # first (highest priority) wins
                index[rel] = nif
                win_tier[rel] = mtier
                if mtier > found_max_tier:
                    found_max_tier = mtier
                if remaining is not None:
                    remaining.discard(rel)
            elif _bodymatch and win_tier.get(rel) == mtier:
                # Same tier. Swap ONLY when the incumbent bundles a BESPOKE (mismatched-
                # preset) body but the challenger bundles the CANONICAL 3BA body: the
                # challenger converts flush where the incumbent's body mismatch leaves
                # the piece standing off. A source that bundles NO body (e.g. a physics
                # robe) is NOT overridden -> its SMP physics is preserved.
                inc = _body_provenance(index[rel])
                chal = _body_provenance(nif)
                if (inc is not None and chal is not None
                        and inc == (False, True)     # incumbent: bespoke body, no canonical
                        and chal[0]):                # challenger: has canonical body
                    index[rel] = nif       # tier unchanged; priority already lost, body wins
    return index
