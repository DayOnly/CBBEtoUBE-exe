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

"""Optional Tkinter GUI for the CBBE/3BA -> UBE converter (the `gui` subcommand).

Thin + additive: it drives the SAME `auto_convert.main(["auto", ...])` pipeline
the CLI uses, on a BACKGROUND THREAD so the window stays responsive, streaming
the pipeline's stdout into a scrolling log via a thread-safe queue. The
conversion logic is unchanged; this is purely a front-end.

Two modes (radio):
  * "Convert all"  -> the normal full `auto` run (every armor mod).
  * "Convert selected mods" -> reconvert ONLY the ticked mods (fast), then the
    pipeline re-merges ALL patches in _unmerged_patches/ into the Combined ESP,
    so untouched mods keep their existing patch + meshes. Passes `--only-mods`.
    The mod checklist is populated by `auto_convert.list_convertible_mods()`
    (same discovery as `auto`, so the names match what `--only-mods` expects).

Threading model (important):
  * The converter is long-running and fans out across a ProcessPoolExecutor.
    Running it on the Tk main loop would freeze the window, so it runs on a
    daemon worker thread. Mod-list discovery (seconds) also runs on a daemon
    thread; both ENQUEUE results and update widgets only on the main thread.
  * Tk is NOT thread-safe: worker threads only ever ENQUEUE text + a final
    (DONE, exit_code) sentinel, or schedule a main-thread callback via
    root.after(0, ...). ALL widget updates happen on the main thread.
  * freeze_support() (in cbbe_to_ube_main, called first) still intercepts the
    ProcessPoolExecutor worker re-launches, so pool workers never reach the GUI.

tkinter is imported lazily INSIDE launch_gui(), so importing this module is
side-effect-free (no window) -- safe for tests / static analysis.
"""
from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

_DONE = object()  # sentinel; the worker pushes (_DONE, exit_code) when finished

# Where a diagnostics zip is meant to go. The exe is built without `ssl`, so it
# can't submit anything itself -- the intake is a human pasting into an issue,
# which only works if the address is in front of them at export time.
ISSUES_URL = "https://github.com/DayOnly/CBBEtoUBE-exe/issues"


def mod_name_matches(name: str, query: str) -> bool:
    """Case-insensitive, multi-token AND match for the checklist Filter box:
    every whitespace-separated token in `query` must appear in `name` (so
    typing the first word of a multi-word mod name narrows the list to
    matching mods). Empty query matches everything. Module-level (not a
    closure) so the filter contract is unit-testable."""
    q = query.strip().lower()
    if not q:
        return True
    low = name.lower()
    return all(tok in low for tok in q.split())


def _fmt_eta(seconds: float) -> str:
    """Human 'time left' string from a seconds estimate."""
    s = int(max(0, round(seconds)))
    if s < 1:
        return "finishing…"
    if s < 60:
        return f"~{s}s left"
    return f"~{s // 60}m {s % 60:02d}s left"


def _eta_step(eta: dict, done: int, total: int, now: float,
              alpha: float = 0.25) -> str:
    """Advance the per-mod ETA on a progress marker and return a 'time left'
    string. `eta` is mutated state {last_t, rate}. The rate is an EWMA of the
    time BETWEEN consecutive mod markers -- i.e. the actual per-mod conversion
    speed -- so it excludes the pre-conversion discovery/warmup and adapts as the
    speed changes. Module-level (not a closure) so it's unit-testable.

    Returns 'estimating…' until at least one inter-mod gap has been measured."""
    last = eta.get("last_t")
    if last is not None:
        gap = now - last
        if gap >= 0:                       # ignore a non-monotonic clock
            r = eta.get("rate")
            eta["rate"] = gap if r is None else alpha * gap + (1 - alpha) * r
    eta["last_t"] = now
    rate = eta.get("rate")
    remaining = max(0, total - done + 1)   # the in-progress mod + those after it
    if rate is None or remaining <= 0:
        return "estimating…"
    return _fmt_eta(rate * remaining)


