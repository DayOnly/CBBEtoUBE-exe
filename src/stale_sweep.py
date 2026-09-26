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

r"""#stale-output-sweep -- move our old conversions that no source makes any
more out of `meshes\!UBE`, on evidence, never on absence alone.

THE DEFECT. The output folder is never cleaned. A mesh an earlier run converted
stays in `meshes\!UBE` after the planner stops making it -- its source was
removed, disabled or excluded, a third-party UBE patch now covers the armour, a
later plugin made it non-playable, the female-only rule dropped the male mesh.
Our mod sits high in MO2, so the stale copy still wins its path in game, and
the coverage step counts it as converted and points armatures at it. Measured
on the reported modlist (2026-09-25): 37 bases, 103 files, 128 MB.

WHY NOT "EVERY FILE NO CLAIM OWNS". Absence is the signature of a failure as
well as of a decision: the planner fails OPEN to a SMALLER plan in several
places (an NPC-outfit read that failed drops 6 sources and 48 armour pieces
female NPCs wear; an archive or mesh index that did not build; a source that
resolved nothing). Moving on absence would make those pieces invisible. So:

1. A MANIFEST (`_conversion_manifest.json`) is written every `auto` run: each
   weight base this run claimed and the source mod that claimed it, each
   per-source patch and its source, the run stamp and the build. A base the
   manifest does not record is never moved in the run that finds it (a first
   run after this shipped has no manifest, so it only reports). It is recorded
   ("adopted") only when a source of that run gives a positive reason for
   exactly its weight base; a later full run then moves it if that source still
   gives the reason or is gone. Nothing tells a file a user placed by hand from
   one an earlier run wrote -- the converter marks neither its meshes nor
   keeps a list that survives the next run of the same source -- so a
   hand-placed file at such a path is adopted too; any other hand-placed file
   never moves.
2. PLAN_COMPLETE: the caller lists every shrink-direction fallback that fired
   (`plan_gaps`); any one makes the run REPORT-ONLY.
3. A stale base moves only when its recorded source is gone (removed, disabled,
   excluded) or that source gave a POSITIVE reason to drop the base
   (`POSITIVE_REASONS`). A source that ran and simply resolved nothing HOLDS
   its bases.
4. Only on a full `auto` run with the merge on; the whole base (bare/_0/_1
   `.nif`, `.tri`, `.xml`) moves or none of it, to `_superseded\<run stamp>\`
   keeping relative paths; a brake refuses a large share; and the move is
   TRANSACTIONAL with the merge -- if the new Combined is not written (or names
   a moved mesh) every file goes back, because an old Combined pointing at a
   moved `!UBE` mesh is a missing-mesh crash. A merge that falls back to the
   per-source patches puts every file back BEFORE it lists them.
5. A per-source patch that stays on disk can be merged by a later run's
   fallback: a base moves only when no per-source patch this run leaves in
   place names it, or is recorded for its source (`hold_for_staying_patches`).
   A source's bases and its old patch set move together or not at all.
6. ISOLATED: an unreadable or malformed manifest is no manifest; any error in
   the sweep puts back what it moved and the run goes on as without it.

Nothing is ever deleted. CBBE2UBE_NO_STALE_OUTPUT_SWEEP=1: no manifest, no
report, no move. CBBE2UBE_STALE_OUTPUT_SWEEP_REPORT_ONLY=1: report, never move.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .envflags import flag as _flag

MANIFEST_NAME = "_conversion_manifest.json"
MANIFEST_FORMAT = 1
SUPERSEDED_DIR = "_superseded"
#: The last evaluation, overwritten every full run (report-only or not).
REPORT_NAME = "stale_output_report.json"
#: One per run that moved something, inside its stamp folder.
JOURNAL_NAME = "stale_output.json"
MESH_EXTS = (".nif", ".tri", ".xml")
#: The manifest's name for the vanilla sweep source (it has no mod folder).
VANILLA = "vanilla"
#: A drop reason the planner states about a base, not an absence. A base whose
#: source ran moves only with one of these.
POSITIVE_REASONS = ("third-party covered", "non-playable", "female-only",
                    "built twin")
#: The brake: a run may move at most this share of the output's bases (and
#: always up to BRAKE_FLOOR); more means report-only. Legitimate today: 37 of
#: 1,973 (1.9%).
BRAKE_SHARE = 0.025
BRAKE_FLOOR = 10


def sweep_on() -> bool:
    r"""#stale-output-sweep: manifest, report and moves. On by default.
    CBBE2UBE_NO_STALE_OUTPUT_SWEEP=1 turns all three off."""
    return not _flag("CBBE2UBE_NO_STALE_OUTPUT_SWEEP", False)


def report_only_forced() -> bool:
    """CBBE2UBE_STALE_OUTPUT_SWEEP_REPORT_ONLY=1: list, never move."""
    return _flag("CBBE2UBE_STALE_OUTPUT_SWEEP_REPORT_ONLY", False)


def recover_every_run() -> bool:
    r"""#sweep-recover-every-run: a journal a killed run left unsettled is put
    back at the start of EVERY `auto` run (a Select-mods run, `--plugins-only`,
    the merge off, the sweep off), not only by the next full run's sweep, and
    the manifest keeps the record of a base whose files are still in a stamp
    folder. CBBE2UBE_NO_SWEEP_RECOVER_EVERY_RUN=1: the full run's sweep alone
    puts them back, and a run before it drops their record."""
    return not _flag("CBBE2UBE_NO_SWEEP_RECOVER_EVERY_RUN", False)


def base_key(rel: str) -> str:
    r"""The weight base of a file below `meshes\!UBE`: the key the planner
    claims (`_weight_base_key`), with a `.tri` or `.xml` mapped to its mesh's.
    The converter names them after the mesh's stem without `_0`/`_1`, so
    `x.tri` goes with `x_1.nif` and `x.nif.tri` with `x.nif_1.nif`: the stem
    is read back as the mesh `<stem>.nif`."""
    from .auto_convert import _weight_base_key
    s = rel.replace("\\", "/")
    if s.lower().endswith((".tri", ".xml")):
        s = s[:-4] + ".nif"
    return _weight_base_key(s)


def _inside(real_root: str, p) -> bool:
    try:
        real = os.path.realpath(p)
        return os.path.commonpath([real_root, real]) == real_root
    except (OSError, ValueError):
        return False


@dataclass
class Inventory:
    r"""What `meshes\!UBE` of the output holds, by weight base."""
    bases: dict = field(default_factory=dict)    # base -> [Path, ...]
    unknown: list = field(default_factory=list)  # rel of files we never write
    unsafe: set = field(default_factory=set)     # bases with a file outside the output


def inventory(output) -> Inventory:
    r"""Walk `<output>\meshes\!UBE`. A file whose real path leaves the output
    (a link or junction) marks its base unsafe; a file of another type is
    listed and never touched."""
    inv = Inventory()
    root = Path(output)
    ube = root / "meshes" / "!UBE"
    if not ube.is_dir():
        return inv
    real_root = os.path.realpath(root)
    for dp, _dns, fns in os.walk(ube):
        for fn in sorted(fns):
            p = Path(dp) / fn
            rel = p.relative_to(ube).as_posix()
            if p.suffix.lower() not in MESH_EXTS:
                inv.unknown.append(rel)
                continue
            b = base_key(rel)
            if os.path.islink(p) or not _inside(real_root, p):
                inv.unsafe.add(b)
            inv.bases.setdefault(b, []).append(p)
    return inv


# ---------------------------------------------------------------- manifest

def _name_map(v) -> bool:
    """{str: str}: every key a path or file name, every value a source name --
    a mod folder name: not empty, and one folder, never a path (an empty name
    joined to the mods root is the mods root itself, which reads as a present
    folder of a disabled mod)."""
    return isinstance(v, dict) and all(
        isinstance(k, str) and k.strip() and isinstance(s, str) and _mod_name(s)
        for k, s in v.items())


def _mod_name(s: str) -> bool:
    return bool(s.strip()) and s not in (".", "..") and not any(c in s for c in "/\\:")


def read_manifest(output) -> "tuple[dict | None, str]":
    """(manifest, problem). None and "" when there is none yet; None and the
    reason when it cannot be read or holds a value that is not a name (a hand
    edit, a foreign write) -- either way nothing it would have recorded can
    move, and the run's finish writes it anew."""
    p = Path(output) / MANIFEST_NAME
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return None, ""
    except OSError as e:
        return None, f"cannot be read ({type(e).__name__})"
    try:
        d = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None, "is not valid JSON"
    if (not isinstance(d, dict) or d.get("format") != MANIFEST_FORMAT
            or not isinstance(d.get("bases"), dict)
            or not isinstance(d.get("patches", {}), dict)):
        return None, "has an unknown layout"
    if not _name_map(d["bases"]) or not _name_map(d.get("patches", {})):
        return None, "has an entry that is not a mod name"
    return d, ""


