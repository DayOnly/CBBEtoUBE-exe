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

"""Frozen-exe entry point for the CBBE/3BA -> UBE converter.

This is the script PyInstaller freezes into CBBEtoUBE.exe (see CBBEtoUBE.spec).
Run with no arguments it performs the one-click `auto` pipeline; it also
forwards any CLI subcommand (auto/convert/scan/discover-body-ref/merge/
validate/gui) straight through to src.auto_convert.main().

Two things here exist ONLY for the frozen build:

  1. multiprocessing.freeze_support() — the converter fans NIF conversion out
     across a ProcessPoolExecutor. On Windows (spawn) each worker re-launches
     this very executable; freeze_support() intercepts that re-launch in the
     child and runs the worker instead of falling through to main(). It MUST
     be the first thing called, before any pool is created.

  2. pause-on-finish — when launched by a double-click or via the MO2
     executable entry, a console window opens. We ALWAYS pause for a keypress
     before closing (success OR failure) so the run summary ("=== all clear ==="
     or the failure tally) stays readable. CBBE2UBE_NO_PAUSE=1 forces immediate
     auto-close (scripted/CI runs), and we never block when there's no
     interactive console to read a key from.
"""
import multiprocessing
import os
import sys

# MODULE LEVEL, and deliberately so: on Windows every pool worker RE-LAUNCHES
# this exe, and multiprocessing's spawn re-imports this module in the child
# before freeze_support() diverts it -- so running the cap here is what makes
# every worker inherit it, not just the process the user started. It must also
# be above any import that reaches numpy (`src.auto_convert` at _run(), line
# ~186), because the cap does nothing once numpy is loaded. #blas-thread-cap
from src.blas_env import cap_blas_threads

cap_blas_threads()


def _rotate_previous(path):
    """Move an existing log aside instead of letting "w" truncate it.

    THE RUN LOG IS THE ONLY ARTEFACT THAT SURVIVES A HARD KILL. It is
    line-buffered, so it holds everything up to the instant the process died --
    including the machine and memory-plan lines. Everything else a user could
    send (conversion_report.json, conversion_settings.json,
    conversion_summary.txt) is written at the END of a batch and simply does not
    exist after an out-of-memory death.

    And the advice the tool itself gives after such a death is "run again with
    fewer workers" -- which, with a truncating open, DESTROYED the only evidence
    of the failure it was reacting to. One rename fixes that. Best-effort: a
    failure here must never stop a run from starting. #commit-headroom"""
    try:
        if not os.path.exists(path):
            return
        # ONE NAME, BOTH LAUNCH PATHS. The GUI rotates to
        # "CBBEtoUBE_previous_run.log" and that is the name the changelog and
        # REPORTING tell a user to attach; a plain splitext form here would
        # produce "CBBEtoUBE_last_run_previous.log" instead, so whoever ran the
        # exe directly would end up with a file no document mentions. Swap
        # "_last_" for "_previous_" when it is there, and fall back to a suffix
        # for an arbitrary CBBE2UBE_RUN_LOG path.
        #
        # Split on the EXTENSION rather than str.replace(".log", ""): the path
        # comes from the user's own layout and a directory named e.g. "my.logs"
        # would be mangled by a blind replace.
        d, name = os.path.split(path)
        if "_last_" in name:
            prev = name.replace("_last_", "_previous_", 1)
        else:
            stem, ext = os.path.splitext(name)
            prev = stem + "_previous" + (ext or ".previous")
        os.replace(path, os.path.join(d, prev))
    except Exception:
        pass


# Kept alive for the life of the process so the tee target isn't GC'd /
# closed mid-run. Path is surfaced in _finish so the user can find the log.
_LOG_FILE = None
_LOG_PATH = None


class _Tee:
    """A write-through stream wrapper: everything written to the console also
    goes to the log file. Unknown attribute access (isatty, fileno, encoding,
    ...) delegates to the wrapped console stream so callers can't tell the
    difference."""

    def __init__(self, stream, logf):
        self._stream = stream
        self._logf = logf

    def write(self, s):
        if self._stream is not None:
            try:
                self._stream.write(s)
            except Exception:
                pass
        try:
            self._logf.write(s)
        except Exception:
            pass
        return len(s)

    def flush(self):
        for t in (self._stream, self._logf):
            if t is not None:
                try:
                    t.flush()
                except Exception:
                    pass

    def __getattr__(self, name):
        # Only reached for attrs not defined above; delegate to the console.
        return getattr(self._stream, name)


