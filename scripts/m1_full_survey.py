"""Comprehensive M1 survey across multiple UBE/CBBE pairs.

Looks at ALL matched NIFs (not just first 3) and inspects ESP patches.
Output: structured summary of variance to update docs/M1_findings.md.
"""
import os, sys, struct, zlib, json
from pathlib import Path
from collections import Counter, defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.pynifly'))
from pyn import pynifly

PROJ    = Path(__file__).resolve().parents[1]
SAMPLES = PROJ / 'samples' / 'm1'

# Body shapes we expect to find in CBBE armor (inline body)
BODY_SHAPE_NAMES = {'3BA', '3BA_Anus', '3BA_Vagina', 'Panty', 'CL'}
UBE_BODY_NAMES   = {'BaseShape', 'VirtualBody'}


def find_nifs(root):
    return [p for p in root.rglob('*.nif') if 'calientetools' not in str(p).lower()]


def summarize_nif(path):
    try:
        nf = pynifly.NifFile(str(path))
    except Exception as e:
        return None
    out = []
    for s in nf.shapes:
        bones = list(getattr(s, 'bone_names', []) or [])
        out.append({
            'name': s.name,
            'block_type': s.__class__.__name__,
            'verts': len(s.verts),
            'tris': len(s.tris),
            'bones': bones,
            'bone_count': len(bones),
        })
    return out


def get_payload(rec_data, flags):
    if flags & 0x00040000: return zlib.decompress(rec_data[4:])
    return rec_data


def subrecords(payload):
    p = 0
    while p < len(payload):
        sig = payload[p:p+4]
        size = struct.unpack_from('<H', payload, p+4)[0]
        p += 6
        yield sig, payload[p:p+size]
        p += size


def survey_esp(path):
    """Return a dict summarizing the ESP's TES4 masters + record counts +
    ARMA-record patterns (primary race, additional race count, model paths).
    """
    with open(path, 'rb') as f:
        data = f.read()
    if data[:4] != b'TES4':
        return None
    tes4_size = struct.unpack_from('<I', data, 4)[0]
    tp = data[24:24+tes4_size]
    masters = []
    for sr, sd in subrecords(tp):
        if sr == b'MAST':
            masters.append(sd.rstrip(b'\x00').decode('ascii', errors='ignore'))

    pos = 24 + tes4_size
    grup_counts = Counter()
    arma_info = []
    while pos < len(data):
        if data[pos:pos+4] != b'GRUP': break
        grup_size = struct.unpack_from('<I', data, pos+4)[0]
        label = data[pos+8:pos+12].decode('ascii', errors='ignore')
        inner = pos + 24; end = pos + grup_size
        while inner < end:
            sig = data[inner:inner+4]
            size = struct.unpack_from('<I', data, inner+4)[0]
            flags = struct.unpack_from('<I', data, inner+8)[0]
            fid = struct.unpack_from('<I', data, inner+12)[0]
            if sig.decode('ascii', errors='ignore') == label:
                grup_counts[label] += 1
                if sig == b'ARMA':
                    payload = get_payload(data[inner+24:inner+24+size], flags)
                    edid=None; primary=None; addnl=[]; mod3=None
                    for sr, sd in subrecords(payload):
                        if sr == b'EDID':
                            edid = sd.rstrip(b'\x00').decode('ascii', errors='ignore')
                        elif sr == b'RNAM' and len(sd) == 4:
                            primary = struct.unpack_from('<I', sd, 0)[0]
                        elif sr == b'MODL' and len(sd) == 4:
                            addnl.append(struct.unpack_from('<I', sd, 0)[0])
                        elif sr == b'MOD3':
                            mod3 = sd.rstrip(b'\x00').decode('ascii', errors='ignore')
                    arma_info.append({
                        'edid': edid, 'primary_rnam': primary,
                        'additional_count': len(addnl), 'mod3': mod3
                    })
            inner += 24 + size
        pos += grup_size

    return {'masters': masters, 'grup_counts': dict(grup_counts), 'arma': arma_info}


