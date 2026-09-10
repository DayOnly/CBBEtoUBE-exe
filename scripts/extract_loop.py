"""Lift a per-shape loop out of an orchestrator into its own function,
byte-for-byte (step 6 increment 1 of the 2026-09-01 audit plan).

    python scripts/extract_loop.py --fn convert_nif --loop-line 3333 \
        --name _fit_shapes_copy --doc "..." [--dry]

Reads src/nif_convert.py (must be CRLF). Writes it back with:
  * a new module-level `def <name>(ctx) -> None:` placed directly above
    `--fn`, whose body is `x = ctx.x` for every orchestrator local the loop
    reads, followed by the loop text dedented to function level -- the loop
    body itself is not edited, so a diff shows only the move;
  * the loop in the orchestrator replaced by one call that builds the
    context from those same locals.

Refuses (exit 2, nothing written) when the loop writes a local the
orchestrator reads afterwards, contains a `return` that is not inside a
nested def (it would exit the orchestrator), uses `nonlocal`/`global`, or
holds a line that does not carry the loop's indentation (a column-0 string
continuation would not survive the dedent). Population floor: the target
function must have >= 50 lines and the loop >= 20.

PASS means the file parsed and the loop text moved unchanged; it does not
prove behaviour -- run pass_map, the guards, golden and the suite after.
"""
import argparse, ast, builtins, pathlib, sys

REPO = pathlib.Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "nif_convert.py"

ap = argparse.ArgumentParser()
ap.add_argument("--fn", required=True)
ap.add_argument("--loop-line", type=int, required=True)
ap.add_argument("--name", required=True)
ap.add_argument("--doc", required=True)
ap.add_argument("--dry", action="store_true")
a = ap.parse_args()

raw = SRC.read_bytes().decode("utf-8"); assert "\r\n" in raw
L = raw.split("\r\n"); tree = ast.parse(raw)
fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
fn = fns[a.fn]
if fn.end_lineno - fn.lineno < 50:
    raise SystemExit("extract_loop: population floor -- target function is too small; measured NOTHING like an orchestrator")
loop = next(n for n in ast.walk(fn) if isinstance(n, ast.For) and n.lineno == a.loop_line)
lo, hi = loop.lineno, loop.end_lineno
if hi - lo < 20:
    raise SystemExit("extract_loop: population floor -- loop is too small to be the per-shape chain")
mod_names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
for n in tree.body:
    if isinstance(n, (ast.Assign, ast.AnnAssign)):
        for t in ([n.target] if isinstance(n, ast.AnnAssign) else n.targets):
            if isinstance(t, ast.Name): mod_names.add(t.id)
    if isinstance(n, (ast.Import, ast.ImportFrom)):
        for al in n.names: mod_names.add((al.asname or al.name).split(".")[0])

inside = lambda x: lo <= getattr(x, "lineno", -1) <= hi
out_store = {x.arg for x in fn.args.args} | {x.arg for x in fn.args.kwonlyargs}
in_store, in_load, after_store, after_load = set(), set(), {}, {}
refused = []
nested_defs = [x for x in ast.walk(loop) if isinstance(x, (ast.FunctionDef, ast.Lambda))]
def in_nested(x):
    return any(getattr(d, "lineno", 0) <= x.lineno <= getattr(d, "end_lineno", 0) for d in nested_defs if isinstance(d, ast.FunctionDef))
for x in ast.walk(fn):
    if isinstance(x, ast.Name):
        if inside(x):
            (in_store if isinstance(x.ctx, ast.Store) else in_load).add(x.id)
        elif isinstance(x.ctx, ast.Store):
            (out_store.add(x.id) if x.lineno < lo else after_store.setdefault(x.id, x.lineno))
        elif x.lineno > hi:
            after_load.setdefault(x.id, x.lineno)
    elif isinstance(x, ast.arg):
        (in_store if inside(x) else out_store).add(x.arg)
    elif inside(x) and isinstance(x, (ast.Nonlocal, ast.Global)):
        refused.append(f"{type(x).__name__} at L{x.lineno}")
    elif inside(x) and isinstance(x, ast.Return) and not in_nested(x):
        refused.append(f"orchestrator return inside the loop at L{x.lineno}")
inputs = sorted((in_load - in_store) & out_store - mod_names - set(dir(builtins)))
outputs = sorted(v for v in in_store & set(after_load) if after_load[v] < after_store.get(v, 10 ** 9))
if outputs:
    refused.append(f"loop writes locals the orchestrator reads afterwards: {outputs}")
indent = len(L[lo - 1]) - len(L[lo - 1].lstrip(" "))
body = L[lo - 1:hi]
for i, ln in enumerate(body):
    if ln.strip() and not ln.startswith(" " * indent):
        refused.append(f"L{lo + i} does not carry the loop indentation ({indent}); dedent would corrupt it")
if refused:
    print("REFUSED:\n  " + "\n  ".join(refused)); sys.exit(2)
dedented = [ln[indent - 4:] if ln.strip() else "" for ln in body]
new_fn = [f"def {a.name}(ctx) -> None:",
          f'    """{a.doc.strip()}',
          "",
          f"    Lifted verbatim out of `{a.fn}` on 2026-09-01 (audit step 6, increment 1):",
          "    the loop body below is the orchestrator's own text, unchanged; `ctx` carries",
          "    exactly the orchestrator locals it read. Results still flow through the",
          "    mutated containers on `ctx` (the shape jobs and the failure list).",
          '    """'] + [f"    {nm} = ctx.{nm}" for nm in inputs] + [""] + dedented + ["", ""]
call = [" " * indent + f"{a.name}(_types.SimpleNamespace(",
        *[" " * (indent + 4) + f"{nm}={nm}," for nm in inputs],
        " " * indent + "))"]
print(f"{a.fn}: loop L{lo}-{hi} ({hi - lo + 1} lines, indent {indent}) -> {a.name}(ctx) with {len(inputs)} inputs; nested defs {[d.name for d in nested_defs if isinstance(d, ast.FunctionDef)]}")
if a.dry: sys.exit(0)
out = L[:fn.lineno - 1 - 0]
# find the comment block directly above the orchestrator def, insert before it
ins = fn.lineno - 1
while ins > 0 and L[ins - 1].startswith("#"): ins -= 1
out = L[:ins] + new_fn + L[ins:lo - 1] + call + L[hi:]
txt = "\r\n".join(out)
if "import types as _types" not in txt:
    txt = txt.replace("import sys\r\n", "import sys\r\nimport types as _types\r\n", 1)
ast.parse(txt)
SRC.write_bytes(txt.encode("utf-8"))
b = SRC.read_bytes(); print("written; CRLF", b.count(b"\r\n"), "bareLF", b.count(b"\n") - b.count(b"\r\n"))
