"""STEP 4 pipeline (prototype): for RaceMenuBase scripts that register a BODY
texture on the FEET slot (multi-slot reuse), generate a feet-UV version of that
texture at a new path and recompile the script so AddFeetPaint points there.
Feet-only AddFeetPaint entries (their own dedicated file) are left alone -- the
normal overlay pass already converts those correctly at their own path.

Runs into a STAGING dir for verification first. Set LIMIT to one script's stem
to prove end-to-end, then clear LIMIT for all packs."""
import os, sys, re, tempfile, zipfile, shutil, subprocess
from pathlib import Path

os.environ["CBBE2UBE_MODS_ROOT"] = r"D:\Modlists\ARR\mods"
REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / ".pynifly"))
from src import overlay_transfer as ot          # noqa: E402
from src import overlay_slots as osl            # noqa: E402
from src import paths as P                       # noqa: E402
from src.bsa_strings import BSAArchive          # noqa: E402

MODS = Path(r"D:\Modlists\ARR\mods")
SK = Path(r"D:\SteamLibrary\steamapps\common\Skyrim Special Edition")
COMPILER = SK / "Papyrus Compiler" / "PapyrusCompiler.exe"
SCRIPTS_ZIP = SK / "Data" / "Scripts.zip"
SKSE_SRC = MODS / "Skyrim Script Extender (SKSE64)" / "Scripts" / "Source"
STAGING = Path(tempfile.mkdtemp(prefix="feet_stage_"))
LIMIT = None
FEET_SUFFIX = "_ubefeet"

AFP = re.compile(r'(AddFeetPaint\s*\(\s*"[^"]*"\s*,\s*")([^"]+)(")', re.IGNORECASE)


def assemble_src(work):
    """Build the import source dir: SKSE-extended base (first = wins over vanilla)
    + RaceMenu's racemenubase/nioverride + vanilla base from Scripts.zip."""
    base = work / "base"; src = work / "src"; base.mkdir(); src.mkdir()
    with zipfile.ZipFile(SCRIPTS_ZIP) as z:
        z.extractall(base)
    basesrc = next(base.rglob("TESV_Papyrus_Flags.flg")).parent
    for f in SKSE_SRC.glob("*.psc"):           # SKSE additions override vanilla
        shutil.copy(f, src)
    rm = BSAArchive(next((MODS / "RaceMenu").glob("*.bsa")), eager=False)
    for n in rm.list_files(""):
        if n.lower().endswith(".psc") and "source" in n.lower():
            (src / Path(n).name).write_bytes(rm.read_file(n))
    return src, basesrc


def compile_psc(psc_path, src, basesrc, out_dir):
    flg = next(basesrc.glob("TESV_Papyrus_Flags.flg"))
    shutil.copy(psc_path, src)                 # target joins the import dir
    r = subprocess.run(
        [str(COMPILER), Path(psc_path).stem, "-import=" + str(src) + ";" + str(basesrc),
         "-output=" + str(out_dir), "-flags=" + str(flg)],
        capture_output=True, text=True, cwd=str(out_dir))
    pex = out_dir / (Path(psc_path).stem + ".pex")
    return pex if pex.is_file() else None, (r.stdout + r.stderr)


def build_source_map():
    """rel_texture_path -> source, for resolving an AddFeetPaint path to its file."""
    out = {}
    for mod in sorted(p for p in MODS.iterdir() if p.is_dir()):
        if mod.name.lower() == "cbbetoube auto":
            continue
        for root in ot._OVERLAY_ROOTS:
            d = mod / Path(root)
            if d.is_dir():
                for f in d.rglob("*.dds"):
                    out.setdefault(f.relative_to(mod).as_posix().lower(), ("loose", f))
        for bsa in mod.glob("*.bsa"):
            try:
                arc = BSAArchive(bsa, eager=False)
            except Exception:
                continue
            for root in ot._OVERLAY_ROOTS:
                try:
                    names = arc.list_files(root)
                except Exception:
                    continue
                for n in names:
                    rel = n.replace("\\", "/").lower()
                    if rel.endswith(".dds") and rel.startswith(root):
                        out.setdefault(rel, ("bsa", arc, n))
    return out


