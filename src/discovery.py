"""Find refit candidates by walking an MO2 mod tree.

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
    ``'armor/foo/bar_1.nif'``) to the WINNING provider's absolute file across
    all enabled mods, in MO2 priority order — the first enabled mod to provide
    a path wins, matching how the game's VFS resolves it.

    Why this exists: the per-mod converter used to look for an armour's meshes
    ONLY inside that armour's own mod folder. But in an MO2 modlist the meshes
    the game actually loads can live in a DIFFERENT mod — most commonly the
    BodySlide *output* mod (built female meshes), or a mesh/texture replacer,
    or a patch. Resolving through the full VFS here means the converter finds
    and refits those armours instead of silently missing them.

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
    remaining = set(target_keys) if target_keys is not None else None
    for mod_name in enabled_mods:
        if remaining is not None and not remaining:
            break  # every referenced mesh found; lower-priority mods can't win
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
                if remaining is not None:
                    remaining.discard(rel)
    return index