def build_manifest(prev, claims, patches, on_disk, patch_files, adopted,
                   run_stamp, build, stranded=None) -> dict:
    """This run's record. `claims` {base: source} and `patches` {name: source}
    are what this run made; an earlier entry is carried while its file is still
    on disk (`on_disk` bases, `patch_files` names) and nothing this run claims
    it -- a held base keeps its source, a moved one drops out. `adopted` {base:
    source}: a base no run recorded that a source this run gave a positive
    reason to drop; it is recorded now and can move on a later run.
    `stranded` (bases, lower-case patch names), from `stranded_files`: files a
    move no run settled left in a stamp folder -- their earlier entry is carried
    too, so the file that comes back is still ours. #sweep-recover-every-run"""
    bases: dict = {}
    pats: dict = {}
    s_bases, s_patches = stranded or (set(), set())
    if prev:
        for b, s in prev.get("bases", {}).items():
            if b in on_disk and b not in claims:
                bases[b] = s
            elif b in s_bases and b not in claims:
                bases[b] = s
        for n, s in prev.get("patches", {}).items():
            if n.lower() in patch_files and n not in patches:
                pats[n] = s
            elif n.lower() in s_patches and n not in patches:
                pats[n] = s
    for b, s in adopted.items():
        if b in on_disk and b not in claims:
            bases.setdefault(b, s)
    bases.update(claims)
    pats.update(patches)
    return {"format": MANIFEST_FORMAT, "run_stamp": run_stamp, "build": build,
            "bases": dict(sorted(bases.items())),
            "patches": dict(sorted(pats.items()))}


