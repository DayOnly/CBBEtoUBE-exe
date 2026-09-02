"""Move named functions / module-level state out of src/nif_convert.py into a
sibling module, behaviour-neutrally. The tool behind the 2026-09-01 split.

    python scripts/split_move.py --module nif_convert_<topic> --step "split step N"         --doc "<module docstring>" --names f1,f2,... [--state C1,C2,...] [--dry]

Reads: src/nif_convert.py (must be CRLF), scripts/pass_map.py.
Writes: src/<module>.py (LF), src/nif_convert.py (CRLF, spans replaced by a
by-name import shim; the module appended to `_SPLIT_MODULES`),
scripts/pass_map.py (`CONVERTER_MODULES`).

Exit codes: 0 = moved (or --dry: would move); 2 = REFUSED, nothing written
(a `global` on a moved/unknown name, a store to a nif_convert name, a
nif_convert-only name in a signature default / annotation / decorator, or an
unknown free name). An AssertionError = a name was not found at module level
or spans overlap; also nothing written.

PASS means: the block parsed, every free name was classified, and the
nif_convert file kept its line endings. It does NOT prove behaviour: after a
move run `python scripts/pass_map.py`, the guards
(tests/test_split_preconditions.py and friends), then
`PYTHONHASHSEED=1 python scripts/golden_output.py check` and the full suite --
that sequence is what proved each of the nine 2026-09-01 moves.
"""

import argparse, ast, builtins, pathlib, re, sys

REPO = pathlib.Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "nif_convert.py"
PM = REPO / "scripts" / "pass_map.py"

ap = argparse.ArgumentParser()
ap.add_argument("--module", required=True)
ap.add_argument("--names", default="")
ap.add_argument("--state", default="")
ap.add_argument("--doc", required=True)
ap.add_argument("--step", required=True, help="e.g. 'split step 4'")
ap.add_argument("--dry", action="store_true")
a = ap.parse_args()
NAMES = [x for x in a.names.split(",") if x]
STATE = [x for x in a.state.split(",") if x]

raw = SRC.read_bytes().decode("utf-8"); assert "\r\n" in raw
L = raw.split("\r\n"); tree = ast.parse(raw)
top = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
stnode = {}
for n in tree.body:
    if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name): stnode[n.target.id] = n
    if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name): stnode[n.targets[0].id] = n
imports = {}   # bound name -> import statement source
for n in tree.body:
    if isinstance(n, (ast.Import, ast.ImportFrom)):
        seg = "\n".join(L[n.lineno - 1:n.end_lineno])
        for al in n.names:
            imports[(al.asname or al.name).split(".")[0]] = seg
# POPULATION FLOOR: this parses nif_convert for its module-level defs and
# assignments; if that idiom moved it would "find" a handful and cut the wrong
# thing. 308 defs / 300+ bindings on 2026-09-01; the floor catches a collapse.
if len(top) < 60 or len(stnode) < 60:
    raise SystemExit(f"split_move: population floor -- only {len(top)} top-level "
                     f"defs and {len(stnode)} bindings parsed from {SRC.name}; "
                     "measured NOTHING like the real file, refusing to cut")
missing = [x for x in NAMES if x not in top] + [x for x in STATE if x not in stnode]
assert not missing, f"not found at module level: {missing}"

def span(node):
    s = node.lineno - 1; e = node.end_lineno
    while s > 0 and L[s - 1].startswith("#"): s -= 1
    return s, e
spans = sorted([span(top[f]) for f in NAMES] + [span(stnode[s]) for s in STATE])
for (s1, e1), (s2, e2) in zip(spans, spans[1:]): assert e1 <= s2, ("overlap", s1, e1, s2, e2)