def survey_pair(pair_name):
    pair_dir = SAMPLES / pair_name
    print(f'\n{"="*70}\nPAIR: {pair_name}\n{"="*70}')

    # ESP files
    cbbe_esps = list((pair_dir / 'cbbe').rglob('*.esp')) + list((pair_dir / 'cbbe').rglob('*.esm'))
    ube_esps  = list((pair_dir / 'ube').rglob('*.esp'))  + list((pair_dir / 'ube').rglob('*.esm'))

    for esp in cbbe_esps + ube_esps:
        info = survey_esp(esp)
        if info is None: continue
        print(f'\n  ESP: {esp.name}  (size {esp.stat().st_size} bytes)')
        print(f'    masters: {info["masters"]}')
        print(f'    GRUPs: {info["grup_counts"]}')
        if info["arma"]:
            print(f'    ARMA records:')
            for a in info["arma"][:5]:
                rnam_str = f'{a["primary_rnam"]:#010x}' if a["primary_rnam"] else '<none>'
                print(f'      [{a["edid"]}] primary_rnam={rnam_str} '
                      f'additional={a["additional_count"]}  mod3={a["mod3"]}')
            if len(info["arma"]) > 5:
                print(f'      ... and {len(info["arma"])-5} more')

    # NIFs
    cbbe_nifs = find_nifs(pair_dir / 'cbbe')
    ube_nifs  = find_nifs(pair_dir / 'ube')
    ube_by_name = {p.name.lower(): p for p in ube_nifs}
    matched = [(c, ube_by_name[c.name.lower()]) for c in cbbe_nifs
               if c.name.lower() in ube_by_name]

    print(f'\n  NIFs: CBBE={len(cbbe_nifs)}, UBE={len(ube_nifs)}, matched={len(matched)}')

    # Aggregate patterns across ALL matched NIFs
    has_body_inline_cbbe = 0
    has_body_inline_ube  = 0
    body_swap_cases = 0
    topology_preserved_cases = 0
    bone_changes = defaultdict(int)
    new_ube_bones = Counter()
    removed_cbbe_bones = Counter()

    for c_path, u_path in matched:
        c_shapes = summarize_nif(c_path)
        u_shapes = summarize_nif(u_path)
        if not c_shapes or not u_shapes: continue

        c_names = {s['name'] for s in c_shapes}
        u_names = {s['name'] for s in u_shapes}

        if c_names & BODY_SHAPE_NAMES: has_body_inline_cbbe += 1
        if u_names & UBE_BODY_NAMES:   has_body_inline_ube  += 1
        if (c_names & BODY_SHAPE_NAMES) and (u_names & UBE_BODY_NAMES):
            body_swap_cases += 1

        common = c_names & u_names
        all_preserved = True
        for name in common:
            c_s = next(s for s in c_shapes if s['name'] == name)
            u_s = next(s for s in u_shapes if s['name'] == name)
            if c_s['verts'] != u_s['verts']:
                all_preserved = False
            added   = set(u_s['bones']) - set(c_s['bones'])
            removed = set(c_s['bones']) - set(u_s['bones'])
            for b in added:   new_ube_bones[b]    += 1
            for b in removed: removed_cbbe_bones[b] += 1
            if c_s['bone_count'] != u_s['bone_count']:
                bone_changes[(c_s['bone_count'], u_s['bone_count'])] += 1
        if all_preserved and common:
            topology_preserved_cases += 1

    print(f'\n  AGGREGATE across {len(matched)} matched NIFs:')
    print(f'    CBBE inline-body shapes present (3BA/etc): {has_body_inline_cbbe}/{len(matched)}')
    print(f'    UBE  inline-body shapes present (BaseShape/etc): {has_body_inline_ube}/{len(matched)}')
    print(f'    Full body-swap cases (CBBE body -> UBE body): {body_swap_cases}/{len(matched)}')
    print(f'    Topology-preserved cases (vert counts unchanged): {topology_preserved_cases}/{len(matched)}')
    print(f'    Bone-count change tally: {dict(bone_changes)}')
    if new_ube_bones:
        print(f'\n    Top 10 NEW bones (introduced by UBE rebuild):')
        for bone, count in new_ube_bones.most_common(10):
            print(f'      ({count}x) {bone}')
    if removed_cbbe_bones:
        print(f'\n    Top 10 REMOVED bones (dropped from CBBE):')
        for bone, count in removed_cbbe_bones.most_common(10):
            print(f'      ({count}x) {bone}')


for pair in ['eve_sunfire', 'kozakowy_vampire']:
    survey_pair(pair)


# Also re-survey Obi's Druchii from earlier (using the installed mod folder)
print(f'\n{"="*70}\nPAIR: obi_druchii (from earlier - reference)\n{"="*70}')
obi_cbbe = Path(os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\Obi's Druchii Armor MAIN FILE 3Ba")
obi_ube  = Path(os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\Bodyslide Output\meshes\!UBE\Obicnii\DruchiiArmor")
# Use only the .nif we know
obi_cbbe_nif = obi_cbbe / 'meshes' / 'Obicnii' / 'DruchiiArmor' / 'Druchii Top_1.nif'
obi_ube_nif  = obi_ube / 'Druchii Top_1.nif'
if obi_cbbe_nif.is_file() and obi_ube_nif.is_file():
    c_shapes = summarize_nif(obi_cbbe_nif)
    u_shapes = summarize_nif(obi_ube_nif)
    c_names = {s['name'] for s in c_shapes}
    u_names = {s['name'] for s in u_shapes}
    print(f'  Druchii Top_1.nif')
    print(f'    CBBE shapes: {sorted(c_names)}')
    print(f'    UBE  shapes: {sorted(u_names)}')
    new_bones = Counter()
    for name in c_names & u_names:
        c_s = next(s for s in c_shapes if s['name'] == name)
        u_s = next(s for s in u_shapes if s['name'] == name)
        added = set(u_s['bones']) - set(c_s['bones'])
        for b in added: new_bones[b] += 1
    print(f'    New UBE bones introduced (top 10):')
    for b, n in new_bones.most_common(10):
        print(f'      {b}')
