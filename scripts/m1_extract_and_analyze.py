"""Extract the new UBE/CBBE armor sample archives and run M1 comparison.

For each pair, we want:
  - the CBBE/3BA source mesh + ESP
  - the UBE-built mesh + ESP patch

Output: samples/m1/<name>/ with subfolders for cbbe + ube, plus a comparison
report appended to docs/M1_findings.md.
"""
import os, sys, shutil, subprocess, zipfile, py7zr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.pynifly'))
from pyn import pynifly

DL    = Path(r'<HOME>\Downloads')
PROJ  = Path(__file__).resolve().parents[1]
SAMPLES = PROJ / 'samples' / 'm1'
WINRAR = r'C:\Program Files\WinRAR\Rar.exe'


def fresh(p):
    if p.exists(): shutil.rmtree(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def extract_zip(archive, dest):
    with zipfile.ZipFile(archive, 'r') as z:
        z.extractall(dest)


def extract_7z(archive, dest):
    with py7zr.SevenZipFile(archive, 'r') as z:
        z.extractall(path=dest)


def extract_rar(archive, dest):
    # WinRAR's Rar.exe extract: 'x' = extract with full paths
    subprocess.run(
        [WINRAR, 'x', '-y', '-p-', str(archive), str(dest) + os.sep],
        check=True, capture_output=True
    )


def extract(archive_name, dest):
    archive = DL / archive_name
    if not archive.is_file():
        print(f'  MISSING: {archive_name}')
        return False
    fresh(dest)
    ext = archive.suffix.lower()
    print(f'  extracting {archive.name} -> {dest}')
    try:
        if ext == '.zip':  extract_zip(archive, dest)
        elif ext == '.7z': extract_7z(archive, dest)
        elif ext == '.rar': extract_rar(archive, dest)
        else: print(f'  unknown ext {ext}'); return False
    except Exception as e:
        print(f'  EXTRACT FAILED: {e}'); return False
    return True


# --- Stage 1: extract all archives ---
pairs = {
    'eve_sunfire': {
        'cbbe': "Eve's Sunfire Armor Main File-140366-1-0-1738428193.zip",
        'ube':  "Eve's Sunfire Armor UBE Bodyslide Files-140366-1-1-1738770593.rar",
    },
    'kozakowy_vampire': {
        'cbbe': "Female Vampire Armor Replacer 3BA-143980-1-3-1779068914.7z",
        'ube':  "Female Vampire Armor Replacer UBE-143980-1-0opt-1779069100.7z",
    },
}

for pair_name, files in pairs.items():
    pair_dir = SAMPLES / pair_name
    print(f'\n[extract pair] {pair_name}')
    extract(files['cbbe'], pair_dir / 'cbbe')
    extract(files['ube'],  pair_dir / 'ube')


# --- Stage 2: per-pair NIF comparison (where both sides have built meshes) ---
def find_nifs(root):
    """Find all .nif files NOT under CalienteTools (those are sliderset sources, not built outputs)."""
    out = []
    for p in root.rglob('*.nif'):
        if 'calientetools' in str(p).lower(): continue
        out.append(p)
    return out


def summarize_nif(path):
    try:
        nf = pynifly.NifFile(str(path))
    except Exception as e:
        return {'error': str(e)}
    return {
        'shape_count': len(nf.shapes),
        'shapes': [
            {
                'name': s.name,
                'block_type': s.__class__.__name__,
                'verts': len(s.verts),
                'tris': len(s.tris),
                'bone_count': len(getattr(s, 'bone_names', []) or []),
                'bones': list(getattr(s, 'bone_names', []) or []),
            }
            for s in nf.shapes
        ],
    }


def match_nif_by_basename(cbbe_nifs, ube_nifs):
    """Pair up CBBE and UBE NIFs by filename (case-insensitive)."""
    ube_by_name = {p.name.lower(): p for p in ube_nifs}
    out = []
    for c in cbbe_nifs:
        u = ube_by_name.get(c.name.lower())
        if u: out.append((c, u))
    return out


def diff_shapes(c_summary, u_summary):
    cs = {s['name']: s for s in c_summary['shapes']}
    us = {s['name']: s for s in u_summary['shapes']}
    only_c = set(cs) - set(us)
    only_u = set(us) - set(cs)
    common = set(cs) & set(us)
    return cs, us, only_c, only_u, common


def report_pair(pair_name, pair_dir):
    print(f'\n{"="*60}\npair: {pair_name}\n{"="*60}')
    cbbe_nifs = find_nifs(pair_dir / 'cbbe')
    ube_nifs  = find_nifs(pair_dir / 'ube')
    print(f'  CBBE side: {len(cbbe_nifs)} built NIFs')
    print(f'  UBE side:  {len(ube_nifs)} built NIFs')

    matched = match_nif_by_basename(cbbe_nifs, ube_nifs)
    if not matched:
        print('  no matching filenames - UBE side probably has only sliderset source, not built')
        print(f'  (CBBE samples: {[p.name for p in cbbe_nifs[:5]]})')
        print(f'  (UBE samples : {[p.name for p in ube_nifs[:5]]})')
        return None

    print(f'  matched {len(matched)} CBBE/UBE pairs by filename')

    # Aggregate diffs across all matched NIFs in this pair
    all_only_c   = set()
    all_only_u   = set()
    all_common   = set()
    all_bone_changes = []
    for c_path, u_path in matched[:3]:  # cap at 3 per pair to keep report manageable
        c_sum = summarize_nif(c_path)
        u_sum = summarize_nif(u_path)
        if 'error' in c_sum or 'error' in u_sum:
            print(f'  skipping {c_path.name}: parse error'); continue
        cs, us, only_c, only_u, common = diff_shapes(c_sum, u_sum)
        all_only_c |= only_c
        all_only_u |= only_u
        all_common |= common
        print(f'\n  --- {c_path.name} ---')
        print(f'    CBBE side: {c_sum["shape_count"]} shapes, ' +
              ', '.join(f'{n}({s["verts"]}v)' for n, s in cs.items()))
        print(f'    UBE side:  {u_sum["shape_count"]} shapes, ' +
              ', '.join(f'{n}({s["verts"]}v)' for n, s in us.items()))
        print(f'    REMOVED (CBBE-only): {sorted(only_c) or "none"}')
        print(f'    ADDED   (UBE-only):  {sorted(only_u) or "none"}')
        # Per-shape bone changes for common shapes
        for name in sorted(common):
            c = cs[name]; u = us[name]
            if c['verts'] != u['verts']:
                print(f'    {name}: verts {c["verts"]} -> {u["verts"]}  (topology CHANGED)')
            if c['bone_count'] != u['bone_count']:
                # Show first 3 changes between bone lists
                added_bones   = set(u['bones']) - set(c['bones'])
                removed_bones = set(c['bones']) - set(u['bones'])
                print(f'    {name}: bones {c["bone_count"]} -> {u["bone_count"]}')
                if added_bones:   print(f'      + {sorted(list(added_bones))[:5]}')
                if removed_bones: print(f'      - {sorted(list(removed_bones))[:5]}')

    print(f'\n  AGGREGATE (across {min(3, len(matched))} samples):')
    print(f'    Always REMOVED shapes: {sorted(all_only_c) or "none"}')
    print(f'    Always ADDED shapes:   {sorted(all_only_u) or "none"}')


for pair_name in pairs:
    pair_dir = SAMPLES / pair_name
    report_pair(pair_name, pair_dir)