def _install_log_tee() -> None:
    """Mirror stdout+stderr into a log file so the run output survives even
    when the console window closes before the user can read it (the recurring
    MO2 auto-close). Written next to the exe (overwritten each run); falls
    back to the temp dir if that location isn't writable. Best-effort: any
    failure leaves the console streams untouched.

    Must run in the MAIN process only — call it AFTER freeze_support(), which
    intercepts worker re-launches before they reach here, so pool workers
    never open (and clobber) the log.
    """
    global _LOG_FILE, _LOG_PATH
    f = None
    # CBBE2UBE_RUN_LOG lets a parent (the GUI, which spawns this as a child
    # process) pin the log to a known path it can tail -- the reliable way to
    # capture a windowed-exe run's output, whose stdout is a null sink.
    _override = os.environ.get("CBBE2UBE_RUN_LOG", "").strip()
    # THE SETTINGS WINDOW IS NOT A RUN. #commit-headroom
    # With no subcommand (or `gui`) and no CBBE2UBE_RUN_LOG pinned, this process
    # is the GUI PARENT: it spawns a child to do the conversion, and that child
    # owns the run log. When the parent ALSO teed to CBBEtoUBE_last_run.log it
    # rotated the dead run aside at startup, and then the first Run click
    # rotated the parent's ~80 bytes of startup chatter OVER it -- destroying
    # the one artefact an out-of-memory death leaves behind, on exactly the path
    # the tool's own "run again with fewer workers" advice sends the user down.
    # So the parent keeps its own session log under a separate name and does not
    # rotate: it is not a run, and no document points a bug report at it.
    _gui_parent = (not _override
                   and (not sys.argv[1:] or sys.argv[1] == "gui"))
    _name = ("CBBEtoUBE_gui_session.log" if _gui_parent
             else "CBBEtoUBE_last_run.log")
    _paths = ([_override] if _override else [])
    _paths += [os.path.join(base, _name) for base in _log_dir_candidates()]
    for path in _paths:
        try:
            if not _gui_parent:
                _rotate_previous(path)
            f = open(path, "w", encoding="utf-8", buffering=1)  # line-buffered
            _LOG_PATH = path
            break
        except Exception:
            continue
    if f is None:
        return  # nowhere writable — leave console streams as-is
    _LOG_FILE = f
    try:
        sys.stdout = _Tee(sys.stdout, f)
        sys.stderr = _Tee(sys.stderr, f)
    except Exception:
        pass


def release_log_tee() -> bool:
    """Give up this process's claim on the run log. True if it had one.

    ONE FILE, ONE OWNER. The GUI process installs the tee at startup like any
    other, then spawns a CHILD to do the run and pins `CBBE2UBE_RUN_LOG` to the
    same path -- so two processes hold `"w"` handles on one file, each with its
    OWN write offset, and whichever writes second lands wherever its offset
    happens to be. Not theoretical: on the 2026-08-23 run it cut the startup
    flag echo mid-token --

        active flags (3): GLOW_LOG=...\\glowdebug.log, RUN  duplicate-plugin
        dedup: dropped 41 lower-priority source(s) ...

    -- and mangled the unseen-settings NOTE the same way, leaving only its tail
    ("once to record them."). Both diagnostics RAN correctly; the log destroyed
    the evidence. That is worse than cosmetic: `_echo_active_experiment_flags`
    exists precisely so "did my setting apply?" is a fact you can READ instead
    of infer, after an hour-long run was once spent on a flag that never got
    set. With the echo unreadable, the 08-23 run's configuration had to be
    established by arithmetic.

    The child does the run, so the child keeps the log; the GUI calls this
    before spawning it. Never raises -- losing the parent's tee must not be able
    to stop a run from starting.
    """
    global _LOG_FILE, _LOG_PATH
    if _LOG_FILE is None:
        return False
    for nm in ("stdout", "stderr"):
        inner = getattr(getattr(sys, nm, None), "_stream", None)
        if inner is not None:
            try:
                setattr(sys, nm, inner)
            except Exception:
                pass
    try:
        _LOG_FILE.close()
    except Exception:
        pass
    _LOG_FILE = None
    _LOG_PATH = None
    return True


