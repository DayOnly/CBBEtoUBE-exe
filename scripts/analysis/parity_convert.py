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

"""ONE A/B arm through the BATCH path, from source, at the live recipe.

    python -m scripts.analysis.parity_convert <out_dir> "<mod>[,<mod>...]" [--workers N] [VAR=VALUE ...]

Every A/B in this project is judged between two arms produced by THIS harness,
never against a shipped pack: both arms run the same code, the same live
settings, the same seed, and differ only in the trailing `VAR=VALUE` overrides.
Pass an override as a trailing argument -- `CBBE2UBE_NO_X=1` -- and read it back
in the `OVERRIDE :` line and the converter's own `active flags (N):` echo. The
override is what proves the arm was armed; an arm whose echo does not show it
was NOT an A/B arm.

THE SHELL IS STRIPPED ON PURPOSE. Every `CBBE2UBE_*` in the calling environment
is dropped before the live settings are applied, so the arm cannot inherit a
stale export from an earlier experiment. That trap has produced four "arms"
that were the same arm ([[feedback_parity_harness_strips_env]]).

What it resolves, in this order:
  * the repo -- from this file's location, never a hardcoded path;
  * the live settings json -- `CBBE2UBE_CONFIG` if set, else `--settings`, else
    `gui_settings.config_path()`. For a verdict-grade arm point it at the json
    beside the DEPLOYED exe (the path is machine-local and lives in memory, not
    here); at the repo default you are measuring code defaults, which is a
    different arm and must be labelled as one;
  * the MO2 ini -- `CBBE2UBE_MO2_INI` if set, else `paths.find_mo2_ini()`.

`PYTHONHASHSEED` is pinned to 1 unless already set, so two runs of one arm are
byte-identical and a diff between arms means the override.

`--workers 1` is the REFERENCE mode. It is NO LONGER REQUIRED for weight A/Bs.

A `_0` and its `_1` share one physics XML and one tri, and a pool that converted
the pair CONCURRENTLY let the `_1` weight passes read that XML inside the window
where the sibling's finalize had just overwritten it with the authored copy and
not yet re-added the split collider -- so the collider was reweighted in one arm
and kept in another. Measured 2026-09-06: three identical pool arms, 5 shapes of
144 files bimodal (every one a `_1` collider proxy, 0 vertices moved, the same
0.8486 worst delta in each odd arm).

CLOSED the same day as `#pair-unit-dispatch` (`auto_convert._pair_units` /
`_run_unit`): a pair is now ONE unit on ONE worker, in serial order, and two
fixed 16-worker arms and a `--workers 1` arm are byte-identical over all 296
files under `meshes/`. Weight A/Bs are sound at pool scale again -- a 16-worker
arm is ~5 minutes against ~70 for serial, so use the pool and keep `--workers 1`
for confirming a suspected race. Any pool-scale WEIGHT number measured BEFORE
that fix is contaminated at the ~1000-row / 0.85 level on collider shapes.

The `--no-auto-merge` batch is what runs: work items go through
`auto_convert._nif_convert_worker` exactly as a real reconvert does
(#single-vs-batch-parity), including the VFS resolve, the BSA staging and the
weight-partner fill.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

from src import gui_settings as gs  # noqa: E402
from src import paths  # noqa: E402


def build_env(settings_json: Path, mo2_ini: Path | None, overrides: list[str],
              base: dict | None = None) -> dict:
    """The arm's environment: live settings over a SCRUBBED shell, plus the
    trailing overrides, echoed so the arm is provably armed."""
    base = dict(os.environ if base is None else base)
    scrubbed = {k: v for k, v in base.items() if not k.startswith("CBBE2UBE_")}
    values = gs.load_values(settings_json)
    env = gs.apply_env(values, base_env=scrubbed)
    if mo2_ini is not None:
        env["CBBE2UBE_MO2_INI"] = str(mo2_ini)
    env["CBBE2UBE_NO_PAUSE"] = "1"
    env.setdefault("PYTHONHASHSEED", "1")
    for ov in overrides:
        if "=" not in ov:
            raise SystemExit(f"override must be VAR=VALUE, got {ov!r}")
        k, v = ov.split("=", 1)
        env[k] = v
        print(f"OVERRIDE : {k}={v}   <- A/B ARM")
    managed = sorted(k for k in env if k.startswith("CBBE2UBE_")
                     and k not in ("CBBE2UBE_MO2_INI", "CBBE2UBE_NO_PAUSE"))
    print(f"settings : {settings_json}")
    print(f"mo2 ini  : {mo2_ini}")
    print(f"env      : {len(managed)} var(s): {', '.join(managed)}")
    print(f"seed     : PYTHONHASHSEED={env['PYTHONHASHSEED']}")
    return env


def main(argv: list[str]) -> int:
    args = list(argv)
    settings = None
    if "--settings" in args:
        i = args.index("--settings")
        settings = Path(args[i + 1])
        del args[i:i + 2]
    workers = None
    if "--workers" in args:
        i = args.index("--workers")
        workers = int(args[i + 1])
        del args[i:i + 2]
    if len(args) < 2:
        print(__doc__)
        return 2
    out_dir, mods = args[0], args[1]
    overrides = args[2:]

    cfg = os.environ.get("CBBE2UBE_CONFIG", "").strip()
    if settings is None:
        settings = Path(cfg) if cfg else gs.config_path()
    if not settings.is_file():
        raise SystemExit(
            f"no settings json at {settings} -- pass --settings or set "
            "CBBE2UBE_CONFIG to the json beside the deployed exe. Refusing to "
            "run an unlabelled arm at code defaults.")
    ini = os.environ.get("CBBE2UBE_MO2_INI", "").strip()
    mo2_ini = Path(ini) if ini else paths.find_mo2_ini()

    env = build_env(settings, mo2_ini, overrides)
    cmd = [sys.executable, str(_REPO / "cbbe_to_ube_main.py"), "auto",
           "--only-mods", mods, "-o", out_dir, "--no-auto-merge"]
    if workers is not None:
        cmd += ["--workers", str(workers)]
    print(f"workers  : {workers if workers is not None else 'pool default'}")
    print("cmd      :", " ".join(cmd))
    return subprocess.call(cmd, env=env, cwd=str(_REPO))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
