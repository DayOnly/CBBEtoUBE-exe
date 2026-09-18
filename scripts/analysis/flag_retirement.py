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


def raw_env_kill_switches() -> "list[tuple[str, str]]":
    """(module, env) for every `CBBE2UBE_NO_*` read by RAW `os.environ`.

    These are kill switches this census cannot score and must not pretend it
    has. `rows()` below parses `_flag(...)` calls, so a switch read straight off
    `os.environ` is invisible to it -- and to `dead_gate_audit.py` and
    `flag_surface.declared_all()` alike. Eight of them existed when this was
    written, which means "kill-switch bindings : 79" was a count over the
    `_flag` surface and not over the whole one. The report now says so instead
    of quietly being short.

    The real fix is upstream -- `envflags` exists BECAUSE 12 inline spellings
    disagreed on what "0"/"true"/empty meant -- but converting a read changes
    how that switch parses its value, so it is a measured change, not a tidy.
    """
    import re as _re
    # A WRITE (`os.environ["X"] = "1"`, the window arming its child) is not a
    # read. The name must run to its closing quote (else the group backtracks
    # one letter and the lookahead has nothing to reject), and what follows
    # must not be `] =`. Measured 2026-09-16: it counted one such write.
    pat = _re.compile(r"os\.environ(?:\.get\(|\[)\s*[\"']"
                      r"(CBBE2UBE_NO_[A-Z0-9_]+)(?=[\"'])(?![\"']\]\s*=[^=])")
    out = []
    src_dir = _REPO / "src"
    if not src_dir.is_dir():
        return out
    for p in sorted(src_dir.glob("*.py")):
        if p.stem == "envflags":
            continue                       # the helper itself, by definition
        text = p.read_text(encoding="utf-8", errors="replace")
        for env in sorted(set(pat.findall(text))):
            out.append((p.stem, env))
    return out


#: The switches the mechanical checks leave STANDING (default ON, nothing but
#: their own binding names them, the live recipe does not touch them) and what
#: each is waiting on. A standing switch is retired only when the record says
#: the feature has an in-game verdict AND that OFF is worse (memory: E1 is
#: worth ONE flag, not a sweep). The census names every standing switch that
#: is NOT in this table as UNRECORDED, and every entry that is no longer
#: standing as STALE, and tests/test_flag_retirement.py pins both directions.
#: Coverage measured 2026-09-16 (docs / maintainer notes / tests):
AWAITING_VERDICT = {
    "BUST_SURFACE_REQ": "CHANGELOG + the 2026-09-01 audit; no note, no test",
    "GROOVE_SMOOTH_ENABLED": "two maintainer notes + the 2026-09-01 audit; no test",
    "PROXY_ENCLOSE_GUARD": "one maintainer note + the 2026-09-01 audit; no test",
    "STATIC_AUTHORED_FIT": "CHANGELOG + one maintainer note + the audit; no test",
    "_TIGHT_SOFTBODY_GATE": "one maintainer note + the 2026-09-01 audit; no test",
}
# Retired 2026-09-16, the one that was decoration by every definition:
# BACK_RESIDUAL_VERBOSE, the back-residual log-verbosity switch (the CHANGELOG
# names its variable; naming it here would count as a reference), with no
# reader, no test and no record; its two prints are unconditional now.


def standing(rs) -> list:
    """The rows the mechanical checks leave standing: default ON, unreferenced
    outside the binding, untouched by the live recipe."""
    return [r for r in rs if r["resolves_on"] and r["n_refs"] == 0
            and not r["in_live_settings"]]


def verdict_status(name: str) -> str:
    """What the report prints beside a standing switch."""
    note = AWAITING_VERDICT.get(name)
    if note:
        return "awaiting verdict: " + note
    return "UNRECORDED -- decoration until the record says otherwise"


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
    unref = standing(rs)
    refd = [r for r in on if r["n_refs"] > 0 and not r["in_live_settings"]]
    out("=" * 76)
    out("KILL-SWITCH RETIREMENT EVIDENCE   (plan item E1, mechanical half)")
    out("=" * 76)
    out("  kill-switch bindings                       : %d" % len(rs))
    _raw = raw_env_kill_switches()
    if _raw:
        out("  NOT COUNTED -- read by raw os.environ      : %d   (invisible"
            % len(_raw))
        out("        to this census; the number above is the `_flag` surface,")
        out("        not the whole one. Retiring any of these needs a manual")
        out("        read of the site.)")
        for _m, _e in _raw:
            out("        %-40s src/%s.py" % (_e, _m))
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
    out("STILL STANDING, in full (and what each waits on):")
    for r in sorted(unref, key=lambda x: x["name"]):
        out("    %-40s %-38s gui=%s"
            % (r["name"], r["env"], "yes" if r["gui"] else "NO"))
        out("        %s" % verdict_status(r["name"]))
    if not unref:
        out("    (none)")
    stale = sorted(set(AWAITING_VERDICT) - {r["name"] for r in unref})
    for name in stale:
        out("    STALE VERDICT ENTRY: %s is no longer standing (referenced,"
            " thrown by the recipe, or retired) -- drop it from"
            " AWAITING_VERDICT" % name)
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
    settings = os.environ.get("CBBE2UBE_CONFIG", "")
    show_all = recipe = False
    i = 0
    # EVERY arg is read before ANY of them acts. Acting inside the parse loop
    # made `--recipe --settings X` report "NOT MEASURED" -- an honest exit 3,
    # but a wrong one, and the natural argument order is the one that hit it.
    while i < len(raw):
        if raw[i] == "--settings" and i + 1 < len(raw):
            settings = raw[i + 1]; i += 2; continue
        if raw[i] == "--all":
            show_all = True; i += 1; continue
        if raw[i] == "--recipe":
            recipe = True; i += 1; continue
        if raw[i].startswith("--"):
            i += 1; continue
        print(__doc__)
        return 2
    if recipe:
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
    rs, live = rows(settings)
    # The population is the BINDINGS. Zero of them means the regex stopped
    # matching, which must not read as "nothing to retire".
    require_population(rs, "kill-switch bindings")
    return report(rs, live, show_all)


if __name__ == "__main__":
    sys.exit(main())
