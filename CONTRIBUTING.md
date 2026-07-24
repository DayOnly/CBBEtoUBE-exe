# Contributing to CBBEtoUBE

## Reporting a problem

Open an issue: <https://github.com/DayOnly/CBBEtoUBE-exe/issues>. There are two
templates, and picking the right one matters because they ask for different
evidence:

- **Bug report** — the converter itself errored, hung, or produced nothing.
- **Conversion problem** — the run finished, but a piece is invisible, clips,
  lost its physics, or an overlay landed wrong.

### Attach the diagnostics zip

In the GUI, click **Export diagnostics**. It writes
`CBBEtoUBE_diagnostics_<timestamp>.zip` next to your output folder, containing:

| File | What it is |
| --- | --- |
| `gui_log.txt` | The run log as the GUI saw it |
| `settings.json` | Your saved conversion settings |
| `exclusions.json` | Any armors you excluded |
| `layout.json` | The discovered MO2 mods root, profile, and game data dirs |
| `preflight.txt` | A fresh **Check setup** run |

That zip answers most of the first round of questions on its own. **Look at it
before you attach it** — the layout snapshot contains your MO2 paths and profile
name, and the run log names the mods in your load order.

Other artifacts worth attaching, all written by a normal run:

- `CBBEtoUBE_last_run.log` and `CBBEtoUBE_last_failures.json` — next to the exe.
- `conversion_report.json`, `conversion_summary.txt`, and
  `conversion_report_<mod>.txt` — at the output mod root. The GUI's **Report**
  button reads the first of these as a health scoreboard.

There is no automatic crash upload and there will not be one: the shipped exe
excludes the `ssl` extension on purpose (OpenSSL 1.1.x is GPL-incompatible and
this project vendors GPL-3.0 PyNifly), so the binary cannot make a network
request at all. Reporting is deliberately manual and file-based.

## Working on the code

```bash
git clone https://github.com/DayOnly/CBBEtoUBE-exe
cd CBBEtoUBE-exe
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt pytest
python -m pytest -q
```

`pynifly` is **not on PyPI** — it is vendored in `.pynifly/` (the `pyn` package
plus `NiflyDLL.dll`) and added to `sys.path` at import time. `NiflyDLL.dll` is a
Windows binary, so the suite is Windows-only.

The suite is ~820 tests and runs in about 15 seconds. Run it before you push;
CI runs the same command on `windows-latest`.

### Pull requests

Branch off `main`, and open the PR against `main`. Keep the diff scoped to one
change — this codebase encodes a lot of hard-won geometry behaviour, and a small
diff is far easier to reason about against a symptom nobody can reproduce
without the exact modlist.

Two things that are easy to get wrong here:

- **Line endings.** `.gitattributes` sets `* -text` and the repo expects
  `core.autocrlf=false`. The tree is intentionally mixed CRLF/LF. Set it in your
  clone (`git config core.autocrlf false`) so a tool cannot silently renormalize
  a file and bury the real diff.
- **Behaviour changes need a test.** Almost every fit-correction pass in
  `DESIGN.md` exists because of a specific in-game failure, and the test suite is
  what stops the next change from reintroducing it.

If your change alters how armor is fitted, say in the PR **what you verified in
game** — which armor, on which body. Structural tests catch structural problems;
they do not catch a mesh that is technically valid and visibly wrong.

## License

CBBEtoUBE is GPL-3.0 (see [LICENSE](LICENSE)) — it vendors GPL-3.0 PyNifly, so
the whole is distributed under GPL-3.0. Contributions are accepted under the
same license.