def write_manifest(output, manifest: dict) -> None:
    from .atomic_io import atomic_write_bytes
    atomic_write_bytes(Path(output) / MANIFEST_NAME,
                       json.dumps(manifest, indent=1).encode("utf-8"))


# ---------------------------------------------------------------- decisions

@dataclass
class Decision:
    key: str          # weight base, or a patch file name
    action: str       # "move" | "hold" | "adopt"
    source: "str | None"
    reason: str


def _source_state(status, s: str) -> str:
    try:
        return status(s)
    except Exception:
        return "unknown"


def decide_bases(stale, recorded, ran, status, classify) -> "list[Decision]":
    """One decision per stale base.

    `recorded` {base: source} is the manifest; `ran` {source: {base: positive
    reason}} holds every source of this run; `status(source)` says where a
    source that did not run went ("removed", "disabled", "excluded", "not
    selected", "vanilla", "unknown"); `classify(source)` returns {base:
    positive reason} for a source that is present and enabled but was not
    selected, or None when it cannot tell."""
    out: "list[Decision]" = []
    cls_cache: dict = {}
    for b in sorted(stale):
        s = recorded.get(b)
        if s is None:
            hit = next(((src, why[b]) for src, why in sorted(ran.items())
                        if why.get(b) in POSITIVE_REASONS), None)
            if hit is not None:
                out.append(Decision(b, "adopt", hit[0],
                                    f"not recorded by an earlier run; {hit[1]} "
                                    "this run, so it is recorded now"))
            else:
                out.append(Decision(b, "hold", None,
                                    "not recorded as converted by any run"))
            continue
        if s in ran:
            why = ran[s].get(b)
            if why in POSITIVE_REASONS:
                out.append(Decision(b, "move", s, why))
            else:
                out.append(Decision(b, "hold", s,
                                    "its source ran and gave no reason to drop it"))
            continue
        st = _source_state(status, s)
        if st in ("removed", "disabled", "excluded"):
            out.append(Decision(b, "move", s, f"source mod {st}"))
            continue
        if st == "not selected":
            if s not in cls_cache:
                try:
                    cls_cache[s] = classify(s)
                except Exception:
                    cls_cache[s] = None
            why = (cls_cache[s] or {}).get(b)
            if why in POSITIVE_REASONS:
                out.append(Decision(b, "move", s, f"{why} (source not selected)"))
            else:
                out.append(Decision(b, "hold", s,
                                    "its source was not selected and gave no "
                                    "reason to drop it"))
            continue
        out.append(Decision(b, "hold", s, f"its source did not run ({st})"))
    return out


