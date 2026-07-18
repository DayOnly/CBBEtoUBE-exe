# P3 — Move runtime state out of the deploy dir (detailed design)

User/run state must not live in the artifact directory a redeploy overwrites. Fixes the
2026-07-09 loss of the user's `CBBEtoUBE_exclusions.json` to `robocopy /MIR`.

Status: DESIGN ONLY. Low risk / low effort. Safe to do independently of the other Ps.

---

## 1. Problem

Three pieces of runtime state each resolve to "next to the exe" via their OWN logic:

- `exclusions.config_path()` (`exclusions.py:50`) — the user's per-mod skip list. Its
  docstring says "Survives an exe redeploy", but that's only true for a file-by-file copy;
  a MIRROR copy (`robocopy /MIR`, or any deploy that deletes extras) removes it because it
  isn't in the build. That's how it was lost.
- `_last_failures_path()` (`auto_convert.py:753`) — `CBBEtoUBE_last_failures.json`.
- The `UNIFIED_COVERAGE` sentinel + `CBBEtoUBE_last_run.log` — also resolved next to the
  exe.

Three code paths, one fragile location. State that the USER owns (exclusions) and state
the RUN owns (failures/log) both sit where the next deploy can wipe them.

## 2. Approach

One helper, one stable location, used by all consumers:

    def _state_dir() -> Path:
        # 1. explicit override (CI / portable installs)
        ov = os.environ.get("CBBE2UBE_STATE_DIR", "").strip()
        if ov:
            return Path(ov)
        # 2. per-user app data (Windows: %LOCALAPPDATA%\CBBEtoUBE)
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            d = Path(base) / "CBBEtoUBE"
            try:
                d.mkdir(parents=True, exist_ok=True)
                return d
            except OSError:
                pass
        # 3. fallback: next to the exe / repo root (today's behaviour)
        return (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
                else Path(__file__).resolve().parent.parent)

`%LOCALAPPDATA%\CBBEtoUBE\` is per-user, outside the MO2 VFS, and untouched by any deploy.
The fallback keeps portable/locked-down installs working.

**One-time migration** (per file): on first access, if the file exists in the OLD (exe)
location but not the new `_state_dir()`, move it over. So an existing user's exclusions
follow them to the new home instead of silently resetting. Keep a READ fallback to the old
location for one release in case migration is skipped.

**Consumers to switch:** `exclusions.config_path` (keep its `CBBE2UBE_EXCLUSIONS`
per-file override as highest precedence), `_last_failures_path`, the `UNIFIED_COVERAGE`
sentinel search, and the run-log path. Each becomes `_state_dir() / "<name>"`.

## 3. Interactions

- **MO2 VFS**: MO2 launches the exe; writes next to the exe land in the REAL tools dir
  (observed this session — the files were there, not in Overwrite). `%LOCALAPPDATA%` is
  fully outside the VFS, so state is stable regardless of how MO2 mounts the tool.
- **Deploy scripts**: with state relocated, a deploy can safely mirror the build dir. Add a
  short note to the deploy step (and prefer `robocopy /E`, not `/MIR`, as belt-and-braces —
  see the deploy footnote below).
- **The GUI "reviewed exclusions this session?" gate** is in-memory (per the exclusions
  docstring), unaffected.

## 4. Test plan

- `_state_dir()`: `CBBE2UBE_STATE_DIR` override wins; `%LOCALAPPDATA%` chosen + created
  when writable; fallback to exe/repo when both env vars unset (monkeypatch `os.environ`).
- Migration: an old-location file is moved to `_state_dir()` on first access; no double-
  move; new-location file is preferred when both exist.
- Each consumer reads/writes under `_state_dir()`; the `CBBE2UBE_EXCLUSIONS` override still
  beats it.
- No golden impact (state paths don't touch mesh/ESP output).

## 5. Deploy footnote (do alongside)

Add `scripts/deploy_exe.ps1` that copies `dist\CBBEtoUBE` to the tools dir with `/E`
(copy, no delete) — NOT `/MIR` — so a redeploy never removes files the build doesn't ship.
Even after P3 moves user state away, `/E` is the safe default. (This session's loss was a
manual `/MIR`; a scripted `/E` deploy prevents a repeat.)

Effort: low. Risk: low (migration + read-fallback; output untouched).
