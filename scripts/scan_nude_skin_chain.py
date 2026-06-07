"""COMPLETE scan: what governs nude hands/feet/body rendering on a UBE race.

Traces, on the live load order:
  RACE.WNAM (skin) -> ARMO (SkinNaked) -> armatures list -> per ARMA
  (RNAM primary race, BOD2 slots, MOD2/MOD3 mesh) -> on-disk mesh.

The engine selects an ARMA for an actor by RNAM PRIMARY race + biped
slot. If a UBE race has no ARMA whose RNAM primary == that race for a
given slot, the actor falls back to its vanilla SkinForm -> CBBE mesh.

Reports per-UBE-race coverage for slots 32 (body) / 33 (hands) /
37 (feet) and flags every gap + every non-!UBE mesh target.
"""
from __future__ import annotations
import io, struct, sys, glob
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import esp  # noqa: E402

ARR_MODS = Path(r"<MODLIST>\mods")
ARR_PROFILES = Path(r"<MODLIST>\profiles")

# biped slot -> bit (slot-30) in BOD2 first u32
SLOTS = {32: "BODY", 33: "HANDS", 37: "FEET"}


def edid_of(rec) -> str:
    for sig, d in esp.iter_subrecords(rec.payload):
        if sig == b"EDID":
            return d.rstrip(b"\x00").decode("ascii", "replace")
    return ""


def arma_info(rec):
    """Return dict: edid, rnam(int|None), slots(set[int]), meshes(list)."""
    edid = ""
    rnam = None
    slots: set[int] = set()
    meshes: list[str] = []
    addl_races: list[int] = []
    seen_first_modl = False
    for sig, d in esp.iter_subrecords(rec.payload):
        if sig == b"EDID":
            edid = d.rstrip(b"\x00").decode("ascii", "replace")
        elif sig == b"RNAM" and len(d) == 4:
            rnam = struct.unpack("<I", d)[0]
        elif sig in (b"BOD2", b"BODT") and len(d) >= 4:
            bits = struct.unpack_from("<I", d, 0)[0]
            for s in range(30, 62):
                if bits & (1 << (s - 30)):
                    slots.add(s)
        elif sig in (b"MOD2", b"MOD3"):
            try:
                meshes.append(d.rstrip(b"\x00").decode("ascii", "replace"))
            except Exception:
                meshes.append(repr(d))
        elif sig == b"MODL" and len(d) == 4:
            addl_races.append(struct.unpack("<I", d)[0])
    return dict(edid=edid, rnam=rnam, slots=slots, meshes=meshes, addl=addl_races)


def load_modlist():
    for prof in ("Authoria - Requiem Reforged", "Authoria"):
        ml = ARR_PROFILES / prof / "modlist.txt"
        if ml.is_file():
            out = []
            for ln in ml.read_text(encoding="utf-8", errors="replace").splitlines():
                if ln.startswith("+"):
                    out.append(ln[1:].strip())
            if out:
                return prof, out
    # any profile
    for d in sorted(ARR_PROFILES.iterdir()):
        ml = d / "modlist.txt"
        if ml.is_file():
            out = [ln[1:].strip() for ln in ml.read_text(encoding="utf-8", errors="replace").splitlines() if ln.startswith("+")]
            if out:
                return d.name, out
    return None, []


def find_winning(basename: str, ordered: list[str]):
    """Highest-priority enabled mod providing basename; else any on disk."""
    # ordered = highest priority first
    rank = {name: i for i, name in enumerate(ordered)}
    cands = []
    for p in glob.glob(str(ARR_MODS / "*" / basename)):
        mod = Path(p).parent.name
        cands.append((rank.get(mod, 10**9), mod, p))
    # also stock game data
    for stock in (r"<MODLIST>\Stock Game\Data", r"<MODLIST>\mods\Stock Game\Data"):
        sp = Path(stock) / basename
        if sp.is_file():
            cands.append((10**9 + 1, "<stock>", str(sp)))
    if not cands:
        return None, []
    cands.sort(key=lambda t: t[0])
    return cands[0], cands