def iter_feet_scripts():
    """(mod, psc_name, text) for every loose/BSA .psc containing AddFeetPaint."""
    for mod in sorted(p for p in MODS.iterdir() if p.is_dir()):
        for f in mod.rglob("*.psc"):
            try:
                t = f.read_text("utf-8", "replace")
            except OSError:
                continue
            if "AddFeetPaint" in t:
                yield mod.name, f.name, t
        for bsa in mod.glob("*.bsa"):
            try:
                arc = BSAArchive(bsa, eager=False); names = arc.list_files("")
            except Exception:
                continue
            for n in names:
                if n.lower().endswith(".psc"):
                    d = arc.read_file(n)
                    t = d.decode("utf-8", "replace") if isinstance(d, (bytes, bytearray)) else str(d)
                    if "AddFeetPaint" in t:
                        yield mod.name, n.rsplit("/", 1)[-1], t


def main():
    lay = P.discover_layout()
    tex = ot.find_texconv()
    slot_map = osl.build_script_slot_map(lay)
    srcmap = build_source_map()
    feet_corr = ot.build_region_correspondence("feet")
    work = Path(tempfile.mkdtemp(prefix="feet_tool_"))
    src, basesrc = assemble_src(work)
    print("toolchain assembled. staging ->", STAGING)
    tex_out = STAGING; pex_out = STAGING / "Scripts"; pex_out.mkdir(parents=True)
    twork = work / "tw"; twork.mkdir()

    n_scripts = n_tex = n_skip_feetonly = n_compiled = 0
    for mod, name, text in iter_feet_scripts():
        if LIMIT and Path(name).stem.lower() != LIMIT.lower():
            continue
        # which AddFeetPaint paths are MULTI-slot (also body) -> need repoint
        repoint = {}     # original rel -> new rel
        for m in AFP.finditer(text):
            rel = osl.normalize_script_texpath(m.group(2))
            slots = slot_map.get(rel) or set()
            if "body" not in slots:
                n_skip_feetonly += 1
                continue                       # feet-only: handled by normal pass
            stem_rel = rel[:-4] + FEET_SUFFIX + ".dds"
            repoint[rel] = stem_rel
        if not repoint:
            continue
        # generate the feet-UV textures
        for rel, newrel in repoint.items():
            srcrec = srcmap.get(rel)
            if not srcrec:
                print("  !! source not found:", rel); continue
            if srcrec[0] == "loose":
                src_dds = srcrec[1]
            else:
                src_dds = twork / "s.dds"; src_dds.write_bytes(srcrec[1].read_file(srcrec[2]))
            rgba = ot.dds_to_rgba(src_dds, tex, twork)
            outp = tex_out / newrel.replace("/", "\\")
            ot.rgba_to_dds(ot.transfer_overlay(rgba, feet_corr), outp, tex, twork)
            n_tex += 1
        # rewrite the .psc AddFeetPaint paths (only the multi-slot ones)
        def _sub(mm):
            rel = osl.normalize_script_texpath(mm.group(2))
            if rel in repoint:
                # rebuild the original-style path with the suffix inserted
                newpath = mm.group(2)[:-4] + FEET_SUFFIX + ".dds"
                return mm.group(1) + newpath + mm.group(3)
            return mm.group(0)
        edited = AFP.sub(_sub, text)
        epsc = twork / name
        # normalize to \n + write bytes -- write_text would re-translate \n->\r\n
        # and double the CRs the source already has (compiler rejects bare \r).
        epsc.write_bytes(edited.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))
        pex, log = compile_psc(epsc, src, basesrc, pex_out)
        n_scripts += 1
        if pex:
            n_compiled += 1
            print(f"  OK  {name}: {len(repoint)} feet repointed -> {pex.name}")
        else:
            print(f"  FAIL {name}:\n{log[-800:]}")
    print(f"\nscripts processed {n_scripts}, compiled {n_compiled}, feet textures {n_tex}, "
          f"feet-only skipped {n_skip_feetonly}")
    print("staging dir:", STAGING)


if __name__ == "__main__":
    main()
