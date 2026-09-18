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

"""Convert ONE MOD with the DEPLOYED EXE -- byte-parity with a real GUI run.

    python -m scripts.analysis.exe_parity_convert <out_mod_dir> "<mod>" [VAR=VALUE ...]

WHY NOT `parity_convert` / `convert_one_armor`. Those run the INTERPRETED
SOURCE, and the frozen exe's float maths differs from it. MEASURED on one piece
against the mesh the exe had just shipped in bulk: **3605 verts moved, worst
0.3966u**. So a source run can never byte-match what the user ships, and an
"identical / differs" verdict taken from one is confounded by the build.

That does NOT make source arms wrong -- it makes them SELF-CONSISTENT ONLY. An
A/B of two source arms is valid because both carry the same offset; what it
cannot do is state an ABSOLUTE number for the shipped pack. The acceptance
gate's baselines (folds 15974, inverted 1937, and the rest) are SOURCE numbers.
Use this harness when the question is about the shipped artefact itself.

WHY THIS IS TRACKED (2026-09-06). It lived only in an untracked scratchpad while
`docs/worklog/2026-09-01_AUDIT.md` cited it repeatedly as the standard proof
mechanism -- "rule 5: single-harness `exe_parity_convert.py` A/B" -- for finding
after finding. It is the third such tool found missing from the toolkit while
being treated as standard practice, after `morph_sweep` and `damage_ledger`. A
proof step that names a tool nobody has is not a proof step.

It runs the SAME BINARY the GUI runs, through the SAME entry point
(`CBBEtoUBE.exe auto ...`), with the SAME env the GUI builds
(`gui_settings.apply_env` over the saved settings json), scoped to one mod with
`--only-mods` and pointed at a scratch output.

`out_mod_dir` is an output MOD folder -- the harness does not append `meshes/`,
the pipeline does. Give it a fresh scratch dir.

Trailing `VAR=VALUE` pairs are applied AFTER `apply_env`, FOR A/B ARMS ONLY: the
base env deliberately strips every `CBBE2UBE_*` the shell carries, so an
exported kill switch would otherwise be dropped and both arms would silently run
the SAME recipe. Every override is printed; grep the converter's own
`active flags (N):` echo to prove the arm was armed.

Machine paths are RESOLVED, never hardcoded (tracked content carries none):
  exe       `CBBE2UBE_EXE`, else the live MO2 ini's `23\\binary=`
  settings  `CBBE2UBE_CONFIG`, else the json beside that exe
  mo2 ini   `CBBE2UBE_MO2_INI`, else `paths.find_mo2_ini()`
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
from src import gui_settings as gs                        # noqa: E402


def _mo2_ini() -> "str | None":
    got = os.environ.get("CBBE2UBE_MO2_INI")
    if got:
        return got
    try:
        from src import paths
        found = paths.find_mo2_ini()
        return str(found) if found else None
    except Exception:
        return None


def _exe_from_ini(ini: "str | None") -> "Path | None":
    """The tool path the live MO2 instance actually launches. It MOVES, so it is
    read from the ini every time rather than remembered."""
    if not ini or not Path(ini).is_file():
        return None
    try:
        txt = Path(ini).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for m in re.finditer(r"^\s*\d+\\binary\s*=\s*(.+?)\s*$", txt, re.M):
        cand = Path(m.group(1).replace("\\\\", "\\"))
        if cand.name.lower() == "cbbetoube.exe" and cand.is_file():
            return cand
    return None


def main(argv: "list[str]") -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    out_dir, mod_name = argv[0], argv[1]

    ini = _mo2_ini()
    exe = (Path(os.environ["CBBE2UBE_EXE"])
           if os.environ.get("CBBE2UBE_EXE") else _exe_from_ini(ini))
    if exe is None or not exe.is_file():
        print("REFUSING: no deployed exe. Set CBBE2UBE_EXE, or CBBE2UBE_MO2_INI "
              "to an instance whose customExecutables name CBBEtoUBE.exe.")
        return 2
    if not ini:
        print("REFUSING: no MO2 ini. Set CBBE2UBE_MO2_INI.")
        return 2

    live_json = Path(os.environ.get("CBBE2UBE_CONFIG")
                     or (exe.parent / "CBBEtoUBE_settings.json"))
    if not live_json.is_file():
        print(f"REFUSING: settings json not found at {live_json}")
        return 2

    # The exe is the artefact the user ships; say WHICH one this was.
    h = hashlib.sha256(exe.read_bytes()).hexdigest()[:8]
    print(f"exe      : {exe}  sha256 {h}...")

    values = gs.load_values(live_json)
    base = {k: v for k, v in os.environ.items() if not k.startswith("CBBE2UBE_")}
    env = gs.apply_env(values, base_env=base)
    env["CBBE2UBE_MO2_INI"] = ini
    env["CBBE2UBE_NO_PAUSE"] = "1"      # scripted run: never block on a keypress

    for ov in argv[2:]:
        if "=" not in ov:
            print(f"REFUSING: override {ov!r} is not VAR=VALUE")
            return 2
        k, v = ov.split("=", 1)
        env[k] = v
        print(f"OVERRIDE : {k}={v}   <- A/B ARM, not the shipped recipe")

    managed = sorted(k for k in env if k.startswith("CBBE2UBE_")
                     and k not in ("CBBE2UBE_MO2_INI", "CBBE2UBE_NO_PAUSE"))
    print(f"settings : {live_json}")
    print(f"env      : {len(managed)} registry var(s) set -- "
          f"{', '.join(managed) or '(none)'}")
    print("           everything else runs at its CODE default, as a real run does")

    cmd = [str(exe), "auto", "--only-mods", mod_name, "-o", out_dir,
           "--no-auto-merge"]

    # DO NOT DESTROY THE LIVE PACK'S PROVENANCE.
    #
    # The exe writes CBBEtoUBE_last_run.log / _last_failures.json NEXT TO
    # ITSELF, not next to its output, so a scratch A/B arm overwrites the log
    # belonging to the pack the user is currently judging in game. That log says
    # WHICH BUILD AND WHICH FLAGS produced what they are looking at, and it has
    # been lost that way twice. A measurement tool must not perturb the thing it
    # measures. The pack's own conversion_settings.json carries the same build
    # and flag stamp, so this is belt-and-braces -- but the run log holds the
    # per-mod detail that file does not.
    state_dir = exe.parent
    stash = Path(tempfile.mkdtemp(prefix="exeparity_state_"))
    saved = []
    for name in ("CBBEtoUBE_last_run.log", "CBBEtoUBE_last_failures.json"):
        f = state_dir / name
        if f.is_file():
            shutil.copy2(f, stash / name)
            saved.append(f)
    if saved:
        print(f"guarding : {len(saved)} live run-state file(s) -> {stash}")
        print("           restored after the arm; an A/B must not overwrite the")
        print("           provenance of the pack being judged in game")

    print(f"\n$ {' '.join(cmd)}\n")
    try:
        rc = subprocess.call(cmd, env=env)
    finally:
        # Keep THIS ARM's own run log with THIS ARM's output before putting the
        # live one back. Without it the arm's `active flags` echo -- the only
        # proof the exe RECEIVED an override for a flag with no Setting row --
        # is destroyed by the restore.
        for f in saved:
            if f.is_file():
                try:
                    shutil.copy2(f, Path(out_dir) / ("arm_" + f.name))
                except Exception:
                    pass
            shutil.copy2(stash / f.name, f)
        if saved:
            print("\nrestored : " + ", ".join(f.name for f in saved))
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