def decide_patches(stale, recorded, ran, status, base_moves, claims_by_source,
                   recorded_bases) -> "list[Decision]":
    """One decision per stale per-source patch set (a patch this run did not
    write). It moves when its recorded source is gone, or when that source was
    not selected, claims nothing this run and every base recorded for it moves
    now. Otherwise it stays and is listed."""
    moving = {d.key for d in base_moves if d.action == "move"}
    out: "list[Decision]" = []
    for n in sorted(stale):
        s = recorded.get(n)
        if s is None:
            out.append(Decision(n, "hold", None, "not recorded as written by any run"))
            continue
        if s in ran:
            out.append(Decision(n, "hold", s, "its source ran but did not write it"))
            continue
        st = _source_state(status, s)
        if st in ("removed", "disabled", "excluded"):
            out.append(Decision(n, "move", s, f"source mod {st}"))
            continue
        mine = [b for b, src in recorded_bases.items() if src == s]
        if (st == "not selected" and not claims_by_source.get(s)
                and all(b in moving for b in mine)):
            out.append(Decision(n, "move", s,
                                "its source was not selected and all its meshes move"))
            continue
        out.append(Decision(n, "hold", s, f"its source did not run ({st})"))
    return out


def hold_for_staying_patches(base_dec, patch_dec, names_of) -> bool:
    r"""Hold every base move a per-source patch this run leaves in place could
    still reach. Such a patch is merged by a later run whose coverage fails
    (the per-source fallback), so an armature of it naming a moved `!UBE` mesh
    would be a missing-mesh crash then -- in a run that has no record of the
    move to put back. A base move is held when a staying patch set:

    - is recorded for the base's source (a source's bases and its old patch set
      move together or not at all -- the patch was written from an older plan
      and may name any base of its source), or
    - names the base in an armature (`names_of(patch name)` -> the weight bases
      its armatures name, or None when it cannot be read: then every move is
      held, since an unread patch may name any of them).

    Mutates the decisions; returns whether any changed (a patch that moved
    because all its source's bases move may have to stay now -- decide again
    until nothing changes). #stale-output-sweep"""
    staying = [d for d in patch_dec if d.action != "move"]
    if not staying:
        return False
    by_source = {d.source: d.key for d in staying if d.source}
    named: dict = {}
    unread = ""
    for d in staying:
        got = names_of(d.key)
        if got is None:
            unread = unread or d.key
            continue
        for b in got:
            named.setdefault(b, d.key)
    changed = False
    for d in base_dec:
        if d.action != "move":
            continue
        if d.source in by_source:
            why = (f"the per-source patch {by_source[d.source]} of its source stays, "
                   "so its meshes stay with it")
        elif d.key in named:
            why = f"the per-source patch {named[d.key]} left in place names it"
        elif unread:
            why = f"the per-source patch {unread} left in place could not be read"
        else:
            continue
        d.action, d.reason = "hold", why
        changed = True
    return changed