def main():
    prof, ordered = load_modlist()
    print(f"profile: {prof}  ({len(ordered)} enabled mods)\n")

    win, all_cands = find_winning("UBE_AllRace.esp", ordered)
    print(f"UBE_AllRace.esp providers on disk: {len(all_cands)}")
    for rk, mod, p in all_cands[:8]:
        print(f"   rank={rk if rk<10**9 else 'NOT-ENABLED'}  {mod}")
    if win is None:
        print("FATAL: no UBE_AllRace.esp found"); return
    print(f"\n=> WINNING UBE_AllRace.esp: {win[1]}  ({win[2]})\n")

    e = esp.ESP.load(win[2])
    own_top = len(e.header.masters)
    print(f"masters ({own_top}): {e.header.masters}")
    print(f"own-record top byte = 0x{own_top:02x}\n")

    arma_g = next((g for g in e.groups if g.label == b"ARMA"), None)
    armo_g = next((g for g in e.groups if g.label == b"ARMO"), None)
    race_g = next((g for g in e.groups if g.label == b"RACE"), None)

    # own RACE map: low24 -> edid
    race_edid = {}
    if race_g:
        for r in race_g.records:
            race_edid[r.formid & 0xFFFFFF] = edid_of(r)
    print(f"RACE records in UBE_AllRace: {len(race_edid)}")

    # own ARMA map: low24 -> info
    arma_by_low = {}
    if arma_g:
        for r in arma_g.records:
            arma_by_low[r.formid & 0xFFFFFF] = (r.formid, arma_info(r))
    print(f"ARMA records in UBE_AllRace: {len(arma_by_low)}")

    # find SkinNaked
    skin = None
    if armo_g:
        for r in armo_g.records:
            if edid_of(r) == "00UBE_SkinNaked":
                skin = r; break
    if skin is None:
        print("FATAL: 00UBE_SkinNaked ARMO not found in winning file "
              "(may be defined in a master / overridden elsewhere)")
        # still try: scan all ARMAs for slot 33/37 coverage below
    else:
        # SkinNaked armatures = MODL 4-byte FID list
        arm_fids = [struct.unpack("<I", d)[0]
                    for sig, d in esp.iter_subrecords(skin.payload)
                    if sig == b"MODL" and len(d) == 4]
        print(f"\n00UBE_SkinNaked: 0x{skin.formid:08x}  "
              f"armatures listed: {len(arm_fids)}\n")

        # Resolve each armature -> per slot/race coverage
        # coverage[slot][race_low24] = list of (edid, mesh, is_ube_mesh)
        coverage = {32: {}, 33: {}, 37: {}}
        non_own = 0
        for fid in arm_fids:
            top = (fid >> 24) & 0xFF
            low = fid & 0xFFFFFF
            if top != own_top or low not in arma_by_low:
                non_own += 1
                continue
            _, info = arma_by_low[low]
            rn = info["rnam"]
            rn_low = (rn & 0xFFFFFF) if rn is not None else None
            for s in (32, 33, 37):
                if s in info["slots"]:
                    mesh = next((m for m in info["meshes"] if m), "")
                    is_ube = mesh.lower().replace("/", "\\").startswith("!ube") or "\\!ube\\" in ("\\"+mesh.lower().replace("/","\\"))
                    coverage[s].setdefault(rn_low, []).append(
                        (info["edid"], mesh, is_ube, rn))
        print(f"(armatures pointing outside this file / unresolved: {non_own})\n")

        # Report per slot
        for s in (32, 33, 37):
            print(f"===== SLOT {s} ({SLOTS[s]}) =====")
            cov = coverage[s]
            if not cov:
                print("  NO armatures for this slot in SkinNaked!\n")
                continue
            for rn_low, entries in sorted(cov.items(), key=lambda kv: (kv[0] is None, kv[0])):
                rlabel = race_edid.get(rn_low, f"0x{rn_low:06x}" if rn_low is not None else "??")
                for edid, mesh, is_ube, rn in entries:
                    flag = "OK-UBE" if is_ube else ">>> NON-UBE (CBBE fallback mesh!)"
                    print(f"  race={rlabel:28s} arma={edid:30s} {flag}")
                    print(f"        mesh={mesh}")
            print()

        # Coverage matrix: which UBE races have body/hands/feet
        print("===== PER-RACE COVERAGE MATRIX (UBE races) =====")
        all_races = set()
        for s in (32, 33, 37):
            all_races |= set(coverage[s].keys())
        all_races.discard(None)
        hdr = f"{'race':30s} {'BODY(32)':10s} {'HANDS(33)':10s} {'FEET(37)':10s}"
        print(hdr)
        for rn_low in sorted(all_races):
            rlabel = race_edid.get(rn_low, f"0x{rn_low:06x}")
            cells = []
            for s in (32, 33, 37):
                ents = coverage[s].get(rn_low, [])
                if not ents:
                    cells.append("--MISSING")
                elif all(e[2] for e in ents):
                    cells.append("UBE")
                else:
                    cells.append("CBBE!!")
            print(f"{rlabel:30s} {cells[0]:10s} {cells[1]:10s} {cells[2]:10s}")

    # Did integrate_ube_race_skins run? look for per-race suffixed EDIDs
    print("\n===== per-race skin ARMA EDIDs present? =====")
    for base in ("00UBE_NakedTorso", "00UBE_NakedHands", "00UBE_NakedFeet"):
        variants = [v[1]["edid"] for v in arma_by_low.values()
                    if v[1]["edid"] == base or v[1]["edid"].startswith(base + "_")]
        print(f"  {base}: {len(variants)} variant(s) -> {sorted(variants)[:6]}{'...' if len(variants)>6 else ''}")

    # Does our Vanilla_UBE_Race_Compat override SkinNaked or add competing skin ARMAs?
    print("\n===== Vanilla_UBE_Race_Compat.esp interaction =====")
    vc, _ = find_winning("Vanilla_UBE_Race_Compat.esp", ordered)
    if vc is None:
        print("  not found on disk")
    else:
        ve = esp.ESP.load(vc[2])
        v_armo = next((g for g in ve.groups if g.label == b"ARMO"), None)
        v_arma = next((g for g in ve.groups if g.label == b"ARMA"), None)
        overrides_skin = False
        if v_armo:
            for r in v_armo.records:
                if edid_of(r) == "00UBE_SkinNaked":
                    overrides_skin = True
        nude_arma = 0
        if v_arma:
            for r in v_arma.records:
                info = arma_info(r)
                if (info["slots"] & {33, 37}):
                    mesh = next((m for m in info["meshes"] if m), "").lower()
                    if mesh and not (mesh.startswith("!ube") or "\\!ube\\" in "\\"+mesh.replace("/","\\")):
                        nude_arma += 1
        print(f"  overrides 00UBE_SkinNaked: {overrides_skin}")
        print(f"  hands/feet ARMAs w/ non-!UBE mesh (CBBE/beast competitors): {nude_arma}")


if __name__ == "__main__":
    main()
