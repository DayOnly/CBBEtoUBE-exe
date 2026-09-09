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

"""WHICH KILL SWITCHES COULD BE RETIRED -- the mechanical half of plan item E1.

    python -m scripts.analysis.flag_retirement [--all] [--settings <json>]

The retirement rule has four conditions: the flag is default ON, an in-game
verdict is ON RECORD for the feature, the switch has gone unused since, and the
OFF branch reproduces a measured-WORSE state. **Two of those are judgements
against the record and this tool does not make them.** It computes the other
two, and -- more usefully -- it finds the flags that are DISQUALIFIED on
mechanical grounds, so the judgement is only ever spent on the ones still
standing.

WHAT IT COMPUTES, per `not _flag("CBBE2UBE_NO_X", True)` binding:

  * whether it actually RESOLVES to ON in a scrubbed environment (a declared
    default is a dated claim; four constants in this codebase are reassigned
    after declaration);
  * whether the env name appears anywhere OUTSIDE its own binding -- source,
    tests, docs, scripts -- because "unused since" is exactly that;
  * whether the LIVE settings json turns it off, which disqualifies it
    outright: someone is running with that switch thrown;
  * whether the GUI can reach it at all.

WHAT IT DELIBERATELY DOES NOT DO. It never says "retire this". A count cannot
know whether a feature has an in-game verdict on record, and this project has
already deleted flags on evidence and kept others that looked identical from
the outside. The output is an EVIDENCE TABLE for that judgement.

A NOTE ON "UNUSED". A grep is a lower bound: a flag read through a computed
name would not appear. That has not happened here -- every binding is a literal
-- but the count is reported as "references found", never as "proven unused".

Exit 0 = reported, 2 = bad arguments, 3 = no kill switches found at all
(0/0 is not a pass; the binding regex has drifted).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

from scripts.analysis import flag_surface as fs               # noqa: E402
from scripts.analysis._census_common import require_population  # noqa: E402

# Where a switch could still be in use. `docs/` counts: a flag named in the
# worklog is one a reader may be about to set.
_SEARCH_DIRS = ("src", "tests", "scripts", "docs")


def _text_files():
    for d in _SEARCH_DIRS:
        root = _REPO / d
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            if p.suffix.lower() not in (".py", ".md", ".ps1", ".json", ".txt"):
                continue
            if "__pycache__" in p.parts:
                continue
            yield p


def references(envs: "set[str]") -> dict:
    """env name -> {relative path: count}, EXCLUDING its own binding line.

    The binding is excluded by matching the `_flag("NAME"` form, so a file that
    only declares the flag does not read as a use of it.
    """
    out = {e: {} for e in envs}
    bind = {e: re.compile(r'_flag\(\s*"%s"' % re.escape(e)) for e in envs}
    for p in _text_files():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for e in envs:
            if e not in text:
                continue
            n = text.count(e) - len(bind[e].findall(text))
            if n > 0:
                out[e][p.relative_to(_REPO).as_posix()] = n
    return out


def live_overrides(path) -> dict:
    """Flags the DEPLOYED settings json actually sets, keyed by env name.

    A switch the live recipe throws is not a retirement candidate at all, and
    the settings file is the one place a default can be overridden without any
    of the greps above seeing it.
    """
    if not path:
        return {}
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for k, v in d.items():
        if isinstance(v, bool):
            out["CBBE2UBE_" + k.upper()] = v
            out["CBBE2UBE_NO_" + k.upper()] = v
    return out


def recipe_drift(settings_path):
    """Flags whose CODE default disagrees with the LIVE settings json.

    THE TRAP THIS MAKES MECHANICAL. A flag's default has to be resolved from the
    code, and the shipped pack is built from the code default PLUS whatever the
    deployed settings file overrides. Read only the code and you are wrong about
    the pack; read only the record and you are wrong twice, because a memory's
    default is dated. Both halves of that have cost verdicts here.

    Returns (rows, unknown) -- `unknown` is every boolean key in the settings
    file that no `_flag` binding claims, because a setting nothing reads is
    just as much a defect as a default nothing matches.
    """
    live_raw = {}
    try:
        live_raw = {k: v for k, v in json.loads(
            Path(settings_path).read_text(encoding="utf-8")).items()
            if isinstance(v, bool)}
    except Exception:
        return [], []
    # KEY -> ENV comes from the GUI registry, which is the authority on that
    # mapping. Deriving it by stripping the `CBBE2UBE_` prefix works only while
    # a flag is a plain opt-in: the moment one is promoted and becomes
    # `CBBE2UBE_NO_X`, the derived key is `no_x`, the settings key `x` matches
    # nothing, and five real entries read as "claimed by no binding". That is
    # what this tool did for an hour on 2026-09-09.
    key_to_env = {}
    try:
        from src import gui_settings as gs
        for row in gs.SETTINGS:
            if getattr(row, "env", None):
                key_to_env[row.key] = row.env
    except Exception:
        pass

    env_to = {}
    for r in fs.declared_all():
        if r["const"]:
            env_to[r["env"]] = (r["module"], r["const"])

    vals_by_mod = {}
    out, claimed = [], set()
    for key, live_on in sorted(live_raw.items()):
        env = key_to_env.get(key) or ("CBBE2UBE_" + key.upper())
        hit = env_to.get(env)
        if hit is None:
            continue
        mod, const = hit
        if mod not in vals_by_mod:
            vals_by_mod[mod] = fs.resolved_values(
                sorted(c for m, c in env_to.values() if m == mod), mod)
        claimed.add(key)
        code_on = vals_by_mod[mod].get(const) is True
        if live_on != code_on:
            out.append({"module": mod, "const": const, "env": env,
                        "code": code_on, "live": live_on})
    unknown = sorted(k for k in live_raw if k not in claimed)
    return out, unknown


def rows(settings_path=None):
    src = (_REPO / "src" / "nif_convert.py").read_text(encoding="utf-8",
                                                       errors="replace")
    decl = fs.declared(src)
    kills = [(n, e) for n, e, kill, _d in decl if kill]
    if not kills:
        return [], {}
    values = fs.resolved_values([n for n, _e in kills])
    gui = fs.gui_envs()
    refs = references({e for _n, e in kills})
    live = live_overrides(settings_path)
    out = []
    for name, env in kills:
        r = refs.get(env, {})
        out.append({
            "name": name,
            "env": env,
            "resolves_on": values.get(name) is True,
            "refs": r,
            "n_refs": sum(r.values()),
            "in_live_settings": env in live,
            "gui": env in gui,
        })
    return out, live


def report(rs, live, show_all=False, out=print) -> int:
    on = [r for r in rs if r["resolves_on"]]
    off = [r for r in rs if not r["resolves_on"]]
    live_set = [r for r in rs if r["in_live_settings"]]
    unref = [r for r in on if r["n_refs"] == 0 and not r["in_live_settings"]]
    refd = [r for r in on if r["n_refs"] > 0 and not r["in_live_settings"]]
    out("=" * 76)
    out("KILL-SWITCH RETIREMENT EVIDENCE   (plan item E1, mechanical half)")
    out("=" * 76)
    out("  kill-switch bindings                       : %d" % len(rs))
    out("  of those, RESOLVE to ON in a clean env     : %d" % len(on))
    out("  resolve OFF -- NOT retirement candidates   : %d   (excluded)"
        % len(off))
    out("  thrown by the LIVE settings json           : %d   (excluded)"
        % len(live_set))
    out("")
    out("  STILL STANDING after the mechanical checks : %d" % len(unref))
    out("      ^ default ON, no reference outside its own binding, and the")
    out("        live recipe does not touch it. The record still has to say")
    out("        the feature has an in-game verdict AND that OFF is worse.")
    out("  referenced elsewhere (judge case by case)  : %d" % len(refd))
    out("")
    if not on:
        out("  NOTHING RESOLVES ON -- the binding regex has drifted.")
    out("STILL STANDING, in full:")
    for r in sorted(unref, key=lambda x: x["name"]):
        out("    %-40s %-38s gui=%s"
            % (r["name"], r["env"], "yes" if r["gui"] else "NO"))
    if not unref:
        out("    (none)")
    out("")
    out("REFERENCED ELSEWHERE -- each reference is a reason it may be in use:")
    for r in sorted(refd, key=lambda x: -x["n_refs"])[:None if show_all else 20]:
        where = ", ".join("%s x%d" % (k, v)
                          for k, v in sorted(r["refs"].items())[:3])
        out("    %-40s %3d  %s" % (r["name"], r["n_refs"], where[:70]))
    if not show_all and len(refd) > 20:
        out("    ... and %d more (--all)" % (len(refd) - 20))
    if live_set:
        out("")
        out("THROWN BY THE LIVE RECIPE -- not candidates, someone runs these:")
        for r in sorted(live_set, key=lambda x: x["name"]):
            out("    %-40s %s" % (r["name"], r["env"]))
    return 0


def main(argv=None) -> int:
    raw = list(argv if argv is not None else sys.argv[1:])
    settings, show_all, i = os.environ.get("CBBE2UBE_CONFIG", ""), False, 0
    while i < len(raw):
        if raw[i] == "--settings" and i + 1 < len(raw):
            settings = raw[i + 1]; i += 2; continue
        if raw[i] == "--all":
            show_all = True; i += 1; continue
        if raw[i] == "--recipe":
            drift, unknown = recipe_drift(settings)
            print("=" * 76)
            print("CODE DEFAULT vs THE LIVE RECIPE   (the deployed settings json)")
            print("=" * 76)
            if not settings:
                print("  NO SETTINGS FILE GIVEN -- set CBBE2UBE_CONFIG or pass")
                print("  --settings. NOT MEASURED is not 'no drift'.")
                return 3
            print("  flags whose code default DISAGREES with the recipe : %d"
                  % len(drift))
            for d in drift:
                print("    %-34s code=%-5s live=%-5s  (%s)"
                      % (d["const"], d["code"], d["live"], d["module"]))
            print("  settings keys no `_flag` binding claims            : %d"
                  % len(unknown))
            for k in unknown:
                print("    %s" % k)
            print()
            print("  A disagreement is not automatically a bug -- but the pack")
            print("  ships the LIVE column, and every reader of the code sees")
            print("  the other one.")
            return 0
        if raw[i].startswith("--"):
            i += 1; continue
        print(__doc__)
        return 2
    rs, live = rows(settings)
    # The population is the BINDINGS. Zero of them means the regex stopped
    # matching, which must not read as "nothing to retire".
    require_population(rs, "kill-switch bindings")
    return report(rs, live, show_all)


if __name__ == "__main__":
    sys.exit(main())