def _log_dir_candidates():
    """Directories to try for the log file, best first."""
    out = []
    try:
        if getattr(sys, "frozen", False):
            out.append(os.path.dirname(sys.executable))
        else:
            out.append(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        pass
    try:
        import tempfile
        out.append(tempfile.gettempdir())
    except Exception:
        pass
    return out


def _run() -> int:
    # Imported lazily so freeze_support() runs first in worker processes.
    from src.auto_convert import main
    return main()


def _press_any_key(prompt: str) -> None:
    """Print `prompt` and wait for a single keypress, then a newline.

    Uses msvcrt.getch() for a true any-key wait on Windows (the frozen
    target platform); falls back to input() (Enter) elsewhere or if
    msvcrt is unavailable."""
    print(prompt, end="", flush=True)
    try:
        import msvcrt  # Windows only
        msvcrt.getch()
        print()  # move off the prompt line after the keypress
    except Exception:
        try:
            input()  # non-Windows / no msvcrt — wait for Enter instead
        except EOFError:
            pass


def _console_attached() -> bool:
    """True if a console WINDOW is attached to pause for. Uses the Win32
    console-window handle, which is present even when stdin is redirected
    (the MO2-launched case, where ``sys.stdin.isatty()`` is False and the old
    guard wrongly let the window auto-close). Falls back to the stdin tty
    check off-Windows / if ctypes is unavailable."""
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        try:
            return bool(sys.stdin is not None and sys.stdin.isatty())
        except Exception:
            return False


def _finish(rc: int) -> None:
    """Frozen-exe exit behavior: pause for a keypress on finish.

    Only relevant when frozen (a real console window is open). Prints a
    one-line status (success or failure) then waits for any key so the
    run summary stays on screen until the user dismisses it. Skipped when
    CBBE2UBE_NO_PAUSE is set (scripted/CI runs) or there's no interactive
    console to read a key from.
    """
    if not getattr(sys, "frozen", False):
        return
    if rc == 0:
        print("\nDone.")
    else:
        print("\nFinished with errors (see above).")
    if _LOG_PATH:
        print(f"(full log saved to: {_LOG_PATH})")
    if os.environ.get("CBBE2UBE_NO_PAUSE"):
        return
    # Pause whenever a console WINDOW is attached — not only when stdin is a
    # tty. MO2 launches the exe with a visible console but a redirected stdin,
    # so the old isatty() guard skipped the pause there and the window
    # auto-closed. _press_any_key uses msvcrt.getch(), which reads the console
    # directly and works under that redirection.
    try:
        if _console_attached():
            _press_any_key("\nPress any key to close...")
    except Exception:
        pass  # truly headless — nothing to wait on


if __name__ == "__main__":
    # Windowed (console=False) build: a frozen GUI app has sys.stdout/stderr
    # == None. ProcessPool workers re-launch this exe and freeze_support() runs
    # them WITHOUT reaching _install_log_tee, so guard the streams here (before
    # freeze_support) or a worker's progress/warning print() raises and kills
    # the conversion. No-op for the console build (streams already valid).
    for _nm in ("stdout", "stderr"):
        if getattr(sys, _nm, None) is None:
            try:
                setattr(sys, _nm, open(os.devnull, "w", encoding="utf-8"))
            except Exception:
                pass
    multiprocessing.freeze_support()
    # Tee console output to a log file (main process only — freeze_support
    # above already diverted any pool worker re-launch, so workers never reach
    # here to clobber the log). Robust fix for the run output being lost when
    # the console window closes before it can be read.
    _install_log_tee()
    rc = 0
    try:
        rc = _run()
    except KeyboardInterrupt:
        print("\ninterrupted.")
        rc = 130
    except Exception as e:  # surface a readable error before the pause
        import traceback
        traceback.print_exc()
        print(f"\nERROR: {e!r}")
        rc = 1
    _finish(rc)
    sys.exit(rc)