def patch_bases(esp_path) -> "set[str] | None":
    r"""The weight bases the armatures of one plugin name below `!UBE`; None
    when it cannot be read."""
    try:
        return {b for _m, b in _ube_models(esp_path)}
    except Exception:
        return None


def brake_limit(total_bases: int) -> int:
    """The most bases one run may move."""
    return max(BRAKE_FLOOR, int(total_bases * BRAKE_SHARE))


# ---------------------------------------------------------------- moves

@dataclass
class Handle:
    """Moves waiting for the merge to confirm them."""
    output: Path
    stamp_dir: Path
    journal: Path
    moved: list = field(default_factory=list)   # [(key, kind, [(src, dst), ...])]
    #: Every (src, dst) the journal lists, moved or not: the stamp folder is new
    #: this run, so a file at a dst is one this run moved -- the put-back after
    #: an error mid-move finds it even when `moved` does not list it yet.
    planned: list = field(default_factory=list)

    @property
    def moved_bases(self) -> "set[str]":
        return {k for k, kind, _f in self.moved if kind == "mesh"}

    def pairs(self) -> list:
        return [pr for _k, _kind, fs in self.moved for pr in fs]


#: The handle of this process's run, between the sweep and the merge.
_PENDING: "Handle | None" = None


def pending() -> "Handle | None":
    return _PENDING


def _set_pending(h: "Handle | None") -> None:
    global _PENDING
    _PENDING = h


def run_stamp(started: float) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(started))


def new_stamp_dir(output, stamp: str) -> Path:
    """`<output>\\_superseded\\<stamp>`, never one that exists: `-2`, `-3`..."""
    base = Path(output) / SUPERSEDED_DIR
    d = base / stamp
    n = 2
    while d.exists():
        d = base / f"{stamp}-{n}"
        n += 1
    return d


def move_group(files, output, stamp_dir) -> "tuple[list, str, list]":
    """Move `files` (under `output`) to the same relative paths under
    `stamp_dir`, all or nothing: on the first failure the files already moved
    go back. Returns (moved pairs, error or "", names that could not go back).
    A destination that exists is a failure, never overwritten. Any other error
    also puts back what moved, then propagates."""
    root = Path(output)
    done: list = []
    try:
        for f in files:
            f = Path(f)
            if not f.is_file():
                continue
            to = Path(stamp_dir) / f.relative_to(root)
            if to.exists():
                raise FileExistsError(f"{to.name} already in {to.parent}")
            to.parent.mkdir(parents=True, exist_ok=True)
            os.replace(f, to)
            done.append((f, to))
    except Exception as e:
        torn = []
        for f, to in reversed(done):
            try:
                os.replace(to, f)
            except Exception:
                torn.append(f.name)
        if not isinstance(e, (OSError, ValueError)):
            raise
        msg = str(e).strip()
        return [], (f"{type(e).__name__}: {msg}" if msg else type(e).__name__), sorted(torn)
    return done, "", []


def put_back(pairs) -> "list[str]":
    """Move each (original, moved) pair back. A file whose original path is
    taken again, or that cannot move, stays where it is and is named -- once,
    though the same pair may be listed twice (moved and planned)."""
    failed: list = []
    for f, to in reversed(list(dict.fromkeys((str(f), str(t)) for f, t in pairs))):
        f, to = Path(f), Path(to)
        try:
            if not to.is_file():
                continue
            if f.exists():
                raise FileExistsError(f.name)
            f.parent.mkdir(parents=True, exist_ok=True)
            os.replace(to, f)
        except Exception:
            failed.append(str(f))
    return failed


def write_json(path, data) -> None:
    from .atomic_io import atomic_write_bytes
    atomic_write_bytes(Path(path), json.dumps(data, indent=1).encode("utf-8"))


