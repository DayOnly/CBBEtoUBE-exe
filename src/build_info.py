"""Who am I, and what am I running with -- the build stamp and the effective
configuration of a run.

Two questions every in-game verdict has to answer before it can be scored:
WHICH build produced the pack, and WHAT settings that build ran at. Until
2026-09-01 neither was written down by the run itself: the exe carried only
the release string (twelve different builds shipped as "1.3"), and the flag
echo listed environment OVERRIDES only, so a default promoted in code and a
setting silently lost printed the same line. Attribution was a hand-kept
CRC32 and a hand-copied settings backup.

This module answers both from inside the process:

* `stamp()`      -- version, git commit (+dirty), build time, whether frozen,
                    and the sha256 of the running exe. The git fields come
                    from `src/_build_stamp.py`, which `scripts/build_exe.ps1`
                    writes just before PyInstaller runs (gitignored; absent in
                    a source checkout, which then reads as "source").
* `effective_settings()` -- every registered GUI setting resolved the way the
                    converter resolves it: environment first, default
                    otherwise, polarity applied. This is the value the passes
                    see, whether it came from the settings file, the shell, or
                    a code default.
* `run_config()`  -- the two above plus the settings-file path, its sha256 and
                    load status, and the mods root: the block that goes into
                    `conversion_report.json` and `conversion_settings.json`.

Nothing here touches geometry. Everything is best-effort and never raises
into a run.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

try:                                    # written by scripts/build_exe.ps1
    from ._build_stamp import BUILD as _BUILD      # type: ignore
except Exception:                       # source checkout: no stamp file
    _BUILD = {}


def _sha256_file(p, n: int = 12) -> str | None:
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:n]
    except Exception:
        return None


def stamp() -> dict:
    """Identity of the running build. Keys are stable; values may be None."""
    try:
        from .version import __version__ as version
    except Exception:
        version = "?"
    frozen = bool(getattr(sys, "frozen", False))
    exe = sys.executable if frozen else None
    return {
        "version": version,
        "git": _BUILD.get("git", "source"),
        "dirty": bool(_BUILD.get("dirty", False)),
        "built_utc": _BUILD.get("built_utc"),
        "pyinstaller": _BUILD.get("pyinstaller"),
        "frozen": frozen,
        "exe": exe,
        "exe_sha256": _sha256_file(exe) if exe else None,
    }


def stamp_line() -> str:
    """One line for the top of a log: `build 1.3 @ 56863d2+dirty 2026-09-01T... exe-sha256 5d10971961`."""
    s = stamp()
    git = s["git"] + ("+dirty" if s["dirty"] else "")
    parts = [f"build {s['version']} @ {git}"]
    if s["built_utc"]:
        parts.append(str(s["built_utc"]))
    parts.append(f"exe-sha256 {s['exe_sha256']}" if s["exe_sha256"] else "source run")
    return " ".join(parts)


_TRUE = ("1", "true", "yes", "on")


def _effective_one(s, env: dict) -> object:
    """The value the converter uses for one registry row, from env + default."""
    if not s.env:
        return s.default
    raw = env.get(s.env)
    if raw is None or not str(raw).strip():
        return s.default
    if s.kind == "bool":
        on = str(raw).strip().lower() in _TRUE
        return (not on) if s.invert else on
    try:
        from .gui_settings import _coerce
        return _coerce(s, raw)
    except Exception:
        return raw


def effective_settings(env: dict | None = None) -> dict:
    """{key: effective value} for every registered setting."""
    from .gui_settings import SETTINGS
    env = os.environ if env is None else env
    return {s.key: _effective_one(s, env) for s in SETTINGS}


def non_default_settings(env: dict | None = None) -> dict:
    from .gui_settings import by_key
    reg = by_key()
    return {k: v for k, v in effective_settings(env).items()
            if v != reg[k].default}


def settings_file_status() -> dict:
    """Where the settings file is, whether it parsed, and its content hash."""
    try:
        from .gui_settings import config_path, load_status
        p = config_path()
        return {"path": str(p), "status": load_status(p),
                "sha256": _sha256_file(p) if p.is_file() else None}
    except Exception as e:
        return {"path": None, "status": f"unknown ({type(e).__name__})", "sha256": None}


def run_config() -> dict:
    """The whole attribution block. Never raises."""
    try:
        from . import paths
        mr = paths.mods_root()
    except Exception:
        mr = None
    try:
        eff = effective_settings()
        nd = non_default_settings()
    except Exception as e:
        eff, nd = {}, {"_error": f"{type(e).__name__}: {e}"}
    return {
        "build": stamp(),
        "settings_file": settings_file_status(),
        "mods_root": str(mr) if mr else None,
        "non_default": nd,
        "effective": eff,
        "env_overrides": {k: v for k, v in os.environ.items()
                          if k.startswith("CBBE2UBE_")},
    }


def echo_lines() -> list[str]:
    """The lines a run prints at its top, after the raw env echo."""
    out = [f"  {stamp_line()}"]
    sf = settings_file_status()
    st = sf.get("status")
    if st == "malformed":
        out.append(f"  SETTINGS FILE MALFORMED: {sf.get('path')} -- running at "
                   "DEFAULTS. Restore CBBEtoUBE_settings.json.bak before the "
                   "next save cements them.")
    try:
        nd = non_default_settings()
        if nd:
            out.append(f"  effective settings ({len(nd)} non-default): "
                       + ", ".join(f"{k}={v}" for k, v in sorted(nd.items())))
        else:
            out.append("  effective settings: all registry defaults")
    except Exception as e:
        out.append(f"  effective settings: unavailable ({type(e).__name__})")
    return out


def write_run_config(output_dir, name: str = "conversion_settings.json"):
    """Drop the attribution block next to conversion_summary.txt."""
    import json
    try:
        from .atomic_io import atomic_write_bytes
        p = Path(output_dir) / name
        atomic_write_bytes(p, json.dumps(run_config(), indent=2, default=str)
                           .encode("utf-8"))
        return p
    except Exception:
        return None
