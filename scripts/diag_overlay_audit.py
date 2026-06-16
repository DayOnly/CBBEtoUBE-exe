"""Audit the overlay classifier against EVERY overlay in the load order (loose +
BSA), using the production classify_overlay + _overlay_set + the set-level
resolution discover_overlays applies. Reports the final region per set and lists
the SKIPPED ones so a missed body paint (body overlay wrongly skipped) or a
leaked face overlay (face wrongly sent to body) is visible. Read-only."""
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / ".pynifly"))
from src import overlay_transfer as ot          # noqa: E402
from src.bsa_strings import BSAArchive          # noqa: E402

MODS = Path(r"D:\Modlists\ARR\mods")
REL_ROOT = ot._OVERLAY_ROOT


def collect():
    """Every unique overlay rel-path across all mods (loose + BSA), skipping our
    own output. Returns {rel: (mod, kind)} (first seen kept; dir order)."""
    seen: dict = {}
    for mod in sorted(p for p in MODS.iterdir() if p.is_dir()):
        if mod.name.lower() == "cbbetoube auto":
            continue
        ovl = mod / Path(REL_ROOT)
        if ovl.is_dir():
            for f in ovl.rglob("*.dds"):
                seen.setdefault(f.relative_to(mod).as_posix().lower(),
                                (mod.name, "loose"))
        for bsa in mod.glob("*.bsa"):
            try:
                names = BSAArchive(bsa, eager=False).list_files(REL_ROOT)
            except Exception:
                continue
            for n in names:
                rel = n.replace("\\", "/").lower()
                if rel.endswith(".dds") and rel.startswith(REL_ROOT):
                    seen.setdefault(rel, (mod.name, "bsa"))
    return seen


def main():
    seen = collect()
    info = [(rel, ot.classify_overlay(rel), ot._overlay_set(rel)) for rel in seen]
    sws = {st for _, cl, st in info if cl in ("body", "hands", "feet")}

    def final(cl, st):
        return "body" if (cl == "ambiguous" and st in sws) else cl

    fc = Counter(final(cl, st) for _, cl, st in info)
    per_set = defaultdict(Counter)
    for _, cl, st in info:
        per_set[st][final(cl, st)] += 1
    print(f"TOTAL overlays (loose+BSA): {len(info)}")
    print(f"FINAL regions: {dict(fc)}")
    print(f"\nPER-SET final classification "
          f"(* = a body-paint set; MIXED = both body and skipped):")
    for st in sorted(per_set):
        c = per_set[st]
        transferred = c.get("body", 0) + c.get("hands", 0) + c.get("feet", 0)
        skipped = c.get("head", 0) + c.get("ambiguous", 0)
        tag = " *" if st in sws else ""
        mixed = "  <-- MIXED (eyeball for a missed body paint)" if (transferred and skipped) else ""
        print(f"  {st or '(root)'}{tag}: {dict(c)}{mixed}")

    print("\nSKIPPED overlays per set (sample filenames -- any body paint here = a MISS):")
    skp = defaultdict(list)
    for rel, cl, st in info:
        if final(cl, st) not in ("body", "hands", "feet"):
            skp[st].append(rel.rsplit("/", 1)[-1])
    for st in sorted(skp):
        names = sorted(set(skp[st]))
        print(f"  {st or '(root)'} ({len(skp[st])}): {names[:6]}")


if __name__ == "__main__":
    main()