moved = set(NAMES) | set(STATE)
nc_names = set(top) | set(stnode)
lines = list(L)                      # working copy for rewrites
rewrites = 0; needed_imports = {}; refused = []
for f in NAMES + STATE:
    node = top[f] if f in top else stnode[f]
    # import-time references (signature defaults / annotations / decorators)
    import_time = []
    if isinstance(node, ast.FunctionDef):
        for sub in list(node.decorator_list) + list(node.args.defaults) + [d for d in node.args.kw_defaults if d] + [node.returns] + [x.annotation for x in ast.walk(node.args) if isinstance(x, ast.arg)]:
            if sub is None: continue
            import_time += [x.id for x in ast.walk(sub) if isinstance(x, ast.Name) and x.id in nc_names and x.id not in moved]
    if import_time:
        refused.append(f"{f}: import-time reference to {sorted(set(import_time))} (move the constant too, or keep {f})")
    rebound = set()
    for g in ast.walk(node):
        if isinstance(g, ast.Global):
            if any(nm in moved or nm not in nc_names for nm in g.names):
                refused.append(f"{f}: global {g.names} (moved or unknown name)")
            else:
                rebound |= set(g.names)
                lines[g.lineno - 1] = lines[g.lineno - 1][:g.col_offset] + "pass  # (global " + ", ".join(g.names) + " -> _nc().<name>, split)"
    bound = set()
    for x in ast.walk(node):
        if isinstance(x, ast.arg): bound.add(x.arg)
        if isinstance(x, ast.ExceptHandler) and x.name: bound.add(x.name)
        if isinstance(x, (ast.Import, ast.ImportFrom)):          # local imports
            for al in x.names: bound.add((al.asname or al.name).split(".")[0])
        if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and x is not node:
            bound.add(x.name)                                      # nested defs
        if isinstance(x, ast.Name) and isinstance(x.ctx, (ast.Store, ast.Del)):
            if x.id in rebound:
                continue                          # rewritten below as _nc().X = ...
            if x.id in nc_names and x.id not in moved and x.id not in bound:
                refused.append(f"{f}: stores to nif_convert name {x.id}")
            bound.add(x.id)
    for x in sorted([x for x in ast.walk(node) if isinstance(x, ast.Name) and x.id in rebound],
                    key=lambda x: (x.lineno, x.col_offset), reverse=True):
        ln = lines[x.lineno - 1]
        assert ln[x.col_offset:x.end_col_offset] == x.id, (f, x.lineno, ln)
        lines[x.lineno - 1] = ln[:x.col_offset] + "_nc()." + x.id + ln[x.end_col_offset:]
        rewrites += 1
    body_names = [x for x in ast.walk(node) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)]
    for x in sorted(body_names, key=lambda x: (x.lineno, x.col_offset), reverse=True):
        nm = x.id
        if nm in rebound: continue                # already rewritten above
        if nm in moved or nm in bound or nm in dir(builtins): continue
        if nm in imports: needed_imports[nm] = imports[nm]; continue
        if nm in nc_names:
            ln = lines[x.lineno - 1]
            assert ln[x.col_offset:x.end_col_offset] == nm, (f, x.lineno, ln)
            lines[x.lineno - 1] = ln[:x.col_offset] + "_nc()." + nm + ln[x.end_col_offset:]
            rewrites += 1; continue
        refused.append(f"{f}: unknown free name {nm}")
if refused:
    print("REFUSED:\n  " + "\n  ".join(sorted(set(refused)))); sys.exit(2)

block = []
for s, e in spans: block += lines[s:e] + [""]
block_txt = "\n".join(block).rstrip() + "\n"
ast.parse(block_txt)
hdr = ['"""' + a.doc.strip(), "",
       f"Split out of nif_convert.py on 2026-09-01 ({a.step}). Imported by name into",
       "nif_convert, so every `nc.<name>` handle keeps working. Names that stayed in",
       "nif_convert are reached through `_nc()` at CALL time, so flags read at",
       "import, tests' monkeypatches on `nc` and `importlib.reload(nc)` all still",
       'apply."""', "from __future__ import annotations", ""]
std = sorted({s for s in needed_imports.values() if not s.lstrip().startswith("from .")})
loc = sorted({s for s in needed_imports.values() if s.lstrip().startswith("from .")})
hdr += std + ([""] if std else []) + loc + ([""] if loc else [])
if rewrites:
    hdr += ["", "def _nc():", '    """The monolith, resolved at call time (never at import: circular)."""',
            "    return sys.modules[__package__ + \".nif_convert\"]", "", ""]
    if not any(x.strip() == "import sys" for x in hdr):
        hdr.insert(hdr.index("from __future__ import annotations") + 1, "import sys")
mod_txt = "\n".join(hdr) + "\n" + block_txt
out_mod = REPO / "src" / f"{a.module}.py"
names_all = STATE + NAMES
shim = [f"# ({a.step}) {', '.join(names_all[:4])}{' ...' if len(names_all) > 4 else ''} live in {a.module}.py",
        f"# since 2026-09-01. Imported BY NAME so `nc.<name>` keeps working everywhere.",
        f"from .{a.module} import (  # noqa: E402"]
row = "   "
for nm in names_all:
    if len(row) + len(nm) + 2 > 78:
        shim.append(row); row = "   "
    row += " " + nm + ","
if row.strip(): shim.append(row)
shim += [")", ""]
out_lines = list(L)
first = spans[0][0]
for s, e in sorted(spans, reverse=True):
    out_lines[s:e] = [f"# (moved to {a.module}.py, 2026-09-01)"]
out_lines[first:first + 1] = shim
print(f"module {a.module}: {len(NAMES)} funcs, {len(STATE)} state, {sum(e - s for s, e in spans)} lines out, {rewrites} refs rewritten to _nc(), imports {sorted(needed_imports)}")
if a.dry: sys.exit(0)
out_mod.write_bytes(mod_txt.encode("utf-8"))
txt = "\r\n".join(out_lines)
# register in the reload hook (dependency order = move order)
m = re.search(r'_SPLIT_MODULES = \(([^)]*)\)', txt)
assert m, "_SPLIT_MODULES tuple not found in nif_convert.py"
inner = m.group(1).rstrip()
inner = inner + ("" if inner.endswith(",") else ",") + f'\r\n                  "{a.module}",'
txt = txt[:m.start(1)] + inner + txt[m.end(1):]
SRC.write_bytes(txt.encode("utf-8"))
pm = PM.read_text(encoding="utf-8")
anchor = '    "src/fit_metrics.py",'
assert anchor in pm and f'"src/{a.module}.py"' not in pm
PM.write_text(pm.replace(anchor, f'    "src/{a.module}.py",   # {a.step}\n' + anchor, 1), encoding="utf-8")
b = SRC.read_bytes(); print("nif_convert", len(L), "->", len(out_lines), "CRLF", b.count(b"\r\n"), "bareLF", b.count(b"\n") - b.count(b"\r\n"))