def update_journal(path, **fields) -> None:
    """Set `fields` in a journal; best effort (the first write listed the files)."""
    try:
        d = json.loads(Path(path).read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        d = {}
    d.update(fields)
    try:
        write_json(path, d)
    except OSError:
        pass


#: A journal in one of these states belongs to a run that never settled.
_UNSETTLED = ("moving", "waiting for the merge")


def recover_interrupted(output) -> "list[tuple[str, list[str]]]":
    """Put back the files of every journal a run left unsettled (it died
    between its moves and its merge), so the Combined it left behind finds its
    meshes again. Returns [(stamp folder, files that could not go back)]."""
    out: list = []
    root = Path(output) / SUPERSEDED_DIR
    try:
        journals = sorted(root.glob("*/" + JOURNAL_NAME))
    except OSError:
        return out
    mine = _PENDING.journal if _PENDING is not None else None
    for j in journals:
        if mine is not None and j == mine:
            continue
        try:
            d = json.loads(j.read_bytes().decode("utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or d.get("status") not in _UNSETTLED:
            continue
        pairs = [(Path(output) / rel, j.parent / rel)
                 for rel in d.get("planned", []) if isinstance(rel, str)]
        failed = put_back(pairs)
        update_journal(j, status=("partly put back after an interrupted run"
                                  if failed else "put back after an interrupted run"))
        out.append((j.parent.name, failed))
    return out


def _stranded(status) -> bool:
    """A journal whose files may still sit in its stamp folder though no run
    decided they should: unsettled, or a put-back that left some behind."""
    return isinstance(status, str) and (
        status in _UNSETTLED or status.startswith("partly put back"))


def stranded_files(output) -> "tuple[set[str], set[str]]":
    r"""(weight bases, lower-case patch file names) of the files a journal that
    no run settled -- or whose put-back left some behind -- lists, and that
    still sit in its stamp folder. The manifest keeps their record: a file that
    comes back later must still read as ours, or it can never move again.
    #sweep-recover-every-run"""
    bases: set = set()
    patches: set = set()
    root = Path(output) / SUPERSEDED_DIR
    try:
        journals = sorted(root.glob("*/" + JOURNAL_NAME))
    except OSError:
        return bases, patches
    for j in journals:
        try:
            d = json.loads(j.read_bytes().decode("utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or not _stranded(d.get("status")):
            continue
        for rel in d.get("planned", []):
            if not isinstance(rel, str) or not (j.parent / rel).is_file():
                continue
            s = rel.replace("\\", "/")
            if s.lower().startswith("meshes/!ube/"):
                bases.add(base_key(s[len("meshes/!ube/"):]))
            else:
                patches.add(Path(s).name.lower())
    return bases, patches


def combined_references(combined_path, bases) -> "list[str]":
    r"""The `!UBE` model paths in the written Combined (and its split pieces)
    whose weight base is in `bases` -- a moved mesh the plugin still names.
    Raises when a piece cannot be read: an unread plugin is not a clean one."""
    combined_path = Path(combined_path)
    pieces = sorted(combined_path.parent.glob(combined_path.stem + "*.esp"))
    hits: list = []
    for piece in pieces:
        for m, b in _ube_models(piece):
            if b in bases:
                hits.append(f"{piece.name}: {m}")
    return hits


def _ube_models(esp_path) -> "list[tuple[str, str]]":
    r"""[(model path, weight base)] for every `!UBE` model an armature of the
    plugin names (MOD2-MOD5). Raises when the plugin cannot be read."""
    from . import esp
    out: list = []
    e = esp.ESP.load(Path(esp_path))
    for g in e.groups:
        if g.label != b"ARMA":
            continue
        for r in g.records:
            for sig, d in esp.iter_subrecords(r.payload):
                if sig not in (b"MOD2", b"MOD3", b"MOD4", b"MOD5"):
                    continue
                m = d.rstrip(b"\x00").decode("cp1252", "replace")
                s = m.replace("\\", "/").lstrip("/")
                if s.lower().startswith("meshes/"):
                    s = s[len("meshes/"):]
                if not s.lower().startswith("!ube/"):
                    continue
                out.append((m, base_key(s[len("!ube/"):])))
    return out