def _kill_proc_tree(proc) -> None:
    """Terminate the conversion subprocess AND its ProcessPoolExecutor worker
    descendants. On Windows proc.terminate() kills only the DIRECT child, so the
    spawned workers (each a re-launched CBBEtoUBE.exe under spawn) are orphaned
    and sit alive forever, locking the exe. `taskkill /T /F` reaps the whole tree.
    Falls back to proc.terminate() elsewhere / on any failure."""
    if proc is None:
        return
    try:
        if os.name == "nt" and proc.poll() is None:
            # Fire-and-forget (Popen, not run): taskkill must NOT block the Tk UI
            # thread -- a slow kill would freeze the window on close/cancel. It's a
            # detached process, so it still reaps the whole tree even after the GUI
            # itself exits (Windows doesn't cascade-kill a parent's children).
            subprocess.Popen(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return
    except Exception:
        pass
    try:
        proc.terminate()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Colour palettes. Module level (not nested in launch_gui) so tests can import
# and check them without a display. Adding a palette here is the ONLY edit
# needed -- the picker derives its list from this dict.
#
# Every palette carries the same key set (see THEME_KEYS) and must stay legible:
# tests/test_gui_themes.py enforces WCAG contrast on the pairs that render text
# (fg/bg, logfg/logbg, onaccent/accent) plus weaker bars for the secondary ones.
#
# `onaccent` is the text drawn ON the accent (button labels). A DARK theme wants
# a bright accent, so `onaccent` goes near-black; a LIGHT theme wants a deeper
# accent with white on it. No single blue satisfies both -- the old shared
# #3b7dd8 gave white only 4.11 contrast, under the 4.5 AA bar.
THEME_KEYS = frozenset((
    "bg", "fg", "field", "hover", "border", "disabled", "hint", "tab",
    "accent", "accenthi", "onaccent", "logbg", "logfg", "labelfg", "tabselfg",
))

_THEMES = {
    "standard": {   # dark + gold (default)
        "bg": "#262421", "fg": "#e8e2d4", "field": "#34312b",
        "hover": "#403c33", "border": "#5c5445", "disabled": "#888274",
        "hint": "#b6a98c", "tab": "#302d27", "accent": "#c9a24b",
        "accenthi": "#e0b95e", "onaccent": "#241f16", "logbg": "#201e1a",
        "logfg": "#ddd3bf", "labelfg": "#d9b968", "tabselfg": "#e0b95e",
    },
    "light": {
        "bg": "#f4f4f3", "fg": "#1b1b1a", "field": "#ffffff",
        "hover": "#e7e7e6", "border": "#c9c9c7", "disabled": "#a6a6a3",
        "hint": "#6b6b6b", "tab": "#e4e4e2", "accent": "#3b7dd8",
        "accenthi": "#5591e6", "onaccent": "#ffffff", "logbg": "#ffffff",
        "logfg": "#1b1b1a", "labelfg": "#1b1b1a", "tabselfg": "#1b1b1a",
    },
    "dark": {
        "bg": "#2b2b2b", "fg": "#e8e8e8", "field": "#3c3f41",
        "hover": "#45494c", "border": "#565656", "disabled": "#7a7a7a",
        "hint": "#a0a0a0", "tab": "#333638", "accent": "#3b7dd8",
        "accenthi": "#5591e6", "onaccent": "#ffffff", "logbg": "#1e1e1e",
        "logfg": "#dcdcdc", "labelfg": "#e8e8e8", "tabselfg": "#e8e8e8",
    },
    "whispa": {     # silver ground + purple accent
        # A MID-TONE ground: plenty of headroom above it for dark text, very little
        # below. `hint` sinks to #5c5c5d to clear 3.0:1 and `disabled` lands at
        # 2.20:1 -- legible, but no room for a third muted tier. Lift `bg` toward
        # #d4d4d4 if another muted level is ever wanted. Surfaces are kept SILVER
        # rather than white: field/log sit just above the ground and the button
        # label is a silver-white, not #ffffff. The accent does real work here --
        # legend, selected tab, log highlight -- not just the button.
        "bg": "#c0c0c0", "fg": "#141416", "field": "#cfcfcf",
        "hover": "#b1b1b1", "border": "#888888", "disabled": "#7f7f7f",
        "hint": "#5c5c5d", "tab": "#b6b6b6", "accent": "#800080",
        "accenthi": "#a900a9", "onaccent": "#e8e8e8", "logbg": "#c9c9c9",
        "logfg": "#141416", "labelfg": "#800080", "tabselfg": "#800080",
    },
    "jbish": {      # black ground + dusty rose accent
        # On a PURE BLACK ground everything can only go lighter, so the structural
        # tones are set by hand rather than derived: `border` is lifted to #3d3d3d
        # (below that an edge simply doesn't read against black) and `logbg` to
        # #0a0a0a so the log reads as a pane instead of dissolving into the window.
        # The button label is near-BLACK, not white: white on this rose is only
        # 2.70:1, near-black is 7.20:1.
        "bg": "#000000", "fg": "#ffffff", "field": "#141414",
        "hover": "#1f1f1f", "border": "#3d3d3d", "disabled": "#616161",
        "hint": "#949494", "tab": "#0b0b0b", "accent": "#cd8d8d",
        "accenthi": "#ddb0b0", "onaccent": "#0d0d10", "logbg": "#0a0a0a",
        "logfg": "#ffffff", "labelfg": "#cd8d8d", "tabselfg": "#cd8d8d",
    },
}

# Display order + labels for the picker, derived so a new palette above needs no
# second edit here (the old hard-coded ("Standard","Light","Dark") tuple silently
# hid any theme you forgot to add to it).
THEME_NAMES = tuple(_THEMES)
THEME_LABELS = tuple(n.capitalize() for n in THEME_NAMES)


def launch_gui(argv=None, auto_close_ms=None, _smoke_settings=False) -> int:
    # auto_close_ms: test hook -- window auto-destroys after that many ms.
    # _smoke_settings: test hook -- open the settings dialog once, so a smoke
    # run exercises the registry-driven widget generation, then auto-closes.
    # None = normal interactive run.
    import tkinter as tk
    from tkinter import ttk, scrolledtext, filedialog, messagebox
    from . import auto_convert
    from . import gui_settings
    from . import exclusions as excl
    from . import preflight as pf
    from . import paths as _paths
    from . import report_template as _rt

    from .version import __version__ as _app_version

    root = tk.Tk()
    root.title(f"CBBE/3BA to UBE Converter  v{_app_version}")
    root.geometry("860x680")
    root.minsize(680, 520)

    q: "queue.Queue" = queue.Queue()
    state = {"running": False, "result": None, "output_dir": None,
             "_eta": {"last_t": None, "rate": None}}   # per-mod EWMA ETA state
    # Persisted feature/tuning/diagnostic settings (registry-driven). Loaded from
    # the settings JSON now; the tabbed settings UI (to come) edits these in place.
    state["settings"] = gui_settings.load_values()
    state["_setting_vars"] = []          # anchor tk vars for the settings tabs
    state["_setting_var_by_key"] = {}    # setting key -> its tk var (reset/import)
    state["_canvases"] = []              # scroll canvases to recolor on theme change
    # Persistent exclusions (mods never converted on an All-mods run) + the
    # per-SESSION "have I reviewed exclusions for this domain?" gate. An All-mods
    # domain can't convert until it's reviewed -- the un-ignorable safety net.
    state["exclusions"] = excl.load()
    state["reviewed"] = {"armor": False, "overlay": False}

    try:
        mods_root = _paths.mods_root()
    except Exception:
        mods_root = None
    default_out = str(mods_root / "CBBEtoUBE Auto") if mods_root else ""

    # ---- tk vars ----
    out_var = tk.StringVar(value=default_out)
    workers_var = tk.IntVar(value=max(1, (os.cpu_count() or 2) - 1))
    copy_tex = tk.BooleanVar(value=False)  # default: resolve textures via VFS
    dry = tk.BooleanVar(value=False)
    convert_armor = tk.BooleanVar(value=True)   # master toggle: convert armor mods
    mode = tk.StringVar(value="all")            # armor: "all" | "selected"
    convert_overlays = tk.BooleanVar(value=False)   # master toggle: convert overlays
    overlay_sel_mode = tk.StringVar(value="all")    # overlays: "all" | "selected"
    overlay_copy = tk.BooleanVar(value=False)       # add "UBE (name)" copies vs overwrite
    overlay_skip_male = tk.BooleanVar(value=True)   # skip male overlays (they don't convert reliably)
    overlay_mod_vars: "dict[str, tk.BooleanVar]" = {}  # overlay mod -> ticked
    merge_armors = tk.BooleanVar(value=True)        # merge patch ESPs into one Combined
    mod_vars: "dict[str, tk.BooleanVar]" = {}    # armor mod name -> checkbox var (PERSISTS across filtering)
    mod_checkboxes: list = []                    # Checkbutton widgets (toggled during a run)
    mod_cbs: "dict[str, object]" = {}            # mod name -> its Checkbutton (for show/hide on filter)
    mod_items_all: list = []                     # full unfiltered scan result (master order)
    search_var = tk.StringVar()                  # live filter text for the armor checklist
    # ---- overlay-mod checklist state (parallels the armor one above) ----
    ov_search_var = tk.StringVar()
    ov_items_all: list = []                      # full unfiltered overlay-mod scan
    ov_cbs: "dict[str, object]" = {}             # overlay mod name -> its Checkbutton

    # ---- mode/selection helpers (defined early; reference widgets created
    # below -- resolved at CALL time, never during construction) ----
    def _domain_ready(enabled, modevar, ticks, domain):
        # A domain is "ready to convert" when: disabled (doesn't block), or in
        # Select mode with >=1 mod ticked, or in All mode AFTER exclusions were
        # reviewed this session (the un-ignorable safety gate).
        if not enabled.get():
            return True
        if modevar.get() == "selected":
            return any(v.get() for v in ticks.values())
        return bool(state["reviewed"].get(domain))

    def _run_allowed():
        if not (convert_armor.get() or convert_overlays.get()):
            return False
        return (_domain_ready(convert_armor, mode, mod_vars, "armor")
                and _domain_ready(convert_overlays, overlay_sel_mode,
                                  overlay_mod_vars, "overlay"))

    def _refresh_run_button():
        if not state["running"]:
            run_btn.configure(state=("normal" if _run_allowed() else "disabled"))

    _OK_FG, _BAD_FG = "#4caf50", "#e0574f"    # green / red, readable on all themes

    def _sync_domain(enabled, modevar, box, exclframe, excllbl, domain):
        # In each Convert section: Select mode shows the mod checklist; All mode
        # shows the exclusions strip (which gates the run). Disabled -> hide both.
        box.pack_forget()
        exclframe.pack_forget()
        if not enabled.get():
            return
        if modevar.get() == "selected":
            box.pack(fill="both", expand=True, padx=6, pady=(2, 6))
        else:
            exclframe.pack(fill="x", padx=6, pady=(2, 6))
            n = len(state["exclusions"].get(domain, {}))
            if state["reviewed"].get(domain):
                excllbl.configure(
                    text=f"✓ reviewed — {n} excluded", foreground=_OK_FG)
            else:
                excllbl.configure(
                    text="⛔ review exclusions before converting",
                    foreground=_BAD_FG)

    def _sync_run(*_a):
        _sync_domain(convert_armor, mode, mods_box, armor_excl,
                     armor_excl_lbl, "armor")
        _sync_domain(convert_overlays, overlay_sel_mode, ov_box, overlay_excl,
                     overlay_excl_lbl, "overlay")
        _refresh_run_button()

    _matches = mod_name_matches   # module-level matcher (unit-tested)

    def _update_title():
        # Reflect filter/selection state in the frame label. Skipped during a run
        # (the label is commandeered to show "selection locked").
        if state["running"]:
            return
        total = len(mod_items_all)
        if total == 0:
            txt = "Mods to reconvert (Refresh, then tick)"
        else:
            q = search_var.get()
            shown = sum(1 for it in mod_items_all if _matches(it["name"], q))
            ticked = sum(1 for v in mod_vars.values() if v.get())
            hidden_ticked = sum(
                1 for it in mod_items_all
                if mod_vars.get(it["name"]) and mod_vars[it["name"]].get()
                and not _matches(it["name"], q))
            txt = f"Mods to reconvert: {shown}/{total} shown, {ticked} ticked"
            if hidden_ticked:
                txt += f"  ({hidden_ticked} ticked but hidden by filter)"
        try:
            mods_box.configure(text=txt)
        except Exception:
            pass
        _refresh_run_button()   # ticking a mod in Select mode gates Convert

    def _apply_filter():
        # Show/hide checkbuttons to match the filter WITHOUT touching mod_vars,
        # so ticks survive filtering. Re-pack matches in master order.
        q = search_var.get()
        for cb in mod_cbs.values():
            cb.pack_forget()
        for it in mod_items_all:
            if _matches(it["name"], q):
                mod_cbs[it["name"]].pack(anchor="w")
        try:
            _canvas.configure(scrollregion=_canvas.bbox("all"))
            _canvas.yview_moveto(0.0)
        except Exception:
            pass
        _update_title()

    def _set_all(val):
        # All/None act on the VISIBLE (filtered) set only -- so "kco" + All ticks
        # just that family. With an empty filter this is every mod, as before.
        q = search_var.get()
        for it in mod_items_all:
            name = it["name"]
            if _matches(name, q) and name in mod_vars:
                mod_vars[name].set(val)
        _update_title()

    def _populate_mods(items):
        if state["running"]:
            return     # a Refresh finished mid-run -> don't clobber "Converting..."
        for w in _inner.winfo_children():
            w.destroy()
        mod_vars.clear()
        mod_checkboxes.clear()
        mod_cbs.clear()
        mod_items_all[:] = items
        for it in items:
            v = tk.BooleanVar(value=False)
            mod_vars[it["name"]] = v
            cb = ttk.Checkbutton(_inner, variable=v, command=_update_title,
                                 text=f'{it["name"]}  ({it["nifs"]} nif)')
            mod_cbs[it["name"]] = cb
            mod_checkboxes.append(cb)
        _apply_filter()   # packs matches in order + sets scrollregion + title
        if items:
            status.set(f"{len(items)} convertible mods found. "
                       "Tick the ones to reconvert (or type in Filter to narrow).")
        else:
            status.set("No convertible mods found (or layout not detected).")

    def _refresh_mods():
        status.set("Scanning mods (this can take a few seconds)...")

        def work():
            try:
                od = out_var.get().strip()
                items = auto_convert.list_convertible_mods(
                    Path(od) if od else None)
            except Exception as e:
                items = []
                q.put(f"\n[mod scan failed: {e}]\n")
            root.after(0, lambda: _populate_mods(items))

        threading.Thread(target=work, daemon=True).start()

    # ---- overlay-mod checklist helpers (mirror the armor ones above) ----
    def _ov_update_title():
        if state["running"]:
            return
        total = len(ov_items_all)
        if total == 0:
            txt = "Which mods (none ticked = every mod)"
        else:
            qf = ov_search_var.get()
            shown = sum(1 for it in ov_items_all if _matches(it["name"], qf))
            ticked = sum(1 for v in overlay_mod_vars.values() if v.get())
            txt = f"Which mods: {shown}/{total} shown, {ticked} ticked (none = all)"
        try:
            ov_box.configure(text=txt)
        except Exception:
            pass
        _refresh_run_button()   # ticking an overlay mod gates Convert

    def _ov_apply_filter():
        qf = ov_search_var.get()
        for cb in ov_cbs.values():
            cb.pack_forget()
        for it in ov_items_all:
            if _matches(it["name"], qf):
                ov_cbs[it["name"]].pack(anchor="w")
        try:
            ov_canvas.configure(scrollregion=ov_canvas.bbox("all"))
            ov_canvas.yview_moveto(0.0)
        except Exception:
            pass
        _ov_update_title()

    def _ov_set_all(val):
        qf = ov_search_var.get()
        for it in ov_items_all:
            name = it["name"]
            if _matches(name, qf) and name in overlay_mod_vars:
                overlay_mod_vars[name].set(val)
        _ov_update_title()

    def _ov_populate(items):
        if state["running"]:
            return
        for w in ov_inner.winfo_children():
            w.destroy()
        overlay_mod_vars.clear()
        ov_cbs.clear()
        ov_items_all[:] = items
        for it in items:
            v = tk.BooleanVar(value=False)
            overlay_mod_vars[it["name"]] = v
            cb = ttk.Checkbutton(ov_inner, variable=v,
                                 command=_ov_update_title, text=it["name"])
            ov_cbs[it["name"]] = cb
        _ov_apply_filter()
        if items:
            status.set(f"{len(items)} overlay mods found. Tick which to convert "
                       "(or type in Filter to narrow).")
        else:
            status.set("No overlay mods found (or layout not detected).")

    def _ov_refresh():
        status.set("Scanning overlay mods (this can take a few seconds)...")

        def work():
            try:
                items = auto_convert.list_overlay_mods()
            except Exception as e:
                items = []
                q.put(f"\n[overlay mod scan failed: {e}]\n")
            root.after(0, lambda: _ov_populate(items))

        threading.Thread(target=work, daemon=True).start()

    # ---- theme (clam base so light AND dark recolor cleanly; the full palette
    #      is applied by _apply_theme() at the end, once every widget exists) ----
    style = ttk.Style()
    _BASE_FONT = ("Segoe UI", 10)
    try:
        style.theme_use("clam")
        root.option_add("*Font", _BASE_FONT)
    except Exception:
        pass

    # ---- top header: "Support me on Ko-fi" link, anchored top-right ----
    try:
        _header = ttk.Frame(root)
        _header.pack(side="top", fill="x", padx=8, pady=(6, 0))
        _kofi = ttk.Label(
            _header, text="Support me on Ko-fi", cursor="hand2",
            foreground="#29abe0",
            font=(_BASE_FONT[0], _BASE_FONT[1], "underline"))
        _kofi.pack(side="right")

        def _open_kofi(_e=None):
            import webbrowser
            try:
                webbrowser.open("https://ko-fi.com/daymodding")
            except Exception:
                pass
        _kofi.bind("<Button-1>", _open_kofi)
    except Exception:
        pass  # a header hiccup must never block the converter window

    # ---- theme control row: top-right, directly under the Ko-fi link ----
    _theme_row = ttk.Frame(root)
    _theme_row.pack(side="top", fill="x", padx=8, pady=(2, 0))

    # ---- notebook (tabs) + persistent bottom strip ----
    nb = ttk.Notebook(root)
    nb.pack(side="top", fill="both", expand=True, padx=8, pady=(8, 0))
    run_tab = ttk.Frame(nb)
    nb.add(run_tab, text="Run")
    # Armor / Overlays / Paths / Diagnostics tabs are added later, in order,
    # once their builder helpers exist (see the tab-assembly block below).

    bottom = ttk.Frame(root)
    bottom.pack(side="bottom", fill="both", expand=False)

    # ---- options (Run tab) -- WHAT to convert. The "how" (armor mesh fixes,
    #      overlay options) lives on the Armor / Overlays tabs. ----
    cfg = ttk.LabelFrame(run_tab, text="Run")
    cfg.pack(fill="x", padx=8, pady=(8, 4))
    cfg.columnconfigure(1, weight=1)

    ttk.Label(cfg, text="Output mod folder:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
    ttk.Entry(cfg, textvariable=out_var).grid(row=0, column=1, sticky="we", padx=4)

    def _browse():
        d = filedialog.askdirectory(initialdir=out_var.get() or ".")
        if d:
            out_var.set(d)

    ttk.Button(cfg, text="Browse...", command=_browse).grid(row=0, column=2, padx=4)

    ttk.Label(cfg, text="Worker processes:").grid(row=1, column=0, sticky="w", padx=4, pady=4)
    ttk.Spinbox(cfg, from_=1, to=(os.cpu_count() or 64),
                textvariable=workers_var, width=6).grid(row=1, column=1, sticky="w", padx=4)

    ttk.Checkbutton(cfg, text="Dry run (list mods, convert nothing)",
                    variable=dry).grid(row=2, column=1, sticky="w", padx=4, pady=(2, 4))

    # ---- registry-driven settings tabs -------------------------------------
    # The conversion-settings tabs (Armor / Paths / Diagnostics) are GENERATED
    # from the gui_settings registry and bound to state["settings"] (persisted on
    # every change; applied to the child env in _worker). Rendered as inline
    # notebook tabs alongside the Run tab (added after the log below).
    def _settings_set(key, value):
        state["settings"][key] = value
        try:
            gui_settings.save_values(state["settings"])
        except Exception:
            pass

    def _numset(key, var, kind):
        try:
            val = var.get()
        except Exception:
            return                      # mid-typing / invalid -> ignore
        _settings_set(key, int(round(val)) if kind == "int" else float(val))

    def _pick_path(var):
        d = filedialog.askopenfilename(initialdir=".")
        if d:
            var.set(d)

    def _bind_mousewheel(canvas):
        # Scroll the canvas with the mouse wheel while the pointer is over it.
        # A plain Canvas ignores the wheel; bind it app-wide on Enter (so the
        # event reaches us even over child widgets) and release it on Leave.
        def _on_wheel(e):
            try:
                canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
            except Exception:
                pass
        canvas.bind("<Enter>",
                    lambda e: canvas.bind_all("<MouseWheel>", _on_wheel))
        canvas.bind("<Leave>",
                    lambda e: canvas.unbind_all("<MouseWheel>"))

    _SEMI = ("Segoe UI Semibold", 10)

    def _apply_theme(mode):
        """Recolor the whole window for one of the themes in _THEMES. Uses the
        clam theme (the only built-in ttk theme that honours custom colours) plus
        explicit colours for the non-ttk widgets (log, scroll canvases)."""
        p = _THEMES.get(str(mode).lower(), _THEMES["standard"])
        try:
            style.theme_use("clam")
            # Strip the dashed focus ring the clam theme draws around a tab.
            style.layout("TNotebook.Tab", [
                ("Notebook.tab", {"sticky": "nswe", "children": [
                    ("Notebook.padding", {"side": "top", "sticky": "nswe",
                                          "children": [
                        ("Notebook.label", {"side": "top", "sticky": ""})]})]})])
        except Exception:
            pass
        try:
            root.configure(bg=p["bg"])
        except Exception:
            pass
        st = style
        st.configure(".", background=p["bg"], foreground=p["fg"],
                     fieldbackground=p["field"], bordercolor=p["border"],
                     lightcolor=p["bg"], darkcolor=p["bg"],
                     troughcolor=p["field"], insertcolor=p["fg"],
                     font=_BASE_FONT)
        st.configure("TFrame", background=p["bg"])
        st.configure("TLabel", background=p["bg"], foreground=p["fg"])
        st.configure("Hint.TLabel", background=p["bg"], foreground=p["hint"])
        st.configure("TLabelframe", background=p["bg"], bordercolor=p["border"])
        st.configure("TLabelframe.Label", background=p["bg"],
                     foreground=p["labelfg"], font=_SEMI)
        for w in ("TCheckbutton", "TRadiobutton"):
            st.configure(w, background=p["bg"], foreground=p["fg"])
            st.map(w, background=[("active", p["bg"])],
                   foreground=[("disabled", p["disabled"])])
        st.configure("TButton", background=p["field"], foreground=p["fg"],
                     bordercolor=p["border"], padding=(10, 4))
        st.map("TButton", background=[("active", p["hover"]),
                                      ("disabled", p["bg"])],
               foreground=[("disabled", p["disabled"])])
        st.configure("Accent.TButton", background=p["accent"],
                     foreground=p["onaccent"], font=_SEMI, padding=(16, 6))
        st.map("Accent.TButton", background=[("active", p["accenthi"]),
                                             ("disabled", p["border"])])
        for w in ("TEntry", "TSpinbox", "TCombobox"):
            st.configure(w, fieldbackground=p["field"], foreground=p["fg"],
                         insertcolor=p["fg"], arrowcolor=p["fg"],
                         bordercolor=p["border"],
                         selectbackground=p["field"],   # hide click-highlight
                         selectforeground=p["fg"])
        st.map("TCombobox",
               fieldbackground=[("readonly", p["field"])],
               foreground=[("readonly", p["fg"])],
               selectbackground=[("readonly", p["field"])],
               selectforeground=[("readonly", p["fg"])])
        st.configure("TNotebook", background=p["bg"], bordercolor=p["border"])
        st.configure("TNotebook.Tab", background=p["tab"], foreground=p["fg"],
                     padding=(16, 7))
        st.map("TNotebook.Tab", background=[("selected", p["bg"])],
               foreground=[("selected", p["tabselfg"])])
        st.configure("TProgressbar", background=p["accent"],
                     troughcolor=p["field"], bordercolor=p["border"])
        st.configure("Vertical.TScrollbar", background=p["field"],
                     troughcolor=p["bg"], arrowcolor=p["fg"],
                     bordercolor=p["border"])
        try:
            # Combobox dropdown is a Tk Listbox (unstyled by ttk); colour it too.
            root.option_add("*TCombobox*Listbox.background", p["field"])
            root.option_add("*TCombobox*Listbox.foreground", p["fg"])
            root.option_add("*TCombobox*Listbox.selectBackground", p["accent"])
            root.option_add("*TCombobox*Listbox.selectForeground", p["onaccent"])
        except Exception:
            pass
        try:
            log.configure(bg=p["logbg"], fg=p["logfg"],
                          insertbackground=p["logfg"])
        except Exception:
            pass
        # Prune canvases from closed dialogs so the list can't grow unbounded
        # across repeated dialog opens (each open appends one); recolor the live
        # ones. winfo_exists() is False for a destroyed widget.
        _live = []
        for c in state.get("_canvases", []):
            try:
                if not c.winfo_exists():
                    continue
                c.configure(bg=p["bg"])
                _live.append(c)
            except Exception:
                pass
        if "_canvases" in state:
            state["_canvases"][:] = _live

    def _apply_setting_values(vals):
        # Push a dict of values into the live settings vars (their write-traces
        # persist + apply). Used by reset-to-defaults and import.
        for k, var in state["_setting_var_by_key"].items():
            if k in vals:
                try:
                    var.set(vals[k])
                except Exception:
                    pass

    def _reset_settings():
        if not messagebox.askokcancel(
                "Reset settings",
                "Reset ALL conversion settings to their defaults?"):
            return
        _apply_setting_values(gui_settings.defaults())
        status.set("Settings reset to defaults.")

    def _export_settings():
        p = filedialog.asksaveasfilename(
            title="Export settings preset", defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile="CBBEtoUBE_settings.json")
        if not p:
            return
        try:
            gui_settings.save_values(state["settings"], p)
            status.set(f"Settings exported: {Path(p).name}")
        except Exception as e:
            messagebox.showerror("Export failed", str(e))

    def _import_settings():
        p = filedialog.askopenfilename(
            title="Import settings preset", filetypes=[("JSON", "*.json")])
        if not p:
            return
        try:
            vals = gui_settings.load_values(p)
        except Exception as e:
            messagebox.showerror("Import failed", str(e))
            return
        _apply_setting_values(vals)
        status.set(f"Settings imported: {Path(p).name}")

    def _build_settings_tab(container, tab, prefix=None):
        """Populate one notebook tab from the registry: a scrollable body of
        LabelFrame groups, each widget bound to state["settings"] and persisted
        on change. Shared by every settings tab (Armor/Paths/Diagnostics). An
        optional `prefix(body)` callback packs extra (hardcoded) widgets at the
        top of the same scroll body -- used for the Armor tab's Output & coverage
        section, which is CLI-flag driven rather than registry-driven."""
        cv = tk.Canvas(container, highlightthickness=0)
        sb = ttk.Scrollbar(container, orient="vertical", command=cv.yview)
        body = ttk.Frame(cv)
        body.bind("<Configure>",
                  lambda e, c=cv: c.configure(scrollregion=c.bbox("all")))
        cv.create_window((0, 0), window=body, anchor="nw")
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        _bind_mousewheel(cv)
        state["_canvases"].append(cv)
        if tab == "Armor":          # global settings actions (once, on the main tab)
            tb = ttk.Frame(body)
            tb.pack(fill="x", padx=10, pady=(8, 0))
            ttk.Button(tb, text="Reset to defaults",
                       command=_reset_settings).pack(side="left")
            ttk.Button(tb, text="Export…",
                       command=_export_settings).pack(side="left", padx=6)
            ttk.Button(tb, text="Import…",
                       command=_import_settings).pack(side="left")
        if prefix is not None:
            prefix(body)
        av = state["_setting_vars"]        # keep tk vars alive for the window
        for group in gui_settings.groups_in_tab(tab):
            gf = ttk.LabelFrame(body, text=group)
            gf.pack(fill="x", padx=10, pady=6)
            for s in gui_settings.settings_in(tab, group):
                cur = state["settings"].get(s.key, s.default)
                if s.kind == "bool":
                    var = tk.BooleanVar(value=bool(cur))
                    av.append(var)
                    state["_setting_var_by_key"][s.key] = var
                    ttk.Checkbutton(gf, text=s.label, variable=var).pack(
                        anchor="w", padx=8, pady=(4, 0))
                    var.trace_add("write", lambda *a, k=s.key, v=var:
                                  _settings_set(k, bool(v.get())))
                elif s.kind in ("float", "int"):
                    row = ttk.Frame(gf)
                    row.pack(fill="x", padx=8, pady=(4, 0))
                    ttk.Label(row, text=s.label, width=28,
                              anchor="w").pack(side="left")
                    var = tk.DoubleVar(value=float(cur))
                    av.append(var)
                    state["_setting_var_by_key"][s.key] = var
                    ttk.Spinbox(
                        row, textvariable=var, width=8,
                        from_=s.min if s.min is not None else 0,
                        to=s.max if s.max is not None else 1e9,
                        increment=s.step or 0.1).pack(side="left")
                    var.trace_add("write", lambda *a, k=s.key, v=var,
                                  kd=s.kind: _numset(k, v, kd))
                else:
                    row = ttk.Frame(gf)
                    row.pack(fill="x", padx=8, pady=(4, 0))
                    ttk.Label(row, text=s.label, width=28,
                              anchor="w").pack(side="left")
                    var = tk.StringVar(value=str(cur))
                    av.append(var)
                    state["_setting_var_by_key"][s.key] = var
                    ttk.Entry(row, textvariable=var).pack(
                        side="left", fill="x", expand=True, padx=(0, 4))
                    var.trace_add("write", lambda *a, k=s.key, v=var:
                                  _settings_set(k, v.get()))
                    if s.kind == "path":
                        ttk.Button(row, text="Browse", width=8,
                                   command=lambda v=var: _pick_path(v)).pack(
                                       side="left")
                if s.tooltip:
                    ttk.Label(gf, text=s.tooltip, style="Hint.TLabel",
                              wraplength=580, justify="left").pack(
                                  anchor="w", padx=26, pady=(0, 2))

    # ---- action bar (persistent, below the tabs) ----
    bar = ttk.Frame(bottom)
    bar.pack(fill="x", padx=8, pady=(8, 4))
    run_btn = ttk.Button(bar, text="Convert", style="Accent.TButton")
    run_btn.pack(side="left")
    cancel_btn = ttk.Button(bar, text="Cancel", state="disabled",
                            command=lambda: _cancel_run())
    cancel_btn.pack(side="left", padx=(6, 0))
    check_btn = ttk.Button(bar, text="Check setup",
                           command=lambda: _open_preflight())
    check_btn.pack(side="left", padx=6)
    open_out_btn = ttk.Button(bar, text="Open output folder", state="disabled")
    open_out_btn.pack(side="left", padx=6)
    open_rep_btn = ttk.Button(bar, text="Report",
                              command=lambda: _open_report())
    open_rep_btn.pack(side="left", padx=4)
    diag_btn = ttk.Button(bar, text="Export diagnostics",
                          command=lambda: _export_diagnostics())
    diag_btn.pack(side="left", padx=(12, 0))
    copy_rep_btn = ttk.Button(bar, text="Copy report",
                              command=lambda: _copy_report())
    copy_rep_btn.pack(side="left", padx=4)

    # theme selector (right-aligned)
    theme_var = tk.StringVar(
        value=str(state["settings"].get("theme", "standard")).capitalize())

    def _on_theme(*_a):
        m = theme_var.get().strip().lower()
        state["settings"]["theme"] = m
        try:
            gui_settings.save_values(state["settings"])
        except Exception:
            pass
        _apply_theme(m)
        try:
            _paint_swatch()          # keep the preview chip in sync with the theme
        except Exception:
            pass

    def _register_scroll_canvas(cv):
        """Track a scroll canvas for theme recolours AND paint it with the
        CURRENT palette immediately. Dialogs are created after startup, so
        registration alone leaves them at the Tk default (white) until the
        next theme switch — the white-list-on-dark-theme bug."""
        state["_canvases"].append(cv)
        try:
            p = _THEMES.get(theme_var.get().strip().lower(),
                            _THEMES["standard"])
            cv.configure(bg=p["bg"], highlightthickness=0)
        except Exception:
            pass

    def _theme_popup(win):
        """Paint a dialog Toplevel with the current palette. A raw
        tk.Toplevel keeps the system default (white) background —
        _apply_theme recolors ROOT, but dialogs are created later, so their
        white shows through every padding gap between the ttk children."""
        try:
            p = _THEMES.get(theme_var.get().strip().lower(),
                            _THEMES["standard"])
            win.configure(bg=p["bg"])
        except Exception:
            pass

    theme_cb = ttk.Combobox(_theme_row, textvariable=theme_var, width=11,
                            state="readonly", values=THEME_LABELS)
    theme_cb.pack(side="right", padx=(4, 0))
    # Live theme swatch: a small two-tone chip (bg + accent) that PREVIEWS the active
    # theme and repaints on switch. Font-independent "this control sets the appearance"
    # indicator, sitting between the "Theme:" label and the value. Clicking it opens the
    # dropdown so the whole cluster reads as one theme control.
    theme_swatch = tk.Canvas(_theme_row, width=24, height=14, bd=0,
                             highlightthickness=1, takefocus=0, cursor="hand2")

    def _paint_swatch(*_a):
        p = _THEMES.get(theme_var.get().strip().lower(), _THEMES["standard"])
        theme_swatch.configure(bg=p["bg"], highlightbackground=p["border"])
        theme_swatch.delete("all")
        theme_swatch.create_rectangle(0, 0, 12, 14, fill=p["bg"], outline="")
        theme_swatch.create_rectangle(12, 0, 24, 14, fill=p["accent"], outline="")

    theme_swatch.bind("<Button-1>", lambda e: (theme_cb.focus_set(),
                                               theme_cb.event_generate("<Down>")))
    theme_swatch.pack(side="right", padx=(6, 2))
    _paint_swatch()
    ttk.Label(_theme_row, text="Theme").pack(side="right", padx=(8, 4))
    theme_var.trace_add("write", _on_theme)
    # After a pick, drop focus + clear the text selection so it doesn't stay
    # highlighted.
    theme_cb.bind("<<ComboboxSelected>>",
                  lambda e: (theme_cb.selection_clear(), root.focus_set()))

    # ======================================================================
    # Run tab: two master sections -- Convert armor / Convert overlays. Each has
    # an enable checkbox, an All / Select-mods radio, and (when "Select mods") a
    # Refresh/All/None/Filter checklist that only appears while enabled+selected.
    # ======================================================================
    # ---- Convert armor ----
    armor_sec = ttk.LabelFrame(run_tab, text="Convert armor")
    armor_sec.pack(fill="x", padx=8, pady=(4, 0))
    ttk.Checkbutton(armor_sec, text="Convert armor mods to UBE",
                    variable=convert_armor, command=_sync_run).pack(
                        anchor="w", padx=6, pady=(4, 0))
    _arm_mode = ttk.Frame(armor_sec)
    _arm_mode.pack(anchor="w", padx=24, pady=(0, 2))
    arm_all_rb = ttk.Radiobutton(_arm_mode, text="All mods", value="all",
                                 variable=mode, command=_sync_run)
    arm_all_rb.pack(side="left")
    arm_sel_rb = ttk.Radiobutton(_arm_mode, text="Select mods", value="selected",
                                 variable=mode, command=_sync_run)
    arm_sel_rb.pack(side="left", padx=8)

    # Exclusions strip (shown in All mode; gates the run until reviewed).
    armor_excl = ttk.Frame(armor_sec)
    armor_excl_btn = ttk.Button(armor_excl, text="Exclusions…", width=13,
                                command=lambda: _open_exclusions("armor"))
    armor_excl_btn.pack(side="right", padx=(6, 0))
    armor_excl_lbl = ttk.Label(armor_excl, text="")
    armor_excl_lbl.pack(side="left", padx=(2, 0))

    mods_box = ttk.LabelFrame(armor_sec, text="Mods to reconvert (Refresh, then tick)")
    _topbar = ttk.Frame(mods_box)
    _topbar.pack(fill="x")
    refresh_btn = ttk.Button(_topbar, text="Refresh mod list", command=_refresh_mods)
    refresh_btn.pack(side="left", padx=4, pady=4)
    all_btn = ttk.Button(_topbar, text="All", width=4, command=lambda: _set_all(True))
    all_btn.pack(side="left")
    none_btn = ttk.Button(_topbar, text="None", width=5, command=lambda: _set_all(False))
    none_btn.pack(side="left", padx=(0, 4))
    ttk.Label(_topbar, text="Filter:").pack(side="left", padx=(8, 2))
    search_entry = ttk.Entry(_topbar, textvariable=search_var, width=20)
    search_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
    search_entry.bind("<Escape>", lambda e: search_var.set(""))   # Esc clears
    # Live filter as the user types. All/None then act on the visible subset.
    search_var.trace_add("write", lambda *a: _apply_filter())
    sel_widgets = [refresh_btn, all_btn, none_btn, search_entry,
                   arm_all_rb, arm_sel_rb, armor_excl_btn]   # locked during a run
    _cwrap = ttk.Frame(mods_box)
    _cwrap.pack(fill="both", expand=True)
    _canvas = tk.Canvas(_cwrap, height=140, highlightthickness=0)
    _sb = ttk.Scrollbar(_cwrap, orient="vertical", command=_canvas.yview)
    _inner = ttk.Frame(_canvas)
    _inner.bind("<Configure>",
                lambda e: _canvas.configure(scrollregion=_canvas.bbox("all")))
    _canvas.create_window((0, 0), window=_inner, anchor="nw")
    _canvas.configure(yscrollcommand=_sb.set)
    _canvas.pack(side="left", fill="both", expand=True)
    _sb.pack(side="right", fill="y")
    _bind_mousewheel(_canvas)
    state["_canvases"].append(_canvas)

    # ---- Convert overlays ----
    overlay_sec = ttk.LabelFrame(run_tab, text="Convert overlays")
    overlay_sec.pack(fill="x", padx=8, pady=(6, 0))
    ttk.Checkbutton(overlay_sec, text="Convert overlays to UBE (tattoos, body paint)",
                    variable=convert_overlays, command=_sync_run).pack(
                        anchor="w", padx=6, pady=(4, 0))
    _ov_modef = ttk.Frame(overlay_sec)
    _ov_modef.pack(anchor="w", padx=24, pady=(0, 2))
    ov_all_rb = ttk.Radiobutton(_ov_modef, text="All mods", value="all",
                                variable=overlay_sel_mode, command=_sync_run)
    ov_all_rb.pack(side="left")
    ov_sel_rb = ttk.Radiobutton(_ov_modef, text="Select mods", value="selected",
                                variable=overlay_sel_mode, command=_sync_run)
    ov_sel_rb.pack(side="left", padx=8)

    # Exclusions strip (shown in All mode; gates the run until reviewed).
    overlay_excl = ttk.Frame(overlay_sec)
    overlay_excl_btn = ttk.Button(overlay_excl, text="Exclusions…", width=13,
                                  command=lambda: _open_exclusions("overlay"))
    overlay_excl_btn.pack(side="right", padx=(6, 0))
    overlay_excl_lbl = ttk.Label(overlay_excl, text="")
    overlay_excl_lbl.pack(side="left", padx=(2, 0))

    ov_box = ttk.LabelFrame(overlay_sec, text="Which mods (none ticked = every mod)")
    ov_top = ttk.Frame(ov_box)
    ov_top.pack(fill="x")
    ov_refresh_btn = ttk.Button(ov_top, text="Refresh mod list", command=_ov_refresh)
    ov_refresh_btn.pack(side="left", padx=4, pady=4)
    ov_all_btn = ttk.Button(ov_top, text="All", width=4,
                            command=lambda: _ov_set_all(True))
    ov_all_btn.pack(side="left")
    ov_none_btn = ttk.Button(ov_top, text="None", width=5,
                             command=lambda: _ov_set_all(False))
    ov_none_btn.pack(side="left", padx=(0, 4))
    ttk.Label(ov_top, text="Filter:").pack(side="left", padx=(8, 2))
    ov_search = ttk.Entry(ov_top, textvariable=ov_search_var, width=20)
    ov_search.pack(side="left", fill="x", expand=True, padx=(0, 4))
    ov_search.bind("<Escape>", lambda e: ov_search_var.set(""))
    ov_search_var.trace_add("write", lambda *a: _ov_apply_filter())
    sel_widgets.extend([ov_refresh_btn, ov_all_btn, ov_none_btn, ov_search,
                        ov_all_rb, ov_sel_rb, overlay_excl_btn])
    ov_wrap = ttk.Frame(ov_box)
    ov_wrap.pack(fill="both", expand=True)
    ov_canvas = tk.Canvas(ov_wrap, height=140, highlightthickness=0)
    ov_sb = ttk.Scrollbar(ov_wrap, orient="vertical", command=ov_canvas.yview)
    ov_inner = ttk.Frame(ov_canvas)
    ov_inner.bind("<Configure>", lambda e: ov_canvas.configure(
        scrollregion=ov_canvas.bbox("all")))
    ov_canvas.create_window((0, 0), window=ov_inner, anchor="nw")
    ov_canvas.configure(yscrollcommand=ov_sb.set)
    ov_canvas.pack(side="left", fill="both", expand=True)
    ov_sb.pack(side="right", fill="y")
    _bind_mousewheel(ov_canvas)
    state["_canvases"].append(ov_canvas)

    prog = ttk.Progressbar(bottom, mode="indeterminate")
    prog.pack(fill="x", padx=8, pady=2)
    status = tk.StringVar(value="Idle. Pick options and press Convert.")
    ttk.Label(bottom, textvariable=status).pack(anchor="w", padx=8)

    log = scrolledtext.ScrolledText(bottom, height=12, wrap="word",
                                    state="disabled", font=("Consolas", 9))
    log.pack(fill="both", expand=True, padx=8, pady=(4, 8))

    def _build_overlays_tab(container):
        """The Overlays tab holds overlay conversion OPTIONS (the 'how'). Enable
        it and choose all/selected mods on the Run tab; this tab controls how the
        overlays are written (copy vs overwrite, skip male)."""
        f = ttk.Frame(container)
        f.pack(fill="both", expand=True, padx=8, pady=8)
        ttk.Label(f, text="Rebakes CBBE/3BA overlays onto the UBE body so "
                  "tattoos and paint line up. Turn it on from the Run tab. "
                  "No ESP -- loose textures only.", style="Hint.TLabel",
                  wraplength=560, justify="left").pack(anchor="w", padx=4,
                                                       pady=(0, 8))
        g2 = ttk.LabelFrame(f, text="Options")
        g2.pack(fill="x")
        ttk.Checkbutton(g2, text="Keep originals; add \"UBE (name)\" copies",
                        variable=overlay_copy).pack(anchor="w", padx=8,
                                                    pady=(6, 0))
        ttk.Label(g2, text="Lists UBE versions in RaceMenu instead of "
                  "overwriting, so non-UBE races keep theirs. Needs the "
                  "Papyrus compiler.", style="Hint.TLabel", wraplength=560,
                  justify="left").pack(anchor="w", padx=26, pady=(0, 4))
        ttk.Checkbutton(g2, text="Skip male overlays",
                        variable=overlay_skip_male).pack(anchor="w", padx=8,
                                                         pady=(2, 0))
        ttk.Label(g2, text="Male overlays don't convert reliably.",
                  style="Hint.TLabel", wraplength=560, justify="left").pack(
                      anchor="w", padx=26, pady=(0, 6))

    def _build_armor_output_section(body):
        """The Armor tab's top section: output + vanilla-coverage toggles moved
        out of the Run tab. These are CLI-flag driven (see _build_argv) and some
        only apply in a specific Run mode, so they stay hardcoded rather than
        joining the env-driven registry below."""
        gf = ttk.LabelFrame(body, text="Output")
        gf.pack(fill="x", padx=10, pady=6)
        ttk.Checkbutton(gf, text="Copy textures into the output folder",
                        variable=copy_tex).pack(anchor="w", padx=8, pady=(4, 0))
        ttk.Checkbutton(gf, text="Merge armors into one Combined ESP",
                        variable=merge_armors).pack(anchor="w", padx=8,
                                                    pady=(2, 4))

    # ---- tab assembly: Run (above) -> Armor -> Overlays -> Paths -> Diagnostics
    present = gui_settings.tabs_present()
    if "Armor" in present:
        armor_tab = ttk.Frame(nb)
        nb.add(armor_tab, text="Armor")
        _build_settings_tab(armor_tab, "Armor",
                            prefix=_build_armor_output_section)

    overlays_tab = ttk.Frame(nb)
    nb.add(overlays_tab, text="Overlays")
    _build_overlays_tab(overlays_tab)

    for _tab in ("Paths", "Diagnostics"):
        if _tab not in present:
            continue
        _tframe = ttk.Frame(nb)
        nb.add(_tframe, text=_tab)
        _build_settings_tab(_tframe, _tab)

    def _append(s):
        log.configure(state="normal")
        log.insert("end", s)
        log.see("end")
        log.configure(state="disabled")

    def _open_path(p):
        try:
            os.startfile(str(p))    # Windows shell open
        except Exception as e:
            _append(f"\n[could not open {p}: {e}]\n")

    def _open_exclusions(domain):
        """Modal editor: tick mods to EXCLUDE from an All-mods run of `domain`.
        Saving marks the domain reviewed (clears the Run-tab gate). Persists to
        exclusions.json. A name-based scan pre-ticks likely UBE-native mods."""
        pretty = "armor" if domain == "armor" else "overlay"
        win = tk.Toplevel(root)
        _theme_popup(win)
        win.title(f"Exclusions — {pretty} mods")
        win.transient(root)
        win.geometry("640x680")
        win.minsize(540, 500)
        try:
            win.grab_set()
        except Exception:
            pass
        ttk.Label(win, text=(
            "Tick mods to EXCLUDE from an All-mods run. Exclude any mod already "
            "built for UBE — converting it would double-convert and break it. "
            "Excluded mods keep their original meshes/overlays."),
            wraplength=470, justify="left").pack(anchor="w", padx=12, pady=(12, 6))

        vars_by_name: "dict[str, object]" = {}
        items_holder: list = []
        detected: "dict[str, dict]" = {}     # name -> {reason, source} to persist
        top = ttk.Frame(win)
        top.pack(fill="x", padx=12)
        note = ttk.Label(win, text="", style="Hint.TLabel", wraplength=470,
                         justify="left")

        # The mod lister + UBE mesh scan run in background threads and update
        # this modal's widgets via root.after. If the modal was closed
        # (Save/Cancel) before a scan finished, those widgets are gone ->
        # "invalid command name" TclError spam. Every deferred update guards on
        # the window still existing (checked on the main thread inside after()).
        def _cfg(widget, **kw):
            try:
                if win.winfo_exists() and widget.winfo_exists():
                    widget.configure(**kw)
            except tk.TclError:
                pass

        def _apply_flags(names):
            n = 0
            for name in names:
                if name in vars_by_name:
                    vars_by_name[name].set(True)
                    detected[name] = {"reason": excl.UBE_NATIVE_REASON,
                                      "source": "ube-auto"}
                    _auto_tagged.add(name)
                    n += 1
            if n:
                _render()      # show the ✦ tags + refresh the ticked count
            return n

        def _scan():
            if domain == "armor":
                # Deep, mesh-based: opens NIFs and checks for UBE/advanced-body
                # scale bones. Slow -> background thread with progress.
                scan_btn.configure(state="disabled")
                note.configure(text="Scanning meshes for UBE bones… (opens NIFs)")

                def _done(res):
                    if not win.winfo_exists():
                        return                    # modal closed mid-scan
                    scan_btn.configure(state="normal")
                    ube = [r for r in res if r["verdict"] == "ube"]
                    unknown = sum(1 for r in res if r["verdict"] == "unknown")
                    n = _apply_flags([r["name"] for r in ube])
                    msg = (f"Detected {n} mod(s) shaped for UBE — pre-ticked. "
                           "Review + Save." if n
                           else "No UBE-shaped mods detected.")
                    if unknown:
                        msg += (f"  ({unknown} couldn't be classified from their "
                                "meshes — tick manually if you know they're UBE.)")
                    note.configure(text=msg)

                def work():
                    def _prog(done, total, name):
                        root.after(0, lambda d=done, t=total: _cfg(
                            note, text=f"Scanning meshes… {d}/{t}"))
                    try:
                        res = auto_convert.scan_ube_native("armor",
                                                           progress=_prog)
                    except Exception as e:
                        res = []
                        q.put(f"\n[UBE mesh scan failed: {e}]\n")
                    root.after(0, lambda: _done(res))

                threading.Thread(target=work, daemon=True).start()
            else:
                # Overlays have no meshes -> name heuristic only.
                already = [n for n, v in vars_by_name.items() if v.get()]
                props = excl.scan_names([it["name"] for it in items_holder],
                                        existing=already)
                n = _apply_flags([p["name"] for p in props])
                note.configure(text=(
                    f"Name scan flagged {n} mod(s) — review and confirm."
                    if n else "Name scan found no obvious UBE overlays."))

        scan_btn = ttk.Button(
            top, text=("Scan meshes for UBE-native" if domain == "armor"
                       else "Scan for UBE-native (by name)"), command=_scan)
        scan_btn.pack(side="left")
        note.pack(anchor="w", padx=12, pady=(4, 0))

        # Search + bulk-tick controls. Filtering only changes which rows are
        # SHOWN; ticks on hidden rows are preserved (vars live in vars_by_name).
        ctl = ttk.Frame(win)
        ctl.pack(fill="x", padx=12, pady=(6, 0))
        ttk.Label(ctl, text="Filter:").pack(side="left")
        filter_var = tk.StringVar(value="")
        filter_entry = ttk.Entry(ctl, textvariable=filter_var, width=28)
        filter_entry.pack(side="left", padx=(4, 10))
        tick_shown_btn = ttk.Button(ctl, text="Tick shown", state="disabled")
        tick_shown_btn.pack(side="left")
        untick_shown_btn = ttk.Button(ctl, text="Untick shown",
                                      state="disabled")
        untick_shown_btn.pack(side="left", padx=(6, 0))
        count_lbl = ttk.Label(ctl, text="", style="Hint.TLabel")
        count_lbl.pack(side="right")

        btns = ttk.Frame(win)
        btns.pack(side="bottom", fill="x", padx=12, pady=(0, 10))
        status_lbl = ttk.Label(
            win, text="Scanning mods… (the first scan walks the whole "
                      "modlist — this can take a while on big lists)")
        status_lbl.pack(side="bottom", anchor="w", padx=12, pady=(0, 4))

        mid = ttk.Frame(win)
        mid.pack(fill="both", expand=True, padx=(12, 0), pady=8)
        cvs = tk.Canvas(mid, highlightthickness=0)
        sb = ttk.Scrollbar(mid, orient="vertical", command=cvs.yview)
        inner = ttk.Frame(cvs)
        inner.bind("<Configure>",
                   lambda e: cvs.configure(scrollregion=cvs.bbox("all")))
        cvs.create_window((0, 0), window=inner, anchor="nw")
        cvs.configure(yscrollcommand=sb.set)
        cvs.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        _bind_mousewheel(cvs)
        _register_scroll_canvas(cvs)

        _auto_tagged: "set[str]" = set()

        def _update_count():
            ticked = sum(1 for v in vars_by_name.values() if v.get())
            _cfg(count_lbl, text=f"{ticked}/{len(vars_by_name)} excluded")

        def _shown_names():
            f = filter_var.get().strip().lower()
            return [it["name"] for it in items_holder
                    if not f or f in it["name"].lower()]

        def _render():
            if not win.winfo_exists():
                return
            for w in inner.winfo_children():
                w.destroy()
            shown = _shown_names()
            for name in shown:
                v = vars_by_name[name]
                row = ttk.Frame(inner)
                row.pack(anchor="w", fill="x")
                ttk.Checkbutton(row, text=name, variable=v,
                                command=_update_count).pack(
                    side="left", anchor="w")
                if name in _auto_tagged:
                    ttk.Label(row, text="✦ UBE-native",
                              style="Hint.TLabel").pack(side="left", padx=(6, 0))
            f = filter_var.get().strip()
            status_lbl.configure(text=(
                (f"{len(shown)}/{len(items_holder)} mods shown"
                 + (f" (filter: {f!r})" if f else "")
                 + ". Tick any to exclude, then Save.")
                if items_holder else "No mods found (layout not detected?)."))
            cvs.yview_moveto(0.0)
            _update_count()

        def _bulk(value):
            for name in _shown_names():
                vars_by_name[name].set(value)
            _update_count()

        def _populate(items):
            if not win.winfo_exists():
                return                            # modal closed before list ready
            # The vanilla sweep pseudo-source has its own Armor-tab toggle and
            # is not gated by this list — showing it here would be a control
            # that lies. (CLI --exclude-mods vanilla IS honored.)
            items = [it for it in items
                     if str(it.get("name", "")).lower() != "vanilla"]
            vars_by_name.clear()
            _auto_tagged.clear()
            items_holder[:] = items
            pre = dict(state["exclusions"].get(domain, {}))
            for it in items:
                name = it["name"]
                vars_by_name[name] = tk.BooleanVar(value=(name in pre))
                if str((pre.get(name) or {}).get("source", "")).endswith("auto"):
                    _auto_tagged.add(name)
            tick_shown_btn.configure(state="normal",
                                     command=lambda: _bulk(True))
            untick_shown_btn.configure(state="normal",
                                       command=lambda: _bulk(False))
            filter_var.trace_add("write", lambda *_a: _render())
            _render()

        def _save():
            # Update the decision only for mods SHOWN this session; preserve any
            # existing exclusion whose mod wasn't listed (empty/failed scan, or a
            # mod not in this domain's list) so it can't be silently dropped.
            shown = set(vars_by_name)
            chosen_shown = {n for n, v in vars_by_name.items() if v.get()}
            prior = set(excl.excluded_names(state["exclusions"], domain))
            final = (prior - shown) | chosen_shown
            excl.set_excluded(state["exclusions"], domain, final)
            # Tag detector-flagged (and still-ticked) mods with the ✦ reason/source.
            for name, meta in detected.items():
                if name in chosen_shown:
                    state["exclusions"][domain][name] = dict(meta)
            excl.save(state["exclusions"])
            state["reviewed"][domain] = True     # clears the Run-tab gate
            _sync_run()
            win.destroy()

        ttk.Button(btns, text="Save & mark reviewed", style="Accent.TButton",
                   command=_save).pack(side="right")
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(
            side="right", padx=6)

        lister = (auto_convert.list_convertible_mods if domain == "armor"
                  else auto_convert.list_overlay_mods)

        def work():
            try:
                if domain == "armor":
                    # Live progress into the status line (first scan walks the
                    # whole modlist; without this the dialog looks hung).
                    items = lister(progress=lambda t: root.after(
                        0, lambda s=t: _cfg(status_lbl, text=s)))
                else:
                    items = lister()
            except Exception as e:
                items = []
                q.put(f"\n[exclusion scan failed: {e}]\n")
            root.after(0, lambda: _populate(items))

        threading.Thread(target=work, daemon=True).start()

    # ---- preflight ("Check setup") ----
    _PF_COLOR = {"ok": "#4caf50", "warn": "#e0a030", "fail": "#e0574f"}
    _PF_ICON = {"ok": "✓", "warn": "!", "fail": "✕"}

    def _run_preflight(callback):
        def work():
            try:
                checks = pf.run_checks(
                    want_overlays=convert_overlays.get(),
                    want_overlay_copy=(convert_overlays.get()
                                       and overlay_copy.get()))
            except Exception as e:
                checks = None
                q.put(f"\n[setup check failed: {e}]\n")
            root.after(0, lambda: callback(checks))
        threading.Thread(target=work, daemon=True).start()

    def _pf_auto(checks):
        if not checks:
            return
        ov = pf.overall(checks)
        state["preflight"] = ov
        if ov == "fail":
            status.set("Setup check found problems — click 'Check setup' "
                       "before converting.")
        elif ov == "warn":
            status.set("Setup check: warnings — click 'Check setup' to review.")

    def _open_preflight():
        win = tk.Toplevel(root)
        _theme_popup(win)
        win.title("Check setup")
        win.transient(root)
        win.geometry("580x540")
        try:
            win.grab_set()
        except Exception:
            pass
        head = ttk.Label(win, text="Checking your setup…", font=_SEMI)
        head.pack(anchor="w", padx=12, pady=(12, 6))
        mid = ttk.Frame(win)
        mid.pack(fill="both", expand=True, padx=(12, 0))
        cvs = tk.Canvas(mid, highlightthickness=0)
        sb = ttk.Scrollbar(mid, orient="vertical", command=cvs.yview)
        inner = ttk.Frame(cvs)
        inner.bind("<Configure>",
                   lambda e: cvs.configure(scrollregion=cvs.bbox("all")))
        cvs.create_window((0, 0), window=inner, anchor="nw")
        cvs.configure(yscrollcommand=sb.set)
        cvs.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        _bind_mousewheel(cvs)
        _register_scroll_canvas(cvs)
        btns = ttk.Frame(win)
        btns.pack(side="bottom", fill="x", padx=12, pady=8)

        def _render(checks):
            if not win.winfo_exists():
                return                            # modal closed before checks done
            for w in inner.winfo_children():
                w.destroy()
            if not checks:
                ttk.Label(inner, text="Couldn't run the setup checks.").pack(
                    anchor="w")
                head.configure(text="Check setup")
                return
            ov = pf.overall(checks)
            state["preflight"] = ov
            head.configure(text={"ok": "Setup looks good",
                                 "warn": "Setup OK — with warnings",
                                 "fail": "Setup problems found"}[ov],
                           foreground=_PF_COLOR[ov])
            for c in checks:
                row = ttk.Frame(inner)
                row.pack(anchor="w", fill="x", pady=(6, 0))
                ttk.Label(row, text=_PF_ICON[c.status], width=2,
                          foreground=_PF_COLOR[c.status]).pack(side="left")
                ttk.Label(row, text=c.label, font=_SEMI).pack(side="left")
                if c.detail:
                    ttk.Label(inner, text=c.detail, style="Hint.TLabel",
                              wraplength=520, justify="left").pack(
                                  anchor="w", padx=26)
                if c.status != "ok" and c.fix:
                    ttk.Label(inner, text="Fix: " + c.fix, wraplength=520,
                              justify="left", foreground=_PF_COLOR[c.status]).pack(
                                  anchor="w", padx=26, pady=(0, 2))

        def _load():
            for w in inner.winfo_children():
                w.destroy()
            head.configure(text="Checking your setup…")
            _run_preflight(_render)

        ttk.Button(btns, text="Re-check", command=_load).pack(side="left")
        ttk.Button(btns, text="Close", command=win.destroy).pack(side="right")
        _load()

    def _cancel_run():
        proc = state.get("proc")
        if proc is None:
            return
        _kill_proc_tree(proc)     # tree-kill: reap the worker pool, not just the parent
        status.set("Cancelling… the running worker will stop shortly.")
        cancel_btn.configure(state="disabled")

    def _diag_done(zpath, err):
        if not root.winfo_exists():
            return   # app quit while the daemon export thread was still running
        if err:
            status.set("Diagnostics export failed — see log.")
            _append(f"\n[diagnostics export failed: {err}]\n")
            return
        status.set(f"Diagnostics written: {zpath.name}")
        _append(f"\n[diagnostics written: {zpath}]\n")
        # The zip is useless if nobody knows where to send it, and this message
        # is the only thing a user sees after the export.
        _append(f"  attach it to an issue at {ISSUES_URL}\n"
                "  or upload it to the chat channel with the report from\n"
                "  the 'Copy report' button -- REPORT.txt inside the zip is\n"
                "  the same text.\n"
                "  it holds your MO2 paths, profile name, and load-order mod\n"
                "  names -- look it over before posting it publicly.\n")
        try:
            _open_path(zpath.parent)
        except Exception:
            pass

    def _read_conversion_report():
        """conversion_report.json for the current output mod, or None.

        Best-effort by design: a report is worth sending even when no run has
        happened yet, so a missing file must not block the paste.
        """
        import json as _j
        od = (state.get("output_dir") or out_var.get().strip() or default_out)
        try:
            p = Path(od) / "conversion_report.json"
            if p.is_file():
                return _j.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
        return None

    def _copy_report():
        """Put a filled-in problem report on the clipboard.

        The chat-paste half of the intake. Everything the tool already knows is
        filled in, so what is left is only what the user alone can answer.
        """
        text = _rt.build_report(_app_version, kind="conversion",
                                report=_read_conversion_report())
        try:
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update_idletasks()   # flush the write before we report success
        except Exception as e:
            status.set("Could not reach the clipboard — see log.")
            _append(f"\n[clipboard failed: {e}]\n")
            _append(text + "\n")      # still give them something to select
            return
        status.set("Report copied — paste it into the chat or an issue.")
        _append("\n[report template copied to clipboard]\n" + text + "\n")

    def _export_diagnostics():
        """Zip the run log + settings + exclusions + a layout snapshot + a fresh
        preflight into one file, for sharing when asking for help."""
        import json
        import time as _t
        import zipfile
        out = out_var.get().strip() or default_out
        base = Path(out) if (out and Path(out).parent.exists()) else Path.home()
        zpath = base / f"CBBEtoUBE_diagnostics_{_t.strftime('%Y%m%d-%H%M%S')}.zip"
        status.set("Collecting diagnostics…")
        try:
            gui_log = log.get("1.0", "end")
        except Exception:
            gui_log = ""
        # Built on the main thread: it reads Tk vars. The zip carries its own
        # cover sheet so a bare "here's my zip" hand-off still says what broke.
        try:
            cover = _rt.build_report(_app_version, kind="conversion",
                                     report=_read_conversion_report(),
                                     diagnostics_zip=zpath.name)
        except Exception:
            cover = ""

        def work():
            err = None
            try:
                with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
                    if cover:
                        z.writestr("REPORT.txt", cover)
                    z.writestr("gui_log.txt", gui_log)
                    for label, p in (("settings.json", gui_settings.config_path()),
                                     ("exclusions.json", excl.config_path())):
                        try:
                            if Path(p).is_file():
                                z.write(str(p), label)
                        except Exception:
                            pass
                    try:
                        lay = _paths.discover_layout()
                        z.writestr("layout.json", json.dumps({
                            "mods_root": str(lay.mods_root),
                            "profile": lay.selected_profile,
                            "game_data": [str(d) for d in
                                          (lay.game_data_dirs or [])]}, indent=2))
                    except Exception:
                        pass
                    try:
                        checks = pf.run_checks()
                        z.writestr("preflight.txt", "\n".join(
                            f"[{c.status}] {c.label}: {c.detail}"
                            + (f"  | fix: {c.fix}" if c.fix else "")
                            for c in checks))
                    except Exception:
                        pass
            except Exception as e:
                err = e
            root.after(0, lambda: _diag_done(zpath, err))

        threading.Thread(target=work, daemon=True).start()

    def _open_report(output_dir=None):
        """Post-run health panel: a scoreboard read from conversion_report.json,
        with a 'Re-verify output' button that re-runs the invisibility-risk scan
        against the current meshes without reconverting."""
        import json as _json
        od = (output_dir or state.get("output_dir")
              or out_var.get().strip() or default_out)
        win = tk.Toplevel(root)
        _theme_popup(win)
        win.title("Conversion report")
        win.transient(root)
        win.geometry("580x540")
        try:
            win.grab_set()
        except Exception:
            pass
        head = ttk.Label(win, text="Conversion report", font=_SEMI)
        head.pack(anchor="w", padx=12, pady=(12, 4))
        mid = ttk.Frame(win)
        mid.pack(fill="both", expand=True, padx=(12, 0))
        cvs = tk.Canvas(mid, highlightthickness=0)
        sb = ttk.Scrollbar(mid, orient="vertical", command=cvs.yview)
        content = ttk.Frame(cvs)
        content.bind("<Configure>",
                     lambda e: cvs.configure(scrollregion=cvs.bbox("all")))
        cvs.create_window((0, 0), window=content, anchor="nw")
        cvs.configure(yscrollcommand=sb.set)
        cvs.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        _bind_mousewheel(cvs)
        _register_scroll_canvas(cvs)
        btns = ttk.Frame(win)
        btns.pack(side="bottom", fill="x", padx=12, pady=8)

        def _render(data):
            if not win.winfo_exists():
                return                            # report modal closed mid-verify
            for w in content.winfo_children():
                w.destroy()
            rep = (data or {}).get("report")
            if rep is None:
                try:
                    rj = Path(od) / "conversion_report.json"
                    if rj.is_file():
                        rep = _json.loads(rj.read_text(encoding="utf-8"))
                except Exception:
                    rep = None
            if not rep:
                ttk.Label(content, text="No conversion_report.json yet — run a "
                          "conversion first.", style="Hint.TLabel",
                          wraplength=520, justify="left").pack(anchor="w", pady=8)
                return
            wp = (data or {}).get("weight_partner_warnings")
            if wp is None:
                wp = rep.get("weight_partner_warnings", [])
            rows = [
                ("Source mods", rep.get("source_mods", 0), "ok"),
                ("Converted OK", rep.get("converted_ok", 0), "ok"),
                ("Hard failures", rep.get("hard_failures", 0),
                 "fail" if rep.get("hard_failures") else "ok"),
                ("Armor NIFs written", rep.get("armor_nifs", 0), "ok"),
                ("ESP patches", rep.get("esp_patches", 0), "ok"),
                ("NIF errors", rep.get("nif_errors", 0),
                 "warn" if rep.get("nif_errors") else "ok"),
                ("Zero-mesh (likely missing)",
                 len(rep.get("zero_mesh_mods", [])),
                 "warn" if rep.get("zero_mesh_mods") else "ok"),
                ("Invisibility risk (weight-partner)", len(wp),
                 "warn" if wp else "ok"),
            ]
            for label, val, st in rows:
                row = ttk.Frame(content)
                row.pack(anchor="w", fill="x", pady=(4, 0))
                ttk.Label(row, text=_PF_ICON.get(st, ""), width=2,
                          foreground=_PF_COLOR.get(st)).pack(side="left")
                ttk.Label(row, text=f"{label}: ", font=_SEMI).pack(side="left")
                ttk.Label(row, text=str(val)).pack(side="left")
            for title, names in (
                    ("Likely-missing mods", rep.get("zero_mesh_mods", [])),
                    ("Failed mods",
                     [m.get("name") for m in rep.get("failed_mods", [])]),
                    ("Weight-partner divergence", list(wp))):
                if names:
                    ttk.Label(content, text=title + ":", font=_SEMI).pack(
                        anchor="w", padx=6, pady=(8, 0))
                    for n in names[:20]:
                        ttk.Label(content, text="  • " + str(n),
                                  style="Hint.TLabel", wraplength=520,
                                  justify="left").pack(anchor="w", padx=6)
                    if len(names) > 20:
                        ttk.Label(content, text=f"  … and {len(names) - 20} more",
                                  style="Hint.TLabel").pack(anchor="w", padx=6)

        def _verify():
            head.configure(text="Re-verifying output…")
            for w in content.winfo_children():
                w.destroy()

            def work():
                try:
                    data = auto_convert.verify_output(od)
                except Exception as e:
                    data = None
                    q.put(f"\n[verify output failed: {e}]\n")
                def _fin():
                    _render(data)                 # guards on win existing
                    if win.winfo_exists():
                        head.configure(text="Conversion report")
                root.after(0, _fin)
            threading.Thread(target=work, daemon=True).start()

        ttk.Button(btns, text="Re-verify output", command=_verify).pack(
            side="left")
        _sumtxt = Path(od) / "conversion_summary.txt"
        if _sumtxt.is_file():
            ttk.Button(btns, text="Open summary (.txt)",
                       command=lambda: _open_path(_sumtxt)).pack(side="left",
                                                                 padx=6)
        ttk.Button(btns, text="Close", command=win.destroy).pack(side="right")
        _render(None)

    def _build_argv():
        a = ["auto"]
        if out_var.get().strip():
            a += ["-o", out_var.get().strip()]
        a += ["--workers", str(int(workers_var.get()))]
        if copy_tex.get():
            a.append("--copy-textures")
        if not merge_armors.get():
            a.append("--no-auto-merge")
        if dry.get():
            a.append("--list-only")
        do_armor = convert_armor.get()
        do_overlay = convert_overlays.get()
        # Armor + overlay combination -> the pipeline's mode flag.
        #   overlays only  -> --overlays-only (early-returns before armor work)
        #   both           -> --convert-overlays (armor + overlays)
        #   armor only     -> neither flag (the default `auto` behaviour)
        if do_overlay and not do_armor:
            a.append("--overlays-only")
        elif do_overlay and do_armor:
            a.append("--convert-overlays")
        # Armor selection. Exclusions apply to All-mods runs (Select mode is
        # already an explicit pick, so exclusions don't gate it).
        if do_armor:
            if mode.get() == "selected":
                for name, v in mod_vars.items():
                    if v.get():
                        a += ["--only-mods", name]
            else:
                for name in excl.excluded_names(state["exclusions"], "armor"):
                    a += ["--exclude-mods", name]
        # Overlay options + selection.
        if do_overlay:
            if overlay_copy.get():
                a.append("--overlay-copy")
            if overlay_skip_male.get():
                a.append("--overlay-skip-male")
            if overlay_sel_mode.get() == "selected":
                for name, v in overlay_mod_vars.items():
                    if v.get():
                        a += ["--overlay-mods", name]
            else:
                for name in excl.excluded_names(state["exclusions"], "overlay"):
                    a += ["--overlay-exclude-mods", name]
        return a

    def _child_cmd(argv_run):
        """Re-invoke the converter as a CHILD PROCESS so every module re-reads
        the (settings-driven) environment fresh -- import-time flags included --
        and a native crash in the conversion can't take the GUI down with it.
        Frozen: the exe re-runs itself with the subcommand (same mechanism the
        worker pool uses). Source: python cbbe_to_ube_main.py <subcommand>."""
        if getattr(sys, "frozen", False):
            return [sys.executable] + list(argv_run)
        main_py = Path(__file__).resolve().parent.parent / "cbbe_to_ube_main.py"
        return [sys.executable, str(main_py)] + list(argv_run)

    def _tail_log_into_queue(proc, log_path):
        """Stream the child's log file into the output queue until it exits.
        The windowed frozen exe's stdout is a null sink, but its `_Tee` always
        writes the run log (path pinned via CBBE2UBE_RUN_LOG), so tailing the
        file captures output reliably for both frozen and source runs."""
        f = None
        try:
            for _ in range(200):                 # wait up to ~10s for the child
                if os.path.isfile(log_path) or proc.poll() is not None:
                    break
                time.sleep(0.05)
            while True:
                if f is None and os.path.isfile(log_path):
                    try:
                        f = open(log_path, "r", encoding="utf-8", errors="replace")
                    except Exception:
                        f = None
                if f is not None:
                    chunk = f.read()
                    if chunk:
                        q.put(chunk)
                if proc.poll() is not None:
                    if f is not None:                # final drain after exit
                        chunk = f.read()
                        if chunk:
                            q.put(chunk)
                    break
                time.sleep(0.15)
        finally:
            if f is not None:
                try:
                    f.close()
                except Exception:
                    pass

    def _worker(argv_run):
        rc = 1
        proc = None
        try:
            # Pin the child's log to its NORMAL location (next to the exe when
            # frozen, else the repo root) so GUI runs still write the same
            # CBBEtoUBE_last_run.log everyone reads -- and the GUI tails it.
            if getattr(sys, "frozen", False):
                _log_dir = Path(sys.executable).resolve().parent
            else:
                _log_dir = Path(__file__).resolve().parent.parent
            log_path = str(_log_dir / "CBBEtoUBE_last_run.log")
            try:
                if os.path.exists(log_path):
                    os.remove(log_path)
            except Exception:
                pass
            # Failure summary the child writes at end of run (empty on a clean
            # run). Delete up front so a crash-before-write can never make the
            # end-of-run popup show a PREVIOUS run's failures.
            fail_path = str(_log_dir / "CBBEtoUBE_last_failures.json")
            state["fail_path"] = fail_path
            try:
                if os.path.exists(fail_path):
                    os.remove(fail_path)
            except Exception:
                pass
            # Registry settings win over the inherited environment (they're the
            # UI's source of truth); a fresh child re-imports every module against
            # this env, so import-time flags apply too -- the whole point of the
            # child-process launch.
            env = gui_settings.apply_env(state.get("settings") or {},
                                         base_env=dict(os.environ))
            env["CBBE2UBE_NO_PAUSE"] = "1"       # child must not block on a keypress
            env["CBBE2UBE_RUN_LOG"] = log_path
            kw = {"stdin": subprocess.DEVNULL,
                  "stdout": subprocess.DEVNULL,
                  "stderr": subprocess.DEVNULL,
                  "env": env}
            if os.name == "nt":
                kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.Popen(_child_cmd(argv_run), **kw)
            state["proc"] = proc
            _tail_log_into_queue(proc, log_path)
            proc.wait()
            rc = proc.returncode if proc.returncode is not None else 1
        except Exception:
            import traceback
            q.put("\n*** conversion failed to launch ***\n"
                  + traceback.format_exc())
            rc = 1
        finally:
            state["proc"] = None
            q.put((_DONE, rc))

    def _start():
        if state["running"]:
            return
        if not convert_armor.get() and not convert_overlays.get():
            messagebox.showinfo(
                "Nothing to convert",
                "Enable 'Convert armor' and/or 'Convert overlays' first.")
            return
        if state.get("preflight") == "fail" and not messagebox.askokcancel(
                "Setup problems",
                "The setup check found problems that may break the conversion "
                "or make it invisible in-game.\n\nClick 'Check setup' to see the "
                "details, or convert anyway?"):
            return
        if (convert_armor.get() and mode.get() == "selected"
                and not any(v.get() for v in mod_vars.values())):
            messagebox.showinfo(
                "Pick armor mods",
                "Tick at least one armor mod, or set Convert armor to 'All mods'.\n"
                "(Use 'Refresh mod list' if the list is empty.)")
            return
        if (convert_overlays.get() and overlay_sel_mode.get() == "selected"
                and not any(v.get() for v in overlay_mod_vars.values())):
            messagebox.showinfo(
                "Pick overlay mods",
                "Tick at least one overlay mod, or set Convert overlays to "
                "'All mods'.\n(Use 'Refresh mod list' if the list is empty.)")
            return
        state["running"] = True
        state["output_dir"] = out_var.get().strip() or default_out
        run_btn.configure(state="disabled")
        cancel_btn.configure(state="normal")
        open_out_btn.configure(state="disabled")
        open_rep_btn.configure(state="disabled")
        # Lock the selection UI: stop a mid-run Refresh from starting + flip the
        # "...then tick" frame label so it doesn't linger while converting.
        try:
            mods_box.configure(text="Mods (conversion running - selection locked)")
        except Exception:
            pass
        for w in sel_widgets + mod_checkboxes:
            try:
                w.configure(state="disabled")
            except Exception:
                pass
        state["run_started"] = time.time()
        state["_eta"] = {"last_t": None, "rate": None}   # reset per-mod ETA
        prog.configure(mode="indeterminate", value=0)
        prog.start(12)
        status.set("Converting... this can take many minutes; the window stays responsive.")
        argv_run = _build_argv()
        _append("> CBBEtoUBE " + " ".join(argv_run) + "\n\n")
        # Don't let the frozen entry pause for a keypress after the GUI closes.
        os.environ["CBBE2UBE_NO_PAUSE"] = "1"
        threading.Thread(target=_worker, args=(argv_run,), daemon=True).start()

    run_btn.configure(command=_start)

    def _show_failures_popup():
        """End-of-run dialog listing everything that failed to convert,
        grouped by source mod — read from the child's failure summary. No
        file or an empty list = clean run = no popup."""
        import json as _json
        fp = state.get("fail_path")
        if not fp:
            return
        try:
            data = _json.loads(Path(fp).read_text(encoding="utf-8"))
            fails = data.get("failures") or []
        except Exception:
            return
        if not fails:
            return
        top = tk.Toplevel(root)
        _theme_popup(top)
        top.title(f"{len(fails)} item(s) failed to convert")
        top.geometry("760x440")
        ttk.Label(
            top,
            text="These items did NOT convert this run — their armor keeps "
                 "its previous state (or is invisible on UBE actors). "
                 "Everything else converted normally. Details are also in "
                 "the log and the coverage report.",
            wraplength=720, justify="left").pack(anchor="w", padx=10,
                                                 pady=(10, 4))
        txt = scrolledtext.ScrolledText(top, wrap="word", height=18)
        try:   # raw tk widget: match the main log panel's themed colors
            _p = _THEMES.get(theme_var.get().strip().lower(),
                             _THEMES["standard"])
            txt.configure(bg=_p["logbg"], fg=_p["logfg"],
                          insertbackground=_p["logfg"])
        except Exception:
            pass
        txt.pack(fill="both", expand=True, padx=10, pady=4)
        by_src: dict = {}
        for fl in fails:
            by_src.setdefault(fl.get("source", "?"), []).append(fl)
        lines = []
        for s in by_src:
            lines.append(s)
            for fl in by_src[s]:
                d = f" — {fl['detail']}" if fl.get("detail") else ""
                lines.append(f"    [{fl.get('kind', '?')}] "
                             f"{fl.get('item', '')}{d}")
            lines.append("")
        txt.insert("1.0", "\n".join(lines))
        txt.configure(state="disabled")
        ttk.Button(top, text="OK", command=top.destroy).pack(pady=(2, 10))
        top.transient(root)
        try:
            top.grab_set()
        except Exception:
            pass

    def _finish(rc):
        if not root.winfo_exists():
            return   # app quit while the worker was still live; nothing to update
        state["running"] = False
        state["result"] = rc
        prog.stop()
        try:
            prog.configure(mode="indeterminate", value=0)   # reset for next run
        except Exception:
            pass
        cancel_btn.configure(state="disabled")
        for w in sel_widgets + mod_checkboxes:
            try:
                w.configure(state="normal")
            except Exception:
                pass
        _update_title()      # restore the armor checklist count label
        _ov_update_title()   # restore the overlay checklist count label
        _sync_run()          # re-gate the Convert button + checklist visibility
        od = state.get("output_dir")
        open_rep_btn.configure(state="normal")
        if od and Path(od).is_dir():
            open_out_btn.configure(state="normal", command=lambda p=od: _open_path(p))
        if rc == 0:
            status.set("Done - success (exit 0). Review the log + report for any coverage notes.")
        else:
            status.set(f"Finished with exit code {rc} - check the log for errors/warnings.")
        _append(f"\n=== finished (exit {rc}) ===\n")
        _show_failures_popup()

    _PROG_RX = re.compile(r"\[progress\] (\d+) (\d+) ([^\n]*)\n?")

    def _update_progress(done, total, name):
        if total <= 0:
            return
        try:
            if str(prog.cget("mode")) != "determinate":
                prog.stop()
                prog.configure(mode="determinate", maximum=total)
            prog.configure(value=done)
        except Exception:
            pass
        eta = _eta_step(state["_eta"], done, total, time.time())
        status.set(f"Converting {done}/{total}: {name}"
                   + (f" — {eta}" if eta else ""))

    def _poll():
        if not root.winfo_exists():
            return   # stop rescheduling once the window is gone
        try:
            while True:
                item = q.get_nowait()
                if isinstance(item, tuple) and item and item[0] is _DONE:
                    _finish(item[1])
                elif isinstance(item, str):
                    # Pull per-mod progress markers -> determinate bar + ETA, then
                    # strip them so the machine markers don't clutter the log.
                    for m in _PROG_RX.finditer(item):
                        _update_progress(int(m.group(1)), int(m.group(2)),
                                         m.group(3).strip())
                    text = _PROG_RX.sub("", item)
                    if text:
                        _append(text)
                else:
                    _append(item)
        except queue.Empty:
            pass
        root.after(120, _poll)

    def _on_close():
        if state["running"] and not messagebox.askokcancel(
                "Quit", "A conversion is still running. Quit anyway?\n"
                "(The conversion will be stopped.)"):
            return
        # Child-process launch means we can actually stop the run on quit
        # (the old in-process thread couldn't be killed cleanly). Tree-kill so
        # the ProcessPoolExecutor workers don't orphan and lock the exe.
        _kill_proc_tree(state.get("proc"))
        root.destroy()

    _apply_theme(state["settings"].get("theme", "standard"))  # after all widgets exist
    _sync_run()  # initial checklist visibility + Convert-button gating
    _run_preflight(_pf_auto)  # background setup check on launch
    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.after(120, _poll)
    if _smoke_settings:
        try:
            nb.select(1)                              # jump to a settings tab
        except Exception:
            pass
    if auto_close_ms:
        root.after(int(auto_close_ms), root.destroy)  # smoke-test self-close
    root.mainloop()
    return int(state.get("result") or 0)

