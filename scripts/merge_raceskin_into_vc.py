"""Fold UBE_RaceSkin_Patch.esp's records INTO the already-deployed
Vanilla_UBE_Race_Compat.esp (so there is no separate plugin to enable).

Remaps every FormID from the standalone patch's master order into VC's,
reassigns the new ARMA own-FormIDs into VC's free FE-space, appends the
54 skin ARMAs + the 00UBE_SkinNaked override, then deletes the standalone.
"""
import os
import io, sys, struct
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import esp

PATCH = Path(os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\CBBEtoUBE Auto\UBE_RaceSkin_Patch.esp")
VC = Path(os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\CBBEtoUBE Auto\Vanilla_UBE_Race_Compat.esp")
FORMID_SIGS = {b"RNAM", b"MODL", b"SNDD", b"ZNAM", b"YNAM", b"ETYP", b"BIDS", b"BAMT"}

def main():
    p = esp.ESP.load(PATCH)
    vc = esp.ESP.load(VC)
    pm = [m.lower() for m in p.header.masters]      # patch masters
    vm = [m.lower() for m in vc.header.masters]      # VC masters
    p_own = len(p.header.masters)                    # 4
    vc_own = len(vc.header.masters)                  # 6
    print(f"patch masters: {p.header.masters} (own={p_own})")
    print(f"VC    masters: {vc.header.masters} (own={vc_own})")

    # top-byte remap patch-space -> VC-space (by master NAME). own handled separately.
    top_remap = {}
    for i, name in enumerate(pm):
        if name in vm:
            top_remap[i] = vm.index(name)
        else:
            top_remap[i] = None     # master missing in VC (must not be referenced)
    print(f"top-byte remap (patch->VC): {top_remap}  (own {p_own}->{vc_own})")

    p_arma = next(g for g in p.groups if g.label == b"ARMA")
    p_armo = next(g for g in p.groups if g.label == b"ARMO")
    vc_arma = next(g for g in vc.groups if g.label == b"ARMA")
    vc_armo = next(g for g in vc.groups if g.label == b"ARMO")

    # allocate new own FE-space FIDs in VC for the patch's own ARMAs
    max_low = 0
    for g in vc.groups:
        for r in g.records:
            if (r.formid >> 24) == vc_own:
                max_low = max(max_low, r.formid & 0xFFFFFF)
    next_low = max_low + 1
    own_map = {}                                     # patch own fid -> VC own fid
    for r in p_arma.records:
        if (r.formid >> 24) == p_own:
            own_map[r.formid] = (vc_own << 24) | next_low
            next_low += 1
    print(f"VC own FE-space: existing max 0x{max_low:X}; "
          f"new ARMAs 0x{(own_map and min(v&0xFFFFFF for v in own_map.values())):X}"
          f"..0x{next_low-1:X} ({len(own_map)} new)")

    bad = []
    def remap_fid(fid):
        top = fid >> 24; low = fid & 0xFFFFFF
        if top == p_own:
            return own_map[fid]
        if top in top_remap:
            nt = top_remap[top]
            if nt is None:
                bad.append(fid)
                return fid
            return (nt << 24) | low
        bad.append(fid); return fid

    def remap_payload(payload):
        out = b""
        for sig, d in esp.iter_subrecords(payload):
            if sig in FORMID_SIGS and len(d) == 4:
                d = struct.pack("<I", remap_fid(struct.unpack("<I", d)[0]))
            out += esp.encode_subrecord(sig, d)
        return out

    # remap + append ARMAs
    new_armas = []
    for r in p_arma.records:
        new_armas.append(esp.Record(
            sig=b"ARMA", flags=0, formid=remap_fid(r.formid),
            timestamp_vc=0, version_unk=0x002C, payload=remap_payload(r.payload)))
    # remap + append SkinNaked override
    skin = p_armo.records[0]
    skin_ovr = esp.Record(
        sig=b"ARMO", flags=0, formid=remap_fid(skin.formid),
        timestamp_vc=0, version_unk=0x002C, payload=remap_payload(skin.payload))

    if bad:
        print(f"\nFATAL: {len(bad)} refs to a master VC lacks "
              f"(e.g. 0x{bad[0]:08x}); aborting. (RaceCompat would need adding.)")
        return

    vc_arma.records.extend(new_armas)
    vc_armo.records.append(skin_ovr)
    if next_low > (vc.header.next_object_id & 0xFFFFFF):
        vc.header.next_object_id = next_low

    bak = VC.with_name(VC.stem + ".preraceskin.bak")
    if not bak.exists():
        import shutil; shutil.copy2(VC, bak); print(f"backed up VC -> {bak.name}")
    vc.save(VC)
    print(f"\nFOLDED into {VC.name}: +{len(new_armas)} ARMAs, +1 SkinNaked override")

    # delete standalone
    if PATCH.exists():
        PATCH.unlink(); print(f"removed standalone {PATCH.name}")

if __name__ == "__main__":
    main()
